# cogs/control_panel_cog.py
import discord
from discord.ext import commands
import logging
import datetime 
from datetime import timezone
import asyncio
import math
import re
import os
from typing import Dict, Any, Optional, List, Tuple
from decimal import Decimal

from utils.snag_api_client import SnagApiClient
from cogs.block_checker_cog import BlockCheckModal
from utils.checks import is_prefix_admin_in_guild

logger = logging.getLogger(__name__)
EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")

# --- КОНСТАНТЫ ДЛЯ КОГА ---
PAGE_LIMIT = 1000 
MAX_API_PAGES_TO_FETCH = 20 # Увеличим на всякий случай, если транзакций много
API_REQUEST_DELAY = 0.3 # Можно немного уменьшить, если API позволяет
ITEMS_PER_PAGE = 10 
BADGES_PER_PAGE = 5 
VIEW_TIMEOUT = 300.0
MATCHSTICKS_CURRENCY_ID = os.getenv("MATCHSTICKS_CURRENCY_ID", "7f74ae35-a6e2-496a-83ea-5b2e18769560")
# ---------------------


# --- Модальные Окна ---
class FindWalletModal(discord.ui.Modal, title="Find Wallet by Social Handle"):
    discord_input = discord.ui.TextInput(label='Discord Handle (Optional)', placeholder='username#1234 or username', required=False, style=discord.TextStyle.short, row=0, max_length=100)
    twitter_input = discord.ui.TextInput(label='Twitter/X Handle (Optional)', placeholder='@username or username', required=False, style=discord.TextStyle.short, row=1, max_length=100)
    def __init__(self, cog_instance: "ControlPanelCog"): super().__init__(timeout=None); self.cog = cog_instance
    async def on_submit(self, interaction: discord.Interaction): await interaction.response.defer(ephemeral=True, thinking=True); await self.cog.handle_find_wallet_logic(interaction, self.discord_input.value, self.twitter_input.value)
    async def on_error(self, interaction: discord.Interaction, error: Exception): logger.error(f"FindWalletModal Error: {error}", exc_info=True); await interaction.followup.send('An error occurred in the modal.', ephemeral=True)

class AddressForHistoryModal(discord.ui.Modal, title="Get Transaction History by Address"):
    address_input = discord.ui.TextInput(label='EVM Wallet Address', placeholder='0x...', required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0)
    quest_filter_input = discord.ui.TextInput(label='Transaction/Rule Name Filter (Optional)', placeholder='Enter keywords...', required=False, style=discord.TextStyle.short, max_length=100, row=1)
    def __init__(self, cog_instance: "ControlPanelCog"): super().__init__(timeout=None); self.cog = cog_instance
    async def on_submit(self, interaction: discord.Interaction): await interaction.response.defer(thinking=True, ephemeral=True); await self.cog._process_and_send_transaction_history(interaction, self.address_input.value, self.quest_filter_input.value)
    async def on_error(self, interaction: discord.Interaction, error: Exception): logger.error(f"AddressHistoryModal Error: {error}", exc_info=True); await interaction.followup.send('An error occurred in the history modal.', ephemeral=True)

class AddressForSocialsModal(discord.ui.Modal, title="Find Socials by Wallet Address"):
    address_input = discord.ui.TextInput(label='EVM Wallet Address', placeholder='0x...', required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0)
    def __init__(self, cog_instance: "ControlPanelCog"): super().__init__(timeout=None); self.cog = cog_instance
    async def on_submit(self, interaction: discord.Interaction): await interaction.response.defer(thinking=True, ephemeral=True); await self.cog.handle_find_socials_logic(interaction, self.address_input.value)
    async def on_error(self, interaction: discord.Interaction, error: Exception): logger.error(f"AddressSocialsModal Error: {error}", exc_info=True); await interaction.followup.send('An error occurred in the socials modal.', ephemeral=True)

class BalanceCheckModal(discord.ui.Modal, title="Check All Balances by Wallet"):
    address_input = discord.ui.TextInput(label='EVM Wallet Address', placeholder='0x...', required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0)
    def __init__(self, cog_instance: "ControlPanelCog"): super().__init__(timeout=None); self.cog = cog_instance
    async def on_submit(self, interaction: discord.Interaction): await interaction.response.defer(thinking=True, ephemeral=True); await self.cog.handle_balance_check_logic(interaction, self.address_input.value)
    async def on_error(self, interaction: discord.Interaction, error: Exception): logger.error(f"BalanceCheckModal Error: {error}", exc_info=True); await interaction.followup.send('An error occurred in the balance modal.', ephemeral=True)

class AddressForBadgesModal(discord.ui.Modal, title="Get User Badges (wip)"):
    address_input = discord.ui.TextInput(label='EVM Wallet Address', placeholder='0x...', required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0)
    def __init__(self, cog_instance: "ControlPanelCog"): super().__init__(timeout=None); self.cog = cog_instance
    async def on_submit(self, interaction: discord.Interaction): await interaction.response.defer(thinking=True, ephemeral=True); await self.cog.handle_get_badges_logic(interaction, self.address_input.value)
    async def on_error(self, interaction: discord.Interaction, error: Exception): logger.error(f"AddressForBadgesModal Error: {error}", exc_info=True); await interaction.followup.send('An error occurred in the badges modal.', ephemeral=True)

