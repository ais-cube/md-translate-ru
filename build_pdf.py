#!/usr/bin/env python3
"""
build_pdf.py — Сборка переведённых Markdown-файлов в PDF с вёрсткой и иллюстрациями.

Собирает docs_ru/*.md в единый PDF с:
- Типографской вёрсткой (заголовки, таблицы, code blocks, цитаты)
- Оригинальными или переведёнными иллюстрациями из images/
- Переведёнными подписями из images_ru_text/
- Содержанием, нумерацией страниц, колонтитулами

Требования:
    pip install xhtml2pdf markdown Pygments rich

Использование:
    python build_pdf.py                        # собрать PDF из docs_ru/
    python build_pdf.py --output book_ru.pdf   # указать имя файла
    python build_pdf.py --source docs_en       # собрать из другой папки
    python build_pdf.py --no-images            # без иллюстраций
    python build_pdf.py --translated-images translated_images/  # папка с переведёнными картинками
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Rich (опционально)
# ---------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich.table import Table
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

console = Console() if HAS_RICH else None

# ---------------------------------------------------------------------------
# Пути
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DOCS_RU = ROOT / "docs_ru"
DOCS_EN = ROOT / "docs_en"
IMAGES_DIR = ROOT / "images"
IMAGES_RU_TEXT = ROOT / "images_ru_text"
TRANSLATIONS_JSON = ROOT / "image_translations.json"
GLOSSARY_PATH = ROOT / "glossary.json"

# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def ui_print(msg: str, **kw):
    if HAS_RICH:
        console.print(msg, **kw)
    else:
        clean = re.sub(r'\[/?[^\]]*\]', '', str(msg))
        print(clean)


def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    if HAS_RICH:
        colors = {"INFO": "cyan", "WARN": "yellow", "ERROR": "red", "OK": "green"}
        c = colors.get(level, "white")
        console.print(f"[dim]{ts}[/] [{c}]{level:>5}[/]  {msg}")
    else:
        print(f"[{ts}] [{level}] {msg}")


# ---------------------------------------------------------------------------
# Загрузка переведённых подписей к изображениям
# ---------------------------------------------------------------------------

def load_image_captions() -> dict[str, dict]:
    """Загрузить переведённые alt-тексты и описания из images_ru_text/.

    Возвращает {image_filename: {"alt_ru": "...", "description": "..."}}
    """
    captions = {}

    # Из JSON (если есть)
    if TRANSLATIONS_JSON.exists():
        try:
            data = json.loads(TRANSLATIONS_JSON.read_text(encoding="utf-8"))
            for entry in data:
                if isinstance(entry, dict) and entry.get("filename"):
                    md_text = entry.get("translation_md", "")
                    alt_ru = ""
                    desc = ""

                    # Извлечь "Перевод для alt-текста"
                    alt_match = re.search(
                        r'##\s*Перевод для alt-текста\s*\n+(.+?)(?:\n##|\Z)',
                        md_text, re.DOTALL
                    )
                    if alt_match:
                        alt_ru = alt_match.group(1).strip()

                    # Извлечь "Краткое описание"
                    desc_match = re.search(
                        r'##\s*Краткое описание\s*\n+(.+?)(?:\n##|\Z)',
                        md_text, re.DOTALL
                    )
                    if desc_match:
                        desc = desc_match.group(1).strip()

                    captions[entry["filename"]] = {
                        "alt_ru": alt_ru,
                        "description": desc,
                    }
        except (json.JSONDecodeError, KeyError):
            pass

    # Из отдельных .md файлов (fallback / дополнение)
    if IMAGES_RU_TEXT.exists():
        for md_file in IMAGES_RU_TEXT.glob("*.md"):
            text = md_file.read_text(encoding="utf-8")

            # Имя изображения из заголовка
            name_match = re.match(r'#\s+(\S+)', text)
            if not name_match:
                continue
            img_name = name_match.group(1)

            if img_name in captions:
                continue  # уже из JSON

            alt_ru = ""
            alt_match = re.search(
                r'##\s*Перевод для alt-текста\s*\n+(.+?)(?:\n##|\Z)',
                text, re.DOTALL
            )
            if alt_match:
                alt_ru = alt_match.group(1).strip()

            desc = ""
            desc_match = re.search(
                r'##\s*Краткое описание\s*\n+(.+?)(?:\n##|\Z)',
                text, re.DOTALL
            )
            if desc_match:
                desc = desc_match.group(1).strip()

            captions[img_name] = {"alt_ru": alt_ru, "description": desc}

    return captions


# ---------------------------------------------------------------------------
# Markdown → HTML
# ---------------------------------------------------------------------------

def markdown_to_html(md_text: str) -> str:
    """Конвертировать Markdown в HTML с расширениями."""
    import markdown
    from markdown.extensions.codehilite import CodeHiliteExtension
    from markdown.extensions.tables import TableExtension
    from markdown.extensions.fenced_code import FencedCodeExtension
    from markdown.extensions.toc import TocExtension

    extensions = [
        TableExtension(),
        FencedCodeExtension(),
        CodeHiliteExtension(css_class="codehilite", guess_lang=True),
        TocExtension(permalink=False),
        "markdown.extensions.attr_list",
        "markdown.extensions.def_list",
        "markdown.extensions.admonition",
        "markdown.extensions.md_in_html",
    ]

    html = markdown.markdown(md_text, extensions=extensions)
    return html


def fix_image_paths(html: str, source_dir: Path,
                    translated_images_dir: Path | None,
                    captions: dict[str, dict],
                    include_images: bool = True) -> str:
    """Заменить пути к изображениям и добавить переведённые подписи.

    Приоритет источника изображения:
    1. translated_images_dir (переведённые иллюстрации)
    2. images/ (оригиналы)
    """

    def replace_img(match):
        full_tag = match.group(0)
        src = match.group(1)
        alt = match.group(2) if match.group(2) else ""

        if not include_images:
            # Заменить на подпись
            img_name = Path(src).name
            caption_data = captions.get(img_name, {})
            alt_ru = caption_data.get("alt_ru", alt)
            if alt_ru:
                return f'<p class="image-placeholder">[Иллюстрация: {alt_ru}]</p>'
            return ""

        # Определить имя файла
        img_name = Path(src).name

        # Приоритет: переведённые > оригиналы
        resolved_path = None
        if translated_images_dir:
            candidate = translated_images_dir / img_name
            if candidate.exists():
                resolved_path = candidate

        if not resolved_path:
            # Попробовать относительно source_dir
            candidate = (source_dir / src).resolve()
            if candidate.exists():
                resolved_path = candidate

        if not resolved_path:
            # Попробовать в images/
            candidate = IMAGES_DIR / img_name
            if candidate.exists():
                resolved_path = candidate

        if not resolved_path:
            # Изображение не найдено — вставить placeholder
            caption_data = captions.get(img_name, {})
            alt_ru = caption_data.get("alt_ru", alt)
            return f'<p class="image-placeholder">[Изображение не найдено: {img_name}]</p>'

        # Переведённая подпись
        caption_data = captions.get(img_name, {})
        alt_ru = caption_data.get("alt_ru", alt)

        # Абсолютный путь для weasyprint
        abs_path = resolved_path.resolve().as_uri()

        figure_html = f'<figure class="book-figure">\n'
        figure_html += f'  <img src="{abs_path}" alt="{alt_ru}" />\n'
        if alt_ru:
            figure_html += f'  <figcaption>{alt_ru}</figcaption>\n'
        figure_html += f'</figure>'

        return figure_html

    # Паттерн для img тегов
    html = re.sub(
        r'<img\s+[^>]*src="([^"]+)"[^>]*alt="([^"]*)"[^>]*/?>',
        replace_img, html
    )
    # Также обработать src перед alt
    html = re.sub(
        r'<img\s+[^>]*alt="([^"]*)"[^>]*src="([^"]+)"[^>]*/?>',
        lambda m: replace_img(type('M', (), {'group': lambda s, i: {0: m.group(0), 1: m.group(2), 2: m.group(1)}[i]})()),
        html
    )

    return html


# ---------------------------------------------------------------------------
# CSS — книжная вёрстка
# ---------------------------------------------------------------------------

BOOK_CSS = """
@page {
    size: A4;
    margin: 25mm 20mm 30mm 25mm;

    @frame footer {
        -pdf-frame-content: footerContent;
        bottom: 0mm;
        margin-left: 20mm;
        margin-right: 20mm;
        height: 10mm;
    }
}

