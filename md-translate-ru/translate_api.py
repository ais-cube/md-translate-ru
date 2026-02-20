#!/usr/bin/env python3
"""
translate_api.py - Автоматический переводчик EN->RU через Anthropic API.

Реплицирует логику агента TRANSLATOR из пайплайна AgenticDesignPatternsRU,
но работает через API без лимитов подписки Claude Pro.

Требования:
    pip install anthropic

Использование:
    # Перевести все непереведённые файлы
    python translate_api.py

    # Перевести конкретный файл
    python translate_api.py --file 32_Glossary.md

    # Перевести все файлы заново (перезапись)
    python translate_api.py --force

    # Только оценить стоимость, без перевода
    python translate_api.py --dry-run

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
import sys
import time
from pathlib import Path
from datetime import datetime

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
MAX_OUTPUT_TOKENS = 16384  # максимум токенов на ответ
CHUNK_SIZE_CHARS = 40000   # порог для разбивки файла на чанки (~10k токенов)

# Примерная стоимость (USD за 1M токенов) - Sonnet 4.5
COST_INPUT_PER_M = 3.0
COST_OUTPUT_PER_M = 15.0

# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


def load_json(path: Path) -> list:
    """Загрузить JSON-файл. Fail-fast при невалидном JSON."""
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
    """Сохранить JSON с красивым форматированием."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def estimate_tokens(text: str) -> int:
    """Грубая оценка количества токенов (1 токен ~ 4 символа для русского)."""
    return len(text) // 3


def format_cost(input_tokens: int, output_tokens: int) -> str:
    """Форматировать стоимость в долларах."""
    cost = (input_tokens * COST_INPUT_PER_M + output_tokens * COST_OUTPUT_PER_M) / 1_000_000
    return f"${cost:.2f}"


# ---------------------------------------------------------------------------
# Подготовка системного промпта
# ---------------------------------------------------------------------------

def build_system_prompt(glossary: list) -> str:
    """Собрать системный промпт из TRANSLATE.md, HUMANIZER.md и глоссария."""

    parts = []

    parts.append("Ты - профессиональный технический переводчик EN->RU.")
    parts.append("Следуй приведённым ниже правилам ТОЧНО и БЕЗ ОТКЛОНЕНИЙ.\n")

    # Спецификация перевода
    if TRANSLATE_SPEC.exists():
        spec = TRANSLATE_SPEC.read_text(encoding="utf-8")
        parts.append("=" * 60)
        parts.append("СПЕЦИФИКАЦИЯ ПЕРЕВОДА (TRANSLATE.md)")
        parts.append("=" * 60)
        parts.append(spec)

    # HUMANIZER - только anti-AI cleanup секции, без "личного голоса"
    if HUMANIZER_SPEC.exists():
        humanizer = HUMANIZER_SPEC.read_text(encoding="utf-8")
        parts.append("\n" + "=" * 60)
        parts.append("РЕДАКТОРСКИЕ ПРАВИЛА (HUMANIZER.md) - только anti-AI cleanup")
        parts.append("ВАЖНО: НЕ применять секцию PERSONALITY AND SOUL.")
        parts.append("НЕ добавлять личный голос, эмоции, юмор, первое лицо.")
        parts.append("Использовать ТОЛЬКО для устранения AI-штампов.")
        parts.append("=" * 60)
        parts.append(humanizer)

    # Глоссарий
    if glossary:
        parts.append("\n" + "=" * 60)
        parts.append("КАНОНИЧЕСКИЙ ГЛОССАРИЙ (glossary.json)")
        parts.append("Если term_en встречается в тексте - использовать ТОЛЬКО term_ru.")
        parts.append("=" * 60)
        glossary_text = "\n".join(
            f"- {e['term_en']} -> {e['term_ru']}"
            for e in glossary
        )
        parts.append(glossary_text)

    return "\n\n".join(parts)


def build_user_prompt(source_text: str, filename: str, is_chunk: bool = False,
                      chunk_num: int = 0, total_chunks: int = 0) -> str:
    """Собрать пользовательский промпт для перевода."""

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
    """Разбить документ на чанки по заголовкам Markdown.

    Стратегия: разбиваем по заголовкам уровня ## или #.
    Если отдельная секция превышает лимит - разбиваем по параграфам.
    """
    if len(text) <= max_chars:
        return [text]

    # Разбиваем по заголовкам ## (уровень 2)
    sections = re.split(r'(?=\n## )', text)

    chunks = []
    current_chunk = ""

    for section in sections:
        if len(current_chunk) + len(section) <= max_chars:
            current_chunk += section
        else:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            # Если секция сама превышает лимит - разбиваем по параграфам
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
# API-вызовы
# ---------------------------------------------------------------------------

