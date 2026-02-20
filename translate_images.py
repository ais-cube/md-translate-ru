#!/usr/bin/env python3
"""
translate_images.py - Извлечение и перевод текста из изображений через Claude Vision API.

Для каждого изображения создаёт структурированное описание на русском:
- Тип изображения (схема, скриншот, график, таблица)
- Структурное описание (что изображено)
- Извлечённые текстовые элементы EN -> RU
- Контекст из alt-текста markdown-файла

Результаты:
- images_ru_text/ - Markdown-файлы с описаниями каждого изображения
- image_translations.json - единый JSON со всеми переводами
- image_summary.md - сводная таблица всех изображений

Требования:
    pip install anthropic

Использование:
    python translate_images.py                    # обработать все изображения
    python translate_images.py --file fig-006.png # конкретное изображение
    python translate_images.py --dry-run          # оценить стоимость
    python translate_images.py --batch            # Batch API (дешевле)
"""

import argparse
import base64
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
IMAGES_DIR = ROOT / "images"
IMAGES_RU_TEXT = ROOT / "images_ru_text"
DOCS_EN = ROOT / "docs_en"
GLOSSARY_PATH = ROOT / "glossary.json"
TRANSLATIONS_JSON = ROOT / "image_translations.json"
SUMMARY_MD = ROOT / "image_summary.md"

# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
MAX_OUTPUT_TOKENS = 8192

# Стоимость (USD за 1M токенов) - Sonnet 4.5
COST_INPUT_PER_M = 3.0
COST_OUTPUT_PER_M = 15.0

# Поддерживаемые форматы
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


def load_json(path: Path) -> list:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        log(f"Невалидный JSON: {path.name}: {e}", "WARN")
        return []


def format_cost(input_tokens: int, output_tokens: int) -> str:
    cost = (input_tokens * COST_INPUT_PER_M + output_tokens * COST_OUTPUT_PER_M) / 1_000_000
    return f"${cost:.2f}"


def image_to_base64(path: Path) -> tuple[str, str]:
    """Конвертировать изображение в base64. Возвращает (base64_data, media_type)."""
    suffix = path.suffix.lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_types.get(suffix, "image/png")

    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")

    return data, media_type


def collect_alt_texts() -> dict[str, str]:
    """Собрать alt-тексты из всех markdown-файлов docs_en/.

    Возвращает {filename: alt_text}, например:
    {"fig-006.png": "Prompt Chaining Pattern: Agents receive..."}
    """
    alt_texts = {}

    if not DOCS_EN.exists():
        return alt_texts

    for md_file in sorted(DOCS_EN.glob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        # Паттерн: ![alt text](../images/fig-NNN.ext)
        for match in re.finditer(r'!\[([^\]]*)\]\([^)]*images/([^)]+)\)', content):
            alt_text = match.group(1).strip()
            img_filename = match.group(2).strip()
            if alt_text and img_filename:
                alt_texts[img_filename] = alt_text

    return alt_texts


# ---------------------------------------------------------------------------
# Системный промпт
# ---------------------------------------------------------------------------

def build_system_prompt(glossary: list) -> str:
    parts = []

    parts.append("""Ты - эксперт по анализу технических изображений и переводу EN->RU.

Твоя задача: извлечь ВСЁ текстовое содержимое из изображения и создать
структурированное описание на русском языке.

ФОРМАТ ОТВЕТА (строго Markdown):

## Тип изображения
Одно из: схема / скриншот / график / таблица / диаграмма / фото / иллюстрация

## Краткое описание
1-3 предложения: что изображено, какой процесс/концепцию иллюстрирует.

## Структура изображения
Описание визуальной структуры: какие блоки, стрелки, связи, слои присутствуют.
Для схем - описать поток данных/процесса.
Для скриншотов - описать интерфейс и основные области.
Для графиков - описать оси, тренды, ключевые значения.

## Текстовые элементы

| Оригинал (EN) | Перевод (RU) | Расположение |
|---|---|---|
| User | Пользователь | левый блок |
| Output | Выход | верхний блок |
| ... | ... | ... |

Включить ВСЕ текстовые надписи, заголовки, подписи, метки, кнопки, пункты меню.
Если текст мелкий или частично обрезан - указать [нечитаемо] или [частично: ...].

## Перевод для alt-текста
Одно предложение на русском для использования как alt-текст в Markdown.

ПРАВИЛА:
- Технический перевод, нейтральный тон.
- Не переводить: URL, email, имена файлов, code identifiers, тикеры.
- Числовой формат: пробел-разделитель (1 000), десятичная запятая (15,73).
- Используй канонические термины из глоссария (если предоставлен).""")

    # Глоссарий
    if glossary:
        glossary_text = "\n".join(
            f"- {e['term_en']} -> {e['term_ru']}"
            for e in glossary
        )
        parts.append(f"\n\nКАНОНИЧЕСКИЙ ГЛОССАРИЙ:\n{glossary_text}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def analyze_image(client, model: str, system_prompt: str,
                  image_path: Path, alt_text: str = "") -> tuple[str, int, int]:
    """Отправить изображение в Claude Vision API. Возвращает (markdown, inp_tok, out_tok)."""

    b64_data, media_type = image_to_base64(image_path)

    user_content = []

    # Изображение
    user_content.append({
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": b64_data,
        }
    })

    # Контекст
    context = f"Файл: {image_path.name}"
    if alt_text:
        context += f"\nAlt-текст из документа: {alt_text}"
    context += "\n\nИзвлеки и переведи всё текстовое содержимое из этого изображения."

    user_content.append({"type": "text", "text": context})

    response = client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )

    result_text = response.content[0].text
    return result_text, response.usage.input_tokens, response.usage.output_tokens


