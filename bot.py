# bot.py
import discord
from discord.ext import commands
import os
import asyncio
import logging
import aiohttp # Убедитесь, что этот импорт есть
from dotenv import load_dotenv
from utils.snag_api_client import SnagApiClient # Убедитесь, что этот импорт правильный

# --- Настройка логирования ---
log_level = logging.INFO
handler = logging.FileHandler(filename='discord_bot.log', encoding='utf-8', mode='a')
formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s')
handler.setFormatter(formatter)
logging.basicConfig(level=log_level, handlers=[handler, logging.StreamHandler()])
logger = logging.getLogger('discord_bot')

# --- Загрузка конфигурации ---
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
PROXY_URL = os.getenv('PROXY_URL')
ADMIN_GUILD_ID_STR = os.getenv('ADMIN_GUILD_ID') # <--- ДОБАВЛЕНО

# Основной (НОВЫЙ) Snag API
MAIN_SNAG_API_KEY = os.getenv('NEW_SNAG_API_KEY')
MAIN_ORGANIZATION_ID = os.getenv('NEW_SNAG_ORGANIZATION_ID')
MAIN_WEBSITE_ID = os.getenv('NEW_SNAG_WEBSITE_ID')

# Устаревший (СТАРЫЙ) Snag API
LEGACY_SNAG_API_KEY = os.getenv('OLD_SNAG_API_KEY')
LEGACY_ORGANIZATION_ID = os.getenv('OLD_SNAG_ORGANIZATION_ID')
LEGACY_WEBSITE_ID = os.getenv('OLD_SNAG_WEBSITE_ID')

# Проверки токенов
if DISCORD_TOKEN is None:
    logger.critical("CRITICAL ERROR: DISCORD_TOKEN not found in .env file. Bot cannot start.")
    exit()

# <--- НАЧАЛО ИЗМЕНЕНИЙ В ПРОВЕРКАХ ---
if not ADMIN_GUILD_ID_STR or not ADMIN_GUILD_ID_STR.isdigit():
    logger.critical("CRITICAL ERROR: ADMIN_GUILD_ID is not set or is not a valid number in .env. Admin commands will be insecure.")
else:
    logger.info(f"Admin commands will be restricted to Guild ID: {ADMIN_GUILD_ID_STR}")
# <--- КОНЕЦ ИЗМЕНЕНИЙ В ПРОВЕРКАХ ---

if not all([MAIN_SNAG_API_KEY, MAIN_ORGANIZATION_ID, MAIN_WEBSITE_ID]):
    logger.warning("MAIN Snag API credentials (NEW_SNAG_API_KEY, NEW_SNAG_ORGANIZATION_ID, NEW_SNAG_WEBSITE_ID) are not fully set in .env. Main API features may fail.")
else:
    logger.info("Main Snag API credentials loaded.")

if not all([LEGACY_SNAG_API_KEY, LEGACY_ORGANIZATION_ID, LEGACY_WEBSITE_ID]):
    logger.warning("LEGACY Snag API credentials (OLD_SNAG_API_KEY, OLD_SNAG_ORGANIZATION_ID, OLD_SNAG_WEBSITE_ID) are not fully set in .env. Legacy API features will fail.")
else:
    logger.info("Legacy Snag API credentials loaded.")


# --- Настройка намерений ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True # Для Stage Tracker

# --- Настройка бота ---
bot_options = {
    'command_prefix': '!',
    'intents': intents,
    'help_command': commands.DefaultHelpCommand(no_category = 'Команды')
}
if PROXY_URL:
    logger.info(f"Proxy URL detected: {PROXY_URL.split('@')[-1]}") # Показываем только хост:порт
    bot_options['proxy'] = PROXY_URL
else:
    logger.info("No proxy URL detected. Connecting directly.")
bot = commands.Bot(**bot_options)


# --- ГЛОБАЛЬНЫЕ ЛОГЕРЫ ВЗАИМОДЕЙСТВИЙ ---

@bot.before_invoke
async def log_prefix_command_usage(ctx: commands.Context):
    """
    Вызывается перед каждой успешной префикс-командой.
    """
    guild_info = f"Guild: {ctx.guild.name} ({ctx.guild.id})" if ctx.guild else "Direct Message"
    channel_info = f"Channel: #{ctx.channel.name} ({ctx.channel.id})" if ctx.channel and hasattr(ctx.channel, 'name') else "DM Channel"

    logger.info(
        f"[PREFIX_CMD_USAGE] User: {ctx.author.name} ({ctx.author.id}) | "
        f"Command: {ctx.command.qualified_name} | "
        f"Message: \"{ctx.message.content}\" | "
        f"Location: {guild_info} | {channel_info}"
    )

@bot.tree.interaction_check
async def log_slash_command_usage(interaction: discord.Interaction) -> bool:
    """
    Глобальная проверка, которая логирует каждое использование слэш-команды.
    Возвращает True, чтобы разрешить выполнение команды.
    """
    command_name = "N/A"
    if interaction.command:
        command_name = interaction.command.qualified_name

    guild_info = f"Guild: {interaction.guild.name} ({interaction.guild.id})" if interaction.guild else "Direct Message"
    channel_info = f"Channel: #{interaction.channel.name} ({interaction.channel.id})" if interaction.channel and hasattr(interaction.channel, 'name') else "DM Channel"

    logger.info(
        f"[SLASH_CMD_USAGE] User: {interaction.user.name} ({interaction.user.id}) | "
        f"Command: /{command_name} | "
        f"Location: {guild_info} | {channel_info}"
    )
    
    # Возвращаем True, чтобы команда могла выполниться дальше.
    return True


