#!/usr/bin/env python3
"""
run.py - Единый пайплайн: документ на входе -> перевод -> все форматы на выходе.

Принимает: .md, .docx, .pdf (один или папка)
Спрашивает: язык перевода, бюджет, форматы
Выдает: переведенный документ в PDF + DOCX + HTML + MD (все в одном файле)

Требования:
    pip install anthropic rich fpdf2 markdown python-docx pdfplumber

Использование:
    python run.py                          # интерактивный режим
    python run.py --input docs/            # папка с файлами
    python run.py --input report.pdf       # один файл
    python run.py --lang en-de             # английский -> немецкий
    python run.py --dry-run                # только оценить стоимость
    python run.py --no-interactive         # для скриптов/CI

Переменные окружения:
    ANTHROPIC_API_KEY  - ключ API (обязателен для перевода)
    TRANSLATE_MODEL    - модель (по умолчанию: claude-sonnet-4-5-20250929)
"""

import argparse
import json
import os
import re
import signal
import sys
import time
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Rich / Fallback
# ---------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
    from rich.prompt import Prompt, Confirm, IntPrompt
    from rich.rule import Rule
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

console = Console() if HAS_RICH else None

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
FONT_DIR = ROOT / "fonts"
GLOSSARY_PATH = ROOT / "glossary.json"
GLOSSARY_CANDIDATES_PATH = ROOT / "glossary_candidates.json"
TRANSLATE_SPEC = ROOT / "TRANSLATE.md"
HUMANIZER_SPEC = ROOT / "HUMANIZER.md"

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
MAX_OUTPUT_TOKENS = 16384
CHUNK_SIZE_CHARS = 40000

# Стоимость (USD за 1M токенов) - Sonnet 4.5
COST_INPUT_PER_M = 3.0
COST_OUTPUT_PER_M = 15.0
AVG_SECONDS_PER_1K_CHARS = 3.5

# Языковые пары
LANGUAGES = {
    "en-ru": ("английского", "русский", "English", "Russian"),
    "ru-en": ("русского", "английский", "Russian", "English"),
    "en-de": ("английского", "немецкий", "English", "German"),
    "en-es": ("английского", "испанский", "English", "Spanish"),
    "en-fr": ("английского", "французский", "English", "French"),
    "en-zh": ("английского", "китайский", "English", "Chinese"),
    "en-ja": ("английского", "японский", "English", "Japanese"),
    "en-pt": ("английского", "португальский", "English", "Portuguese"),
    "de-en": ("немецкого", "английский", "German", "English"),
    "fr-en": ("французского", "английский", "French", "English"),
    "es-en": ("испанского", "английский", "Spanish", "English"),
}

# Поддерживаемые форматы ввода
INPUT_EXTENSIONS = {'.md', '.markdown', '.txt', '.docx', '.doc', '.pdf'}

# ---------------------------------------------------------------------------
# Graceful interrupt
# ---------------------------------------------------------------------------
_interrupted = False

def _signal_handler(signum, frame):
    global _interrupted
    if _interrupted:
        ui_print("\n[bold red]Принудительный выход.[/]" if HAS_RICH else "\nПринудительный выход.")
        sys.exit(1)
    _interrupted = True
    ui_print(
        "\n[yellow]Ctrl+C - перевод остановится после текущего файла. "
        "Нажмите еще раз для немедленного выхода.[/]"
        if HAS_RICH else
        "\nCtrl+C - остановка после текущего файла."
    )

signal.signal(signal.SIGINT, _signal_handler)

# ---------------------------------------------------------------------------
# UI Helpers
# ---------------------------------------------------------------------------

def ui_print(msg: str, **kwargs):
    if HAS_RICH:
        console.print(msg, **kwargs)
    else:
        clean = re.sub(r'\[/?[^\]]*\]', '', str(msg))
        print(clean)


def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    if HAS_RICH:
        colors = {"INFO": "cyan", "WARN": "yellow", "ERROR": "red", "OK": "green",
                  "STEP": "magenta"}
        c = colors.get(level, "white")
        console.print(f"[dim]{ts}[/] [{c}]{level:>5}[/]  {msg}")
    else:
        print(f"[{ts}] [{level}] {msg}")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def load_json(path: Path) -> list:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, Exception):
        return []


def estimate_tokens(text: str) -> int:
    return len(text) // 3


def calc_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * COST_INPUT_PER_M + output_tokens * COST_OUTPUT_PER_M) / 1_000_000


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f} сек"
    elif seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m} мин {s} сек"
    else:
        h, rem = divmod(int(seconds), 3600)
        m = rem // 60
        return f"{h} ч {m} мин"


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} Б"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} КБ"
    else:
        return f"{size_bytes / (1024*1024):.1f} МБ"


# ---------------------------------------------------------------------------
# STEP 1: Input - чтение файлов любого формата
# ---------------------------------------------------------------------------

def extract_text_from_pdf(path: Path) -> str:
    """Извлечь текст из PDF."""
    try:
        import pdfplumber
        texts = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    texts.append(text)
        return "\n\n".join(texts)
    except ImportError:
        log("Установите pdfplumber: pip install pdfplumber", "ERROR")
        return ""
    except Exception as e:
        log(f"Ошибка чтения PDF {path.name}: {e}", "ERROR")
        return ""


def extract_text_from_docx(path: Path) -> str:
    """Извлечь текст из DOCX."""
    try:
        from docx import Document as DocxDocument
        doc = DocxDocument(str(path))
        paragraphs = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                # Сохранить стиль заголовков
                style = para.style.name if para.style else ""
                if "Heading 1" in style:
                    paragraphs.append(f"# {text}")
                elif "Heading 2" in style:
                    paragraphs.append(f"## {text}")
                elif "Heading 3" in style:
                    paragraphs.append(f"### {text}")
                elif "Heading 4" in style:
                    paragraphs.append(f"#### {text}")
                elif "List" in style:
                    paragraphs.append(f"- {text}")
                else:
                    paragraphs.append(text)

        # Таблицы
        for table in doc.tables:
            rows = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                rows.append("| " + " | ".join(cells) + " |")
            if rows:
                header_sep = "| " + " | ".join(["---"] * len(table.rows[0].cells)) + " |"
                rows.insert(1, header_sep)
                paragraphs.append("\n".join(rows))

        return "\n\n".join(paragraphs)
    except ImportError:
        log("Установите python-docx: pip install python-docx", "ERROR")
        return ""
    except Exception as e:
        log(f"Ошибка чтения DOCX {path.name}: {e}", "ERROR")
        return ""


