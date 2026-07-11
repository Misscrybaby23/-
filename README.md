# Raiffeisen DSJ Quiz Bot

Профессиональный Telegram-бот для викторины по ДСЖ.

## Возможности

- 15 вопросов с кнопками ответов
- без ограничения времени на вопрос
- общее время прохождения
- рейтинг по баллам и скорости
- баннер и главное меню
- повторное прохождение
- несколько участников одновременно
- SQLite для результатов
- health endpoint для Render

## Локальный запуск

1. Установите Python 3.11+
2. Выполните:
   `pip install -r requirements.txt`
3. Укажите переменную окружения:
   `BOT_TOKEN=новый_токен_бота`
4. Запустите:
   `python main.py`

## Развёртывание на Render

1. Загрузите все файлы проекта в корень GitHub-репозитория.
2. В Render создайте **New Web Service** из этого репозитория.
3. Build Command:
   `pip install -r requirements.txt`
4. Start Command:
   `python main.py`
5. Добавьте Environment Variable:
   - Key: `BOT_TOKEN`
   - Value: новый токен от BotFather
6. При необходимости добавьте:
   - Key: `ADMIN_ID`
   - Value: ваш цифровой Telegram ID
7. Нажмите Deploy.

## Важно о рейтинге

На бесплатном Render локальная SQLite-база может быть потеряна при пересоздании сервиса.
Для постоянного рейтинга лучше подключить PostgreSQL или платный Persistent Disk.
