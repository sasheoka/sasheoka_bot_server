# cogs/ticket_investigator_cog.py
import discord
from discord.ext import commands
from discord import app_commands
import logging
import re
import os
import asyncio
from typing import Optional, Dict, Any

from utils.snag_api_client import SnagApiClient
from utils.checks import is_admin_in_guild

logger = logging.getLogger(__name__)

# --- КОНСТАНТЫ ---
EVM_ADDRESS_PATTERN = re.compile(r"0x[a-fA-F0-9]{40}")
INVESTIGATION_CATEGORY_ID = int(os.getenv('INVESTIGATION_CATEGORY_ID')) # Загружаем из .env или используем дефолтный

# --- СООБЩЕНИЯ ДЛЯ КОПИПАСТЫ ---
BAN_MESSAGE = """Thank you for writing in.

Upon review, your account has been blocked for suspicious activity consistent with Sybil behavior.

Participation in the Camp Network Climb to the Summit is not guaranteed and covered by the Camp Network Terms of Use. Camp may, at its sole discretion, remove wallets that engage behavior prohibited by the Terms of Use. 

You may review these terms and conditions here: https://campaignlabs.notion.site/Camp-Foundation-Terms-of-Use-eba426fcea184a90bc4bd26fd795bdbe

We have determined through an analysis of your wallet's behavior that your wallet has not abided by our guidelines. You may review Section 7 of the Terms to understand more about our policy.

This decision is final."""

HMM_MESSAGE = """Hello, thank you for writing in. After review, your account has been blocked due to suspicious activity that appears to be consistent with Sybil behavior.

Our analysis indicates that your wallet has not complied with our guidelines. 

We will thoroughly investigate this matter and will get back to you once we have the results. Please be patient, as this process may take time due to a high volume of requests. We appreciate your understanding."""