# ---------------------------------------------------------------------------
# Обработка
# ---------------------------------------------------------------------------

def get_images_to_process(specific_file: str = None, force: bool = False) -> list[Path]:
    """Собрать список изображений для обработки."""
    if not IMAGES_DIR.exists():
        log(f"Папка {IMAGES_DIR} не найдена", "ERROR")
        sys.exit(1)

    if specific_file:
        img = IMAGES_DIR / specific_file
        if not img.exists():
            log(f"Файл не найден: {img}", "ERROR")
            sys.exit(1)
        return [img]

    all_images = sorted(
        f for f in IMAGES_DIR.iterdir()
        if f.suffix.lower() in IMAGE_EXTENSIONS
    )

    if not force:
        # Пропустить уже обработанные
        processed = set()
        if TRANSLATIONS_JSON.exists():
            existing = load_json(TRANSLATIONS_JSON)
            processed = {e["filename"] for e in existing if isinstance(e, dict)}
        all_images = [f for f in all_images if f.name not in processed]

    return all_images


def estimate_image_tokens(image_path: Path) -> int:
    """Грубая оценка токенов для изображения (по размеру файла)."""
    size_bytes = image_path.stat().st_size
    # Anthropic: ~1 токен на 750 байт для изображений (грубо)
    return max(size_bytes // 750, 200)


def process_image(client, model: str, system_prompt: str,
                  image_path: Path, alt_text: str,
                  dry_run: bool = False) -> dict:
    """Обработать одно изображение."""

    stats = {
        "filename": image_path.name,
        "size_bytes": image_path.stat().st_size,
        "alt_text_en": alt_text,
        "input_tokens": 0,
        "output_tokens": 0,
        "status": "ok",
        "translation_md": "",
    }

    if dry_run:
        stats["input_tokens"] = estimate_image_tokens(image_path) + 2000  # +system prompt
        stats["output_tokens"] = 800  # typical structured response
        stats["status"] = "dry-run"
        return stats

    try:
        result_md, inp_tok, out_tok = analyze_image(
            client, model, system_prompt, image_path, alt_text
        )
        stats["input_tokens"] = inp_tok
        stats["output_tokens"] = out_tok
        stats["translation_md"] = result_md

        cost = format_cost(inp_tok, out_tok)
        log(f"  {inp_tok:,} input + {out_tok:,} output = {cost}")

    except Exception as e:
        log(f"  ОШИБКА: {e}", "ERROR")
        stats["status"] = f"error: {e}"

    return stats


def save_individual_md(filename: str, translation_md: str, alt_text: str):
    """Сохранить перевод изображения в отдельный .md файл."""
    IMAGES_RU_TEXT.mkdir(parents=True, exist_ok=True)

    stem = Path(filename).stem
    md_path = IMAGES_RU_TEXT / f"{stem}.md"

    header = f"# {filename}\n\n"
    if alt_text:
        header += f"> **Оригинальный alt-текст:** {alt_text}\n\n"
    header += "---\n\n"

    md_path.write_text(header + translation_md, encoding="utf-8")


def save_translations_json(all_results: list):
    """Сохранить все переводы в единый JSON."""
    output = []
    for r in all_results:
        output.append({
            "filename": r["filename"],
            "size_bytes": r["size_bytes"],
            "alt_text_en": r.get("alt_text_en", ""),
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "status": r["status"],
            "translation_md": r.get("translation_md", ""),
        })

    # Merge с существующими
    existing = []
    if TRANSLATIONS_JSON.exists():
        existing = load_json(TRANSLATIONS_JSON)

    existing_map = {e["filename"]: e for e in existing if isinstance(e, dict)}
    for item in output:
        existing_map[item["filename"]] = item

    merged = sorted(existing_map.values(), key=lambda x: x["filename"])

    with open(TRANSLATIONS_JSON, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)


def generate_summary(all_results: list):
    """Сгенерировать сводную таблицу."""
    lines = [
        "# Сводка переводов изображений\n",
        f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
        "| Файл | Размер | Статус | Стоимость |",
        "|---|---|---|---|",
    ]

    total_input = 0
    total_output = 0

    for r in all_results:
        size_kb = r["size_bytes"] / 1024
        cost = format_cost(r["input_tokens"], r["output_tokens"])
        status = "ok" if r["status"] == "ok" else r["status"]
        lines.append(f"| {r['filename']} | {size_kb:.0f} KB | {status} | {cost} |")
        total_input += r["input_tokens"]
        total_output += r["output_tokens"]

    total_cost = format_cost(total_input, total_output)
    lines.append(f"| **ИТОГО** | **{len(all_results)} файлов** | | **{total_cost}** |")
    lines.append("")
    lines.append(f"Токены: {total_input:,} input + {total_output:,} output")

    SUMMARY_MD.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Batch API
# ---------------------------------------------------------------------------

def create_batch_requests(system_prompt: str, images: list[Path],
                          alt_texts: dict) -> list[dict]:
    """Подготовить запросы для Batch API."""
    requests = []

    for image_path in images:
        b64_data, media_type = image_to_base64(image_path)
        alt_text = alt_texts.get(image_path.name, "")

        context = f"Файл: {image_path.name}"
        if alt_text:
            context += f"\nAlt-текст из документа: {alt_text}"
        context += "\n\nИзвлеки и переведи всё текстовое содержимое из этого изображения."

        user_content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64_data,
                }
            },
            {"type": "text", "text": context}
        ]

        requests.append({
            "custom_id": image_path.name,
            "params": {
                "model": os.getenv("TRANSLATE_MODEL", DEFAULT_MODEL),
                "max_tokens": MAX_OUTPUT_TOKENS,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_content}],
            }
        })

    return requests


