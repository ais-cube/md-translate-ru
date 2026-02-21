#!/usr/bin/env python3
"""
convert.py - Конвертер переведенных Markdown-файлов в PDF и HTML.

Берет файлы из docs_ru/ и генерирует:
  - docs_ru/*.md   (уже есть после перевода)
  - output_pdf/*.pdf
  - output_html/*.html

Использует локальные шрифты из fonts/ - никаких внешних зависимостей.

Требования:
    pip install fpdf2 markdown pymdown-extensions

Использование:
    # Конвертировать все переведенные файлы
    python convert.py

    # Конвертировать конкретный файл
    python convert.py --file 01_Introduction.md

    # Только PDF
    python convert.py --format pdf

    # Только HTML
    python convert.py --format html

    # Указать входную/выходную папки
    python convert.py --input docs_ru --output-dir output
"""

import argparse
import re
import sys
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Rich / Fallback
# ---------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

console = Console() if HAS_RICH else None


def ui_print(msg: str):
    if HAS_RICH:
        console.print(msg)
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
# Пути
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
FONT_DIR = ROOT / "fonts"
DEFAULT_INPUT = ROOT / "docs_ru"

# ---------------------------------------------------------------------------
# HTML-шаблон
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        @font-face {{
            font-family: 'DejaVu Sans';
            src: url('data:font/ttf;base64,') format('truetype');
        }}
        * {{
            box-sizing: border-box;
        }}
        body {{
            font-family: 'DejaVu Sans', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.7;
            color: #1a1a2e;
            background: #fafbfc;
            max-width: 860px;
            margin: 0 auto;
            padding: 40px 32px;
        }}
        h1 {{
            font-size: 2em;
            border-bottom: 3px solid #4361ee;
            padding-bottom: 12px;
            margin-top: 40px;
            color: #16213e;
        }}
        h2 {{
            font-size: 1.5em;
            border-bottom: 1px solid #dee2e6;
            padding-bottom: 8px;
            margin-top: 36px;
            color: #1a1a2e;
        }}
        h3 {{
            font-size: 1.25em;
            margin-top: 28px;
            color: #2d3436;
        }}
        h4, h5, h6 {{
            margin-top: 24px;
            color: #2d3436;
        }}
        p {{
            margin: 12px 0;
        }}
        a {{
            color: #4361ee;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
        code {{
            background: #e9ecef;
            padding: 2px 6px;
            border-radius: 4px;
            font-family: 'DejaVu Sans Mono', 'Consolas', 'Courier New', monospace;
            font-size: 0.9em;
            color: #d63384;
        }}
        pre {{
            background: #1e1e2e;
            color: #cdd6f4;
            padding: 20px;
            border-radius: 8px;
            overflow-x: auto;
            line-height: 1.5;
            margin: 16px 0;
        }}
        pre code {{
            background: none;
            color: inherit;
            padding: 0;
            font-size: 0.88em;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 16px 0;
        }}
        th, td {{
            border: 1px solid #dee2e6;
            padding: 10px 14px;
            text-align: left;
        }}
        th {{
            background: #f1f3f5;
            font-weight: 600;
        }}
        tr:nth-child(even) {{
            background: #f8f9fa;
        }}
        blockquote {{
            border-left: 4px solid #4361ee;
            margin: 16px 0;
            padding: 12px 20px;
            background: #eef2ff;
            color: #3b3b5c;
        }}
        blockquote p {{
            margin: 4px 0;
        }}
        img {{
            max-width: 100%;
            height: auto;
            border-radius: 8px;
            margin: 16px 0;
        }}
        ul, ol {{
            padding-left: 28px;
        }}
        li {{
            margin: 4px 0;
        }}
        hr {{
            border: none;
            border-top: 2px solid #dee2e6;
            margin: 32px 0;
        }}
        .meta {{
            color: #868e96;
            font-size: 0.85em;
            border-top: 1px solid #dee2e6;
            padding-top: 16px;
            margin-top: 48px;
        }}
        @media print {{
            body {{
                max-width: none;
                padding: 20px;
            }}
            pre {{
                white-space: pre-wrap;
                word-wrap: break-word;
            }}
        }}
    </style>
</head>
<body>
{content}
<div class="meta">
    Сгенерировано: {date} | md-translate-ru
</div>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Markdown -> HTML
# ---------------------------------------------------------------------------

def md_to_html(md_text: str, title: str = "") -> str:
    """Конвертировать Markdown в HTML с красивым оформлением."""
    import markdown
    extensions = [
        'markdown.extensions.tables',
        'markdown.extensions.fenced_code',
        'markdown.extensions.codehilite',
        'markdown.extensions.toc',
        'markdown.extensions.nl2br',
        'markdown.extensions.sane_lists',
    ]
    extension_configs = {
        'markdown.extensions.codehilite': {
            'css_class': 'highlight',
            'guess_lang': False,
        },
        'markdown.extensions.toc': {
            'permalink': False,
        },
    }

    html_content = markdown.markdown(
        md_text,
        extensions=extensions,
        extension_configs=extension_configs,
    )

    if not title:
        # Извлечь заголовок из первого # в markdown
        match = re.search(r'^#\s+(.+)', md_text, re.MULTILINE)
        title = match.group(1).strip() if match else "Документ"

    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    return HTML_TEMPLATE.format(
        title=title,
        content=html_content,
        date=date_str,
    )


# ---------------------------------------------------------------------------
# Markdown -> PDF (через fpdf2 + локальные шрифты)
# ---------------------------------------------------------------------------

class RuPDF:
    """PDF-генератор с поддержкой кириллицы через локальные шрифты."""

    def __init__(self, font_dir: Path = FONT_DIR):
        from fpdf import FPDF

        self.pdf = FPDF()
        self.pdf.set_auto_page_break(auto=True, margin=20)
        self.font_dir = font_dir

        # Регистрация шрифтов
        self._register_fonts()

    def _register_fonts(self):
        """Зарегистрировать шрифты из папки fonts/."""
        font_map = {
            "DejaVuSans": {
                "": "DejaVuSans.ttf",
                "B": "DejaVuSans-Bold.ttf",
                "I": "DejaVuSans-Oblique.ttf",
                "BI": "DejaVuSans-BoldOblique.ttf",
            },
            "DejaVuMono": {
                "": "DejaVuSansMono.ttf",
                "B": "DejaVuSansMono-Bold.ttf",
            },
        }

        for family, styles in font_map.items():
            for style, filename in styles.items():
                font_path = self.font_dir / filename
                if font_path.exists():
                    self.pdf.add_font(family, style, str(font_path))
                else:
                    log(f"Шрифт не найден: {font_path}", "WARN")

    def generate(self, md_text: str, output_path: Path, title: str = ""):
        """Сгенерировать PDF из Markdown-текста."""
        pdf = self.pdf
        pdf.add_page()

        # Основной шрифт
        pdf.set_font("DejaVuSans", size=10)

        lines = md_text.split('\n')
        in_code_block = False
        code_buffer = []

        for line in lines:
            # Code blocks
            if line.strip().startswith('```'):
                if in_code_block:
                    # Закрытие блока кода
                    self._render_code_block(code_buffer)
                    code_buffer = []
                    in_code_block = False
                else:
                    in_code_block = True
                continue

            if in_code_block:
                code_buffer.append(line)
                continue

            stripped = line.strip()

            # Пустая строка
            if not stripped:
                pdf.ln(4)
                continue

            # Заголовки
            if stripped.startswith('#'):
                level = len(stripped) - len(stripped.lstrip('#'))
                text = stripped.lstrip('#').strip()
                self._render_heading(text, level)
                continue

            # Горизонтальная линия
            if stripped in ('---', '***', '___'):
                pdf.ln(4)
                x = pdf.get_x()
                y = pdf.get_y()
                pdf.set_draw_color(200, 200, 200)
                pdf.line(x, y, x + pdf.epw, y)
                pdf.ln(8)
                continue

            # Таблицы (простая обработка)
            if stripped.startswith('|') and stripped.endswith('|'):
                if re.match(r'^\|[\s\-:|]+\|$', stripped):
                    continue  # Пропуск разделителя таблицы
                cells = [c.strip() for c in stripped.split('|')[1:-1]]
                self._render_table_row(cells)
                continue

            # Цитаты
            if stripped.startswith('>'):
                text = stripped.lstrip('>').strip()
                self._render_blockquote(text)
                continue

            # Списки
            list_match = re.match(r'^(\s*)([-*+]|\d+\.)\s+(.+)', line)
            if list_match:
                indent = len(list_match.group(1))
                marker = list_match.group(2)
                text = list_match.group(3)
                self._render_list_item(text, indent, marker)
                continue

            # Обычный текст (с обработкой inline-элементов)
            self._render_paragraph(stripped)

        pdf.output(str(output_path))

    def _render_heading(self, text: str, level: int):
        pdf = self.pdf
        sizes = {1: 20, 2: 16, 3: 13, 4: 11, 5: 10, 6: 10}
        size = sizes.get(level, 10)

        pdf.ln(6 if level <= 2 else 4)
        pdf.set_font("DejaVuSans", "B", size)
        pdf.set_text_color(26, 26, 46)
        pdf.multi_cell(0, size * 0.6, self._clean_text(text))

        if level <= 2:
            pdf.set_draw_color(67, 97, 238)
            pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + pdf.epw, pdf.get_y())

        pdf.ln(4)
        pdf.set_font("DejaVuSans", size=10)
        pdf.set_text_color(30, 30, 30)

    def _render_code_block(self, lines: list):
        pdf = self.pdf
        pdf.ln(2)

        # Фон
        x = pdf.get_x()
        y = pdf.get_y()
        code_text = '\n'.join(lines)

        pdf.set_fill_color(30, 30, 46)
        pdf.set_text_color(205, 214, 244)
        pdf.set_font("DejaVuMono", size=8)

        # Вычислить высоту
        line_h = 4.5
        total_h = max(len(lines) * line_h + 12, 16)

        # Проверка перехода страницы
        if y + total_h > pdf.h - 20:
            pdf.add_page()
            x = pdf.get_x()
            y = pdf.get_y()

        pdf.set_xy(x, y)
        pdf.rect(x, y, pdf.epw, total_h, 'F')

        pdf.set_xy(x + 6, y + 6)
        for line in lines:
            clean = self._clean_text(line)
            if len(clean) > 95:
                clean = clean[:92] + "..."
            pdf.cell(0, line_h, clean)
            pdf.ln(line_h)
            pdf.set_x(x + 6)

        pdf.set_xy(x, y + total_h + 4)
        pdf.set_font("DejaVuSans", size=10)
        pdf.set_text_color(30, 30, 30)

    def _render_blockquote(self, text: str):
        pdf = self.pdf
        x = pdf.get_x()
        y = pdf.get_y()

        pdf.set_fill_color(238, 242, 255)
        pdf.set_draw_color(67, 97, 238)

        # Отступ + фон
        pdf.set_x(x + 8)
        pdf.set_font("DejaVuSans", "I", 10)
        pdf.set_text_color(59, 59, 92)
        pdf.multi_cell(pdf.epw - 16, 6, self._clean_text(text))
        pdf.ln(2)

        pdf.set_font("DejaVuSans", size=10)
        pdf.set_text_color(30, 30, 30)

    def _render_list_item(self, text: str, indent: int, marker: str):
        pdf = self.pdf
        offset = 10 + (indent // 2) * 6

        pdf.set_x(pdf.l_margin + offset)

        if marker in ('-', '*', '+'):
            bullet = "\u2022 "
        else:
            bullet = f"{marker} "

        pdf.set_font("DejaVuSans", size=10)
        pdf.multi_cell(pdf.epw - offset - 4, 6, bullet + self._clean_text(text))
        pdf.ln(1)

    def _render_table_row(self, cells: list):
        pdf = self.pdf
        n_cols = len(cells)
        if n_cols == 0:
            return
        col_w = pdf.epw / n_cols

        pdf.set_font("DejaVuSans", size=9)
        for cell in cells:
            clean = self._clean_text(cell)
            if len(clean) > 40:
                clean = clean[:37] + "..."
            pdf.cell(col_w, 7, clean, border=1)
        pdf.ln()
        pdf.set_font("DejaVuSans", size=10)

    def _render_paragraph(self, text: str):
        pdf = self.pdf
        pdf.set_font("DejaVuSans", size=10)
        pdf.set_text_color(30, 30, 30)
        pdf.multi_cell(0, 6, self._clean_text(text))
        pdf.ln(2)

    def _clean_text(self, text: str) -> str:
        """Убрать Markdown-разметку из текста для PDF."""
        # Bold/italic
        text = re.sub(r'\*\*\*(.+?)\*\*\*', r'\1', text)
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)
        text = re.sub(r'_(.+?)_', r'\1', text)
        # Inline code
        text = re.sub(r'`(.+?)`', r'\1', text)
        # Links
        text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
        # Images
        text = re.sub(r'!\[(.+?)\]\(.+?\)', r'[\1]', text)
        # Strikethrough
        text = re.sub(r'~~(.+?)~~', r'\1', text)
        # Em dash normalization
        text = text.replace('-', '-')
        return text


def md_to_pdf(md_text: str, output_path: Path, title: str = ""):
    """Конвертировать Markdown в PDF."""
    gen = RuPDF(font_dir=FONT_DIR)
    gen.generate(md_text, output_path, title)


# ---------------------------------------------------------------------------
# Основной процесс
# ---------------------------------------------------------------------------

def get_md_files(input_dir: Path, specific_file: str = None) -> list[Path]:
    """Получить список .md файлов для конвертации."""
    if not input_dir.exists():
        log(f"Папка не найдена: {input_dir}", "ERROR")
        log(f"Сначала запустите перевод: python translate_api.py")
        return []

    if specific_file:
        path = input_dir / specific_file
        if path.exists():
            return [path]
        else:
            log(f"Файл не найден: {path}", "ERROR")
            return []

    files = sorted(input_dir.glob("*.md"))
    return files


def convert_file(md_path: Path, output_dir: Path, formats: list[str]) -> dict:
    """Конвертировать один .md файл в указанные форматы."""
    md_text = md_path.read_text(encoding="utf-8")
    stem = md_path.stem
    title_match = re.search(r'^#\s+(.+)', md_text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else stem

    result = {"file": md_path.name, "formats": [], "errors": []}

    # MD (копия в выходную папку)
    if "md" in formats:
        md_out = output_dir / "md" / md_path.name
        md_out.parent.mkdir(parents=True, exist_ok=True)
        md_out.write_text(md_text, encoding="utf-8")
        result["formats"].append(("md", md_out))

    # HTML
    if "html" in formats:
        try:
            html_out = output_dir / "html" / f"{stem}.html"
            html_out.parent.mkdir(parents=True, exist_ok=True)
            html_content = md_to_html(md_text, title)
            html_out.write_text(html_content, encoding="utf-8")
            result["formats"].append(("html", html_out))
        except Exception as e:
            result["errors"].append(f"HTML: {e}")
            log(f"  Ошибка HTML: {e}", "ERROR")

    # PDF
    if "pdf" in formats:
        try:
            pdf_out = output_dir / "pdf" / f"{stem}.pdf"
            pdf_out.parent.mkdir(parents=True, exist_ok=True)
            md_to_pdf(md_text, pdf_out, title)
            result["formats"].append(("pdf", pdf_out))
        except Exception as e:
            result["errors"].append(f"PDF: {e}")
            log(f"  Ошибка PDF: {e}", "ERROR")

    return result


def show_results(results: list[dict]):
    """Показать итоговую таблицу."""
    if HAS_RICH:
        table = Table(
            title="Результаты конвертации",
            box=box.ROUNDED,
            show_lines=True,
            title_style="bold cyan",
        )
        table.add_column("#", style="dim", width=4, justify="right")
        table.add_column("Файл", style="bold white", max_width=30)
        table.add_column("MD", justify="center", width=6)
        table.add_column("HTML", justify="center", width=6)
        table.add_column("PDF", justify="center", width=6)
        table.add_column("Ошибки", style="red", max_width=30)

        for i, r in enumerate(results, 1):
            fmts = {f[0] for f in r["formats"]}
            table.add_row(
                str(i),
                r["file"],
                "[green]OK[/]" if "md" in fmts else "[dim]-[/]",
                "[green]OK[/]" if "html" in fmts else "[dim]-[/]",
                "[green]OK[/]" if "pdf" in fmts else "[dim]-[/]",
                "; ".join(r["errors"]) if r["errors"] else "",
            )

        console.print()
        console.print(table)
        console.print()
    else:
        print(f"\n{'='*60}")
        print("РЕЗУЛЬТАТЫ КОНВЕРТАЦИИ")
        print(f"{'='*60}")
        for i, r in enumerate(results, 1):
            fmts = ", ".join(f[0] for f in r["formats"])
            errors = "; ".join(r["errors"]) if r["errors"] else ""
            print(f"  {i:2d}. {r['file']:<28s} [{fmts}]  {errors}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Конвертер Markdown в PDF + HTML",
        epilog="""
Примеры:
  python convert.py                        # все файлы, все форматы
  python convert.py --file 01_Intro.md     # конкретный файл
  python convert.py --format pdf           # только PDF
  python convert.py --format html          # только HTML
  python convert.py --input docs_ru        # указать входную папку
        """
    )
    parser.add_argument("--file", help="Конвертировать конкретный файл")
    parser.add_argument("--format", choices=["pdf", "html", "md", "all"], default="all",
                        help="Формат выхода (по умолчанию: all)")
    parser.add_argument("--input", default=None,
                        help=f"Входная папка с .md файлами (по умолчанию: docs_ru)")
    parser.add_argument("--output-dir", default=None,
                        help="Выходная папка (по умолчанию: output)")

    args = parser.parse_args()

    input_dir = Path(args.input).resolve() if args.input else DEFAULT_INPUT
    output_dir = Path(args.output_dir).resolve() if args.output_dir else ROOT / "output"

    if args.format == "all":
        formats = ["md", "html", "pdf"]
    else:
        formats = [args.format]

    # Приветствие
    if HAS_RICH:
        console.print()
        console.print(Panel(
            "[bold cyan]convert.py[/] - генерация PDF + HTML из переведенных Markdown\n"
            f"[dim]Вход: {input_dir} | Выход: {output_dir} | Форматы: {', '.join(formats)}[/]",
            title="Конвертер",
            border_style="cyan",
        ))
    else:
        print(f"\nКонвертер: {input_dir} -> {output_dir} [{', '.join(formats)}]")

    # Проверка шрифтов
    if "pdf" in formats:
        font_file = FONT_DIR / "DejaVuSans.ttf"
        if not font_file.exists():
            log(f"Шрифты не найдены в {FONT_DIR}/", "ERROR")
            log("Положите .ttf файлы в папку fonts/")
            log("Или установите: apt install fonts-dejavu-core")
            sys.exit(1)
        log(f"Шрифты: {FONT_DIR} ({len(list(FONT_DIR.glob('*.ttf')))} файлов)")

    # Получить файлы
    files = get_md_files(input_dir, args.file)
    if not files:
        return

    log(f"Файлов для конвертации: {len(files)}")

    # Конвертация
    results = []
    for i, md_path in enumerate(files, 1):
        log(f"[{i}/{len(files)}] {md_path.name}...")
        result = convert_file(md_path, output_dir, formats)
        results.append(result)
        for fmt, path in result["formats"]:
            try:
                display_path = path.relative_to(ROOT)
            except ValueError:
                display_path = path
            log(f"  {fmt.upper()}: {display_path}", "OK")

    show_results(results)

    # Итого
    total_ok = sum(len(r["formats"]) for r in results)
    total_err = sum(len(r["errors"]) for r in results)

    if HAS_RICH:
        console.print(Panel(
            f"[bold green]Готово![/] Создано файлов: {total_ok}"
            + (f", ошибок: [red]{total_err}[/]" if total_err else "")
            + f"\n\n[dim]Файлы в папке:[/] {output_dir}/",
            border_style="green",
        ))
    else:
        print(f"Готово! Создано: {total_ok}, ошибок: {total_err}")
        print(f"Файлы в: {output_dir}/")


if __name__ == "__main__":
    main()