def read_input_file(path: Path) -> tuple[str, str]:
    """Прочитать файл любого формата. Возвращает (text, original_format)."""
    ext = path.suffix.lower()

    if ext in ('.md', '.markdown', '.txt'):
        text = path.read_text(encoding="utf-8")
        return text, "md"

    elif ext == '.pdf':
        log(f"  Извлечение текста из PDF: {path.name}...")
        text = extract_text_from_pdf(path)
        return text, "pdf"

    elif ext in ('.docx', '.doc'):
        if ext == '.doc':
            log(f"  .doc формат - попытка чтения как .docx: {path.name}", "WARN")
        log(f"  Извлечение текста из DOCX: {path.name}...")
        text = extract_text_from_docx(path)
        return text, "docx"

    else:
        log(f"Неподдерживаемый формат: {ext}", "ERROR")
        return "", "unknown"


def discover_input_files(input_path: Path) -> list[Path]:
    """Найти все поддерживаемые файлы в указанном пути."""
    if input_path.is_file():
        if input_path.suffix.lower() in INPUT_EXTENSIONS:
            return [input_path]
        else:
            log(f"Неподдерживаемый формат: {input_path.suffix}", "ERROR")
            return []

    if input_path.is_dir():
        files = []
        for ext in INPUT_EXTENSIONS:
            files.extend(input_path.glob(f"*{ext}"))
        return sorted(set(files))

    log(f"Путь не найден: {input_path}", "ERROR")
    return []


# ---------------------------------------------------------------------------
# STEP 2: Translation - перевод через Claude API
# ---------------------------------------------------------------------------

def build_system_prompt(lang_pair: str, glossary: list) -> str:
    """Построить системный промпт для выбранной языковой пары."""
    lang_from, lang_to, _, _ = LANGUAGES.get(lang_pair, LANGUAGES["en-ru"])

    parts = []
    parts.append(f"Ты - профессиональный технический переводчик с {lang_from} на {lang_to}.")
    parts.append("Следуй приведенным ниже правилам ТОЧНО и БЕЗ ОТКЛОНЕНИЙ.\n")

    if TRANSLATE_SPEC.exists():
        spec = TRANSLATE_SPEC.read_text(encoding="utf-8")
        parts.append("=" * 60)
        parts.append("СПЕЦИФИКАЦИЯ ПЕРЕВОДА (TRANSLATE.md)")
        parts.append("=" * 60)
        parts.append(spec)

    if HUMANIZER_SPEC.exists():
        humanizer = HUMANIZER_SPEC.read_text(encoding="utf-8")
        parts.append("\n" + "=" * 60)
        parts.append("РЕДАКТОРСКИЕ ПРАВИЛА (HUMANIZER.md) - только anti-AI cleanup")
        parts.append("ВАЖНО: НЕ применять секцию PERSONALITY AND SOUL.")
        parts.append("НЕ добавлять личный голос, эмоции, юмор, первое лицо.")
        parts.append("Использовать ТОЛЬКО для устранения AI-штампов.")
        parts.append("=" * 60)
        parts.append(humanizer)

    if glossary:
        parts.append("\n" + "=" * 60)
        parts.append("КАНОНИЧЕСКИЙ ГЛОССАРИЙ (glossary.json)")
        parts.append("Если term встречается в тексте - использовать ТОЛЬКО перевод из глоссария.")
        parts.append("=" * 60)
        glossary_text = "\n".join(
            f"- {e.get('term_en', e.get('term', ''))} -> {e.get('term_ru', e.get('translation', ''))}"
            for e in glossary if isinstance(e, dict)
        )
        parts.append(glossary_text)

    return "\n\n".join(parts)


def build_user_prompt(source_text: str, filename: str, lang_pair: str,
                      is_chunk: bool = False, chunk_num: int = 0,
                      total_chunks: int = 0) -> str:
    """Построить пользовательский промпт для перевода."""
    lang_from, lang_to, lang_from_en, lang_to_en = LANGUAGES.get(lang_pair, LANGUAGES["en-ru"])

    chunk_info = ""
    if is_chunk:
        chunk_info = f"\n\nЭто чанк {chunk_num}/{total_chunks}. Переводи только этот фрагмент."

    return f"""Переведи следующий документ с {lang_from} на {lang_to}.

Файл: {filename}{chunk_info}

КРИТИЧЕСКИ ВАЖНО:
1. Сохрани структуру Markdown 1:1 (заголовки, списки, таблицы, code blocks, ссылки, изображения).
2. НЕ переводи: code blocks, URL, адреса, идентификаторы, тикеры.
3. Используй ТОЛЬКО канонические термины из глоссария (если предоставлен).
4. Верни ТОЛЬКО переведенный текст. Без комментариев, пояснений, оберток.

---НАЧАЛО ИСХОДНОГО ТЕКСТА---

{source_text}

---КОНЕЦ ИСХОДНОГО ТЕКСТА---

Верни ТОЛЬКО перевод. Без преамбулы, без пост-скриптума."""


def split_into_chunks(text: str, max_chars: int = CHUNK_SIZE_CHARS) -> list[str]:
    """Разбить текст на чанки по заголовкам ##."""
    if len(text) <= max_chars:
        return [text]

    sections = re.split(r'(?=\n## )', text)
    chunks = []
    current_chunk = ""

    for section in sections:
        if len(current_chunk) + len(section) <= max_chars:
            current_chunk += section
        else:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            if len(section) > max_chars:
                paragraphs = section.split("\n\n")
                current_chunk = ""
                for para in paragraphs:
                    if len(current_chunk) + len(para) + 2 <= max_chars:
                        current_chunk += ("\n\n" if current_chunk else "") + para
                    else:
                        if current_chunk.strip():
                            chunks.append(current_chunk.strip())
                        current_chunk = para
            else:
                current_chunk = section

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks if chunks else [text]


