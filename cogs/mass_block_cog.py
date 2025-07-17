# cogs/mass_block_cog.py
import discord
from discord import app_commands
from discord.ext import commands
import logging
import asyncio
import re
import os
from typing import List, Dict, Any, Optional

from utils.snag_api_client import SnagApiClient
from utils.checks import is_admin_in_guild # Import our permission check

logger = logging.getLogger(__name__)

EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")
MAX_FILE_SIZE = 1024 * 100  # 100 KB file size limit

# --- View for confirming the mass action ---
class MassActionConfirmView(discord.ui.View):
    def __init__(self, cog_instance: "MassBlockCog", found_wallets: List[Dict[str, Any]], original_interaction: discord.Interaction):
        super().__init__(timeout=300.0) # 5 minutes to decide
        self.cog = cog_instance
        self.found_wallets = found_wallets # List of user data dicts that were found
        self.original_interaction = original_interaction
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Ensure only the command author can use the buttons
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message("Only the user who initiated this command can perform this action.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(content="The time for this bulk action has expired. Buttons removed.", view=None, embed=None)
            except discord.HTTPException:
                pass # Message might have been deleted
        self.stop()

    async def _perform_mass_action(self, interaction: discord.Interaction, block_flag: bool):
        # Disable buttons and show a status message
        for item in self.children:
            item.disabled = True
        action_name = "Blocking" if block_flag else "Unblocking"
        await interaction.response.edit_message(content=f"Processing **{action_name}** for {len(self.found_wallets)} wallets...", view=self, embed=None)

        results = await self.cog.process_mass_update(self.found_wallets, block_flag, interaction.user)
        
        success_count = results['success']
        fail_count = results['failed']
        
        embed = discord.Embed(
            title=f"Bulk Action Report: {action_name}",
            color=discord.Color.green() if fail_count == 0 else discord.Color.orange()
        )
        embed.description = f"Processed wallets: {len(self.found_wallets)}"
        embed.add_field(name="✅ Success", value=str(success_count), inline=True)
        embed.add_field(name="❌ Failures", value=str(fail_count), inline=True)

        if results['failed_wallets']:
            failed_list = "\n".join([f"`{addr}`" for addr in results['failed_wallets']])
            embed.add_field(name="Wallets with errors", value=failed_list[:1024], inline=False)
            
        await interaction.edit_original_response(content="Bulk operation complete.", embed=embed, view=None)
        self.stop()


    @discord.ui.button(label="🔴 Block All", style=discord.ButtonStyle.danger, custom_id="mass_block:block_all_v2")
    async def block_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._perform_mass_action(interaction, True)

    @discord.ui.button(label="🟢 Unblock All", style=discord.ButtonStyle.success, custom_id="mass_block:unblock_all_v2")
    async def unblock_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._perform_mass_action(interaction, False)


# --- Cog Class ---
class MassBlockCog(commands.Cog, name="Mass Block Tool"):
    """
    Tool for mass checking and blocking/unblocking wallets from a file.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snag_client: Optional[SnagApiClient] = getattr(bot, 'snag_client', None)
        if not self.snag_client:
            logger.error(f"{self.__class__.__name__}: Main SnagApiClient not found! Functionality will be disabled.")
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    def get_block_status_from_userdata(self, user_data: Dict[str, Any]) -> bool:
        """Extracts block status from user data."""
        user_metadata_list = user_data.get("userMetadata", [])
        if not user_metadata_list:
            return False
        return user_metadata_list[0].get("isBlocked", False)
        
    async def _get_wallets_from_file(self, file: discord.Attachment) -> List[str]:
        """Reads, decodes, and validates addresses from a file."""
        content = await file.read()
        lines = content.decode('utf-8').splitlines()
        
        valid_wallets = []
        for line in lines:
            address = line.strip().lower()
            if EVM_ADDRESS_PATTERN.match(address):
                valid_wallets.append(address)
        return list(set(valid_wallets)) # Remove duplicates

    @app_commands.command(name="mass_block_tool", description="Mass check and block/unblock wallets from a .txt file.")
    @is_admin_in_guild() # <-- Using our permission check!
    @app_commands.describe(wallets_file=".txt file with EVM addresses, one per line.")
    async def mass_block_tool(self, interaction: discord.Interaction, wallets_file: discord.Attachment):
        await interaction.response.defer(ephemeral=True, thinking=True)

        # --- 1. File validation ---
        if not wallets_file.filename.lower().endswith('.txt'):
            await interaction.followup.send("⚠️ Error: Please upload a file in `.txt` format.", ephemeral=True)
            return
        if wallets_file.size > MAX_FILE_SIZE:
            await interaction.followup.send(f"⚠️ Error: File is too large (limit {MAX_FILE_SIZE / 1024} KB).", ephemeral=True)
            return
        
        wallets = await self._get_wallets_from_file(wallets_file)
        if not wallets:
            await interaction.followup.send("⚠️ No valid EVM addresses found in the file.", ephemeral=True)
            return
            
        logger.info(f"User {interaction.user.name} initiated mass check for {len(wallets)} wallets.")

        # --- 2. Parallel status checking ---
        tasks = [self.snag_client.get_user_data(wallet_address=w) for w in wallets]
        responses = await asyncio.gather(*tasks)

        # --- 3. Process results and build report ---
        found_wallets: List[Dict[str, Any]] = []
        not_found_wallets: List[str] = []
        api_error_wallets: List[str] = []

        # Categorize results
        for wallet, response in zip(wallets, responses):
            if response and not response.get("error") and isinstance(response.get("data"), list) and response["data"]:
                found_wallets.append(response["data"][0])
            elif response and response.get("error"):
                api_error_wallets.append(wallet)
            else: # Not found or empty response
                not_found_wallets.append(wallet)
        
        # --- 4. Create Embed with status report ---
        embed = discord.Embed(
            title=f"Report for {len(wallets)} wallets",
            description="Below is the information about the current status of each wallet.",
            color=discord.Color.blue()
        )
        embed.set_footer(text="Click the buttons below to block or unblock ALL found wallets.")
        
        # Separate found wallets into blocked and not blocked
        blocked = [f"`{w.get('walletAddress')}`" for w in found_wallets if self.get_block_status_from_userdata(w)]
        not_blocked = [f"`{w.get('walletAddress')}`" for w in found_wallets if not self.get_block_status_from_userdata(w)]

        if blocked: embed.add_field(name=f"🔴 Blocked ({len(blocked)})", value="\n".join(blocked)[:1024], inline=False)
        if not_blocked: embed.add_field(name=f"🟢 Not Blocked ({len(not_blocked)})", value="\n".join(not_blocked)[:1024], inline=False)
        if not_found_wallets: embed.add_field(name=f"❓ Not Found in System ({len(not_found_wallets)})", value="\n".join(f"`{w}`" for w in not_found_wallets)[:1024], inline=False)
        if api_error_wallets: embed.add_field(name=f"⚠️ API Error ({len(api_error_wallets)})", value="\n".join(f"`{w}`" for w in api_error_wallets)[:1024], inline=False)
        
        if not found_wallets:
            await interaction.followup.send(embed=embed, ephemeral=True)
            return # No need for buttons if no wallets can be actioned

        # --- 5. Send report and action buttons ---
        view = MassActionConfirmView(self, found_wallets, interaction)
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        view.message = message

    async def process_mass_update(self, wallets_data: List[Dict[str, Any]], block_flag: bool, user: discord.User) -> Dict:
        """Performs a mass update of wallet statuses."""
        action_text = "blocking" if block_flag else "unblocking"
        logger.info(f"User {user.name} is mass-{action_text} {len(wallets_data)} wallets.")
        
        tasks = []
        for user_data in wallets_data:
            payload = {
                "walletAddress": user_data.get("walletAddress"),
                "organizationId": self.snag_client._organization_id,
                "websiteId": self.snag_client._website_id,
                "isBlocked": block_flag
            }
            tasks.append(self.snag_client.create_user_metadata(payload))

        # Execute requests and collect results, including exceptions
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        results = {"success": 0, "failed": 0, "failed_wallets": []}
        for user_data, response in zip(wallets_data, responses):
            wallet_address = user_data.get("walletAddress")
            if isinstance(response, Exception) or (isinstance(response, dict) and response.get("error")):
                results["failed"] += 1
                results["failed_wallets"].append(wallet_address)
                logger.error(f"Failed to mass-update wallet {wallet_address}. Reason: {response}")
            else:
                results["success"] += 1
        
        return results


async def setup(bot: commands.Bot):
    # Check for everything the cog needs to run
    if not getattr(bot, 'snag_client', None) or not bot.snag_client._api_key:
        logger.critical("CRITICAL: Snag API client or API key is missing. MassBlockCog will NOT be loaded.")
        return
    if not os.getenv('ADMIN_GUILD_ID') or not os.getenv('RANGER_ROLE_ID'):
         logger.critical("CRITICAL: ADMIN_GUILD_ID or RANGER_ROLE_ID not set. MassBlockCog will NOT be loaded as it relies on admin checks.")
         return

    await bot.add_cog(MassBlockCog(bot))