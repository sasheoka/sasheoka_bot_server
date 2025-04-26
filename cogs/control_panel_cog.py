# cogs/user_lookup_cog.py
# --- ПОЛНЫЙ КОД ФАЙЛА С ИСПРАВЛЕННЫМ ВЫЗОВОМ МОДАЛКИ (Версия 2024-04-24 v19) ---
import discord
from discord.ext import commands
from discord import app_commands # Нужно для модальных окон
import aiohttp
import logging
from datetime import datetime, timezone
import json
import asyncio
import math
import re

logger = logging.getLogger(__name__)
EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")

# --- API Constants ---
SNAG_API_BASE_URL = "https://admin.snagsolutions.io"
ACCOUNTS_ENDPOINT = "/api/loyalty/accounts"
TRANSACTIONS_ENDPOINT = "/api/loyalty/transaction_entries"
SNAG_API_KEY_HEADER = "X-API-KEY"
PAGE_LIMIT = 1000
MAX_PAGES_TO_FETCH = 20
API_REQUEST_DELAY = 2.0
# --- Discord Pagination ---
ITEMS_PER_PAGE = 10
VIEW_TIMEOUT = 300.0
# ---------------------

# --- Модальное окно для ввода Discord Handle ---
class DiscordHandleModal(discord.ui.Modal, title='Find Wallet by Discord Handle'):
    handle_input = discord.ui.TextInput(label='Discord Handle (e.g., username#1234)', required=True, style=discord.TextStyle.short, max_length=100)
    def __init__(self, cog_instance): super().__init__(); self.cog: UserLookupCog = cog_instance
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        discord_handle = self.handle_input.value.strip()
        if not discord_handle: await interaction.followup.send("No handle entered.", ephemeral=True); return
        logger.info(f"User {interaction.user.id} looking up handle: {discord_handle}")
        try:
            found_address = await self.cog._find_wallet_by_discord_handle(discord_handle)
            if found_address: await interaction.followup.send(f"Wallet address for `{discord_handle}`: `{found_address}`", ephemeral=True)
            else: await interaction.followup.send(f"Could not find wallet for handle `{discord_handle}`.", ephemeral=True)
        except Exception as e: logger.exception("Error during handle lookup"); await interaction.followup.send("Internal error during lookup.", ephemeral=True)
    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None: logger.error(f"Modal Error: {error}"); await interaction.followup.send('Modal error.', ephemeral=True)

# --- Модальное окно для ввода EVM адреса для ИСТОРИИ ---
class AddressForHistoryModal(discord.ui.Modal, title='Get Quest History by Address'):
    address_input = discord.ui.TextInput(label='EVM Wallet Address', placeholder='0x...', required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0)
    quest_filter_input = discord.ui.TextInput(label='Quest Name Filter (Optional)', placeholder='Enter keywords to filter quests...', required=False, style=discord.TextStyle.short, max_length=100, row=1)
    # Принимаем экземпляр кога при создании
    def __init__(self, cog_instance):
         super().__init__()
         self.cog: UserLookupCog = cog_instance # Сохраняем ког

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=False) # Публичный ответ
        target_address = self.address_input.value.strip().lower()
        quest_filter = self.quest_filter_input.value.strip() if self.quest_filter_input.value else None # Получаем фильтр
        if not EVM_ADDRESS_PATTERN.match(target_address):
            await interaction.followup.send(f"⚠️ Invalid EVM address format: `{self.address_input.value}`", ephemeral=True); return
        log_filter_msg = f" with filter '{quest_filter}'" if quest_filter else ""
        logger.info(f"User {interaction.user.id} requested history for: {target_address}{log_filter_msg}")
        await self.cog._process_and_send_quest_history(interaction, target_address, quest_filter) # Вызываем логику
    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None: logger.error(f"Address History Modal Error: {error}"); await interaction.followup.send("Modal error.", ephemeral=True)

