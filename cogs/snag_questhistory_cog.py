# cogs/snag_cog.py
# --- FINAL CODE WITH DIRECT ADDRESS FILTERING (Version 2024-04-24 v14) ---
import discord
from discord.ext import commands
import os
import re
import aiohttp
import logging
from datetime import datetime, timezone
import json
import asyncio
import math

logger = logging.getLogger(__name__)
EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")

# --- API Constants ---
SNAG_API_BASE_URL = "https://admin.snagsolutions.io"
# Используем ТОЛЬКО эндпоинт транзакций
TRANSACTIONS_ENDPOINT = "/api/loyalty/transaction_entries"
SNAG_API_KEY_HEADER = "X-API-KEY"
PAGE_LIMIT = 1000 # Запрашиваем по 1000 транзакций за раз
MAX_PAGES_TO_FETCH = 20 # Лимит страниц на всякий случай
API_REQUEST_DELAY = 2.0 # Задержка между запросами страниц
# --- Discord Pagination ---
ITEMS_PER_PAGE = 10
VIEW_TIMEOUT = 300.0
# ----------------------------------------------------

# --- Класс Пагинатора (остается без изменений) ---
class QuestPaginatorView(discord.ui.View):
    """View for paginating through quest history embeds."""
    current_page : int = 1
    sep : int = ITEMS_PER_PAGE

    def __init__(self, ctx: commands.Context, data: list, total_unique_quests: int):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.ctx = ctx
        self.data = data
        self.total_unique_quests = total_unique_quests
        self.max_pages = math.ceil(len(self.data) / self.sep) if self.data else 1
        self.message: discord.Message | None = None
        self._update_buttons()

    async def _get_page_data(self) -> list:
        base = (self.current_page - 1) * self.sep
        return self.data[base:base + self.sep]

    async def _create_page_embed(self, page_data: list) -> discord.Embed:
        original_address = self.ctx.kwargs.get('evm_address', 'N/A') # Получаем адрес из контекста
        embed = discord.Embed(
            title="Latest Completed Quests for:",
            description=f"`{original_address}` (excluding Check-ins)",
            color=discord.Color.green()
        )
        if not page_data and self.current_page == 1:
             embed.description += "\n\nNo matching quests found."
        else:
            for tx in page_data:
                amount = tx.get("amount", 0)
                created_at_str = tx.get("createdAt")
                loyalty_tx_details = tx.get("loyaltyTransaction", {})
                rule_details = loyalty_tx_details.get("loyaltyRule", {})
                rule_name = rule_details.get("name", "Unknown Action").strip()
                date_formatted = SnagCog._format_datetime(created_at_str)
                field_name = f"✅ {rule_name}"
                field_value = f"**Earned:** `{amount}` | **Completed:** {date_formatted}"
                embed.add_field(name=field_name, value=field_value, inline=False)
        embed.set_footer(text=f"Page {self.current_page} of {self.max_pages} | Total Unique Quests: {self.total_unique_quests}")
        embed.timestamp = discord.utils.utcnow()
        return embed

    def _update_buttons(self) -> None:
        if hasattr(self, 'first_page'): self.first_page.disabled = self.current_page == 1
        if hasattr(self, 'prev_page'): self.prev_page.disabled = self.current_page == 1
        if hasattr(self, 'next_page'): self.next_page.disabled = self.current_page >= self.max_pages
        if hasattr(self, 'last_page'): self.last_page.disabled = self.current_page >= self.max_pages

    async def show_current_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self._update_buttons()
        page_data = await self._get_page_data()
        embed = await self._create_page_embed(page_data)
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="|< First", style=discord.ButtonStyle.secondary, row=0)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page != 1: self.current_page = 1; await self.show_current_page(interaction)
        else: await interaction.response.defer()
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
                changed = False; view_children = list(self.children)
                for item in view_children:
                    if isinstance(item, discord.ui.Button) and not item.disabled: item.disabled = True; changed = True
                if changed: await self.message.edit(view=self); logger.info(f"View {self.message.id} timed out.")
            except Exception as e: logger.error(f"Error disabling view on timeout for {self.message.id}: {e}")
        else: logger.warning("View timed out, msg ref lost.")
# --- Конец класса QuestPaginatorView ---


