# cogs/control_panel_cog.py
# --- ФИНАЛЬНАЯ ВЕРСИЯ ПОСЛЕ ВСЕХ ИСПРАВЛЕНИЙ ---
import discord
from discord.ext import commands
from discord import app_commands
import logging
import datetime
from datetime import timezone
import json
import asyncio
import math
import re
from typing import Dict, Any, Optional, List, Tuple

# --- ИМПОРТ КЛИЕНТА API И ЕГО ЭНДПОИНТОВ ---
from utils.snag_api_client import (
    SnagApiClient,
    ACCOUNTS_ENDPOINT,
    TRANSACTIONS_ENDPOINT,
    CURRENCIES_ENDPOINT,
    RULES_ENDPOINT,
)

logger = logging.getLogger(__name__)
EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")

# --- КОНСТАНТЫ ДЛЯ КОГА ---
PAGE_LIMIT = 1000
MAX_API_PAGES_TO_FETCH = 10
API_REQUEST_DELAY = 0.8
ITEMS_PER_PAGE = 8
VIEW_TIMEOUT = 300.0
# ---------------------


# --- Модальные Окна ---
class FindWalletModal(discord.ui.Modal, title="Find Wallet by Social Handle"):
    discord_input = discord.ui.TextInput(label='Discord Handle (Optional)',placeholder='username#1234 or username',required=False,style=discord.TextStyle.short,row=0,max_length=100)
    twitter_input = discord.ui.TextInput(label='Twitter/X Handle (Optional)',placeholder='@username or username',required=False,style=discord.TextStyle.short,row=1,max_length=100)
    def __init__(self, cog_instance): super().__init__(timeout=None); self.cog:"ControlPanelCog" = cog_instance
    async def on_submit(self, interaction: discord.Interaction): await interaction.response.defer(ephemeral=True, thinking=True); await self.cog.handle_find_wallet_logic(interaction, self.discord_input.value, self.twitter_input.value)
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"FindWalletModal Error: {error}", exc_info=True)
        try: await interaction.followup.send('An error occurred in the modal.', ephemeral=True)
        except discord.HTTPException: pass

class AddressForHistoryModal(discord.ui.Modal, title="Get Quest History by Address"):
    address_input = discord.ui.TextInput(label='EVM Wallet Address', placeholder='0x...', required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0)
    quest_filter_input = discord.ui.TextInput(label='Quest Name Filter (Optional)', placeholder='Enter keywords...', required=False, style=discord.TextStyle.short, max_length=100, row=1)
    def __init__(self, cog_instance): super().__init__(timeout=None); self.cog:"ControlPanelCog" = cog_instance
    async def on_submit(self, interaction: discord.Interaction): await interaction.response.defer(thinking=True, ephemeral=True); await self.cog._process_and_send_quest_history(interaction, self.address_input.value, self.quest_filter_input.value)
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"AddressHistoryModal Error: {error}", exc_info=True)
        try: await interaction.followup.send('An error occurred in the history modal.', ephemeral=True)
        except discord.HTTPException: pass

class AddressForSocialsModal(discord.ui.Modal, title="Find Socials by Wallet Address"):
    address_input = discord.ui.TextInput(label='EVM Wallet Address', placeholder='0x...', required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0)
    def __init__(self, cog_instance): super().__init__(timeout=None); self.cog:"ControlPanelCog" = cog_instance
    async def on_submit(self, interaction: discord.Interaction): await interaction.response.defer(thinking=True, ephemeral=True); await self.cog.handle_find_socials_logic(interaction, self.address_input.value)
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"AddressSocialsModal Error: {error}", exc_info=True)
        try: await interaction.followup.send('An error occurred in the socials modal.', ephemeral=True)
        except discord.HTTPException: pass

