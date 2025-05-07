# cogs/control_panel_cog.py
# --- ПОЛНАЯ ВЕРСИЯ С ИСПРАВЛЕНИЯМИ NameError и AssertionError ---
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import logging
import datetime
from datetime import timezone
import json
import asyncio
import math
import re
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)
EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")

# --- API Constants ---
# !!! Убедитесь, что эти константы определены ЗДЕСЬ !!!
SNAG_API_BASE_URL = "https://admin.snagsolutions.io"
ACCOUNTS_ENDPOINT = "/api/loyalty/accounts"
TRANSACTIONS_ENDPOINT = "/api/loyalty/transaction_entries"
CURRENCIES_ENDPOINT = "/api/loyalty/currencies"
SNAG_API_KEY_HEADER = "X-API-KEY"
PAGE_LIMIT = 1000
MAX_PAGES_TO_FETCH = 20
API_REQUEST_DELAY = 2.0

# --- ID Организации/Сайта (ВАЖНО: убедитесь, что они актуальны) ---
ORGANIZATION_ID = '8f48e0f1-f648-4b0e-99be-3a3c25597a97' # ЗАМЕНИТЬ при необходимости
WEBSITE_ID = 'd88e4c28-d8cc-45ff-8cff-1180cdc1e87c'      # ЗАМЕНИТЬ при необходимости

# --- Discord Pagination/UI ---
ITEMS_PER_PAGE = 10
VIEW_TIMEOUT = 300.0
# ---------------------

# --- Модальные окна (FindWalletModal, AddressForHistoryModal, AddressForSocialsModal, BalanceCheckModal) ---
# --- Вставьте сюда полный код всех модальных окон из предыдущих версий ---
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
        self.cog: ControlPanelCog = cog_instance

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        discord_h = self.discord_input.value.strip() if self.discord_input.value else None
        twitter_h = self.twitter_input.value.strip() if self.twitter_input.value else None
        if twitter_h and twitter_h.startswith('@'): twitter_h = twitter_h[1:]

        if not discord_h and not twitter_h:
            await interaction.followup.send("Please enter a Discord or Twitter/X handle.", ephemeral=True)
            return

        identifier_type, identifier_value = None, None
        if discord_h:
            identifier_type, identifier_value = "discordUser", discord_h
        elif twitter_h:
            identifier_type, identifier_value = "twitterUser", twitter_h

        if not identifier_type:
             await interaction.followup.send("Failed to process input.", ephemeral=True); return

        logger.info(f"User {interaction.user.id} looking up wallet via {identifier_type}: {identifier_value}")
        try:
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
        try: await interaction.followup.send('An error occurred in the modal.', ephemeral=True)
        except discord.HTTPException: pass

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
        await self.cog._process_and_send_quest_history(interaction, target_address, quest_filter)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"AddressHistoryModal Error: {error}", exc_info=True)
        try: await interaction.followup.send('An error occurred in the history modal.', ephemeral=True)
        except discord.HTTPException: pass

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