def translate_text(client, model: str, system_prompt: str,
                   source_text: str, filename: str,
                   is_chunk: bool = False, chunk_num: int = 0,
                   total_chunks: int = 0) -> tuple[str, int, int]:
    """Перевести текст через Anthropic API. Возвращает (перевод, input_tokens, output_tokens)."""

    user_prompt = build_user_prompt(source_text, filename, is_chunk, chunk_num, total_chunks)

    response = client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    translated = response.content[0].text
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    return translated, input_tokens, output_tokens


def translate_file(client, model: str, system_prompt: str,
                   source_path: Path, target_path: Path,
                   dry_run: bool = False) -> dict:
    """Перевести один файл. Возвращает статистику."""

    filename = source_path.name
    source_text = source_path.read_text(encoding="utf-8")
    chunks = split_into_chunks(source_text)

    stats = {
        "file": filename,
        "source_chars": len(source_text),
        "chunks": len(chunks),
        "input_tokens": 0,
        "output_tokens": 0,
        "status": "ok",
    }

    if dry_run:
        est_input = estimate_tokens(source_text) + 8000  # +system prompt overhead
        est_output = int(estimate_tokens(source_text) * 1.15)  # русский ~15% длиннее
        stats["input_tokens"] = est_input
        stats["output_tokens"] = est_output
        stats["status"] = "dry-run"
        return stats

    translated_parts = []

    for i, chunk in enumerate(chunks, 1):
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

        # Пауза между чанками чтобы не перегружать API
        if is_chunked and i < len(chunks):
            time.sleep(1)

    # Собрать результат
    full_translation = "\n\n".join(translated_parts)

    # Записать файл
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(full_translation, encoding="utf-8")

    cost = format_cost(stats["input_tokens"], stats["output_tokens"])
    log(f"  Готово: {stats['input_tokens']:,} input + {stats['output_tokens']:,} output токенов = {cost}")

    return stats


# ---------------------------------------------------------------------------
# Batch API (асинхронный, в 2 раза дешевле)
# ---------------------------------------------------------------------------

def create_batch_requests(system_prompt: str, files: list[Path]) -> list[dict]:
    """Подготовить запросы для Batch API."""
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
    """Отправить пакет запросов в Batch API."""
    import tempfile

    # Записать JSONL-файл
    jsonl_path = ROOT / ".batch_requests.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for req in requests:
            f.write(json.dumps(req, ensure_ascii=False) + "\n")

    log(f"Создан файл запросов: {jsonl_path} ({len(requests)} запросов)")

    # Отправить через API
    batch = client.messages.batches.create(
        requests=requests
    )

    log(f"Batch создан: id={batch.id}, статус={batch.processing_status}")
    log(f"Ожидайте результатов (обычно до 24 часов).")
    log(f"Проверить статус: python translate_api.py --batch-status {batch.id}")

    return batch.id


def check_batch_status(client, batch_id: str):
    """Проверить статус и при готовности скачать результаты."""
    batch = client.messages.batches.retrieve(batch_id)

    log(f"Batch {batch_id}")
    log(f"  Статус: {batch.processing_status}")
    log(f"  Создан: {batch.created_at}")

    if hasattr(batch, 'request_counts'):
        rc = batch.request_counts
        log(f"  Обработано: {rc.succeeded} успешно, {rc.errored} ошибок, {rc.processing} в процессе")

    if batch.processing_status == "ended":
        log("Загрузка результатов...")
        assemble_batch_results(client, batch_id)


def assemble_batch_results(client, batch_id: str):
    """Собрать результаты Batch API в файлы docs_ru/."""
    results = {}

    for result in client.messages.batches.results(batch_id):
        custom_id = result.custom_id
        if result.result.type == "succeeded":
            text = result.result.message.content[0].text
            results[custom_id] = text
        else:
            log(f"  Ошибка в {custom_id}: {result.result.type}", "ERROR")

    # Группировать чанки по файлам
    file_chunks = {}
    for custom_id, text in results.items():
        # Формат: filename__chunk_N_of_M
        match = re.match(r'^(.+?)__chunk_(\d+)_of_(\d+)$', custom_id)
        if match:
            filename = match.group(1)
            chunk_num = int(match.group(2))
            total = int(match.group(3))
            if filename not in file_chunks:
                file_chunks[filename] = {"total": total, "chunks": {}}
            file_chunks[filename]["chunks"][chunk_num] = text

    # Собрать файлы
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
        target.write_text(full_text, encoding="utf-8")
        log(f"  Сохранён: {target}")

    log(f"Готово! Обработано файлов: {len(file_chunks)}")


