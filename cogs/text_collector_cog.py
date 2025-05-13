# cogs/text_collector_cog.py
import discord
from discord.ext import commands
import logging
import datetime
import os
import asyncio
import io
from typing import Dict, Set, Optional, Tuple, List, Union

logger = logging.getLogger(__name__)

# --- Modal Window for Input Parameters ---
class CollectParamsModal(discord.ui.Modal, title="Collect Addresses from Chat"): # MODIFIED
    channel_id_input = discord.ui.TextInput(
        label="Channel ID (Text/Voice)", # MODIFIED
        placeholder="Enter channel ID (numeric)", # MODIFIED
        required=True,
        style=discord.TextStyle.short,
        min_length=17,
        max_length=20
    )
    date_input = discord.ui.TextInput(
        label="Date for Collection (YYYY-MM-DD)", # MODIFIED
        placeholder="Example: 2023-10-27", # MODIFIED
        required=True,
        style=discord.TextStyle.short,
        min_length=10,
        max_length=10
    )
    message_limit_input = discord.ui.TextInput(
        label="Message Limit (0 or empty = all)", # MODIFIED
        placeholder="Example: 500 (default: all for the date)", # MODIFIED
        required=False,
        style=discord.TextStyle.short,
        max_length=7
    )

    def __init__(self, cog_instance: "TextCollectorCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        channel_id_str = self.channel_id_input.value
        date_str = self.date_input.value
        limit_str = self.message_limit_input.value
        
        await self.cog.process_text_collection_request(
            interaction,
            channel_id_str,
            date_str,
            limit_str
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in TextCollector modal: {error}", exc_info=True) # Internal log, no need to translate
        try:
            if interaction.response.is_done():
                await interaction.followup.send("An error occurred in the modal. Please try again.", ephemeral=True) # MODIFIED
            else:
                await interaction.response.send_message("An error occurred in the modal. Please try again.", ephemeral=True) # MODIFIED
        except discord.HTTPException:
            pass


# --- View Class for Text Collector Panel ---
class TextCollectorPanelView(discord.ui.View):
    def __init__(self, cog_instance: "TextCollectorCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def _check_ranger_role(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
             await interaction.response.send_message("This command can only be used on a server.", ephemeral=True); return False # MODIFIED
        ranger_role = discord.utils.get(interaction.guild.roles, name="Ranger")
        if not ranger_role:
            await interaction.response.send_message("⛔ The 'Ranger' role was not found on this server.", ephemeral=True); return False # MODIFIED
        if ranger_role not in interaction.user.roles:
            await interaction.response.send_message("⛔ You do not have the required 'Ranger' role.", ephemeral=True); return False # MODIFIED
        return True

    @discord.ui.button(label="📝 Collect Addresses from Chat", style=discord.ButtonStyle.primary, custom_id="textcollect:open_modal_v1") # MODIFIED
    async def collect_from_text_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction):
            return
        
        modal = CollectParamsModal(self.cog)
        await interaction.response.send_modal(modal)


# --- Cog Class ---
class TextCollectorCog(commands.Cog, name="Text Chat Collector"):
    """
    Collects Discord handles from the specified text or voice chat for a given date,
    queries their wallets via Snag API (main and legacy), and generates a TXT file.
    """ # MODIFIED
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snag_client = getattr(bot, 'snag_client', None)
        self.snag_client_legacy = getattr(bot, 'snag_client_legacy', None)
        
        if not self.snag_client:
            logger.error(f"{self.__class__.__name__}: Main SnagApiClient (bot.snag_client) not found!")
        if not self.snag_client_legacy:
            logger.warning(f"{self.__class__.__name__}: Legacy SnagApiClient (bot.snag_client_legacy) not found! Address comparison will be incomplete.")
        
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    async def cog_load(self):
        logger.info(f"Cog '{self.__class__.__name__}' successfully initialized by bot.")

    async def cog_unload(self):
        logger.info(f"Cog '{self.__class__.__name__}' unloaded.")

    async def _fetch_wallet_from_api(self, client, handle: str) -> Optional[str]:
        if not client or not client._api_key:
            client_name = getattr(client, '_client_name', 'Unknown Snag client')
            logger.warning(f"Client {client_name} has no API key. Skipping request for {handle}.")
            return None
            
        response = await client.get_account_by_social(handle_type="discordUser", handle_value=handle)
        if response and isinstance(response.get("data"), list) and response["data"]:
            account_data = response["data"][0]
            user_info = account_data.get("user")
            if isinstance(user_info, dict):
                wallet_address = user_info.get("walletAddress")
                if wallet_address:
                    return str(wallet_address).lower()
        return None

    async def process_text_collection_request(self, interaction: discord.Interaction, channel_id_str: str, date_str: str, limit_str: Optional[str]):
        target_channel: Optional[Union[discord.TextChannel, discord.VoiceChannel]] = None
        channel_type_str = "channel" 

        try:
            target_channel_id = int(channel_id_str)
            fetched_channel = self.bot.get_channel(target_channel_id)
            if not fetched_channel:
                fetched_channel = await self.bot.fetch_channel(target_channel_id)

            if isinstance(fetched_channel, discord.TextChannel):
                target_channel = fetched_channel
                channel_type_str = "text channel" # MODIFIED
            elif isinstance(fetched_channel, discord.VoiceChannel):
                target_channel = fetched_channel
                channel_type_str = "voice channel's text chat" # MODIFIED
            else:
                await interaction.followup.send(
                    f"⚠️ ID `{target_channel_id}` does not belong to a text or voice channel from which history can be read.", # MODIFIED
                    ephemeral=True
                )
                return
        except ValueError:
            await interaction.followup.send("⚠️ Channel ID must be a number.", ephemeral=True) # MODIFIED
            return
        except discord.NotFound:
            await interaction.followup.send(f"⚠️ Channel with ID `{channel_id_str}` not found.", ephemeral=True) # MODIFIED
            return
        except discord.Forbidden:
            await interaction.followup.send(f"⛔ I don't have permission to access channel ID `{channel_id_str}`.", ephemeral=True) # MODIFIED
            return
        except Exception as e:
            logger.error(f"Error fetching channel {channel_id_str}: {e}", exc_info=True)
            await interaction.followup.send(f"⚠️ An error occurred while accessing the channel.", ephemeral=True) # MODIFIED
            return

        try:
            target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
            after_dt = target_date
            before_dt = target_date + datetime.timedelta(days=1) - datetime.timedelta(microseconds=1)
        except ValueError:
            await interaction.followup.send("⚠️ Invalid date format. Please use YYYY-MM-DD.", ephemeral=True) # MODIFIED
            return

        message_limit: Optional[int] = None
        if limit_str and limit_str.strip():
            try:
                limit_val = int(limit_str)
                if limit_val > 0: message_limit = limit_val
            except ValueError:
                await interaction.followup.send("⚠️ Message limit must be a number.", ephemeral=True) # MODIFIED
                return
        
        await interaction.followup.send(f"⏳ Starting data collection from {channel_type_str} `{target_channel.name}` for `{date_str}`...", ephemeral=True) # MODIFIED
        
        unique_user_ids: Set[int] = set()
        messages_scanned = 0
        try:
            async for message in target_channel.history(limit=message_limit, after=after_dt, before=before_dt, oldest_first=False):
                messages_scanned += 1
                if not message.author.bot:
                    unique_user_ids.add(message.author.id)
                if messages_scanned % 200 == 0:
                     await interaction.edit_original_response(content=f"⏳ Scanning... processed {messages_scanned} messages from `{target_channel.name}`...") # MODIFIED

        except discord.Forbidden:
            await interaction.edit_original_response(content=f"⛔ I don't have permission to read history in {channel_type_str} `{target_channel.name}`.") # MODIFIED
            return
        except discord.HTTPException as e_http:
            if isinstance(target_channel, discord.VoiceChannel):
                 logger.error(f"HTTP error reading history from VoiceChannel {target_channel.id}: {e_http.status} - {e_http.text}", exc_info=True)
                 await interaction.edit_original_response(content=f"⚠️ Could not read history from voice channel `{target_channel.name}`. Its text chat might be disabled or inaccessible.") # MODIFIED
            else:
                logger.error(f"HTTP error reading channel history {target_channel.id}: {e_http.status} - {e_http.text}", exc_info=True)
                await interaction.edit_original_response(content=f"⚠️ An HTTP error occurred while reading the {channel_type_str} history.") # MODIFIED
            return
        except Exception as e:
            logger.error(f"Error reading {channel_type_str} history {target_channel.id}: {e}", exc_info=True)
            await interaction.edit_original_response(content=f"⚠️ An error occurred while reading the {channel_type_str} history.") # MODIFIED
            return

        if not unique_user_ids:
            await interaction.edit_original_response(content=f"ℹ️ No messages found from non-bot users in {channel_type_str} `{target_channel.name}` for `{date_str}`.") # MODIFIED
            return

        await interaction.edit_original_response(content=f"⏳ Collected {len(unique_user_ids)} unique users from {channel_type_str}. Fetching wallets...") # MODIFIED

        results: List[Tuple[str, str]] = []
        api_tasks = []

        for user_id in unique_user_ids:
            user = self.bot.get_user(user_id)
            if not user:
                try: user = await self.bot.fetch_user(user_id)
                except discord.NotFound:
                    logger.warning(f"Could not find user with ID {user_id} for text collection.")
                    results.append((f"UnknownUser (ID: {user_id})", "User not found"))
                    continue
                except Exception as e_fetch:
                    logger.error(f"Error fetching user {user_id}: {e_fetch}")
                    results.append((f"UnknownUser (ID: {user_id})", "Error fetching user"))
                    continue
            
            if user.discriminator == '0': discord_handle = user.name
            else: discord_handle = f"{user.name}#{user.discriminator}"

            tasks_for_handle = []
            if self.snag_client: tasks_for_handle.append(self._fetch_wallet_from_api(self.snag_client, discord_handle))
            else: tasks_for_handle.append(asyncio.sleep(0, result=None))

            if self.snag_client_legacy: tasks_for_handle.append(self._fetch_wallet_from_api(self.snag_client_legacy, discord_handle))
            else: tasks_for_handle.append(asyncio.sleep(0, result=None))

            api_tasks.append((discord_handle, asyncio.gather(*tasks_for_handle, return_exceptions=True)))
            
            if len(api_tasks) % 10 == 0: await asyncio.sleep(0.1) 

        final_wallet_statuses: List[Tuple[str, str]] = []
        processed_api_tasks = 0
        for handle, gather_task in api_tasks:
            try:
                api_responses = await gather_task
                wallet_main = api_responses[0] if len(api_responses) > 0 and not isinstance(api_responses[0], Exception) else None
                wallet_legacy = api_responses[1] if len(api_responses) > 1 and not isinstance(api_responses[1], Exception) else None

                if len(api_responses) > 0 and isinstance(api_responses[0], Exception): logger.error(f"API Error (Main) for {handle}: {api_responses[0]}")
                if len(api_responses) > 1 and isinstance(api_responses[1], Exception): logger.error(f"API Error (Legacy) for {handle}: {api_responses[1]}")
                
                chosen_wallet = "Wallet Not Found"
                if wallet_main and wallet_legacy:
                    if wallet_main == wallet_legacy: chosen_wallet = wallet_main
                    else:
                        chosen_wallet = wallet_main
                        logger.info(f"For {handle} addresses DIFFER. Main: {wallet_main}, Legacy: {wallet_legacy}. Chose Main.")
                elif wallet_main: chosen_wallet = wallet_main
                elif wallet_legacy: chosen_wallet = wallet_legacy
                
                final_wallet_statuses.append((handle, chosen_wallet))
            except Exception as e_gather:
                logger.error(f"Error processing API requests for {handle}: {e_gather}", exc_info=True)
                final_wallet_statuses.append((handle, "Error during API lookup"))
            
            processed_api_tasks += 1
            if processed_api_tasks % 20 == 0:
                 await interaction.edit_original_response(content=f"⏳ Fetching wallets... {processed_api_tasks}/{len(api_tasks)} users processed...") # MODIFIED

        if not final_wallet_statuses:
             await interaction.edit_original_response(content="Could not retrieve wallet information for the collected users.") # MODIFIED
             return

        file_content = f"Address collection from {channel_type_str}: #{target_channel.name} (ID: {target_channel.id})\n"
        file_content += f"Collection date: {date_str}\n"
        file_content += f"Messages scanned (approx.): {messages_scanned}\n"
        file_content += f"Unique users: {len(unique_user_ids)}\n"
        file_content += "---------------------------------------\n"
        file_content += "Discord Handle: Wallet Address\n"
        file_content += "---------------------------------------\n"

        for handle, wallet_status in final_wallet_statuses:
            file_content += f"{handle}: {wallet_status}\n"

        file_bytes = file_content.encode('utf-8')
        data_stream = io.BytesIO(file_bytes)
        
        safe_channel_name = "".join(c if c.isalnum() else "_" for c in target_channel.name)
        filename = f"wallets_{safe_channel_name}_{date_str.replace('-', '')}.txt"
        
        discord_file = discord.File(fp=data_stream, filename=filename)
        
        try:
            await interaction.edit_original_response(
                content=f"✅ Collection complete! Results are in the attached file.", # MODIFIED
                attachments=[discord_file]
            )
        except discord.HTTPException as e:
            logger.warning(f"Could not edit original interaction response, sending file as a new followup: {e}")
            await interaction.followup.send(
                 f"✅ Collection complete! Results are in the attached file.", # MODIFIED
                 file=discord_file,
                 ephemeral=True
            )
        logger.info(f"Collection from {channel_type_str} {target_channel.id} for {date_str} completed. File {filename} sent.")


    @commands.command(name="send_textcollector_panel")
    @commands.has_any_role("Ranger") 
    async def send_textcollector_panel_command(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Text Chat Address Collector Panel", # MODIFIED
            description="Click the button to specify parameters and start collecting from a text or voice chat.", # MODIFIED
            color=discord.Color.dark_teal()
        )
        view = TextCollectorPanelView(self)
        await ctx.send(embed=embed, view=view)
        logger.info(f"TextCollector panel sent by {ctx.author.name} to channel {ctx.channel.id}")

    @send_textcollector_panel_command.error
    async def send_textcollector_panel_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingAnyRole):
            await ctx.send("⛔ You do not have the required 'Ranger' role to use this command.") # MODIFIED
        else:
            logger.error(f"Error in send_textcollector_panel command: {error}", exc_info=True)
            await ctx.send("⚙️ An unexpected error occurred while sending the control panel.") # MODIFIED

async def setup(bot: commands.Bot):
    if not getattr(bot, 'snag_client', None) and not getattr(bot, 'snag_client_legacy', None):
        logger.error("CRITICAL: No Snag API client (main or legacy) is configured in the bot. TextCollectorCog will NOT be loaded.")
        return

    cog = TextCollectorCog(bot)
    await bot.add_cog(cog)
    bot.add_view(TextCollectorPanelView(cog))
    logger.info("Registered persistent View for TextCollectorCog.")