class BalanceCheckModal(discord.ui.Modal, title="Check All Balances by Wallet"):
    address_input = discord.ui.TextInput(label='EVM Wallet Address', placeholder='0x...', required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0)
    def __init__(self, cog_instance): super().__init__(timeout=None); self.cog:"ControlPanelCog" = cog_instance
    async def on_submit(self, interaction: discord.Interaction): await interaction.response.defer(thinking=True, ephemeral=True); await self.cog.handle_balance_check_logic(interaction, self.address_input.value)
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"BalanceCheckModal Error: {error}", exc_info=True)
        try: await interaction.followup.send('An error occurred in the balance modal.', ephemeral=True)
        except discord.HTTPException: pass

# --- Пагинатор для истории квестов ---
class QuestHistoryPaginatorView(discord.ui.View):
    current_page: int = 1
    sep: int = ITEMS_PER_PAGE

    def __init__(self, original_interaction: discord.Interaction, data: list, total_unique_quests: int, target_address: str):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.original_interaction = original_interaction
        self.data = data
        self.total_unique_quests = total_unique_quests
        self.target_address = target_address
        self.max_pages = math.ceil(len(self.data) / self.sep) if self.data else 1
        self.message: Optional[discord.Message] = None
        self._update_buttons()

    async def _get_page_data(self) -> list:
        base = (self.current_page - 1) * self.sep
        return self.data[base : base + self.sep]

    async def _create_page_embed(self, page_data: list) -> discord.Embed:
        embed = discord.Embed(title="Latest Completed Quests for:", description=f"`{self.target_address}` (excluding Check-ins)", color=discord.Color.green())
        if not page_data and self.current_page == 1: embed.description += "\n\nNo matching quests found."
        else:
            for tx in page_data:
                amount = tx.get("amount", "N/A"); created_at_str = tx.get("createdAt"); rule_name = tx.get("loyaltyTransaction", {}).get("loyaltyRule", {}).get("name", "Unknown Action").strip()
                date_formatted = self._format_datetime_static(created_at_str) # Используем статический метод ниже
                field_name = f"✅ {rule_name}"; field_value = f"**Earned:** `{amount}` | **Completed:** {date_formatted}"; embed.add_field(name=field_name, value=field_value, inline=False)
        embed.set_footer(text=f"Page {self.current_page} of {self.max_pages} | Total Unique Quests: {self.total_unique_quests}"); embed.timestamp = discord.utils.utcnow(); return embed

    @staticmethod
    def _format_datetime_static(datetime_str: str | None) -> str: # Определен внутри View
        if not datetime_str: return "Date unknown"
        # --- ВОССТАНОВЛЕНО ОПРЕДЕЛЕНИЕ ---
        formats_to_try = ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"]
        parsed_dt = None
        for fmt in formats_to_try:
            try: dt_str_cleaned = datetime_str.split('.')[0].replace('Z', ''); dt_obj = datetime.datetime.strptime(dt_str_cleaned, '%Y-%m-%dT%H:%M:%S'); parsed_dt = dt_obj.replace(tzinfo=timezone.utc); break
            except ValueError: continue
        if parsed_dt: return parsed_dt.strftime("%d/%m/%y") # Формат ДД/ММ/ГГ
        logger.warning(f"Could not parse date format: {datetime_str}"); return datetime_str

    def _update_buttons(self):
        for btn_name in ["first_page", "prev_page", "next_page", "last_page"]: btn = getattr(self, btn_name, None)
        if btn is not None:
            if btn_name in ["first_page", "prev_page"]: btn.disabled = self.current_page == 1
            else: btn.disabled = self.current_page >= self.max_pages
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_interaction.user.id: await interaction.response.send_message("Sorry, only the user who initiated can use these.", ephemeral=True); return False
        return True
    async def _send_page(self, interaction: discord.Interaction):
        if not interaction.response.is_done(): await interaction.response.defer()
        self._update_buttons(); page_data = await self._get_page_data(); embed = await self._create_page_embed(page_data)
        await interaction.edit_original_response(embed=embed, view=self)
    @discord.ui.button(label="|< First", style=discord.ButtonStyle.secondary, row=0)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button): self.current_page = 1; await self._send_page(interaction)
    @discord.ui.button(label="< Prev", style=discord.ButtonStyle.primary, row=0)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button): self.current_page -=1; await self._send_page(interaction)
    @discord.ui.button(label="Next >", style=discord.ButtonStyle.primary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button): self.current_page +=1; await self._send_page(interaction)
    @discord.ui.button(label="Last >|", style=discord.ButtonStyle.secondary, row=0)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button): self.current_page = self.max_pages; await self._send_page(interaction)
    async def on_timeout(self) -> None:
        if self.message:
            try: await self.message.edit(view=None); logger.info(f"History view for {self.target_address} timed out.")
            except discord.HTTPException as e: logger.error(f"Error removing view on timeout: {e}")

