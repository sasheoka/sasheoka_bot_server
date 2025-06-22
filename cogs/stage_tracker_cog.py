# cogs/stage_tracker_cog.py
import discord
from discord.ext import commands, tasks
import logging
import datetime
import os
import asyncio
import io # Для создания файла в памяти
from typing import Dict, Set, Optional, List, Tuple # Добавлены List и Tuple
from utils.checks import is_prefix_admin_in_guild

logger = logging.getLogger(__name__)

# --- View Class for Control Panel ---
class StageTrackerView(discord.ui.View):
    def __init__(self, cog_instance: "StageTrackerCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance
        self._update_buttons()

    def _update_buttons(self):
        if self.cog.is_running:
            self.start_button.disabled = True
            self.stop_button.disabled = False
            self.process_users_button.disabled = False # Можно обрабатывать, когда запущено
        else:
            self.start_button.disabled = False
            self.stop_button.disabled = True
            # Разрешаем обработку даже если мониторинг остановлен, но есть пользователи в очереди
            self.process_users_button.disabled = not bool(self.cog.users_met_voice_criteria)


    async def _check_ranger_role(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
             await interaction.response.send_message("This command can only be used on a server.", ephemeral=True)
             return False
        ranger_role = discord.utils.get(interaction.guild.roles, name="Ranger")
        if not ranger_role:
            await interaction.response.send_message("⛔ The 'Ranger' role was not found on this server.", ephemeral=True)
            return False
        if ranger_role not in interaction.user.roles:
            await interaction.response.send_message("⛔ You do not have the required 'Ranger' role to use this button.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="▶️ Start Tracking", style=discord.ButtonStyle.green, custom_id="stagetrack:start_v2", row=0)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return

        success = await self.cog.start_tracking()
        if success:
            self._update_buttons()
            try:
                 if interaction.message: await interaction.message.edit(view=self)
            except discord.NotFound: logger.warning("Could not edit original panel message (possibly deleted).")
            except discord.HTTPException as e: logger.error(f"Error editing panel message: {e}")
            await interaction.response.send_message("✅ Stage channel monitoring started.", ephemeral=True)
        else:
             await interaction.response.send_message("⚠️ Could not start monitoring. Check configuration (channel ID) and logs.", ephemeral=True)

    @discord.ui.button(label="⏹️ Stop Tracking", style=discord.ButtonStyle.red, custom_id="stagetrack:stop_v2", row=0)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return

        await self.cog.stop_tracking()
        self._update_buttons()
        try:
            if interaction.message: await interaction.message.edit(view=self)
        except discord.NotFound: logger.warning("Could not edit original panel message (possibly deleted).")
        except discord.HTTPException as e: logger.error(f"Error editing panel message: {e}")
        await interaction.response.send_message("⏹️ Stage channel monitoring stopped.", ephemeral=True)

    @discord.ui.button(label="📊 Show Status", style=discord.ButtonStyle.blurple, custom_id="stagetrack:status_v2", row=0)
    async def status_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return

        await interaction.response.defer(ephemeral=True)
        status_embed = await self.cog.get_status_embed()
        await interaction.followup.send(embed=status_embed, ephemeral=True)

    @discord.ui.button(label="⚙️ Process Eligible Users", style=discord.ButtonStyle.secondary, custom_id="stagetrack:process_v2", row=1)
    async def process_users_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return

        await interaction.response.defer(thinking=True, ephemeral=True)
        processed_count, file_content, error_message = await self.cog.process_eligible_users()

        self._update_buttons() # Обновляем состояние кнопок, т.к. users_met_voice_criteria может измениться
        try:
            if interaction.message: await interaction.message.edit(view=self)
        except discord.NotFound: logger.warning("Could not edit original panel message (possibly deleted).")
        except discord.HTTPException as e: logger.error(f"Error editing panel message: {e}")

        if error_message:
            await interaction.followup.send(error_message, ephemeral=True)
        elif file_content:
            file_bytes = file_content.encode('utf-8')
            data_stream = io.BytesIO(file_bytes)
            filename = f"stage_wallets_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
            discord_file = discord.File(fp=data_stream, filename=filename)
            await interaction.followup.send(
                f"✅ Processed {processed_count} users who met the time criteria. Results in the attached file.",
                file=discord_file,
                ephemeral=True
            )
        else:
            await interaction.followup.send("ℹ️ No users were found eligible for processing at this time.", ephemeral=True)


# --- Cog Class ---
class StageTrackerCog(commands.Cog, name="Stage Tracker"):
    """
    Tracks user activity in a specified Stage channel.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_sessions: Dict[int, datetime.datetime] = {}
        self.users_met_voice_criteria: Set[int] = set()
        self._lock = asyncio.Lock()

        # API Clients
        self.snag_client = getattr(bot, 'snag_client', None)
        self.snag_client_legacy = getattr(bot, 'snag_client_legacy', None)

        self.min_duration: Optional[datetime.timedelta] = None
        self.is_running: bool = False

        # --- ЗАГРУЗКА ID КАНАЛА ИЗ .ENV ---
        try:
            self.target_channel_id = int(os.getenv('STAGE_CHANNEL_ID', 0))
        except (ValueError, TypeError):
            self.target_channel_id = 0

        try:
            # Оставляем чтение MIN_DURATION_SECONDS из .env или задаем его тоже, если нужно
            duration_str = os.getenv('MIN_DURATION_SECONDS')
            if not duration_str:
                logger.warning("MIN_DURATION_SECONDS not found in .env! Using default: 600 seconds (10 minutes).")
                self.min_duration = datetime.timedelta(seconds=600)
            else:
                try:
                    self.min_duration = datetime.timedelta(seconds=int(duration_str))
                except ValueError:
                    logger.error("MIN_DURATION_SECONDS in .env is not a valid number. Using default: 600 seconds.")
                    self.min_duration = datetime.timedelta(seconds=600)


            logger.info(f"Cog '{self.__class__.__name__}' loaded.")
            if self.target_channel_id:
                logger.info(f"  Target Stage Channel ID: {self.target_channel_id} (Loaded from .env)")
            else:
                logger.error("  Target Stage Channel ID is NOT SET or invalid in .env. Monitoring will not work.")

            if self.min_duration:
                logger.info(f"  Minimum Duration: {self.min_duration.total_seconds()} seconds")

        except Exception as e:
            logger.exception(f"Unexpected error during {self.__class__.__name__} initialization: {e}")
            if not self.target_channel_id:
                 logger.error("  Failed to initialize Target Stage Channel ID. Monitoring will not work.")


    async def cog_load(self):
        if not self.snag_client:
            logger.error(f"{self.__class__.__name__}: Main SnagApiClient (bot.snag_client) not found!")
        if not self.snag_client_legacy:
            logger.warning(f"{self.__class__.__name__}: Legacy SnagApiClient (bot.snag_client_legacy) not found! Address comparison will be incomplete.")
        logger.info(f"Cog '{self.__class__.__name__}' successfully initialized by bot.")

    async def cog_unload(self):
        await self.stop_tracking()
        logger.info(f"Cog '{self.__class__.__name__}' unloaded. Monitoring stopped.")

    async def start_tracking(self) -> bool:
        if self.is_running:
            logger.warning("Attempted to start tracking when it's already running.")
            return True
        if not self.target_channel_id or not self.min_duration: # Проверяем и min_duration тоже
             logger.error("Cannot start monitoring: Channel ID or minimum duration not configured correctly.")
             return False

        try:
            channel = self.bot.get_channel(self.target_channel_id)
            if not channel:
                logger.info(f"Channel {self.target_channel_id} not in cache, fetching...")
                channel = await self.bot.fetch_channel(self.target_channel_id)
            if not isinstance(channel, discord.StageChannel):
                logger.error(f"Channel ID {self.target_channel_id} is not a Stage channel! Monitoring not started.")
                return False
            logger.info(f"Target channel '{channel.name}' found and is a Stage channel.")
        except discord.NotFound:
            logger.error(f"Channel ID {self.target_channel_id} not found! Monitoring not started.")
            return False
        except discord.Forbidden:
            logger.error(f"No permission to access channel ID {self.target_channel_id}. Monitoring not started.")
            return False
        except Exception as e:
            logger.exception(f"Error checking channel {self.target_channel_id}: {e}")
            return False

        self.is_running = True
        logger.info(f"Stage channel monitoring {self.target_channel_id} STARTED.")
        return True

    async def stop_tracking(self):
        if not self.is_running: return
        self.is_running = False
        logger.info(f"Stage channel monitoring {self.target_channel_id} STOPPED.")

    async def get_status_embed(self) -> discord.Embed:
        status_text = "🟢 Running" if self.is_running else "🔴 Stopped"
        channel_name = "Not configured or not found"
        if self.target_channel_id:
            channel = self.bot.get_channel(self.target_channel_id)
            channel_name = f"'{channel.name}' ({self.target_channel_id})" if channel else f"ID: {self.target_channel_id} (Not in cache/Fetch failed)"

        async with self._lock: users_ready_count = len(self.users_met_voice_criteria)

        embed = discord.Embed(title="📊 Stage Channel Monitoring Status", color=discord.Color.blue())
        embed.add_field(name="State", value=status_text, inline=True)
        embed.add_field(name="Target Channel", value=channel_name, inline=True)
        embed.add_field(name="Min. Duration", value=f"{self.min_duration.total_seconds()} sec" if self.min_duration else "Not set", inline=True)
        embed.add_field(name="Currently in Channel", value=f"{len(self.active_sessions)} users", inline=True)
        embed.add_field(name="Eligible for Processing", value=f"{users_ready_count} users", inline=True)
        embed.set_footer(text="Eligible users have met the duration criteria; their wallets have not yet been fetched.")
        embed.timestamp = discord.utils.utcnow()
        return embed

    @commands.Cog.listener("on_voice_state_update")
    async def track_stage_activity(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if not self.is_running or not self.target_channel_id or member.bot: return
        target_id = self.target_channel_id

        if after.channel and after.channel.id == target_id and (not before.channel or before.channel.id != target_id):
            if member.id not in self.active_sessions:
                self.active_sessions[member.id] = discord.utils.utcnow()
                logger.info(f"➕ StageTracker: {member.name} ({member.id}) joined channel {target_id}.")
        elif before.channel and before.channel.id == target_id and (not after.channel or after.channel.id != target_id):
            if member.id in self.active_sessions:
                join_time = self.active_sessions.pop(member.id)
                duration = discord.utils.utcnow() - join_time
                logger.info(f"➖ StageTracker: {member.name} ({member.id}) left channel {target_id}. Duration: {duration}.")
                if self.min_duration and duration >= self.min_duration: # Проверяем, что min_duration установлено
                    async with self._lock: self.users_met_voice_criteria.add(member.id)
                    logger.info(f"👍 StageTracker: {member.name} ({member.id}) met criteria ({duration.total_seconds():.0f}s). Added to queue.")
            else:
                 logger.warning(f"⚠️ StageTracker: {member.name} ({member.id}) left channel {target_id}, but not found in active_sessions.")

    async def _fetch_wallet_from_api_st(self, client, handle: str) -> Optional[str]: # Renamed to avoid conflict if merged
        """Helper to fetch wallet from a single API client."""
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
                if wallet_address: return str(wallet_address).lower()
        return None

    async def process_eligible_users(self) -> Tuple[int, Optional[str], Optional[str]]:
        """Processes users from users_met_voice_criteria, fetches wallets, and returns content for a TXT file."""
        async with self._lock:
            if not self.users_met_voice_criteria:
                return 0, None, "No users are currently eligible for processing."
            
            user_ids_to_process = list(self.users_met_voice_criteria)
            self.users_met_voice_criteria.clear()
        
        if not self.snag_client and not self.snag_client_legacy:
            logger.error("Cannot process eligible users: No Snag API clients are available.")
            async with self._lock: self.users_met_voice_criteria.update(user_ids_to_process)
            return 0, None, "⚠️ Cannot process users: Snag API clients are not configured."

        logger.info(f"Processing {len(user_ids_to_process)} eligible users from Stage channel...")
        
        final_wallet_statuses: List[Tuple[str, str]] = []
        api_tasks_meta = [] 

        for user_id in user_ids_to_process:
            user = self.bot.get_user(user_id)
            if not user:
                try: user = await self.bot.fetch_user(user_id)
                except discord.NotFound:
                    logger.warning(f"Could not find user ID {user_id} for Stage processing.")
                    final_wallet_statuses.append((f"UnknownUser (ID: {user_id})", "User not found by bot"))
                    continue
                except Exception as e_fetch:
                    logger.error(f"Error fetching user {user_id}: {e_fetch}")
                    final_wallet_statuses.append((f"UnknownUser (ID: {user_id})", "Error fetching user by bot"))
                    continue
            
            if user.discriminator == '0': discord_handle = user.name
            else: discord_handle = f"{user.name}#{user.discriminator}"

            tasks_for_handle = []
            if self.snag_client: tasks_for_handle.append(self._fetch_wallet_from_api_st(self.snag_client, discord_handle))
            else: tasks_for_handle.append(asyncio.sleep(0, result=None)) 

            if self.snag_client_legacy: tasks_for_handle.append(self._fetch_wallet_from_api_st(self.snag_client_legacy, discord_handle))
            else: tasks_for_handle.append(asyncio.sleep(0, result=None)) 
            
            api_tasks_meta.append((discord_handle, asyncio.gather(*tasks_for_handle, return_exceptions=True)))
            if len(api_tasks_meta) % 10 == 0: await asyncio.sleep(0.1) 

        processed_api_tasks_count = 0
        for handle, gather_task in api_tasks_meta:
            try:
                api_responses = await gather_task
                wallet_main = api_responses[0] if len(api_responses) > 0 and not isinstance(api_responses[0], Exception) else None
                wallet_legacy = api_responses[1] if len(api_responses) > 1 and not isinstance(api_responses[1], Exception) else None

                if len(api_responses) > 0 and isinstance(api_responses[0], Exception): logger.error(f"Stage API Error (Main) for {handle}: {api_responses[0]}")
                if len(api_responses) > 1 and isinstance(api_responses[1], Exception): logger.error(f"Stage API Error (Legacy) for {handle}: {api_responses[1]}")

                chosen_wallet = "Wallet Not Found"
                if wallet_main and wallet_legacy:
                    if wallet_main == wallet_legacy: chosen_wallet = wallet_main
                    else:
                        chosen_wallet = wallet_main 
                        logger.info(f"Stage: For {handle} addresses DIFFER. Main: {wallet_main}, Legacy: {wallet_legacy}. Chose Main.")
                elif wallet_main: chosen_wallet = wallet_main
                elif wallet_legacy: chosen_wallet = wallet_legacy
                
                final_wallet_statuses.append((handle, chosen_wallet))
            except Exception as e_gather:
                logger.error(f"Error processing Stage API requests for {handle}: {e_gather}", exc_info=True)
                final_wallet_statuses.append((handle, "Error during API lookup"))
            processed_api_tasks_count +=1
        
        if not final_wallet_statuses and not user_ids_to_process : 
             return 0, None, "No users were processed, or no wallet data could be retrieved."

        file_content_lines = [
            f"Wallet Collection from Stage Channel: {self.target_channel_id if self.target_channel_id else 'N/A'}",
            f"Processing Timestamp: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"Processed Users: {len(user_ids_to_process)}",
            "---------------------------------------",
            "Discord Handle: Wallet Address",
            "---------------------------------------"
        ]

        for handle, wallet_status in final_wallet_statuses:
            file_content_lines.append(f"{handle}: {wallet_status}")
        
        file_content = "\n".join(file_content_lines)
        
        return len(user_ids_to_process), file_content, None


    @commands.command(name="send_stage_panel")
    @is_prefix_admin_in_guild()
    async def send_stage_panel_command(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Stage Channel Activity Monitoring Panel",
            description="Use the buttons below to start, stop, check status, and process eligible users.",
            color=discord.Color.purple()
        )
        view = StageTrackerView(self)
        await ctx.send(embed=embed, view=view)
        logger.info(f"Stage Tracker panel sent by {ctx.author.name} (Ranger role) to channel {ctx.channel.id}")

    @send_stage_panel_command.error
    async def send_stage_panel_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingAnyRole):
            await ctx.send("⛔ You do not have the required 'Ranger' role to use this command.")
        else:
            logger.error(f"Error in send_stage_panel command: {error}", exc_info=True)
            await ctx.send("⚙️ An unexpected error occurred while sending the control panel.")

async def setup(bot: commands.Bot):
    # Убрана проверка STAGE_CHANNEL_ID из .env, так как он теперь задается в коде
    
    # Убедимся, что API клиенты есть в боте перед загрузкой кога
    if not getattr(bot, 'snag_client', None) and not getattr(bot, 'snag_client_legacy', None):
        logger.error("CRITICAL: No Snag API clients (main or legacy) found in bot. StageTrackerCog will NOT be loaded as it needs them for processing.")
        return

    cog = StageTrackerCog(bot)
    await bot.add_cog(cog)
    bot.add_view(StageTrackerView(cog))
    logger.info("Registered persistent View for StageTrackerCog.")