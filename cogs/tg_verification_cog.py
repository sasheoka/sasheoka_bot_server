# cogs/telegram_verification_cog.py
import discord
from discord.ext import commands
from discord import app_commands
import logging
import asyncio
import aiohttp
from typing import Optional
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Константы
TEMP_ACCESS_DURATION_MINUTES = 30  # Длительность временного доступа в минутах
VERIFICATION_CHANNEL_ID = 1307784438767161385  # Замените на ID канала, где будет кнопка
TARGET_CHANNEL_ID = 1384466812766257193  # Замените на ID канала, куда дается доступ
TELEGRAM_GROUP_ID = -1002193560609  # Замените на ID Telegram-группы (обычно начинается с -100)

class TelegramVerificationView(discord.ui.View):
    def __init__(self, cog_instance: "TelegramVerificationCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    @discord.ui.button(label="🔒 Verify Telegram Membership", style=discord.ButtonStyle.primary, custom_id="telegram:verify")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            await self.cog.verify_telegram_membership(interaction)
        except Exception as e:
            logger.error(f"Error processing Telegram verification for {interaction.user.name}: {e}", exc_info=True)
            await interaction.followup.send("⚙️ An unexpected error occurred during verification.", ephemeral=True)

class TelegramVerificationCog(commands.Cog, name="Telegram Verification"):
    """Cog to verify Telegram group membership and grant temporary Discord channel access."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        load_dotenv()
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_api_url = f"https://api.telegram.org/bot{self.telegram_token}"
        self.session: Optional[aiohttp.ClientSession] = None

        if not self.telegram_token:
            logger.critical("CRITICAL: TELEGRAM_BOT_TOKEN not found in .env. TelegramVerificationCog functionality will fail.")
        
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    async def cog_load(self):
        self.session = aiohttp.ClientSession()
        self.bot.add_view(TelegramVerificationView(self))
        logger.info(f"Cog '{self.__class__.__name__}' initialized with persistent view.")

    async def cog_unload(self):
        if self.session:
            await self.session.close()
            logger.info(f"Closed aiohttp session for {self.__class__.__name__}.")

    async def verify_telegram_membership(self, interaction: discord.Interaction):
        if not self.telegram_token or not self.session:
            await interaction.followup.send("⚠️ Telegram verification is not properly configured.", ephemeral=True)
            return

        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.followup.send("⚠️ This command can only be used in a server.", ephemeral=True)
            return

        # Получаем Telegram-username из профиля пользователя в Discord
        user = interaction.user
        discord_handle = user.name if user.discriminator == '0' else f"{user.name}#{user.discriminator}"
        telegram_username = None

        # Проверяем, указан ли Telegram-username в профиле через Snag API
        snag_client = getattr(self.bot, 'snag_client', None)
        if snag_client and snag_client._api_key:
            account_data = await snag_client.get_account_by_social("discordUser", discord_handle)
            if account_data and isinstance(account_data.get("data"), list) and account_data["data"]:
                user_info = account_data["data"][0].get("user", {})
                telegram_username = user_info.get("telegramUser")
                logger.debug(f"Found Telegram username {telegram_username} for Discord user {discord_handle} via Snag API.")

        if not telegram_username:
            await interaction.followup.send(
                "⚠️ Your Telegram username is not linked in the Snag Loyalty System. "
                "Please link it at https://loyalty.campnetwork.xyz/home?editProfile=1&modalTab=social",
                ephemeral=True
            )
            return

        # Проверяем членство в Telegram-группе
        try:
            async with self.session.get(
                f"{self.telegram_api_url}/getChatMember",
                params={"chat_id": TELEGRAM_GROUP_ID, "user_id": telegram_username}
            ) as response:
                result = await response.json()
                logger.debug(f"Telegram API response for {telegram_username}: {result}")

                if not result.get("ok"):
                    await interaction.followup.send(
                        "⚠️ Failed to verify Telegram membership. Please ensure your Telegram username is correct.",
                        ephemeral=True
                    )
                    return

                status = result["result"].get("status")
                if status in ["member", "administrator", "creator"]:
                    # Пользователь в группе, даем доступ
                    await self.grant_channel_access(interaction)
                else:
                    await interaction.followup.send(
                        f"⚠️ You are not a member of the specified Telegram group (status: {status}).",
                        ephemeral=True
                    )
        except aiohttp.ClientError as e:
            logger.error(f"Telegram API request failed for {telegram_username}: {e}", exc_info=True)
            await interaction.followup.send("⚠️ Failed to contact Telegram API. Please try again later.", ephemeral=True)

    async def grant_channel_access(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user
        target_channel = guild.get_channel(TARGET_CHANNEL_ID)

        if not target_channel or not isinstance(target_channel, discord.TextChannel):
            try:
                target_channel = await self.bot.fetch_channel(TARGET_CHANNEL_ID)
                if not isinstance(target_channel, discord.TextChannel):
                    logger.error(f"Target channel {TARGET_CHANNEL_ID} is not a TextChannel.")
                    await interaction.followup.send("⚠️ Target channel is misconfigured.", ephemeral=True)
                    return
            except discord.NotFound:
                logger.error(f"Target channel {TARGET_CHANNEL_ID} not found.")
                await interaction.followup.send("⚠️ Target channel not found.", ephemeral=True)
                return
            except discord.Forbidden:
                logger.error(f"No permission to access target channel {TARGET_CHANNEL_ID}.")
                await interaction.followup.send("⚠️ Bot lacks permission to access the target channel.", ephemeral=True)
                return

        # Проверяем, есть ли уже доступ
        overwrite = target_channel.overwrites_for(user)
        if overwrite.view_channel is True:
            await interaction.followup.send("ℹ You already have access to the target channel.", ephemeral=True)
            logger.info(f"User {user.name} already has access to channel {TARGET_CHANNEL_ID}.")
            return

        # Даем временный доступ
        try:
            await target_channel.set_permissions(
                user,
                view_channel=True,
                reason=f"Granted temporary access via Telegram verification by {user.name}"
            )
            logger.info(f"Granted temporary access to channel {TARGET_CHANNEL_ID} for user {user.name} for {TEMP_ACCESS_DURATION_MINUTES} minutes.")

            await interaction.followup.send(
                f"✅ Access granted to <#{TARGET_CHANNEL_ID}> for {TEMP_ACCESS_DURATION_MINUTES} minutes!",
                ephemeral=True
            )

            # Планируем удаление доступа
            self.bot.loop.create_task(
                self.revoke_channel_access(target_channel, user, TEMP_ACCESS_DURATION_MINUTES)
            )

        except discord.Forbidden:
            logger.error(f"Bot lacks permission to set permissions for channel {TARGET_CHANNEL_ID}.")
            await interaction.followup.send("⚠️ Bot lacks permission to modify channel permissions.", ephemeral=True)
        except discord.HTTPException as e:
            logger.error(f"Failed to set permissions for channel {TARGET_CHANNEL_ID}: {e}", exc_info=True)
            await interaction.followup.send("⚠️ Failed to grant channel access.", ephemeral=True)

    async def revoke_channel_access(self, channel: discord.TextChannel, user: discord.Member, duration_minutes: int):
        await asyncio.sleep(duration_minutes * 60)
        try:
            await channel.set_permissions(
                user,
                overwrite=None,
                reason=f"Revoked temporary access after {duration_minutes} minutes."
            )
            logger.info(f"Revoked access to channel {channel.id} for user {user.name} after {duration_minutes} minutes.")
            try:
                await user.send(f"ℹ Your temporary access to <#{channel.id}> has expired.")
            except discord.Forbidden:
                logger.warning(f"Could not DM {user.name} about access revocation (DMs disabled).")
        except discord.Forbidden:
            logger.error(f"Bot lacks permission to revoke permissions for channel {channel.id}.")
        except discord.HTTPException as e:
            logger.error(f"Failed to revoke permissions for channel {channel.id}: {e}", exc_info=True)

    @commands.command(name="send_telegram_verify_panel")
    @commands.has_any_role("Ranger")
    async def send_telegram_verify_panel(self, ctx: commands.Context):
        channel = self.bot.get_channel(VERIFICATION_CHANNEL_ID)
        if not channel or not isinstance(channel, discord.TextChannel):
            try:
                channel = await self.bot.fetch_channel(VERIFICATION_CHANNEL_ID)
                if not isinstance(channel, discord.TextChannel):
                    logger.error(f"Verification channel {VERIFICATION_CHANNEL_ID} is not a TextChannel.")
                    await ctx.send("⚠️ Verification channel is misconfigured.")
                    return
            except discord.NotFound:
                logger.error(f"Verification channel {VERIFICATION_CHANNEL_ID} not found.")
                await ctx.send(f"⚠️ Verification channel (ID: {VERIFICATION_CHANNEL_ID}) not found.")
                return
            except discord.Forbidden:
                logger.error(f"No permission to access verification channel {VERIFICATION_CHANNEL_ID}.")
                await ctx.send("⚠️ Bot lacks permission to access the verification channel.")
                return

        embed = discord.Embed(
            title="🔒 Telegram Group Verification",
            description=(
                "Click the button below to verify your membership in the specified Telegram group.\n"
                "If you are a member, you will gain temporary access to a private Discord channel.\n\n"
                "**Requirements**:\n"
                "- Your Telegram username must be linked in the Snag Loyalty System.\n"
                "- You must be a member of the specified Telegram group."
            ),
            color=discord.Color.blue(),
            timestamp=datetime.now(tz=timezone.utc)
        )
        embed.set_footer(text="Verification powered by TelegramVerificationCog")

        view = TelegramVerificationView(self)
        try:
            await channel.send(embed=embed, view=view)
            await ctx.send("✅ Verification panel sent successfully.")
            logger.info(f"Telegram verification panel sent by {ctx.author.name} to channel {VERIFICATION_CHANNEL_ID}.")
        except discord.Forbidden:
            logger.error(f"Bot lacks permission to send message in channel {VERIFICATION_CHANNEL_ID}.")
            await ctx.send("⚠️ Bot lacks permission to send messages in the verification channel.")
        except discord.HTTPException as e:
            logger.error(f"Failed to send verification panel to {VERIFICATION_CHANNEL_ID}: {e}", exc_info=True)
            await ctx.send("⚠️ Failed to send verification panel.")

    @send_telegram_verify_panel.error
    async def send_telegram_verify_panel_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingAnyRole):
            await ctx.send("⛔ You lack the 'Ranger' role.")
        else:
            logger.error(f"Error in send_telegram_verify_panel: {error}", exc_info=True)
            await ctx.send("⚙️ An unexpected error occurred.")

async def setup(bot: commands.Bot):
    cog = TelegramVerificationCog(bot)
    await bot.add_cog(cog)
    logger.info("TelegramVerificationCog loaded with persistent view.")