/* === Базовая типографика === */

body {
    font-family: CyrSerif, serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #1a1a1a;
}

/* === Заголовки === */

h1 {
    font-family: CyrSans, sans-serif;
    font-size: 24pt;
    font-weight: bold;
    color: #1a3a5c;
    margin-top: 40pt;
    margin-bottom: 16pt;
    border-bottom: 2pt solid #1a3a5c;
    padding-bottom: 8pt;
    -pdf-keep-with-next: true;
}

h2 {
    font-family: CyrSans, sans-serif;
    font-size: 16pt;
    font-weight: bold;
    color: #2c5f8a;
    margin-top: 28pt;
    margin-bottom: 10pt;
    -pdf-keep-with-next: true;
}

h3 {
    font-family: CyrSans, sans-serif;
    font-size: 13pt;
    font-weight: bold;
    color: #3a7ab5;
    margin-top: 20pt;
    margin-bottom: 8pt;
    -pdf-keep-with-next: true;
}

h4, h5, h6 {
    font-family: CyrSans, sans-serif;
    font-weight: bold;
    color: #4a8ac5;
    -pdf-keep-with-next: true;
}

/* === Параграфы === */

p {
    font-family: CyrSerif, serif;
    margin: 0 0 8pt 0;
}

/* === Списки === */