class AddressForStatsModal(discord.ui.Modal, title="Get Quest Statistics"):
    address_input = discord.ui.TextInput(label='EVM Wallet Address', placeholder='0x...', required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0)
    def __init__(self, cog_instance: "ControlPanelCog"): super().__init__(timeout=None); self.cog = cog_instance
    async def on_submit(self, interaction: discord.Interaction): await interaction.response.defer(thinking=True, ephemeral=True); await self.cog.handle_quest_stats_logic(interaction, self.address_input.value)
    async def on_error(self, interaction: discord.Interaction, error: Exception): logger.error(f"AddressForStatsModal Error: {error}", exc_info=True); await interaction.followup.send('An error occurred in the quest stats modal.', ephemeral=True)


# --- Пагинатор TransactionHistoryPaginatorView ---
class TransactionHistoryPaginatorView(discord.ui.View):
    current_page: int = 1
    sep: int = ITEMS_PER_PAGE 
    def __init__(self, original_interaction: discord.Interaction, all_transactions: List[Dict[str, Any]], target_address: str, total_matchsticks_credits: Decimal, total_matchsticks_debits: Decimal):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.original_interaction = original_interaction
        self.all_transactions = all_transactions
        self.target_address = target_address
        self.total_matchsticks_credits = total_matchsticks_credits
        self.total_matchsticks_debits = total_matchsticks_debits
        self.net_matchsticks_total = self.total_matchsticks_credits - self.total_matchsticks_debits
        self.max_pages = math.ceil(len(self.all_transactions) / self.sep) if self.all_transactions else 1
        self.message: Optional[discord.Message] = None
        self._update_buttons()

    async def _get_page_data(self) -> List[Dict[str, Any]]:
        base = (self.current_page - 1) * self.sep
        return self.all_transactions[base : base + self.sep]

    async def _create_page_embed(self, page_transactions: List[Dict[str, Any]]) -> discord.Embed:
        embed = discord.Embed(title=f"Transaction History for:",
                              description=f"`{self.target_address}`",
                              color=discord.Color.blue()) 

        if not page_transactions and self.current_page == 1:
            embed.description += "\n\nNo relevant transactions found."
        else:
            for tx in page_transactions:
                amount_str = tx.get("amount", "N/A")
                created_at_str = tx.get("createdAt")
                
                # Логика получения имени, как в предыдущем рабочем варианте + fallback
                rule_name = "Unknown Transaction" # Имя по умолчанию
                loyalty_transaction_data = tx.get("loyaltyTransaction")
                if isinstance(loyalty_transaction_data, dict):
                    loyalty_rule_data = loyalty_transaction_data.get("loyaltyRule")
                    if isinstance(loyalty_rule_data, dict):
                        name_from_rule = loyalty_rule_data.get("name")
                        if name_from_rule and name_from_rule.strip():
                            rule_name = name_from_rule.strip()
                    
                    if rule_name == "Unknown Transaction" or not rule_name.strip(): # Если из правила не взяли
                        desc_from_lt = loyalty_transaction_data.get("description")
                        if desc_from_lt and desc_from_lt.strip():
                            rule_name = desc_from_lt.strip()
                
                if rule_name == "Unknown Transaction" or not rule_name.strip(): # Если все еще не нашли
                    desc_from_tx = tx.get("description")
                    if desc_from_tx and desc_from_tx.strip():
                        rule_name = desc_from_tx.strip()
                
                if not rule_name.strip(): # Финальная проверка на пустоту
                    rule_name = "Unnamed Transaction Entry"


                date_formatted = self._format_datetime_static(created_at_str)
                currency_id_tx = tx.get("loyaltyCurrencyId")
                currency_name_display = f"Currency ID: {currency_id_tx[:8]}"
                if currency_id_tx == MATCHSTICKS_CURRENCY_ID:
                    currency_name_display = "Matchsticks"
                
                direction = tx.get("direction", "unknown")
                icon = "⚙️" 
                action_verb = "Action"
                
                if direction == "credit":
                    icon = "✅" 
                    action_verb = "Received"
                elif direction == "debit":
                    icon = "➖"
                    action_verb = "debit"
                
                field_name = f"{icon} {rule_name}" # rule_name здесь всегда будет непустым
                field_value = f"**{action_verb}:** `{amount_str}` {currency_name_display}**Date:** {date_formatted}"
                embed.add_field(name=field_name, value=field_value, inline=False)
        
        footer_lines = [f"Page {self.current_page} of {self.max_pages}"]
        if self.total_matchsticks_credits > 0 or self.total_matchsticks_debits > 0:
            footer_lines.append(f"Credit Matchsticks : {self.total_matchsticks_credits}")
            footer_lines.append(f"Debit Matchsticks : {self.total_matchsticks_debits}")
            footer_lines.append(f"Total Matchsticks: {self.net_matchsticks_total}")
        
        embed.set_footer(text=" | ".join(footer_lines))
        embed.timestamp = discord.utils.utcnow()
        return embed

    @staticmethod
    def _format_datetime_static(datetime_str: Optional[str]) -> str:
        if not datetime_str: return "Date unknown"
        formats_to_try = ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"]
        parsed_dt = None
        for fmt in formats_to_try:
            try:
                dt_obj_naive = datetime.datetime.strptime(datetime_str.split('.')[0].replace('Z',''), '%Y-%m-%dT%H:%M:%S')
                parsed_dt = dt_obj_naive.replace(tzinfo=timezone.utc)
                break
            except ValueError: continue
        if parsed_dt: return parsed_dt.strftime("%d/%m/%y %H:%M")
        logger.warning(f"Could not parse date format: {datetime_str}"); return datetime_str

    def _update_buttons(self):
        self.first_page.disabled = self.current_page == 1; self.prev_page.disabled = self.current_page == 1
        self.next_page.disabled = self.current_page >= self.max_pages; self.last_page.disabled = self.current_page >= self.max_pages

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_interaction.user.id: await interaction.response.send_message("Sorry, only the user who initiated this can use these buttons.", ephemeral=True); return False
        return True

    async def _send_page(self, interaction: discord.Interaction):
        if not interaction.response.is_done(): await interaction.response.defer()
        self._update_buttons(); page_data = await self._get_page_data(); embed = await self._create_page_embed(page_data)
        if self.message: await self.message.edit(embed=embed, view=self)
        elif interaction.is_original_response(): await interaction.edit_original_response(embed=embed, view=self)
        else: await interaction.followup.edit_message(message_id=interaction.message.id if interaction.message else "@original", embed=embed, view=self)

    @discord.ui.button(label="|< First", style=discord.ButtonStyle.secondary, row=0, custom_id="txh_first_v1_final_rbk2")
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button): self.current_page = 1; await self._send_page(interaction)
    @discord.ui.button(label="< Prev", style=discord.ButtonStyle.primary, row=0, custom_id="txh_prev_v1_final_rbk2")
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 1: self.current_page -= 1
        await self._send_page(interaction)
    @discord.ui.button(label="Next >", style=discord.ButtonStyle.primary, row=0, custom_id="txh_next_v1_final_rbk2")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.max_pages: self.current_page += 1
        await self._send_page(interaction)
    @discord.ui.button(label="Last >|", style=discord.ButtonStyle.secondary, row=0, custom_id="txh_last_v1_final_rbk2")
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button): self.current_page = self.max_pages; await self._send_page(interaction)
    async def on_timeout(self) -> None:
        if self.message:
            try: await self.message.edit(view=None)
            except discord.HTTPException as e: logger.error(f"Error removing view on timeout for TransactionHistoryPaginatorView: {e}")