# --- View для панели управления ---
class InfoPanelView(discord.ui.View):
    def __init__(self, cog_instance): super().__init__(timeout=None); self.cog:"ControlPanelCog" = cog_instance

    async def _check_ranger_role(self, interaction: discord.Interaction) -> bool:
        """Checks if the interacting user has the 'Ranger' role."""
        if not isinstance(interaction.user, discord.Member): # Should not happen in guild context
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return False
        ranger_role = discord.utils.get(interaction.user.roles, name="Ranger")
        if not ranger_role:
            await interaction.response.send_message("⛔ You do not have the required role ('Ranger') to use this button.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Find Wallet by Social", style=discord.ButtonStyle.success, custom_id="info_panel:find_wallet", row=0)
    async def find_wallet_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction):
            return
        await interaction.response.send_modal(FindWalletModal(self.cog))

    @discord.ui.button(label="Task History by Wallet", style=discord.ButtonStyle.primary, custom_id="info_panel:history_by_wallet", row=1)
    async def task_history_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction):
            return
        await interaction.response.send_modal(AddressForHistoryModal(self.cog))

    @discord.ui.button(label="Find Socials by Wallet", style=discord.ButtonStyle.secondary, custom_id="info_panel:socials_by_wallet", row=2)
    async def find_socials_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction):
            return
        await interaction.response.send_modal(AddressForSocialsModal(self.cog))

    @discord.ui.button(label="Check Balances by Wallet", style=discord.ButtonStyle.danger, custom_id="info_panel:balance_by_wallet", row=3)
    async def check_balance_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction):
            return
        await interaction.response.send_modal(BalanceCheckModal(self.cog))

