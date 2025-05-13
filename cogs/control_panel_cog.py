# cogs/control_panel_cog.py
import discord
from discord.ext import commands
import logging
import datetime 
from datetime import timezone
import asyncio
import math
import re
from typing import Dict, Any, Optional, List, Tuple # Tuple импортирован

from utils.snag_api_client import SnagApiClient

logger = logging.getLogger(__name__)
EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")

# --- КОНСТАНТЫ ДЛЯ КОГА ---
PAGE_LIMIT = 1000
MAX_API_PAGES_TO_FETCH = 10
API_REQUEST_DELAY = 0.5
ITEMS_PER_PAGE = 10 
BADGES_PER_PAGE = 5 
VIEW_TIMEOUT = 300.0
MATCHSTICKS_CURRENCY_ID = "7f74ae35-a6e2-496a-83ea-5b2e18769560"
# ---------------------


# --- Модальные Окна ---
# (Без изменений)
class FindWalletModal(discord.ui.Modal, title="Find Wallet by Social Handle"):
    discord_input = discord.ui.TextInput(label='Discord Handle (Optional)', placeholder='username#1234 or username', required=False, style=discord.TextStyle.short, row=0, max_length=100)
    twitter_input = discord.ui.TextInput(label='Twitter/X Handle (Optional)', placeholder='@username or username', required=False, style=discord.TextStyle.short, row=1, max_length=100)
    def __init__(self, cog_instance: "ControlPanelCog"): super().__init__(timeout=None); self.cog = cog_instance
    async def on_submit(self, interaction: discord.Interaction): await interaction.response.defer(ephemeral=True, thinking=True); await self.cog.handle_find_wallet_logic(interaction, self.discord_input.value, self.twitter_input.value)
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"FindWalletModal Error: {error}", exc_info=True)
        try:
            if interaction.response.is_done(): await interaction.followup.send('An error occurred in the modal.', ephemeral=True)
            else: await interaction.response.send_message('An error occurred in the modal.', ephemeral=True)
        except discord.HTTPException: pass
class AddressForHistoryModal(discord.ui.Modal, title="Get Quest History by Address"):
    address_input = discord.ui.TextInput(label='EVM Wallet Address', placeholder='0x...', required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0)
    quest_filter_input = discord.ui.TextInput(label='Quest Name Filter (Optional)', placeholder='Enter keywords...', required=False, style=discord.TextStyle.short, max_length=100, row=1)
    def __init__(self, cog_instance: "ControlPanelCog"): super().__init__(timeout=None); self.cog = cog_instance
    async def on_submit(self, interaction: discord.Interaction): await interaction.response.defer(thinking=True, ephemeral=True); await self.cog._process_and_send_quest_history(interaction, self.address_input.value, self.quest_filter_input.value)
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"AddressHistoryModal Error: {error}", exc_info=True)
        try:
            if interaction.response.is_done(): await interaction.followup.send('An error occurred in the history modal.', ephemeral=True)
            else: await interaction.response.send_message('An error occurred in the history modal.', ephemeral=True)
        except discord.HTTPException: pass
class AddressForSocialsModal(discord.ui.Modal, title="Find Socials by Wallet Address"):
    address_input = discord.ui.TextInput(label='EVM Wallet Address', placeholder='0x...', required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0)
    def __init__(self, cog_instance: "ControlPanelCog"): super().__init__(timeout=None); self.cog = cog_instance
    async def on_submit(self, interaction: discord.Interaction): await interaction.response.defer(thinking=True, ephemeral=True); await self.cog.handle_find_socials_logic(interaction, self.address_input.value)
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"AddressSocialsModal Error: {error}", exc_info=True)
        try:
            if interaction.response.is_done(): await interaction.followup.send('An error occurred in the socials modal.', ephemeral=True)
            else: await interaction.response.send_message('An error occurred in the socials modal.', ephemeral=True)
        except discord.HTTPException: pass
class BalanceCheckModal(discord.ui.Modal, title="Check All Balances by Wallet"):
    address_input = discord.ui.TextInput(label='EVM Wallet Address', placeholder='0x...', required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0)
    def __init__(self, cog_instance: "ControlPanelCog"): super().__init__(timeout=None); self.cog = cog_instance
    async def on_submit(self, interaction: discord.Interaction): await interaction.response.defer(thinking=True, ephemeral=True); await self.cog.handle_balance_check_logic(interaction, self.address_input.value)
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"BalanceCheckModal Error: {error}", exc_info=True)
        try:
            if interaction.response.is_done(): await interaction.followup.send('An error occurred in the balance modal.', ephemeral=True)
            else: await interaction.response.send_message('An error occurred in the balance modal.', ephemeral=True)
        except discord.HTTPException: pass
class AddressForBadgesModal(discord.ui.Modal, title="Get User Badges by Wallet (WIP)"):
    address_input = discord.ui.TextInput(label='EVM Wallet Address', placeholder='0x...', required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0)
    def __init__(self, cog_instance: "ControlPanelCog"): super().__init__(timeout=None); self.cog = cog_instance
    async def on_submit(self, interaction: discord.Interaction): await interaction.response.defer(thinking=True, ephemeral=True); await self.cog.handle_get_badges_logic(interaction, self.address_input.value)
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"AddressForBadgesModal Error: {error}", exc_info=True)
        try:
            if interaction.response.is_done(): await interaction.followup.send('An error occurred in the badges modal.', ephemeral=True)
            else: await interaction.response.send_message('An error occurred in the badges modal.', ephemeral=True)
        except discord.HTTPException: pass
