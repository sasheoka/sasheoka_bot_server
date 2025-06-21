# cogs/quest_completer_cog.py
import discord
from discord.ext import commands
import logging
import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# --- Модальное окно для ввода данных ---
class QuestCompleteModal(discord.ui.Modal, title="Manually Complete a Quest"):
    wallet_address_input = discord.ui.TextInput(
        label="Target Wallet Address",
        placeholder="0x...",
        required=True, style=discord.TextStyle.short, min_length=42, max_length=42
    )
    rule_id_input = discord.ui.TextInput(
        label="Quest ID (Loyalty Rule ID)",
        placeholder="Enter the unique ID of the quest...",
        required=True, style=discord.TextStyle.short, min_length=10, max_length=50
    )

    def __init__(self, cog_instance: "QuestCompleterCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        wallet_address = self.wallet_address_input.value.strip().lower()
        rule_id = self.rule_id_input.value.strip()

        # Простая валидация
        if not (wallet_address.startswith("0x") and len(wallet_address) == 42):
            await interaction.followup.send("⚠️ Invalid wallet address format.", ephemeral=True)
            return

        if not rule_id:
            await interaction.followup.send("⚠️ Quest ID cannot be empty.", ephemeral=True)
            return

        await self.cog.process_quest_completion(interaction, wallet_address, rule_id)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in QuestCompleteModal: {error}", exc_info=True)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("An error occurred in the modal.", ephemeral=True)
            else:
                await interaction.response.send_message("An error occurred in the modal.", ephemeral=True)
        except discord.HTTPException:
            pass


# --- Класс Кога ---
class QuestCompleterCog(commands.Cog, name="Quest Completer"):
    """Ког для ручного завершения квестов для указанного кошелька."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snag_client = getattr(bot, 'snag_client', None)
        if not self.snag_client:
            logger.error(f"{self.__class__.__name__}: Main SnagApiClient (bot.snag_client) not found! Quest completion will fail.")
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    async def cog_load(self):
        logger.info(f"Cog '{self.__class__.__name__}' successfully initialized by bot.")

    async def process_quest_completion(self, interaction: discord.Interaction, wallet_address: str, rule_id: str):
        if not self.snag_client or not self.snag_client._api_key:
            await interaction.followup.send("⚠️ Main Snag API client is not configured. Cannot proceed.", ephemeral=True)
            return
            
        logger.info(f"User {interaction.user.name} ({interaction.user.id}) is attempting to complete quest `{rule_id}` for wallet `{wallet_address}`.")

        # --- ИЗМЕНЕНИЕ: Убираем idempotencyKey ---
        # API не разрешает передавать этот ключ для внутренних правил, таких как подписка на твиттер.
        payload = {
            "walletAddress": wallet_address
        }
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        logger.debug(f"Calling complete_loyalty_rule with rule_id: {rule_id}, payload: {payload}")
        
        response = await self.snag_client.complete_loyalty_rule(rule_id, payload)

        if response and not response.get("error"):
            api_message = response.get("message", "Success (no specific message).")
            success_msg = (
                f"✅ **Quest Completion Request Sent!**\n\n"
                f"**Wallet:** `{wallet_address}`\n"
                f"**Quest ID:** `{rule_id}`\n"
                f"**API Response:** `{api_message}`\n\n"
                f"The points should be credited shortly if the user was eligible."
            )
            await interaction.followup.send(success_msg, ephemeral=True)
            logger.info(f"Quest completion successful for rule {rule_id}, wallet {wallet_address}. API Response: {response}")
        else:
            error_message = response.get("message", "Unknown error.") if response else "No response from API."
            raw_response_snippet = str(response)[:500] if response else "N/A"
            failure_msg = (
                f"❌ **Failed to complete quest.**\n\n"
                f"**Wallet:** `{wallet_address}`\n"
                f"**Quest ID:** `{rule_id}`\n"
                f"**Reason:** `{error_message}`\n\n"
                f"*Full API response (truncated):*\n```json\n{raw_response_snippet}\n```"
            )
            await interaction.followup.send(failure_msg, ephemeral=True)
            logger.error(f"Failed to complete quest {rule_id} for wallet {wallet_address}. Response: {response}")

async def setup(bot: commands.Bot):
    if not getattr(bot, 'snag_client', None) or not getattr(bot.snag_client, '_api_key', None):
        logger.error("CRITICAL: Main Snag API client missing or no API key. QuestCompleterCog will NOT be loaded.")
        return
    if not hasattr(bot.snag_client, 'complete_loyalty_rule'):
        logger.error("CRITICAL: SnagApiClient is missing 'complete_loyalty_rule' method. QuestCompleterCog will NOT be loaded.")
        return

    await bot.add_cog(QuestCompleterCog(bot))