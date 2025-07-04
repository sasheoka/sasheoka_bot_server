# cogs/block_unblock_cog.py
import discord
from discord.ext import commands
import logging
from typing import Optional, Dict, Any
import re

logger = logging.getLogger(__name__)

EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")

# --- View for action confirmation ---
class ConfirmBlockActionView(discord.ui.View):
    def __init__(self, cog_instance: "BlockUnblockCog", user_data: Dict[str, Any], original_interaction: discord.Interaction):
        super().__init__(timeout=180.0)
        self.cog = cog_instance
        self.user_data = user_data
        self.wallet_address = user_data.get("walletAddress", "N/A")
        self.original_interaction = original_interaction
        self.message: Optional[discord.Message] = None

        current_status = self.cog.get_block_status_from_userdata(self.user_data)
        self.block_button.disabled = current_status
        self.unblock_button.disabled = not current_status

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message("Only the user who initiated this action can use these buttons.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(content=f"Action for wallet `{self.wallet_address}` timed out. Buttons removed.", view=None)
            except discord.HTTPException:
                pass
        self.stop()

    async def _handle_action(self, interaction: discord.Interaction, block_flag: bool):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        
        action_text = "blocking" if block_flag else "unblocking"
        await interaction.response.edit_message(content=f"Processing {action_text} request for `{self.wallet_address}`...", view=self)
        
        await self.cog.handle_update_action(interaction, self.user_data, block_flag)
        self.stop()

    @discord.ui.button(label="🔴 Block", style=discord.ButtonStyle.danger, custom_id="block_unblock:block_v3")
    async def block_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_action(interaction, True)

    @discord.ui.button(label="🟢 Unblock", style=discord.ButtonStyle.success, custom_id="block_unblock:unblock_v3")
    async def unblock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_action(interaction, False)

# --- Modal window for address input ---
class BlockUnblockModal(discord.ui.Modal, title="Manage Wallet Block Status"):
    wallet_address_input = discord.ui.TextInput(
        label="Wallet Address (EVM)",
        placeholder="0x...",
        required=True,
        style=discord.TextStyle.short,
        min_length=42,
        max_length=42
    )

    def __init__(self, cog_instance: "BlockUnblockCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        wallet_address = self.wallet_address_input.value.strip().lower()
        await self.cog.handle_initial_check(interaction, wallet_address)

# --- Cog class ---
class BlockUnblockCog(commands.Cog, name="Block/Unblock User"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snag_client = getattr(bot, 'snag_client', None)
        if not self.snag_client:
            logger.error(f"{self.__class__.__name__}: SnagApiClient not found! Functionality will be disabled.")
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    def get_block_status_from_userdata(self, user_data: Dict[str, Any]) -> bool:
        user_metadata_list = user_data.get("userMetadata", [])
        if not user_metadata_list:
            return False
        return user_metadata_list[0].get("isBlocked", False)

    async def handle_initial_check(self, interaction: discord.Interaction, wallet_address: str):
        if not EVM_ADDRESS_PATTERN.match(wallet_address):
            await interaction.followup.send("⚠️ Invalid EVM wallet address format. Please use `0x...`", ephemeral=True)
            return

        if not self.snag_client or not self.snag_client._api_key:
            await interaction.followup.send("⚠️ Snag API client is not configured.", ephemeral=True)
            return

        response = await self.snag_client._make_request(
            "GET",
            "/api/users",
            params={"walletAddress": wallet_address}
        )

        if not response or response.get("error") or not isinstance(response.get("data"), list) or not response["data"]:
            error_message = response.get("message", "API request failed.") if response else "No response from API."
            await interaction.followup.send(f"❌ **Wallet Not Found.**\nCould not find a user with wallet `{wallet_address}`.\n`{error_message}`", ephemeral=True)
            return
        
        user_data = response["data"][0]
        is_blocked = self.get_block_status_from_userdata(user_data)
        
        status_text = "🔴 Blocked" if is_blocked else "🟢 Not Blocked"
        
        embed = discord.Embed(
            title="Wallet Status",
            description=f"**Wallet:** `{wallet_address}`\n**Current Status:** {status_text}",
            color=discord.Color.red() if is_blocked else discord.Color.green()
        )
        
        view = ConfirmBlockActionView(self, user_data, interaction)
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        view.message = message

    async def handle_update_action(self, interaction: discord.Interaction, user_data: Dict[str, Any], block_flag: bool):
        wallet_address = user_data.get("walletAddress")
        user_id = user_data.get("id")
        action_text = "Block" if block_flag else "Unblock"

        if not wallet_address or not user_id:
            await interaction.edit_original_response(content=f"❌ Critical Error: Could not retrieve `walletAddress` or `userId` for the update.", view=None)
            return

        payload = {
            "userId": user_id,
            "walletAddress": wallet_address,
            "organizationId": self.snag_client._organization_id,
            "websiteId": self.snag_client._website_id,
            "isBlocked": block_flag
        }
        
        logger.info(f"User {interaction.user.name} is performing '{action_text}' for wallet {wallet_address}.")

        update_response = await self.snag_client.create_user_metadata(payload)

        if update_response and not update_response.get("error"):
            final_status_text = "🔴 Blocked" if block_flag else "🟢 Not Blocked"
            embed = discord.Embed(
                title=f"✅ Success: {action_text}",
                description=f"**Wallet:** `{wallet_address}`\n**New Status:** {final_status_text}",
                color=discord.Color.red() if block_flag else discord.Color.green()
            )
            await interaction.edit_original_response(embed=embed, view=None)
        else:
            error_message = update_response.get("message", "Unknown error") if update_response else "No response"
            embed = discord.Embed(
                title=f"❌ Error: {action_text}",
                description=f"Failed to update status for wallet `{wallet_address}`.\n**Reason:** `{error_message}`",
                color=discord.Color.orange()
            )
            await interaction.edit_original_response(embed=embed, view=None)

async def setup(bot: commands.Bot):
    if not getattr(bot, 'snag_client', None) or not bot.snag_client._api_key:
        logger.critical("CRITICAL: Snag API client or API key is missing. BlockUnblockCog will NOT be loaded.")
        return
    await bot.add_cog(BlockUnblockCog(bot))