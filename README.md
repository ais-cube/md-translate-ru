# md-translate-ru

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Anthropic API](https://img.shields.io/badge/API-Anthropic%20Claude-blueviolet.svg)](https://docs.anthropic.com/)

Автоматический переводчик Markdown-документов и изображений **EN -> RU** через Anthropic Claude API.

Заменяет ручной перевод через чат Claude Pro, который упирается в 5-часовые лимиты сообщений. Два скрипта воспроизводят полный переводческий пайплайн с глоссариями, anti-AI cleanup и Vision API - без ограничений.

## Зачем

| | Claude Pro (чат, $20/мес) | md-translate-ru (API) |
|---|---|---|
| Книга 145 стр. | ~15 часов, 3 лимита | **20 минут, $6.56** |
| 67 изображений | вручную, по одному | **15 минут, ~$2.80** |
| Batch API | - | **в 2 раза дешевле** |
| Глоссарий | держать в голове | подхватывается автоматически |

## Возможности

**Перевод текста** (`translate_api.py`):
- Перевод Markdown с сохранением структуры 1:1 (заголовки, таблицы, code blocks, ссылки)
- Канонический глоссарий (`glossary.json`) как единый источник терминологии
- Автоматическая разбивка больших файлов на чанки по заголовкам `##`
- Anti-AI cleanup по правилам HUMANIZER.md
- Batch API (в 2 раза дешевле)
- Dry-run для оценки стоимости

**Перевод изображений** (`translate_images.py`):
- Извлечение всего текста из схем, скриншотов, графиков, таблиц через Vision API
- Структурированный выход: тип, описание, таблица текстовых элементов EN -> RU
- Автоматический сбор alt-текстов из Markdown-файлов для контекста
- Готовый перевод alt-текста для подстановки в документ
- Единый JSON со всеми переводами + сводная таблица

## Быстрый старт

### 1. Установка

```bash
git clone https://github.com/ais-cube/md-translate-ru.git
cd md-translate-ru
pip install -r requirements.txt
```

### 2. API-ключ

Зарегистрируйся на [console.anthropic.com](https://console.anthropic.com/), создай ключ и пополни баланс ($5-10 достаточно для целой книги).

```bash
export ANTHROPIC_API_KEY='sk-ant-api03-...'
```

Windows (PowerShell):
```powershell
$env:ANTHROPIC_API_KEY = 'sk-ant-api03-...'
```

### 3. Структура проекта

```
my-project/
  translate_api.py          # перевод текста
  translate_images.py       # перевод изображений
  docs_en/                  # исходники Markdown
  docs_ru/                  # переводы Markdown (создастся)
  images/                   # исходные изображения
  images_ru_text/           # описания изображений (создастся)
  glossary.json             # терминология (опционально)
  image_translations.json   # переводы изображений (создастся)
```

### 4. Перевод текста

```bash
# Оценить стоимость
python translate_api.py --dry-run

# Перевести все непереведённые файлы
python translate_api.py

# Конкретный файл
python translate_api.py --file 01_Introduction.md

# Перевести заново
python translate_api.py --force
```

### 5. Перевод изображений

```bash
# Оценить стоимость
python translate_images.py --dry-run

# Обработать все изображения
python translate_images.py

# Конкретное изображение
python translate_images.py --file fig-006.png

# Обработать заново
python translate_images.py --force
```

---

## Перевод изображений - подробности

### Что делает скрипт

Для каждого изображения из `images/` создаёт структурированный Markdown-файл в `images_ru_text/`:

```
images/fig-006.png  ->  images_ru_text/fig-006.md
```

### Формат выходного файла

Каждый файл содержит семантически организованное описание:

```markdown
## Тип изображения
Схема

## Краткое описание
Архитектурная схема паттерна Prompt Chaining. Показывает, как
пользователь отправляет серию промптов нескольким агентам...

## Структура изображения
Схема построена вертикально-горизонтально с тремя уровнями:
- Слева: фигура пользователя, от которой отходят две стрелки
- Нижний поток: Prompt 1 -> Agent 1
- Верхний поток: Prompt n -> Agent n -> Output
...

## Текстовые элементы

| Оригинал (EN) | Перевод (RU) | Расположение |
|---|---|---|
| User | Пользователь | левая часть |
| Prompt 1 | Промпт 1 | нижний левый блок |
| Agent 1 | Агент 1 | нижний правый блок |
| Output | Выход | верхний блок |

## Перевод для alt-текста
Рис. 2: Паттерн Prompt Chaining - пользователь отправляет серию
промптов агентам, где выход каждого агента служит входом для следующего.
```

Полный пример: [`examples/images_ru_text/fig-006.md`](examples/images_ru_text/fig-006.md)

### Откуда берётся контекст

Скрипт автоматически собирает alt-тексты из `docs_en/*.md`. Если в документе написано:

```markdown
![Fig. 2: Prompt Chaining Pattern...](../images/fig-006.png)
```

...этот alt-текст передаётся в Vision API как дополнительный контекст. Модель понимает назначение изображения и даёт более точный перевод.

### Выходные файлы

| Файл | Описание |
|---|---|
| `images_ru_text/*.md` | Отдельный Markdown на каждое изображение |
| `image_translations.json` | Единый JSON со всеми переводами |
| `image_summary.md` | Сводная таблица: файл, размер, статус, стоимость |

### Стоимость перевода изображений

Vision API тарифицируется по размеру изображения:

| Размер | Токены (примерно) | Стоимость |
|---|---|---|
| < 100 KB | ~400 | ~$0.01 |
| 100-300 KB | ~800 | ~$0.02 |
| 300-500 KB | ~1 600 | ~$0.03 |
| > 500 KB | ~3 200 | ~$0.05 |

67 изображений общим размером ~12 MB: **~$2.80** (Sonnet), **~$1.40** (Batch).

---

## Перевод текста - подробности

### Как это работает

```
docs_en/*.md
     |
     v
 [Разбивка на чанки по ## заголовкам]
     |
     v
 [Claude API + системный промпт]
 |  - TRANSLATE.md (правила перевода)
 |  - HUMANIZER.md (anti-AI cleanup)
 |  - glossary.json (канонические термины)
     |
     v
docs_ru/*.md
```

1. Скрипт загружает глоссарий, правила перевода и HUMANIZER в системный промпт
2. Для каждого файла из `docs_en/`:
   - Проверяет, есть ли перевод в `docs_ru/`
   - Если файл > 40 000 символов - разбивает на чанки по `##`
   - Отправляет в Claude API
   - Сохраняет результат в `docs_ru/`
3. Выводит стоимость и статистику

### Стоимость перевода текста

| Модель | Input / 1M tok | Output / 1M tok | Книга 145 стр. | Batch API |
|---|---|---|---|---|
| **Sonnet 4.5** (по умолчанию) | $3 | $15 | **~$6.56** | **~$3.28** |
| Haiku 4.5 | $0.80 | $4 | ~$1.70 | ~$0.85 |
| Opus 4.5 | $15 | $75 | ~$33 | ~$16.50 |

Грубая формула: **~$7-8 за 1 МБ** исходного текста (Sonnet), **~$3.5-4 через Batch API**.

---

## Глоссарий

Файл `glossary.json` - JSON-массив канонических пар терминов. Используется обоими скриптами:

```json
[
  {
    "term_en": "Context Window",
    "term_ru": "контекстное окно",
    "definition": "Maximum number of tokens an AI model can process at once.",
    "rationale": "Standard LLM term."
  }
]
```

Без файла оба скрипта работают в свободном режиме. Пример: [`examples/glossary.json`](examples/glossary.json).

## Batch API

Асинхронная обработка - результат до 24 часов, стоимость в 2 раза ниже. Работает для обоих скриптов:

```bash
# Текст
python translate_api.py --batch
python translate_api.py --batch-status msgbatch_abc123

# Изображения
python translate_images.py --batch
python translate_images.py --batch-status msgbatch_def456
```

## Конфигурация

### Переменные окружения

| Переменная | Описание | По умолчанию |
|---|---|---|
| `ANTHROPIC_API_KEY` | API-ключ (обязателен) | - |
| `TRANSLATE_MODEL` | Модель | `claude-sonnet-4-5-20250929` |

### Флаги командной строки

Оба скрипта поддерживают одинаковый набор флагов:

| Флаг | Описание |
|---|---|
| `--file FILENAME` | Обработать конкретный файл |
| `--force` | Обработать заново (перезаписать) |
| `--dry-run` | Оценить стоимость без запуска |
| `--batch` | Использовать Batch API |
| `--batch-status ID` | Проверить статус пакета |
| `--model MODEL` | Указать модель |

### Настройки в коде

`translate_api.py`:
```python
MAX_OUTPUT_TOKENS = 16384   # макс. токенов на ответ
CHUNK_SIZE_CHARS = 40000    # порог разбивки (~10k токенов)
```

`translate_images.py`:
```python
MAX_OUTPUT_TOKENS = 8192    # макс. токенов на описание изображения
```

## Совместимость с AgenticDesignPatternsRU

Оба скрипта создавались для пайплайна [AgenticDesignPatternsRU](https://github.com/densmirnov/AgenticDesignPatternsRU):

- `translate_api.py` воспроизводит логику агента `TRANSLATOR`
- `translate_images.py` дополняет пайплайн Vision-анализом
- Используют общий `glossary.json` (142 термина)
- Совместимы с агентами `TRANSLATION_REVIEWER`, `GLOSSARY_MINER`, `GLOSSARY_CURATOR`

Для интеграции: скопируй оба скрипта в корень репо AgenticDesignPatternsRU.

## Устранение проблем

| Проблема | Решение |
|---|---|
| `ОШИБКА: установите ANTHROPIC_API_KEY` | `echo $ANTHROPIC_API_KEY` - проверить ключ |
| Перевод обрезается | Уменьшить `CHUNK_SIZE_CHARS` до 25000 |
| Rate limit | Скрипт делает паузы; подождать и перезапустить |
| Невалидный JSON в глоссарии | Скрипт остановится (fail-fast); проверить JSON |
| `ModuleNotFoundError: anthropic` | `pip install anthropic` |
| Изображение не распознаётся | Проверить формат (png, jpg, jpeg, gif, webp) |
| `Папка images/ не найдена` | Создать `images/` и положить файлы |

## Структура репозитория

```
md-translate-ru/
  translate_api.py              # перевод текста Markdown
  translate_images.py           # перевод изображений (Vision API)
  requirements.txt              # зависимости
  LICENSE                       # MIT
  CHANGELOG.md
  CONTRIBUTING.md
  examples/
    glossary.json               # пример глоссария
    glossary_candidates.json
    docs_en/
      01_Introduction.md        # тестовый документ
    images_ru_text/
      fig-006.md                # пример выхода translate_images
```

## Требования

- Python 3.10+
- Пакет [anthropic](https://pypi.org/project/anthropic/) >= 0.40.0
- API-ключ Anthropic с балансом

## Лицензия

[MIT](LICENSE)

---

Вопросы и предложения - через [Issues](https://github.com/ais-cube/md-translate-ru/issues).