# --- Класс Кога ControlPanel ---
class ControlPanelCog(commands.Cog, name="Control Panel"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot; self.snag_client: Optional[SnagApiClient] = getattr(bot, 'snag_client', None)
        self._currency_cache: Optional[Dict[str, Dict[str, Any]]] = None; self._currency_cache_time: Optional[datetime.datetime] = None
        self._currency_cache_lock = asyncio.Lock(); logger.info(f"Cog '{self.__class__.__name__}' loaded.")
        if not self.snag_client: logger.error(f"SnagApiClient not found for {self.__class__.__name__}!")
    async def cog_unload(self): logger.info(f"Cog '{self.__class__.__name__}' unloaded.")
    @commands.Cog.listener("on_ready")
    async def on_ready_register_views(self):
        if not self.snag_client: logger.warning("Snag client missing."); return
        self.bot.add_view(InfoPanelView(self)); logger.info("Persistent InfoPanelView registered."); await self._get_currency_map()
    @commands.command(name="send_info_panel")
    @commands.has_any_role("Ranger") # This command already checks for Ranger role
    async def send_info_panel_command(self, ctx: commands.Context): embed = discord.Embed(title="ℹ️ Info Panel", description="Use buttons below.", color=discord.Color.purple()); await ctx.send(embed=embed, view=InfoPanelView(self)); logger.info(f"Panel sent in {ctx.channel.id}")
    @send_info_panel_command.error
    async def send_info_panel_error(self, ctx, error):
        if isinstance(error, commands.MissingAnyRole): await ctx.send("⛔ You do not have the required role ('Ranger')")
        else: logger.error(f"Error in send_info_panel: {error}", exc_info=True); await ctx.send("⚙️ An unexpected error occurred.")

    async def _get_currency_map(self, force_refresh: bool = False) -> Optional[Dict[str, Dict[str, Any]]]:
        if not self.snag_client: return None
        cache_duration = datetime.timedelta(minutes=5)
        # --- ИСПРАВЛЕНИЕ: Определяем now ДО блока ---
        now = discord.utils.utcnow()
        async with self._currency_cache_lock:
            if not force_refresh and self._currency_cache and self._currency_cache_time and (now - self._currency_cache_time) < cache_duration:
                return self._currency_cache
            logger.info("Refreshing currency map via API Client...")
            response_data = await self.snag_client.get_currencies()
            if response_data and isinstance(response_data.get("data"), list):
                self._currency_cache = {c['id']: c for c in response_data["data"] if c.get("id")}
                self._currency_cache_time = now # Используем 'now' определенный ранее
                logger.info(f"Currency map updated. Found {len(self._currency_cache)} currencies.")
                return self._currency_cache
            logger.error("Failed to parse currency data from client. Returning previous cache.")
            return self._currency_cache # Возвращаем старый кэш, если есть

    # --- Методы, вызываемые из модальных окон ---
    async def handle_find_wallet_logic(self, interaction: discord.Interaction, discord_h: Optional[str], twitter_h: Optional[str]):
        discord_h = discord_h.strip() if discord_h else None; twitter_h = twitter_h.strip() if twitter_h else None
        if twitter_h and twitter_h.startswith('@'): twitter_h = twitter_h[1:]
        if not discord_h and not twitter_h: await interaction.followup.send("Please enter a Discord or Twitter/X handle.",ephemeral=True); return
        identifier_type, identifier_value = (("discordUser", discord_h) if discord_h else ("twitterUser", twitter_h)) if discord_h or twitter_h else (None, None)
        if not identifier_type: await interaction.followup.send("Failed to process input.",ephemeral=True); return
        logger.info(f"User {interaction.user.id} looking up wallet via {identifier_type}: {identifier_value}")
        try: found_address = await self._find_wallet_by_social_api_filter(identifier_type, identifier_value); await interaction.followup.send(f"Wallet for {identifier_type} `{identifier_value}`: `{found_address}`" if found_address else f"Could not find wallet for {identifier_type} `{identifier_value}`.",ephemeral=True)
        except Exception: logger.exception(f"Error during wallet lookup by {identifier_type}"); await interaction.followup.send("Internal error during lookup.",ephemeral=True)

    async def handle_find_socials_logic(self, interaction: discord.Interaction, address_val: str):
        target_address = address_val.strip().lower()
        if not EVM_ADDRESS_PATTERN.match(target_address): await interaction.followup.send("⚠️ Invalid EVM address format.",ephemeral=True); return
        logger.info(f"User {interaction.user.id} requested socials for: {target_address}")
        try: socials_text = await self._find_socials_by_wallet(target_address); await interaction.followup.send(socials_text, ephemeral=True)
        except Exception: logger.exception(f"Error during socials lookup for {target_address}"); await interaction.followup.send("Internal error during lookup.",ephemeral=True)

    async def handle_balance_check_logic(self, interaction: discord.Interaction, address_val: str):
        target_address = address_val.strip().lower()
        if not EVM_ADDRESS_PATTERN.match(target_address): await interaction.followup.send("⚠️ Invalid EVM address format.",ephemeral=True); return
        logger.info(f"User {interaction.user.id} requested all balances for wallet {target_address}")
        try:
            result_message = await self._get_all_wallet_balances(target_address)
            if len(result_message) > 2000: result_message = result_message[:1997] + "..."
            await interaction.followup.send(result_message, ephemeral=True)
        except Exception: logger.exception(f"Error during all balances check for {target_address}"); await interaction.followup.send("⚙️ An internal error occurred.",ephemeral=True)

    # --- Методы для работы с API через snag_client ---
    async def _find_wallet_by_social_api_filter(self, handle_type: str, handle_value: str) -> Optional[str]:
        if not self.snag_client: return None; logger.info(f"Finding wallet for {handle_type}='{handle_value}' via API client...")
        account_data_response = await self.snag_client.get_account_by_social(handle_type, handle_value)
        if account_data_response and isinstance(account_data_response.get("data"), list) and account_data_response["data"]:
            account_data = account_data_response["data"][0]; user_info = account_data.get("user"); wallet_address = user_info.get("walletAddress") if isinstance(user_info, dict) else None
            if wallet_address: logger.info(f"Found wallet: {wallet_address}"); return wallet_address
        logger.warning(f"Wallet not found for {handle_type} {handle_value}"); return None

    async def _find_socials_by_wallet(self, target_address: str) -> str:
        if not self.snag_client: return "⚙️ API Client is not available."; logger.info(f"Finding socials for {target_address} via API client...")
        account_data_response = await self.snag_client.get_account_by_wallet(target_address)
        if not account_data_response: return "⚙️ Error contacting API or API key/config missing."
        if not isinstance(account_data_response.get("data"), list) or not account_data_response["data"]: return f"❌ No account data found for `{target_address}`."
        account_data = account_data_response["data"][0]; user_info = account_data.get("user", {}); metadata_list = user_info.get("userMetadata", [])
        display_name = "N/A"; discord_handle = None; twitter_handle = None; meta = None
        if isinstance(metadata_list, list) and metadata_list: meta = metadata_list[0]
        if isinstance(meta, dict): display_name = meta.get("displayName", "N/A"); discord_handle = meta.get("discordUser"); twitter_handle = meta.get("twitterUser");
        if twitter_handle and not twitter_handle.startswith('@'): twitter_handle = f"@{twitter_handle}"
        return (f"**Socials for:** `{target_address}`\n**Display Name:** {display_name}\n**Discord:** `{discord_handle or 'Not linked'}`\n**Twitter/X:** `{twitter_handle or 'Not linked'}`\n")

    async def _get_all_wallet_balances(self, wallet_address: str) -> str:
        if not self.snag_client:
            return "⚙️ API Client is not available."
        currency_map = await self._get_currency_map()
        if currency_map is None:
             return "⚠️ Error: Could not retrieve currency info. Check logs."
        logger.info(f"Requesting balances for {wallet_address} via API Client")
        acc_resp = await self.snag_client.get_all_accounts_for_wallet(wallet_address)
        if acc_resp and isinstance(acc_resp.get("data"), list):
            accounts = acc_resp["data"]
            if not accounts:
                return f"ℹ️ No balances found for `{wallet_address}`."
            lines = [f"💰 **Balances for:** `{wallet_address}`\n"]
            found = False
            for acc in accounts:
                cid = acc.get("loyaltyCurrencyId"); amt = acc.get("amount")
                if cid and amt is not None:
                    found = True; cinfo = currency_map.get(cid); name = cinfo.get("name", "Unknown") if cinfo else "Unknown"; sym = cinfo.get("symbol", "???") if cinfo else "???"
                    lines.append(f"- **{name} ({sym}):** `{amt}`")
            if found: # Возвращаем только если найдены валидные записи
                return "\n".join(lines)
            else:
                return f"ℹ️ No valid balance entries for `{wallet_address}`."
        logger.error(f"Failed to retrieve or parse balance data for {wallet_address}. Response: {acc_resp}")
        return "⚙️ Error retrieving balances. Check logs."

    # --- Метод для истории квестов ---
    async def _process_and_send_quest_history(self, interaction: discord.Interaction, target_address: str, quest_filter: Optional[str]):
        if not self.snag_client: await interaction.followup.send("⚙️ API Client is not available.", ephemeral=True); return
        all_fetched_transactions = []; last_transaction_id = None; has_more_pages = True; page_count = 0
        logger.info(f"Fetching transactions for {target_address} via API Client...")
        while has_more_pages and page_count < MAX_API_PAGES_TO_FETCH:
            page_count += 1; logger.info(f"Quest History: Requesting page {page_count}...")
            transaction_page_data = await self.snag_client.get_transaction_entries(wallet_address=target_address, limit=PAGE_LIMIT, starting_after=last_transaction_id)
            if not transaction_page_data: await interaction.followup.send("⚙️ Error fetching quest history page.", ephemeral=True); return
            current_page_transactions = transaction_page_data.get("data", []); has_more_pages = transaction_page_data.get("hasNextPage", False)
            if not isinstance(current_page_transactions, list): logger.warning("Quest History: 'data' not list."); has_more_pages = False; continue
            if not current_page_transactions: logger.info("Quest History: Empty page."); has_more_pages = False; continue
            all_fetched_transactions.extend(current_page_transactions); last_tx = current_page_transactions[-1]; last_transaction_id = last_tx.get('id')
            if not last_transaction_id: logger.warning("Quest History: Cannot get last ID."); has_more_pages = False; continue
            if page_count > 0 and has_more_pages: await asyncio.sleep(API_REQUEST_DELAY)
        warning_message = None
        if page_count >= MAX_API_PAGES_TO_FETCH and has_more_pages: warning_message = f"⚠️ Loaded max pages ({MAX_API_PAGES_TO_FETCH}). Results might be incomplete."
        logger.info(f"Finished fetching tx for {target_address}. Total raw: {len(all_fetched_transactions)}. Filtering...")
        latest_unique_quests = {}
        if all_fetched_transactions:
            all_fetched_transactions.sort(key=lambda x: x.get('createdAt', '0'))
            for tx in all_fetched_transactions:
                if tx.get("direction") != "credit": continue
                rule_info = tx.get("loyaltyTransaction", {}).get("loyaltyRule", {});
                if not isinstance(rule_info, dict): continue
                rule_type = rule_info.get("type"); rule_name = rule_info.get("name", "").strip()
                if not rule_name or rule_type == "check_in": continue
                if quest_filter and quest_filter.lower() not in rule_name.lower(): continue
                latest_unique_quests[rule_name.lower()] = tx
        final_quests_list = list(latest_unique_quests.values()); final_quests_list.sort(key=lambda x: x.get('createdAt', '0'), reverse=True)
        logger.info(f"Found {len(final_quests_list)} unique quests for {target_address}.")
        sent_warning_followup = False
        if warning_message: await interaction.followup.send(warning_message, ephemeral=True); sent_warning_followup = True
        if not final_quests_list:
            filter_msg = f" matching '{quest_filter}'" if quest_filter else ""; final_msg = f"✅ No unique completed quests{filter_msg} found for `{target_address}` (excluding Check-ins)."
            await interaction.edit_original_response(content=final_msg, view=None, embed=None)
        else:
            view = QuestHistoryPaginatorView(interaction, final_quests_list, len(final_quests_list), target_address)
            initial_page_data = await view._get_page_data(); initial_embed = await view._create_page_embed(initial_page_data)
            original_message = await interaction.edit_original_response(content=None, embed=initial_embed, view=view)
            if isinstance(original_message, (discord.InteractionMessage, discord.Message)): view.message = original_message

    # --- Статический Метод для форматирования даты ---
    # Определен здесь для использования в QuestHistoryPaginatorView._create_page_embed
    @staticmethod
    def _format_datetime(datetime_str: str | None) -> str:
        if not datetime_str: return "Date unknown"
        formats_to_try = ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"]
        parsed_dt = None
        for fmt in formats_to_try:
            try: dt_str_cleaned = datetime_str.split('.')[0].replace('Z', ''); dt_obj = datetime.datetime.strptime(dt_str_cleaned, '%Y-%m-%dT%H:%M:%S'); parsed_dt = dt_obj.replace(tzinfo=timezone.utc); break
            except ValueError: continue
        if parsed_dt: return parsed_dt.strftime("%d/%m/%y")
        logger.warning(f"Could not parse date format: {datetime_str}"); return datetime_str

# --- Обязательная функция setup ---
async def setup(bot: commands.Bot):
    await bot.add_cog(ControlPanelCog(bot))
# --- КОНЕЦ ФАЙЛА cogs/control_panel_cog.py ---