def translate_text(client, model: str, system_prompt: str,
                   source_text: str, filename: str, lang_pair: str,
                   is_chunk: bool = False, chunk_num: int = 0,
                   total_chunks: int = 0) -> tuple[str, int, int]:
    """Перевести текст через Claude API."""
    user_prompt = build_user_prompt(source_text, filename, lang_pair,
                                    is_chunk, chunk_num, total_chunks)

    response = client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    translated = response.content[0].text
    return translated, response.usage.input_tokens, response.usage.output_tokens


def translate_document(client, model: str, system_prompt: str,
                       source_text: str, filename: str, lang_pair: str) -> dict:
    """Перевести один документ (с разбивкой на чанки)."""
    chunks = split_into_chunks(source_text)

    stats = {
        "file": filename,
        "source_chars": len(source_text),
        "chunks": len(chunks),
        "input_tokens": 0,
        "output_tokens": 0,
        "cost": 0.0,
        "status": "ok",
        "translated_text": "",
    }

    translated_parts = []

    for i, chunk in enumerate(chunks, 1):
        if _interrupted:
            stats["status"] = "interrupted"
            return stats

        is_chunked = len(chunks) > 1
        if is_chunked:
            log(f"    Чанк {i}/{len(chunks)} ({len(chunk):,} символов)...")

        try:
            translation, inp_tok, out_tok = translate_text(
                client, model, system_prompt,
                chunk, filename, lang_pair,
                is_chunk=is_chunked, chunk_num=i, total_chunks=len(chunks)
            )
            translated_parts.append(translation)
            stats["input_tokens"] += inp_tok
            stats["output_tokens"] += out_tok
        except Exception as e:
            log(f"    ОШИБКА: {e}", "ERROR")
            stats["status"] = f"error: {e}"
            return stats

        if is_chunked and i < len(chunks):
            time.sleep(1)

    stats["translated_text"] = "\n\n".join(translated_parts)
    stats["cost"] = calc_cost(stats["input_tokens"], stats["output_tokens"])
    return stats


# ---------------------------------------------------------------------------
# STEP 3: Assembly - сборка в один документ
# ---------------------------------------------------------------------------

def assemble_document(translations: list[dict], title: str = "") -> str:
    """Собрать все переводы в один Markdown-документ."""
    parts = []

    if title:
        parts.append(f"# {title}\n")

    for i, t in enumerate(translations):
        if len(translations) > 1 and i > 0:
            parts.append(f"\n---\n")
        parts.append(t["translated_text"])

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# STEP 4: Output - генерация всех форматов
# ---------------------------------------------------------------------------