# BadgePaginatorView ... (без изменений, как в предыдущей полной версии)
class BadgePaginatorView(discord.ui.View): 
    current_page: int = 1; sep: int = BADGES_PER_PAGE
    def __init__(self, original_interaction: discord.Interaction, all_badges: List[Dict[str, Any]], target_address: str):
        super().__init__(timeout=VIEW_TIMEOUT); self.original_interaction = original_interaction; self.all_badges = all_badges; self.target_address = target_address
        self.max_pages = math.ceil(len(self.all_badges) / self.sep) if self.all_badges else 1; self.message: Optional[discord.Message] = None; self._update_buttons()
    async def _get_page_data(self) -> List[Dict[str, Any]]: base = (self.current_page - 1) * self.sep; return self.all_badges[base : base + self.sep]
    async def _create_page_embed(self, page_badges: List[Dict[str, Any]]) -> discord.Embed:
        embed = discord.Embed(title=f"Badges for: `{self.target_address}`", description=f"(New Loyalty System) - Page {self.current_page}/{self.max_pages}", color=discord.Color.gold())
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
    @discord.ui.button(label="|< First", style=discord.ButtonStyle.secondary, row=0, custom_id="badge_first_v4_ctrlpanel_rbk2")
    async def first_badge_page(self, interaction: discord.Interaction, button: discord.ui.Button): self.current_page = 1; await self._send_page(interaction)
    @discord.ui.button(label="< Prev", style=discord.ButtonStyle.primary, row=0, custom_id="badge_prev_v4_ctrlpanel_rbk2")
    async def prev_badge_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 1: self.current_page -= 1
        await self._send_page(interaction)
    @discord.ui.button(label="Next >", style=discord.ButtonStyle.primary, row=0, custom_id="badge_next_v4_ctrlpanel_rbk2")
    async def next_badge_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.max_pages: self.current_page += 1
        await self._send_page(interaction)
    @discord.ui.button(label="Last >|", style=discord.ButtonStyle.secondary, row=0, custom_id="badge_last_v4_ctrlpanel_rbk2")
    async def last_badge_page(self, interaction: discord.Interaction, button: discord.ui.Button): self.current_page = self.max_pages; await self._send_page(interaction)
    async def on_timeout(self) -> None:
        if self.message:
            try: await self.message.edit(view=None)
            except discord.HTTPException as e: logger.error(f"Error removing view on timeout for BadgePaginatorView: {e}")


