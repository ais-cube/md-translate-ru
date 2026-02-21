#!/usr/bin/env python3
"""
translate_api.py - Автоматический переводчик EN->RU через Anthropic API.

Реплицирует логику агента TRANSLATOR из пайплайна AgenticDesignPatternsRU,
но работает через API без лимитов подписки Claude Pro.

Требования:
    pip install anthropic rich

Использование:
    # Интерактивный режим (меню выбора)
    python translate_api.py

    # Перевести конкретный файл
    python translate_api.py --file 32_Glossary.md

    # Перевести все файлы заново (перезапись)
    python translate_api.py --force

    # Только оценить стоимость, без перевода
    python translate_api.py --dry-run

    # Ограничить бюджет (USD)
    python translate_api.py --budget 5.00

    # Использовать Batch API (в 2 раза дешевле, результат до 24ч)
    python translate_api.py --batch

Переменные окружения:
    ANTHROPIC_API_KEY  - ключ API (обязателен)
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
# Rich / Fallback UI
# ---------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
    from rich.prompt import Prompt, Confirm, IntPrompt
    from rich.text import Text
    from rich.rule import Rule
    from rich.columns import Columns
    from rich.live import Live
    from rich.markup import escape
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

console = Console() if HAS_RICH else None

# ---------------------------------------------------------------------------
# Пути
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DOCS_EN = ROOT / "docs_en"
DOCS_RU = ROOT / "docs_ru"
GLOSSARY_PATH = ROOT / "glossary.json"
GLOSSARY_CANDIDATES_PATH = ROOT / "glossary_candidates.json"
TRANSLATE_SPEC = ROOT / "TRANSLATE.md"
HUMANIZER_SPEC = ROOT / "HUMANIZER.md"

# ---------------------------------------------------------------------------
# Настройки API
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
MAX_OUTPUT_TOKENS = 16384
CHUNK_SIZE_CHARS = 40000

# Стоимость (USD за 1M токенов) - Sonnet 4.5
COST_INPUT_PER_M = 3.0
COST_OUTPUT_PER_M = 15.0

# Средняя скорость перевода (секунд на 1000 символов) - эмпирическая оценка
AVG_SECONDS_PER_1K_CHARS = 3.5

# ---------------------------------------------------------------------------
# Graceful interrupt
# ---------------------------------------------------------------------------
_interrupted = False

def _signal_handler(signum, frame):
    global _interrupted
    if _interrupted:
        # Повторный Ctrl+C - аварийный выход
        ui_print("\n[bold red]Принудительный выход.[/]" if HAS_RICH else "\nПринудительный выход.")
        sys.exit(1)
    _interrupted = True
    ui_print(
        "\n[yellow]⏸  Ctrl+C - перевод остановится после текущего файла. "
        "Нажмите ещё раз для немедленного выхода.[/]"
        if HAS_RICH else
        "\nCtrl+C - остановка после текущего файла. Ещё раз - выход."
    )

signal.signal(signal.SIGINT, _signal_handler)

# ---------------------------------------------------------------------------
# UI Helpers
# ---------------------------------------------------------------------------

def ui_print(msg: str, **kwargs):
    if HAS_RICH:
        console.print(msg, **kwargs)
    else:
        # Strip rich markup for fallback
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
# Утилиты
# ---------------------------------------------------------------------------

def load_json(path: Path) -> list:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            log(f"ОШИБКА: {path.name} не является JSON-массивом", "ERROR")
            sys.exit(1)
        return data
    except json.JSONDecodeError as e:
        log(f"ОШИБКА: невалидный JSON в {path.name}: {e}", "ERROR")
        sys.exit(1)


def save_json(path: Path, data: list):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def estimate_tokens(text: str) -> int:
    return len(text) // 3


def calc_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * COST_INPUT_PER_M + output_tokens * COST_OUTPUT_PER_M) / 1_000_000


def format_cost(input_tokens: int, output_tokens: int) -> str:
    return f"${calc_cost(input_tokens, output_tokens):.2f}"


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


def brief_description(text: str, max_lines: int = 2) -> str:
    """Извлечь краткое описание из Markdown-файла (первый параграф после заголовка)."""
    lines = text.strip().splitlines()
    desc_lines = []
    past_heading = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            past_heading = True
            continue
        if past_heading and stripped:
            desc_lines.append(stripped)
            if len(desc_lines) >= max_lines:
                break
        elif past_heading and not stripped and desc_lines:
            break
    desc = " ".join(desc_lines)
    if len(desc) > 120:
        desc = desc[:117] + "..."
    return desc or "(нет описания)"


# ---------------------------------------------------------------------------
# Системный промпт (без изменений)
# ---------------------------------------------------------------------------

def build_system_prompt(glossary: list) -> str:
    parts = []
    parts.append("Ты - профессиональный технический переводчик EN->RU.")
    parts.append("Следуй приведённым ниже правилам ТОЧНО и БЕЗ ОТКЛОНЕНИЙ.\n")

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
        parts.append("Если term_en встречается в тексте - использовать ТОЛЬКО term_ru.")
        parts.append("=" * 60)
        glossary_text = "\n".join(
            f"- {e['term_en']} -> {e['term_ru']}" for e in glossary
        )
        parts.append(glossary_text)

    return "\n\n".join(parts)


def build_user_prompt(source_text: str, filename: str, is_chunk: bool = False,
                      chunk_num: int = 0, total_chunks: int = 0) -> str:
    chunk_info = ""
    if is_chunk:
        chunk_info = f"\n\nЭто чанк {chunk_num}/{total_chunks}. Переводи только этот фрагмент."

    return f"""Переведи следующий Markdown-документ с английского на русский.