class AddressForStatsModal(discord.ui.Modal, title="Get Quest Statistics"): # (WIP) добавлено
    address_input = discord.ui.TextInput(label='EVM Wallet Address', placeholder='0x...', required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0)
    def __init__(self, cog_instance: "ControlPanelCog"): super().__init__(timeout=None); self.cog = cog_instance
    async def on_submit(self, interaction: discord.Interaction): await interaction.response.defer(thinking=True, ephemeral=True); await self.cog.handle_quest_stats_logic(interaction, self.address_input.value)
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"AddressForStatsModal Error: {error}", exc_info=True)
        try:
            if interaction.response.is_done(): await interaction.followup.send('An error occurred in the quest stats modal.', ephemeral=True)
            else: await interaction.response.send_message('An error occurred in the quest stats modal.', ephemeral=True)
        except discord.HTTPException: pass

# --- Пагинаторы ---
class QuestHistoryPaginatorView(discord.ui.View):
    # ... (Код QuestHistoryPaginatorView как в предыдущем ответе, с исправленным _format_datetime_static) ...
    current_page: int = 1; sep: int = ITEMS_PER_PAGE
    def __init__(self, original_interaction: discord.Interaction, all_transactions: List[Dict[str, Any]], total_matchsticks_earned: int, target_address: str):
        super().__init__(timeout=VIEW_TIMEOUT); self.original_interaction = original_interaction; self.all_transactions = all_transactions; self.total_matchsticks_earned = total_matchsticks_earned
        self.target_address = target_address; self.max_pages = math.ceil(len(self.all_transactions) / self.sep) if self.all_transactions else 1; self.message: Optional[discord.Message] = None; self._update_buttons()
    async def _get_page_data(self) -> List[Dict[str, Any]]: base = (self.current_page - 1) * self.sep; return self.all_transactions[base : base + self.sep]
    async def _create_page_embed(self, page_transactions: List[Dict[str, Any]]) -> discord.Embed:
        embed = discord.Embed(title=f"Quest Completion History for:", description=f"`{self.target_address}`", color=discord.Color.green())
        if not page_transactions and self.current_page == 1: embed.description += "\n\nNo quest completions found."
        else:
            for tx in page_transactions:
                amount = tx.get("amount", "N/A"); created_at_str = tx.get("createdAt"); rule_name = tx.get("loyaltyTransaction", {}).get("loyaltyRule", {}).get("name", "Unknown Action").strip()
                date_formatted = self._format_datetime_static(created_at_str)
                currency_id_tx = tx.get("loyaltyCurrencyId"); currency_name_display = "points"
                if currency_id_tx == MATCHSTICKS_CURRENCY_ID: currency_name_display = "Matchsticks"
                field_name = f"✅ {rule_name}"; field_value = f"**Earned:** `{amount}` {currency_name_display} | **Completed:** {date_formatted}"; embed.add_field(name=field_name, value=field_value, inline=False)
        footer_text = f"Page {self.current_page} of {self.max_pages} | Total Matchsticks from listed: {self.total_matchsticks_earned}"; embed.set_footer(text=footer_text); embed.timestamp = discord.utils.utcnow(); return embed
    @staticmethod
    def _format_datetime_static(datetime_str: Optional[str]) -> str: # ИСПРАВЛЕНИЕ ЗДЕСЬ
        if not datetime_str: return "Date unknown"
        formats_to_try = ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"] # ВОССТАНОВЛЕНО
        parsed_dt = None
        for fmt in formats_to_try:
            try: dt_str_cleaned = datetime_str.replace('Z', '').split('.')[0]; dt_obj = datetime.datetime.strptime(dt_str_cleaned, '%Y-%m-%dT%H:%M:%S'); parsed_dt = dt_obj.replace(tzinfo=timezone.utc); break
            except ValueError: continue
        if parsed_dt: return parsed_dt.strftime("%d/%m/%y")
        logger.warning(f"Could not parse date format: {datetime_str}"); return datetime_str
    def _update_buttons(self): self.first_page.disabled = self.current_page == 1; self.prev_page.disabled = self.current_page == 1; self.next_page.disabled = self.current_page >= self.max_pages; self.last_page.disabled = self.current_page >= self.max_pages
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_interaction.user.id: await interaction.response.send_message("Sorry, only the user who initiated this can use these buttons.", ephemeral=True); return False
        return True
    async def _send_page(self, interaction: discord.Interaction):
        if not interaction.response.is_done(): await interaction.response.defer()
        self._update_buttons(); page_data = await self._get_page_data(); embed = await self._create_page_embed(page_data)
        if self.message: await self.message.edit(embed=embed, view=self)
        elif interaction.is_original_response(): await interaction.edit_original_response(embed=embed, view=self)
        else: await interaction.followup.edit_message(message_id=interaction.message.id if interaction.message else "@original", embed=embed, view=self)
    @discord.ui.button(label="|< First", style=discord.ButtonStyle.secondary, row=0, custom_id="qh_first_v2_panel_final_v3")
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button): self.current_page = 1; await self._send_page(interaction)
    @discord.ui.button(label="< Prev", style=discord.ButtonStyle.primary, row=0, custom_id="qh_prev_v2_panel_final_v3")
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 1: self.current_page -= 1
        await self._send_page(interaction)
    @discord.ui.button(label="Next >", style=discord.ButtonStyle.primary, row=0, custom_id="qh_next_v2_panel_final_v3")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.max_pages: self.current_page += 1
        await self._send_page(interaction)
    @discord.ui.button(label="Last >|", style=discord.ButtonStyle.secondary, row=0, custom_id="qh_last_v2_panel_final_v3")
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button): self.current_page = self.max_pages; await self._send_page(interaction)
    async def on_timeout(self) -> None:
        if self.message:
            try: await self.message.edit(view=None)
            except discord.HTTPException as e: logger.error(f"Error removing view on timeout for QuestHistoryPaginatorView: {e}")

