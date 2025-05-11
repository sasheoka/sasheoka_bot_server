# bot.py
import discord
from discord.ext import commands
import os
import asyncio
import logging
import aiohttp
from dotenv import load_dotenv
# --- НОВЫЙ ИМПОРТ ---
from utils.snag_api_client import SnagApiClient

# --- Настройка логирования (без изменений) ---
log_level = logging.INFO; handler = logging.FileHandler(filename='discord_bot.log', encoding='utf-8', mode='a'); # Используем mode='a'
formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'); handler.setFormatter(formatter)
logging.basicConfig(level=log_level, handlers=[handler, logging.StreamHandler()]); logger = logging.getLogger('discord_bot')

# --- Загрузка конфигурации (без изменений) ---
load_dotenv(); DISCORD_TOKEN = os.getenv('DISCORD_TOKEN'); SNAG_API_KEY = os.getenv('SNAG_API_KEY')
PROXY_URL = os.getenv('PROXY_URL')
# --- НОВЫЕ ПЕРЕМЕННЫЕ для клиента API ---
ORGANIZATION_ID = os.getenv('SNAG_ORGANIZATION_ID') # Предполагаем, что ID хранятся в .env
WEBSITE_ID = os.getenv('SNAG_WEBSITE_ID')

if DISCORD_TOKEN is None: logger.critical("CRITICAL ERROR: DISCORD_TOKEN not found in .env file."); exit()
if SNAG_API_KEY is None: logger.warning("SNAG_API_KEY not found in .env file. API features may not work.")
if ORGANIZATION_ID is None or WEBSITE_ID is None: logger.warning("SNAG_ORGANIZATION_ID or SNAG_WEBSITE_ID not found in .env. API features may not work.")


# --- Настройка намерений (без изменений) ---
intents = discord.Intents.default(); intents.message_content = True; intents.members = True; intents.voice_states = True # Добавляем voice_states для Stage Tracker

# --- Настройка бота (без изменений) ---
bot_options = {'command_prefix': '!', 'intents': intents, 'help_command': commands.DefaultHelpCommand(no_category = 'Команды')}
if PROXY_URL: logger.info(f"Proxy URL detected: {PROXY_URL.split('@')[-1]}"); bot_options['proxy'] = PROXY_URL
else: logger.info("No proxy URL detected. Connecting directly.")
bot = commands.Bot(**bot_options)

# --- Передаем ключ боту (для информации, клиент будет использовать свой) ---
bot.snag_api_key = SNAG_API_KEY # Оставим для обратной совместимости или отображения

# --- Событие готовности бота (без изменений) ---
@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})'); logger.info(f'discord.py version: {discord.__version__}')
    logger.info('Bot is ready.'); await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="за документацией"))
    if bot.snag_api_key: logger.info("SNAG_API_KEY found.")
    else: logger.warning("SNAG_API_KEY not found in .env file.")
    # --- Сообщение о наличии клиента API ---
    if hasattr(bot, 'snag_client'): logger.info("SnagApiClient initialized and attached to bot.")
    else: logger.warning("SnagApiClient failed to initialize or attach to bot.")


# --- Событие обработки ошибок команд (без изменений) ---
@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound): return
    elif isinstance(error, commands.MissingRequiredArgument): await ctx.send(f"Error: Missing argument `{error.param.name}`.\nUse `!help {ctx.command.qualified_name}` for help.")
    elif isinstance(error, commands.MissingRole): await ctx.send(f"⛔ You lack the required role to use this command.")
    elif isinstance(error, commands.ChannelNotFound): await ctx.send(f"⚠️ Could not find channel: {error.argument}")
    elif isinstance(error, commands.BadArgument): await ctx.send(f"⚠️ Invalid argument provided.")
    else: logger.error(f'Unhandled error in command {ctx.command}: {error}', exc_info=True); await ctx.send("An unexpected error occurred.")

# --- Функция для загрузки когов (без изменений) ---
async def load_extensions(bot_instance: commands.Bot):
    logger.info("Loading extensions (cogs)..."); loaded_count = 0; failed_count = 0; cog_dir = './cogs'
    if not os.path.exists(cog_dir): logger.warning(f"Cogs directory '{cog_dir}' not found."); return
    # --- ИЗМЕНЕНИЕ: Определяем порядок загрузки, если нужно ---
    # Сначала загружаем коги с зависимостями (например, ControlPanelCog, если другие от него зависят)
    # Затем остальные. Можно сделать список или загружать по очереди.
    # Пока оставим как есть, но помним о возможной зависимости.
    for filename in os.listdir(cog_dir):
        if filename.endswith('.py') and filename != '__init__.py':
            extension_name = f'cogs.{filename[:-3]}';
            try: await bot_instance.load_extension(extension_name); logger.info(f'  [+] Loaded: {extension_name}'); loaded_count += 1
            except commands.ExtensionError as e: logger.error(f'  [!] Failed to load {extension_name}: {e.__class__.__name__} - {e}', exc_info=True); failed_count += 1
            except Exception as e: logger.error(f'  [!] Critical error loading {extension_name}: {e}', exc_info=True); failed_count += 1
    logger.info(f"Cogs loading finished. Success: {loaded_count}, Failed: {failed_count}.")


# --- Основная асинхронная функция запуска ---
async def main():
    # --- ИЗМЕНЕНИЕ: Создаем сессию и клиент API здесь ---
    async with aiohttp.ClientSession() as session:
        # Создаем клиент API, передавая сессию и учетные данные
        bot.snag_client = SnagApiClient(session, SNAG_API_KEY, ORGANIZATION_ID, WEBSITE_ID)

        # Запускаем бота с созданным клиентом
        async with bot:
            await load_extensions(bot)
            logger.info("Starting bot...")
            await bot.start(DISCORD_TOKEN)

# --- Точка входа скрипта (без изменений) ---
if __name__ == "__main__":
    try: import aiohttp_socks; logger.info("aiohttp-socks library found.")
    except ImportError:
        if PROXY_URL and PROXY_URL.startswith('socks'): logger.warning("SOCKS PROXY_URL detected, but aiohttp-socks is not installed. Run: pip install aiohttp-socks")
    try: asyncio.run(main())
    except discord.LoginFailure: logger.critical("CRITICAL ERROR: Invalid Discord token. Check .env.")
    except KeyboardInterrupt: logger.info("Bot stopped manually (KeyboardInterrupt).")
    except Exception as e:
        logger.critical(f"CRITICAL ERROR during bot startup or runtime: {e}", exc_info=True)
        if PROXY_URL and isinstance(e, (aiohttp.ClientConnectorError, aiohttp.ClientHttpProxyError, aiohttp_socks.errors.ProxyConnectionError if 'aiohttp_socks' in locals() else OSError)):
             logger.critical(f"Potential proxy issue ({PROXY_URL.split('@')[-1]}). Check proxy availability/settings.")