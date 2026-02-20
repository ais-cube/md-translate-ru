# Changelog

## [1.1.0] - 2026-02-20

### Добавлено

- Скрипт `translate_images.py` - перевод изображений через Claude Vision API
- Структурированный Markdown-выход: тип, описание, текстовые элементы EN -> RU, alt-текст
- Автоматический сбор alt-текстов из docs_en/*.md
- Единый JSON (`image_translations.json`) и сводная таблица (`image_summary.md`)
- Batch API для изображений
- Пример выходного файла `examples/images_ru_text/fig-006.md`

## [1.0.0] - 2026-02-20

### Добавлено

- Основной скрипт перевода `translate_api.py`
- Поддержка глоссариев (`glossary.json`)
- Автоматическая разбивка больших файлов на чанки по заголовкам
- Anti-AI cleanup через HUMANIZER.md
- Batch API для двукратной экономии
- Dry-run режим для оценки стоимости
- Сбор терминов-кандидатов в `glossary_candidates.json`
- Примеры в `examples/`
