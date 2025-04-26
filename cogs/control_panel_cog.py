# cogs/control_panel_cog.py
# --- ПОЛНЫЙ КОД С КОРРЕКТНЫМ ПОИСКОМ И ФИЛЬТРАМИ (Версия от 2024-04-26 v17) ---
import discord
from discord.ext import commands
from discord import app_commands # Используется для App Commands, но оставим для совместимости, если понадобится
import aiohttp
import logging
from datetime import datetime, timezone
import json
import asyncio
import math
import re

logger = logging.getLogger(__name__) # Логгер для этого кога
EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")

# --- API Constants ---
SNAG_API_BASE_URL = "https://admin.snagsolutions.io"
ACCOUNTS_ENDPOINT = "/api/loyalty/accounts"
TRANSACTIONS_ENDPOINT = "/api/loyalty/transaction_entries"
SNAG_API_KEY_HEADER = "X-API-KEY"
PAGE_LIMIT = 1000
MAX_PAGES_TO_FETCH = 20
API_REQUEST_DELAY = 2.0 # Задержка в секундах
# --- Discord Pagination/UI ---
ITEMS_PER_PAGE = 10 # Квестов на странице истории
VIEW_TIMEOUT = 300.0 # 5 минут
# ---------------------

# --- Модальное окно: Поиск кошелька по соц. сетям ---
class FindWalletModal(discord.ui.Modal, title='Find Wallet by Social Handle'):
    discord_input = discord.ui.TextInput(
        label='Discord Handle (Optional)',
        placeholder='username#1234 or username', required=False,
        style=discord.TextStyle.short, row=0, max_length=100
    )
    twitter_input = discord.ui.TextInput(
        label='Twitter/X Handle (Optional)',
        placeholder='@username or username', required=False,
        style=discord.TextStyle.short, row=1, max_length=100
    )

    def __init__(self, cog_instance):
        super().__init__(timeout=None)
        self.cog: ControlPanelCog = cog_instance # Указываем тип кога

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        discord_h = self.discord_input.value.strip() if self.discord_input.value else None
        twitter_h = self.twitter_input.value.strip() if self.twitter_input.value else None
        if twitter_h and twitter_h.startswith('@'): twitter_h = twitter_h[1:] # Убираем @ для Twitter

        # Проверяем, что хоть что-то введено
        if not discord_h and not twitter_h:
            await interaction.followup.send("Please enter a Discord or Twitter/X handle.", ephemeral=True)
            return

        # Определяем, по какому хендлу искать (приоритет Discord, если введены оба)
        identifier_type, identifier_value = None, None
        if discord_h:
            identifier_type, identifier_value = "discordUser", discord_h
        elif twitter_h:
            identifier_type, identifier_value = "twitterUser", twitter_h

        if not identifier_type: # На всякий случай
             await interaction.followup.send("Failed to process input.", ephemeral=True); return

        logger.info(f"User {interaction.user.id} looking up wallet via {identifier_type}: {identifier_value}")
        try:
            # Используем прямой поиск через API
            found_address = await self.cog._find_wallet_by_social_api_filter(identifier_type, identifier_value)
            if found_address:
                await interaction.followup.send(f"Wallet for {identifier_type} `{identifier_value}`: `{found_address}`", ephemeral=True)
            else:
                await interaction.followup.send(f"Could not find wallet for {identifier_type} `{identifier_value}`.", ephemeral=True)
        except Exception:
            logger.exception(f"Error during wallet lookup by {identifier_type}")
            await interaction.followup.send("Internal error during lookup.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"FindWalletModal Error: {error}", exc_info=True)
        # Пытаемся ответить, если возможно
        try: await interaction.followup.send('An error occurred in the modal.', ephemeral=True)
        except discord.HTTPException: pass # Если ответить уже нельзя
# --- Конец FindWalletModal ---


# --- Модальное окно: Ввод адреса для ИСТОРИИ ---
class AddressForHistoryModal(discord.ui.Modal, title='Get Quest History by Address'):
    address_input = discord.ui.TextInput(label='EVM Wallet Address', placeholder='0x...', required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0)
    quest_filter_input = discord.ui.TextInput(label='Quest Name Filter (Optional)', placeholder='Enter keywords...', required=False, style=discord.TextStyle.short, max_length=100, row=1)

    def __init__(self, cog_instance):
        super().__init__(timeout=None)
        self.cog: ControlPanelCog = cog_instance

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True) # Ответ будет эфемерным
        target_address = self.address_input.value.strip().lower()
        quest_filter = self.quest_filter_input.value.strip() if self.quest_filter_input.value else None
        if not EVM_ADDRESS_PATTERN.match(target_address):
            await interaction.followup.send(f"⚠️ Invalid EVM address format.", ephemeral=True); return
        log_filter_msg = f" matching '{quest_filter}'" if quest_filter else ""
        logger.info(f"User {interaction.user.id} requested history for: {target_address}{log_filter_msg}")
        # Вызываем основную логику, которая отправит эфемерный ответ или пагинатор
        await self.cog._process_and_send_quest_history(interaction, target_address, quest_filter)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"AddressHistoryModal Error: {error}", exc_info=True)
        try: await interaction.followup.send('An error occurred in the history modal.', ephemeral=True)
        except discord.HTTPException: pass