ul, ol {
    margin: 8pt 0 8pt 20pt;
    padding: 0;
}

li {
    font-family: CyrSerif, serif;
    margin-bottom: 4pt;
}

/* === Таблицы === */

table {
    width: 100%;
    border-collapse: collapse;
    margin: 12pt 0;
    font-size: 10pt;
    -pdf-keep-in-frame-mode: shrink;
}

thead tr {
    background-color: #1a3a5c;
    color: white;
}

th {
    font-family: CyrSans, sans-serif;
    font-weight: bold;
    padding: 8pt 10pt;
    text-align: left;
    border: 1pt solid #1a3a5c;
    color: white;
    background-color: #1a3a5c;
}

td {
    font-family: CyrSerif, serif;
    padding: 6pt 10pt;
    border: 1pt solid #ddd;
    vertical-align: top;
}

/* === Код === */

code {
    font-family: CyrMono, monospace;
    font-size: 9pt;
    background-color: #f4f4f8;
    padding: 1pt 4pt;
    color: #c7254e;
}

pre {
    background-color: #f5f5f5;
    color: #333;
    padding: 12pt 16pt;
    font-size: 9pt;
    line-height: 1.5;
    margin: 12pt 0;
    border-left: 4pt solid #1a3a5c;
    font-family: CyrMono, monospace;
    white-space: pre-wrap;
    word-wrap: break-word;
}

pre code {
    background: none;
    padding: 0;
    color: inherit;
    font-size: 9pt;
}

.codehilite {
    background-color: #f5f5f5;
    color: #333;
    padding: 12pt 16pt;
    margin: 12pt 0;
    border-left: 4pt solid #1a3a5c;
}

.codehilite pre {
    background: none;
    padding: 0;
    margin: 0;
    border: none;
    border-left: none;
}

/* === Цитаты === */

blockquote {
    margin: 12pt 0;
    padding: 10pt 16pt;
    border-left: 4pt solid #4a8ac5;
    background-color: #f0f4f8;
    color: #333;
}

blockquote p {
    font-family: CyrSerif, serif;
    margin: 4pt 0;
}

/* === Изображения === */

.book-figure {
    margin: 16pt auto;
    text-align: center;
}

.book-figure img {
    max-width: 100%;
    height: auto;
}

.book-figure figcaption {
    font-family: CyrSans, sans-serif;
    font-size: 9pt;
    color: #666;
    margin-top: 6pt;
    text-align: center;
}

img {
    max-width: 100%;
    height: auto;
}

.image-placeholder {
    text-align: center;
    color: #999;
    padding: 20pt;
    border: 1pt dashed #ccc;
    margin: 12pt 0;
}

/* === Горизонтальная линия === */

hr {
    border: none;
    border-top: 1pt solid #ddd;
    margin: 20pt 0;
}

/* === Ссылки === */

a {
    color: #2c5f8a;
    text-decoration: none;
}

/* === Сильный / курсив === */