# --- Класс Пагинатора для истории квестов ---
class QuestHistoryPaginatorView(discord.ui.View):
    current_page : int = 1; sep : int = ITEMS_PER_PAGE
    def __init__(self, original_interaction: discord.Interaction, data: list, total_unique_quests: int, target_address: str):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.original_interaction = original_interaction; self.data = data; self.total_unique_quests = total_unique_quests
        self.target_address = target_address; self.max_pages = math.ceil(len(self.data) / self.sep) if self.data else 1
        self.message: discord.Message | None = None; self._update_buttons()
    async def _get_page_data(self) -> list: base = (self.current_page - 1) * self.sep; return self.data[base:base + self.sep]
    async def _create_page_embed(self, page_data: list) -> discord.Embed:
        embed = discord.Embed(title="Latest Completed Quests for:", description=f"`{self.target_address}` (excluding Check-ins)", color=discord.Color.green())
        if not page_data and self.current_page == 1: embed.description += "\n\nNo matching quests found."
        else:
            for tx in page_data:
                amount = tx.get("amount", 0); created_at_str = tx.get("createdAt")
                rule_name = tx.get("loyaltyTransaction", {}).get("loyaltyRule", {}).get("name", "Unknown Action").strip()
                date_formatted = UserLookupCog._format_datetime(created_at_str)
                field_name = f"✅ {rule_name}"; field_value = f"**Earned:** `{amount}` | **Completed:** {date_formatted}"
                embed.add_field(name=field_name, value=field_value, inline=False)
        embed.set_footer(text=f"Page {self.current_page} of {self.max_pages} | Total Unique Quests: {self.total_unique_quests}")
        embed.timestamp = discord.utils.utcnow(); return embed
    def _update_buttons(self) -> None:
        if hasattr(self, 'first_page'): self.first_page.disabled = self.current_page == 1
        if hasattr(self, 'prev_page'): self.prev_page.disabled = self.current_page == 1
        if hasattr(self, 'next_page'): self.next_page.disabled = self.current_page >= self.max_pages
        if hasattr(self, 'last_page'): self.last_page.disabled = self.current_page >= self.max_pages
    async def show_current_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self._update_buttons(); page_data = await self._get_page_data(); embed = await self._create_page_embed(page_data)
        await interaction.edit_original_response(embed=embed, view=self)
    @discord.ui.button(label="|< First", style=discord.ButtonStyle.secondary, row=0)
    async def first_page(self, i: discord.Interaction, b: discord.ui.Button):
        if self.current_page != 1: self.current_page = 1; await self.show_current_page(i)
        else: await i.response.defer()
    @discord.ui.button(label="< Previous", style=discord.ButtonStyle.primary, row=0)
    async def prev_page(self, i: discord.Interaction, b: discord.ui.Button):
        if self.current_page > 1: self.current_page -= 1; await self.show_current_page(i)
        else: await i.response.defer()
    @discord.ui.button(label="Next >", style=discord.ButtonStyle.primary, row=0)
    async def next_page(self, i: discord.Interaction, b: discord.ui.Button):
        if self.current_page < self.max_pages: self.current_page += 1; await self.show_current_page(i)
        else: await i.response.defer()
    @discord.ui.button(label="Last >|", style=discord.ButtonStyle.secondary, row=0)
    async def last_page(self, i: discord.Interaction, b: discord.ui.Button):
        if self.current_page != self.max_pages: self.current_page = self.max_pages; await self.show_current_page(i)
        else: await i.response.defer()
    async def on_timeout(self) -> None:
        if self.message:
            try:
                changed = False; view_children = list(self.children)
                for item in view_children:
                    if isinstance(item, discord.ui.Button) and not item.disabled: item.disabled = True; changed = True
                if changed: await self.message.edit(view=self); logger.info(f"History view {self.message.id} timed out.")
            except Exception as e: logger.error(f"Error disabling history view {self.message.id}: {e}")
# --- Конец QuestHistoryPaginatorView ---