class BadgePaginatorView(discord.ui.View): # Код как был
    current_page: int = 1; sep: int = BADGES_PER_PAGE
    def __init__(self, original_interaction: discord.Interaction, all_badges: List[Dict[str, Any]], target_address: str):
        super().__init__(timeout=VIEW_TIMEOUT); self.original_interaction = original_interaction; self.all_badges = all_badges; self.target_address = target_address
        self.max_pages = math.ceil(len(self.all_badges) / self.sep) if self.all_badges else 1; self.message: Optional[discord.Message] = None; self._update_buttons()
    async def _get_page_data(self) -> List[Dict[str, Any]]: base = (self.current_page - 1) * self.sep; return self.all_badges[base : base + self.sep]
    async def _create_page_embed(self, page_badges: List[Dict[str, Any]]) -> discord.Embed:
        embed = discord.Embed(title=f"Badges for: `{self.target_address}` (WIP)", description=f"(New Loyalty System) - Page {self.current_page}/{self.max_pages}", color=discord.Color.gold())
        if not page_badges and self.current_page == 1: embed.description += "\n\nNo badges found for this wallet."
        for i, badge_info in enumerate(page_badges):
            badge_name = badge_info.get("name", "Unnamed Badge"); badge_desc = badge_info.get("description") or "No description."
            status_text = "✅ Associated"
            field_value = f"{badge_desc}\n*Status: {status_text}*"; embed.add_field(name=f"🏅 {badge_name}", value=field_value, inline=False)
            if i == 0 and self.current_page == 1:
                badge_image_url = badge_info.get("imageUrl")
                if badge_image_url: embed.set_thumbnail(url=badge_image_url)
        embed.set_footer(text=f"Total Badges associated: {len(self.all_badges)}"); embed.timestamp = discord.utils.utcnow(); return embed
    def _update_buttons(self): self.first_badge_page.disabled = self.current_page == 1; self.prev_badge_page.disabled = self.current_page == 1; self.next_badge_page.disabled = self.current_page >= self.max_pages; self.last_badge_page.disabled = self.current_page >= self.max_pages
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_interaction.user.id: await interaction.response.send_message("Sorry, only the user who initiated this can use these buttons.", ephemeral=True); return False
        return True
    async def _send_page(self, interaction: discord.Interaction):
        if not interaction.response.is_done(): await interaction.response.defer()
        self._update_buttons(); page_data = await self._get_page_data(); embed = await self._create_page_embed(page_data)
        if self.message: await self.message.edit(embed=embed, view=self)
        elif interaction.is_original_response(): await interaction.edit_original_response(embed=embed, view=self)
        else: await interaction.followup.edit_message(message_id=interaction.message.id if interaction.message else "@original", embed=embed, view=self)
    @discord.ui.button(label="|< First", style=discord.ButtonStyle.secondary, row=0, custom_id="badge_first_v3_final_v3")
    async def first_badge_page(self, interaction: discord.Interaction, button: discord.ui.Button): self.current_page = 1; await self._send_page(interaction)
    @discord.ui.button(label="< Prev", style=discord.ButtonStyle.primary, row=0, custom_id="badge_prev_v3_final_v3")
    async def prev_badge_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 1: self.current_page -= 1
        await self._send_page(interaction)
    @discord.ui.button(label="Next >", style=discord.ButtonStyle.primary, row=0, custom_id="badge_next_v3_final_v3")
    async def next_badge_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.max_pages: self.current_page += 1
        await self._send_page(interaction)
    @discord.ui.button(label="Last >|", style=discord.ButtonStyle.secondary, row=0, custom_id="badge_last_v3_final_v3")
    async def last_badge_page(self, interaction: discord.Interaction, button: discord.ui.Button): self.current_page = self.max_pages; await self._send_page(interaction)
    async def on_timeout(self) -> None:
        if self.message:
            try: await self.message.edit(view=None)
            except discord.HTTPException as e: logger.error(f"Error removing view on timeout for BadgePaginatorView: {e}")