# --- Событие готовности бота ---
@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    logger.info(f'discord.py version: {discord.__version__}')
    logger.info('Bot is ready.')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="за API Snag"))

    if hasattr(bot, 'snag_client') and bot.snag_client._api_key:
        logger.info("Main SnagApiClient initialized and attached to bot.")
    else:
        logger.warning("Main SnagApiClient FAILED to initialize or is missing API key.")

    if hasattr(bot, 'snag_client_legacy') and bot.snag_client_legacy._api_key:
        logger.info("Legacy SnagApiClient initialized and attached to bot.")
    else:
        logger.warning("Legacy SnagApiClient FAILED to initialize or is missing API key.")

    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        logger.exception(f"Failed to sync slash commands: {e}")

# --- Событие обработки ошибок команд ---
@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return # Игнорируем неизвестные команды
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Ошибка: Пропущен аргумент `{error.param.name}`.\nИспользуйте `!help {ctx.command.qualified_name}` для помощи.")
    elif isinstance(error, commands.MissingRole) or isinstance(error, commands.MissingAnyRole):
         await ctx.send(f"⛔ У вас нет необходимой роли для использования этой команды.")
    elif isinstance(error, commands.ChannelNotFound):
        await ctx.send(f"⚠️ Канал не найден: {error.argument}")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"⚠️ Предоставлен неверный аргумент.")
    elif isinstance(error, commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
        await ctx.send(f"⛔ У меня нет прав для выполнения этого действия. Проверьте права бота.")
        logger.error(f'Permission error in command {ctx.command}: {error.original}', exc_info=True)
    else:
        logger.error(f'Unhandled error in command {ctx.command}: {error}', exc_info=True)
        await ctx.send("Произошла непредвиденная ошибка. Информация записана в лог.")

# --- Функция для загрузки когов ---
async def load_extensions(bot_instance: commands.Bot):
    logger.info("Loading extensions (cogs)...")
    loaded_count = 0
    failed_count = 0
    cog_dir = './cogs'
    if not os.path.exists(cog_dir):
        logger.warning(f"Cogs directory '{cog_dir}' not found.")
        return

    for filename in os.listdir(cog_dir):
        if filename.endswith('.py') and filename != '__init__.py':
            extension_name = f'cogs.{filename[:-3]}'
            try:
                await bot_instance.load_extension(extension_name)
                logger.info(f'  [+] Loaded: {extension_name}')
                loaded_count += 1
            except commands.ExtensionError as e:
                logger.error(f'  [!] Failed to load {extension_name}: {e.__class__.__name__} - {e}', exc_info=True)
                failed_count += 1
            except Exception as e: # Ловим другие возможные ошибки при загрузке
                logger.error(f'  [!] Critical error loading {extension_name}: {e}', exc_info=True)
                failed_count += 1
    logger.info(f"Cogs loading finished. Success: {loaded_count}, Failed: {failed_count}.")


# --- Основная асинхронная функция запуска ---
async def main():
    # Создаем сессию aiohttp один раз
    async with aiohttp.ClientSession() as session:
        # Инициализируем основной Snag API клиент (для нового API)
        bot.snag_client = SnagApiClient(
            session,
            MAIN_SNAG_API_KEY,
            MAIN_ORGANIZATION_ID,
            MAIN_WEBSITE_ID,
            client_name="MainSnagClient" # Имя для логов
        )
        # Инициализируем устаревший Snag API клиент (для старого API)
        bot.snag_client_legacy = SnagApiClient(
            session,
            LEGACY_SNAG_API_KEY,
            LEGACY_ORGANIZATION_ID,
            LEGACY_WEBSITE_ID,
            client_name="LegacySnagClient" # Имя для логов
        )

        # Запускаем бота с созданными клиентами
        async with bot:
            await load_extensions(bot)
            logger.info("Starting bot...")
            await bot.start(DISCORD_TOKEN)

# --- Точка входа скрипта ---
if __name__ == "__main__":
    # Проверка наличия aiohttp-socks (если прокси используется)
    aiohttp_socks_imported = False
    try:
        import aiohttp_socks
        aiohttp_socks_imported = True
        logger.info("aiohttp-socks library found.")
    except ImportError:
        if PROXY_URL and PROXY_URL.lower().startswith('socks'):
            logger.warning("SOCKS PROXY_URL detected, but aiohttp-socks is not installed. Run: pip install aiohttp-socks")

    try:
        asyncio.run(main())
    except discord.LoginFailure:
        logger.critical("CRITICAL ERROR: Invalid Discord token. Check .env.")
    except KeyboardInterrupt:
        logger.info("Bot stopped manually (KeyboardInterrupt).")
    except Exception as e:
        logger.critical(f"CRITICAL ERROR during bot startup or runtime: {e}", exc_info=True)
        # Более детальная диагностика ошибки прокси
        if PROXY_URL:
            is_proxy_error = False
            if isinstance(e, (aiohttp.ClientConnectorError, aiohttp.ClientHttpProxyError)):
                is_proxy_error = True
            elif aiohttp_socks_imported and isinstance(e, aiohttp_socks.errors.ProxyConnectionError):
                is_proxy_error = True
            
            if is_proxy_error:
                 logger.critical(f"Potential proxy issue with {PROXY_URL.split('@')[-1]}. Check proxy availability/settings and ensure aiohttp-socks is installed if using SOCKS.")