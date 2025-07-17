# cogs/mass_block_cog.py
import discord
from discord import app_commands
from discord.ext import commands
import logging
import asyncio
import re
from typing import List, Dict, Any, Optional

from utils.snag_api_client import SnagApiClient
from utils.checks import is_admin_in_guild # Импортируем нашу проверку прав

logger = logging.getLogger(__name__)

EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")
MAX_FILE_SIZE = 1024 * 100  # 100 KB лимит на размер файла

# --- View для подтверждения массового действия ---
class MassActionConfirmView(discord.ui.View):
    def __init__(self, cog_instance: "MassBlockCog", found_wallets: List[Dict[str, Any]], original_interaction: discord.Interaction):
        super().__init__(timeout=300.0) # 5 минут на принятие решения
        self.cog = cog_instance
        self.found_wallets = found_wallets # Список словарей с данными пользователей, которых нашли
        self.original_interaction = original_interaction
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Убедимся, что только автор команды может нажимать кнопки
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message("Только пользователь, вызвавший команду, может выполнять это действие.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(content="Время на выполнение массового действия истекло. Кнопки удалены.", view=None, embed=None)
            except discord.HTTPException:
                pass # Сообщение уже могло быть удалено
        self.stop()

    async def _perform_mass_action(self, interaction: discord.Interaction, block_flag: bool):
        # Отключаем кнопки и показываем статус
        for item in self.children:
            item.disabled = True
        action_name = "Блокировка" if block_flag else "Разблокировка"
        await interaction.response.edit_message(content=f"Выполняется **{action_name}** для {len(self.found_wallets)} кошельков...", view=self, embed=None)

        results = await self.cog.process_mass_update(self.found_wallets, block_flag, interaction.user)
        
        success_count = results['success']
        fail_count = results['failed']
        
        embed = discord.Embed(
            title=f"Отчет о массовой операции: {action_name}",
            color=discord.Color.green() if fail_count == 0 else discord.Color.orange()
        )
        embed.description = f"Обработано кошельков: {len(self.found_wallets)}"
        embed.add_field(name="✅ Успешно", value=str(success_count), inline=True)
        embed.add_field(name="❌ Ошибки", value=str(fail_count), inline=True)

        if results['failed_wallets']:
            failed_list = "\n".join([f"`{addr}`" for addr in results['failed_wallets']])
            embed.add_field(name="Кошельки с ошибками", value=failed_list[:1024], inline=False)
            
        await interaction.edit_original_response(content="Массовая операция завершена.", embed=embed, view=None)
        self.stop()


    @discord.ui.button(label="🔴 Заблокировать все", style=discord.ButtonStyle.danger, custom_id="mass_block:block_all")
    async def block_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._perform_mass_action(interaction, True)

    @discord.ui.button(label="🟢 Разблокировать все", style=discord.ButtonStyle.success, custom_id="mass_block:unblock_all")
    async def unblock_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._perform_mass_action(interaction, False)


# --- Класс Кога ---
class MassBlockCog(commands.Cog, name="Mass Block Tool"):
    """
    Инструмент для массовой проверки и блокировки/разблокировки кошельков из файла.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snag_client: Optional[SnagApiClient] = getattr(bot, 'snag_client', None)
        if not self.snag_client:
            logger.error(f"{self.__class__.__name__}: Main SnagApiClient not found! Functionality will be disabled.")
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    def get_block_status_from_userdata(self, user_data: Dict[str, Any]) -> bool:
        """Извлекает статус блокировки из данных пользователя."""
        user_metadata_list = user_data.get("userMetadata", [])
        if not user_metadata_list:
            return False
        return user_metadata_list[0].get("isBlocked", False)
        
    async def _get_wallets_from_file(self, file: discord.Attachment) -> List[str]:
        """Читает, декодирует и валидирует адреса из файла."""
        content = await file.read()
        lines = content.decode('utf-8').splitlines()
        
        valid_wallets = []
        for line in lines:
            address = line.strip().lower()
            if EVM_ADDRESS_PATTERN.match(address):
                valid_wallets.append(address)
        return list(set(valid_wallets)) # Удаляем дубликаты

    @app_commands.command(name="mass_block_tool", description="Массовая проверка и блокировка/разблокировка кошельков из .txt файла.")
    @is_admin_in_guild() # <-- Используем нашу проверку прав!
    @app_commands.describe(wallets_file="Файл .txt с EVM адресами, каждый на новой строке.")
    async def mass_block_tool(self, interaction: discord.Interaction, wallets_file: discord.Attachment):
        await interaction.response.defer(ephemeral=True, thinking=True)

        # --- 1. Валидация файла ---
        if not wallets_file.filename.lower().endswith('.txt'):
            await interaction.followup.send("⚠️ Ошибка: Пожалуйста, загрузите файл в формате `.txt`.", ephemeral=True)
            return
        if wallets_file.size > MAX_FILE_SIZE:
            await interaction.followup.send(f"⚠️ Ошибка: Файл слишком большой (лимит {MAX_FILE_SIZE / 1024} KB).", ephemeral=True)
            return
        
        wallets = await self._get_wallets_from_file(wallets_file)
        if not wallets:
            await interaction.followup.send("⚠️ В файле не найдено валидных EVM адресов.", ephemeral=True)
            return
            
        logger.info(f"User {interaction.user.name} initiated mass check for {len(wallets)} wallets.")

        # --- 2. Параллельная проверка статусов ---
        tasks = [self.snag_client.get_user_data(wallet_address=w) for w in wallets]
        responses = await asyncio.gather(*tasks)

        # --- 3. Обработка результатов и формирование отчета ---
        found_wallets: List[Dict[str, Any]] = []
        not_found_wallets: List[str] = []
        api_error_wallets: List[str] = []

        # Категоризация результатов
        for wallet, response in zip(wallets, responses):
            if response and not response.get("error") and isinstance(response.get("data"), list) and response["data"]:
                found_wallets.append(response["data"][0])
            elif response and response.get("error"):
                api_error_wallets.append(wallet)
            else: # Не найдены или пустой ответ
                not_found_wallets.append(wallet)
        
        # --- 4. Создание Embed с отчетом о статусах ---
        embed = discord.Embed(
            title=f"Отчет по {len(wallets)} кошелькам",
            description="Ниже представлена информация о текущем статусе каждого кошелька.",
            color=discord.Color.blue()
        )
        embed.set_footer(text="Нажмите на кнопки ниже, чтобы заблокировать или разблокировать ВСЕ найденные кошельки.")
        
        # Разделение найденных кошельков на заблокированных и нет
        blocked = [f"`{w.get('walletAddress')}`" for w in found_wallets if self.get_block_status_from_userdata(w)]
        not_blocked = [f"`{w.get('walletAddress')}`" for w in found_wallets if not self.get_block_status_from_userdata(w)]

        if blocked: embed.add_field(name=f"🔴 Заблокированы ({len(blocked)})", value="\n".join(blocked)[:1024], inline=False)
        if not_blocked: embed.add_field(name=f"🟢 Не заблокированы ({len(not_blocked)})", value="\n".join(not_blocked)[:1024], inline=False)
        if not_found_wallets: embed.add_field(name=f"❓ Не найдены в системе ({len(not_found_wallets)})", value="\n".join(f"`{w}`" for w in not_found_wallets)[:1024], inline=False)
        if api_error_wallets: embed.add_field(name=f"⚠️ Ошибка API ({len(api_error_wallets)})", value="\n".join(f"`{w}`" for w in api_error_wallets)[:1024], inline=False)
        
        if not found_wallets:
            await interaction.followup.send(embed=embed, ephemeral=True)
            return # Если нет кошельков для действия, кнопки не нужны

        # --- 5. Отправка отчета и кнопок ---
        view = MassActionConfirmView(self, found_wallets, interaction)
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        view.message = message

    async def process_mass_update(self, wallets_data: List[Dict[str, Any]], block_flag: bool, user: discord.User) -> Dict:
        """Выполняет массовое обновление статусов кошельков."""
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

        # Выполняем запросы и собираем результаты, включая исключения
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
    # Проверяем, что есть все необходимое для работы кога
    if not getattr(bot, 'snag_client', None) or not bot.snag_client._api_key:
        logger.critical("CRITICAL: Snag API client or API key is missing. MassBlockCog will NOT be loaded.")
        return
    if not os.getenv('ADMIN_GUILD_ID') or not os.getenv('RANGER_ROLE_ID'):
         logger.critical("CRITICAL: ADMIN_GUILD_ID or RANGER_ROLE_ID not set. MassBlockCog will NOT be loaded as it relies on admin checks.")
         return

    await bot.add_cog(MassBlockCog(bot))