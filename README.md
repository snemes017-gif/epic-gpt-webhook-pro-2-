
# Epic GPT Webhook Pro

Готовый сервер для студии **Эпичные**: маршрутизация ролей, поддержка вложений, выгрузка файлов в Google Drive, автоочистка, CSV-логирование.

## Переменные окружения
- `OPENAI_API_KEY` — ключ OpenAI
- `GOOGLE_SERVICE_JSON` — JSON сервисного аккаунта Google (весь текст)
- `DRIVE_FOLDER_ID` — ID папки в Google Drive
- `UPLOAD_TTL_HOURS` — срок жизни временных файлов (по умолчанию 24)
- `OPENAI_MODEL` — модель (по умолчанию gpt-5)
- `LOG_TO_CSV` — 1/0

## Эндпоинты
- `GET /` — статус
- `POST /webhook`
```json
{
  "role": "analytic",
  "message": "Клиент: можно завтра?",
  "attachments": ["https://example.com/chat.png"]
}
```

## Ответ
```json
{
  "reply": "Есть слот завтра в 15:00. Подойдёт?",
  "role": "analytic",
  "attachments_uploaded": ["https://drive.google.com/..."]
}
```
