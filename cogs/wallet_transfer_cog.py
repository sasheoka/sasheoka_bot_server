# cogs/wallet_transfer_cog.py
import discord
from discord.ext import commands
import logging
import re
from typing import Optional, List

from utils.snag_api_client import SnagApiClient, GET_USER_ENDPOINT
from utils.checks import is_prefix_admin_in_guild

logger = logging.getLogger(__name__)
EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")

# --- Модальное окно для ввода адресов ---
class WalletTransferModal(discord.ui.Modal, title="Wallet Transfer"):
    old_wallet_address_input = discord.ui.TextInput(
        label="Compromised Wallet Address",
        placeholder="0x...",
        required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0
    )
    new_wallet_address_input = discord.ui.TextInput(
        label="New Wallet Address",
        placeholder="0x...",
        required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=1
    )

    def __init__(self, cog_instance: "WalletTransferCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        old_wallet = self.old_wallet_address_input.value.strip().lower()
        new_wallet = self.new_wallet_address_input.value.strip().lower()

        await self.cog.process_wallet_transfer(interaction, old_wallet, new_wallet)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in WalletTransferModal: {error}", exc_info=True)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("An error occurred in the modal.", ephemeral=True)
            else:
                await interaction.response.send_message("An error occurred in the modal.", ephemeral=True)
        except discord.HTTPException:
            pass

# --- View для панели управления ---
class WalletTransferPanelView(discord.ui.View):
    def __init__(self, cog_instance: "WalletTransferCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def _check_ranger_role(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
             await interaction.response.send_message("This command can only be used on a server.", ephemeral=True)
             return False
        ranger_role = discord.utils.get(interaction.guild.roles, name="Ranger")
        if not ranger_role:
            await interaction.response.send_message("⛔ The 'Ranger' role was not found on this server.", ephemeral=True)
            return False
        if ranger_role not in interaction.user.roles:
            await interaction.response.send_message("⛔ You do not have the required 'Ranger' role to use this button.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✈️ Initiate Wallet Transfer", style=discord.ButtonStyle.danger, custom_id="wallet_transfer:open_modal_v1")
    async def open_transfer_modal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction):
            return
        modal = WalletTransferModal(self.cog)
        await interaction.response.send_modal(modal)


# --- Класс Кога ---
class WalletTransferCog(commands.Cog, name="Wallet Transfer"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snag_client: Optional[SnagApiClient] = getattr(bot, 'snag_client', None)
        if not self.snag_client:
            logger.error(f"{self.__class__.__name__}: Main SnagApiClient (bot.snag_client) not found! Transfers will not work.")
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    async def cog_load(self):
        logger.info(f"Cog '{self.__class__.__name__}' successfully initialized by bot.")
        self.bot.add_view(WalletTransferPanelView(self))
        logger.info(f"Registered persistent View for WalletTransferPanelView.")

    async def process_wallet_transfer(self, interaction: discord.Interaction, old_wallet: str, new_wallet: str):
        report_lines: List[str] = []

        # --- 0. Валидация ---
        if not EVM_ADDRESS_PATTERN.match(old_wallet) or not EVM_ADDRESS_PATTERN.match(new_wallet):
            await interaction.followup.send("⚠️ Invalid EVM address format. Both addresses must be valid `0x...` wallets.", ephemeral=True)
            return
        if old_wallet == new_wallet:
            await interaction.followup.send("⚠️ The old and new wallet addresses cannot be the same.", ephemeral=True)
            return
            
        logger.info(f"User {interaction.user.name} initiated wallet transfer from {old_wallet} to {new_wallet}")

        # --- 1. Найти User ID по старому кошельку ---
        report_lines.append(f"**Step 1: Finding User by Old Wallet** (`...{old_wallet[-6:]}`)")
        await interaction.edit_original_response(content="\n".join(report_lines))

        user_response = await self.snag_client._make_request("GET", GET_USER_ENDPOINT, params={'walletAddress': old_wallet, 'limit': 1})
        
        if not user_response or user_response.get("error") or not isinstance(user_response.get("data"), list) or not user_response["data"]:
            report_lines.append(f"❌ **Error:** Could not find a user linked to the old wallet address.")
            await interaction.edit_original_response(content="\n".join(report_lines))
            return

        user_data = user_response["data"][0]
        user_id = user_data.get("id")

        if not user_id:
            report_lines.append(f"❌ **Error:** Found user data but it's missing a `userId`. Aborting.")
            await interaction.edit_original_response(content="\n".join(report_lines))
            return
            
        report_lines.append(f"✅ User found. **User ID:** `{user_id}`")
        await interaction.edit_original_response(content="\n".join(report_lines))

        # --- 2. Отвязать старый кошелек ---
        report_lines.append(f"\n**Step 2: Disconnecting Old Wallet**")
        await interaction.edit_original_response(content="\n".join(report_lines))

        disconnect_payload = {
            'userId': user_id, 
            'walletAddress': old_wallet,
            'organizationId': self.snag_client._organization_id,
            'websiteId': self.snag_client._website_id
        }
        disconnect_response = await self.snag_client._make_request("POST", "/api/users/disconnect", json_data=disconnect_payload)

        # Успешный дисконнект часто возвращает 204 No Content, поэтому проверяем на наличие ошибки
        if disconnect_response and disconnect_response.get("error"):
            error_msg = disconnect_response.get('message', 'Unknown error during disconnection.')
            report_lines.append(f"❌ **Error:** Failed to disconnect old wallet. API says: `{error_msg}`")
            await interaction.edit_original_response(content="\n".join(report_lines))
            return

        report_lines.append(f"✅ Old wallet disconnected successfully.")
        await interaction.edit_original_response(content="\n".join(report_lines))

        # --- 3. Привязать новый кошелек ---
        report_lines.append(f"\n**Step 3: Connecting New Wallet** (`...{new_wallet[-6:]}`)")
        await interaction.edit_original_response(content="\n".join(report_lines))

        connect_payload = {
            'userId': user_id, 
            'walletAddress': new_wallet,
            'organizationId': self.snag_client._organization_id,
            'websiteId': self.snag_client._website_id
        }
        connect_response = await self.snag_client._make_request("POST", "/api/users/connect", json_data=connect_payload)

        if not connect_response or connect_response.get("error"):
            error_msg = connect_response.get('message', 'Unknown error during connection.')
            report_lines.append(f"❌ **Error:** Failed to connect the new wallet. API says: `{error_msg}`")
            report_lines.append("\n**ACTION FAILED! The user's account may now be without any wallet. Please investigate.**")
            await interaction.edit_original_response(content="\n".join(report_lines))
            return
            
        report_lines.append(f"✅ New wallet connected successfully.")

        # --- 4. Финальный отчет ---
        final_embed = discord.Embed(
            title="✅ Wallet Transfer Successful",
            description=f"The user's progress has been successfully transferred to the new wallet address.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        final_embed.add_field(name="User ID", value=f"`{user_id}`", inline=False)
        final_embed.add_field(name="Old Wallet (Disconnected)", value=f"`{old_wallet}`", inline=False)
        final_embed.add_field(name="New Wallet (Connected)", value=f"`{new_wallet}`", inline=False)
        final_embed.set_footer(text=f"Operation performed by: {interaction.user.display_name}")

        await interaction.edit_original_response(content="", embed=final_embed)
        logger.info(f"Wallet transfer for userId {user_id} from {old_wallet} to {new_wallet} completed by {interaction.user.name}.")

    @commands.command(name="send_wallet_transfer_panel")
    @is_prefix_admin_in_guild()
    async def send_wallet_transfer_panel_command(self, ctx: commands.Context):
        """Sends the persistent panel for wallet transfers."""
        embed = discord.Embed(
            title="Wallet Progress Transfer Panel",
            description="Initiate progress transfer between wallets. This is a sensitive operation, use with caution.",
            color=discord.Color.red()
        )
        view = WalletTransferPanelView(self)
        await ctx.send(embed=embed, view=view)
        logger.info(f"WalletTransferPanel sent by {ctx.author.name} to channel {ctx.channel.id}")

    @send_wallet_transfer_panel_command.error
    async def send_panel_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("⛔ You do not have permission to use this command.", delete_after=10)
        else:
            logger.error(f"Error in send_wallet_transfer_panel_command: {error}", exc_info=True)
            await ctx.send("⚙️ An unexpected error occurred.")


async def setup(bot: commands.Bot):
    if not hasattr(bot, 'snag_client') or not bot.snag_client:
        logger.error("WalletTransferCog cannot be loaded: Main SnagApiClient (bot.snag_client) is missing.")
        return
    await bot.add_cog(WalletTransferCog(bot))