strong {
    font-weight: bold;
    color: #1a1a1a;
}

em {
    font-style: italic;
}

/* === Титульная страница === */

.title-page {
    text-align: center;
    padding-top: 200pt;
    page-break-after: always;
}

.title-page h1 {
    font-size: 32pt;
    border: none;
    color: #1a3a5c;
    margin: 0;
}

.title-page .subtitle {
    font-family: CyrSans, sans-serif;
    font-size: 14pt;
    color: #666;
    margin-top: 12pt;
}

.title-page .meta {
    font-family: CyrSans, sans-serif;
    font-size: 10pt;
    color: #999;
    margin-top: 40pt;
}

/* === Содержание === */

.toc {
    page-break-after: always;
}

.toc h1 {
    border-bottom: 2pt solid #1a3a5c;
}

.toc ul {
    list-style: none;
    padding: 0;
    margin: 0;
}

.toc li {
    margin: 4pt 0;
    font-family: CyrSans, sans-serif;
    font-size: 11pt;
}

.toc li.toc-h1 {
    font-weight: bold;
    font-size: 12pt;
    margin-top: 12pt;
    color: #1a3a5c;
}

.toc li.toc-h2 {
    padding-left: 20pt;
    color: #333;
}

.toc li.toc-h3 {
    padding-left: 40pt;
    font-size: 10pt;
    color: #666;
}

/* === Admonitions === */

.admonition {
    padding: 10pt 16pt;
    margin: 12pt 0;
}

.admonition.note {
    background-color: #e8f0fe;
    border-left: 4pt solid #4a8ac5;
}

.admonition.warning {
    background-color: #fff3cd;
    border-left: 4pt solid #ffc107;
}

.admonition-title {
    font-family: CyrSans, sans-serif;
    font-weight: bold;
    margin-bottom: 4pt;
}
"""


# ---------------------------------------------------------------------------
# Pygments CSS для code highlighting (тёмная тема)
# ---------------------------------------------------------------------------

def get_pygments_css() -> str:
    """Сгенерировать CSS для подсветки кода."""
    try:
        from pygments.formatters import HtmlFormatter
        formatter = HtmlFormatter(style="monokai")
        return formatter.get_style_defs('.codehilite')
    except ImportError:
        return ""


# ---------------------------------------------------------------------------
# Сборка HTML-документа
# ---------------------------------------------------------------------------

def extract_headings(md_text: str) -> list[tuple[int, str]]:
    """Извлечь заголовки из Markdown для содержания."""
    headings = []
    for match in re.finditer(r'^(#{1,3})\s+(.+)$', md_text, re.MULTILINE):
        level = len(match.group(1))
        title = match.group(2).strip()
        headings.append((level, title))
    return headings


def build_toc_html(all_headings: list[tuple[int, str]]) -> str:
    """Построить HTML-содержание."""
    lines = ['<div class="toc">', '<h1>Содержание</h1>', '<ul>']
    for level, title in all_headings:
        css_class = f"toc-h{level}"
        clean_title = re.sub(r'[*_`]', '', title)
        lines.append(f'  <li class="{css_class}">{clean_title}</li>')
    lines.append('</ul>')
    lines.append('</div>')
    return '\n'.join(lines)


def build_title_page(title: str = "", subtitle: str = "") -> str:
    """Построить титульную страницу."""
    if not title:
        title = "Перевод документации"

    now = datetime.now().strftime("%d.%m.%Y")

    return f"""
<div class="title-page">
    <h1>{title}</h1>
    <div class="subtitle">{subtitle}</div>
    <div class="meta">
        Автоматический перевод EN → RU<br/>
        Сгенерировано: {now}<br/>
        <em>md-translate-ru</em>
    </div>
