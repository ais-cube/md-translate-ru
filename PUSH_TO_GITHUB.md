# Как выложить на GitHub

## Вариант А: через браузер + командную строку (рекомендуется)

### 1. Создать репозиторий на GitHub

1. Зайди на https://github.com/new
2. Repository name: `md-translate-ru`
3. Description: `Автоматический переводчик Markdown EN->RU через Claude API`
4. Выбери **Public**
5. **НЕ** ставь галочки на README, .gitignore, LICENSE (всё уже в архиве)
6. Нажми **Create repository**

### 2. Распаковать архив

```bash
# Распаковать
unzip md-translate-ru.zip
cd md-translate-ru
```

### 3. Инициализировать и запушить

```bash
git init
git add -A
git commit -m "Initial release: MD Translate RU v1.0.0"
git branch -M main
git remote add origin https://github.com/<YOUR_USERNAME>/md-translate-ru.git
git push -u origin main
```

Замени `<YOUR_USERNAME>` на свой логин GitHub (например, `densmirnov`).

### 4. Добавить тэг релиза

```bash
git tag -a v1.0.0 -m "v1.0.0: Initial release"
git push origin v1.0.0
```

### 5. Создать Release на GitHub (опционально)

1. Зайди на `https://github.com/<YOUR_USERNAME>/md-translate-ru/releases/new`
2. Tag: `v1.0.0`
3. Title: `v1.0.0 - Initial Release`
4. Описание:

```
Первый релиз md-translate-ru.

- Автоматический перевод Markdown EN->RU через Anthropic Claude API
- Поддержка глоссариев
- Авто-разбивка больших файлов на чанки
- Anti-AI cleanup
- Batch API (в 2 раза дешевле)
- Dry-run для оценки стоимости

Стоимость перевода книги на 145 страниц: ~$6.56 (Sonnet) / ~$3.28 (Batch)
```

5. Нажми **Publish release**

---

## Вариант Б: через GitHub CLI (если установлен `gh`)

```bash
unzip md-translate-ru.zip
cd md-translate-ru
git init && git add -A && git commit -m "Initial release: MD Translate RU v1.0.0"
git branch -M main

gh repo create md-translate-ru --public --description "Автоматический переводчик Markdown EN->RU через Claude API" --source=. --push

git tag -a v1.0.0 -m "v1.0.0: Initial release"
git push origin v1.0.0
gh release create v1.0.0 --title "v1.0.0 - Initial Release" --notes "Первый релиз"
```

---

## После публикации

Добавь Topics на странице репо (Settings -> General -> Topics):

```
markdown, translation, russian, anthropic, claude, api, nlp, localization
```