# --- View с кнопками для действий ---
class InvestigationActionView(discord.ui.View):
    def __init__(self, ticket_channel: discord.TextChannel, ticket_creator: discord.Member, cog_instance: "TicketInvestigatorCog"):
        super().__init__(timeout=1800.0) # 30 минут таймаут
        self.ticket_channel = ticket_channel
        self.ticket_creator = ticket_creator
        self.cog = cog_instance
        self.message: Optional[discord.Message] = None

    async def _disable_buttons(self, interaction: discord.Interaction, processing_message: str):
        """Отключает все кнопки и показывает сообщение о выполнении."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        
        try:
            await interaction.response.edit_message(content=processing_message, view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Ban & Close", style=discord.ButtonStyle.danger, custom_id="investigate:ban_v1")
    async def ban_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._disable_buttons(interaction, "⏳ Processing Ban...")

        if not self.ticket_creator:
            await interaction.followup.send("❌ Cannot ban: User who created the ticket is no longer on the server.", ephemeral=True)
            self.stop()
            return
        
        TICKET_CLOSE_COMMAND = "/close"
            
        try:
            # --- ИЗМЕНЕННЫЙ ПОРЯДОК ДЕЙСТВИЙ ---

            # 1. Отправить сообщение в тикет
            await self.ticket_channel.send(BAN_MESSAGE)
            logger.info(f"Sent ban message to ticket {self.ticket_channel.name} ({self.ticket_channel.id}).")
            
            # 2. Закрыть тикет с помощью команды другого бота
            # Бот отправит команду в чат, и другой бот на нее среагирует
            await self.ticket_channel.send(TICKET_CLOSE_COMMAND)
            logger.info(f"Sent close command '{TICKET_CLOSE_COMMAND}' to ticket {self.ticket_channel.id}.")

            # Даем боту-тикет-менеджеру пару секунд на обработку закрытия и сохранение логов
            await asyncio.sleep(2) 

            # 3. Забанить пользователя
            await self.ticket_creator.ban(reason="Snag block (handled via Ticket Investigator)")
            logger.info(f"Banned user {self.ticket_creator.name} ({self.ticket_creator.id}).")

            await interaction.edit_original_response(content="✅ **Success:** User has been banned and the ticket is closed.", view=None, embed=None)

        except discord.Forbidden:
            logger.error(f"Forbidden: Bot lacks permissions to ban {self.ticket_creator.id} or delete channel {self.ticket_channel.id}.")
            await interaction.edit_original_response(content="❌ **Permission Error:** The bot lacks permissions to ban the user or delete the channel. Check bot roles.", view=None, embed=None)
        except Exception as e:
            logger.exception(f"An error occurred during the ban process for ticket {self.ticket_channel.id}.")
            await interaction.edit_original_response(content=f"❌ **An unexpected error occurred:** `{e}`", view=None, embed=None)
        
        self.stop()

    @discord.ui.button(label="Hmm... (Investigate)", style=discord.ButtonStyle.secondary, custom_id="investigate:hmm_v1")
    async def hmm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._disable_buttons(interaction, "⏳ Moving ticket for further investigation...")
        
        investigation_category = interaction.guild.get_channel(INVESTIGATION_CATEGORY_ID)
        if not investigation_category or not isinstance(investigation_category, discord.CategoryChannel):
            await interaction.edit_original_response(content=f"❌ **Error:** Category with ID `{INVESTIGATION_CATEGORY_ID}` not found.", view=None, embed=None)
            self.stop()
            return

        try:
            # 1. Отправить сообщение
            await self.ticket_channel.send(HMM_MESSAGE)
            logger.info(f"Sent 'hmm' message to ticket {self.ticket_channel.name} ({self.ticket_channel.id}).")
            
            # 2. Переместить тикет
            await self.ticket_channel.edit(category=investigation_category, reason="Ticket moved for further investigation.")
            logger.info(f"Moved ticket {self.ticket_channel.id} to category '{investigation_category.name}'.")

            await interaction.edit_original_response(content=f"✅ **Success:** Ticket has been moved to **#{investigation_category.name}** for further review.", view=None, embed=None)

        except discord.Forbidden:
            logger.error(f"Forbidden: Bot lacks permissions to move channel {self.ticket_channel.id}.")
            await interaction.edit_original_response(content="❌ **Permission Error:** The bot lacks permissions to move the channel.", view=None, embed=None)
        except Exception as e:
            logger.exception(f"An error occurred during the 'hmm' process for ticket {self.ticket_channel.id}.")
            await interaction.edit_original_response(content=f"❌ **An unexpected error occurred:** `{e}`", view=None, embed=None)

        self.stop()


# --- Основной класс кога ---
class TicketInvestigatorCog(commands.Cog, name="Ticket Investigator"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snag_client: Optional[SnagApiClient] = getattr(bot, 'snag_client', None)
        if not self.snag_client:
            logger.error(f"{self.__class__.__name__}: SnagApiClient not found! Cog will not work.")
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")
        
    @app_commands.command(name="investigate_ticket", description="Investigates a user's block claim from a ticket.")
    @is_admin_in_guild() # Используем вашу проверку на админа
    async def investigate_ticket(self, interaction: discord.Interaction, ticket_channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        if not self.snag_client or not self.snag_client._api_key:
            await interaction.followup.send("⚠️ Snag API client is not configured. Cannot proceed.", ephemeral=True)
            return

        # 1. Найти адрес и создателя тикета
        wallet_address: Optional[str] = None
        ticket_creator: Optional[discord.Member] = None

        # Пытаемся получить создателя из пермишенов канала (для некоторых ботов-тикетов)
        # Если не получится, будем искать по автору первого сообщения
        if isinstance(ticket_channel, discord.Thread) and ticket_channel.owner_id:
             ticket_creator = interaction.guild.get_member(ticket_channel.owner_id)

        # Сканируем историю, чтобы найти создателя (если не нашли выше) и адрес кошелька
        async for message in ticket_channel.history(limit=15, oldest_first=True):
            if not message.author.bot:
                # Если создатель еще не определен, то первый автор-не-бот и есть создатель
                if not ticket_creator:
                    ticket_creator = message.author
                
                # Ищем адрес в его сообщении
                match = EVM_ADDRESS_PATTERN.search(message.content)
                if match:
                    wallet_address = match.group(0)
                    # Если нашли и адрес, и создателя, можно выйти из цикла
                    if ticket_creator:
                        break # Нашли адрес, выходим из цикла

        if not wallet_address:
            await interaction.followup.send(f"❌ Could not find a valid EVM wallet address in the first 15 messages of {ticket_channel.mention}.", ephemeral=True)
            return
            
        if not ticket_creator:
            await interaction.followup.send(f"❌ Could not determine the user who created the ticket {ticket_channel.mention}. They may have left the server.", ephemeral=True)
            return

        # 2. Получить данные из Snag API
        user_data_response = await self.snag_client.get_user_data(wallet_address=wallet_address)
        
        if not user_data_response or user_data_response.get("error") or not isinstance(user_data_response.get("data"), list) or not user_data_response["data"]:
            error_message = user_data_response.get("message", "API request failed or user not found.") if user_data_response else "No response from API."
            await interaction.followup.send(f"❌ **API Error for wallet `{wallet_address}`:**\n`{error_message}`", ephemeral=True)
            return
        
        # 3. Собрать информацию для вывода
        user_data = user_data_response["data"][0]
        metadata = user_data.get("userMetadata", [{}])[0]

        is_blocked = metadata.get("isBlocked", False)
        status_text = "🔴 Blocked" if is_blocked else "🟢 Not Blocked"
        
        twitter_handle = metadata.get("twitterUser")
        twitter_link = f"[@{twitter_handle}](https://twitter.com/{twitter_handle})" if twitter_handle else "Not linked"

        debank_link = f"https://debank.com/profile/{wallet_address}"
        explorer_link = f"https://basecamp.cloud.blockscout.com/address/{wallet_address}"

        # 4. Создать Embed
        embed_color = discord.Color.red() if is_blocked else discord.Color.green()
        embed = discord.Embed(
            title=f"Investigation for {ticket_creator.display_name}",
            description=f"Analysis of wallet `{wallet_address}` from ticket {ticket_channel.mention}.",
            color=embed_color
        )
        embed.set_thumbnail(url=ticket_creator.display_avatar.url)
        
        embed.add_field(name="👤 Discord User", value=f"{ticket_creator.mention} (`{ticket_creator.name}`)", inline=False)
        embed.add_field(name="📅 Account Created", value=f"<t:{int(ticket_creator.created_at.timestamp())}:R>", inline=True)
        embed.add_field(name="📥 Joined Server", value=f"<t:{int(ticket_creator.joined_at.timestamp())}:R>", inline=True)
        embed.add_field(name="💼 Wallet Address", value=f"`{wallet_address}`", inline=False)
        embed.add_field(name="💳 Wallet Status", value=f"**{status_text}**", inline=False)
        embed.add_field(name="🔗 External Links", value=f"[Debank]({debank_link}) • [Blockscout]({explorer_link})", inline=False)
        embed.add_field(name="🐦 Twitter/X", value=twitter_link, inline=False)
        
        embed.set_footer(text=f"User ID: {ticket_creator.id}")

        # 5. Отправить ответ с кнопками
        view = InvestigationActionView(ticket_channel, ticket_creator, self)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    # Убедимся, что основной клиент Snag существует
    if not getattr(bot, 'snag_client', None):
        logger.critical("CRITICAL: Main Snag API client is missing. TicketInvestigatorCog will NOT be loaded.")
        return
    await bot.add_cog(TicketInvestigatorCog(bot))