# InfoPanelView ... (без изменений, ID кнопок могут остаться v7 или обновлены на v8 для консистентности)
class InfoPanelView(discord.ui.View):
    def __init__(self, cog_instance: "ControlPanelCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance
        # Получаем доступ к боту через ког, чтобы найти другой ког
        self.bot = cog_instance.bot
    async def _check_ranger_role(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member): await interaction.response.send_message("This command can only be used in a server.", ephemeral=True); return False
        ranger_role = discord.utils.get(interaction.guild.roles, name="Ranger")
        if not ranger_role: await interaction.response.send_message("⛔ The 'Ranger' role was not found on this server.", ephemeral=True); return False
        if ranger_role not in interaction.user.roles: await interaction.response.send_message("⛔ You do not have the required role ('Ranger') to use this button.", ephemeral=True); return False
        return True
    @discord.ui.button(label="Find Wallet by Social", style=discord.ButtonStyle.success, custom_id="info_panel:find_wallet_v8_final", row=0)
    async def find_wallet_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.send_modal(FindWalletModal(self.cog))
    @discord.ui.button(label="Transaction History by Wallet", style=discord.ButtonStyle.primary, custom_id="info_panel:history_by_wallet_v8_final", row=1)
    async def transaction_history_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.send_modal(AddressForHistoryModal(self.cog))
    @discord.ui.button(label="Find Socials by Wallet", style=discord.ButtonStyle.secondary, custom_id="info_panel:socials_by_wallet_v8_final", row=0)
    async def find_socials_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.send_modal(AddressForSocialsModal(self.cog))
    @discord.ui.button(label="Check Balances by Wallet", style=discord.ButtonStyle.danger, custom_id="info_panel:balance_by_wallet_v8_final", row=0)
    async def check_balance_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.send_modal(BalanceCheckModal(self.cog))
    @discord.ui.button(label="Get User Badges", style=discord.ButtonStyle.grey, custom_id="info_panel:get_user_badges_v8_final", row=2)
    async def get_user_badges_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.send_modal(AddressForBadgesModal(self.cog))
    @discord.ui.button(label="Quest Stats", style=discord.ButtonStyle.blurple, custom_id="info_panel:quest_stats_v8_final", row=1)
    async def quest_stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.send_modal(AddressForStatsModal(self.cog))
    @discord.ui.button(label="🚫 Block Status", style=discord.ButtonStyle.danger, custom_id="info_panel:block_check_v1", row=0)
    async def block_status_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction):
            return
        
        # Получаем ког "Block Checker" через экземпляр бота
        target_cog = self.bot.get_cog("Block Checker")
        if not target_cog:
            await interaction.response.send_message("Block Checker feature is temporarily unavailable.", ephemeral=True)
            return
            
        modal = BlockCheckModal(target_cog)
        await interaction.response.send_modal(modal)

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
        if not self.snag_client_legacy: logger.warning(f"Legacy SnagApiClient not found for {self.__class__.__name__}! Legacy API features might not work.")

    async def cog_unload(self): logger.info(f"Cog '{self.__class__.__name__}' unloaded.")
    
    @commands.Cog.listener("on_ready")
    async def on_ready_register_views(self):
        if not self.snag_client or not self.snag_client._api_key: logger.warning(f"{self.__class__.__name__}: Main Snag client missing or API key not set. InfoPanelView might not function correctly.")
        self.bot.add_view(InfoPanelView(self)); logger.info(f"{self.__class__.__name__}: Persistent InfoPanelView registered.")
        if self.snag_client and self.snag_client._api_key:
            await self._get_currency_map(include_deleted_currencies=True) 
        else: logger.warning(f"{self.__class__.__name__}: Could not pre-fetch currency map as main Snag client is not ready.")

    @commands.command(name="send_info_panel")
    @is_prefix_admin_in_guild()
    async def send_info_panel_command(self, ctx: commands.Context):
        embed = discord.Embed(title="ℹ️ Snag Loyalty Info Panel", description="Use the buttons below to query Snag Loyalty System.", color=discord.Color.purple());
        await ctx.send(embed=embed, view=InfoPanelView(self)); logger.info(f"Info Panel sent by {ctx.author.name} in channel {ctx.channel.id}")
    
    @send_info_panel_command.error
    async def send_info_panel_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingAnyRole): await ctx.send("⛔ You do not have the required role ('Ranger') to use this command.")
        else: logger.error(f"Error in send_info_panel_command: {error}", exc_info=True); await ctx.send("⚙️ An unexpected error occurred while trying to send the info panel.")

    async def _get_currency_map(self, force_refresh: bool = False, include_deleted_currencies: bool = False) -> Optional[Dict[str, Dict[str, Any]]]:
        if not self.snag_client or not self.snag_client._api_key: 
            logger.error("Cannot get currency map: Main SnagApiClient is not available or API key missing.")
            return self._currency_cache 
        cache_duration = datetime.timedelta(minutes=30); now = discord.utils.utcnow()
        async with self._currency_cache_lock:
            if not force_refresh and self._currency_cache and self._currency_cache_time and (now - self._currency_cache_time < cache_duration):
                logger.debug("Using cached currency map."); return self._currency_cache
            logger.info(f"Refreshing currency map from Main Snag API (include_deleted={include_deleted_currencies})...")
            response_data = await self.snag_client.get_currencies(limit=PAGE_LIMIT, include_deleted=include_deleted_currencies)
            if response_data and isinstance(response_data.get("data"), list):
                self._currency_cache = {c['id']: c for c in response_data["data"] if isinstance(c, dict) and c.get("id")}
                self._currency_cache_time = now; logger.info(f"Currency map updated. Found {len(self._currency_cache)} currencies from Main API.")
                return self._currency_cache
            logger.error("Failed to refresh currency map from Main API. Response was invalid or empty.")
            return self._currency_cache

    async def _find_wallet_by_social_api_filter(self, client: SnagApiClient, handle_type: str, handle_value: str) -> Optional[str]: # ... (без изменений) ...
        if not client or not client._api_key: logger.warning(f"Attempted to use an uninitialized or keyless API client ({getattr(client, '_client_name', 'UnknownClient')}) for social lookup."); return None
        logger.info(f"[{getattr(client, '_client_name', 'SnagClient')}] Finding wallet for {handle_type}='{handle_value}'"); account_data_response = await client.get_account_by_social(handle_type, handle_value)
        if account_data_response and isinstance(account_data_response.get("data"), list) and account_data_response["data"]:
            account_data = account_data_response["data"][0]; user_info = account_data.get("user"); wallet_address = user_info.get("walletAddress") if isinstance(user_info, dict) else None
            if wallet_address: logger.info(f"[{getattr(client, '_client_name', '')}] Found wallet: {wallet_address} for {handle_type} {handle_value}"); return wallet_address
        logger.warning(f"[{getattr(client, '_client_name', '')}] Wallet not found for {handle_type} {handle_value}. Response: {str(account_data_response)[:200]}"); return None
    async def _find_socials_by_wallet(self, client: SnagApiClient, target_address: str) -> str: # ... (без изменений) ...
        if not client or not client._api_key: return "⚙️ API Client instance is not available or keyless."
        logger.info(f"[{getattr(client, '_client_name', 'SnagClient')}] Finding socials for {target_address}"); account_data_response = await client.get_account_by_wallet(target_address)
        if not account_data_response: return "⚙️ Error contacting API or API key/config missing."
        if not isinstance(account_data_response.get("data"), list) or not account_data_response["data"]: return f"❌ No account data found for `{target_address}`."
        account_data = account_data_response["data"][0]; user_info = account_data.get("user", {}); metadata_list = user_info.get("userMetadata", []); display_name = "N/A"; discord_handle = None; twitter_handle = None
        if isinstance(metadata_list, list) and metadata_list:
            meta = metadata_list[0]
            if isinstance(meta, dict): display_name = meta.get("displayName", "N/A"); discord_handle = meta.get("discordUser"); twitter_handle = meta.get("twitterUser")
        if twitter_handle and not twitter_handle.startswith('@'): twitter_handle = f"@{twitter_handle}"
        return (f"**Display Name:** `{display_name}`\n"f"**Discord:** `{discord_handle or 'Not linked'}`\n"f"**Twitter/X:** `{twitter_handle or 'Not linked'}`")
    async def _get_all_wallet_balances_from_client(self, client: SnagApiClient, wallet_address: str, system_name: str) -> str: # ... (без изменений) ...
        if not client or not client._api_key: return f"⚙️ API Client for **{system_name}** is not available or keyless."
        currency_map = await self._get_currency_map(include_deleted_currencies=True) 
        if currency_map is None: return f"⚠️ Error: Could not retrieve currency info. Balances for **{system_name}** cannot be fully displayed."
        logger.info(f"[{getattr(client, '_client_name', 'SnagClient')}] Requesting balances for {wallet_address} for {system_name}"); acc_resp = await client.get_all_accounts_for_wallet(wallet_address)
        if acc_resp and isinstance(acc_resp.get("data"), list):
            accounts = acc_resp["data"]
            if not accounts: return f"ℹ️ No balances found for `{wallet_address}` in **{system_name}**."
            lines = [f"💰 **Balances for `{wallet_address}` ({system_name}):**"]; found_valid_balance = False
            for acc in accounts:
                currency_id = acc.get("loyaltyCurrencyId"); amount_val = acc.get("amount")
                if currency_id and amount_val is not None:
                    amount_str = str(amount_val); found_valid_balance = True; currency_info = currency_map.get(currency_id)
                    if currency_info:
                        currency_name = currency_info.get("name", f"Unknown Currency (ID: {currency_id[:8]})"); currency_symbol = currency_info.get("symbol", "")
                        display_name = f"{currency_name} ({currency_symbol})" if currency_symbol else currency_name
                        if currency_info.get("deletedAt"): display_name += " (Deleted Currency)"
                    else: display_name = f"Currency ID: {currency_id} (Not in map)"
                    lines.append(f"- **{display_name}:** `{amount_str}`")
            if found_valid_balance: return "\n".join(lines)
            else: return f"ℹ️ No valid balance entries found for `{wallet_address}` in **{system_name}**."
        logger.error(f"Failed to retrieve or parse balance data for {wallet_address} from {system_name} using {getattr(client, '_client_name', '')}. Response: {str(acc_resp)[:200]}"); return f"⚙️ Error retrieving balances from **{system_name}**. Check logs."
    async def handle_find_wallet_logic(self, interaction: discord.Interaction, discord_h: Optional[str], twitter_h: Optional[str]): # ... (без изменений) ...
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
        response_lines = [];
        if found_address_legacy: response_lines.append(f"**Old Loyalty System Wallet:** `{found_address_legacy}`")
        if found_address_main: response_lines.append(f"**New Loyalty System Wallet:** `{found_address_main}`")
        if not response_lines: await interaction.followup.send(f"Could not find wallet for {identifier_type} `{identifier_value}` in either loyalty system.", ephemeral=True)
        else: await interaction.followup.send("\n".join(response_lines), ephemeral=True)
    async def handle_find_socials_logic(self, interaction: discord.Interaction, address_val: str): # ... (без изменений) ...
        target_address = address_val.strip().lower()
        if not EVM_ADDRESS_PATTERN.match(target_address): await interaction.followup.send("⚠️ Invalid EVM address format. Please use `0x...`", ephemeral=True); return
        logger.info(f"User {interaction.user.id} requested socials for wallet: {target_address}")
        socials_text_legacy = "Old Loyalty System: Socials could not be fetched (API client unavailable or error)."; socials_text_main = "New Loyalty System: Socials could not be fetched (API client unavailable or error)."
        if self.snag_client_legacy and self.snag_client_legacy._api_key: legacy_socials = await self._find_socials_by_wallet(self.snag_client_legacy, target_address); socials_text_legacy = f"**--- Old Loyalty System ---**\n{legacy_socials}"
        else: logger.warning(f"Legacy SnagApiClient not available for socials lookup of {target_address}.")
        if self.snag_client and self.snag_client._api_key: main_socials = await self._find_socials_by_wallet(self.snag_client, target_address); socials_text_main = f"**--- New Loyalty System ---**\n{main_socials}"
        else: logger.warning(f"Main SnagApiClient not available for socials lookup of {target_address}.")
        full_response = f"{socials_text_legacy}\n\n{socials_text_main}".strip();
        if len(full_response) > 1950: full_response = full_response[:1950] + "..."
        await interaction.followup.send(full_response if full_response else "No data found for this wallet in either system.", ephemeral=True)
    async def handle_balance_check_logic(self, interaction: discord.Interaction, address_val: str): # ... (без изменений) ...
        target_address = address_val.strip().lower()
        if not EVM_ADDRESS_PATTERN.match(target_address): await interaction.followup.send("⚠️ Invalid EVM address format. Please use `0x...`", ephemeral=True); return
        logger.info(f"User {interaction.user.id} requested all balances for wallet: {target_address}"); results = []
        if self.snag_client and self.snag_client._api_key: main_balances_msg = await self._get_all_wallet_balances_from_client(self.snag_client, target_address, "New Loyalty System"); results.append(main_balances_msg)
        else: results.append("ℹ️ Main Loyalty System (New) API client not available."); logger.warning(f"Main SnagApiClient not available for balance check of {target_address}.")
        if self.snag_client_legacy and self.snag_client_legacy._api_key: legacy_balances_msg = await self._get_all_wallet_balances_from_client(self.snag_client_legacy, target_address, "Old Loyalty System"); results.append(legacy_balances_msg)
        else: results.append("ℹ️ Legacy Loyalty System (Old) API client not available."); logger.warning(f"Legacy SnagApiClient not available for balance check of {target_address}.")
        full_response = "\n\n".join(results).strip();
        if not full_response: full_response = "⚙️ No API clients available to check balances."
        if len(full_response) > 1950: full_response = full_response[:1950] + "..."
        await interaction.followup.send(full_response, ephemeral=True)
        
    async def _fetch_and_process_all_transactions(
        self, client: SnagApiClient, target_address: str, name_filter: Optional[str] = None,
        exclude_deleted_curr_flag: bool = False 
    ) -> Tuple[List[Dict[str, Any]], str, Decimal, Decimal]:
        all_fetched_transactions: List[Dict[str, Any]] = []
        last_transaction_id: Optional[str] = None
        has_more_pages = True; api_page_count = 0; warning_message = ""
        total_matchsticks_credits_processed = Decimal('0')
        total_matchsticks_debits_processed = Decimal('0')
        client_name = getattr(client, '_client_name', 'SnagClient')
        logger.info(f"[{client_name}] Fetching all transaction_entries for {target_address} (excludeDeletedCurrency={exclude_deleted_curr_flag})...")
        
        while has_more_pages and api_page_count < MAX_API_PAGES_TO_FETCH:
            api_page_count += 1
            transaction_page_data = await client.get_transaction_entries(
                wallet_address=target_address, limit=PAGE_LIMIT, 
                starting_after=last_transaction_id,
                exclude_deleted_currency=exclude_deleted_curr_flag 
            )
            if not transaction_page_data:
                warning_message += f"⚙️ Error fetching transaction history (Page {api_page_count}) from {client_name}.\n"; break 
            current_page_transactions = transaction_page_data.get("data", [])
            has_more_pages = transaction_page_data.get("hasNextPage", False)
            if not isinstance(current_page_transactions, list):
                logger.warning(f"[{client_name}] Transactions: 'data' not a list (Page {api_page_count}). Stopping."); has_more_pages = False; continue
            if not current_page_transactions:
                logger.info(f"[{client_name}] Transactions: Page {api_page_count} is empty."); has_more_pages = False; continue

            for tx in current_page_transactions:
                # БОЛЕЕ НАДЕЖНАЯ ЛОГИКА ПОЛУЧЕНИЯ ИМЕНИ/ОПИСАНИЯ
                tx_name_for_filter = "Unknown Transaction" # Имя по умолчанию
                
                loyalty_transaction_data = tx.get("loyaltyTransaction")
                if isinstance(loyalty_transaction_data, dict):
                    loyalty_rule_data = loyalty_transaction_data.get("loyaltyRule") # Может вернуть None, если ключ есть, но значение null
                    if isinstance(loyalty_rule_data, dict): # Проверяем, что это словарь
                        name_from_rule = loyalty_rule_data.get("name")
                        if name_from_rule and name_from_rule.strip():
                            tx_name_for_filter = name_from_rule.strip()
                    
                    if tx_name_for_filter == "Unknown Transaction" or not tx_name_for_filter.strip():
                        desc_from_lt = loyalty_transaction_data.get("description")
                        if desc_from_lt and desc_from_lt.strip():
                            tx_name_for_filter = desc_from_lt.strip()
                
                if tx_name_for_filter == "Unknown Transaction" or not tx_name_for_filter.strip():
                    desc_from_tx = tx.get("description")
                    if desc_from_tx and desc_from_tx.strip():
                        tx_name_for_filter = desc_from_tx.strip()
                
                if not tx_name_for_filter.strip(): # Если все равно пусто
                    tx_name_for_filter = "Unnamed Transaction Entry"

                # Фильтр по имени, если он есть и не пустой
                if name_filter and name_filter.strip():
                    if name_filter.strip().lower() not in tx_name_for_filter.lower():
                        continue 
                
                all_fetched_transactions.append(tx)

                if tx.get("loyaltyCurrencyId") == MATCHSTICKS_CURRENCY_ID:
                    try:
                        amount = Decimal(str(tx.get("amount", "0")))
                        if tx.get("direction") == "credit": total_matchsticks_credits_processed += amount
                        elif tx.get("direction") == "debit": total_matchsticks_debits_processed += amount
                    except: pass 
            
            if current_page_transactions:
                last_tx = current_page_transactions[-1]; last_transaction_id = last_tx.get('id')
                if not last_transaction_id: logger.warning(f"[{client_name}] No last ID. Stopping."); has_more_pages = False; continue
            if has_more_pages: await asyncio.sleep(API_REQUEST_DELAY)
        
        if api_page_count >= MAX_API_PAGES_TO_FETCH and has_more_pages:
            warning_message += f"⚠️ Loaded max pages ({MAX_API_PAGES_TO_FETCH}). History might be incomplete.\n"
        
        all_fetched_transactions.sort(key=lambda x: x.get('createdAt', '0'), reverse=True)
        logger.info(f"[{client_name}] Found {len(all_fetched_transactions)} transactions for {target_address} after applying name filter (if any).") # Изменен лог
        return all_fetched_transactions, warning_message.strip(), total_matchsticks_credits_processed, total_matchsticks_debits_processed

    async def _process_and_send_transaction_history(self, interaction: discord.Interaction, target_address_str: str, name_filter: Optional[str]):
        target_address = target_address_str.strip().lower()
        if not EVM_ADDRESS_PATTERN.match(target_address): await interaction.followup.send("⚠️ Invalid EVM address format.", ephemeral=True); return
        if not self.snag_client or not self.snag_client._api_key: await interaction.followup.send("⚙️ Main API Client not available.", ephemeral=True); return

        processed_transactions, warning_message, total_credits, total_debits = await self._fetch_and_process_all_transactions(
            self.snag_client, target_address, name_filter, exclude_deleted_curr_flag=False 
        )
        
        final_message_content = warning_message
        if not processed_transactions:
            filter_msg_part = f" matching '{name_filter.strip()}'" if name_filter and name_filter.strip() else ""
            final_message_content += f"✅ No transactions{filter_msg_part} found for `{target_address}`."
            # Важно: если interaction уже был использован для defer, нужен followup. Если это первый ответ - response.
            # Так как defer был в on_submit модалки, здесь всегда должен быть followup или edit_original_response
            try:
                await interaction.edit_original_response(content=final_message_content, view=None, embed=None)
            except discord.NotFound: # Если оригинальное сообщение было удалено или что-то пошло не так
                await interaction.followup.send(content=final_message_content, ephemeral=True)
        else:
            view = TransactionHistoryPaginatorView(interaction, processed_transactions, target_address, total_credits, total_debits)
            # Пытаемся получить оригинальное сообщение, чтобы его отредактировать
            try:
                original_message = await interaction.original_response()
                view.message = original_message
                initial_page_data = await view._get_page_data(); initial_embed = await view._create_page_embed(initial_page_data)
                await interaction.edit_original_response(content=final_message_content if final_message_content else None, embed=initial_embed, view=view)
            except discord.NotFound: # Если не удалось получить original_response (например, время вышло)
                 logger.warning("Could not get original_response for transaction history, sending as new followup.")
                 # В этом случае мы не можем использовать view.message, т.к. нет сообщения для редактирования кнопками
                 # Лучше просто отправить первую страницу без кнопок или с кнопками, но они не будут работать для старых сообщений.
                 # Для простоты, отправим первую страницу.
                 initial_page_data = await view._get_page_data(); initial_embed = await view._create_page_embed(initial_page_data)
                 # Создаем новый view, так как старый view.message не будет установлен
                 new_view_for_followup = TransactionHistoryPaginatorView(interaction, processed_transactions, target_address, total_credits, total_debits)
                 followup_message = await interaction.followup.send(content=final_message_content if final_message_content else None, embed=initial_embed, view=new_view_for_followup, ephemeral=True)
                 new_view_for_followup.message = followup_message


    async def handle_quest_stats_logic(self, interaction: discord.Interaction, address_val: str): # ... (без изменений) ...
        target_address = address_val.strip().lower()
        if not EVM_ADDRESS_PATTERN.match(target_address): await interaction.followup.send("⚠️ Invalid EVM address format. Please use `0x...`", ephemeral=True); return
        if not self.snag_client or not self.snag_client._api_key: await interaction.followup.send("⚙️ Main API Client (New Loyalty System) is not available for quest statistics.", ephemeral=True); return
        logger.info(f"User {interaction.user.id} requested quest statistics for wallet (Main System): {target_address}")
        all_txns, warning_msg_txn, total_matchsticks_credits, _ = await self._fetch_and_process_all_transactions(self.snag_client, target_address, name_filter=None, exclude_deleted_curr_flag=False)
        completed_quest_executions = [tx for tx in all_txns if tx.get("direction") == "credit" and (tx.get("loyaltyTransaction") or {}).get("loyaltyRule", {}).get("name")]
        num_total_completed_executions = len(completed_quest_executions)
        all_available_rules_api: List[Dict[str, Any]] = []; last_rule_id: Optional[str] = None; has_more_rules_pages = True; api_rule_page_count = 0; warning_msg_rules = ""
        while has_more_rules_pages and api_rule_page_count < MAX_API_PAGES_TO_FETCH:
            api_rule_page_count += 1; rules_page_data = await self.snag_client.get_loyalty_rules(limit=PAGE_LIMIT, starting_after=last_rule_id, include_deleted=False)
            if not rules_page_data: warning_msg_rules += f"⚙️ Error fetching loyalty rules (Page {api_rule_page_count}).\n"; break
            current_page_rules = rules_page_data.get("data", []); has_more_rules_pages = rules_page_data.get("hasNextPage", False)
            if not isinstance(current_page_rules, list): has_more_rules_pages = False; continue
            if not current_page_rules: has_more_rules_pages = False; continue
            all_available_rules_api.extend(rule for rule in current_page_rules if isinstance(rule, dict) and not rule.get("deletedAt") and rule.get("hideInUi") is not True and rule.get("isActive") is True)
            if current_page_rules: last_rule_item = current_page_rules[-1]; last_rule_id = last_rule_item.get("id");
            if not last_rule_id: has_more_rules_pages = False; continue
            if has_more_rules_pages: await asyncio.sleep(API_REQUEST_DELAY)
        if api_rule_page_count >= MAX_API_PAGES_TO_FETCH and has_more_rules_pages: warning_msg_rules += f"⚠️ Max rule pages loaded.\n"
        total_available_quests_count = len(all_available_rules_api); max_possible_matchsticks = Decimal('0')
        for rule in all_available_rules_api:
            if rule.get("rewardType") == "points" and rule.get("loyaltyCurrencyId") == MATCHSTICKS_CURRENCY_ID:
                try: max_possible_matchsticks += Decimal(str(rule.get("amount", 0)))
                except: pass
        embed = discord.Embed(title=f"📊 Quest Statistics for: `{target_address}`", description="(New Loyalty System)", color=discord.Color.blue())
        embed.add_field(name="Total Quest Executions (Credits)", value=f"**{num_total_completed_executions}**", inline=True)
        embed.add_field(name=f"Total Matchsticks Earned (Credits)", value=f"**{total_matchsticks_credits}**", inline=True)
        if total_available_quests_count > 0:
            embed.add_field(name="Total Active Public Quests", value=f"**{total_available_quests_count}**", inline=True)
            if max_possible_matchsticks > 0 : embed.add_field(name=f"Max Possible Matchsticks from Active", value=f"**{max_possible_matchsticks}**", inline=True)
        final_content_stats = (warning_msg_txn + warning_msg_rules).strip()
        if not embed.fields and not final_content_stats: final_content_stats = "No quest data found."
        await interaction.followup.send(content=final_content_stats if final_content_stats else None, embed=embed, ephemeral=True)
        
    async def handle_get_badges_logic(self, interaction: discord.Interaction, address_val: str): # ... (без изменений) ...
        target_address = address_val.strip().lower()
        if not EVM_ADDRESS_PATTERN.match(target_address): await interaction.followup.send("⚠️ Invalid EVM address format.", ephemeral=True); return
        if not self.snag_client or not self.snag_client._api_key: await interaction.followup.send("⚙️ Main API Client not available.", ephemeral=True); return
        logger.info(f"User {interaction.user.id} requested badges for wallet (Main System): {target_address}")
        all_user_badges: List[Dict[str, Any]] = []; last_badge_id: Optional[str] = None; has_more_pages = True; api_page_count = 0; warning_msg = ""
        while has_more_pages and api_page_count < MAX_API_PAGES_TO_FETCH:
            api_page_count += 1; badges_response = await self.snag_client.get_badges_by_wallet(wallet_address=target_address, limit=PAGE_LIMIT, starting_after=last_badge_id, include_deleted=False)
            if not badges_response: msg = warning_msg + f"⚙️ Error fetching badges (Page {api_page_count})."; await interaction.followup.send(msg, ephemeral=True); return
            current_page_data = badges_response.get("data", []); has_more_pages = badges_response.get("hasNextPage", False)
            if not isinstance(current_page_data, list): logger.warning(f"Badge data not a list (Page {api_page_count})."); has_more_pages = False; continue
            if not current_page_data: logger.info(f"Badge page {api_page_count} empty."); has_more_pages = False; continue
            all_user_badges.extend(badge for badge in current_page_data if not badge.get("deletedAt"))
            if current_page_data: last_badge_item = current_page_data[-1]; last_badge_id = last_badge_item.get("id");
            if not last_badge_id: logger.warning("No last badge ID. Stopping."); has_more_pages = False; continue
            if has_more_pages: await asyncio.sleep(API_REQUEST_DELAY)
        if api_page_count >= MAX_API_PAGES_TO_FETCH and has_more_pages: warning_msg += f"⚠️ Max badge pages loaded.\n"
        if not all_user_badges: msg = warning_msg + f"ℹ️ No active badges found for `{target_address}`."; await interaction.followup.send(msg, ephemeral=True); return
        view = BadgePaginatorView(interaction, all_user_badges, target_address); 
        try:
            original_message = await interaction.original_response()
            view.message = original_message
            initial_page_data = await view._get_page_data(); initial_embed = await view._create_page_embed(initial_page_data)
            await interaction.edit_original_response(content=warning_msg if warning_msg else None, embed=initial_embed, view=view)
        except discord.NotFound:
            logger.warning("Could not get original_response for badge history, sending as new followup.")
            initial_page_data = await view._get_page_data(); initial_embed = await view._create_page_embed(initial_page_data)
            new_view_for_followup = BadgePaginatorView(interaction, all_user_badges, target_address)
            followup_message = await interaction.followup.send(content=warning_msg if warning_msg else None, embed=initial_embed, view=new_view_for_followup, ephemeral=True)
            new_view_for_followup.message = followup_message


# --- Обязательная функция setup ---
async def setup(bot: commands.Bot):
    if not hasattr(bot, 'snag_client') or not bot.snag_client:
        logger.error("ControlPanelCog cannot be loaded: Main SnagApiClient (bot.snag_client) is missing.")
        return
    await bot.add_cog(ControlPanelCog(bot))