def generate_html(md_text: str, title: str) -> str:
    """Markdown -> HTML."""
    import markdown
    extensions = [
        'markdown.extensions.tables',
        'markdown.extensions.fenced_code',
        'markdown.extensions.toc',
        'markdown.extensions.sane_lists',
    ]
    html_content = markdown.markdown(md_text, extensions=extensions)
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{
            font-family: 'Segoe UI', 'DejaVu Sans', 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.7; color: #1a1a2e; background: #fafbfc;
            max-width: 860px; margin: 0 auto; padding: 40px 32px;
        }}
        h1 {{ font-size: 2em; border-bottom: 3px solid #4361ee; padding-bottom: 12px; margin-top: 40px; }}
        h2 {{ font-size: 1.5em; border-bottom: 1px solid #dee2e6; padding-bottom: 8px; margin-top: 36px; }}
        h3 {{ font-size: 1.25em; margin-top: 28px; }}
        a {{ color: #4361ee; text-decoration: none; }}
        code {{ background: #e9ecef; padding: 2px 6px; border-radius: 4px;
                font-family: 'Consolas', 'Courier New', monospace; font-size: 0.9em; color: #d63384; }}
        pre {{ background: #1e1e2e; color: #cdd6f4; padding: 20px; border-radius: 8px;
               overflow-x: auto; line-height: 1.5; margin: 16px 0; }}
        pre code {{ background: none; color: inherit; padding: 0; }}
        table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
        th, td {{ border: 1px solid #dee2e6; padding: 10px 14px; text-align: left; }}
        th {{ background: #f1f3f5; font-weight: 600; }}
        tr:nth-child(even) {{ background: #f8f9fa; }}
        blockquote {{ border-left: 4px solid #4361ee; margin: 16px 0;
                      padding: 12px 20px; background: #eef2ff; }}
        img {{ max-width: 100%; height: auto; }}
        hr {{ border: none; border-top: 2px solid #dee2e6; margin: 32px 0; }}
        .meta {{ color: #868e96; font-size: 0.85em; border-top: 1px solid #dee2e6;
                 padding-top: 16px; margin-top: 48px; }}
        @media print {{ body {{ max-width: none; padding: 20px; }}
            pre {{ white-space: pre-wrap; }} }}
    </style>
</head>
<body>
{html_content}
<div class="meta">md-translate-ru | {date_str}</div>
</body>
</html>"""


def generate_pdf(md_text: str, output_path: Path, title: str):
    """Markdown -> PDF через fpdf2 с кириллицей."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)

    # Регистрация шрифтов
    font_map = {
        "DejaVuSans": {"": "DejaVuSans.ttf", "B": "DejaVuSans-Bold.ttf",
                        "I": "DejaVuSans-Oblique.ttf", "BI": "DejaVuSans-BoldOblique.ttf"},
        "DejaVuMono": {"": "DejaVuSansMono.ttf", "B": "DejaVuSansMono-Bold.ttf"},
    }
    for family, styles in font_map.items():
        for style, filename in styles.items():
            font_path = FONT_DIR / filename
            if font_path.exists():
                pdf.add_font(family, style, str(font_path))

    pdf.add_page()
    pdf.set_font("DejaVuSans", size=10)

    def clean(text):
        text = re.sub(r'\*\*\*(.+?)\*\*\*', r'\1', text)
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
        text = re.sub(r'!\[(.+?)\]\(.+?\)', r'[\1]', text)
        return text

    lines = md_text.split('\n')
    in_code = False
    code_buf = []

    for line in lines:
        if line.strip().startswith('```'):
            if in_code:
                pdf.set_fill_color(30, 30, 46)
                pdf.set_text_color(205, 214, 244)
                pdf.set_font("DejaVuMono", size=8)
                for cl in code_buf:
                    t = cl[:95] + "..." if len(cl) > 95 else cl
                    pdf.cell(0, 4.5, t)
                    pdf.ln(4.5)
                pdf.set_font("DejaVuSans", size=10)
                pdf.set_text_color(30, 30, 30)
                pdf.ln(4)
                code_buf = []
                in_code = False
            else:
                in_code = True
            continue

        if in_code:
            code_buf.append(line)
            continue

        stripped = line.strip()

        if not stripped:
            pdf.ln(3)
            continue

        if stripped.startswith('#'):
            level = len(stripped) - len(stripped.lstrip('#'))
            text = stripped.lstrip('#').strip()
            sizes = {1: 20, 2: 16, 3: 13, 4: 11}
            pdf.ln(4)
            pdf.set_font("DejaVuSans", "B", sizes.get(level, 10))
            pdf.set_text_color(26, 26, 46)
            pdf.multi_cell(0, sizes.get(level, 10) * 0.6, clean(text))
            if level <= 2:
                pdf.set_draw_color(67, 97, 238)
                pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + pdf.epw, pdf.get_y())
            pdf.ln(3)
            pdf.set_font("DejaVuSans", size=10)
            pdf.set_text_color(30, 30, 30)
            continue

        if stripped in ('---', '***', '___'):
            pdf.ln(4)
            pdf.set_draw_color(200, 200, 200)
            pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + pdf.epw, pdf.get_y())
            pdf.ln(8)
            continue

        if stripped.startswith('|') and stripped.endswith('|'):
            if re.match(r'^\|[\s\-:|]+\|$', stripped):
                continue
            cells = [c.strip() for c in stripped.split('|')[1:-1]]
            n = len(cells) or 1
            w = pdf.epw / n
            pdf.set_font("DejaVuSans", size=9)
            for c in cells:
                t = clean(c)[:40]
                pdf.cell(w, 7, t, border=1)
            pdf.ln()
            pdf.set_font("DejaVuSans", size=10)
            continue

        list_m = re.match(r'^(\s*)([-*+]|\d+\.)\s+(.+)', line)
        if list_m:
            indent = len(list_m.group(1))
            offset = 10 + (indent // 2) * 6
            pdf.set_x(pdf.l_margin + offset)
            marker = list_m.group(2)
            bullet = "\u2022 " if marker in ('-', '*', '+') else f"{marker} "
            pdf.multi_cell(pdf.epw - offset - 4, 6, bullet + clean(list_m.group(3)))
            pdf.ln(1)
            continue

        if stripped.startswith('>'):
            text = stripped.lstrip('>').strip()
            pdf.set_x(pdf.l_margin + 8)
            pdf.set_font("DejaVuSans", "I", 10)
            pdf.set_text_color(59, 59, 92)
            pdf.multi_cell(pdf.epw - 16, 6, clean(text))
            pdf.ln(2)
            pdf.set_font("DejaVuSans", size=10)
            pdf.set_text_color(30, 30, 30)
            continue

        pdf.multi_cell(0, 6, clean(stripped))
        pdf.ln(2)

    pdf.output(str(output_path))


def generate_docx(md_text: str, output_path: Path, title: str):
    """Markdown -> DOCX через python-docx."""
    from docx import Document as DocxDocument
    from docx.shared import Pt, Inches, RGBColor

    doc = DocxDocument()

    style = doc.styles['Normal']
    style.font.name = 'Arial'
    style.font.size = Pt(11)
    style.paragraph_format.space_after = Pt(6)

    lines = md_text.split('\n')
    in_code = False
    code_buf = []
    table_buf = []

    def flush_table():
        nonlocal table_buf
        if not table_buf:
            return
        rows_data = []
        for row_line in table_buf:
            if re.match(r'^\|[\s\-:|]+\|$', row_line.strip()):
                continue
            cells = [c.strip() for c in row_line.strip().split('|')[1:-1]]
            if cells:
                rows_data.append(cells)

        if rows_data:
            n_cols = max(len(r) for r in rows_data)
            table = doc.add_table(rows=len(rows_data), cols=n_cols)
            table.style = 'Table Grid'
            for i, row_data in enumerate(rows_data):
                for j, cell_text in enumerate(row_data):
                    if j < n_cols:
                        table.rows[i].cells[j].text = clean_md(cell_text)
            if rows_data:
                for cell in table.rows[0].cells:
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            run.font.bold = True
        table_buf = []

    def clean_md(text):
        text = re.sub(r'\*\*\*(.+?)\*\*\*', r'\1', text)
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
        text = re.sub(r'!\[(.+?)\]\(.+?\)', r'[\1]', text)
        return text

    for line in lines:
        if line.strip().startswith('```'):
            if in_code:
                flush_table()
                code_text = '\n'.join(code_buf)
                p = doc.add_paragraph()
                run = p.add_run(code_text)
                run.font.name = 'Courier New'
                run.font.size = Pt(9)
                run.font.color.rgb = RGBColor(0x20, 0x20, 0x30)
                p.paragraph_format.left_indent = Inches(0.3)
                code_buf = []
                in_code = False
            else:
                flush_table()
                in_code = True
            continue

        if in_code:
            code_buf.append(line)
            continue

        stripped = line.strip()

        if stripped.startswith('|') and stripped.endswith('|'):
            table_buf.append(line)
            continue
        else:
            flush_table()

        if not stripped:
            continue

        if stripped.startswith('#'):
            level = min(len(stripped) - len(stripped.lstrip('#')), 4)
            text = stripped.lstrip('#').strip()
            doc.add_heading(clean_md(text), level=level)
            continue

        if stripped in ('---', '***', '___'):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(12)
            p.paragraph_format.space_after = Pt(12)
            continue

        list_m = re.match(r'^(\s*)([-*+])\s+(.+)', line)
        if list_m:
            doc.add_paragraph(clean_md(list_m.group(3)), style='List Bullet')
            continue

        num_m = re.match(r'^(\s*)\d+\.\s+(.+)', line)
        if num_m:
            doc.add_paragraph(clean_md(num_m.group(2)), style='List Number')
            continue

        if stripped.startswith('>'):
            text = stripped.lstrip('>').strip()
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.5)
            run = p.add_run(clean_md(text))
            run.font.italic = True
            run.font.color.rgb = RGBColor(0x3B, 0x3B, 0x5C)
            continue

        doc.add_paragraph(clean_md(stripped))

    flush_table()
    doc.save(str(output_path))


def generate_outputs(md_text: str, output_dir: Path, base_name: str,
                     title: str, formats: list[str]) -> dict:
    """Сгенерировать все выходные форматы."""
    results = {"files": [], "errors": []}
    output_dir.mkdir(parents=True, exist_ok=True)

    if "md" in formats:
        try:
            path = output_dir / f"{base_name}.md"
            path.write_text(md_text, encoding="utf-8")
            results["files"].append(("MD", path))
            log(f"  MD:   {path.name}", "OK")
        except Exception as e:
            results["errors"].append(f"MD: {e}")

    if "html" in formats:
        try:
            path = output_dir / f"{base_name}.html"
            html = generate_html(md_text, title)
            path.write_text(html, encoding="utf-8")
            results["files"].append(("HTML", path))
            log(f"  HTML: {path.name}", "OK")
        except Exception as e:
            results["errors"].append(f"HTML: {e}")

    if "pdf" in formats:
        try:
            path = output_dir / f"{base_name}.pdf"
            generate_pdf(md_text, path, title)
            results["files"].append(("PDF", path))
            log(f"  PDF:  {path.name}", "OK")
        except Exception as e:
            results["errors"].append(f"PDF: {e}")

    if "docx" in formats:
        try:
            path = output_dir / f"{base_name}.docx"
            generate_docx(md_text, path, title)
            results["files"].append(("DOCX", path))
            log(f"  DOCX: {path.name}", "OK")
        except Exception as e:
            results["errors"].append(f"DOCX: {e}")

    return results


# ---------------------------------------------------------------------------
# Interactive Menu
# ---------------------------------------------------------------------------

def find_input_candidates() -> list[tuple[Path, int, str]]:
    """Найти файлы с поддерживаемыми форматами."""
    candidates = []
    skip = {'.git', '__pycache__', 'fonts', 'output', 'node_modules', '.venv', 'venv'}

    def scan(base: Path, depth: int = 0):
        if depth > 2:
            return
        try:
            for p in sorted(base.iterdir()):
                if p.name.startswith('.') or p.name in skip:
                    continue
                if p.is_file() and p.suffix.lower() in INPUT_EXTENSIONS:
                    candidates.append((p, p.stat().st_size, p.suffix.lower()))
                elif p.is_dir():
                    scan(p, depth + 1)
        except PermissionError:
            pass

    scan(ROOT)
    cwd = Path.cwd()
    if cwd.resolve() != ROOT.resolve():
        scan(cwd)

    seen = set()
    unique = []
    for item in candidates:
        resolved = item[0].resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(item)
    return unique


def interactive_menu() -> dict:
    """Полное интерактивное меню."""
    result = {
        "input_files": [],
        "lang_pair": "en-ru",
        "budget": None,
        "formats": ["md", "html", "pdf", "docx"],
        "output_dir": ROOT / "output",
        "output_name": "translated",
        "dry_run": False,
        "batch": False,
        "model": None,
    }

    if HAS_RICH:
        console.print()
        console.print(Panel(
            "[bold cyan]md-translate-ru[/] - единый пайплайн перевода документов\n"
            "[dim]Вход: .md, .docx, .pdf -> Перевод -> Выход: MD + HTML + PDF + DOCX[/]",
            title="run.py",
            border_style="cyan",
        ))

        # ========== 1. ВХОДНЫЕ ФАЙЛЫ ==========
        console.print()
        console.print(Rule("[bold]Шаг 1: Входные файлы[/]", style="cyan"))
        console.print()

        files_found = find_input_candidates()

        if files_found:
            console.print("[bold]Найдены файлы:[/]\n")
            for i, (f, size, ext) in enumerate(files_found, 1):
                try:
                    rel = f.relative_to(ROOT)
                except ValueError:
                    rel = f
                ext_colors = {'.md': 'green', '.pdf': 'red', '.docx': 'blue', '.txt': 'yellow'}
                c = ext_colors.get(ext, 'white')
                console.print(f"  [bold cyan]{i:2d}[/]  [{c}]{ext:6s}[/]  {rel}  [dim]{format_size(size)}[/]")

            console.print()
            console.print("  [bold cyan] 0[/]  Ввести путь вручную")
            console.print()

            choice = Prompt.ask(
                "Выберите файлы (номера через запятую, или 'all')",
                default="all"
            )

            if choice.strip() == "0":
                path_str = Prompt.ask("Путь к файлу или папке")
                p = Path(path_str)
                if p.exists():
                    result["input_files"] = discover_input_files(p)
                else:
                    log(f"Путь не найден: {path_str}", "ERROR")
                    sys.exit(1)
            elif choice.strip().lower() == "all":
                result["input_files"] = [f[0] for f in files_found]
            else:
                indices = []
                for part in choice.split(","):
                    part = part.strip()
                    if "-" in part:
                        a, b = part.split("-", 1)
                        indices.extend(range(int(a), int(b) + 1))
                    elif part.isdigit():
                        indices.append(int(part))
                for idx in indices:
                    if 1 <= idx <= len(files_found):
                        result["input_files"].append(files_found[idx - 1][0])
        else:
            console.print("[yellow]Файлы не найдены автоматически.[/]")
            path_str = Prompt.ask("Путь к файлу или папке с документами")
            p = Path(path_str)
            if p.exists():
                result["input_files"] = discover_input_files(p)
            else:
                log(f"Путь не найден: {path_str}", "ERROR")
                sys.exit(1)

        if not result["input_files"]:
            log("Нет файлов для обработки", "ERROR")
            sys.exit(1)

        console.print(f"\n  [green]Выбрано файлов: {len(result['input_files'])}[/]")

        # ========== 2. ЯЗЫК ==========
        console.print()
        console.print(Rule("[bold]Шаг 2: Язык перевода[/]", style="cyan"))
        console.print()

        lang_options = list(LANGUAGES.keys())
        for i, lp in enumerate(lang_options, 1):
            _, _, from_en, to_en = LANGUAGES[lp]
            marker = " [bold green](по умолчанию)[/]" if lp == "en-ru" else ""
            console.print(f"  [bold cyan]{i:2d}[/]  {from_en} -> {to_en}  [dim]({lp})[/]{marker}")

        console.print()
        lang_choice = Prompt.ask("Выбор", default="1")
        try:
            idx = int(lang_choice) - 1
            if 0 <= idx < len(lang_options):
                result["lang_pair"] = lang_options[idx]
        except ValueError:
            if lang_choice in LANGUAGES:
                result["lang_pair"] = lang_choice

        _, _, from_en, to_en = LANGUAGES[result["lang_pair"]]
        console.print(f"  [green]{from_en} -> {to_en}[/]")

        # ========== 3. РЕЖИМ ==========
        console.print()
        console.print(Rule("[bold]Шаг 3: Режим[/]", style="cyan"))
        console.print()

        modes = [
            ("1", "Перевести сейчас (синхронно)", "sync"),
            ("2", "Только оценить стоимость (dry-run)", "dry"),
        ]
        for key, label, _ in modes:
            console.print(f"  [bold cyan]{key}[/]  {label}")
        console.print()

        mode_choice = Prompt.ask("Выбор", choices=["1", "2"], default="1")
        mode = next(m[2] for m in modes if m[0] == mode_choice)
        result["dry_run"] = mode == "dry"

        # ========== 4. БЮДЖЕТ ==========
        if not result["dry_run"]:
            console.print()
            if Confirm.ask("Задать лимит бюджета?", default=False):
                budget_str = Prompt.ask("Максимальный бюджет (USD)", default="10.00")
                try:
                    result["budget"] = float(budget_str)
                except ValueError:
                    pass

        # ========== 5. ВЫХОДНЫЕ ФОРМАТЫ ==========
        console.print()
        console.print(Rule("[bold]Шаг 4: Выходные форматы[/]", style="cyan"))
        console.print()

        fmt_options = [
            ("1", "Все (MD + HTML + PDF + DOCX)", ["md", "html", "pdf", "docx"]),
            ("2", "PDF + DOCX", ["pdf", "docx"]),
            ("3", "Только PDF", ["pdf"]),
            ("4", "Только DOCX", ["docx"]),
            ("5", "PDF + HTML", ["pdf", "html"]),
            ("6", "Только MD", ["md"]),
        ]
        for key, label, _ in fmt_options:
            console.print(f"  [bold cyan]{key}[/]  {label}")
        console.print()

        fmt_choice = Prompt.ask("Выбор", choices=[f[0] for f in fmt_options], default="1")
        result["formats"] = next(f[2] for f in fmt_options if f[0] == fmt_choice)

        # ========== 6. ВЫХОДНОЕ ИМЯ ==========
        console.print()
        default_name = "translated"
        if len(result["input_files"]) == 1:
            default_name = result["input_files"][0].stem + "_translated"
        result["output_name"] = Prompt.ask("Имя выходного файла (без расширения)",
                                            default=default_name)

        # ========== 7. ВЫХОДНАЯ ПАПКА ==========
        default_out = str(ROOT / "output")
        out_str = Prompt.ask("Выходная папка", default=default_out)
        result["output_dir"] = Path(out_str).resolve()

    else:
        # === Fallback без Rich ===
        print(f"\n{'='*55}")
        print("md-translate-ru - единый пайплайн перевода")
        print(f"{'='*55}")

        files_found = find_input_candidates()
        if files_found:
            print("\nНайдены файлы:")
            for i, (f, size, ext) in enumerate(files_found, 1):
                print(f"  {i:2d}. [{ext}] {f.name} ({format_size(size)})")
            print("   0. Ввести путь вручную")
            choice = input("\nВыбор (номера/all) [all]: ").strip() or "all"
            if choice == "0":
                p = Path(input("Путь: ").strip())
                result["input_files"] = discover_input_files(p) if p.exists() else []
            elif choice.lower() == "all":
                result["input_files"] = [f[0] for f in files_found]
            else:
                for part in choice.split(","):
                    idx = int(part.strip())
                    if 1 <= idx <= len(files_found):
                        result["input_files"].append(files_found[idx - 1][0])
        else:
            p = Path(input("Путь к файлам: ").strip())
            result["input_files"] = discover_input_files(p) if p.exists() else []

        if not result["input_files"]:
            print("Нет файлов!"); sys.exit(1)

        print("\nЯзык: 1=EN->RU 2=RU->EN 3=EN->DE 4=EN->ES 5=EN->FR")
        lang_map = {"1": "en-ru", "2": "ru-en", "3": "en-de", "4": "en-es", "5": "en-fr"}
        result["lang_pair"] = lang_map.get(input("Выбор [1]: ").strip() or "1", "en-ru")

        print("\nРежим: 1=перевод 2=dry-run")
        mode = input("Выбор [1]: ").strip() or "1"
        result["dry_run"] = mode == "2"

        b = input("Бюджет USD (Enter=без лимита): ").strip()
        if b:
            try: result["budget"] = float(b)
            except ValueError: pass

        print("\nФорматы: 1=все 2=PDF+DOCX 3=PDF 4=DOCX 5=MD")
        fmt_map = {"1": ["md","html","pdf","docx"], "2": ["pdf","docx"],
                   "3": ["pdf"], "4": ["docx"], "5": ["md"]}
        result["formats"] = fmt_map.get(input("Выбор [1]: ").strip() or "1",
                                         ["md","html","pdf","docx"])

        default_name = "translated"
        if len(result["input_files"]) == 1:
            default_name = result["input_files"][0].stem + "_translated"
        result["output_name"] = input(f"Имя файла [{default_name}]: ").strip() or default_name

        result["output_dir"] = Path(
            input(f"Выходная папка [{ROOT / 'output'}]: ").strip() or str(ROOT / "output")
        ).resolve()

    return result


# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------

def show_forecast(input_files: list[Path], texts: list[str], system_tokens: int,
                  budget: float = None) -> list[dict]:
    """Показать прогноз стоимости."""
    forecasts = []
    for f, text in zip(input_files, texts):
        chunks = split_into_chunks(text)
        est_input = estimate_tokens(text) + system_tokens
        est_output = int(estimate_tokens(text) * 1.15)
        est_cost = calc_cost(est_input, est_output)
        est_time = len(text) / 1000 * AVG_SECONDS_PER_1K_CHARS
        forecasts.append({
            "name": f.name, "chars": len(text), "chunks": len(chunks),
            "est_cost": est_cost, "est_time": est_time,
        })

    total_cost = sum(fc["est_cost"] for fc in forecasts)
    total_time = sum(fc["est_time"] for fc in forecasts)
    total_chars = sum(fc["chars"] for fc in forecasts)

    if HAS_RICH:
        table = Table(title="Прогноз", box=box.ROUNDED, show_lines=True, title_style="bold cyan")
        table.add_column("#", style="dim", width=4, justify="right")
        table.add_column("Файл", style="bold white", max_width=30)
        table.add_column("Символы", justify="right", style="cyan")
        table.add_column("Чанки", justify="center")
        table.add_column("Время", justify="right", style="yellow")
        table.add_column("Цена", justify="right", style="green")

        for i, fc in enumerate(forecasts, 1):
            table.add_row(str(i), fc["name"], f"{fc['chars']:,}",
                          str(fc["chunks"]), format_duration(fc["est_time"]),
                          f"${fc['est_cost']:.2f}")

        table.add_section()
        budget_note = ""
        if budget is not None:
            r = budget - total_cost
            budget_note = (f"  [green]бюджет ${budget:.2f}, остаток ${r:.2f}[/]" if r >= 0
                           else f"  [red]бюджет ${budget:.2f}, не хватает ${-r:.2f}[/]")

        table.add_row("", f"[bold]ИТОГО: {len(forecasts)} файлов[/]", f"[bold]{total_chars:,}[/]",
                       "", f"[bold]{format_duration(total_time)}[/]",
                       f"[bold]${total_cost:.2f}[/]" + budget_note)
        console.print()
        console.print(table)
        console.print()
    else:
        print(f"\nПРОГНОЗ: {len(forecasts)} файлов, {total_chars:,} символов")
        print(f"Время: {format_duration(total_time)}, Цена: ${total_cost:.2f}")
        if budget is not None:
            print(f"Бюджет: ${budget:.2f}")
        print()

    return forecasts


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Единый пайплайн: документ -> перевод -> все форматы",
    )
    parser.add_argument("--input", help="Файл или папка для перевода")
    parser.add_argument("--lang", default=None, help="Языковая пара (en-ru, en-de, ...)")
    parser.add_argument("--budget", type=float, default=None, help="Лимит бюджета USD")
    parser.add_argument("--format", default=None, help="Форматы: all, pdf, docx, html, md")
    parser.add_argument("--output", default=None, help="Имя выходного файла")
    parser.add_argument("--output-dir", default=None, help="Выходная папка")
    parser.add_argument("--dry-run", action="store_true", help="Только оценить стоимость")
    parser.add_argument("--model", default=None, help="Модель Claude")
    parser.add_argument("--no-interactive", action="store_true", help="Без интерактивного меню")
    args = parser.parse_args()

    is_interactive = (
        not args.no_interactive
        and args.input is None
        and sys.stdin.isatty()
    )

    if is_interactive:
        config = interactive_menu()
    else:
        if not args.input:
            log("Укажите --input или запустите без аргументов для интерактивного режима", "ERROR")
            sys.exit(1)

        input_path = Path(args.input)
        input_files = discover_input_files(input_path)
        if not input_files:
            sys.exit(1)

        fmt_map = {
            "all": ["md", "html", "pdf", "docx"],
            "pdf": ["pdf"], "docx": ["docx"], "html": ["html"], "md": ["md"],
        }
        formats = fmt_map.get(args.format or "all", ["md", "html", "pdf", "docx"])

        default_name = "translated"
        if len(input_files) == 1:
            default_name = input_files[0].stem + "_translated"

        config = {
            "input_files": input_files,
            "lang_pair": args.lang or "en-ru",
            "budget": args.budget,
            "formats": formats,
            "output_dir": Path(args.output_dir or str(ROOT / "output")).resolve(),
            "output_name": args.output or default_name,
            "dry_run": args.dry_run,
            "model": args.model,
        }

    input_files = config["input_files"]
    lang_pair = config["lang_pair"]
    _, _, from_en, to_en = LANGUAGES.get(lang_pair, LANGUAGES["en-ru"])

    # ========================
    # PIPELINE START
    # ========================

    if HAS_RICH:
        console.print()
        console.print(Rule("[bold magenta]ПАЙПЛАЙН ПЕРЕВОДА[/]", style="magenta"))

    # ----- ШАГ 1: Чтение -----
    log("ШАГ 1/4: Чтение входных файлов...", "STEP")

    source_texts = []
    for f in input_files:
        log(f"  {f.name} ({f.suffix.lower()})...")
        text, fmt = read_input_file(f)
        if text.strip():
            source_texts.append(text)
            log(f"    {len(text):,} символов", "OK")
        else:
            log(f"    Пустой файл или ошибка чтения", "WARN")

    if not source_texts:
        log("Нет текста для перевода", "ERROR")
        return

    total_chars = sum(len(t) for t in source_texts)
    log(f"  Итого: {len(source_texts)} файлов, {total_chars:,} символов")

    # ----- ШАГ 2: Прогноз -----
    log("ШАГ 2/4: Прогноз стоимости...", "STEP")

    glossary = load_json(GLOSSARY_PATH)
    system_prompt = build_system_prompt(lang_pair, glossary)
    system_tokens = estimate_tokens(system_prompt)

    if glossary:
        log(f"  Глоссарий: {len(glossary)} терминов")

    forecasts = show_forecast(input_files, source_texts, system_tokens, config["budget"])

    if config["dry_run"]:
        ui_print("[dim]Это была оценка. Уберите --dry-run для запуска перевода.[/]"
                 if HAS_RICH else "Это была оценка.")
        return

    total_cost = sum(fc["est_cost"] for fc in forecasts)
    if HAS_RICH:
        console.print(f"  Направление: [bold]{from_en} -> {to_en}[/]")
        console.print(f"  Форматы выхода: [bold]{', '.join(f.upper() for f in config['formats'])}[/]")
        console.print(f"  [dim]Ctrl+C - остановить после текущего файла[/]")
        if not Confirm.ask("\nЗапустить перевод?", default=True):
            ui_print("[dim]Отменено.[/]")
            return
    else:
        answer = input("Запустить? [Y/n]: ").strip().lower()
        if answer not in ("", "y", "yes", "д", "да"):
            return

    # ----- ШАГ 3: Перевод -----
    log("ШАГ 3/4: Перевод...", "STEP")

    model = config.get("model") or os.getenv("TRANSLATE_MODEL", DEFAULT_MODEL)
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log("ОШИБКА: установите ANTHROPIC_API_KEY", "ERROR")
        log("  export ANTHROPIC_API_KEY='sk-ant-...'")
        sys.exit(1)

    try:
        import anthropic
    except ImportError:
        log("ОШИБКА: pip install anthropic", "ERROR")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    log(f"  Модель: {model}")
    log(f"  Направление: {from_en} -> {to_en}")

    translations = []
    total_stats = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "errors": 0}
    spent = 0.0
    start_time = time.time()

    for i, (f, text) in enumerate(zip(input_files, source_texts), 1):
        if _interrupted:
            log("Остановлено пользователем", "WARN")
            break

        if config["budget"] is not None:
            fc = forecasts[i - 1]
            if spent + fc["est_cost"] > config["budget"]:
                log(f"Бюджет исчерпан: ${spent:.2f} / ${config['budget']:.2f}", "WARN")
                break

        if HAS_RICH:
            console.print(Rule(f"[bold]{f.name}[/]", style="cyan"))

        log(f"  [{i}/{len(input_files)}] {f.name} ({len(text):,} символов)...")

        stats = translate_document(client, model, system_prompt, text, f.name, lang_pair)

        if stats["translated_text"]:
            translations.append(stats)
            total_stats["input_tokens"] += stats["input_tokens"]
            total_stats["output_tokens"] += stats["output_tokens"]
            spent += stats["cost"]
            log(f"    {stats['input_tokens']:,} in + {stats['output_tokens']:,} out = ${stats['cost']:.2f}", "OK")
        else:
            total_stats["errors"] += 1
            log(f"    Ошибка: {stats['status']}", "ERROR")

        if i < len(input_files):
            time.sleep(2)

    if not translations:
        log("Нет переведенных текстов", "ERROR")
        return

    elapsed = time.time() - start_time

    # ----- ШАГ 4: Сборка и генерация -----
    log("ШАГ 4/4: Сборка и генерация файлов...", "STEP")

    assembled_md = assemble_document(translations)

    output_dir = config["output_dir"]
    base_name = config["output_name"]

    title_match = re.search(r'^#\s+(.+)', assembled_md, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else base_name

    if "pdf" in config["formats"]:
        if not (FONT_DIR / "DejaVuSans.ttf").exists():
            log("Шрифты не найдены в fonts/ - PDF будет пропущен", "WARN")
            config["formats"] = [f for f in config["formats"] if f != "pdf"]

    gen_results = generate_outputs(assembled_md, output_dir, base_name,
                                    title, config["formats"])

    # ========================
    # ИТОГИ
    # ========================
    total_cost_actual = calc_cost(total_stats["input_tokens"], total_stats["output_tokens"])

    if HAS_RICH:
        console.print()
        console.print(Rule("[bold green]ГОТОВО[/]", style="green"))
        console.print()

        summary = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        summary.add_column("Key", style="dim")
        summary.add_column("Value", style="bold")

        summary.add_row("Файлов переведено", f"{len(translations)} / {len(input_files)}")
        summary.add_row("Направление", f"{from_en} -> {to_en}")
        summary.add_row("Символов", f"{sum(t['source_chars'] for t in translations):,}")
        summary.add_row("Токены", f"{total_stats['input_tokens']:,} in + {total_stats['output_tokens']:,} out")
        summary.add_row("Стоимость", f"${total_cost_actual:.2f}")
        summary.add_row("Время", format_duration(elapsed))
        summary.add_row("", "")
        summary.add_row("Выходная папка", str(output_dir))
        for fmt, path in gen_results["files"]:
            summary.add_row(f"  {fmt}", path.name)

        if gen_results["errors"]:
            for err in gen_results["errors"]:
                summary.add_row("[red]Ошибка[/]", err)

        console.print(Panel(summary, title="Результат", border_style="green"))
    else:
        print(f"\n{'='*55}")
        print(f"ГОТОВО: {len(translations)} файлов, ${total_cost_actual:.2f}, {format_duration(elapsed)}")
        print(f"Файлы: {output_dir}/")
        for fmt, path in gen_results["files"]:
            print(f"  {fmt}: {path.name}")
        print()


if __name__ == "__main__":
    main()
