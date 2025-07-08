# cogs/poker_cog.py
import discord
from discord.ext import commands
from discord import app_commands
import logging
import datetime
import asyncio
import io
import re
import csv
import os
from typing import Dict, List, Tuple, Optional
from decimal import Decimal
from utils.snag_api_client import SnagApiClient
from utils.checks import is_admin_in_guild

logger = logging.getLogger(__name__)

# Constants
# Константы, которые не зависят от переменных окружения
EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")
INVITE_CODE_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{10}$")
GET_USER_ENDPOINT = "/api/users"
PARTICIPANTS_LIST_DELETION_DELAY_SECONDS = 3600

MATCHSTICKS_CURRENCY_ID = os.getenv("MATCHSTICKS_CURRENCY_ID", "")
if not MATCHSTICKS_CURRENCY_ID:
    logger.warning("MATCHSTICKS_CURRENCY_ID не установлена. Проверка баланса может не работать.")
    
try:
    POKER_CHANNEL_ID = 1384466812766257193
except (ValueError, TypeError):
    POKER_CHANNEL_ID = 0
    logger.warning("POKER_CHANNEL_ID не задан или имеет неверный формат. Установлено значение 0.")

try:
    MENTION_ROLE_ID = int(os.getenv("POKER_MENTION_ROLE_ID", "0"))
except (ValueError, TypeError):
    MENTION_ROLE_ID = 0
    logger.warning("MENTION_ROLE_ID не задан или имеет неверный формат. Установлено значение 0.")

class PokerLoginModal(discord.ui.Modal, title="Enter PokerNow Login"):
    poker_login = discord.ui.TextInput(
        label="PokerNow Login",
        placeholder="Enter your PokerNow username",
        required=True,
        style=discord.TextStyle.short,
        min_length=3,
        max_length=50
    )

    def __init__(self, cog_instance: "PokerCog", link: str, event_id: int):
        super().__init__(timeout=None)
        self.cog = cog_instance
        self.link = link
        self.event_id = event_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        poker_login = self.poker_login.value.strip()
        await self.cog.process_poker_request(interaction, poker_login, self.link, self.event_id)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in PokerLoginModal: {error}", exc_info=True)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("An error occurred in the modal.", ephemeral=True)
            else:
                await interaction.response.send_message("An error occurred in the modal.", ephemeral=True)
        except discord.HTTPException:
            pass

class PokerButtonView(discord.ui.View):
    def __init__(self, cog_instance: "PokerCog", link: str, expiry_time: datetime.datetime, event_id: int, min_matchsticks: Decimal, invite_codes: List[str]):
        super().__init__(timeout=None)
        self.cog = cog_instance
        self.link = link
        self.expiry_time = expiry_time
        self.event_id = event_id
        self.min_matchsticks = min_matchsticks
        self.invite_codes = invite_codes
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if discord.utils.utcnow() >= self.expiry_time:
            self.join_button.disabled = True
            await interaction.response.send_message("This poker event has expired.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Join Poker Game", style=discord.ButtonStyle.green, custom_id="poker:join")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PokerLoginModal(self.cog, self.link, self.event_id)
        await interaction.response.send_modal(modal)