# --- View для основной панели ---
class UserLookupView(discord.ui.View):
    def __init__(self, cog_instance):
        super().__init__(timeout=None)
        self.cog: UserLookupCog = cog_instance

    @discord.ui.button(label="Discord Handle -> Wallet", style=discord.ButtonStyle.success, custom_id="user_lookup:discord_to_wallet", row=0)
    async def find_wallet_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = DiscordHandleModal(self.cog); await interaction.response.send_modal(modal)

    # --- ИСПРАВЛЕННЫЙ ВЫЗОВ МОДАЛЬНОГО ОКНА ---
    @discord.ui.button(label="Task History by Wallet", style=discord.ButtonStyle.primary, custom_id="user_lookup:history_by_wallet", row=1)
    async def task_history_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Передаем ТОЛЬКО экземпляр кога (self.cog)
        modal = AddressForHistoryModal(self.cog)
        await interaction.response.send_modal(modal)
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
# --- Конец UserLookupView ---


# --- Класс Кога ---
class UserLookupCog(commands.Cog, name="User Lookup & History"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot; self.api_key = getattr(bot, 'snag_api_key', None)
        self.http_session = aiohttp.ClientSession(base_url=SNAG_API_BASE_URL)
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")
        if not self.api_key: logger.warning(f"SNAG_API_KEY missing for {self.__class__.__name__}.")

    async def cog_unload(self): await self.http_session.close(); logger.info(f"Cog '{self.__class__.__name__}' unloaded.")

    @commands.Cog.listener("on_ready")
    async def on_ready_register_lookup_view(self): self.bot.add_view(UserLookupView(self)); logger.info("Persistent UserLookupView registered.")

    @commands.command(name="send_lookup_panel")
    @commands.has_any_role("Ranger") # Проверка роли
    async def send_lookup_panel_command(self, ctx: commands.Context):
        embed = discord.Embed(title="👤 User Info Panel", description="Use buttons below.", color=discord.Color.blue())
        await ctx.send(embed=embed, view=UserLookupView(self)); logger.info(f"Panel sent in {ctx.channel.id}")

    @send_lookup_panel_command.error
    async def send_lookup_panel_error(self, ctx, error):
        if isinstance(error, commands.MissingAnyRole): await ctx.send(f"You need the `Ranger` role.")
        elif isinstance(error, commands.NoPrivateMessage): await ctx.send("Use in server channels only.")
        else: logger.error(f"Error in send_lookup_panel: {error}"); await ctx.send("An error occurred.")

    # --- Вспомогательный метод: Поиск адреса по Discord Handle ---
    async def _find_wallet_by_discord_handle(self, discord_handle: str) -> str | None:
        # (Код этого метода не менялся)
        if not self.api_key: logger.error("API Key missing"); return None
        logger.info(f"Searching wallet for Discord Handle: {discord_handle}")
        organization_id = '8f48e0f1-f648-4b0e-99be-3a3c25597a97' # ЗАМЕНИТЬ!
        website_id = 'd88e4c28-d8cc-45ff-8cff-1180cdc1e87c'      # ЗАМЕНИТЬ!
        last_account_id = None; has_more_pages = True; page_count = 0; max_pages = MAX_PAGES_TO_FETCH
        while has_more_pages and page_count < max_pages:
             if page_count > 0: await asyncio.sleep(API_REQUEST_DELAY)
             page_count += 1; response_text = ""
             try:
                 api_url_path = ACCOUNTS_ENDPOINT; headers = {SNAG_API_KEY_HEADER: self.api_key}
                 params = {'limit': PAGE_LIMIT, 'discordUser': discord_handle, 'organizationId': organization_id, 'websiteId': website_id }
                 if last_account_id: params['startingAfter'] = last_account_id
                 logger.info(f"Account Search by Discord: Requesting page {page_count}...")
                 async with self.http_session.get(api_url_path, headers=headers, params=params, timeout=15) as response:
                     logger.info(f"Account Search by Discord: Page {page_count}: Status {response.status}")
                     response_text = await response.text(); response.raise_for_status()
                     try: data = json.loads(response_text)
                     except json.JSONDecodeError: logger.error("Account Search: JSON Error"); return None
                     accounts_on_page = data.get("data", []); has_more_pages = data.get("hasNextPage", False)
                     if not isinstance(accounts_on_page, list): logger.warning("Account Search: 'data' not list."); return None
                     if not accounts_on_page: logger.info("Account Search: Empty page."); has_more_pages = False; continue
                     for acc in accounts_on_page:
                         user_info = acc.get("user"); wallet_address = user_info.get("walletAddress") if isinstance(user_info, dict) else None
                         if isinstance(wallet_address, str) and wallet_address:
                             user_metadata_list = user_info.get("userMetadata", []); discord_user_in_meta = None
                             if isinstance(user_metadata_list, list) and user_metadata_list: discord_user_in_meta = user_metadata_list[0].get("discordUser")
                             if discord_user_in_meta == discord_handle: logger.info(f"!!! Found walletAddress: {wallet_address} !!!"); return wallet_address
                     last_account_id = accounts_on_page[-1].get('id');
                     if not last_account_id: logger.warning("Account Search: Cannot get last ID."); has_more_pages = False
             except Exception as e: logger.exception("Account Search: Unexpected error"); return None
        logger.warning(f"Could not find wallet for handle {discord_handle} after {page_count} pages.")
        return None

    # --- Вспомогательный метод: Получение и отправка истории квестов ---
    async def _process_and_send_quest_history(self, interaction: discord.Interaction, target_address: str, quest_filter: str | None = None):
        """Загружает, фильтрует и отправляет историю квестов (вызывается из модального окна)."""
        # (Код этого метода не менялся)
        organization_id = '8f48e0f1-f648-4b0e-99be-3a3c25597a97' # ЗАМЕНИТЬ!
        website_id = 'd88e4c28-d8cc-45ff-8cff-1180cdc1e87c'      # ЗАМЕНИТЬ!
        all_fetched_transactions = []; last_transaction_id = None; has_more_pages = True; page_count = 0
        log_filter_msg = f" matching '{quest_filter}'" if quest_filter else ""
        logger.info(f"Fetching transactions for address {target_address}{log_filter_msg}...")
        while has_more_pages and page_count < MAX_PAGES_TO_FETCH:
            if page_count > 0: await asyncio.sleep(API_REQUEST_DELAY)
            page_count += 1; response_text = ""
            try:
                api_url_path = TRANSACTIONS_ENDPOINT; headers = {SNAG_API_KEY_HEADER: self.api_key}
                params = {'limit': PAGE_LIMIT,'organizationId': organization_id,'websiteId': website_id, 'walletAddress': target_address}
                if last_transaction_id: params['startingAfter'] = last_transaction_id
                logger.info(f"Quest History: Requesting page {page_count} for address {target_address}")
                async with self.http_session.get(api_url_path, headers=headers, params=params, timeout=30) as response:
                    logger.info(f"Quest History: Page {page_count}: Status {response.status}")
                    response_text = await response.text(); response.raise_for_status()
                    try: data = json.loads(response_text)
                    except json.JSONDecodeError: logger.error("Quest History: JSON Error"); has_more_pages = False; continue
                    current_page_transactions = data.get("data", []); has_more_pages = data.get("hasNextPage", False)
                    if not isinstance(current_page_transactions, list): logger.warning("Quest History: 'data' not list."); has_more_pages = False; continue
                    if not current_page_transactions: logger.info("Quest History: Empty page."); has_more_pages = False; continue
                    all_fetched_transactions.extend(current_page_transactions)
                    last_transaction_id = current_page_transactions[-1].get('id')
                    if not last_transaction_id: logger.warning("Quest History: Cannot get last ID."); has_more_pages = False; continue
                    logger.info(f"Quest History: Page {page_count}: Loaded {len(current_page_transactions)}. Total: {len(all_fetched_transactions)}. More: {has_more_pages}")
            except aiohttp.ClientResponseError as e: logger.error(f"Quest History: HTTP Error {e.status}"); has_more_pages = False; await interaction.followup.send(f"⛔ API Error ({e.status}).", ephemeral=True); return
            except Exception as e: logger.exception("Quest History: Unexpected error"); has_more_pages = False; await interaction.followup.send("⚙️ Internal error.", ephemeral=True); return

        warning_message = None
        if page_count >= MAX_PAGES_TO_FETCH and has_more_pages: warning_message = f"⚠️ Loaded maximum pages ({MAX_PAGES_TO_FETCH}). Results might be incomplete."
        logger.info(f"Finished fetching history. Total transactions: {len(all_fetched_transactions)}. Filtering...")

        latest_unique_quests = {}
        if all_fetched_transactions:
            all_fetched_transactions.sort(key=lambda x: x.get('createdAt', '0'))
            for tx in all_fetched_transactions:
                if tx.get("direction") != "credit": continue
                rule_info = tx.get("loyaltyTransaction", {}).get("loyaltyRule", {})
                if not isinstance(rule_info, dict): continue
                rule_type = rule_info.get("type"); rule_id = rule_info.get("id"); rule_name = rule_info.get("name", "")
                if rule_type == "check_in": continue
                if not isinstance(rule_id, str) or not rule_id: continue
                if quest_filter and quest_filter.lower() not in rule_name.lower(): continue
                latest_unique_quests[rule_name.strip().lower()] = tx
        final_quests_list = list(latest_unique_quests.values())
        final_quests_list.sort(key=lambda x: x.get('createdAt', '0'), reverse=True)
        logger.info(f"Filtering complete. Found {len(final_quests_list)} unique relevant quests.")

        sent_message = None
        if warning_message: sent_message = await interaction.followup.send(warning_message, ephemeral=False)

        if not final_quests_list:
            final_msg = f"✅ No unique completed quests{log_filter_msg} found for `{target_address}` (excluding Check-ins)."
            if sent_message: await interaction.followup.send(final_msg, ephemeral=False)
            else: await interaction.edit_original_response(content=final_msg, view=None, embed=None)
        else:
            # Сохраняем адрес в kwargs ИСХОДНОГО interaction кнопки для доступа из View пагинатора
            view = QuestHistoryPaginatorView(interaction, final_quests_list, len(final_quests_list), target_address)
            initial_page_data = await view._get_page_data()
            initial_embed = await view._create_page_embed(initial_page_data)
            if sent_message: view.message = await interaction.followup.send(embed=initial_embed, view=view, ephemeral=False)
            else: view.message = await interaction.edit_original_response(content=None, embed=initial_embed, view=view)

    # --- Статические Вспомогательные Методы ---
    @staticmethod
    def _format_datetime(datetime_str: str | None) -> str:
        if not datetime_str: return "Date unknown"
        formats_to_try = ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"]
        for fmt in formats_to_try:
            try: dt_object = datetime.strptime(datetime_str, fmt).replace(tzinfo=timezone.utc); return dt_object.strftime("%d/%m/%y")
            except ValueError: continue
        logger.warning(f"Could not parse date format: {datetime_str}"); return datetime_str

    @staticmethod
    def _format_metadata(metadata: dict) -> str:
         if not metadata: return ""; parts = []
         return " | ".join(parts) if parts else ""

# --- Обязательная функция setup ---
async def setup(bot: commands.Bot):
    if not getattr(bot, 'snag_api_key', None): logger.error("Failed to load UserLookupCog: 'snag_api_key' missing."); return
    await bot.add_cog(UserLookupCog(bot))
# --- КОНЕЦ ФАЙЛА cogs/user_lookup_cog.py ---