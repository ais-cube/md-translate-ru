#!/usr/bin/env python3
"""
translate.py - Единый переводчик EN->RU: текст + изображения.

Автоматически определяет, где в файле текст, а где картинки,
и переводит всё за один запуск.

Поддерживаемые форматы:
  - PDF  -> извлекает текст + рендерит страницы-картинки через Vision API
  - MD   -> переводит текст + обрабатывает вложенные изображения
  - Папка MD-файлов -> пакетная обработка

Результаты:
  output/
    translated/           # переведённые тексты (.md)
    images_described/     # описания изображений (.md)
    image_translations.json
    summary.md

Требования:
    pip install anthropic pymupdf

Использование:
    python translate.py document.pdf
    python translate.py article.md
    python translate.py docs_en/
    python translate.py document.pdf --dry-run
    python translate.py document.pdf --file page-03   # конкретная страница/файл
    python translate.py document.pdf --batch
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
# Пути (создаются относительно входного файла)
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
GLOSSARY_PATH = ROOT / "glossary.json"
TRANSLATE_SPEC = ROOT / "TRANSLATE.md"
HUMANIZER_SPEC = ROOT / "HUMANIZER.md"

# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
MAX_OUTPUT_TOKENS_TEXT = 16384
MAX_OUTPUT_TOKENS_IMAGE = 8192
CHUNK_SIZE_CHARS = 40000

COST_INPUT_PER_M = 3.0
COST_OUTPUT_PER_M = 15.0

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# Порог: если извлечённый текст страницы PDF короче этого - считаем страницу "картинкой"
PDF_TEXT_THRESHOLD = 150  # символов

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
            data = json.load(f)
        if not isinstance(data, list):
            log(f"ОШИБКА: {path.name} не является JSON-массивом", "ERROR")
            sys.exit(1)
        return data
    except json.JSONDecodeError as e:
        log(f"ОШИБКА: невалидный JSON в {path.name}: {e}", "ERROR")
        sys.exit(1)


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def estimate_tokens(text: str) -> int:
    return len(text) // 3


def format_cost(input_tokens: int, output_tokens: int) -> str:
    cost = (input_tokens * COST_INPUT_PER_M + output_tokens * COST_OUTPUT_PER_M) / 1_000_000
    return f"${cost:.4f}"


def format_cost_total(input_tokens: int, output_tokens: int) -> str:
    cost = (input_tokens * COST_INPUT_PER_M + output_tokens * COST_OUTPUT_PER_M) / 1_000_000
    return f"${cost:.2f}"


def image_to_base64(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    media_types = {
        ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    }
    media_type = media_types.get(suffix, "image/png")
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return data, media_type


# ---------------------------------------------------------------------------
# Классификация контента
# ---------------------------------------------------------------------------

class ContentBlock:
    """Единица контента для перевода."""
    TEXT = "text"
    IMAGE = "image"

    def __init__(self, block_type: str, **kwargs):
        self.type = block_type
        self.source_name = kwargs.get("source_name", "")
        # Для TEXT
        self.text = kwargs.get("text", "")
        # Для IMAGE
        self.image_path = kwargs.get("image_path", None)
        self.alt_text = kwargs.get("alt_text", "")
        # Общие
        self.page_num = kwargs.get("page_num", 0)

    def __repr__(self):
        if self.type == self.TEXT:
            return f"<Text '{self.source_name}' {len(self.text)} chars>"
        return f"<Image '{self.source_name}' alt='{self.alt_text[:30]}...'>"


# ---------------------------------------------------------------------------
# Парсеры входных файлов
# ---------------------------------------------------------------------------

def parse_pdf(pdf_path: Path, temp_dir: Path) -> list[ContentBlock]:
    """Разобрать PDF: для каждой страницы определить текст или картинка."""
    try:
        import fitz
    except ImportError:
        log("ОШИБКА: установите PyMuPDF: pip install pymupdf", "ERROR")
        sys.exit(1)

    doc = fitz.open(str(pdf_path))
    blocks = []
    images_dir = temp_dir / "_page_images"
    images_dir.mkdir(parents=True, exist_ok=True)

    log(f"PDF: {pdf_path.name}, {doc.page_count} страниц")

    for i, page in enumerate(doc):
        page_num = i + 1
        page_name = f"page-{page_num:02d}"

        # Извлекаем текст
        text = page.get_text("text").strip()
        # Считаем изображения на странице
        image_list = page.get_images(full=True)
        has_images = len(image_list) > 0

        # Логика классификации:
        # 1. Если текста много и нет картинок -> текстовый блок
        # 2. Если текста мало или есть картинки -> рендерим как изображение
        # 3. Если текст есть И картинки есть -> и текст, и изображение
        #    (Vision API хорошо читает текст с картинок, поэтому отправляем
        #     как изображение - это надёжнее для скриншотов и диаграмм)

        if len(text) > PDF_TEXT_THRESHOLD and not has_images:
            # Чисто текстовая страница
            blocks.append(ContentBlock(
                ContentBlock.TEXT,
                source_name=page_name,
                text=text,
                page_num=page_num,
            ))
            log(f"  {page_name}: ТЕКСТ ({len(text)} символов)")

        else:
            # Страница с картинками или мало текста -> рендерим
            pix = page.get_pixmap(dpi=200)
            img_path = images_dir / f"{page_name}.png"
            pix.save(str(img_path))
            size_kb = img_path.stat().st_size / 1024

            # alt_text - первые 200 символов текста как контекст
            alt = text[:200] if text else ""

            blocks.append(ContentBlock(
                ContentBlock.IMAGE,
                source_name=page_name,
                image_path=img_path,
                alt_text=alt,
                page_num=page_num,
            ))
            log(f"  {page_name}: ИЗОБРАЖЕНИЕ ({size_kb:.0f} KB, "
                f"{len(image_list)} встроенных картинок, {len(text)} символов текста)")

    doc.close()
    return blocks


def parse_markdown(md_path: Path, base_dir: Path = None) -> list[ContentBlock]:
    """Разобрать MD-файл: текст + ссылки на изображения."""
    content = md_path.read_text(encoding="utf-8")
    if base_dir is None:
        base_dir = md_path.parent

    blocks = []

    # Найти все ссылки на изображения
    image_refs = list(re.finditer(
        r'!\[([^\]]*)\]\(([^)]+)\)', content
    ))

    # Собрать пути изображений
    found_images = []
    for match in image_refs:
        alt_text = match.group(1).strip()
        img_rel_path = match.group(2).strip()

        # Пробуем найти файл
        candidates = [
            base_dir / img_rel_path,
            md_path.parent / img_rel_path,
            ROOT / img_rel_path,
        ]
        img_path = None
        for c in candidates:
            if c.exists() and c.suffix.lower() in IMAGE_EXTENSIONS:
                img_path = c
                break

        if img_path:
            found_images.append((img_path, alt_text))

    # Текстовый блок - весь файл
    blocks.append(ContentBlock(
        ContentBlock.TEXT,
        source_name=md_path.stem,
        text=content,
    ))
    log(f"  {md_path.name}: ТЕКСТ ({len(content)} символов)")

    # Блоки изображений
    for img_path, alt_text in found_images:
        blocks.append(ContentBlock(
            ContentBlock.IMAGE,
            source_name=img_path.stem,
            image_path=img_path,
            alt_text=alt_text,
        ))
        size_kb = img_path.stat().st_size / 1024
        log(f"  {img_path.name}: ИЗОБРАЖЕНИЕ ({size_kb:.0f} KB)")

    if not found_images:
        log(f"  Изображений не найдено в ссылках")

    return blocks


def parse_directory(dir_path: Path) -> list[ContentBlock]:
    """Разобрать папку MD-файлов."""
    blocks = []
    md_files = sorted(dir_path.glob("*.md"))
    if not md_files:
        log(f"В папке {dir_path} нет .md файлов", "ERROR")
        sys.exit(1)

    log(f"Папка: {dir_path}, {len(md_files)} файлов")
    for md_file in md_files:
        blocks.extend(parse_markdown(md_file, dir_path))

    return blocks


# ---------------------------------------------------------------------------
# Системные промпты
# ---------------------------------------------------------------------------

def build_text_system_prompt(glossary: list) -> str:
    """Системный промпт для перевода текста."""
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
        glossary_text = "\n".join(f"- {e['term_en']} -> {e['term_ru']}" for e in glossary)
        parts.append(glossary_text)

    return "\n\n".join(parts)


def build_image_system_prompt(glossary: list) -> str:
    """Системный промпт для анализа изображений."""
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

Включить ВСЕ текстовые надписи, заголовки, подписи, метки, кнопки, пункты меню.
Если текст мелкий или частично обрезан - указать [нечитаемо] или [частично: ...].

## Перевод для alt-текста
Одно предложение на русском для использования как alt-текст в Markdown.

ПРАВИЛА:
- Технический перевод, нейтральный тон.
- Не переводить: URL, email, имена файлов, code identifiers, тикеры.
- Числовой формат: пробел-разделитель (1 000), десятичная запятая (15,73).
- Используй канонические термины из глоссария (если предоставлен).""")

    if glossary:
        glossary_text = "\n".join(f"- {e['term_en']} -> {e['term_ru']}" for e in glossary)
        parts.append(f"\n\nКАНОНИЧЕСКИЙ ГЛОССАРИЙ:\n{glossary_text}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Разбивка текста на чанки
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
# API-вызовы
# ---------------------------------------------------------------------------

def translate_text_block(client, model: str, system_prompt: str,
                         text: str, name: str,
                         is_chunk=False, chunk_num=0, total_chunks=0) -> tuple[str, int, int]:
    """Перевести текстовый блок. Возвращает (перевод, input_tok, output_tok)."""
    chunk_info = ""
    if is_chunk:
        chunk_info = f"\n\nЭто чанк {chunk_num}/{total_chunks}. Переводи только этот фрагмент."

    user_prompt = f"""Переведи следующий текст с английского на русский.

Файл: {name}{chunk_info}

КРИТИЧЕСКИ ВАЖНО:
1. Сохрани структуру Markdown 1:1 (заголовки, списки, таблицы, code blocks, ссылки, изображения).
2. НЕ переводи: code blocks, URL, адреса, идентификаторы.
3. Используй ТОЛЬКО канонические термины из глоссария.
4. Числовой формат: разделитель тысяч пробел (1 000), десятичная запятая (15,73).
5. Выполни anti-AI cleanup (убери штампы), но НЕ добавляй личный голос.
6. Верни ТОЛЬКО переведённый текст. Без комментариев.

---НАЧАЛО---

{text}

---КОНЕЦ---

Верни ТОЛЬКО перевод."""

    response = client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS_TEXT,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text, response.usage.input_tokens, response.usage.output_tokens


def analyze_image_block(client, model: str, system_prompt: str,
                        image_path: Path, alt_text: str = "") -> tuple[str, int, int]:
    """Отправить изображение в Vision API. Возвращает (markdown, inp_tok, out_tok)."""
    b64_data, media_type = image_to_base64(image_path)

    user_content = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64_data},
        }
    ]

    context = f"Файл: {image_path.name}"
    if alt_text:
        context += f"\nКонтекст/alt-текст: {alt_text}"
    context += "\n\nИзвлеки и переведи всё текстовое содержимое из этого изображения."
    user_content.append({"type": "text", "text": context})

    response = client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS_IMAGE,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text, response.usage.input_tokens, response.usage.output_tokens


