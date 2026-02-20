# md-translate-ru

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Anthropic API](https://img.shields.io/badge/API-Anthropic%20Claude-blueviolet.svg)](https://docs.anthropic.com/)

Автоматический переводчик Markdown-документов **EN -> RU** через Anthropic Claude API.

Заменяет ручной перевод через чат Claude Pro, который упирается в 5-часовые лимиты сообщений. Скрипт воспроизводит полный переводческий пайплайн с глоссариями, anti-AI cleanup и контролем качества - но работает через API без ограничений.

## Зачем

| | Claude Pro (чат, $20/мес) | md-translate-ru (API) |
|---|---|---|
| Книга 145 стр. | ~15 часов, 3 лимита | **20 минут, $6.56** |
| Batch API | - | **20 минут + ожидание, $3.28** |
| Автоматизация | копировать-вставить руками | одна команда |
| Глоссарий | держать в голове | подхватывается автоматически |

## Возможности

- Перевод Markdown с сохранением структуры 1:1 (заголовки, таблицы, code blocks, ссылки, изображения)
- Канонический глоссарий (`glossary.json`) как единый источник терминологии
- Автоматическая разбивка больших файлов на чанки по заголовкам `##`
- Anti-AI cleanup по правилам [HUMANIZER.md](https://github.com/densmirnov/AgenticDesignPatternsRU/blob/main/HUMANIZER.md) - убирает штампы, сохраняет технический тон
- Поддержка Batch API (в 2 раза дешевле)
- Dry-run для оценки стоимости до запуска
- Нет ограничений на размер файла

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

### 3. Подготовка

Положи файлы по структуре:

```
my-project/
  translate_api.py          # скрипт (скопировать из этого репо)
  docs_en/                  # исходники на английском (.md)
    01_Introduction.md
    02_Architecture.md
    ...
  docs_ru/                  # переводы (создастся автоматически)
  glossary.json             # терминология (опционально)
  glossary_candidates.json  # новые термины (создастся автоматически)
```

### 4. Запуск

```bash
# Оценить стоимость без перевода
python translate_api.py --dry-run

# Перевести все непереведённые файлы
python translate_api.py

# Перевести конкретный файл
python translate_api.py --file 01_Introduction.md

# Перевести всё заново (перезаписать существующие)
python translate_api.py --force

# Batch API - в 2 раза дешевле, результат до 24 часов
python translate_api.py --batch
```

## Как это работает

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
 |  - glossary.json (142 термина)
     |
     v
docs_ru/*.md
```

1. Скрипт загружает глоссарий, правила перевода и HUMANIZER в системный промпт
2. Для каждого файла из `docs_en/`:
   - Проверяет, есть ли уже перевод в `docs_ru/`
   - Если файл > 40 000 символов - автоматически разбивает на чанки по `##`
   - Отправляет в Claude API
   - Сохраняет результат в `docs_ru/`
3. Выводит стоимость и статистику

## Стоимость

Цены Anthropic API (февраль 2026):

| Модель | Input / 1M tok | Output / 1M tok | Книга 145 стр. | Batch API |
|---|---|---|---|---|
| **Sonnet 4.5** (по умолчанию) | $3 | $15 | **~$6.56** | **~$3.28** |
| Haiku 4.5 | $0.80 | $4 | ~$1.70 | ~$0.85 |
| Opus 4.5 | $15 | $75 | ~$33 | ~$16.50 |

Рекомендация: **Sonnet** для основного перевода, **Opus** для ревью критичных глав.

Грубая формула: **~$7-8 за 1 МБ** исходного английского текста (Sonnet), **~$3.5-4 через Batch API**.

## Глоссарий

Файл `glossary.json` - JSON-массив канонических пар терминов. Если присутствует, все совпадения переводятся строго по нему:

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

Без файла скрипт работает в свободном режиме. Пример глоссария: [`examples/glossary.json`](examples/glossary.json).

## Batch API

Асинхронная обработка - результат до 24 часов, стоимость в 2 раза ниже:

```bash
# Отправить пакет
python translate_api.py --batch
# > Batch ID: msgbatch_abc123

# Проверить статус (запускать периодически)
python translate_api.py --batch-status msgbatch_abc123

# Когда статус "ended" - файлы сохранятся в docs_ru/
```

## Конфигурация

### Переменные окружения

| Переменная | Описание | По умолчанию |
|---|---|---|
| `ANTHROPIC_API_KEY` | API-ключ (обязателен) | - |
| `TRANSLATE_MODEL` | Модель | `claude-sonnet-4-5-20250929` |

### Флаги командной строки

| Флаг | Описание |
|---|---|
| `--file FILENAME` | Перевести конкретный файл из `docs_en/` |
| `--force` | Перезаписать существующие переводы |
| `--dry-run` | Оценить стоимость без перевода |
| `--batch` | Использовать Batch API |
| `--batch-status ID` | Проверить статус пакета |
| `--model MODEL` | Указать модель |

### Настройки в коде

В начале `translate_api.py`:

```python
MAX_OUTPUT_TOKENS = 16384   # макс. токенов на ответ
CHUNK_SIZE_CHARS = 40000    # порог разбивки (~10k токенов)
```

Если перевод обрезается - уменьши `CHUNK_SIZE_CHARS` до 25000.

## Совместимость с AgenticDesignPatternsRU

Скрипт создавался для пайплайна [AgenticDesignPatternsRU](https://github.com/densmirnov/AgenticDesignPatternsRU) и воспроизводит логику агента `TRANSLATOR`:

- Использует тот же `glossary.json` (142 термина)
- Применяет правила `TRANSLATE.md` (модальности, числовой формат, семантическая точность)
- Выполняет anti-AI cleanup по `HUMANIZER.md` без добавления "личного голоса"
- Совместим с агентами `TRANSLATION_REVIEWER`, `GLOSSARY_MINER`, `GLOSSARY_CURATOR`

Для интеграции: скопируй `translate_api.py` в корень репо AgenticDesignPatternsRU.

## Устранение проблем

| Проблема | Решение |
|---|---|
| `ОШИБКА: установите ANTHROPIC_API_KEY` | `echo $ANTHROPIC_API_KEY` - проверить, что ключ задан |
| Перевод обрезается | Уменьшить `CHUNK_SIZE_CHARS` до 25000 |
| Rate limit | Скрипт делает паузы автоматически; при ошибке подождать и перезапустить |
| Невалидный JSON в глоссарии | Скрипт остановится (fail-fast); проверить JSON-валидность |
| `ModuleNotFoundError: anthropic` | `pip install anthropic` или `pip3 install anthropic` |

## Структура проекта

```
md-translate-ru/
  translate_api.py          # основной скрипт
  requirements.txt          # зависимости
  LICENSE                   # MIT
  CHANGELOG.md              # история версий
  CONTRIBUTING.md           # как внести вклад
  examples/
    glossary.json           # пример глоссария
    glossary_candidates.json
    docs_en/
      01_Introduction.md    # тестовый документ
```

## Требования

- Python 3.10+
- Пакет [anthropic](https://pypi.org/project/anthropic/) >= 0.40.0
- API-ключ Anthropic с балансом

## Лицензия

[MIT](LICENSE)

---

Вопросы и предложения - через [Issues](https://github.com/ais-cube/md-translate-ru/issues).
