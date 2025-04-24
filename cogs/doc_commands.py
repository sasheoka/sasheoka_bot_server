# cogs/doc_commands.py
import discord
from discord.ext import commands
import logging

logger = logging.getLogger(__name__) # Логгер для этого кога

class DocCommands(commands.Cog, name="Основные"): # Имя для !help
    """
    Ког с основными или общими командами бота.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info(f"Ког '{self.__class__.__name__}' загружен.")

    # Команда ping остается здесь (или удалите, если не нужна)
    @commands.command(name='ping')
    async def ping(self, ctx: commands.Context):
        """Проверяет отклик бота и задержку Discord API."""
        latency = round(self.bot.latency * 1000)
        await ctx.send(f'Pong! 🏓 Задержка API: {latency}ms')

    # Здесь можно будет добавлять другие команды, не связанные со Snag API

# --- Обязательная функция для загрузки этого кога ---
async def setup(bot: commands.Bot):
    await bot.add_cog(DocCommands(bot))