# ---------------------------------------------------------------------------
# Обработка блоков
# ---------------------------------------------------------------------------

def process_block(client, model: str, text_prompt: str, image_prompt: str,
                  block: ContentBlock, output_dir: Path,
                  dry_run: bool = False) -> dict:
    """Обработать один блок (текст или изображение). Возвращает статистику."""

    stats = {
        "name": block.source_name,
        "type": block.type,
        "input_tokens": 0,
        "output_tokens": 0,
        "status": "ok",
    }

    if block.type == ContentBlock.TEXT:
        return _process_text_block(
            client, model, text_prompt, block, output_dir, dry_run, stats
        )
    else:
        return _process_image_block(
            client, model, image_prompt, block, output_dir, dry_run, stats
        )


def _process_text_block(client, model, system_prompt, block, output_dir, dry_run, stats):
    """Перевести текстовый блок."""
    text = block.text
    chunks = split_into_chunks(text)
    stats["chunks"] = len(chunks)
    stats["source_chars"] = len(text)

    translated_dir = output_dir / "translated"
    translated_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        est_input = estimate_tokens(text) + 8000
        est_output = int(estimate_tokens(text) * 1.15)
        stats["input_tokens"] = est_input
        stats["output_tokens"] = est_output
        stats["status"] = "dry-run"
        return stats

    translated_parts = []
    for i, chunk in enumerate(chunks, 1):
        is_chunked = len(chunks) > 1
        if is_chunked:
            log(f"    Чанк {i}/{len(chunks)} ({len(chunk):,} символов)...")

        try:
            translation, inp, out = translate_text_block(
                client, model, system_prompt,
                chunk, block.source_name,
                is_chunk=is_chunked, chunk_num=i, total_chunks=len(chunks),
            )
            translated_parts.append(translation)
            stats["input_tokens"] += inp
            stats["output_tokens"] += out
        except Exception as e:
            log(f"    ОШИБКА: {e}", "ERROR")
            stats["status"] = f"error: {e}"
            return stats

        if is_chunked and i < len(chunks):
            time.sleep(1)

    full_translation = "\n\n".join(translated_parts)
    out_path = translated_dir / f"{block.source_name}.md"
    out_path.write_text(full_translation, encoding="utf-8")

    cost = format_cost(stats["input_tokens"], stats["output_tokens"])
    log(f"    -> {out_path.name} ({stats['input_tokens']:,} + {stats['output_tokens']:,} tok = {cost})")
    return stats


