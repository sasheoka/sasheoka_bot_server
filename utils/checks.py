# utils/checks.py
import discord
from discord.ext import commands
from discord import app_commands
import os

# Загружаем ID нашего единственного доверенного сервера
ADMIN_GUILD_ID = int(os.getenv('ADMIN_GUILD_ID', 0))
try:
    RANGER_ROLE_ID = int(os.getenv('RANGER_ROLE_ID', 0))
except (ValueError, TypeError):
    RANGER_ROLE_ID = 0

# --- ПРОВЕРКА ДЛЯ СЛЭШ-КОМАНД (app_commands) ---

def is_admin_in_guild():
    """
    Кастомная проверка для слэш-команд, которая разрешает выполнение, только если:
    1. Команда вызвана на нашем официальном сервере (ADMIN_GUILD_ID).
    2. У пользователя есть роль с названием RANGER_ROLE_ID.
    """
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            raise app_commands.NoPrivateMessage()

        if interaction.guild.id != ADMIN_GUILD_ID:
            raise app_commands.CheckFailure("This command can only be used on the official server.")

        if not isinstance(interaction.user, discord.Member):
            return False

        if not any(role.id == RANGER_ROLE_ID for role in interaction.user.roles):
            raise app_commands.MissingRole(str(RANGER_ROLE_ID))

        return True
    
    return app_commands.check(predicate)


# --- ПРОВЕРКА ДЛЯ ПРЕФИКС-КОМАНД (commands.command) ---

def is_prefix_admin_in_guild():
    """
    Кастомная проверка для префикс-команд, которая разрешает выполнение, только если:
    1. Команда вызвана на нашем официальном сервере (ADMIN_GUILD_ID).
    2. У пользователя есть роль с названием RANGER_ROLE_NAME.
    """
    async def predicate(ctx: commands.Context) -> bool:
        if not ctx.guild:
            raise commands.NoPrivateMessage()

        if ctx.guild.id != ADMIN_GUILD_ID:
            raise commands.CheckFailure("This command can only be used on the official server.")

        if not isinstance(ctx.author, discord.Member):
            return False

        if not any(role.id == RANGER_ROLE_ID for role in ctx.author.roles):
            raise commands.MissingRole(str(RANGER_ROLE_ID))
            
        return True

    return commands.check(predicate)