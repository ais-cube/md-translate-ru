# md-translate-ru

Автоматический переводчик Markdown-документов EN->RU через Anthropic Claude API.

Создан для замены ручного перевода через чат Claude Pro, который упирается в 5-часовые лимиты сообщений. Скрипт воспроизводит полный переводческий пайплайн с поддержкой глоссариев, anti-AI cleanup и контролем качества - но работает через API без ограничений.

> **TL;DR:** 145-страничная книга: вместо $20/мес + 15 часов ручной работы - **$6.56 и 20 минут автоматически**.

## Возможности

- Перевод Markdown с сохранением структуры 1:1 (заголовки, таблицы, code blocks, ссылки, изображения)
- Канонический глоссарий (`glossary.json`) - единый источник терминологии
- Автоматическая разбивка больших файлов на чанки по заголовкам
- Anti-AI cleanup по правилам HUMANIZER.md
- Batch API для двукратной экономии
- Dry-run режим для оценки стоимости до запуска
- Сбор новых терминов-кандидатов

## Быстрый старт

### 1. Установка

```bash
git clone https://github.com/ais-cube/md-translate-ru.git
cd md-translate-ru
pip install -r requirements.txt
```

### 2. API-ключ

Получи ключ на [console.anthropic.com](https://console.anthropic.com/) и пополни баланс ($5-10 достаточно для книги).

```bash
export ANTHROPIC_API_KEY='sk-ant-api03-...'
```

### 3. Подготовка файлов

```
my-project/
  docs_en/          # исходники на английском
  docs_ru/          # сюда попадут переводы (создастся автоматически)
  glossary.json     # каноническая терминология (опционально)
  translate_api.py  # этот скрипт
```

### 4. Запуск

```bash
# Оценить стоимость (без перевода)
python translate_api.py --dry-run

# Перевести непереведённые файлы
python translate_api.py

# Перевести конкретный файл
python translate_api.py --file 01_Introduction.md

# Перевести всё заново
python translate_api.py --force

# Batch API (в 2 раза дешевле, результат до 24ч)
python translate_api.py --batch
```

## Стоимость

Цены Anthropic API за 1M токенов (февраль 2026):

| Модель | Input | Output | Книга 145 стр. | Batch API |
|---|---|---|---|---|
| Sonnet 4.5 (по умолчанию) | $3 | $15 | ~$6.56 | ~$3.28 |
| Haiku 4.5 | $0.80 | $4 | ~$1.70 | ~$0.85 |
| Opus 4.5 | $15 | $75 | ~$33 | ~$16.50 |

Рекомендация: Sonnet для основного перевода. Opus - для ревью критичных глав.

## Как это работает

```
docs_en/*.md ──> [Чанкинг по ##] ──> [API + глоссарий + HUMANIZER] ──> docs_ru/*.md
```

1. Скрипт загружает `glossary.json`, `TRANSLATE.md`, `HUMANIZER.md` в системный промпт
2. Для каждого файла из `docs_en/`:
   - Проверяет, есть ли уже перевод в `docs_ru/`
   - Если файл > 40 000 символов - разбивает на чанки по заголовкам `##`
   - Отправляет в Claude API с полным контекстом правил перевода
   - Сохраняет результат в `docs_ru/`
3. Показывает стоимость и статистику по каждому файлу

### Разбивка на чанки

Большие файлы автоматически разбиваются по заголовкам второго уровня (`##`). Если отдельная секция всё ещё превышает лимит - разбивка идёт по параграфам. Потолок по размеру файла отсутствует.

### Глоссарий

Файл `glossary.json` - JSON-массив с каноническими парами терминов:

```json
[
  {
    "term_en": "Context Window",
    "term_ru": "контекстное окно",
    "definition": "Maximum number of tokens an AI model can process at once.",
    "rationale": "Standard LLM term used throughout the manuscript."
  }
]
```

Если `glossary.json` присутствует - все совпадающие термины переводятся строго по нему. Если файла нет - скрипт работает без глоссария.

### Сбор терминов-кандидатов

Новые терминоподобные сущности автоматически попадают в `glossary_candidates.json`. Структура:

```json
[
  {
    "term_en": "agentic workflow",
    "term_ru": "агентный рабочий процесс",
    "source_file": "docs_en/02_Chapter_1.md",
    "source_context": "## Agentic Workflow Patterns"
  }
]
```

## Конфигурация

### Переменные окружения

| Переменная | Описание | По умолчанию |
|---|---|---|
| `ANTHROPIC_API_KEY` | API-ключ Anthropic (обязателен) | - |
| `TRANSLATE_MODEL` | Модель для перевода | `claude-sonnet-4-5-20250929` |

### Параметры скрипта

| Флаг | Описание |
|---|---|
| `--file FILENAME` | Перевести конкретный файл |
| `--force` | Перезаписать существующие переводы |
| `--dry-run` | Оценить стоимость без перевода |
| `--batch` | Использовать Batch API (дешевле, до 24ч) |
| `--batch-status ID` | Проверить статус пакета |
| `--model MODEL` | Указать модель |

### Настройки в коде

В начале `translate_api.py` можно изменить:

```python
MAX_OUTPUT_TOKENS = 16384   # макс. токенов на ответ
CHUNK_SIZE_CHARS = 40000    # порог разбивки на чанки (~10k токенов)
```

Если перевод обрезается - уменьши `CHUNK_SIZE_CHARS` до 25000.

## Batch API

Batch API обрабатывает запросы в фоновом режиме (до 24 часов) и стоит в 2 раза дешевле.

```bash
# Отправить пакет
python translate_api.py --batch
# Вывод: Batch ID: msgbatch_abc123

# Проверить статус
python translate_api.py --batch-status msgbatch_abc123

# Когда статус "ended" - результаты сохранятся в docs_ru/
```

## Интеграция с AgenticDesignPatternsRU

Скрипт создавался для пайплайна [AgenticDesignPatternsRU](https://github.com/densmirnov/AgenticDesignPatternsRU) и полностью совместим с его агентами:

1. `TRANSLATOR` - скрипт воспроизводит его логику
2. `TRANSLATION_REVIEWER` - можно запустить отдельным проходом через API
3. `GLOSSARY_MINER` / `GLOSSARY_CURATOR` - работают с тем же `glossary_candidates.json`

Для использования: скопируй `translate_api.py` в корень репо AgenticDesignPatternsRU.

## Устранение проблем

| Проблема | Решение |
|---|---|
| `ОШИБКА: установите ANTHROPIC_API_KEY` | Проверь `echo $ANTHROPIC_API_KEY` |
| Перевод обрезается | Уменьши `CHUNK_SIZE_CHARS` до 25000 |
| Rate limit | Скрипт делает паузы; при ошибке подождать и перезапустить |
| Невалидный JSON в глоссарии | Скрипт остановится с ошибкой (fail-fast) |
| `pip install anthropic` не работает | Попробуй `pip3 install anthropic` или `python -m pip install anthropic` |

## Требования

- Python 3.10+
- Пакет `anthropic` (SDK)
- API-ключ Anthropic с балансом

## Лицензия

MIT License. Смотри [LICENSE](LICENSE).

---

*Вопросы и предложения - через [Issues](../../issues).*