# --- Конец AddressForHistoryModal ---


# --- Модальное окно: Ввод адреса для СОЦ. СЕТЕЙ ---
class AddressForSocialsModal(discord.ui.Modal, title='Find Socials by Wallet Address'):
    address_input = discord.ui.TextInput(label='EVM Wallet Address', placeholder='0x...', required=True, style=discord.TextStyle.short, min_length=42, max_length=42, row=0)

    def __init__(self, cog_instance):
        super().__init__(timeout=None)
        self.cog: ControlPanelCog = cog_instance

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True) # Ответ эфемерный
        target_address = self.address_input.value.strip().lower()
        if not EVM_ADDRESS_PATTERN.match(target_address):
            await interaction.followup.send(f"⚠️ Invalid EVM address format.", ephemeral=True); return
        logger.info(f"User {interaction.user.id} requested socials for: {target_address}")
        try:
            socials_text = await self.cog._find_socials_by_wallet(target_address)
            await interaction.followup.send(socials_text, ephemeral=True) # Отправляем результат эфемерно
        except Exception:
            logger.exception(f"Error during socials lookup for {target_address}")
            await interaction.followup.send("Internal error during lookup.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"AddressSocialsModal Error: {error}", exc_info=True)
        try: await interaction.followup.send('An error occurred in the socials modal.', ephemeral=True)
        except discord.HTTPException: pass
# --- Конец AddressForSocialsModal ---


# --- Класс Пагинатора для истории квестов ---
class QuestHistoryPaginatorView(discord.ui.View):
    """View для пагинации по истории квестов."""
    current_page : int = 1
    sep : int = ITEMS_PER_PAGE

    def __init__(self, original_interaction: discord.Interaction, data: list, total_unique_quests: int, target_address: str):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.original_interaction = original_interaction # Сохраняем interaction для проверки автора
        self.data = data
        self.total_unique_quests = total_unique_quests
        self.target_address = target_address # Сохраняем адрес для embed
        self.max_pages = math.ceil(len(self.data) / self.sep) if self.data else 1
        self.message: discord.Message | None = None
        self._update_buttons()

    async def _get_page_data(self) -> list:
        base = (self.current_page - 1) * self.sep
        return self.data[base:base + self.sep]

    async def _create_page_embed(self, page_data: list) -> discord.Embed:
        embed = discord.Embed(
            title="Latest Completed Quests for:",
            description=f"`{self.target_address}` (excluding Check-ins)",
            color=discord.Color.green()
        )
        if not page_data and self.current_page == 1:
             embed.description += "\n\nNo matching quests found."
        else:
            for tx in page_data:
                amount = tx.get("amount", 0); created_at_str = tx.get("createdAt")
                rule_name = tx.get("loyaltyTransaction", {}).get("loyaltyRule", {}).get("name", "Unknown Action").strip()
                date_formatted = ControlPanelCog._format_datetime(created_at_str) # Используем статический метод
                field_name = f"✅ {rule_name}"; field_value = f"**Earned:** `{amount}` | **Completed:** {date_formatted}"
                embed.add_field(name=field_name, value=field_value, inline=False)
        embed.set_footer(text=f"Page {self.current_page} of {self.max_pages} | Total Unique Quests: {self.total_unique_quests}")
        embed.timestamp = discord.utils.utcnow(); return embed

    def _update_buttons(self):
        if hasattr(self, 'first_page'): self.first_page.disabled = self.current_page == 1
        if hasattr(self, 'prev_page'): self.prev_page.disabled = self.current_page == 1
        if hasattr(self, 'next_page'): self.next_page.disabled = self.current_page >= self.max_pages
        if hasattr(self, 'last_page'): self.last_page.disabled = self.current_page >= self.max_pages

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Разрешаем взаимодействие только автору исходной команды
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message("Sorry, only the user who initiated the command can use these buttons.", ephemeral=True)
            return False
        return True

    async def show_current_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self._update_buttons()
        page_data = await self._get_page_data()
        embed = await self._create_page_embed(page_data)
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="|< First", style=discord.ButtonStyle.secondary, row=0)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page != 1: self.current_page = 1; await self.show_current_page(interaction)
        else: await interaction.response.defer() # Нужно ответить на взаимодействие
    @discord.ui.button(label="< Previous", style=discord.ButtonStyle.primary, row=0)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 1: self.current_page -= 1; await self.show_current_page(interaction)
        else: await interaction.response.defer()
    @discord.ui.button(label="Next >", style=discord.ButtonStyle.primary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.max_pages: self.current_page += 1; await self.show_current_page(interaction)
        else: await interaction.response.defer()
    @discord.ui.button(label="Last >|", style=discord.ButtonStyle.secondary, row=0)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page != self.max_pages: self.current_page = self.max_pages; await self.show_current_page(interaction)
        else: await interaction.response.defer()

    async def on_timeout(self) -> None:
        if self.message:
            try:
                # Создаем View без кнопок, чтобы убрать их
                await self.message.edit(view=None)
                logger.info(f"History view {self.message.id} timed out and buttons removed.")
            except Exception as e: logger.error(f"Error removing view on timeout for {self.message.id}: {e}")
# --- Конец QuestHistoryPaginatorView ---


# --- View для панели управления ---
class InfoPanelView(discord.ui.View):
    # (Код остается без изменений)
    def __init__(self, cog_instance): super().__init__(timeout=None); self.cog: ControlPanelCog = cog_instance
    @discord.ui.button(label="Find Wallet by Social", style=discord.ButtonStyle.success, custom_id="info_panel:find_wallet", row=0)
    async def find_wallet_button(self, interaction: discord.Interaction, button: discord.ui.Button): modal = FindWalletModal(self.cog); await interaction.response.send_modal(modal)
    @discord.ui.button(label="Task History by Wallet", style=discord.ButtonStyle.primary, custom_id="info_panel:history_by_wallet", row=1)
    async def task_history_button(self, interaction: discord.Interaction, button: discord.ui.Button): modal = AddressForHistoryModal(self.cog); await interaction.response.send_modal(modal)
    @discord.ui.button(label="Find Socials by Wallet", style=discord.ButtonStyle.secondary, custom_id="info_panel:socials_by_wallet", row=2)
    async def find_socials_button(self, interaction: discord.Interaction, button: discord.ui.Button): modal = AddressForSocialsModal(self.cog); await interaction.response.send_modal(modal)
# --- Конец InfoPanelView ---


# --- Класс Кога ControlPanel ---
class ControlPanelCog(commands.Cog, name="Control Panel"):
    """Cog for staff control panel interactions."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot; self.api_key = getattr(bot, 'snag_api_key', None)
        self.http_session = aiohttp.ClientSession(base_url=SNAG_API_BASE_URL)
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")
        if not self.api_key: logger.warning(f"SNAG_API_KEY missing for {self.__class__.__name__}.")

    async def cog_unload(self):
        await self.http_session.close(); logger.info(f"Cog '{self.__class__.__name__}' unloaded.")

    @commands.Cog.listener("on_ready")
    async def on_ready_register_views(self):
        self.bot.add_view(InfoPanelView(self)); logger.info("Persistent InfoPanelView registered.")

    @commands.command(name="send_info_panel")
    @commands.has_any_role("Ranger") # Укажите нужную роль
    async def send_info_panel_command(self, ctx: commands.Context):
        embed = discord.Embed(title="ℹ️ Info Panel", description="Use buttons below.", color=discord.Color.purple())
        await ctx.send(embed=embed, view=InfoPanelView(self)); logger.info(f"Panel sent in {ctx.channel.id}")

    @send_info_panel_command.error
    async def send_info_panel_error(self, ctx, error):
        if isinstance(error, commands.MissingAnyRole): await ctx.send(f"You need the required role.")
        else: logger.error(f"Error in send_info_panel: {error}"); await ctx.send("An error occurred.")

    # --- Вспомогательный метод: Поиск кошелька по Discord/Twitter (API Фильтр) ---
    async def _find_wallet_by_social_api_filter(self, handle_type: str, handle_value: str) -> str | None:
        if not self.api_key: logger.error("API Key missing for social search"); return None
        organization_id = '8f48e0f1-f648-4b0e-99be-3a3c25597a97' # ЗАМЕНИТЬ!
        website_id = 'd88e4c28-d8cc-45ff-8cff-1180cdc1e87c'      # ЗАМЕНИТЬ!
        if handle_type not in ["discordUser", "twitterUser"]: logger.error(f"Unsupported API filter: {handle_type}"); return None
        logger.info(f"Searching wallet using API filter for {handle_type}: {handle_value}")
        response_text = ""; account_data = None
        try:
            api_url_path = ACCOUNTS_ENDPOINT; headers = {SNAG_API_KEY_HEADER: self.api_key}
            params = {'limit': 1,'organizationId': organization_id,'websiteId': website_id, handle_type: handle_value}
            async with self.http_session.get(api_url_path, headers=headers, params=params, timeout=15) as response:
                logger.info(f"Account Search (API): Status {response.status}"); response_text = await response.text(); response.raise_for_status()
                try: data = json.loads(response_text)
                except json.JSONDecodeError: logger.error("Account Search (API): JSON Error"); return None
                accounts = data.get("data", []); account_data = accounts[0] if accounts and isinstance(accounts, list) else None
        except Exception as e: logger.exception("Account Search (API): Unexpected error"); return None

        if account_data and isinstance(account_data, dict):
            user_info = account_data.get("user"); wallet_address = user_info.get("walletAddress") if isinstance(user_info, dict) else None
            if isinstance(wallet_address, str) and wallet_address: logger.info(f"Found wallet {wallet_address}"); return wallet_address
        logger.warning(f"Wallet not found via API filter for {handle_type} {handle_value}")
        return None
    # --- Конец _find_wallet_by_social_api_filter ---

    # --- Вспомогательный метод: Поиск соцсетей по кошельку ---
    async def _find_socials_by_wallet(self, target_address: str) -> str:
        # (Этот метод остается без изменений)
        if not self.api_key: return "⛔ Error: API key missing."; logger.info(f"Searching socials for {target_address}")
        organization_id = '8f48e0f1-f648-4b0e-99be-3a3c25597a97'; website_id = 'd88e4c28-d8cc-45ff-8cff-1180cdc1e87c' # ЗАМЕНИТЬ!
        account_data = None; response_text = ""
        try:
            api_url_path = ACCOUNTS_ENDPOINT; headers = {SNAG_API_KEY_HEADER: self.api_key}
            params = {'limit': 1,'organizationId': organization_id,'websiteId': website_id, 'walletAddress': target_address}
            async with self.http_session.get(api_url_path, headers=headers, params=params, timeout=15) as response:
                logger.info(f"Socials Search: Status {response.status}"); response_text = await response.text(); response.raise_for_status()
                try: data = json.loads(response_text)
                except json.JSONDecodeError: logger.error("Socials Search: JSON Error"); return "Error processing API response."
                accounts = data.get("data", []); account_data = accounts[0] if accounts and isinstance(accounts, list) else None
        except Exception as e: logger.error(f"Socials Search: Error: {e}"); return "⚙️ Error contacting API."
        if not account_data: return f"❌ No account data found for `{target_address}`."
        user_info = account_data.get("user", {}); metadata_list = user_info.get("userMetadata", [])
        display_name = "N/A"; discord_handle = None; twitter_handle = None; 
        if isinstance(metadata_list, list) and metadata_list: meta = metadata_list[0]
        if isinstance(meta, dict): display_name = meta.get("displayName", "N/A"); discord_handle = meta.get("discordUser"); twitter_handle = meta.get("twitterUser"); 
        if twitter_handle and not twitter_handle.startswith('@'): twitter_handle = f"@{twitter_handle}"
        response_message = (f"**Socials for:** `{target_address}`\n"
                            f"**Display Name:** {display_name}\n"
                            f"**Discord:** `{discord_handle or 'Not linked'}`\n"
                            f"**Twitter/X:** `{twitter_handle or 'Not linked'}`\n")
        return response_message
    # --- Конец _find_socials_by_wallet ---

    # --- Вспомогательный метод: Обработка и отправка истории квестов ---
    async def _process_and_send_quest_history(self, interaction: discord.Interaction, target_address: str, quest_filter: str | None):
        # (Логика получения транзакций остается той же, используем прямой фильтр по адресу)
        organization_id = '8f48e0f1-f648-4b0e-99be-3a3c25597a97' # ЗАМЕНИТЬ!
        website_id = 'd88e4c28-d8cc-45ff-8cff-1180cdc1e87c'      # ЗАМЕНИТЬ!
        all_fetched_transactions = []; last_transaction_id = None; has_more_pages = True; page_count = 0
        logger.info(f"Fetching transactions directly for address {target_address}...")
        while has_more_pages and page_count < MAX_PAGES_TO_FETCH:
            if page_count > 0: await asyncio.sleep(API_REQUEST_DELAY)
            page_count += 1; response_text = ""
            try:
                api_url_path = TRANSACTIONS_ENDPOINT; headers = {SNAG_API_KEY_HEADER: self.api_key}
                params = {'limit': PAGE_LIMIT,'organizationId': organization_id,'websiteId': website_id,'walletAddress': target_address}
                if last_transaction_id: params['startingAfter'] = last_transaction_id
                logger.info(f"Quest History: Requesting page {page_count}...")
                async with self.http_session.get(api_url_path, headers=headers, params=params, timeout=30) as response:
                    logger.info(f"Quest History: Status {response.status}"); response_text = await response.text(); response.raise_for_status()
                    try: data = json.loads(response_text)
                    except json.JSONDecodeError: logger.error("Quest History: JSON Error"); has_more_pages = False; continue
                    current_page_transactions = data.get("data", []); has_more_pages = data.get("hasNextPage", False)
                    if not isinstance(current_page_transactions, list): logger.warning("Quest History: 'data' not list."); has_more_pages = False; continue
                    if not current_page_transactions: logger.info("Quest History: Empty page."); has_more_pages = False; continue
                    all_fetched_transactions.extend(current_page_transactions)
                    last_transaction_id = current_page_transactions[-1].get('id')
                    if not last_transaction_id: logger.warning("Quest History: Cannot get last ID."); has_more_pages = False; continue
            except Exception as e: logger.exception(f"Quest History: Error page {page_count}"); has_more_pages = False; await interaction.followup.send("⚙️ Internal error fetching quest history.", ephemeral=True); return

        warning_message = None
        if page_count >= MAX_PAGES_TO_FETCH and has_more_pages: warning_message = f"⚠️ Loaded maximum pages ({MAX_PAGES_TO_FETCH}). Results might be incomplete."
        logger.info(f"Finished fetching. Total transactions: {len(all_fetched_transactions)}. Filtering unique quests...")

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
        logger.info(f"Found {len(final_quests_list)} unique relevant quests.")

        sent_warning = False
        if warning_message: await interaction.followup.send(warning_message, ephemeral=True); sent_warning = True

        if not final_quests_list:
            final_msg = f"✅ No unique completed quests{f' matching {quest_filter}' if quest_filter else ''} found for `{target_address}` (excluding Check-ins)."
            if sent_warning: await interaction.followup.send(final_msg, ephemeral=True)
            else: await interaction.edit_original_response(content=final_msg, view=None, embed=None)
        else:
            # Передаем interaction и адрес пагинатору
            view = QuestHistoryPaginatorView(interaction, final_quests_list, len(final_quests_list), target_address)
            initial_page_data = await view._get_page_data(); initial_embed = await view._create_page_embed(initial_page_data)
            if sent_warning: view.message = await interaction.followup.send(embed=initial_embed, view=view, ephemeral=True)
            else: view.message = await interaction.edit_original_response(content=None, embed=initial_embed, view=view)
    # --- Конец _process_and_send_quest_history ---

    # --- Статические Вспомогательные Методы ---
    @staticmethod
    def _format_datetime(datetime_str: str | None) -> str:
        if not datetime_str: return "Date unknown"
        formats_to_try = ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"]
        for fmt in formats_to_try:
            try: dt_object = datetime.strptime(datetime_str, fmt).replace(tzinfo=timezone.utc); return dt_object.strftime("%d/%m/%y")
            except ValueError: continue
        logger.warning(f"Could not parse date format: {datetime_str}"); return datetime_str

# --- Обязательная функция setup ---
async def setup(bot: commands.Bot):
    await bot.add_cog(ControlPanelCog(bot)) # Используем правильное имя класса
# --- КОНЕЦ ФАЙЛА cogs/control_panel_cog.py ---