def _process_image_block(client, model, system_prompt, block, output_dir, dry_run, stats):
    """Обработать изображение через Vision API."""
    img_path = block.image_path
    stats["size_bytes"] = img_path.stat().st_size
    stats["alt_text"] = block.alt_text

    described_dir = output_dir / "images_described"
    described_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        stats["input_tokens"] = max(img_path.stat().st_size // 750, 200) + 2000
        stats["output_tokens"] = 800
        stats["status"] = "dry-run"
        return stats

    try:
        result_md, inp, out = analyze_image_block(
            client, model, system_prompt, img_path, block.alt_text
        )
        stats["input_tokens"] = inp
        stats["output_tokens"] = out

        # Добавить заголовок и контекст
        header = f"# {img_path.name}\n"
        if block.alt_text:
            header += f"\n> **Контекст:** {block.alt_text}\n"
        header += "\n---\n\n"

        full_md = header + result_md
        out_path = described_dir / f"{block.source_name}.md"
        out_path.write_text(full_md, encoding="utf-8")

        cost = format_cost(inp, out)
        log(f"    -> {out_path.name} ({inp:,} + {out:,} tok = {cost})")

    except Exception as e:
        log(f"    ОШИБКА: {e}", "ERROR")
        stats["status"] = f"error: {e}"

    return stats


# ---------------------------------------------------------------------------
# Batch API
# ---------------------------------------------------------------------------

def create_batch_requests(text_prompt: str, image_prompt: str,
                          blocks: list[ContentBlock], model: str) -> list[dict]:
    """Подготовить запросы для Batch API."""
    requests = []

    for block in blocks:
        if block.type == ContentBlock.TEXT:
            chunks = split_into_chunks(block.text)
            for i, chunk in enumerate(chunks, 1):
                is_chunked = len(chunks) > 1
                chunk_info = ""
                if is_chunked:
                    chunk_info = f"\n\nЭто чанк {i}/{len(chunks)}."

                user_prompt = f"""Переведи следующий текст с английского на русский.
Файл: {block.source_name}{chunk_info}

---НАЧАЛО---
{chunk}
---КОНЕЦ---

Верни ТОЛЬКО перевод."""

                requests.append({
                    "custom_id": f"text__{block.source_name}__chunk_{i}_of_{len(chunks)}",
                    "params": {
                        "model": model,
                        "max_tokens": MAX_OUTPUT_TOKENS_TEXT,
                        "system": text_prompt,
                        "messages": [{"role": "user", "content": user_prompt}],
                    }
                })

        elif block.type == ContentBlock.IMAGE:
            b64_data, media_type = image_to_base64(block.image_path)
            context = f"Файл: {block.image_path.name}"
            if block.alt_text:
                context += f"\nКонтекст: {block.alt_text}"
            context += "\n\nИзвлеки и переведи всё текстовое содержимое."

            requests.append({
                "custom_id": f"image__{block.source_name}",
                "params": {
                    "model": model,
                    "max_tokens": MAX_OUTPUT_TOKENS_IMAGE,
                    "system": image_prompt,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {
                                "type": "base64", "media_type": media_type, "data": b64_data
                            }},
                            {"type": "text", "text": context},
                        ]
                    }],
                }
            })

    return requests