# --- View для панели управления ---
class InfoPanelView(discord.ui.View):
    def __init__(self, cog_instance: "ControlPanelCog"): super().__init__(timeout=None); self.cog = cog_instance
    async def _check_ranger_role(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member): await interaction.response.send_message("This command can only be used in a server.", ephemeral=True); return False
        ranger_role = discord.utils.get(interaction.guild.roles, name="Ranger")
        if not ranger_role: await interaction.response.send_message("⛔ The 'Ranger' role was not found on this server.", ephemeral=True); return False
        if ranger_role not in interaction.user.roles: await interaction.response.send_message("⛔ You do not have the required role ('Ranger') to use this button.", ephemeral=True); return False
        return True
    @discord.ui.button(label="Find Wallet by Social", style=discord.ButtonStyle.success, custom_id="info_panel:find_wallet_v5_final_v3", row=0)
    async def find_wallet_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.send_modal(FindWalletModal(self.cog))
    @discord.ui.button(label="Task History by Wallet", style=discord.ButtonStyle.primary, custom_id="info_panel:history_by_wallet_v5_final_v3", row=1)
    async def task_history_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.send_modal(AddressForHistoryModal(self.cog))
    @discord.ui.button(label="Find Socials by Wallet", style=discord.ButtonStyle.secondary, custom_id="info_panel:socials_by_wallet_v5_final_v3", row=0)
    async def find_socials_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.send_modal(AddressForSocialsModal(self.cog))
    @discord.ui.button(label="Check Balances by Wallet", style=discord.ButtonStyle.danger, custom_id="info_panel:balance_by_wallet_v5_final_v3", row=0)
    async def check_balance_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.send_modal(BalanceCheckModal(self.cog))
    @discord.ui.button(label="Get User Badges (WIP)", style=discord.ButtonStyle.grey, custom_id="info_panel:get_user_badges_v5_final_v3", row=2)
    async def get_user_badges_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.send_modal(AddressForBadgesModal(self.cog))
    @discord.ui.button(label="Quest Stats", style=discord.ButtonStyle.blurple, custom_id="info_panel:quest_stats_v5_final_v3", row=1)
    async def quest_stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.send_modal(AddressForStatsModal(self.cog))