# --- Класс Кога Snag ---
class SnagCog(commands.Cog, name="Snag API"):
    """Commands to interact with the Snag Solutions API."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot; self.api_key = getattr(bot, 'snag_api_key', None)
        self.http_session = aiohttp.ClientSession(base_url=SNAG_API_BASE_URL)
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")
        if not self.api_key: logger.warning(f"SNAG_API_KEY missing for {self.__class__.__name__}.")

    async def cog_unload(self):
        await self.http_session.close(); logger.info(f"Cog '{self.__class__.__name__}' unloaded.")

    # Метод _find_loyalty_account_id БОЛЬШЕ НЕ НУЖЕН

    # --- Основная команда пользователя (использует ПРЯМОЙ фильтр по адресу) ---
    @commands.command(
        name='questhistory', aliases=['qh', 'loyalty'],
        help="Shows completed quests. Optional: Add keywords to filter by name.\nExample: !questhistory 0x123... Follow Twitter"
    )
    async def quest_history(self, ctx: commands.Context, evm_address: str, *, quest_filter: str | None = None):
        if not self.api_key: await ctx.send("⛔ Error: API key is not configured."); return
        target_address_normalized = evm_address.lower()
        if not EVM_ADDRESS_PATTERN.match(target_address_normalized): await ctx.send(f"⚠️ Invalid EVM address format: `{evm_address}`."); return
        organization_id = '8f48e0f1-f648-4b0e-99be-3a3c25597a97' # ЗАМЕНИТЬ!
        website_id = 'd88e4c28-d8cc-45ff-8cff-1180cdc1e87c'      # ЗАМЕНИТЬ!

        async with ctx.typing():
            # --- Загрузка ВСЕХ транзакций для ДАННОГО АДРЕСА ---
            all_fetched_transactions = []; last_transaction_id = None; has_more_pages = True; page_count = 0
            logger.info(f"Fetching transactions for address {target_address_normalized} (Org: {organization_id}, Web: {website_id})...")

            while has_more_pages and page_count < MAX_PAGES_TO_FETCH:
                if page_count > 0: await asyncio.sleep(API_REQUEST_DELAY) # ЗАДЕРЖКА
                page_count += 1; response_text = ""
                try:
                    api_url_path = TRANSACTIONS_ENDPOINT
                    headers = {SNAG_API_KEY_HEADER: self.api_key}
                    # --- ПАРАМЕТРЫ ЗАПРОСА С ФИЛЬТРОМ ПО АДРЕСУ ---
                    params = {
                        'limit': PAGE_LIMIT,
                        'organizationId': organization_id,
                        'websiteId': website_id,
                        'walletAddress': target_address_normalized # Прямой фильтр по адресу!
                    }
                    # --- ---
                    if last_transaction_id: params['startingAfter'] = last_transaction_id
                    logger.info(f"Transactions: Requesting page {page_count} for address {target_address_normalized}")

                    async with self.http_session.get(api_url_path, headers=headers, params=params, timeout=30) as response:
                        logger.info(f"Transactions: Page {page_count}: Status {response.status}")
                        response_text = await response.text(); response.raise_for_status()
                        try: data = json.loads(response_text)
                        except json.JSONDecodeError: logger.error("Transactions: JSON Decode Error"); has_more_pages = False; continue
                        current_page_transactions = data.get("data", []); has_more_pages = data.get("hasNextPage", False)
                        if not isinstance(current_page_transactions, list): logger.warning("Transactions: 'data' not list."); has_more_pages = False; continue
                        if not current_page_transactions: logger.info("Transactions: Empty page."); has_more_pages = False; continue
                        all_fetched_transactions.extend(current_page_transactions)
                        last_transaction_id = current_page_transactions[-1].get('id')
                        if not last_transaction_id: logger.warning("Transactions: Cannot get last ID."); has_more_pages = False; continue
                        logger.info(f"Transactions: Page {page_count}: Loaded {len(current_page_transactions)}. Total: {len(all_fetched_transactions)}. More: {has_more_pages}")
                except aiohttp.ClientResponseError as e: logger.error(f"Transactions: HTTP Error {e.status}"); has_more_pages = False; await ctx.send(f"⛔ API Error fetching transactions ({e.status}).")
                except asyncio.TimeoutError: logger.error(f"Transactions: Timeout on page {page_count}"); has_more_pages = False; await ctx.send("API timeout while fetching transactions.")
                except Exception as e: logger.exception("Transactions: Unexpected error"); has_more_pages = False; await ctx.send("⚙️ Internal error fetching transactions.")
            # --- Конец цикла загрузки транзакций ---

            if page_count >= MAX_PAGES_TO_FETCH and has_more_pages: await ctx.send(f"⚠️ Loaded maximum pages ({MAX_PAGES_TO_FETCH}). Results might be incomplete.")
            logger.info(f"Finished fetching for {target_address_normalized}. Total transactions: {len(all_fetched_transactions)}. Filtering...")

            # --- Фильтрация (credit, check_in, quest_filter) и Группировка уникальных ---
            latest_unique_quests = {}
            if all_fetched_transactions:
                all_fetched_transactions.sort(key=lambda x: x.get('createdAt', '0')) # Сортировка по ВОЗРАСТАНИЮ даты
                for tx in all_fetched_transactions:
                    if tx.get("direction") != "credit": continue
                    rule_info = tx.get("loyaltyTransaction", {}).get("loyaltyRule", {})
                    if not isinstance(rule_info, dict): continue
                    rule_type = rule_info.get("type"); rule_id = rule_info.get("id"); rule_name = rule_info.get("name", "")
                    if rule_type == "check_in": continue
                    if not isinstance(rule_id, str) or not rule_id: continue
                    if quest_filter and quest_filter.lower() not in rule_name.lower(): continue
                    # Используем НАЗВАНИЕ для уникальности, перезаписываем старые
                    latest_unique_quests[rule_name.strip().lower()] = tx

            final_quests_list = list(latest_unique_quests.values())
            final_quests_list.sort(key=lambda x: x.get('createdAt', '0'), reverse=True) # Сортировка для отображения
            logger.info(f"Filtering complete. Found {len(final_quests_list)} unique relevant quests.")

            # --- Отправка результата ---
            if not final_quests_list:
                filter_msg = f" matching '{quest_filter}'" if quest_filter else ""
                await ctx.send(f"✅ No unique completed quests{filter_msg} found for `{evm_address}` (excluding Check-ins).")
            else:
                ctx.kwargs = {'evm_address': evm_address} # Сохраняем для Embed
                view = QuestPaginatorView(ctx, final_quests_list, len(final_quests_list))
                initial_page_data = await view._get_page_data()
                initial_embed = await view._create_page_embed(initial_page_data)
                view.message = await ctx.send(embed=initial_embed, view=view)
        # --- Конец async with ctx.typing() ---
    # --- Конец quest_history ---

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
    if not getattr(bot, 'snag_api_key', None): logger.error("Failed to load SnagCog: 'snag_api_key' missing."); return
    await bot.add_cog(SnagCog(bot))
# --- КОНЕЦ ФАЙЛА cogs/snag_cog.py ---