def submit_batch(client, requests: list[dict]) -> str:
    batch = client.messages.batches.create(requests=requests)
    log(f"Batch создан: id={batch.id}, статус={batch.processing_status}")
    log(f"Проверить: python translate.py --batch-status {batch.id}")
    return batch.id


def check_batch_status(client, batch_id: str, output_dir: Path):
    batch = client.messages.batches.retrieve(batch_id)
    log(f"Batch {batch_id}")
    log(f"  Статус: {batch.processing_status}")
    if hasattr(batch, 'request_counts'):
        rc = batch.request_counts
        log(f"  Успешно: {rc.succeeded}, ошибки: {rc.errored}, в процессе: {rc.processing}")

    if batch.processing_status == "ended":
        log("Загрузка результатов...")
        _assemble_batch_results(client, batch_id, output_dir)


def _assemble_batch_results(client, batch_id: str, output_dir: Path):
    translated_dir = output_dir / "translated"
    described_dir = output_dir / "images_described"
    translated_dir.mkdir(parents=True, exist_ok=True)
    described_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for result in client.messages.batches.results(batch_id):
        cid = result.custom_id
        if result.result.type == "succeeded":
            results[cid] = result.result.message.content[0].text
        else:
            log(f"  Ошибка: {cid}: {result.result.type}", "ERROR")

    # Текстовые блоки (собрать чанки)
    text_chunks = {}
    for cid, text in results.items():
        if cid.startswith("text__"):
            match = re.match(r'^text__(.+?)__chunk_(\d+)_of_(\d+)$', cid)
            if match:
                name = match.group(1)
                chunk_n = int(match.group(2))
                total = int(match.group(3))
                if name not in text_chunks:
                    text_chunks[name] = {"total": total, "chunks": {}}
                text_chunks[name]["chunks"][chunk_n] = text

    for name, data in text_chunks.items():
        parts = [data["chunks"].get(i, f"<!-- MISSING CHUNK {i} -->")
                 for i in range(1, data["total"] + 1)]
        out_path = translated_dir / f"{name}.md"
        out_path.write_text("\n\n".join(parts), encoding="utf-8")
        log(f"  Текст: {out_path.name}")

    # Изображения
    for cid, text in results.items():
        if cid.startswith("image__"):
            name = cid.replace("image__", "")
            out_path = described_dir / f"{name}.md"
            header = f"# {name}\n\n---\n\n"
            out_path.write_text(header + text, encoding="utf-8")
            log(f"  Изображение: {out_path.name}")

    log(f"Готово: {len(text_chunks)} текстов, "
        f"{sum(1 for c in results if c.startswith('image__'))} изображений")


