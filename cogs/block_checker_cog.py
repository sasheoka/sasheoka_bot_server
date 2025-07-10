# cogs/block_checker_cog.py
import discord
from discord.ext import commands
import logging
from typing import Optional

from utils.snag_api_client import SnagApiClient, GET_USER_ENDPOINT
import re

logger = logging.getLogger(__name__)
EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")

# --- Модальное окно для ввода адреса ---
class BlockCheckModal(discord.ui.Modal, title="Check Wallet Block Status"):
    wallet_address_input = discord.ui.TextInput(
        label="Wallet Address (EVM)",
        placeholder="0x...",
        required=True,
        style=discord.TextStyle.short,
        min_length=42,
        max_length=42
    )

    def __init__(self, cog_instance: "BlockCheckerCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        wallet_address = self.wallet_address_input.value.strip().lower()

        if not EVM_ADDRESS_PATTERN.match(wallet_address):
            await interaction.followup.send("⚠️ Invalid EVM wallet address format. Please use `0x...`", ephemeral=True)
            return

        await self.cog.process_block_check(interaction, wallet_address)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in BlockCheckModal: {error}", exc_info=True)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("An error occurred in the modal.", ephemeral=True)
            else:
                await interaction.followup.send("An error occurred after submitting the modal.", ephemeral=True)
        except discord.HTTPException:
            pass

# --- Класс Кога ---
class BlockCheckerCog(commands.Cog, name="Block Checker"):
    """Ког для проверки статуса блокировки кошелька через Snag API."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snag_client: Optional[SnagApiClient] = getattr(bot, 'snag_client', None)
        if not self.snag_client:
            logger.error(f"{self.__class__.__name__}: Main SnagApiClient not found! Block checking will fail.")
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    async def cog_load(self):
        logger.info(f"Cog '{self.__class__.__name__}' successfully initialized by bot.")

    async def process_block_check(self, interaction: discord.Interaction, wallet_address: str):
        if not self.snag_client or not self.snag_client._api_key:
            await interaction.followup.send("⚠️ Main Snag API client is not configured. Cannot proceed.", ephemeral=True)
            return

        logger.info(f"User {interaction.user.name} ({interaction.user.id}) is checking block status for wallet: {wallet_address}")

        # Используем универсальный метод _make_request для эндпоинта /api/users
        response = await self.snag_client.get_user_data(wallet_address=wallet_address)

        if not response or response.get("error"):
            error_message = response.get("message", "API request failed.") if response else "No response from API."
            # Добавим статус, если он есть
            status = response.get("status", "N/A") if response else "N/A"
            await interaction.followup.send(f"❌ API Error (Status: {status}): `{error_message}`", ephemeral=True)
            return

        if not isinstance(response.get("data"), list) or not response["data"]:
            await interaction.followup.send(f"ℹ️ **NOT FOUND**\nNo user data found for wallet `{wallet_address}`.", ephemeral=True)
            return

        # Парсим ответ, как в вашем примере
        try:
            user_object = response["data"][0]
            user_metadata_list = user_object.get("userMetadata", [])
            
            if not isinstance(user_metadata_list, list) or not user_metadata_list:
                await interaction.followup.send(f"⚠️ **INCOMPLETE DATA**\nUser found, but metadata is missing for wallet `{wallet_address}`.", ephemeral=True)
                return

            metadata = user_metadata_list[0]
            is_blocked = metadata.get("isBlocked", False) # По умолчанию считаем, что не заблокирован, если поля нет

            if is_blocked:
                result_message = f"🔴 **BLOCKED**\nThe wallet `{wallet_address}` is marked as blocked."
            else:
                result_message = f"✅ **NOT BLOCKED**\nThe wallet `{wallet_address}` is not blocked."
            
            await interaction.followup.send(result_message, ephemeral=True)

        except (IndexError, KeyError, TypeError) as e:
            logger.error(f"Error parsing block check API response for {wallet_address}: {e}. Response: {response}", exc_info=True)
            await interaction.followup.send(f"⚙️ Could not parse the API response. The data structure might have changed. Please check the logs.", ephemeral=True)


async def setup(bot: commands.Bot):
    # Проверка, что основной Snag-клиент существует и настроен
    if not getattr(bot, 'snag_client', None) or not getattr(bot.snag_client, '_api_key', None):
        logger.error("CRITICAL: Main Snag API client is missing or has no key. BlockCheckerCog will NOT be loaded.")
        return

    await bot.add_cog(BlockCheckerCog(bot))