</div>
"""


def assemble_html(md_files: list[Path], source_dir: Path,
                  translated_images_dir: Path | None,
                  captions: dict[str, dict],
                  include_images: bool = True,
                  title: str = "",
                  subtitle: str = "") -> str:
    """Собрать единый HTML-документ из списка Markdown-файлов."""

    all_headings = []
    body_parts = []

    for md_path in md_files:
        md_text = md_path.read_text(encoding="utf-8")
        headings = extract_headings(md_text)
        all_headings.extend(headings)

        html_part = markdown_to_html(md_text)
        html_part = fix_image_paths(
            html_part, source_dir, translated_images_dir,
            captions, include_images
        )
        body_parts.append(html_part)

    # Титульная страница
    title_html = build_title_page(title, subtitle)

    # Содержание
    toc_html = build_toc_html(all_headings) if all_headings else ""

    # Pygments CSS
    pygments_css = get_pygments_css()

    # Итоговый HTML
    full_html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8"/>
    <style>
{BOOK_CSS}

{pygments_css}
    </style>
</head>
<body>
{title_html}

{toc_html}

{'<hr/>'.join(body_parts)}
</body>
</html>"""

    return full_html


# ---------------------------------------------------------------------------
# Генерация PDF
# ---------------------------------------------------------------------------

def find_cyrillic_fonts() -> dict[str, dict[str, str]]:
    """Найти TTF-шрифты с кириллицей. Возвращает пути для @font-face."""
    import platform

    result = {}
    system = platform.system()

    if system == "Windows":
        wf = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        candidates = {
            "CyrSerif": {
                "normal": os.path.join(wf, "times.ttf"),
                "bold": os.path.join(wf, "timesbd.ttf"),
                "italic": os.path.join(wf, "timesi.ttf"),
            },
            "CyrSans": {
                "normal": os.path.join(wf, "arial.ttf"),
                "bold": os.path.join(wf, "arialbd.ttf"),
                "italic": os.path.join(wf, "ariali.ttf"),
            },
            "CyrMono": {
                "normal": os.path.join(wf, "cour.ttf"),
                "bold": os.path.join(wf, "courbd.ttf"),
                "italic": os.path.join(wf, "couri.ttf"),
            },
        }
    else:
        dv = "/usr/share/fonts/truetype/dejavu"
        candidates = {
            "CyrSerif": {
                "normal": f"{dv}/DejaVuSerif.ttf",
                "bold": f"{dv}/DejaVuSerif-Bold.ttf",
                "italic": f"{dv}/DejaVuSerif-Italic.ttf",
            },
            "CyrSans": {
                "normal": f"{dv}/DejaVuSans.ttf",
                "bold": f"{dv}/DejaVuSans-Bold.ttf",
                "italic": f"{dv}/DejaVuSans-Oblique.ttf",
            },
            "CyrMono": {
                "normal": f"{dv}/DejaVuSansMono.ttf",
                "bold": f"{dv}/DejaVuSansMono-Bold.ttf",
                "italic": f"{dv}/DejaVuSansMono-Oblique.ttf",
            },
        }

    for family, variants in candidates.items():
        if os.path.exists(variants["normal"]):
            result[family] = {k: v for k, v in variants.items() if os.path.exists(v)}

    return result


def build_font_face_css(fonts: dict[str, dict[str, str]]) -> str:
    """Сгенерировать @font-face CSS блоки из найденных шрифтов."""
    css_parts = []

    style_map = {
        "normal": ("normal", "normal"),
        "bold": ("bold", "normal"),
        "italic": ("normal", "italic"),
    }

    for family, variants in fonts.items():
        for variant, path in variants.items():
            weight, style = style_map.get(variant, ("normal", "normal"))
            # xhtml2pdf принимает абсолютные пути напрямую
            abs_path = os.path.abspath(path).replace("\\", "/")
            css_parts.append(f"""@font-face {{
    font-family: {family};
    src: url("{abs_path}");
    font-weight: {weight};
    font-style: {style};
}}""")

    return "\n\n".join(css_parts)