# ---------------------------------------------------------------------------
# Генерация сводки
# ---------------------------------------------------------------------------

def generate_summary(all_stats: list, output_dir: Path, input_name: str):
    """Создать summary.md и image_translations.json."""

    text_blocks = [s for s in all_stats if s["type"] == "text"]
    image_blocks = [s for s in all_stats if s["type"] == "image"]

    total_inp = sum(s["input_tokens"] for s in all_stats)
    total_out = sum(s["output_tokens"] for s in all_stats)

    lines = [
        f"# Сводка перевода: {input_name}\n",
        f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
        f"## Текстовые блоки: {len(text_blocks)}\n",
        "| Блок | Символов | Чанков | Токены (in+out) | Стоимость | Статус |",
        "|---|---|---|---|---|---|",
    ]
    for s in text_blocks:
        cost = format_cost(s["input_tokens"], s["output_tokens"])
        chars = s.get("source_chars", 0)
        chunks = s.get("chunks", 1)
        lines.append(f"| {s['name']} | {chars:,} | {chunks} | "
                     f"{s['input_tokens']:,}+{s['output_tokens']:,} | {cost} | {s['status']} |")

    lines.append(f"\n## Изображения: {len(image_blocks)}\n")
    lines.append("| Блок | Размер | Токены (in+out) | Стоимость | Статус |")
    lines.append("|---|---|---|---|---|")
    for s in image_blocks:
        cost = format_cost(s["input_tokens"], s["output_tokens"])
        size_kb = s.get("size_bytes", 0) / 1024
        lines.append(f"| {s['name']} | {size_kb:.0f} KB | "
                     f"{s['input_tokens']:,}+{s['output_tokens']:,} | {cost} | {s['status']} |")

    total_cost = format_cost_total(total_inp, total_out)
    batch_cost = format_cost_total(total_inp // 2, total_out // 2)  # rough batch estimate
    lines.append(f"\n## Итого\n")
    lines.append(f"- Текстовых блоков: {len(text_blocks)}")
    lines.append(f"- Изображений: {len(image_blocks)}")
    lines.append(f"- Токены: {total_inp:,} input + {total_out:,} output")
    lines.append(f"- Стоимость (Sonnet): **{total_cost}**")
    lines.append(f"- Стоимость (Batch): **~{batch_cost}**")

    summary_path = output_dir / "summary.md"
    summary_path.write_text("\n".join(lines), encoding="utf-8")

    # JSON для изображений
    if image_blocks:
        json_data = []
        for s in image_blocks:
            json_data.append({
                "filename": s["name"],
                "size_bytes": s.get("size_bytes", 0),
                "alt_text": s.get("alt_text", ""),
                "input_tokens": s["input_tokens"],
                "output_tokens": s["output_tokens"],
                "status": s["status"],
            })
        save_json(output_dir / "image_translations.json", json_data)


# ---------------------------------------------------------------------------
# Главная логика
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Единый переводчик EN->RU: текст + изображения",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python translate.py document.pdf              # PDF -> текст + картинки
  python translate.py article.md                # MD -> текст + вложенные картинки
  python translate.py docs_en/                  # папка MD-файлов
  python translate.py document.pdf --dry-run    # оценка стоимости
  python translate.py document.pdf --batch      # Batch API (дешевле)
  python translate.py --batch-status ID         # проверить статус
        """
    )
    parser.add_argument("input", nargs="?", help="PDF-файл, MD-файл или папка с MD")
    parser.add_argument("--file", help="Обработать конкретный блок (по имени)")
    parser.add_argument("--force", action="store_true", help="Обработать заново")
    parser.add_argument("--dry-run", action="store_true", help="Только оценить стоимость")
    parser.add_argument("--batch", action="store_true", help="Использовать Batch API")
    parser.add_argument("--batch-status", metavar="ID", help="Проверить статус Batch")
    parser.add_argument("--model", default=None, help=f"Модель (default: {DEFAULT_MODEL})")
    parser.add_argument("--output", default=None, help="Папка для результатов")
    args = parser.parse_args()

    model = args.model or os.getenv("TRANSLATE_MODEL", DEFAULT_MODEL)

    # ----- Клиент API -----
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
            log("ОШИБКА: pip install anthropic", "ERROR")
            sys.exit(1)
        client = anthropic.Anthropic(api_key=api_key)

    # ----- Batch status -----
    if args.batch_status:
        out_dir = Path(args.output) if args.output else ROOT / "output"
        check_batch_status(client, args.batch_status, out_dir)
        return

    # ----- Вход обязателен -----
    if not args.input:
        parser.error("Укажите входной файл или папку")

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        log(f"Не найден: {input_path}", "ERROR")
        sys.exit(1)

    # ----- Определяем папку вывода -----
    if args.output:
        output_dir = Path(args.output)
    else:
        output_dir = input_path.parent / f"{input_path.stem}_ru"
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = output_dir / "_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # ----- Загрузка конфигурации -----
    log("=" * 60)
    log("translate.py - Единый переводчик EN->RU")
    log("=" * 60)

    glossary = load_json(GLOSSARY_PATH)
    log(f"Глоссарий: {len(glossary)} терминов")

    text_prompt = build_text_system_prompt(glossary)
    image_prompt = build_image_system_prompt(glossary)
    log(f"Системные промпты: текст ~{estimate_tokens(text_prompt):,} tok, "
        f"изображения ~{estimate_tokens(image_prompt):,} tok")

    # ----- Парсинг входного файла -----
    log(f"\nВход: {input_path}")

    if input_path.is_dir():
        blocks = parse_directory(input_path)
    elif input_path.suffix.lower() == ".pdf":
        blocks = parse_pdf(input_path, temp_dir)
    elif input_path.suffix.lower() in (".md", ".markdown", ".txt"):
        blocks = parse_markdown(input_path)
    else:
        log(f"Неподдерживаемый формат: {input_path.suffix}", "ERROR")
        log("Поддерживаются: .pdf, .md, .txt, или папка с .md файлами")
        sys.exit(1)

    if not blocks:
        log("Нет блоков для обработки", "ERROR")
        sys.exit(1)

    # Фильтр по --file
    if args.file:
        blocks = [b for b in blocks if args.file in b.source_name]
        if not blocks:
            log(f"Блок '{args.file}' не найден", "ERROR")
            sys.exit(1)

    # Фильтр уже обработанных (если не --force)
    if not args.force:
        existing_texts = set(
            f.stem for f in (output_dir / "translated").glob("*.md")
        ) if (output_dir / "translated").exists() else set()
        existing_images = set(
            f.stem for f in (output_dir / "images_described").glob("*.md")
        ) if (output_dir / "images_described").exists() else set()

        before = len(blocks)
        blocks = [
            b for b in blocks
            if b.source_name not in existing_texts and b.source_name not in existing_images
        ]
        skipped = before - len(blocks)
        if skipped:
            log(f"Пропущено (уже обработаны): {skipped}. Используйте --force для перезаписи.")

    # ----- Сводка перед запуском -----
    text_count = sum(1 for b in blocks if b.type == ContentBlock.TEXT)
    image_count = sum(1 for b in blocks if b.type == ContentBlock.IMAGE)
    log(f"\nБлоков для обработки: {len(blocks)}")
    log(f"  Текст: {text_count}")
    log(f"  Изображения: {image_count}")
    log(f"Выход: {output_dir}/")

    # ----- Batch -----
    if args.batch and not args.dry_run:
        log("\nПодготовка Batch API...")
        requests = create_batch_requests(text_prompt, image_prompt, blocks, model)
        log(f"Запросов: {len(requests)}")
        batch_id = submit_batch(client, requests)
        log(f"\nBatch ID: {batch_id}")
        log(f"Проверить: python translate.py --batch-status {batch_id}")
        return

    # ----- Обработка -----
    mode = "dry-run" if args.dry_run else "перевод"
    log(f"\nМодель: {model}")
    log(f"Режим: {mode}\n")

    all_stats = []
    start_time = time.time()

    for i, block in enumerate(blocks, 1):
        type_label = "ТЕКСТ" if block.type == ContentBlock.TEXT else "ИЗОБРАЖЕНИЕ"
        log(f"[{i}/{len(blocks)}] {block.source_name} ({type_label})")

        stats = process_block(
            client, model, text_prompt, image_prompt,
            block, output_dir, dry_run=args.dry_run
        )
        all_stats.append(stats)

        # Пауза между запросами
        if not args.dry_run and i < len(blocks):
            time.sleep(1)

    elapsed = time.time() - start_time

    # ----- Сводка -----
    generate_summary(all_stats, output_dir, input_path.name)

    total_inp = sum(s["input_tokens"] for s in all_stats)
    total_out = sum(s["output_tokens"] for s in all_stats)
    total_cost = format_cost_total(total_inp, total_out)
    errors = sum(1 for s in all_stats if "error" in s["status"])

    log("\n" + "=" * 60)
    log("ИТОГО")
    log("=" * 60)
    log(f"  Блоков: {len(all_stats)} (текст: {text_count}, изображения: {image_count})")
    log(f"  Ошибок: {errors}")
    log(f"  Токены: {total_inp:,} input + {total_out:,} output")
    log(f"  Стоимость (Sonnet): {total_cost}")
    log(f"  Стоимость (Batch):  ~{format_cost_total(total_inp // 2, total_out // 2)}")
    log(f"  Время: {elapsed:.0f} сек")
    log(f"  Результаты: {output_dir}/")

    if args.dry_run:
        log("\nЭто была оценка. Для запуска перевода уберите --dry-run")


if __name__ == "__main__":
    main()