class BalanceCheckModal(discord.ui.Modal, title='Check All Balances by Wallet'):
    address_input = discord.ui.TextInput(
        label='EVM Wallet Address',
        placeholder='0x...', required=True,
        style=discord.TextStyle.short, min_length=42, max_length=42, row=0
    )

    def __init__(self, cog_instance):
        super().__init__(timeout=None)
        self.cog: ControlPanelCog = cog_instance

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        target_address = self.address_input.value.strip().lower()

        if not EVM_ADDRESS_PATTERN.match(target_address):
            await interaction.followup.send("⚠️ Invalid EVM address format.", ephemeral=True)
            return

        logger.info(f"User {interaction.user.id} requested all balances for wallet {target_address}")

        try:
            result_message = await self.cog._get_all_wallet_balances(target_address)
            if len(result_message) > 2000:
                 result_message = result_message[:1997] + "..."
            await interaction.followup.send(result_message, ephemeral=True)
        except Exception as e:
            logger.exception(f"Error during all balances check for {target_address}")
            await interaction.followup.send("⚙️ An internal error occurred while checking balances.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"BalanceCheckModal Error: {error}", exc_info=True)
        try:
            await interaction.followup.send('An error occurred in the balance check modal.', ephemeral=True)
        except discord.HTTPException:
            pass

# --- Класс Пагинатора для истории квестов ---
# --- Вставьте сюда полный код QuestHistoryPaginatorView из предыдущих версий ---
class QuestHistoryPaginatorView(discord.ui.View):
    current_page : int = 1
    sep : int = ITEMS_PER_PAGE

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
                date_formatted = ControlPanelCog._format_datetime(created_at_str)
                field_name = f"✅ {rule_name}"; field_value = f"**Earned:** `{amount}` | **Completed:** {date_formatted}"
                embed.add_field(name=field_name, value=field_value, inline=False)
        embed.set_footer(text=f"Page {self.current_page} of {self.max_pages} | Total Unique Quests: {self.total_unique_quests}")
        embed.timestamp = discord.utils.utcnow(); return embed

    def _update_buttons(self):
        first_button = getattr(self, 'first_page', None)
        prev_button = getattr(self, 'prev_page', None)
        next_button = getattr(self, 'next_page', None)
        last_button = getattr(self, 'last_page', None)

        if first_button: first_button.disabled = self.current_page == 1
        if prev_button: prev_button.disabled = self.current_page == 1
        if next_button: next_button.disabled = self.current_page >= self.max_pages
        if last_button: last_button.disabled = self.current_page >= self.max_pages


    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message("Sorry, only the user who initiated the command can use these buttons.", ephemeral=True)
            return False
        return True

    async def show_current_page(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
             await interaction.response.defer()

        self._update_buttons()
        page_data = await self._get_page_data()
        embed = await self._create_page_embed(page_data)
        try:
             await interaction.edit_original_response(embed=embed, view=self)
        except discord.InteractionResponded:
             await interaction.followup.edit_message(interaction.message.id, embed=embed, view=self)
        except discord.HTTPException as e:
             logger.error(f"Failed to edit message for pagination: {e}")

    @discord.ui.button(label="|< First", style=discord.ButtonStyle.secondary, row=0)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page != 1: self.current_page = 1; await self.show_current_page(interaction)
        elif not interaction.response.is_done(): await interaction.response.defer()

    @discord.ui.button(label="< Previous", style=discord.ButtonStyle.primary, row=0)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 1: self.current_page -= 1; await self.show_current_page(interaction)
        elif not interaction.response.is_done(): await interaction.response.defer()

    @discord.ui.button(label="Next >", style=discord.ButtonStyle.primary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.max_pages: self.current_page += 1; await self.show_current_page(interaction)
        elif not interaction.response.is_done(): await interaction.response.defer()

    @discord.ui.button(label="Last >|", style=discord.ButtonStyle.secondary, row=0)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page != self.max_pages: self.current_page = self.max_pages; await self.show_current_page(interaction)
        elif not interaction.response.is_done(): await interaction.response.defer()


    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
                logger.info(f"History view for {self.target_address} timed out and buttons removed.")
            except discord.NotFound:
                logger.warning(f"Could not find message {self.message.id} to remove view on timeout.")
            except discord.Forbidden:
                 logger.warning(f"Missing permissions to edit message {self.message.id} on timeout.")
            except Exception as e:
                logger.error(f"Error removing view on timeout for {self.message.id}: {e}")


# --- View для панели управления ---
# --- Вставьте сюда полный код InfoPanelView из предыдущих версий ---
class InfoPanelView(discord.ui.View):
    def __init__(self, cog_instance):
        super().__init__(timeout=None)
        self.cog: ControlPanelCog = cog_instance

    @discord.ui.button(label="Find Wallet by Social", style=discord.ButtonStyle.success, custom_id="info_panel:find_wallet", row=0)
    async def find_wallet_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = FindWalletModal(self.cog)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Task History by Wallet", style=discord.ButtonStyle.primary, custom_id="info_panel:history_by_wallet", row=1)
    async def task_history_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddressForHistoryModal(self.cog)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Find Socials by Wallet", style=discord.ButtonStyle.secondary, custom_id="info_panel:socials_by_wallet", row=2)
    async def find_socials_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddressForSocialsModal(self.cog)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Check Balances by Wallet", style=discord.ButtonStyle.danger, custom_id="info_panel:balance_by_wallet", row=3)
    async def check_balance_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = BalanceCheckModal(self.cog)
        await interaction.response.send_modal(modal)


# --- Класс Кога ControlPanel ---
class ControlPanelCog(commands.Cog, name="Control Panel"):
    """Cog for staff control panel interactions."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api_key = getattr(bot, 'snag_api_key', None)
        # Инициализируем сессию с базовым URL
        self.http_session = aiohttp.ClientSession(base_url=SNAG_API_BASE_URL) # Используем константу
        self._currency_cache: Optional[Dict[str, Dict[str, Any]]] = None
        self._currency_cache_time: Optional[datetime.datetime] = None
        self._currency_cache_lock = asyncio.Lock()

        logger.info(f"Cog '{self.__class__.__name__}' loaded.")
        if not self.api_key:
            logger.warning(f"SNAG_API_KEY missing for {self.__class__.__name__}.")

    async def cog_unload(self):
        await self.http_session.close()
        logger.info(f"Cog '{self.__class__.__name__}' unloaded.")

    @commands.Cog.listener("on_ready")
    async def on_ready_register_views(self):
        self.bot.add_view(InfoPanelView(self))
        logger.info("Persistent InfoPanelView registered.")
        await self._get_currency_map()


    @commands.command(name="send_info_panel")
    @commands.has_any_role("Ranger")
    async def send_info_panel_command(self, ctx: commands.Context):
        embed = discord.Embed(title="ℹ️ Info Panel", description="Use buttons below.", color=discord.Color.purple())
        await ctx.send(embed=embed, view=InfoPanelView(self))
        logger.info(f"Panel sent in {ctx.channel.id}")

    @send_info_panel_command.error
    async def send_info_panel_error(self, ctx, error):
        if isinstance(error, commands.MissingAnyRole):
            await ctx.send("⛔ You do not have the required role ('Ranger') to use this command.")
        else:
            logger.error(f"Error in send_info_panel: {error}", exc_info=True)
            await ctx.send("⚙️ An unexpected error occurred while trying to send the panel.")

    # --- Вспомогательные методы для API запросов ---

    async def _make_api_request(self, method: str, endpoint: str, params: Optional[Dict] = None, json_data: Optional[Dict] = None, timeout: int = 20) -> Optional[Dict]:
        """Общий метод для выполнения запросов к API Snag."""
        if not self.api_key:
            logger.error(f"Cannot make API request to {endpoint}: API Key is missing.")
            return None
        # ID организации и сайта должны быть доступны
        if not ORGANIZATION_ID or not WEBSITE_ID:
             logger.error(f"Cannot make API request to {endpoint}: ORGANIZATION_ID or WEBSITE_ID is missing.")
             return None

        request_params = params.copy() if params else {}
        # Убеждаемся, что ID организации/сайта всегда есть для нужных эндпоинтов
        if endpoint in [ACCOUNTS_ENDPOINT, TRANSACTIONS_ENDPOINT, CURRENCIES_ENDPOINT]:
             request_params.setdefault('organizationId', ORGANIZATION_ID)
             request_params.setdefault('websiteId', WEBSITE_ID)

        headers = {SNAG_API_KEY_HEADER: self.api_key}

        response_text = ""
        try:
            # Используем относительный endpoint, т.к. base_url задан в сессии
            # Исправленное логирование
            base_url_str = str(self.http_session._base_url) if self.http_session._base_url else "None"
            logger.debug(f"Making API Request: {method} {endpoint} | Base: {base_url_str} | Params: {request_params} | Headers: {SNAG_API_KEY_HEADER}: ***")
            async with self.http_session.request(
                method, endpoint, # Передаем относительный путь
                headers=headers, params=request_params, json=json_data, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                response_text = await response.text()
                log_msg = f"API Request {method} {endpoint}: Status {response.status}"
                logger.info(log_msg)
                response.raise_for_status()
                if response.status == 204:
                     return {"success": True, "status": 204}
                # Попытка парсинга JSON только если есть контент
                if response_text:
                     data = json.loads(response_text)
                     return data
                else: # Если ответ пустой, но статус 200/ОК
                     return {} # Возвращаем пустой словарь или другое значение по умолчанию

        except json.JSONDecodeError:
             # Логируем статус ответа, если он доступен
             status_code = response.status if 'response' in locals() and hasattr(response, 'status') else 'N/A'
             logger.error(f"API Request {method} {endpoint}: JSON Decode Error. Status: {status_code}. Response text: {response_text[:500]}...")
             return None
        except asyncio.TimeoutError:
            logger.error(f"API Request {method} {endpoint}: Request timed out after {timeout} seconds.")
            return None
        except aiohttp.ClientResponseError as e:
            logger.error(f"API Request {method} {endpoint}: HTTP Error {e.status} - {e.message}. Response: {response_text[:500]}...")
            return None
        except aiohttp.ClientConnectionError as e:
             logger.error(f"API Request {method} {endpoint}: Connection Error - {e}")
             return None
        except Exception as e:
            logger.exception(f"API Request {method} {endpoint}: Unexpected error during request execution")
            return None


    # --- Метод для получения и кэширования карты валют ---
    async def _get_currency_map(self, force_refresh: bool = False) -> Optional[Dict[str, Dict[str, Any]]]:
        cache_duration = datetime.timedelta(minutes=5)
        now = discord.utils.utcnow()

        async with self._currency_cache_lock:
            if not force_refresh and self._currency_cache is not None and self._currency_cache_time is not None and (now - self._currency_cache_time) < cache_duration:
                logger.debug("Returning cached currency map.")
                return self._currency_cache

            logger.info("Fetching or refreshing currency map from API...")
            params = {'limit': 100, 'includeDeleted': 'false'}
            response_data = await self._make_api_request("GET", CURRENCIES_ENDPOINT, params=params)

            if response_data is not None and isinstance(response_data.get("data"), list): # Проверяем что response_data не None
                new_cache = {}
                currencies = response_data["data"]
                for currency in currencies:
                    currency_id = currency.get("id")
                    if currency_id and isinstance(currency_id, str):
                        new_cache[currency_id] = currency
                self._currency_cache = new_cache
                self._currency_cache_time = now
                logger.info(f"Currency map updated. Found {len(new_cache)} currencies.")
                return self._currency_cache
            else:
                # Если response_data is None, ошибка уже залогирована в _make_api_request
                # Если response_data не None, но формат неверный:
                if response_data is not None:
                     logger.error(f"Failed to parse currency data from API in _get_currency_map. Response: {str(response_data)[:200]}...")
                logger.error("Returning previous currency cache if available due to fetch/parse error.")
                return self._currency_cache


    # --- Метод для получения всех балансов ---
    async def _get_all_wallet_balances(self, wallet_address: str) -> str:
        currency_map = await self._get_currency_map()
        if currency_map is None: # Ошибка получения карты валют
             return "⚠️ Error: Could not retrieve currency information to map balances. Please check bot logs."

        logger.info(f"Requesting all balances for wallet {wallet_address}")
        params = {'walletAddress': wallet_address,'limit': 100}
        account_data_response = await self._make_api_request("GET", ACCOUNTS_ENDPOINT, params=params)

        # Проверяем был ли ответ от API и имеет ли он нужную структуру
        if account_data_response is not None and isinstance(account_data_response.get("data"), list):
            accounts: List[Dict[str, Any]] = account_data_response["data"]

            if not accounts:
                logger.info(f"No loyalty accounts found for wallet {wallet_address}.")
                return f"ℹ️ No balances found for wallet `{wallet_address}`."

            balance_lines = [f"💰 **Balances for:** `{wallet_address}`\n"]
            found_balances = False
            for account in accounts:
                currency_id = account.get("loyaltyCurrencyId")
                amount = account.get("amount")

                if currency_id and amount is not None:
                    found_balances = True
                    currency_info = currency_map.get(currency_id) # Ищем в кэше по ID
                    if currency_info:
                        name = currency_info.get("name", "Unknown Currency")
                        symbol = currency_info.get("symbol", "???")
                        balance_lines.append(f"- **{name} ({symbol}):** `{amount}`")
                    elif currency_map: # Если кэш есть, но валюты в нем нет
                        balance_lines.append(f"- *Unknown Currency (ID: `{currency_id[:8]}`...)*: `{amount}`")
                        logger.warning(f"Found balance for unknown currency ID {currency_id} for wallet {wallet_address}")
                    # Если currency_map пустой (не None, а {}), то тоже не найдем инфо
                    elif not currency_map:
                         balance_lines.append(f"- *Currency ID `{currency_id[:8]}`...*: `{amount}` (Info unavailable)")
                         logger.warning(f"Currency map is empty, cannot map ID {currency_id} for wallet {wallet_address}")


            if not found_balances:
                 logger.info(f"Accounts found for {wallet_address}, but no valid balance entries.")
                 return f"ℹ️ No valid balance entries found for wallet `{wallet_address}`."

            return "\n".join(balance_lines)

        elif account_data_response is None: # Явная ошибка API
            # Ошибка уже залогирована в _make_api_request
            return "⚙️ Error: Failed to retrieve account balance information from the API. Please check bot logs."
        else: # Неожиданный формат ответа
             logger.error(f"Unexpected API response format when fetching balances for {wallet_address}. Response: {str(account_data_response)[:200]}...")
             return "⚙️ Error: Unexpected response format from the API when fetching balances."


    # --- Существующие методы (_find_wallet_by_social_api_filter, _find_socials_by_wallet, _process_and_send_quest_history) ---
    # --- Вставьте сюда полный код этих методов из предыдущих версий ---
    async def _find_wallet_by_social_api_filter(self, handle_type: str, handle_value: str) -> str | None:
        if handle_type not in ["discordUser", "twitterUser"]:
            logger.error(f"Unsupported social filter type: {handle_type}"); return None

        logger.info(f"Searching wallet using API filter for {handle_type}: {handle_value}")
        params = {'limit': 1, handle_type: handle_value}
        account_data_response = await self._make_api_request("GET", ACCOUNTS_ENDPOINT, params=params)

        if account_data_response and isinstance(account_data_response.get("data"), list):
            accounts = account_data_response["data"]
            if accounts:
                account_data = accounts[0]
                user_info = account_data.get("user")
                wallet_address = user_info.get("walletAddress") if isinstance(user_info, dict) else None
                if isinstance(wallet_address, str) and wallet_address:
                    logger.info(f"Found wallet {wallet_address} for {handle_type} {handle_value}")
                    return wallet_address

        logger.warning(f"Wallet not found via API filter for {handle_type} {handle_value}")
        return None

    async def _find_socials_by_wallet(self, target_address: str) -> str:
        logger.info(f"Searching socials for {target_address}")
        params = {'limit': 1, 'walletAddress': target_address}
        account_data_response = await self._make_api_request("GET", ACCOUNTS_ENDPOINT, params=params)

        if account_data_response is None:
            return "⚙️ Error contacting API or API key/config missing. Check bot logs."
        if not isinstance(account_data_response.get("data"), list) or not account_data_response["data"]:
             return f"❌ No account data found for `{target_address}`."

        account_data = account_data_response["data"][0]
        user_info = account_data.get("user", {})
        metadata_list = user_info.get("userMetadata", [])
        display_name = "N/A"; discord_handle = None; twitter_handle = None;

        if isinstance(metadata_list, list) and metadata_list:
            meta = metadata_list[0]
            if isinstance(meta, dict):
                display_name = meta.get("displayName", "N/A")
                discord_handle = meta.get("discordUser")
                twitter_handle = meta.get("twitterUser")
                if twitter_handle and not twitter_handle.startswith('@'):
                    twitter_handle = f"@{twitter_handle}"

        response_message = (f"**Socials for:** `{target_address}`\n"
                            f"**Display Name:** {display_name}\n"
                            f"**Discord:** `{discord_handle or 'Not linked'}`\n"
                            f"**Twitter/X:** `{twitter_handle or 'Not linked'}`\n")
        return response_message

    async def _process_and_send_quest_history(self, interaction: discord.Interaction, target_address: str, quest_filter: str | None):
        all_fetched_transactions = []; last_transaction_id = None; has_more_pages = True; page_count = 0
        logger.info(f"Fetching transactions directly for address {target_address}...")

        while has_more_pages and page_count < MAX_PAGES_TO_FETCH:
            if page_count > 0: await asyncio.sleep(API_REQUEST_DELAY)
            page_count += 1
            params = {'limit': PAGE_LIMIT, 'walletAddress': target_address}
            if last_transaction_id: params['startingAfter'] = last_transaction_id

            logger.info(f"Quest History: Requesting page {page_count} for {target_address}...")
            transaction_page_data = await self._make_api_request("GET", TRANSACTIONS_ENDPOINT, params=params, timeout=30)

            if not transaction_page_data:
                 try: await interaction.followup.send("⚙️ Internal error fetching quest history page. Results may be incomplete. Check logs.", ephemeral=True)
                 except discord.HTTPException: pass
                 return

            current_page_transactions = transaction_page_data.get("data", [])
            has_more_pages = transaction_page_data.get("hasNextPage", False)

            if not isinstance(current_page_transactions, list):
                logger.warning("Quest History: API response 'data' is not a list."); has_more_pages = False; continue
            if not current_page_transactions:
                logger.info("Quest History: Received empty page, stopping."); has_more_pages = False; continue

            all_fetched_transactions.extend(current_page_transactions)
            last_tx = current_page_transactions[-1]
            last_transaction_id = last_tx.get('id')
            if not last_transaction_id:
                logger.warning("Quest History: Cannot get last transaction ID from page, stopping pagination."); has_more_pages = False; continue

        warning_message = None
        if page_count >= MAX_PAGES_TO_FETCH and has_more_pages:
            warning_message = f"⚠️ Loaded maximum pages ({MAX_PAGES_TO_FETCH}). Results might be incomplete."
        logger.info(f"Finished fetching transactions for {target_address}. Total raw: {len(all_fetched_transactions)}. Filtering unique quests...")

        latest_unique_quests = {}
        if all_fetched_transactions:
            all_fetched_transactions.sort(key=lambda x: x.get('createdAt', '0'))
            for tx in all_fetched_transactions:
                if tx.get("direction") != "credit": continue
                rule_info = tx.get("loyaltyTransaction", {}).get("loyaltyRule", {})
                if not isinstance(rule_info, dict): continue
                rule_type = rule_info.get("type"); rule_name = rule_info.get("name", "").strip()
                if not rule_name: continue
                if rule_type == "check_in": continue
                if quest_filter and quest_filter.lower() not in rule_name.lower(): continue
                latest_unique_quests[rule_name.lower()] = tx

        final_quests_list = list(latest_unique_quests.values())
        final_quests_list.sort(key=lambda x: x.get('createdAt', '0'), reverse=True)
        logger.info(f"Found {len(final_quests_list)} unique relevant quests for {target_address}.")

        sent_warning = False
        message_content = None
        if warning_message:
             try:
                 await interaction.followup.send(warning_message, ephemeral=True)
                 sent_warning = True
             except discord.HTTPException: pass

        if not final_quests_list:
            filter_msg = f" matching '{quest_filter}'" if quest_filter else ""
            final_msg = f"✅ No unique completed quests{filter_msg} found for `{target_address}` (excluding Check-ins)."
            if sent_warning or interaction.message:
                try: await interaction.followup.send(final_msg, ephemeral=True)
                except discord.HTTPException: pass
            else:
                try: await interaction.edit_original_response(content=final_msg, view=None, embed=None)
                except discord.HTTPException: pass
        else:
            view = QuestHistoryPaginatorView(interaction, final_quests_list, len(final_quests_list), target_address)
            initial_page_data = await view._get_page_data()
            initial_embed = await view._create_page_embed(initial_page_data)
            if sent_warning or interaction.message:
                try:
                    view.message = await interaction.followup.send(content=message_content, embed=initial_embed, view=view, ephemeral=True)
                except discord.HTTPException: pass
            else:
                try:
                    original_message = await interaction.edit_original_response(content=message_content, embed=initial_embed, view=view)
                    # Сохраняем сообщение, чтобы view мог себя отключить по таймауту
                    if isinstance(original_message, discord.InteractionMessage):
                          view.message = original_message
                    elif isinstance(original_message, discord.Message): # На всякий случай, если API изменится
                         view.message = await interaction.original_response() # Попробуем получить исходное сообщение так
                except discord.HTTPException: pass


    # --- Статические Вспомогательные Методы ---
    @staticmethod
    def _format_datetime(datetime_str: str | None) -> str:
        if not datetime_str: return "Date unknown"
        formats_to_try = ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"]
        parsed_dt = None
        for fmt in formats_to_try:
            try:
                # Убираем Z и миллисекунды (если есть) перед парсингом
                dt_str_cleaned = datetime_str.split('.')[0].replace('Z', '')
                dt_obj = datetime.datetime.strptime(dt_str_cleaned, '%Y-%m-%dT%H:%M:%S')
                parsed_dt = dt_obj.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue

        if parsed_dt:
            return discord.utils.format_dt(parsed_dt, style='d') # Короткая дата d=MM/DD/YYYY
        else:
            logger.warning(f"Could not parse date format: {datetime_str}")
            return datetime_str

# --- Обязательная функция setup ---
async def setup(bot: commands.Bot):
    await bot.add_cog(ControlPanelCog(bot))
# --- КОНЕЦ ФАЙЛА cogs/control_panel_cog.py ---