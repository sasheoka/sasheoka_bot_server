# cogs/telegram_verification_cog.py
import discord
from discord.ext import commands
from discord import app_commands
import logging
import asyncio
import aiohttp
from typing import Optional
from datetime import datetime, timedelta, timezone
import os
from dotenv import load_dotenv

from utils.snag_api_client import SnagApiClient
from utils.checks import is_prefix_admin_in_guild

logger = logging.getLogger(__name__)

# --- Загрузка настроек и ID из .env ---
try:
    TEMP_ACCESS_DURATION_MINUTES = int(os.getenv("TG_ACCESS_DURATION_MINUTES", 30))
    VERIFICATION_CHANNEL_ID = int(os.getenv("TG_VERIFICATION_CHANNEL_ID", 0))
    TARGET_CHANNEL_ID = int(os.getenv("TG_TARGET_CHANNEL_ID", 0))
    TELEGRAM_GROUP_ID = int(os.getenv("TELEGRAM_GROUP_ID", 0))
except (ValueError, TypeError):
    TEMP_ACCESS_DURATION_MINUTES = 30
    VERIFICATION_CHANNEL_ID = 0
    TARGET_CHANNEL_ID = 0
    TELEGRAM_GROUP_ID = 0

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
    """Cog to verify Telegram group membership using userId and grant temporary Discord channel access."""
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

    # --- ИЗМЕНЕННЫЙ МЕТОД ---
    async def verify_telegram_membership(self, interaction: discord.Interaction):
        if not self.telegram_token or not self.session:
            await interaction.followup.send("⚠️ Telegram verification is not properly configured.", ephemeral=True)
            return

        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.followup.send("⚠️ This command can only be used in a server.", ephemeral=True)
            return

        user = interaction.user
        discord_handle = user.name if user.discriminator == '0' else f"{user.name}#{user.discriminator}"

        snag_client: Optional[SnagApiClient] = getattr(self.bot, 'snag_client', None)
        if not snag_client or not snag_client._api_key:
            await interaction.followup.send("⚠️ Snag API client is not configured.", ephemeral=True)
            return

        # --- ШАГ 1: Идентифицируем пользователя через get_user_data ---
        user_data_response = await snag_client.get_user_data(discord_user=discord_handle)
        
        if not user_data_response or user_data_response.get("error"):
            await interaction.followup.send(
                "⚠️ Your Discord account is not linked in the Snag Loyalty System or an API error occurred. "
                "Please link it at https://loyalty.campnetwork.xyz/home?editProfile=1&modalTab=social",
                ephemeral=True
            )
            return

        if not isinstance(user_data_response.get("data"), list) or not user_data_response["data"]:
            await interaction.followup.send(
                "⚠️ Your Discord account is not linked in the Snag Loyalty System. "
                "Please link it at https://loyalty.campnetwork.xyz/home?editProfile=1&modalTab=social",
                ephemeral=True
            )
            return

        # --- ШАГ 2: Извлекаем telegramUserId из полученных данных ---
        user_object = user_data_response["data"][0]
        telegram_user_id = None
        user_metadata_list = user_object.get("userMetadata")
        if user_metadata_list and isinstance(user_metadata_list, list):
            telegram_user_id = user_metadata_list[0].get("telegramUserId")

        if not telegram_user_id:
            await interaction.followup.send(
                "⚠️ Your Telegram user ID is not linked in the Snag Loyalty System. "
                "Please link it at https://loyalty.campnetwork.xyz/home?editProfile=1&modalTab=social",
                ephemeral=True
            )
            return

        # --- ШАГ 3: Проверяем членство в группе Telegram (эта часть остается без изменений) ---
        try:
            async with self.session.get(
                f"{self.telegram_api_url}/getChatMember",
                params={"chat_id": TELEGRAM_GROUP_ID, "user_id": telegram_user_id}
            ) as response:
                result = await response.json()
                logger.warning(f"Telegram API response for telegramUserId {telegram_user_id}: {result}")

                if not result.get("ok"):
                    await interaction.followup.send(
                        "⚠️ Failed to verify Telegram membership. Please ensure your Telegram account is linked correctly.",
                        ephemeral=True
                    )
                    return

                status = result["result"].get("status")
                if status in ["administrator", "creator", "member"]:
                    await self.grant_channel_access(interaction)
                else:
                    is_member = result["result"].get("is_member", False)
                    if is_member:
                        await self.grant_channel_access(interaction)
                    else:
                        await interaction.followup.send(
                            f"⚠️ You are not a member of the Campfire Circle.",
                            ephemeral=True
                        )
        except aiohttp.ClientError as e:
            logger.error(f"Telegram API request failed for telegramUserId {telegram_user_id}: {e}", exc_info=True)
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

        overwrite = target_channel.overwrites_for(user)
        if overwrite.view_channel is True:
            await interaction.followup.send("⚠️ You already have access to the target channel.", ephemeral=True)
            logger.info(f"User {user.name} already has access to channel {TARGET_CHANNEL_ID}.")
            return

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
    @is_prefix_admin_in_guild()
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
                "Click the button below to verify your membership in Campfire Circle.\n"
                "If you are a member, you will gain temporary access to a private Discord channel.\n\n"
                "**Requirements**:\n"
                "- Your Telegram account must be linked in the Snag Loyalty System.\n"
                "- You must be a member of the Campfire Cirle."
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