# Discord Documentation Bot

Этот бот предназначен для взаимодействия с API документации ([Название вашего сайта/сервиса] - *не забудьте указать*) через Discord.

## Настройка

1.  **Клонируйте репозиторий:**
    ```bash
    git clone <URL вашего репозитория>
    cd discord_bot
    ```
2.  **Создайте виртуальное окружение (рекомендуется):**
    ```bash
    # Убедитесь, что python или py (для Windows) добавлен в PATH
    py -m venv venv
    # или
    python -m venv venv
    ```
    **Активируйте окружение:**
    ```bash
    # Windows (cmd/powershell)
    .\venv\Scripts\activate
    # MacOS/Linux (bash/zsh)
    source venv/bin/activate
    ```
3.  **Установите зависимости:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **Создайте файл `.env`:** Создайте файл с именем `.env` в корневой папке проекта (`discord_bot/`) и добавьте в него ваши токены:
    ```dotenv
    # .env
    DISCORD_TOKEN=YOUR_DISCORD_BOT_TOKEN_HERE
    SNAG_API_KEY=YOUR_SNAG_API_KEY_HERE
    ```
    *   Замените `YOUR_DISCORD_BOT_TOKEN_HERE` на токен вашего Discord бота.
    *   Замените `YOUR_SNAG_API_KEY_HERE` на ваш API ключ (SNAG API Key).
    *   **ВАЖНО:** Не добавляйте файл `.env` в систему контроля версий (он уже должен быть в `.gitignore`).

## Запуск бота
#
Убедитесь, что ваше виртуальное окружение активировано (вы должны видеть `(venv)` в начале командной строки).

```bash
python bot.py