def submit_batch(client, requests: list[dict]) -> str:
    batch = client.messages.batches.create(requests=requests)
    log(f"Batch создан: id={batch.id}")
    log(f"Проверить: python translate_images.py --batch-status {batch.id}")
    return batch.id


def check_batch_status(client, batch_id: str, alt_texts: dict):
    batch = client.messages.batches.retrieve(batch_id)
    log(f"Batch {batch_id}: {batch.processing_status}")

    if hasattr(batch, 'request_counts'):
        rc = batch.request_counts
        log(f"  Успешно: {rc.succeeded}, ошибок: {rc.errored}, в процессе: {rc.processing}")

    if batch.processing_status == "ended":
        log("Загрузка результатов...")
        results = []

        for result in client.messages.batches.results(batch_id):
            filename = result.custom_id
            if result.result.type == "succeeded":
                text = result.result.message.content[0].text
                inp = result.result.message.usage.input_tokens
                out = result.result.message.usage.output_tokens

                stats = {
                    "filename": filename,
                    "size_bytes": (IMAGES_DIR / filename).stat().st_size if (IMAGES_DIR / filename).exists() else 0,
                    "alt_text_en": alt_texts.get(filename, ""),
                    "input_tokens": inp,
                    "output_tokens": out,
                    "status": "ok",
                    "translation_md": text,
                }
                results.append(stats)
                save_individual_md(filename, text, alt_texts.get(filename, ""))
                log(f"  {filename}: ok")
            else:
                log(f"  {filename}: ошибка - {result.result.type}", "ERROR")

        if results:
            save_translations_json(results)
            generate_summary(results)
            log(f"Сохранено: {len(results)} файлов в images_ru_text/")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Извлечение и перевод текста из изображений через Claude Vision API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python translate_images.py                      # все изображения
  python translate_images.py --file fig-006.png   # одно изображение
  python translate_images.py --dry-run             # оценка стоимости
  python translate_images.py --batch               # Batch API (дешевле)
  python translate_images.py --batch-status ID     # проверить пакет
        """
    )
    parser.add_argument("--file", help="Обработать конкретное изображение")
    parser.add_argument("--force", action="store_true", help="Обработать заново все")
    parser.add_argument("--dry-run", action="store_true", help="Оценить стоимость")
    parser.add_argument("--batch", action="store_true", help="Batch API")
    parser.add_argument("--batch-status", metavar="ID", help="Статус пакета")
    parser.add_argument("--model", default=None, help=f"Модель (по умолчанию: {DEFAULT_MODEL})")
    args = parser.parse_args()

    model = args.model or os.getenv("TRANSLATE_MODEL", DEFAULT_MODEL)

    # Клиент
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key and not args.dry_run:
        log("ОШИБКА: установите ANTHROPIC_API_KEY", "ERROR")
        sys.exit(1)

    client = None
    if not args.dry_run:
        try:
            import anthropic
        except ImportError:
            log("ОШИБКА: pip install anthropic", "ERROR")
            sys.exit(1)
        client = anthropic.Anthropic(api_key=api_key)

    # Alt-тексты из markdown
    alt_texts = collect_alt_texts()
    log(f"Alt-тексты: {len(alt_texts)} найдено в docs_en/")

    # Глоссарий
    glossary = load_json(GLOSSARY_PATH)
    log(f"Глоссарий: {len(glossary)} терминов")

    system_prompt = build_system_prompt(glossary)

    # Batch status
    if args.batch_status:
        check_batch_status(client, args.batch_status, alt_texts)
        return

    # Файлы
    images = get_images_to_process(args.file, args.force)
    if not images:
        log("Все изображения уже обработаны. Используйте --force")
        return

    log(f"Изображений: {len(images)}")
    total_size = sum(f.stat().st_size for f in images)
    log(f"Общий размер: {total_size / 1024 / 1024:.1f} MB")

    # Batch
    if args.batch and not args.dry_run:
        requests = create_batch_requests(system_prompt, images, alt_texts)
        batch_id = submit_batch(client, requests)
        return

    # Последовательная обработка
    log(f"Модель: {model}")
    if args.dry_run:
        log("РЕЖИМ: dry-run\n")
    else:
        log("РЕЖИМ: обработка\n")

    all_results = []
    total_inp = 0
    total_out = 0
    start_time = time.time()

    for i, image_path in enumerate(images, 1):
        alt_text = alt_texts.get(image_path.name, "")
        size_kb = image_path.stat().st_size / 1024
        log(f"[{i}/{len(images)}] {image_path.name} ({size_kb:.0f} KB)")

        stats = process_image(client, model, system_prompt, image_path, alt_text, args.dry_run)
        all_results.append(stats)
        total_inp += stats["input_tokens"]
        total_out += stats["output_tokens"]

        # Сохранить индивидуальный файл
        if stats["translation_md"] and stats["status"] == "ok":
            save_individual_md(stats["filename"], stats["translation_md"], alt_text)

        # Пауза
        if not args.dry_run and i < len(images):
            time.sleep(1)

    elapsed = time.time() - start_time

    # Сохранить результаты
    if not args.dry_run:
        save_translations_json(all_results)
        generate_summary(all_results)

    # Итоги
    log("\n" + "=" * 60)
    log("ИТОГО")
    log("=" * 60)
    log(f"  Изображений: {len(all_results)}")
    log(f"  Ошибок: {sum(1 for r in all_results if 'error' in r['status'])}")
    log(f"  Input токенов:  {total_inp:,}")
    log(f"  Output токенов: {total_out:,}")
    log(f"  Стоимость (Sonnet): {format_cost(total_inp, total_out)}")
    log(f"  Время: {elapsed:.0f} секунд")

    if not args.dry_run:
        ok_count = sum(1 for r in all_results if r["status"] == "ok")
        log(f"\n  Результаты: images_ru_text/ ({ok_count} файлов)")
        log(f"  JSON: {TRANSLATIONS_JSON.name}")
        log(f"  Сводка: {SUMMARY_MD.name}")
    else:
        log("\nЭто была оценка. Уберите --dry-run для запуска.")


if __name__ == "__main__":
    main()
