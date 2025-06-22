# cogs/balance_adjustment_cog.py
import discord
from discord.ext import commands
import logging
import datetime
import os
import asyncio
import io
from typing import Dict, Optional
from decimal import Decimal, InvalidOperation
from utils.checks import is_prefix_admin_in_guild 

logger = logging.getLogger(__name__)

MATCHSTICKS_CURRENCY_ID = os.getenv("MATCHSTICKS_CURRENCY_ID")
BOT_SIGNATURE = os.getenv("BOT_SIGNATURE", "sb")

# --- Модальное Окно для Корректировки Баланса ---
class AdjustBalanceModal(discord.ui.Modal, title="Adjust Wallet Balance"):
    wallet_address_input = discord.ui.TextInput(
        label="Wallet Address (EVM)",
        placeholder="0x...",
        required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0
    )
    add_amount_input = discord.ui.TextInput( 
        label="Amount to Add (e.g., 100)",
        placeholder="Enter a positive number or leave empty",
        required=False, style=discord.TextStyle.short, row=1 
    )
    subtract_amount_input = discord.ui.TextInput( 
        label="Amount to Subtract (e.g., 50)",
        placeholder="Enter a positive number or leave empty",
        required=False, style=discord.TextStyle.short, row=2
    )
    reason_input = discord.ui.TextInput(
        label="Reason (Optional) — visible in history!",
        placeholder="E.g., Manual correction, event reward, etc.",
        required=False, style=discord.TextStyle.paragraph, max_length=200, row=3 # Увеличена строка для причины
    )

    def __init__(self, cog_instance: "BalanceAdjustmentCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        wallet_address = self.wallet_address_input.value.strip().lower()
        add_amount_str = self.add_amount_input.value.strip()
        subtract_amount_str = self.subtract_amount_input.value.strip()
        reason = self.reason_input.value.strip() 

        if not (wallet_address.startswith("0x") and len(wallet_address) == 42):
            await interaction.followup.send("⚠️ Invalid wallet address format.", ephemeral=True)
            return

        if add_amount_str and subtract_amount_str:
            await interaction.followup.send("⚠️ Please fill EITHER 'Amount to Add' OR 'Amount to Subtract', not both.", ephemeral=True)
            return
        if not add_amount_str and not subtract_amount_str:
            await interaction.followup.send("⚠️ Please specify an amount to add or subtract.", ephemeral=True)
            return

        amount_decimal = Decimal('0')
        direction = ""

        if add_amount_str:
            try:
                parsed_amount = Decimal(add_amount_str)
                if parsed_amount <= Decimal('0'):
                    await interaction.followup.send("⚠️ 'Amount to Add' must be a positive number.", ephemeral=True)
                    return
                amount_decimal = parsed_amount
                direction = "credit"
            except InvalidOperation:
                await interaction.followup.send("⚠️ Invalid 'Amount to Add'. Please enter a valid number.", ephemeral=True)
                return
        elif subtract_amount_str:
            try:
                parsed_amount = Decimal(subtract_amount_str)
                if parsed_amount <= Decimal('0'):
                    await interaction.followup.send("⚠️ 'Amount to Subtract' must be a positive number.", ephemeral=True)
                    return
                amount_decimal = parsed_amount
                direction = "debit"
            except InvalidOperation:
                await interaction.followup.send("⚠️ Invalid 'Amount to Subtract'. Please enter a valid number.", ephemeral=True)
                return
        
        # final_reason_for_payload теперь не используется напрямую для главного описания, но передается
        await self.cog.process_balance_adjustment(interaction, wallet_address, amount_decimal, direction, reason) # Передаем 'reason' как есть

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in AdjustBalanceModal: {error}", exc_info=True)
        try:
            if interaction.response.is_done(): await interaction.followup.send("An error occurred in the modal.", ephemeral=True)
            else: await interaction.response.send_message("An error occurred in the modal.", ephemeral=True)
        except discord.HTTPException: pass

# --- View для Панели Управления Корректировкой Баланса ---
class BalanceAdjustmentPanelView(discord.ui.View):
    def __init__(self, cog_instance: "BalanceAdjustmentCog"):
        super().__init__(timeout=None) 
        self.cog = cog_instance

    async def _check_ranger_role(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
             await interaction.response.send_message("This command can only be used on a server.", ephemeral=True); return False
        ranger_role = discord.utils.get(interaction.guild.roles, name="Ranger")
        if not ranger_role:
            await interaction.response.send_message("⛔ The 'Ranger' role was not found on this server.", ephemeral=True); return False
        if ranger_role not in interaction.user.roles:
            await interaction.response.send_message("⛔ You do not have the required 'Ranger' role to use this button.", ephemeral=True); return False
        return True

    @discord.ui.button(label="⚙️ Adjust Wallet Balance", style=discord.ButtonStyle.danger, custom_id="baladj:open_modal_v1")
    async def adjust_balance_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction):
            return
        
        modal = AdjustBalanceModal(self.cog)
        await interaction.response.send_modal(modal)

# --- Класс Кога ---
class BalanceAdjustmentCog(commands.Cog, name="Balance Adjustments"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snag_client = getattr(bot, 'snag_client', None)
        if not self.snag_client:
            logger.error(f"{self.__class__.__name__}: Main SnagApiClient (bot.snag_client) not found! Balance adjustments will not work.")
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    async def cog_load(self):
        logger.info(f"Cog '{self.__class__.__name__}' successfully initialized by bot.")
        if not self.snag_client or not self.snag_client._api_key:
            logger.error(f"{self.__class__.__name__}: Main Snag API client is not available or API key is missing. Adjustment features will fail.")

    async def process_balance_adjustment(self, interaction: discord.Interaction, wallet_address: str, abs_amount: Decimal, direction: str, reason_from_modal: str):
        if not self.snag_client or not self.snag_client._api_key:
            await interaction.followup.send("⚠️ Main Snag API client is not configured. Cannot proceed.", ephemeral=True)
            return

        # Формируем основное описание, которое ПОЙДЕТ В UI (верхнеуровневый description)
        if not reason_from_modal: # Если причина пустая
            main_api_description = f"Administrative balance adjustment ({BOT_SIGNATURE})"
        else:
            main_api_description = f"{reason_from_modal} ({BOT_SIGNATURE})"
        
        if len(main_api_description) > 250: 
            main_api_description = main_api_description[:247] + "..."
            
        # Описание для конкретной entry (может быть более техническим или не использоваться UI)
        entry_specific_description = f"{direction.capitalize()} {abs_amount} {MATCHSTICKS_CURRENCY_ID[:8]} for {reason_from_modal if reason_from_modal else 'Admin Adjustment'}"
        if len(entry_specific_description) > 250:
            entry_specific_description = entry_specific_description[:247] + "..."

        single_entry_data = {
            "walletAddress": wallet_address,
            "amount": float(abs_amount) if '.' in str(abs_amount) else int(abs_amount),
            "loyaltyCurrencyId": MATCHSTICKS_CURRENCY_ID,
            "direction": direction,
            "description": entry_specific_description, # Описание для самой entry
            "externalId": f"bal_adj_{wallet_address[2:9]}_{direction}_{int(datetime.datetime.utcnow().timestamp())}"
        }

        final_payload_for_api = {
            "entries": [single_entry_data],
            "description": main_api_description # ЭТО ОПИСАНИЕ ОТОБРАЖАЕТСЯ В UI HISTORY
        }
        
        if self.snag_client._organization_id:
            final_payload_for_api['organizationId'] = self.snag_client._organization_id
        if self.snag_client._website_id:
            final_payload_for_api['websiteId'] = self.snag_client._website_id

        try:
            logger.info(f"Attempting balance adjustment for {wallet_address}: {direction} {abs_amount} Matchsticks. Reason from modal: '{reason_from_modal}'. API Payload: {final_payload_for_api}")
            response = await self.snag_client.create_transaction(tx_data=final_payload_for_api)

            is_successful_creation = False
            new_tx_batch_id = "N/A"
            if response and not response.get("error") and response.get("id") and isinstance(response.get("entries"), list):
                for entry in response.get("entries", []):
                    if entry.get("direction") == direction and \
                       entry.get("loyaltyAccount", {}).get("user", {}).get("walletAddress", "").lower() == wallet_address and \
                       Decimal(str(entry.get("amount", "0"))) == abs_amount and \
                       entry.get("loyaltyCurrencyId") == MATCHSTICKS_CURRENCY_ID:
                        is_successful_creation = True
                        new_tx_batch_id = response.get("id", "N/A")
                        break
            
            if is_successful_creation:
                display_reason_for_user = reason_from_modal if reason_from_modal else "Administrative adjustment"
                success_msg = (
                    f"✅ Successfully processed balance adjustment for wallet `{wallet_address}`.\n"
                    f"Operation: **{direction.upper()} {abs_amount} Matchsticks**.\n"
                    f"Reason: {display_reason_for_user}.\n" 
                    f"Transaction Batch ID: ...{new_tx_batch_id[-6:] if new_tx_batch_id != 'N/A' else 'N/A'}"
                )
                await interaction.followup.send(success_msg, ephemeral=True)
                logger.info(f"Balance adjustment successful for {wallet_address}. TxBatchID: {new_tx_batch_id}")
            else:
                error_msg = f"❌ Failed to process balance adjustment for `{wallet_address}`. API response was not as expected or indicated failure. \n`Response: {str(response)[:1000]}`"
                await interaction.followup.send(error_msg, ephemeral=True)
                logger.error(f"Balance adjustment failed for {wallet_address}. API Response: {response}")

        except Exception as e:
            logger.exception(f"Exception during balance adjustment for {wallet_address}:")
            await interaction.followup.send(f"⚙️ An unexpected error occurred during balance adjustment: {str(e)[:1000]}", ephemeral=True)

    @commands.command(name="send_balance_adj_panel")
    @is_prefix_admin_in_guild() 
    async def send_balance_adj_panel_command(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Wallet Balance Adjustment Panel",
            description="Use the button below to manually adjust a user's Matchsticks balance.",
            color=discord.Color.gold()
        )
        view = BalanceAdjustmentPanelView(self)
        await ctx.send(embed=embed, view=view) 
        logger.info(f"BalanceAdjustmentPanel sent by {ctx.author.name} to channel {ctx.channel.id}")

    @send_balance_adj_panel_command.error
    async def send_balance_adj_panel_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingAnyRole): 
            await ctx.send("⛔ You do not have the 'Ranger' role for this command.")
        else:
            logger.error(f"Error in send_balance_adj_panel_command: {error}", exc_info=True)
            await ctx.send("⚙️ An error occurred with the panel command.")

async def setup(bot: commands.Bot):
    if not getattr(bot, 'snag_client', None) or not getattr(bot.snag_client, '_api_key', None):
        logger.error("CRITICAL: Main Snag API client (or key) missing. BalanceAdjustmentCog WONT load.")
        return
    if not hasattr(bot.snag_client, 'create_transaction'):
        logger.error("CRITICAL: SnagApiClient is missing 'create_transaction' method. BalanceAdjustmentCog WONT load.")
        return

    cog = BalanceAdjustmentCog(bot)
    await bot.add_cog(cog)
    bot.add_view(BalanceAdjustmentPanelView(cog))
    logger.info("Registered persistent View for BalanceAdjustmentPanelView.")