Файл: {filename}{chunk_info}

КРИТИЧЕСКИ ВАЖНО:
1. Сохрани структуру Markdown 1:1 (заголовки, списки, таблицы, code blocks, ссылки, изображения).
2. НЕ переводи: code blocks, URL, адреса контрактов, идентификаторы, тикеры.
3. Используй ТОЛЬКО канонические термины из глоссария.
4. Числовой формат: разделитель тысяч пробел (1 000), десятичная запятая (15,73), проценты без пробела (0,5%).
5. Модальности: must -> должен, should -> следует, can/may -> может, is required to -> обязан.
6. Выполни anti-AI cleanup по HUMANIZER.md (убери штампы), но НЕ добавляй личный голос.
7. Верни ТОЛЬКО переведённый текст. Без комментариев, пояснений, обёрток.

---НАЧАЛО ИСХОДНОГО ТЕКСТА---

{source_text}

---КОНЕЦ ИСХОДНОГО ТЕКСТА---

Верни ТОЛЬКО перевод. Без преамбулы, без пост-скриптума."""


# ---------------------------------------------------------------------------
# Разбивка на чанки
# ---------------------------------------------------------------------------

def split_into_chunks(text: str, max_chars: int = CHUNK_SIZE_CHARS) -> list[str]:
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


# ---------------------------------------------------------------------------
# Forecast - прогноз стоимости и времени
# ---------------------------------------------------------------------------

def forecast_files(files: list[Path], system_prompt_tokens: int) -> list[dict]:
    """Рассчитать прогноз для каждого файла."""
    forecasts = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        chunks = split_into_chunks(text)
        est_input = estimate_tokens(text) + system_prompt_tokens
        est_output = int(estimate_tokens(text) * 1.15)
        est_cost = calc_cost(est_input, est_output)
        est_time = len(text) / 1000 * AVG_SECONDS_PER_1K_CHARS
        desc = brief_description(text)

        forecasts.append({
            "path": f,
            "name": f.name,
            "chars": len(text),
            "chunks": len(chunks),
            "est_input": est_input,
            "est_output": est_output,
            "est_cost": est_cost,
            "est_time": est_time,
            "description": desc,
        })
    return forecasts


def show_forecast_table(forecasts: list[dict], budget: float | None = None):
    """Показать красивую таблицу с прогнозом."""
    total_cost = sum(fc["est_cost"] for fc in forecasts)
    total_time = sum(fc["est_time"] for fc in forecasts)
    total_chars = sum(fc["chars"] for fc in forecasts)

    if HAS_RICH:
        table = Table(
            title="📋 Прогноз перевода",
            box=box.ROUNDED,
            show_lines=True,
            title_style="bold cyan",
        )
        table.add_column("#", style="dim", width=4, justify="right")
        table.add_column("Файл", style="bold white", max_width=28)
        table.add_column("Описание", style="dim", max_width=40)
        table.add_column("Символы", justify="right", style="cyan")
        table.add_column("Чанки", justify="center")
        table.add_column("≈ Время", justify="right", style="yellow")
        table.add_column("≈ Цена", justify="right", style="green")

        for i, fc in enumerate(forecasts, 1):
            table.add_row(
                str(i),
                fc["name"],
                fc["description"][:40],
                f"{fc['chars']:,}",
                str(fc["chunks"]),
                format_duration(fc["est_time"]),
                f"${fc['est_cost']:.2f}",
            )

        # Итого
        table.add_section()
        budget_note = ""
        if budget is not None:
            remaining = budget - total_cost
            if remaining < 0:
                budget_note = f"  [red]⚠ бюджет ${budget:.2f}, не хватает ${-remaining:.2f}[/]"
            else:
                budget_note = f"  [green]✓ бюджет ${budget:.2f}, остаток ${remaining:.2f}[/]"

        table.add_row(
            "",
            f"[bold]ИТОГО: {len(forecasts)} файлов[/]",
            "",
            f"[bold]{total_chars:,}[/]",
            "",
            f"[bold]{format_duration(total_time)}[/]",
            f"[bold green]${total_cost:.2f}[/]" + budget_note,
        )
        console.print()
        console.print(table)
        console.print()

        # Предупреждение о бюджете
        if budget is not None and total_cost > budget:
            console.print(Panel(
                f"[red bold]⚠ Прогнозируемая стоимость ${total_cost:.2f} превышает бюджет ${budget:.2f}[/]\n"
                f"Будут переведены файлы, пока хватает бюджета. Перевод остановится автоматически.",
                title="Предупреждение о бюджете",
                border_style="red",
            ))
    else:
        print(f"\n{'='*70}")
        print(f"ПРОГНОЗ ПЕРЕВОДА - {len(forecasts)} файлов")
        print(f"{'='*70}")
        for i, fc in enumerate(forecasts, 1):
            print(f"  {i:2d}. {fc['name']:<28s}  {fc['chars']:>8,} симв  "
                  f"{format_duration(fc['est_time']):>8s}  ${fc['est_cost']:.2f}")
            print(f"      {fc['description'][:60]}")
        print(f"{'─'*70}")
        print(f"  ИТОГО: {total_chars:,} символов, {format_duration(total_time)}, ${total_cost:.2f}")
        if budget is not None:
            if total_cost > budget:
                print(f"  ⚠ БЮДЖЕТ ${budget:.2f} - НЕ ХВАТАЕТ ${total_cost - budget:.2f}")
            else:
                print(f"  ✓ Бюджет ${budget:.2f} - остаток ${budget - total_cost:.2f}")
        print()


# ---------------------------------------------------------------------------
# Интерактивное меню
# ---------------------------------------------------------------------------

def interactive_menu(files_all: list[Path], files_new: list[Path]) -> dict:
    """Интерактивное меню при запуске без аргументов. Возвращает dict с настройками."""

    if HAS_RICH:
        console.print()
        console.print(Panel(
            "[bold cyan]md-translate-ru[/] - переводчик Markdown EN→RU через Claude API\n"
            f"[dim]Найдено файлов: {len(files_all)} всего, {len(files_new)} непереведённых[/]",
            title="🌐 Переводчик",
            border_style="cyan",
        ))
        console.print()

        # Выбор действия
        console.print("[bold]Что делаем?[/]\n")
        actions = [
            ("1", "📝 Перевести непереведённые", "new"),
            ("2", "📁 Перевести конкретный файл", "pick"),
            ("3", "🔄 Перевести всё заново (force)", "force"),
            ("4", "💰 Только оценить стоимость (dry-run)", "dry"),
            ("5", "📦 Batch API (дешевле, до 24ч)", "batch"),
            ("6", "🔍 Проверить статус Batch", "status"),
            ("0", "❌ Выход", "exit"),
        ]

        for key, label, _ in actions:
            console.print(f"  [bold cyan]{key}[/]  {label}")

        console.print()
        choice = Prompt.ask(
            "Выбор",
            choices=[a[0] for a in actions],
            default="1",
        )
        action = next(a[2] for a in actions if a[0] == choice)

        if action == "exit":
            console.print("[dim]До встречи![/]")
            sys.exit(0)

        result = {"action": action, "file": None, "budget": None, "batch_id": None}

        # Выбор файла
        if action == "pick":
            console.print()
            for i, f in enumerate(files_all, 1):
                translated = (DOCS_RU / f.name).exists()
                status = "[green]✓[/]" if translated else "[red]✗[/]"
                console.print(f"  {status} [bold]{i:2d}[/]  {f.name}  [dim]{f.stat().st_size:,} байт[/]")
            console.print()
            idx = IntPrompt.ask("Номер файла", default=1)
            if 1 <= idx <= len(files_all):
                result["file"] = files_all[idx - 1].name
            else:
                log("Неверный номер", "ERROR")
                sys.exit(1)

        # Batch status
        if action == "status":
            result["batch_id"] = Prompt.ask("Batch ID")
            return result

        # Бюджет
        if action in ("new", "force", "pick"):
            console.print()
            if Confirm.ask("Задать лимит бюджета?", default=False):
                budget_str = Prompt.ask("Максимальный бюджет (USD)", default="10.00")
                try:
                    result["budget"] = float(budget_str)
                except ValueError:
                    log("Неверный формат бюджета", "WARN")

        return result

    else:
        # Fallback без Rich
        print(f"\n{'='*50}")
        print("md-translate-ru - переводчик Markdown EN→RU")
        print(f"Файлов: {len(files_all)} всего, {len(files_new)} непереведённых")
        print(f"{'='*50}")
        print("  1. Перевести непереведённые")
        print("  2. Перевести конкретный файл")
        print("  3. Перевести всё заново")
        print("  4. Оценить стоимость")
        print("  5. Batch API")
        print("  6. Проверить статус Batch")
        print("  0. Выход")
        choice = input("\nВыбор [1]: ").strip() or "1"
        action_map = {"1": "new", "2": "pick", "3": "force", "4": "dry", "5": "batch", "6": "status", "0": "exit"}
        action = action_map.get(choice, "new")
        if action == "exit":
            sys.exit(0)

        result = {"action": action, "file": None, "budget": None, "batch_id": None}

        if action == "pick":
            for i, f in enumerate(files_all, 1):
                print(f"  {i:2d}. {f.name}")
            idx = int(input("Номер: ") or "1")
            if 1 <= idx <= len(files_all):
                result["file"] = files_all[idx - 1].name

        if action == "status":
            result["batch_id"] = input("Batch ID: ").strip()

        if action in ("new", "force", "pick"):
            b = input("Бюджет USD (Enter = без лимита): ").strip()
            if b:
                try:
                    result["budget"] = float(b)
                except ValueError:
                    pass

        return result


# ---------------------------------------------------------------------------
# Подтверждение запуска
# ---------------------------------------------------------------------------

def confirm_start(forecasts: list[dict], budget: float | None, is_batch: bool = False) -> bool:
    """Показать прогноз и запросить подтверждение."""
    show_forecast_table(forecasts, budget)

    total_cost = sum(fc["est_cost"] for fc in forecasts)
    mode = "Batch API (до 24ч, -50%)" if is_batch else "синхронный"

    if HAS_RICH:
        console.print(f"  Режим: [bold]{mode}[/]")
        if is_batch:
            console.print(f"  Стоимость Batch: [bold green]≈${total_cost / 2:.2f}[/]")
        console.print(f"  [dim]Ctrl+C - остановить после текущего файла[/]\n")
        return Confirm.ask("Запустить перевод?", default=True)
    else:
        print(f"  Режим: {mode}")
        answer = input("Запустить? [Y/n]: ").strip().lower()
        return answer in ("", "y", "yes", "д", "да")


# ---------------------------------------------------------------------------
# API-вызовы
# ---------------------------------------------------------------------------

def translate_text(client, model: str, system_prompt: str,
                   source_text: str, filename: str,
                   is_chunk: bool = False, chunk_num: int = 0,
                   total_chunks: int = 0) -> tuple[str, int, int]:
    user_prompt = build_user_prompt(source_text, filename, is_chunk, chunk_num, total_chunks)

    response = client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    translated = response.content[0].text
    return translated, response.usage.input_tokens, response.usage.output_tokens


def translate_file(client, model: str, system_prompt: str,
                   source_path: Path, target_path: Path,
                   dry_run: bool = False) -> dict:
    filename = source_path.name
    source_text = source_path.read_text(encoding="utf-8")
    chunks = split_into_chunks(source_text)

    stats = {
        "file": filename,
        "source_chars": len(source_text),
        "chunks": len(chunks),
        "input_tokens": 0,
        "output_tokens": 0,
        "cost": 0.0,
        "status": "ok",
    }

    if dry_run:
        est_input = estimate_tokens(source_text) + 8000
        est_output = int(estimate_tokens(source_text) * 1.15)
        stats["input_tokens"] = est_input
        stats["output_tokens"] = est_output
        stats["cost"] = calc_cost(est_input, est_output)
        stats["status"] = "dry-run"
        return stats

    translated_parts = []

    for i, chunk in enumerate(chunks, 1):
        # Проверка прерывания
        if _interrupted:
            log("Прервано пользователем (Ctrl+C)", "WARN")
            stats["status"] = "interrupted"
            return stats

        is_chunked = len(chunks) > 1
        if is_chunked:
            log(f"  Чанк {i}/{len(chunks)} ({len(chunk):,} символов)...")

        try:
            translation, inp_tok, out_tok = translate_text(
                client, model, system_prompt,
                chunk, filename,
                is_chunk=is_chunked, chunk_num=i, total_chunks=len(chunks)
            )
            translated_parts.append(translation)
            stats["input_tokens"] += inp_tok
            stats["output_tokens"] += out_tok

        except Exception as e:
            log(f"  ОШИБКА при переводе чанка {i}: {e}", "ERROR")
            stats["status"] = f"error: {e}"
            return stats

        if is_chunked and i < len(chunks):
            time.sleep(1)

    full_translation = "\n\n".join(translated_parts)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(full_translation, encoding="utf-8")

    stats["cost"] = calc_cost(stats["input_tokens"], stats["output_tokens"])
    cost_str = f"${stats['cost']:.2f}"
    log(f"  ✓ {stats['input_tokens']:,} in + {stats['output_tokens']:,} out = {cost_str}", "OK")

    return stats


# ---------------------------------------------------------------------------
# Batch API
# ---------------------------------------------------------------------------

def create_batch_requests(system_prompt: str, files: list[Path]) -> list[dict]:
    requests = []
    for source_path in files:
        filename = source_path.name
        source_text = source_path.read_text(encoding="utf-8")
        chunks = split_into_chunks(source_text)

        for i, chunk in enumerate(chunks, 1):
            is_chunked = len(chunks) > 1
            user_prompt = build_user_prompt(
                chunk, filename,
                is_chunk=is_chunked, chunk_num=i, total_chunks=len(chunks)
            )
            custom_id = f"{filename}__chunk_{i}_of_{len(chunks)}"
            requests.append({
                "custom_id": custom_id,
                "params": {
                    "model": os.getenv("TRANSLATE_MODEL", DEFAULT_MODEL),
                    "max_tokens": MAX_OUTPUT_TOKENS,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                }
            })
    return requests


def submit_batch(client, requests: list[dict]) -> str:
    jsonl_path = ROOT / ".batch_requests.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for req in requests:
            f.write(json.dumps(req, ensure_ascii=False) + "\n")

    batch = client.messages.batches.create(requests=requests)
    log(f"Batch создан: id={batch.id}, статус={batch.processing_status}", "OK")
    log(f"Проверить статус: python translate_api.py --batch-status {batch.id}")
    return batch.id


def check_batch_status(client, batch_id: str):
    batch = client.messages.batches.retrieve(batch_id)

    if HAS_RICH:
        console.print(Panel(
            f"[bold]ID:[/] {batch_id}\n"
            f"[bold]Статус:[/] {batch.processing_status}\n"
            f"[bold]Создан:[/] {batch.created_at}",
            title="📦 Batch Status",
            border_style="cyan",
        ))
    else:
        log(f"Batch {batch_id}: {batch.processing_status}")

    if hasattr(batch, 'request_counts'):
        rc = batch.request_counts
        log(f"  Обработано: {rc.succeeded} успешно, {rc.errored} ошибок, {rc.processing} в процессе")

    if batch.processing_status == "ended":
        log("Загрузка результатов...")
        assemble_batch_results(client, batch_id)


def assemble_batch_results(client, batch_id: str):
    results = {}
    for result in client.messages.batches.results(batch_id):
        custom_id = result.custom_id
        if result.result.type == "succeeded":
            results[custom_id] = result.result.message.content[0].text
        else:
            log(f"  Ошибка: {custom_id}: {result.result.type}", "ERROR")

    file_chunks = {}
    for custom_id, text in results.items():
        match = re.match(r'^(.+?)__chunk_(\d+)_of_(\d+)$', custom_id)
        if match:
            filename = match.group(1)
            chunk_num = int(match.group(2))
            total = int(match.group(3))
            if filename not in file_chunks:
                file_chunks[filename] = {"total": total, "chunks": {}}
            file_chunks[filename]["chunks"][chunk_num] = text

    for filename, data in file_chunks.items():
        parts = []
        for i in range(1, data["total"] + 1):
            if i in data["chunks"]:
                parts.append(data["chunks"][i])
            else:
                log(f"  Отсутствует чанк {i} для {filename}", "WARN")
                parts.append(f"\n\n<!-- MISSING CHUNK {i} -->\n\n")
        full_text = "\n\n".join(parts)
        target = DOCS_RU / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(full_text, encoding="utf-8")
        log(f"  Сохранён: {target}", "OK")

    log(f"Готово! Файлов: {len(file_chunks)}", "OK")


# ---------------------------------------------------------------------------
# Итоговый отчёт
# ---------------------------------------------------------------------------

def show_summary(total_stats: dict, elapsed: float, budget: float | None):
    cost = calc_cost(total_stats["input_tokens"], total_stats["output_tokens"])

    if HAS_RICH:
        summary = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        summary.add_column("Key", style="bold")
        summary.add_column("Value")

        summary.add_row("Файлов переведено", str(total_stats["files"]))
        summary.add_row("Ошибок", f"[red]{total_stats['errors']}[/]" if total_stats["errors"] else "[green]0[/]")
        summary.add_row("Input токенов", f"{total_stats['input_tokens']:,}")
        summary.add_row("Output токенов", f"{total_stats['output_tokens']:,}")
        summary.add_row("Стоимость (Sonnet)", f"[bold green]${cost:.2f}[/]")
        summary.add_row("Стоимость (Batch)", f"[bold green]${cost/2:.2f}[/]")
        summary.add_row("Время", format_duration(elapsed))

        if budget is not None:
            remaining = budget - cost
            if remaining >= 0:
                summary.add_row("Остаток бюджета", f"[green]${remaining:.2f}[/]")
            else:
                summary.add_row("Перерасход", f"[red]${-remaining:.2f}[/]")

        if total_stats.get("interrupted"):
            summary.add_row("", "[yellow]⏸ Перевод остановлен пользователем[/]")

        console.print()
        console.print(Panel(summary, title="📊 Итого", border_style="green"))
        console.print()
    else:
        print(f"\n{'='*60}")
        print("ИТОГО")
        print(f"{'='*60}")
        print(f"  Файлов: {total_stats['files']}")
        print(f"  Ошибок: {total_stats['errors']}")
        print(f"  Input:  {total_stats['input_tokens']:,}")
        print(f"  Output: {total_stats['output_tokens']:,}")
        print(f"  Стоимость: ${cost:.2f}")
        print(f"  Время: {format_duration(elapsed)}")
        if budget is not None:
            print(f"  Бюджет: ${budget:.2f}, остаток: ${budget - cost:.2f}")


# ---------------------------------------------------------------------------
# Определение файлов
# ---------------------------------------------------------------------------

def get_all_files() -> list[Path]:
    if not DOCS_EN.exists():
        return []
    return sorted(DOCS_EN.glob("*.md"))


def get_files_to_translate(specific_file: str = None, force: bool = False) -> list[Path]:
    if specific_file:
        source = DOCS_EN / specific_file
        if not source.exists():
            log(f"Файл не найден: {source}", "ERROR")
            sys.exit(1)
        return [source]

    files = get_all_files()
    if not force:
        files = [f for f in files if not (DOCS_RU / f.name).exists()]
    return files


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Перевод EN->RU через Anthropic API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python translate_api.py                          # интерактивное меню
  python translate_api.py --file 32_Glossary.md    # конкретный файл
  python translate_api.py --force                  # перевести всё заново
  python translate_api.py --dry-run                # оценить стоимость
  python translate_api.py --budget 5.00            # лимит бюджета
  python translate_api.py --batch                  # Batch API (дешевле)
  python translate_api.py --batch-status ID        # проверить пакет
        """
    )
    parser.add_argument("--file", help="Перевести конкретный файл из docs_en/")
    parser.add_argument("--force", action="store_true", help="Перезаписать существующие переводы")
    parser.add_argument("--dry-run", action="store_true", help="Только оценить стоимость")
    parser.add_argument("--batch", action="store_true", help="Использовать Batch API (дешевле, до 24ч)")
    parser.add_argument("--batch-status", metavar="BATCH_ID", help="Проверить статус пакета")
    parser.add_argument("--budget", type=float, default=None, help="Максимальный бюджет в USD")
    parser.add_argument("--model", default=None, help=f"Модель (по умолчанию: {DEFAULT_MODEL})")
    parser.add_argument("--no-interactive", action="store_true", help="Отключить интерактивный режим")
    args = parser.parse_args()

    model = args.model or os.getenv("TRANSLATE_MODEL", DEFAULT_MODEL)

    # ----- Интерактивный режим -----
    is_interactive = (
        not args.no_interactive
        and not args.file
        and not args.force
        and not args.dry_run
        and not args.batch
        and not args.batch_status
        and sys.stdin.isatty()
    )

    files_all = get_all_files()
    files_new = [f for f in files_all if not (DOCS_RU / f.name).exists()]

    if is_interactive and files_all:
        menu = interactive_menu(files_all, files_new)

        if menu["action"] == "status":
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                log("ОШИБКА: установите ANTHROPIC_API_KEY", "ERROR")
                sys.exit(1)
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            check_batch_status(client, menu["batch_id"])
            return

        if menu["action"] == "dry":
            args.dry_run = True
        elif menu["action"] == "force":
            args.force = True
        elif menu["action"] == "batch":
            args.batch = True
        elif menu["action"] == "pick":
            args.file = menu["file"]

        if menu.get("budget") is not None:
            args.budget = menu["budget"]

    # ----- Инициализация клиента -----
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key and not args.dry_run:
        log("ОШИБКА: установите ANTHROPIC_API_KEY", "ERROR")
        log("  export ANTHROPIC_API_KEY='sk-ant-...'")
        sys.exit(1)

    client = None
    if not args.dry_run:
        try:
            import anthropic
        except ImportError:
            log("ОШИБКА: установите SDK: pip install anthropic", "ERROR")
            sys.exit(1)
        client = anthropic.Anthropic(api_key=api_key)

    # ----- Проверка статуса пакета -----
    if args.batch_status:
        check_batch_status(client, args.batch_status)
        return

    # ----- Загрузка конфигурации -----
    log("Загрузка конфигурации...")
    glossary = load_json(GLOSSARY_PATH)
    log(f"  Глоссарий: {len(glossary)} терминов")
    candidates = load_json(GLOSSARY_CANDIDATES_PATH)
    log(f"  Кандидаты: {len(candidates)} записей")

    system_prompt = build_system_prompt(glossary)
    system_prompt_tokens = estimate_tokens(system_prompt)
    log(f"  Системный промпт: ~{system_prompt_tokens:,} токенов")

    # ----- Определение файлов -----
    files = get_files_to_translate(args.file, args.force)
    if not files:
        log("Все файлы уже переведены. Используйте --force для перезаписи.")
        return

    # ----- Прогноз и подтверждение -----
    forecasts = forecast_files(files, system_prompt_tokens)

    if not args.dry_run:
        if not confirm_start(forecasts, args.budget, is_batch=args.batch):
            ui_print("[dim]Отменено.[/]" if HAS_RICH else "Отменено.")
            return
    else:
        show_forecast_table(forecasts, args.budget)
        ui_print(
            "[dim]Это была оценка. Уберите --dry-run для запуска перевода.[/]"
            if HAS_RICH else
            "Это была оценка. Уберите --dry-run для запуска перевода."
        )
        return

    # ----- Batch-режим -----
    if args.batch:
        log("Подготовка Batch API запросов...")
        requests = create_batch_requests(system_prompt, files)
        log(f"Запросов: {len(requests)}")
        batch_id = submit_batch(client, requests)
        if HAS_RICH:
            console.print(Panel(
                f"[bold]Batch ID:[/] {batch_id}\n\n"
                f"Проверить позже:\n"
                f"  [cyan]python translate_api.py --batch-status {batch_id}[/]",
                title="📦 Batch создан",
                border_style="green",
            ))
        return

    # ----- Последовательный перевод с контролем бюджета -----
    log(f"Модель: {model}")

    total_stats = {
        "files": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "errors": 0,
        "interrupted": False,
    }
    spent = 0.0
    start_time = time.time()

    if HAS_RICH:
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=30),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        )
    else:
        progress = None

    def run_translation():
        nonlocal spent

        for i, source_path in enumerate(files, 1):
            if _interrupted:
                total_stats["interrupted"] = True
                log("⏸  Остановлено пользователем", "WARN")
                break

            # Проверка бюджета ПЕРЕД началом файла
            if args.budget is not None:
                fc = forecasts[i - 1]
                if spent + fc["est_cost"] > args.budget:
                    log(
                        f"⛔ Бюджет исчерпан: потрачено ${spent:.2f}, "
                        f"следующий файл ≈${fc['est_cost']:.2f}, "
                        f"лимит ${args.budget:.2f}",
                        "WARN"
                    )
                    total_stats["interrupted"] = True
                    break

            # Заголовок файла
            fc = forecasts[i - 1]
            if HAS_RICH:
                console.print(Rule(f"[bold]{source_path.name}[/]", style="cyan"))
                console.print(f"  [dim]{fc['description']}[/]")
                console.print(
                    f"  [dim]{fc['chars']:,} символов · {fc['chunks']} чанк(ов) · "
                    f"≈{format_duration(fc['est_time'])} · ≈${fc['est_cost']:.2f}[/]"
                )
            else:
                print(f"\n--- [{i}/{len(files)}] {source_path.name} ---")
                print(f"    {fc['description']}")
                print(f"    {fc['chars']:,} символов, ≈${fc['est_cost']:.2f}")

            target_path = DOCS_RU / source_path.name

            stats = translate_file(
                client, model, system_prompt,
                source_path, target_path,
                dry_run=False,
            )

            total_stats["files"] += 1
            total_stats["input_tokens"] += stats["input_tokens"]
            total_stats["output_tokens"] += stats["output_tokens"]
            spent += stats.get("cost", 0.0)

            if "error" in stats["status"]:
                total_stats["errors"] += 1
            if stats["status"] == "interrupted":
                total_stats["interrupted"] = True
                break

            # Обновить прогресс
            if progress:
                task = progress.tasks[0] if progress.tasks else None
                if task:
                    progress.update(task.id, advance=1)

            if i < len(files):
                time.sleep(2)

    if HAS_RICH and len(files) > 1:
        with progress:
            task_id = progress.add_task("Перевод...", total=len(files))
            run_translation()
    else:
        run_translation()

    elapsed = time.time() - start_time
    show_summary(total_stats, elapsed, args.budget)


if __name__ == "__main__":
    main()