class PokerCog(commands.Cog, name="Poker"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snag_client: Optional[SnagApiClient] = getattr(bot, 'snag_client', None)
        self.participants: Dict[int, List[Tuple[str, str, str, Optional[str]]]] = {}
        self.event_configs: Dict[int, Decimal] = {}
        self.invite_codes: Dict[int, List[str]] = {}
        self.assigned_codes: Dict[int, Dict[str, str]] = {}
        self._lock = asyncio.Lock()
        if not self.snag_client or not self.snag_client._api_key:
            logger.error(f"{self.__class__.__name__}: Main SnagApiClient missing or no API key. Poker functionality will fail.")

    async def cog_load(self):
        logger.info(f"Cog '{self.__class__.__name__}' successfully initialized and loaded.")

    async def _get_wallet_balance(self, wallet_address: str) -> Decimal:
        if not self.snag_client:
            logger.error("SnagApiClient not available for balance check.")
            return Decimal('0')
        try:
            accounts_data: Optional[dict] = await self.snag_client.get_all_accounts_for_wallet(wallet_address, limit=1000)
            if accounts_data and isinstance(accounts_data.get("data"), list):
                for acc in accounts_data["data"]:
                    if acc.get("loyaltyCurrencyId") == MATCHSTICKS_CURRENCY_ID and acc.get("amount") is not None:
                        return Decimal(str(acc["amount"]))
            return Decimal('0')
        except Exception as e:
            logger.error(f"Error fetching balance for {wallet_address}: {e}", exc_info=True)
            return Decimal('0')

    async def _check_user_eligibility(self, user: discord.User, event_id: int) -> Tuple[bool, str, Optional[str]]:
        if not self.snag_client or not self.snag_client._api_key:
            logger.error(f"Eligibility check failed for '{user.name}' (Event {event_id}): SnagApiClient is not configured.")
            return False, "API client not configured. Please contact an admin.", None

        discord_handle = user.name
        account_data: Optional[dict] = await self.snag_client.get_account_by_social("discordUser", discord_handle)

        # 1. Проверяем на наличие ЛЮБОЙ ошибки от API клиента (500, таймаут, и т.д.)
        if not account_data or account_data.get("error"):
            error_details = account_data.get("message", "No details") if account_data else "No response"
            logger.error(f"Eligibility check failed for '{discord_handle}' (Event {event_id}) due to an API error: {error_details}")
            # Отправляем простое сообщение пользователю
            return False, "Something went wrong, please try again.", None

        # 2. Если ошибки не было, проверяем, что данные о пользователе действительно пришли
        if not isinstance(account_data.get("data"), list) or not account_data["data"]:
            logger.warning(f"Eligibility check failed for '{discord_handle}' (Event {event_id}): Snag API found no linked Discord account (API call was successful but returned no data).")
            # Отправляем инструкцию по привязке
            return False, "Please link your Discord account to the Snag Loyalty System. If already linked, try re-linking.\n https://loyalty.campnetwork.xyz/home?editProfile=1&modalTab=social", None

        user_info = account_data["data"][0].get("user", {})
        wallet_address = user_info.get("walletAddress")
        if not wallet_address or not EVM_ADDRESS_PATTERN.match(wallet_address):
            logger.warning(f"Eligibility check failed for '{discord_handle}' (Event {event_id}): Account found, but no valid EVM wallet is linked.")
            return False, "No valid EVM wallet address (e.g., 0x...) linked to your Discord account in the Snag Loyalty System.", None

        # Проверка на блокировку кошелька
        response = await self.snag_client._make_request(
            "GET",
            GET_USER_ENDPOINT,
            params={"walletAddress": wallet_address}
        )

        if not response or response.get("error"):
            error_message = response.get("message", "API request failed.") if response else "No response from API."
            logger.error(f"Eligibility check failed for '{discord_handle}' (Event {event_id}): API Error during block check: {error_message}")
            return False, "Something went wrong, please create https://discord.com/channels/1161497860915875900/1243221599142805626", None

        if not isinstance(response.get("data"), list) or not response["data"]:
            logger.warning(f"Eligibility check failed for '{discord_handle}' (Event {event_id}): No user data found for wallet {wallet_address} during block check.")
            return False, "Something went wrong, please create https://discord.com/channels/1161497860915875900/1243221599142805626", None

        try:
            user_object = response["data"][0]
            user_metadata_list = user_object.get("userMetadata", [])
            if not isinstance(user_metadata_list, list) or not user_metadata_list:
                logger.warning(f"Eligibility check failed for '{discord_handle}' (Event {event_id}): User found, but metadata is missing for wallet {wallet_address}.")
                return False, "Something went wrong, please create https://discord.com/channels/1161497860915875900/1243221599142805626", None

            metadata = user_metadata_list[0]
            is_blocked = metadata.get("isBlocked", False)

            if is_blocked:
                logger.info(f"Eligibility check failed for '{discord_handle}' (Event {event_id}): Wallet {wallet_address} is blocked.")
                return False, "Something went wrong, please create https://discord.com/channels/1161497860915875900/1243221599142805626", None

        except (IndexError, KeyError, TypeError) as e:
            logger.error(f"Error parsing block check API response for {wallet_address} (Event {event_id}): {e}. Response: {response}", exc_info=True)
            return False, "Something went wrong, please create https://discord.com/channels/1161497860915875900/1243221599142805626", None

        # Проверка баланса Matchsticks
        min_matchsticks = self.event_configs.get(event_id, Decimal('3'))
        balance = await self._get_wallet_balance(wallet_address)
        if balance < min_matchsticks:
            logger.info(f"Eligibility check failed for '{discord_handle}' (Event {event_id}): Insufficient Matchsticks balance. Required: {min_matchsticks}, Has: {balance}, Wallet: {wallet_address}")
            return False, f"Insufficient Matchsticks balance. You need at least {min_matchsticks}, but have {balance}.", wallet_address

        logger.info(f"Eligibility check PASSED for '{discord_handle}' (Event {event_id}). Wallet: {wallet_address}, Balance: {balance}")
        return True, "", wallet_address

    async def process_poker_request(self, interaction: discord.Interaction, poker_login: str, link: str, event_id: int):
        async with self._lock:
            eligible, error_message, wallet_address = await self._check_user_eligibility(interaction.user, event_id)
            discord_handle = interaction.user.name if interaction.user.discriminator == '0' else f"{interaction.user.name}#{interaction.user.discriminator}"

            if not eligible:
                await interaction.followup.send(f"⚠️ {error_message}", ephemeral=True)
                return

            if wallet_address is None:
                logger.error(f"Wallet address is None for eligible user {discord_handle} in event {event_id}.")
                await interaction.followup.send("⚙️ An internal error occurred with your wallet information.", ephemeral=True)
                return

            if event_id not in self.participants:
                self.participants[event_id] = []
            if event_id not in self.assigned_codes:
                self.assigned_codes[event_id] = {}

            existing_entry = next((entry for entry in self.participants[event_id] if entry[1] == discord_handle), None)
            if existing_entry:
                invite_code = self.assigned_codes[event_id].get(discord_handle, "No code assigned")
                await interaction.followup.send(
                    f"✅ You are already registered. Poker game link: {link}\nHere is your invite code: `{invite_code}`",
                    ephemeral=True
                )
                logger.info(f"User {discord_handle} re-requested poker details for event {event_id}. Sent link and invite code: {invite_code}")
                return

            if not self.invite_codes.get(event_id):
                await interaction.followup.send("⚠️ No invite codes available for this event.", ephemeral=True)
                logger.error(f"No invite codes available for event {event_id} for user {discord_handle}.")
                return

            invite_code = self.invite_codes[event_id].pop(0)
            self.assigned_codes[event_id][discord_handle] = invite_code

            self.participants[event_id].append((poker_login, discord_handle, wallet_address, invite_code))
            logger.info(f"User {discord_handle} (Wallet: {wallet_address}) registered for poker event {event_id} with PokerNow login: {poker_login} and invite code: {invite_code}")
            await interaction.followup.send(
                f"✅ Success! You are registered. Poker game link: {link}\nHere is your invite code: `{invite_code}`",
                ephemeral=True
            )

    async def create_poker_event(self, interaction: discord.Interaction, link: str, expiry_time: datetime.datetime, min_matchsticks: Decimal, invite_codes: List[str]):
        channel = self.bot.get_channel(POKER_CHANNEL_ID)
        if not channel or not isinstance(channel, discord.TextChannel):
            try:
                fetched_channel = await self.bot.fetch_channel(POKER_CHANNEL_ID)
                if not isinstance(fetched_channel, discord.TextChannel):
                    logger.error(f"Fetched POKER_CHANNEL_ID {POKER_CHANNEL_ID} is not a TextChannel.")
                    await interaction.followup.send("⚠️ Poker channel configured incorrectly (not a text channel).", ephemeral=True)
                    return
                channel = fetched_channel
            except discord.NotFound:
                logger.error(f"Poker channel with ID {POKER_CHANNEL_ID} not found.")
                await interaction.followup.send(f"⚠️ Poker channel (ID: {POKER_CHANNEL_ID}) not found.", ephemeral=True)
                return
            except discord.HTTPException as e:
                logger.error(f"Failed to fetch poker channel {POKER_CHANNEL_ID}: {e}", exc_info=True)
                await interaction.followup.send("⚠️ Could not access the poker channel.", ephemeral=True)
                return

        role_to_mention: Optional[discord.Role] = None
        message_content_for_ping: Optional[str] = None

        if interaction.guild:
        #    role_to_mention = interaction.guild.get_role(MENTION_ROLE_ID)
        #    if role_to_mention:
        #        message_content_for_ping = f"{role_to_mention.mention}"
        #    else:
        #        logger.warning(f"Role with ID {MENTION_ROLE_ID} not found on server {interaction.guild.id}. No role will be pinged.")
        #else:
        #    logger.warning("Poker event creation called outside of a guild. Cannot ping role.")

            embed = discord.Embed(
            title="🃏 Poker Event 🃏",
            description=(
                f"A new PokerNow game has been set up!\n\n"
                f"**🔗 Link**: [Hidden until you register via the button below]\n"
                f"**⏳ Registration Ends**:\n"
                f"{discord.utils.format_dt(expiry_time, style='F')} ({discord.utils.format_dt(expiry_time, style='R')})\n\n"
                f"**📋 Requirements to Join**:\n"
                f"1. Linked Discord account in the Snag Loyalty System.\n"
                f"2. Minimum **{min_matchsticks} Matchsticks** balance.\n"
            ),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="Click 'Join Poker Game' to register. Eligibility will be checked.")
        if self.bot.user:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        event_id = interaction.id
        self.event_configs[event_id] = min_matchsticks
        self.invite_codes[event_id] = invite_codes
        view = PokerButtonView(self, link, expiry_time, event_id, min_matchsticks, invite_codes)
        
        try:
            message = await channel.send(content=message_content_for_ping, embed=embed, view=view)
            view.message = message
            logger.info(f"Poker event {event_id} created by {interaction.user.name} in channel #{channel.name} ({POKER_CHANNEL_ID}) until {expiry_time}. Min Matchsticks: {min_matchsticks}. Invite codes loaded: {len(invite_codes)}")
            self.bot.loop.create_task(self._schedule_button_removal_and_summary(message, expiry_time, interaction))
        except discord.Forbidden:
            logger.error(f"Bot lacks permissions to send message in POKER_CHANNEL_ID {POKER_CHANNEL_ID}.")
            await interaction.followup.send("⚠️ Bot lacks permission to send messages in the poker channel.", ephemeral=True)
        except discord.HTTPException as e:
            logger.error(f"Failed to send poker announcement to {POKER_CHANNEL_ID}: {e}", exc_info=True)
            await interaction.followup.send("⚠️ Failed to send poker event announcement.", ephemeral=True)

    async def _schedule_button_removal_and_summary(self, message: discord.Message, expiry_time: datetime.datetime, original_command_interaction: discord.Interaction):
        now = discord.utils.utcnow()
        wait_time = (expiry_time - now).total_seconds()
        
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        
        try:
            await message.delete()
            logger.info(f"Poker event message {message.id} deleted successfully at expiry time {expiry_time}.")
        except discord.NotFound:
            logger.warning(f"Poker event message {message.id} not found for deletion (already deleted?).")
        except discord.HTTPException as e:
            logger.error(f"Failed to delete poker event message {message.id}: {e}", exc_info=True)

        await asyncio.sleep(60)
        logger.info(f"Poker event {original_command_interaction.id} report preparation started after 60s delay post-expiry.")

        try:
            await self._send_participants_table(original_command_interaction)
        except Exception as e:
            logger.error(f"Failed to send participants table for event {original_command_interaction.id} after event message deletion: {e}", exc_info=True)
            if original_command_interaction.channel:
                try:
                    await original_command_interaction.channel.send(f"⚠️ Critical error sending participants table for event {original_command_interaction.id}. Please check logs.")
                except Exception as ie:
                    logger.error(f"Failed to send critical error notification for event {original_command_interaction.id}: {ie}")

    async def _send_participants_table(self, interaction: discord.Interaction):
        event_id = interaction.id
        current_participants = self.participants.get(event_id, []).copy()
        target_channel = interaction.channel
        message_to_delete_later: Optional[discord.Message] = None

        try:
            if not target_channel:
                logger.error(f"Cannot send participants table for event {event_id}: original command interaction channel is None.")
                return

            if not current_participants:
                try:
                    await target_channel.send(f"📋 No users registered for the poker event (ID: {event_id}).")
                    logger.info(f"No participants found for event {event_id}. 'No users' message sent to channel {target_channel.id}.")
                except discord.HTTPException as e:
                    logger.error(f"Failed to send 'no participants' message for event {event_id} to channel {target_channel.id}: {e}", exc_info=True)
                return

            table_lines = [
                f"Poker Event Participants - Event ID: {event_id}",
                "-----------------------------------------------------------------------------------------------------",
                f"{'PokerNow Login':<20} | {'Discord Handle':<30} | {'Wallet Address':<42} | {'Invite Code':<15}",
                "-----------------------------------------------------------------------------------------------------"
            ]
            for poker_login, discord_handle, wallet, invite_code in current_participants:
                invite_code_display = invite_code or "Not Assigned"
                table_lines.append(f"{poker_login:<20} | {discord_handle:<30} | {wallet:<42} | {invite_code_display:<15}")
            table_content = "\n".join(table_lines)

            file_bytes = table_content.encode('utf-8')
            data_stream = io.BytesIO(file_bytes)
            timestamp_str = discord.utils.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"poker_participants_{event_id}_{timestamp_str}.txt"
            discord_file = discord.File(fp=data_stream, filename=filename)

            embed = discord.Embed(
                title=f"🏆 Poker Event Participants Summary (ID: {event_id})",
                description=(
                    f"The poker event has concluded. Below is the list of users who successfully registered.\n"
                    f"A full list is also attached as a `.txt` file."
                ),
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="Total Participants", value=str(len(current_participants)), inline=False)
            if 0 < len(current_participants) <= 10:
                player_list_str = "\n".join([f"{i+1}. {pl} ({dh})" for i, (pl, dh, _, _) in enumerate(current_participants)])
                embed.add_field(name="Registered Players (Preview)", value=f"```{player_list_str}```", inline=False)

            embed.set_footer(text=f"Report generated for event by {interaction.user.display_name}")

            try:
                message_to_delete_later = await target_channel.send(embed=embed, file=discord_file)
                logger.info(f"Participants table for event {event_id} sent to channel {target_channel.id}. Message ID: {message_to_delete_later.id}")
            except discord.Forbidden:
                logger.error(f"Bot lacks permission to send participant table for event {event_id} to channel {target_channel.id}.")
            except discord.HTTPException as e:
                logger.error(f"Failed to send participants table for event {event_id} to channel {target_channel.id}: {e}", exc_info=True)
        
        finally:
            if message_to_delete_later:
                self.bot.loop.create_task(
                    self._schedule_timed_message_deletion(
                        message_to_delete_later, 
                        PARTICIPANTS_LIST_DELETION_DELAY_SECONDS, 
                        event_id
                    )
                )

            async with self._lock:
                if event_id in self.participants:
                    del self.participants[event_id]
                    logger.info(f"Cleared participants data for event {event_id}.")
                if event_id in self.event_configs:
                    del self.event_configs[event_id]
                    logger.info(f"Cleared event config for event {event_id}.")
                if event_id in self.invite_codes:
                    del self.invite_codes[event_id]
                    logger.info(f"Cleared invite codes for event {event_id}.")
                if event_id in self.assigned_codes:
                    del self.assigned_codes[event_id]
                    logger.info(f"Cleared assigned codes for event {event_id}.")

    async def _schedule_timed_message_deletion(self, message: discord.Message, delay_seconds: int, event_id_for_log: int):
        await asyncio.sleep(delay_seconds)
        try:
            await message.delete()
            logger.info(f"Successfully auto-deleted participants list message {message.id} for event {event_id_for_log} from channel {message.channel.id} after {delay_seconds}s.")
        except discord.NotFound:
            logger.warning(f"Participants list message {message.id} for event {event_id_for_log} in channel {message.channel.id} was not found for deletion (already deleted?).")
        except discord.Forbidden:
            logger.error(f"Bot lacks permission to delete participants list message {message.id} for event {event_id_for_log} in channel {message.channel.id}.")
        except discord.HTTPException as e:
            logger.error(f"Failed to auto-delete participants list message {message.id} for event {event_id_for_log} in channel {message.channel.id}: {e}", exc_info=True)

    @app_commands.command(name="poker", description="Create a poker event announcement with a join button.")
    @app_commands.checks.has_any_role("Ranger")
    @app_commands.describe(
        link="PokerNow game link (e.g., https://poker.now/...)",
        end_time="End time for registration (YYYY-MM-DD HH:MM UTC)",
        min_matchsticks="Minimum Matchsticks required to join",
        csv_file="CSV file containing invite codes"
    )
    @is_admin_in_guild()
    async def poker_slash_command(
        self,
        interaction: discord.Interaction,
        link: str,
        end_time: str,
        min_matchsticks: float,
        csv_file: discord.Attachment
    ):
        
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        if not self.snag_client or not self.snag_client._api_key:
            # FIX: Must use followup.send after a defer.
            await interaction.followup.send("⚠️ Snag API client is not configured. Please contact an administrator.", ephemeral=True)
            return

        # FIX: Removed the redundant defer call that caused the InteractionResponded error.
        # await interaction.response.defer(thinking=True, ephemeral=True)

        if not re.match(r"^https?://[^\s/$.?#].*\S$", link):
            await interaction.followup.send("⚠️ Invalid link format. Please provide a valid URL.", ephemeral=True)
            return

        try:
            expiry_time = datetime.datetime.strptime(end_time, "%Y-%m-%d %H:%M").replace(tzinfo=datetime.timezone.utc)
            if expiry_time <= discord.utils.utcnow():
                await interaction.followup.send("⚠️ Specified time is in the past.", ephemeral=True)
                return
        except ValueError:
            await interaction.followup.send("⚠️ Invalid time format. Use YYYY-MM-DD HH:MM (UTC).", ephemeral=True)
            return

        try:
            min_matchsticks_val = Decimal(str(min_matchsticks))
            if min_matchsticks_val <= 0:
                await interaction.followup.send("⚠️ Minimum Matchsticks must be greater than 0.", ephemeral=True)
                return
        except (ValueError, TypeError):
            await interaction.followup.send("⚠️ Invalid Matchsticks value. Please enter a number.", ephemeral=True)
            return

        if not csv_file.filename.endswith('.csv'):
            await interaction.followup.send("⚠️ File must be a .csv file.", ephemeral=True)
            return

        try:
            file_content = await csv_file.read()
            csv_reader = csv.reader(file_content.decode('utf-8').splitlines())
            invite_codes = [row[0].strip() for row in csv_reader if row and INVITE_CODE_PATTERN.match(row[0].strip())]
            if not invite_codes:
                await interaction.followup.send("⚠️ No valid invite codes found in the CSV file.", ephemeral=True)
                return
        except Exception as e:
            logger.error(f"Failed to process CSV file: {e}", exc_info=True)
            await interaction.followup.send("⚠️ Failed to read the CSV file.", ephemeral=True)
            return

        try:
            await self.create_poker_event(interaction, link, expiry_time, min_matchsticks_val, invite_codes)
            await interaction.followup.send("✅ Poker event created successfully.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error creating poker event: {e}", exc_info=True)
            await interaction.followup.send("⚙️ An unexpected error occurred while setting up the event.", ephemeral=True)

    @poker_slash_command.error
    async def poker_slash_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        # FIX: The entire error handler has been rewritten for correctness.
        # Since the main command always defers, we must always use followup.send().
        
        msg_to_send = "⚙️ An unexpected error occurred while processing the poker command."
        
        if isinstance(error, app_commands.MissingAnyRole):
            msg_to_send = "⛔ You do not have the required 'Ranger' role to use this command."
        elif isinstance(error, app_commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
            msg_to_send = "⚠️ I don't have permissions to perform this action here. Please check my channel permissions."
        else:
            logger.error(f"Error in /poker command invocation by {interaction.user.name}: {error}", exc_info=True)
            
        try:
            # We use followup.send() because the interaction has already been deferred.
            # This handles all error cases correctly with a single response.
            await interaction.followup.send(msg_to_send, ephemeral=True)
        except discord.HTTPException as e:
            # This will catch errors if we fail to send the followup message itself,
            # for example, if the interaction token has expired.
            logger.error(f"Failed to send error response for /poker command by {interaction.user.name}: {e}")

async def setup(bot: commands.Bot):
    snag_api_client = getattr(bot, 'snag_client', None)
    if not snag_api_client or not getattr(snag_api_client, '_api_key', None):
        logger.critical("CRITICAL: Main Snag API client or its API key is missing. PokerCog will NOT be loaded.")
        return
    
    cog = PokerCog(bot)
    await bot.add_cog(cog)