# bot.py (Модифицированный для загрузки когов и поддержки прокси)
import discord
from discord.ext import commands
import os
import asyncio
import logging
import aiohttp
from dotenv import load_dotenv

# --- Настройка логирования ---
log_level = logging.INFO
handler = logging.FileHandler(filename='discord_bot.log', encoding='utf-8', mode='w')
formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s')
handler.setFormatter(formatter)
logging.basicConfig(level=log_level, handlers=[handler, logging.StreamHandler()])
logger = logging.getLogger('discord_bot')

# --- Загрузка конфигурации ---
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
SNAG_API_KEY = os.getenv('SNAG_API_KEY')
# --- ИЗМЕНЕНИЕ: Загрузка URL прокси ---
PROXY_URL = os.getenv('PROXY_URL')

if DISCORD_TOKEN is None:
    logger.critical("КРИТИЧЕСКАЯ ОШИБКА: Токен Discord (DISCORD_TOKEN) не найден в .env файле.")
    exit()

# --- Настройка намерений (Intents) ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# --- ИЗМЕНЕНИЕ: Создание экземпляра бота с прокси ---
bot_options = {
    'command_prefix': '!',
    'intents': intents,
    'help_command': commands.DefaultHelpCommand(no_category = 'Команды')
}
# Если PROXY_URL задан в .env, добавляем его в опции
if PROXY_URL:
    logger.info(f"Обнаружен PROXY_URL в .env. Бот будет использовать прокси: {PROXY_URL.split('@')[-1]}") # Логируем без пароля
    bot_options['proxy'] = PROXY_URL
    # Для SOCKS может потребоваться аутентификация через proxy_auth,
    # но discord.py обычно справляется с user:pass в URL.
    # Если возникнут проблемы с SOCKS auth, понадобится настройка aiohttp.BasicAuth.
else:
    logger.info("PROXY_URL не найден в .env. Бот будет подключаться напрямую.")

bot = commands.Bot(**bot_options) # Передаем словарь опций

# Сохраняем API ключ в объекте бота
bot.snag_api_key = SNAG_API_KEY

# --- Событие готовности бота ---
@bot.event
async def on_ready():
    logger.info(f'Бот залогинен как {bot.user.name} (ID: {bot.user.id})')
    logger.info(f'discord.py версия: {discord.__version__}')
    logger.info('Бот готов к работе.')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="за документацией"))
    if bot.snag_api_key:
        logger.info("SNAG_API_KEY найден и передан боту.")
    else:
        logger.warning("SNAG_API_KEY не найден в .env файле.")

# --- Событие обработки ошибок команд ---
@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    # ... (код обработки ошибок остается таким же, как в предыдущей версии) ...
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Ошибка: не хватает аргумента `{error.param.name}`.\n"
                     f"Используйте `!help {ctx.command.qualified_name}` для справки.")
    # ... (другие обработчики ошибок) ...
    else:
        logger.error(f'Необработанная ошибка в команде {ctx.command}: {error}', exc_info=True)
        await ctx.send("Произошла непредвиденная ошибка при выполнении команды. Администратор уведомлен.")


# --- Функция для загрузки когов ---
async def load_extensions(bot_instance: commands.Bot):
    # ... (код загрузки когов остается таким же) ...
    logger.info("Загрузка расширений (cogs)...")
    loaded_count = 0
    failed_count = 0
    cog_dir = './cogs'
    if not os.path.exists(cog_dir):
        logger.warning(f"Папка для когов '{cog_dir}' не найдена. Коги не будут загружены.")
        return
    for filename in os.listdir(cog_dir):
        if filename.endswith('.py') and filename != '__init__.py':
            extension_name = f'cogs.{filename[:-3]}'
            try:
                await bot_instance.load_extension(extension_name)
                logger.info(f'  [+] Успешно загружен: {extension_name}')
                loaded_count += 1
            except commands.ExtensionError as e:
                logger.error(f'  [!] Ошибка загрузки {extension_name}: {e.__class__.__name__} - {e}', exc_info=True)
                failed_count += 1
            except Exception as e:
                 logger.error(f'  [!] Неожиданная критическая ошибка при загрузке {extension_name}: {e}', exc_info=True)
                 failed_count += 1
    logger.info(f"Загрузка когов завершена. Успешно: {loaded_count}, Ошибки: {failed_count}.")


# --- Основная асинхронная функция запуска ---
async def main():
    async with bot:
        await load_extensions(bot)
        logger.info("Запуск бота...")
        await bot.start(DISCORD_TOKEN) # Токен передается здесь

# --- Точка входа скрипта ---
if __name__ == "__main__":
    # Если используете SOCKS и установили aiohttp-socks, можно добавить проверку:
    try:
        import aiohttp_socks
        logger.info("Библиотека aiohttp-socks найдена.")
    except ImportError:
        if PROXY_URL and PROXY_URL.startswith('socks'):
             logger.warning("Обнаружен SOCKS PROXY_URL, но библиотека aiohttp-socks не установлена. Установите: pip install aiohttp-socks")

    try:
        asyncio.run(main())
    except discord.LoginFailure:
        logger.critical("КРИТИЧЕСКАЯ ОШИБКА: Неверный токен Discord. Проверьте .env.")
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную (KeyboardInterrupt).")
    except Exception as e:
        # Логируем ошибку, которая могла возникнуть при подключении через прокси
        logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА при запуске или работе бота: {e}", exc_info=True)
        # Дополнительно проверяем на ошибки соединения, если использовался прокси
        if PROXY_URL and isinstance(e, (aiohttp.ClientConnectorError, aiohttp.ClientHttpProxyError, aiohttp_socks.errors.ProxyConnectionError if 'aiohttp_socks' in locals() else OSError)):
             logger.critical(f"Возможно, проблема связана с прокси-сервером ({PROXY_URL.split('@')[-1]}) или его доступностью/настройками.")