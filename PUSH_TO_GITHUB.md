# Как залить обновление на GitHub

## Если репо уже склонирован

```bash
cd md-translate-ru

# Проверить статус
git status

# Добавить новые файлы
git add convert.py fonts/ requirements.txt .gitignore README.md CHANGELOG.md CONTRIBUTING.md

# Коммит
git commit -m "feat: multi-format output (PDF+HTML), bundled fonts with Cyrillic support"

# Отправить
git push origin main
```

## Если начинаешь с нуля

```bash
git clone https://github.com/ais-cube/md-translate-ru.git
cd md-translate-ru

# Скопировать обновленные файлы сюда (convert.py, fonts/, и т.д.)
# ...

git add -A
git commit -m "feat: multi-format output (PDF+HTML), bundled fonts with Cyrillic support"
git push origin main
```

## Что изменилось в v1.3.0

- `convert.py` - новый скрипт для генерации PDF + HTML
- `fonts/` - 10 шрифтов с полной кириллицей (DejaVu Sans, Liberation Sans)
- `requirements.txt` - добавлены fpdf2, markdown, pymdown-extensions
- `.gitignore` - output/, docs_ru/
- `README.md` - документация convert.py, структура, troubleshooting
- `CHANGELOG.md` - v1.3.0