# --- Класс Кога ControlPanel ---
class ControlPanelCog(commands.Cog, name="Control Panel"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snag_client: Optional[SnagApiClient] = getattr(bot, 'snag_client', None)
        self.snag_client_legacy: Optional[SnagApiClient] = getattr(bot, 'snag_client_legacy', None)
        self._currency_cache: Optional[Dict[str, Dict[str, Any]]] = None
        self._currency_cache_time: Optional[datetime.datetime] = None
        self._currency_cache_lock = asyncio.Lock()
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")
        if not self.snag_client: logger.error(f"Main SnagApiClient not found for {self.__class__.__name__}! Some features will not work.")
        if not self.snag_client_legacy: logger.warning(f"Legacy SnagApiClient not found for {self.__class__.__name__}! Features relying on old API will not work.")

    async def cog_unload(self): logger.info(f"Cog '{self.__class__.__name__}' unloaded.")
    
    @commands.Cog.listener("on_ready")
    async def on_ready_register_views(self):
        if not self.snag_client or not self.snag_client._api_key: logger.warning(f"{self.__class__.__name__}: Main Snag client missing or API key not set. InfoPanelView might not function correctly.")
        self.bot.add_view(InfoPanelView(self)); logger.info(f"{self.__class__.__name__}: Persistent InfoPanelView registered.")
        if self.snag_client and self.snag_client._api_key: await self._get_currency_map()
        else: logger.warning(f"{self.__class__.__name__}: Could not pre-fetch currency map as main Snag client is not ready.")

    @commands.command(name="send_info_panel")
    @commands.has_any_role("Ranger")
    async def send_info_panel_command(self, ctx: commands.Context):
        embed = discord.Embed(title="ℹ️ Snag Loyalty Info Panel", description="Use the buttons below to query Snag Loyalty System. !!!Updated for new testnet HUB!!!", color=discord.Color.purple()); await ctx.send(embed=embed, view=InfoPanelView(self)); logger.info(f"Info Panel sent by {ctx.author.name} in channel {ctx.channel.id}")
    
    @send_info_panel_command.error
    async def send_info_panel_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingAnyRole): await ctx.send("⛔ You do not have the required role ('Ranger') to use this command.")
        else: logger.error(f"Error in send_info_panel_command: {error}", exc_info=True); await ctx.send("⚙️ An unexpected error occurred while trying to send the info panel.")

    async def _get_currency_map(self, force_refresh: bool = False) -> Optional[Dict[str, Dict[str, Any]]]:
        if not self.snag_client or not self.snag_client._api_key: logger.error("Cannot get currency map: Main SnagApiClient is not available or API key missing."); return self._currency_cache
        cache_duration = datetime.timedelta(minutes=30); now = discord.utils.utcnow()
        async with self._currency_cache_lock:
            if not force_refresh and self._currency_cache and self._currency_cache_time and (now - self._currency_cache_time < cache_duration): logger.debug("Using cached currency map."); return self._currency_cache
            logger.info("Refreshing currency map from Main Snag API..."); response_data = await self.snag_client.get_currencies(limit=PAGE_LIMIT)
            if response_data and isinstance(response_data.get("data"), list):
                self._currency_cache = {c['id']: c for c in response_data["data"] if isinstance(c, dict) and c.get("id")}; self._currency_cache_time = now; logger.info(f"Currency map updated. Found {len(self._currency_cache)} currencies from Main API."); return self._currency_cache
            logger.error("Failed to refresh currency map from Main API. Response was invalid or empty."); return self._currency_cache

    # ИСПРАВЛЕНИЕ: Методы должны быть частью класса (иметь self)
    async def _find_wallet_by_social_api_filter(self, client: SnagApiClient, handle_type: str, handle_value: str) -> Optional[str]:
        if not client or not client._api_key: 
            logger.warning(f"Attempted to use an uninitialized or keyless API client ({getattr(client, '_client_name', 'UnknownClient')}) for social lookup.")
            return None
        logger.info(f"[{getattr(client, '_client_name', 'SnagClient')}] Finding wallet for {handle_type}='{handle_value}'")
        account_data_response = await client.get_account_by_social(handle_type, handle_value)
        if account_data_response and isinstance(account_data_response.get("data"), list) and account_data_response["data"]:
            account_data = account_data_response["data"][0]; user_info = account_data.get("user"); 
            wallet_address = user_info.get("walletAddress") if isinstance(user_info, dict) else None
            if wallet_address: 
                logger.info(f"[{getattr(client, '_client_name', '')}] Found wallet: {wallet_address} for {handle_type} {handle_value}")
                return wallet_address
        logger.warning(f"[{getattr(client, '_client_name', '')}] Wallet not found for {handle_type} {handle_value}. Response: {str(account_data_response)[:200]}")
        return None

    async def _find_socials_by_wallet(self, client: SnagApiClient, target_address: str) -> str:
        if not client or not client._api_key: return "⚙️ API Client instance is not available or keyless."
        logger.info(f"[{getattr(client, '_client_name', 'SnagClient')}] Finding socials for {target_address}"); 
        account_data_response = await client.get_account_by_wallet(target_address)
        if not account_data_response: return "⚙️ Error contacting API or API key/config missing."
        if not isinstance(account_data_response.get("data"), list) or not account_data_response["data"]: return f"❌ No account data found for `{target_address}`."
        account_data = account_data_response["data"][0]; user_info = account_data.get("user", {}); metadata_list = user_info.get("userMetadata", [])
        display_name = "N/A"; discord_handle = None; twitter_handle = None
        if isinstance(metadata_list, list) and metadata_list:
            meta = metadata_list[0]
            if isinstance(meta, dict): display_name = meta.get("displayName", "N/A"); discord_handle = meta.get("discordUser"); twitter_handle = meta.get("twitterUser")
        if twitter_handle and not twitter_handle.startswith('@'): twitter_handle = f"@{twitter_handle}"
        return (f"**Display Name:** `{display_name}`\n"f"**Discord:** `{discord_handle or 'Not linked'}`\n"f"**Twitter/X:** `{twitter_handle or 'Not linked'}`")

    async def _get_all_wallet_balances(self, client: SnagApiClient, wallet_address: str) -> str:
        if not client or not client._api_key: return "⚙️ API Client instance (for balances) is not available or keyless."
        currency_map = await self._get_currency_map() # Использует self.snag_client (основной)
        if currency_map is None: return "⚠️ Error: Could not retrieve currency info from Main API. Balances cannot be displayed."
        logger.info(f"[{getattr(client, '_client_name', 'SnagClient')}] Requesting balances for {wallet_address}"); 
        acc_resp = await client.get_all_accounts_for_wallet(wallet_address)
        if acc_resp and isinstance(acc_resp.get("data"), list):
            accounts = acc_resp["data"]
            if not accounts: return f"ℹ️ No balances found for `{wallet_address}` in this loyalty system."
            lines = [f"💰 **Balances for:** `{wallet_address}` (New Loyalty System)\n"]; found_valid_balance = False
            for acc in accounts:
                currency_id = acc.get("loyaltyCurrencyId"); amount = acc.get("amount")
                if currency_id and amount is not None:
                    found_valid_balance = True; currency_info = currency_map.get(currency_id); 
                    currency_name = currency_info.get("name", "Unknown Currency") if currency_info else f"Unknown Currency (ID: {currency_id})"
                    currency_symbol = currency_info.get("symbol", "???") if currency_info else "???"
                    lines.append(f"- **{currency_name} ({currency_symbol}):** `{amount}`")
            if found_valid_balance: return "\n".join(lines)
            else: return f"ℹ️ No valid balance entries with known currencies for `{wallet_address}` in this system."
        logger.error(f"Failed to retrieve or parse balance data for {wallet_address} using {getattr(client, '_client_name', '')}. Response: {str(acc_resp)[:200]}"); 
        return "⚙️ Error retrieving balances from this loyalty system. Check logs."

    async def handle_find_wallet_logic(self, interaction: discord.Interaction, discord_h: Optional[str], twitter_h: Optional[str]):
        # ... (код как был, вызывает self._find_wallet_by_social_api_filter) ...
        discord_h = discord_h.strip() if discord_h else None; twitter_h = twitter_h.strip() if twitter_h else None
        if twitter_h and twitter_h.startswith('@'): twitter_h = twitter_h[1:]
        if not discord_h and not twitter_h: await interaction.followup.send("Please enter at least one social handle (Discord or Twitter/X).", ephemeral=True); return
        identifier_type = "discordUser" if discord_h else "twitterUser"; identifier_value = discord_h if discord_h else twitter_h
        if not identifier_value: await interaction.followup.send("Failed to determine social handle for lookup.", ephemeral=True); return
        found_address_legacy = None; found_address_main = None
        if self.snag_client_legacy and self.snag_client_legacy._api_key: logger.info(f"User {interaction.user.id} looking up wallet (Legacy Site) for {identifier_type}: {identifier_value}"); found_address_legacy = await self._find_wallet_by_social_api_filter(self.snag_client_legacy, identifier_type, identifier_value)
        else: logger.warning(f"Legacy SnagApiClient not available or key missing for wallet lookup by {identifier_type}.")
        if self.snag_client and self.snag_client._api_key: logger.info(f"User {interaction.user.id} looking up wallet (Main Site) for {identifier_type}: {identifier_value}"); found_address_main = await self._find_wallet_by_social_api_filter(self.snag_client, identifier_type, identifier_value)
        else: logger.warning(f"Main SnagApiClient not available or key missing for wallet lookup by {identifier_type}.")
        response_lines = []
        if found_address_legacy: response_lines.append(f"**Old Loyalty System Wallet:** `{found_address_legacy}`")
        if found_address_main: response_lines.append(f"**New Loyalty System Wallet:** `{found_address_main}`")
        if not response_lines: await interaction.followup.send(f"Could not find wallet for {identifier_type} `{identifier_value}` in either loyalty system.", ephemeral=True)
        else: await interaction.followup.send("\n".join(response_lines), ephemeral=True)

    async def handle_find_socials_logic(self, interaction: discord.Interaction, address_val: str):
        # ... (код как был, вызывает self._find_socials_by_wallet) ...
        target_address = address_val.strip().lower()
        if not EVM_ADDRESS_PATTERN.match(target_address): await interaction.followup.send("⚠️ Invalid EVM address format. Please use `0x...`", ephemeral=True); return
        logger.info(f"User {interaction.user.id} requested socials for wallet: {target_address}")
        socials_text_legacy = "Old Loyalty System: Socials could not be fetched (API client unavailable or error)."; socials_text_main = "New Loyalty System: Socials could not be fetched (API client unavailable or error)."
        if self.snag_client_legacy and self.snag_client_legacy._api_key: legacy_socials = await self._find_socials_by_wallet(self.snag_client_legacy, target_address); socials_text_legacy = f"**--- Old Loyalty System ---**\n{legacy_socials}"
        else: logger.warning(f"Legacy SnagApiClient not available for socials lookup of {target_address}.")
        if self.snag_client and self.snag_client._api_key: main_socials = await self._find_socials_by_wallet(self.snag_client, target_address); socials_text_main = f"**--- New Loyalty System ---**\n{main_socials}"
        else: logger.warning(f"Main SnagApiClient not available for socials lookup of {target_address}.")
        full_response = f"{socials_text_legacy}\n\n{socials_text_main}".strip()
        if len(full_response) > 1950: full_response = full_response[:1950] + "..."
        await interaction.followup.send(full_response if full_response else "No data found for this wallet in either system.", ephemeral=True)

    async def handle_balance_check_logic(self, interaction: discord.Interaction, address_val: str):
        # ... (код как был, вызывает self._get_all_wallet_balances с self.snag_client) ...
        target_address = address_val.strip().lower()
        if not EVM_ADDRESS_PATTERN.match(target_address): await interaction.followup.send("⚠️ Invalid EVM address format. Please use `0x...`", ephemeral=True); return
        if not self.snag_client or not self.snag_client._api_key: await interaction.followup.send("⚙️ Main API Client (New Loyalty System) is not available.", ephemeral=True); return
        logger.info(f"User {interaction.user.id} requested all balances for wallet (Main System): {target_address}"); result_message = await self._get_all_wallet_balances(self.snag_client, target_address)
        if len(result_message) > 1950: result_message = result_message[:1950] + "..."
        await interaction.followup.send(result_message, ephemeral=True)
        
    async def _fetch_and_process_quest_transactions( # Этот метод уже был методом экземпляра
        self, client: SnagApiClient, target_address: str, quest_filter: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], str, int]:
        # ... (код как был, использует переданный 'client') ...
        all_fetched_transactions: List[Dict[str, Any]] = []; last_transaction_id: Optional[str] = None; has_more_pages = True; api_page_count = 0; warning_message = ""; total_matchsticks_earned_for_history = 0
        client_name = getattr(client, '_client_name', 'SnagClient')
        logger.info(f"[{client_name}] Fetching all quest transactions for {target_address}...")
        while has_more_pages and api_page_count < MAX_API_PAGES_TO_FETCH:
            api_page_count += 1
            transaction_page_data = await client.get_transaction_entries(wallet_address=target_address, limit=PAGE_LIMIT, starting_after=last_transaction_id)
            if not transaction_page_data: warning_message += f"⚙️ Error fetching transaction history (Page {api_page_count}) from {client_name}. Results might be incomplete.\n"; break 
            current_page_transactions = transaction_page_data.get("data", []); has_more_pages = transaction_page_data.get("hasNextPage", False)
            if not isinstance(current_page_transactions, list): logger.warning(f"[{client_name}] Quest Transactions: 'data' not a list (Page {api_page_count}). Stopping."); has_more_pages = False; continue
            if not current_page_transactions: logger.info(f"[{client_name}] Quest Transactions: Page {api_page_count} is empty."); has_more_pages = False; continue
            for tx in current_page_transactions:
                if tx.get("direction") != "credit": continue
                rule_info = tx.get("loyaltyTransaction", {}).get("loyaltyRule", {})
                if not isinstance(rule_info, dict): continue
                rule_name = rule_info.get("name", "").strip()
                if not rule_name: continue
                if quest_filter and quest_filter.lower() not in rule_name.lower(): continue
                all_fetched_transactions.append(tx)
                if tx.get("loyaltyCurrencyId") == MATCHSTICKS_CURRENCY_ID:
                    try: total_matchsticks_earned_for_history += int(tx.get("amount", 0))
                    except (ValueError, TypeError): pass
            if current_page_transactions:
                last_tx = current_page_transactions[-1]; last_transaction_id = last_tx.get('id')
                if not last_transaction_id: logger.warning(f"[{client_name}] Cannot get last transaction ID from page. Stopping."); has_more_pages = False; continue
            if has_more_pages: await asyncio.sleep(API_REQUEST_DELAY)
        if api_page_count >= MAX_API_PAGES_TO_FETCH and has_more_pages: warning_message += f"⚠️ Loaded max transaction pages ({MAX_API_PAGES_TO_FETCH}) from {client_name}. History might be incomplete.\n"
        all_fetched_transactions.sort(key=lambda x: x.get('createdAt', '0'), reverse=True)
        logger.info(f"[{client_name}] Found {len(all_fetched_transactions)} filtered quest transactions for {target_address}.")
        return all_fetched_transactions, warning_message.strip(), total_matchsticks_earned_for_history

    async def _process_and_send_quest_history(self, interaction: discord.Interaction, target_address_str: str, quest_filter: Optional[str]):
        # ... (код как был, вызывает self._fetch_and_process_quest_transactions) ...
        target_address = target_address_str.strip().lower()
        if not EVM_ADDRESS_PATTERN.match(target_address): await interaction.followup.send("⚠️ Invalid EVM address format.", ephemeral=True); return
        if not self.snag_client or not self.snag_client._api_key: await interaction.followup.send("⚙️ Main API Client (New Loyalty System) is not available for quest history.", ephemeral=True); return
        processed_transactions, warning_message, total_earned = await self._fetch_and_process_quest_transactions(self.snag_client, target_address, quest_filter)
        final_message_content = warning_message
        if not processed_transactions:
            filter_msg_part = f" matching '{quest_filter}'" if quest_filter else ""; final_message_content += f"✅ No quest completions{filter_msg_part} found for `{target_address}` in the New Loyalty System."
            await interaction.edit_original_response(content=final_message_content, view=None, embed=None)
        else:
            view = QuestHistoryPaginatorView(interaction, processed_transactions, total_earned, target_address); view.message = await interaction.original_response()
            initial_page_data = await view._get_page_data(); initial_embed = await view._create_page_embed(initial_page_data)
            await interaction.edit_original_response(content=final_message_content if final_message_content else None, embed=initial_embed, view=view)


    async def handle_quest_stats_logic(self, interaction: discord.Interaction, address_val: str):
        # ... (код как был, вызывает self._fetch_and_process_quest_transactions) ...
        target_address = address_val.strip().lower()
        if not EVM_ADDRESS_PATTERN.match(target_address): await interaction.followup.send("⚠️ Invalid EVM address format. Please use `0x...`", ephemeral=True); return
        if not self.snag_client or not self.snag_client._api_key: await interaction.followup.send("⚙️ Main API Client (New Loyalty System) is not available for quest statistics.", ephemeral=True); return
        logger.info(f"User {interaction.user.id} requested quest statistics for wallet (Main System): {target_address}")
        completed_transactions, warning_msg_txn, total_matchsticks_earned = await self._fetch_and_process_quest_transactions(self.snag_client, target_address, quest_filter=None)
        num_total_completed_executions = len(completed_transactions)
        all_available_rules_api: List[Dict[str, Any]] = []; last_rule_id: Optional[str] = None; has_more_rules_pages = True; api_rule_page_count = 0; warning_msg_rules = ""
        while has_more_rules_pages and api_rule_page_count < MAX_API_PAGES_TO_FETCH:
            api_rule_page_count += 1
            rules_page_data = await self.snag_client.get_loyalty_rules(limit=PAGE_LIMIT, starting_after=last_rule_id)
            if not rules_page_data: warning_msg_rules += f"⚙️ Error fetching loyalty rules (Page {api_rule_page_count}). Stats might be incomplete.\n"; break
            current_page_rules = rules_page_data.get("data", []); has_more_rules_pages = rules_page_data.get("hasNextPage", False)
            if not isinstance(current_page_rules, list): has_more_rules_pages = False; continue
            if not current_page_rules: has_more_rules_pages = False; continue
            all_available_rules_api.extend(current_page_rules)
            last_rule_item = current_page_rules[-1]; last_rule_id = last_rule_item.get("id")
            if not last_rule_id: has_more_rules_pages = False; continue
            if has_more_rules_pages: await asyncio.sleep(API_REQUEST_DELAY)
        if api_rule_page_count >= MAX_API_PAGES_TO_FETCH and has_more_rules_pages: warning_msg_rules += f"⚠️ Loaded maximum rule pages ({MAX_API_PAGES_TO_FETCH}). Available quest stats might be incomplete.\n"
        total_available_quests_count = 0; max_possible_matchsticks = 0
        if all_available_rules_api:
            for rule in all_available_rules_api:
                if not (isinstance(rule, dict) and rule.get("name") and rule.get("deletedAt") is None and rule.get("hideInUi") is not True): continue
                total_available_quests_count +=1
                if rule.get("rewardType") == "points" and rule.get("loyaltyCurrencyId") == MATCHSTICKS_CURRENCY_ID:
                    try: max_possible_matchsticks += int(rule.get("amount", 0))
                    except (ValueError, TypeError): pass
        embed = discord.Embed(title=f"📊 Quest Statistics for: `{target_address}`", description="(New Loyalty System)", color=discord.Color.blue())
        embed.add_field(name="Total Quest Executions", value=f"**{num_total_completed_executions}**", inline=True)
        embed.add_field(name=f"Total Matchsticks Earned", value=f"**{total_matchsticks_earned}**", inline=True)
        if total_available_quests_count > 0:
            embed.add_field(name="Total Available Quests", value=f"**{total_available_quests_count}**", inline=True)
            if max_possible_matchsticks > 0 : embed.add_field(name=f"Max Possible Matchsticks", value=f"**{max_possible_matchsticks}**", inline=True)
        final_content_stats = (warning_msg_txn + warning_msg_rules).strip()
        if not embed.fields and not final_content_stats: final_content_stats = "No quest data found to generate statistics."
        await interaction.followup.send(content=final_content_stats if final_content_stats else None, embed=embed, ephemeral=True)

    async def handle_get_badges_logic(self, interaction: discord.Interaction, address_val: str): # Код как был
        target_address = address_val.strip().lower()
        if not EVM_ADDRESS_PATTERN.match(target_address): await interaction.followup.send("⚠️ Invalid EVM address format. Please use `0x...`", ephemeral=True); return
        if not self.snag_client or not self.snag_client._api_key: await interaction.followup.send("⚙️ Main API Client (New Loyalty System) is not available for badge lookup.", ephemeral=True); return
        logger.info(f"User {interaction.user.id} requested badges for wallet (Main System): {target_address}")
        all_user_badges: List[Dict[str, Any]] = []; last_badge_id: Optional[str] = None; has_more_pages = True; api_page_count = 0; warning_msg = ""
        while has_more_pages and api_page_count < MAX_API_PAGES_TO_FETCH:
            api_page_count += 1; logger.debug(f"Requesting badge page {api_page_count} for {target_address} (Main System)")
            badges_response = await self.snag_client.get_badges_by_wallet(wallet_address=target_address, limit=PAGE_LIMIT, starting_after=last_badge_id)
            if not badges_response: msg = warning_msg + f"⚙️ Error fetching badges from Main System (Page {api_page_count})."; await interaction.followup.send(msg, ephemeral=True); return
            current_page_data = badges_response.get("data", []); has_more_pages = badges_response.get("hasNextPage", False)
            if not isinstance(current_page_data, list): logger.warning(f"Badge data from Main System is not a list (Page {api_page_count})."); has_more_pages = False; continue
            if not current_page_data: logger.info(f"Badge page {api_page_count} from Main System is empty."); has_more_pages = False; continue
            all_user_badges.extend(current_page_data)
            last_badge_item = current_page_data[-1]; last_badge_id = last_badge_item.get("id")
            if not last_badge_id: logger.warning("Cannot get last badge ID from page. Stopping pagination."); has_more_pages = False; continue
            if has_more_pages: await asyncio.sleep(API_REQUEST_DELAY)
        if api_page_count >= MAX_API_PAGES_TO_FETCH and has_more_pages: warning_msg += f"⚠️ Loaded maximum badge pages ({MAX_API_PAGES_TO_FETCH}) from Main System. Badge list might be incomplete.\n"
        if not all_user_badges: msg = warning_msg + f"ℹ️ No badges found for wallet `{target_address}` in the New Loyalty System."; await interaction.followup.send(msg, ephemeral=True); return
        if len(all_user_badges) <= BADGES_PER_PAGE and not warning_msg :
            embed = discord.Embed(title=f"Badges for: `{target_address}` (WIP)", description="(New Loyalty System)", color=discord.Color.gold())
            for i, badge in enumerate(all_user_badges):
                badge_name = badge.get("name", "Unnamed Badge"); badge_desc = badge.get("description") or "No description."
                status_text = "✅ Associated"
                embed.add_field(name=f"🏅 {badge_name}", value=f"{badge_desc}\n*Status: {status_text}*", inline=False)
                if i == 0 :
                    badge_image_url = badge.get("imageUrl")
                    if badge_image_url: embed.set_thumbnail(url=badge_image_url)
            embed.set_footer(text=f"Total Badges: {len(all_user_badges)}")
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            view = BadgePaginatorView(interaction, all_user_badges, target_address); view.message = await interaction.original_response()
            initial_page_data = await view._get_page_data(); initial_embed = await view._create_page_embed(initial_page_data)
            await interaction.followup.send(content=warning_msg if warning_msg else None, embed=initial_embed, view=view, ephemeral=True)

# --- Обязательная функция setup ---
async def setup(bot: commands.Bot):
    if not hasattr(bot, 'snag_client') or not bot.snag_client:
        logger.error("ControlPanelCog cannot be loaded: Main SnagApiClient (bot.snag_client) is missing.")
        return
    await bot.add_cog(ControlPanelCog(bot))