def generate_pdf(html: str, output_path: Path):
    """Сгенерировать PDF из HTML через xhtml2pdf."""
    from xhtml2pdf import pisa
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.fonts import addMapping

    log("Поиск шрифтов с кириллицей...")
    fonts = find_cyrillic_fonts()

    if fonts:
        log(f"  Найдены: {', '.join(fonts.keys())}", "OK")

        # 1) Зарегистрировать в reportlab
        for family, variants in fonts.items():
            try:
                pdfmetrics.registerFont(TTFont(family, variants["normal"]))
                addMapping(family, 0, 0, family)

                if "bold" in variants:
                    bold_name = f"{family}-Bold"
                    pdfmetrics.registerFont(TTFont(bold_name, variants["bold"]))
                    addMapping(family, 1, 0, bold_name)

                if "italic" in variants:
                    italic_name = f"{family}-Italic"
                    pdfmetrics.registerFont(TTFont(italic_name, variants["italic"]))
                    addMapping(family, 0, 1, italic_name)
            except Exception as e:
                log(f"  Ошибка регистрации {family}: {e}", "WARN")

        # 2) Вставить @font-face в HTML
        font_css = build_font_face_css(fonts)
        html = html.replace("</style>", f"\n{font_css}\n</style>")
    else:
        log("  Кириллические шрифты не найдены — возможны □□□", "WARN")

    log(f"Генерация PDF: {output_path.name}...")

    with open(output_path, "wb") as f:
        status = pisa.CreatePDF(html, dest=f, encoding="utf-8")

    if status.err:
        log(f"xhtml2pdf: {status.err} ошибок при конвертации", "WARN")

    size_mb = output_path.stat().st_size / (1024 * 1024)
    log(f"PDF готов: {output_path} ({size_mb:.1f} MB)", "OK")


# ---------------------------------------------------------------------------
# Интерактивное меню
# ---------------------------------------------------------------------------