# ---------------------------------------------------------------------------
# Главная логика
# ---------------------------------------------------------------------------

def get_files_to_translate(specific_file: str = None, force: bool = False) -> list[Path]:
    """Определить список файлов для перевода."""
    if specific_file:
        source = DOCS_EN / specific_file
        if not source.exists():
            log(f"Файл не найден: {source}", "ERROR")
            sys.exit(1)
        return [source]

    files = sorted(DOCS_EN.glob("*.md"))

    if not force:
        # Только файлы, для которых нет перевода
        files = [f for f in files if not (DOCS_RU / f.name).exists()]

    return files


def main():
    parser = argparse.ArgumentParser(
        description="Перевод EN->RU через Anthropic API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python translate_api.py                     # перевести непереведённые
  python translate_api.py --file 32_Glossary.md  # конкретный файл
  python translate_api.py --force             # перевести всё заново
  python translate_api.py --dry-run           # оценить стоимость
  python translate_api.py --batch             # Batch API (дешевле)
  python translate_api.py --batch-status ID   # проверить пакет
        """
    )
    parser.add_argument("--file", help="Перевести конкретный файл из docs_en/")
    parser.add_argument("--force", action="store_true", help="Перезаписать существующие переводы")
    parser.add_argument("--dry-run", action="store_true", help="Только оценить стоимость")
    parser.add_argument("--batch", action="store_true", help="Использовать Batch API (дешевле, до 24ч)")
    parser.add_argument("--batch-status", metavar="BATCH_ID", help="Проверить статус пакета")
    parser.add_argument("--model", default=None, help=f"Модель (по умолчанию: {DEFAULT_MODEL})")
    args = parser.parse_args()

    model = args.model or os.getenv("TRANSLATE_MODEL", DEFAULT_MODEL)

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
    log(f"  Системный промпт: ~{estimate_tokens(system_prompt):,} токенов")

    # ----- Определение файлов -----
    files = get_files_to_translate(args.file, args.force)
    if not files:
        log("Все файлы уже переведены. Используйте --force для перезаписи.")
        return

    log(f"Файлов для перевода: {len(files)}")
    for f in files:
        log(f"  - {f.name} ({f.stat().st_size:,} байт)")

    # ----- Batch-режим -----
    if args.batch and not args.dry_run:
        log("\nПодготовка Batch API запросов...")
        requests = create_batch_requests(system_prompt, files)
        log(f"Запросов: {len(requests)}")
        batch_id = submit_batch(client, requests)
        log(f"\nBatch ID: {batch_id}")
        log("Скопируйте ID и проверьте позже:")
        log(f"  python translate_api.py --batch-status {batch_id}")
        return

    # ----- Последовательный перевод -----
    log(f"\nМодель: {model}")
    if args.dry_run:
        log("РЕЖИМ: dry-run (оценка стоимости)\n")
    else:
        log("РЕЖИМ: перевод\n")

    total_stats = {
        "files": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "errors": 0,
    }

    start_time = time.time()

    for i, source_path in enumerate(files, 1):
        log(f"[{i}/{len(files)}] {source_path.name}")

        target_path = DOCS_RU / source_path.name

        stats = translate_file(
            client, model, system_prompt,
            source_path, target_path,
            dry_run=args.dry_run
        )

        total_stats["files"] += 1
        total_stats["input_tokens"] += stats["input_tokens"]
        total_stats["output_tokens"] += stats["output_tokens"]
        if "error" in stats["status"]:
            total_stats["errors"] += 1

        # Пауза между файлами
        if not args.dry_run and i < len(files):
            time.sleep(2)

    elapsed = time.time() - start_time

    # ----- Итоги -----
    log("\n" + "=" * 60)
    log("ИТОГО")
    log("=" * 60)
    log(f"  Файлов: {total_stats['files']}")
    log(f"  Ошибок: {total_stats['errors']}")
    log(f"  Input токенов:  {total_stats['input_tokens']:,}")
    log(f"  Output токенов: {total_stats['output_tokens']:,}")

    cost = format_cost(total_stats["input_tokens"], total_stats["output_tokens"])
    log(f"  Стоимость (Sonnet): {cost}")

    if args.batch:
        batch_cost = format_cost(
            total_stats["input_tokens"],
            total_stats["output_tokens"]
        ).replace("$", "")
        half = float(batch_cost) / 2
        log(f"  Стоимость (Batch API): ${half:.2f}")

    log(f"  Время: {elapsed:.0f} секунд")

    if args.dry_run:
        log("\nЭто была оценка. Для запуска перевода уберите --dry-run")


if __name__ == "__main__":
    main()
