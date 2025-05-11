# cogs/temp_collector_cog.py
import discord
from discord.ext import commands
import logging
import io
import asyncio
import datetime # For date parsing
from typing import Optional, Set, Dict, Union

# Import your SnagApiClient
from utils.snag_api_client import SnagApiClient

logger = logging.getLogger(__name__)

MessagableChannel = Union[discord.TextChannel, discord.Thread, discord.VoiceChannel]

class TempCollectorCog(commands.Cog, name="Temporary Collector"):
    """
    Temporary cog to collect Discord handles from messages in a specified chat entity (by ID),
    fetch their wallets via Snag API, and provide a downloadable file.
    Allows filtering by message limit and for a specific date (inclusive).
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snag_client: Optional[SnagApiClient] = getattr(bot, 'snag_client', None)
        if not self.snag_client:
            logger.error(f"{self.__class__.__name__}: SnagApiClient not found in bot instance!")
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    @commands.command(name="collectwalletsfromchat")
    @commands.is_owner()
    async def collect_wallets_from_chat(self, ctx: commands.Context,
                                        chat_id: int,
                                        message_limit_str: Optional[str] = None,
                                        target_date_str: Optional[str] = None): # Renamed for clarity
        """
        Collects Discord handles from messages in a specified chat entity (by ID),
        optionally for a specific date (YYYY-MM-DD, inclusive) and with a message limit.
        If message_limit is "None"/"0"/not given, ALL messages (matching date filter if any) will be analyzed.

        Usage: !collectwalletsfromchat <ID> [limit_or_None_or_0] [YYYY-MM-DD_target_date]
        Examples:
        !collectwalletsfromchat 123...                      (all messages, all time)
        !collectwalletsfromchat 123... 500                   (last 500 messages, all time)
        !collectwalletsfromchat 123... None 2023-01-15       (all messages from 2023-01-15)
        !collectwalletsfromchat 123... 0 2023-01-15          (all messages from 2023-01-15)
        !collectwalletsfromchat 123... 500 2023-01-15        (up to 500 messages from 2023-01-15)
        """
        if not self.snag_client:
            await ctx.send("Snag API client is not configured. Cannot proceed.")
            return

        if not self.snag_client._api_key or not self.snag_client._organization_id or not self.snag_client._website_id:
            await ctx.send("Snag API client is missing required keys/IDs. Check your .env configuration.")
            logger.warning("collectwalletsfromchat: SnagApiClient is missing API key or Org/Website ID.")
            return

        message_limit: Optional[int] = None
        if message_limit_str and message_limit_str.lower() != 'none':
            try:
                limit_val = int(message_limit_str)
                if limit_val > 0:
                    message_limit = limit_val
            except ValueError:
                await ctx.send(f"Invalid format for `message_limit`. It should be a number or 'None'. You provided: '{message_limit_str}'")
                return

        # Date parsing for target_date_str
        after_filter: Optional[datetime.datetime] = None
        before_filter: Optional[datetime.datetime] = None
        date_filter_display_text = ""

        if target_date_str:
            try:
                target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d")
                # To include the entire target_date:
                # 'after' should be the end of the previous day (or very start of target_date, but API is exclusive for after)
                # 'before' should be the start of the day after target_date
                # For simplicity with naive datetimes for discord.py:
                after_filter = target_date - datetime.timedelta(microseconds=1) # Effectively start of target_date
                # Actually, to be safer for `after` being exclusive:
                # after_filter = datetime.datetime.combine(target_date.date() - datetime.timedelta(days=1), datetime.time.max)
                # This is simpler and usually works as expected with `after` being exclusive:
                after_filter = datetime.datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0) - datetime.timedelta(seconds=1)


                before_filter = target_date + datetime.timedelta(days=1)
                before_filter = datetime.datetime(before_filter.year, before_filter.month, before_filter.day, 0, 0, 0)

                date_filter_display_text = f" for {target_date_str}"
            except ValueError:
                await ctx.send(f"Invalid date format for `target_date_str`. Please use `YYYY-MM-DD`. You provided: '{target_date_str}'")
                return

        chat_entity: Optional[MessagableChannel] = None
        entity_type_str = "chat entity"

        try:
            fetched_entity = await self.bot.fetch_channel(chat_id)
            if isinstance(fetched_entity, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
                chat_entity = fetched_entity
                if isinstance(fetched_entity, discord.TextChannel): entity_type_str = "text channel"
                elif isinstance(fetched_entity, discord.Thread): entity_type_str = "thread/post"
                elif isinstance(fetched_entity, discord.VoiceChannel): entity_type_str = "voice channel's text chat"
            else:
                await ctx.send(f"ID `{chat_id}` does not belong to a known chat entity. Please ensure the ID is correct.")
                return
        except discord.NotFound: await ctx.send(f"Chat entity with ID `{chat_id}` not found."); return
        except discord.Forbidden: await ctx.send(f"I don't have permission to access chat entity with ID `{chat_id}`."); return
        except Exception as e:
            logger.error(f"Error fetching chat entity {chat_id}: {e}", exc_info=True)
            await ctx.send(f"An error occurred while trying to fetch info for chat entity with ID `{chat_id}`."); return
        
        if not chat_entity: await ctx.send(f"Failed to correctly identify chat entity with ID `{chat_id}`."); return

        parent_channel_info = ""
        if isinstance(chat_entity, discord.Thread) and chat_entity.parent:
            parent_channel_info = f" (in channel: #{chat_entity.parent.name})"
        elif isinstance(chat_entity, discord.VoiceChannel) and chat_entity.category:
             parent_channel_info = f" (in category: {chat_entity.category.name})"

        limit_text = f"up to {message_limit} messages" if message_limit else "ALL messages"
        
        initial_message = (
            f"Starting data collection from {entity_type_str} **{chat_entity.name}** ({chat_entity.mention}){parent_channel_info}.\n"
            f"Will analyze: **{limit_text}**{date_filter_display_text}.\n"
        )
        if not message_limit and not target_date_str : # Warn if fetching ALL history without any filter
            initial_message += "**ATTENTION:** Analyzing all messages without filters can take a very long time!"
        
        await ctx.send(initial_message)

        processed_handles: Set[str] = set()
        wallet_data: Dict[str, str] = {}
        messages_scanned = 0
        api_calls_made = 0
        users_found_in_chat = 0
        wallets_found_via_api = 0
        errors_fetching_wallet = 0

        try:
            # Pass both after_filter and before_filter to history
            async for message in chat_entity.history(limit=message_limit, after=after_filter, before=before_filter, oldest_first=False):
                messages_scanned += 1
                if message.author.bot: continue

                if message.author.discriminator == '0': discord_handle = message.author.name
                else: discord_handle = f"{message.author.name}#{message.author.discriminator}"

                if discord_handle in processed_handles: continue
                processed_handles.add(discord_handle)
                users_found_in_chat +=1
                
                logger.info(f"Processing user from {entity_type_str} '{chat_entity.name}' (ID: {chat_entity.id}): {discord_handle}")

                try:
                    api_calls_made += 1
                    response = await self.snag_client.get_account_by_social(handle_type="discordUser", handle_value=discord_handle)
                    wallet_address = "Not found in API"
                    if response and isinstance(response.get("data"), list) and response["data"]:
                        account_data = response["data"][0]; user_info = account_data.get("user")
                        found_wallet = user_info.get("walletAddress") if isinstance(user_info, dict) else None
                        if found_wallet: wallet_address = found_wallet; wallets_found_via_api += 1; logger.info(f"Wallet found for {discord_handle}: {wallet_address}")
                        else: logger.warning(f"User data for {discord_handle} OK, but no walletAddress. API Resp: {response}")
                    else: logger.info(f"Account not found via API for {discord_handle}. API Resp: {response}")
                    wallet_data[discord_handle] = wallet_address
                    await asyncio.sleep(0.8)
                except Exception as e:
                    logger.error(f"Error fetching wallet for {discord_handle} via API: {e}", exc_info=True)
                    wallet_data[discord_handle] = "Error fetching (API)"; errors_fetching_wallet +=1
                
                if users_found_in_chat > 0 and users_found_in_chat % 50 == 0 :
                    await ctx.send(f"Scanned {messages_scanned} messages. Processed {users_found_in_chat} unique users...", delete_after=20)

            if not wallet_data:
                await ctx.send(f"No unique non-bot users found to process in {entity_type_str} **{chat_entity.name}** (within {limit_text}{date_filter_display_text}).")
                return

            file_header_lines = [f"Data collected from {entity_type_str}: #{chat_entity.name} (ID: {chat_entity.id})"]
            if isinstance(chat_entity, discord.Thread) and chat_entity.parent:
                 file_header_lines.append(f"Parent channel: #{chat_entity.parent.name} (ID: {chat_entity.parent_id})")
            elif isinstance(chat_entity, discord.VoiceChannel) and chat_entity.category:
                 file_header_lines.append(f"Parent category: {chat_entity.category.name} (ID: {chat_entity.category_id})")
            
            file_content_lines = file_header_lines + [
                f"Messages analyzed: {limit_text}{date_filter_display_text}",
                f"Total messages scanned: {messages_scanned}",
                f"Unique non-bot users found: {users_found_in_chat}",
                f"Wallets found via API: {wallets_found_via_api}",
                f"Errors during API requests: {errors_fetching_wallet}",
                "-" * 40
            ]
            for handle, wallet in wallet_data.items(): file_content_lines.append(f"{handle}: {wallet}")

            file_content = "\n".join(file_content_lines); file_bytes = file_content.encode('utf-8')
            data_stream = io.BytesIO(file_bytes)
            safe_chat_name = "".join(c if c.isalnum() or c in (' ', '_', '-') else '_' for c in chat_entity.name).rstrip()
            discord_file = discord.File(fp=data_stream, filename=f"wallets_{safe_chat_name}_{chat_entity.id}_collected.txt")

            summary_message = (
                f"Collection complete for {entity_type_str} **{chat_entity.name}**!\n"
                f"Processed {users_found_in_chat} unique users from {messages_scanned} scanned messages ({limit_text}{date_filter_display_text}).\n"
                f"Wallets found via API: {wallets_found_via_api}.\n"
                f"Results file attached."
            )
            await ctx.send(summary_message, file=discord_file)

        except discord.Forbidden: await ctx.send(f"I don't have permission to read message history in {entity_type_str} **{chat_entity.name}**."); logger.warning(f"Access denied for {entity_type_str} {chat_entity.id}")
        except discord.HTTPException as e:
            if isinstance(chat_entity, discord.VoiceChannel):
                 await ctx.send(f"Could not fetch message history for voice channel **{chat_entity.name if chat_entity else 'Unknown'}**. It might not have text-in-voice enabled or accessible.")
                 logger.error(f"HTTPException trying to get history for VoiceChannel {chat_entity.id if chat_entity else 'Unknown'}: {e.status} {e.text}")
            else:
                await ctx.send("An HTTP error occurred. Check logs."); logger.error(f"HTTP Error in cmd: {e}", exc_info=True)
        except Exception as e: await ctx.send("An unexpected error occurred. Check logs."); logger.error(f"Error in cmd: {e}", exc_info=True)

    @collect_wallets_from_chat.error
    async def collect_wallets_from_chat_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.NotOwner): await ctx.send("You are not the bot owner.")
        elif isinstance(error, commands.MissingRequiredArgument):
            param_name = error.param.name if hasattr(error.param, 'name') else "arg"
            await ctx.send(f"Missing argument: `{param_name}`. Usage: `!collectwalletsfromchat <ID> [limit_or_None] [YYYY-MM-DD_date]`")
        elif isinstance(error, commands.BadArgument):
            await ctx.send(f"Invalid argument type. Ensure ID is a number. Error: {error}")
        else: logger.error(f"Unhandled error in cmd: {error}", exc_info=True); await ctx.send("An unexpected error occurred.")

async def setup(bot: commands.Bot):
    if hasattr(bot, 'snag_client') and bot.snag_client is not None: await bot.add_cog(TempCollectorCog(bot))
    else: logger.error("Failed to load TempCollectorCog: snag_client missing.")