def interactive_setup(source_dir: Path) -> dict:
    """Интерактивная настройка сборки PDF."""

    md_files = sorted(source_dir.glob("*.md"))

    if HAS_RICH:
        console.print()
        console.print(Panel(
            f"[bold cyan]build_pdf.py[/] — сборка PDF из переведённых Markdown\n"
            f"[dim]Папка: {source_dir.relative_to(ROOT)}/  •  Файлов: {len(md_files)}[/]",
            title="📖 Сборка PDF",
            border_style="cyan",
        ))

        # Показать файлы
        if md_files:
            table = Table(box=box.SIMPLE, show_header=True)
            table.add_column("#", style="dim", width=4, justify="right")
            table.add_column("Файл", style="bold")
            table.add_column("Размер", justify="right", style="cyan")

            for i, f in enumerate(md_files, 1):
                size_kb = f.stat().st_size / 1024
                table.add_row(str(i), f.name, f"{size_kb:.0f} KB")

            console.print(table)
            console.print()

        # Настройки
        title = Prompt.ask("Заголовок книги", default="Перевод документации")
        subtitle = Prompt.ask("Подзаголовок", default="")

        include_images = Confirm.ask("Включить иллюстрации?", default=True)

        translated_images_dir = None
        if include_images:
            default_dir = ROOT / "translated_images"
            if default_dir.exists():
                use_translated = Confirm.ask(
                    f"Использовать переведённые иллюстрации из {default_dir.name}/?",
                    default=True
                )
                if use_translated:
                    translated_images_dir = default_dir
            else:
                custom = Prompt.ask(
                    "Папка с переведёнными иллюстрациями (Enter = оригиналы)",
                    default=""
                )
                if custom and Path(custom).exists():
                    translated_images_dir = Path(custom)

        output_name = Prompt.ask("Имя файла PDF", default="book_ru.pdf")

        return {
            "title": title,
            "subtitle": subtitle,
            "include_images": include_images,
            "translated_images_dir": translated_images_dir,
            "output": output_name,
        }
    else:
        print(f"\nСборка PDF из {source_dir}/  ({len(md_files)} файлов)")
        title = input("Заголовок [Перевод документации]: ").strip() or "Перевод документации"
        subtitle = input("Подзаголовок []: ").strip()
        include_images = input("Иллюстрации? [Y/n]: ").strip().lower() in ("", "y", "yes", "д")
        output_name = input("Имя PDF [book_ru.pdf]: ").strip() or "book_ru.pdf"

        return {
            "title": title,
            "subtitle": subtitle,
            "include_images": include_images,
            "translated_images_dir": None,
            "output": output_name,
        }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Сборка переведённых Markdown в PDF с вёрсткой и иллюстрациями",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python build_pdf.py                                    # интерактивный режим
  python build_pdf.py --output book_ru.pdf               # указать имя
  python build_pdf.py --source docs_en                   # из другой папки
  python build_pdf.py --translated-images tr_images/     # переведённые картинки
  python build_pdf.py --no-images                        # без картинок
  python build_pdf.py --title "Моя книга" --no-interactive
        """
    )
    parser.add_argument("--source", default="docs_ru", help="Папка с Markdown-файлами (по умолчанию: docs_ru)")
    parser.add_argument("--output", "-o", default="book_ru.pdf", help="Имя выходного PDF")
    parser.add_argument("--translated-images", metavar="DIR", help="Папка с переведёнными иллюстрациями")
    parser.add_argument("--no-images", action="store_true", help="Не включать иллюстрации")
    parser.add_argument("--title", default="", help="Заголовок книги")
    parser.add_argument("--subtitle", default="", help="Подзаголовок")
    parser.add_argument("--no-interactive", action="store_true", help="Без интерактивного режима")
    parser.add_argument("--html-only", action="store_true", help="Только HTML (без PDF)")
    args = parser.parse_args()

    source_dir = ROOT / args.source
    if not source_dir.exists():
        log(f"Папка не найдена: {source_dir}", "ERROR")
        sys.exit(1)

    md_files = sorted(source_dir.glob("*.md"))
    if not md_files:
        log(f"Нет .md файлов в {source_dir}", "ERROR")
        sys.exit(1)

    # Интерактивный режим
    is_interactive = (
        not args.no_interactive
        and not args.title
        and not args.no_images
        and not args.translated_images
        and sys.stdin.isatty()
    )

    if is_interactive:
        setup = interactive_setup(source_dir)
        title = setup["title"]
        subtitle = setup["subtitle"]
        include_images = setup["include_images"]
        translated_images_dir = setup["translated_images_dir"]
        output_name = setup["output"]
    else:
        title = args.title or "Перевод документации"
        subtitle = args.subtitle or ""
        include_images = not args.no_images
        translated_images_dir = Path(args.translated_images) if args.translated_images else None
        output_name = args.output

    output_path = ROOT / output_name

    # Проверка зависимостей
    try:
        import markdown
    except ImportError:
        log("ОШИБКА: pip install markdown", "ERROR")
        sys.exit(1)

    if not args.html_only:
        try:
            import xhtml2pdf
        except ImportError:
            log("ОШИБКА: pip install xhtml2pdf", "ERROR")
            sys.exit(1)

    # Загрузка переведённых подписей
    log("Загрузка конфигурации...")
    captions = load_image_captions()
    log(f"  Подписи к изображениям: {len(captions)}")

    if translated_images_dir:
        img_count = len(list(translated_images_dir.glob("*")))
        log(f"  Переведённые иллюстрации: {img_count} из {translated_images_dir.name}/")

    log(f"  Файлов для сборки: {len(md_files)}")
    for f in md_files:
        log(f"    • {f.name}")

    # Сборка HTML
    log("Сборка HTML...")

    if HAS_RICH:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=30),
            console=console,
        ) as progress:
            task = progress.add_task("Конвертация Markdown → HTML", total=1)
            full_html = assemble_html(
                md_files, source_dir, translated_images_dir,
                captions, include_images, title, subtitle
            )
            progress.update(task, advance=1)
    else:
        full_html = assemble_html(
            md_files, source_dir, translated_images_dir,
            captions, include_images, title, subtitle
        )

    # Сохранить HTML (для отладки или --html-only)
    html_path = output_path.with_suffix(".html")
    html_path.write_text(full_html, encoding="utf-8")
    log(f"HTML сохранён: {html_path}", "OK")

    if args.html_only:
        log("Режим --html-only, PDF не генерируется.")
        return

    # Генерация PDF
    try:
        generate_pdf(full_html, output_path)
    except Exception as e:
        log(f"Ошибка генерации PDF: {e}", "ERROR")
        log("HTML сохранён — можно открыть в браузере и распечатать в PDF.")
        sys.exit(1)

    # Итог
    if HAS_RICH:
        console.print()
        console.print(Panel(
            f"[bold green]✓ PDF готов![/]\n\n"
            f"  📄 {output_path.name}  ({output_path.stat().st_size / 1024 / 1024:.1f} MB)\n"
            f"  📝 {len(md_files)} файлов  •  {len(captions)} иллюстраций с подписями\n"
            f"  🖼  {'переведённые' if translated_images_dir else 'оригинальные'} иллюстрации",
            title="📖 Готово",
            border_style="green",
        ))
    else:
        print(f"\n✓ PDF готов: {output_path}")
        print(f"  {len(md_files)} файлов, {output_path.stat().st_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
