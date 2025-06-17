# cogs/smash_karts_cog.py
import discord
from discord.ext import commands
from discord import app_commands
import logging
import io # Для работы с BytesIO для файла
from typing import List, Dict, Optional, Tuple
from collections import Counter # Для подсчета элементов

logger = logging.getLogger(__name__)

# --- Константы ---
REGIONS = ["Asia", "EU"]
SERVERS = ["Camp", "Curvance"]
RANGER_ROLE_NAME = "Ranger" # Роль, необходимая для запуска команд администратора
TOURNAMENT_CHAT_CHANNEL_ID = 1378074870876737686 # ID канала, на который будет вести кнопка

# --- ID ролей для регионов ---
# УБЕДИТЕСЬ, ЧТО ЭТИ ID ВЕРНЫ ДЛЯ ВАШЕГО СЕРВЕРА
ROLE_ID_ASIA = 1379124247707914382
ROLE_ID_EU = 1379124386597965935

REGION_ROLES = {
    "Asia": ROLE_ID_ASIA,
    "EU": ROLE_ID_EU,
}

# URL картинки для анонса турнира
TOURNAMENT_IMAGE_URL = "https://media.discordapp.net/attachments/1376635145943253002/1378089717739946025/CAMPXCURVANCE-.png?ex=683b5590&is=683a0410&hm=8d9793a6bff142f55b09a0660ed9576ad90cd436d398382eeb167bba234c1478&=&format=webp&quality=lossless&width=1328&height=747"


class RegistrationData:
    """Хранит данные для одной регистрации."""
    def __init__(self, user_id: int, user_name: str, region: str, server: str):
        self.user_id = user_id
        self.user_name = user_name
        self.region = region
        self.server = server

    def __str__(self):
        return f"{self.user_name} (ID: {self.user_id}) - Region: {self.region}, Server: {self.server}"

# --- View для шагов регистрации (эфемерные) ---

class ServerSelectView(discord.ui.View):
    """View для выбора сервера после выбора региона."""
    def __init__(self, cog_instance: "SmashKartsCog", region: str):
        super().__init__(timeout=180.0)
        self.cog = cog_instance
        self.selected_region = region
        self.message: Optional[discord.Message] = None # Сообщение, которое этот View редактирует

        for server_name in SERVERS:
            self.add_item(ServerButton(server_name, self))

    async def on_timeout(self):
        if self.message:
            try:
                # Убираем кнопки при таймауте
                await self.message.edit(content="Registration step timed out. Please try again.", view=None)
            except discord.HTTPException:
                pass
        self.stop()

class ServerButton(discord.ui.Button['ServerSelectView']):
    def __init__(self, server_name: str, parent_view: ServerSelectView):
        super().__init__(label=server_name, style=discord.ButtonStyle.secondary, custom_id=f"smash_server:{server_name.lower()}_v2") # Добавил _v2 к ID на всякий случай
        self.server_name = server_name

    async def callback(self, interaction: discord.Interaction):
        # Откладываем ответ, так как finalize_registration будет редактировать исходное сообщение
        # Это основной ответ на нажатие кнопки выбора сервера.
        await interaction.response.defer()

        # Вызываем finalize_registration, который обновит сообщение (self.view.message)
        await self.view.cog.finalize_registration(
            interaction, # Передаем interaction для получения user и guild
            self.view.message, # Передаем сообщение для редактирования
            self.view.selected_region,
            self.server_name
        )
        
        # View больше не нужен, finalize_registration обновил его до view=None или нового view
        self.view.stop()


class RegionSelectView(discord.ui.View):
    """View для выбора региона."""
    def __init__(self, cog_instance: "SmashKartsCog"):
        super().__init__(timeout=180.0)
        self.cog = cog_instance
        self.message: Optional[discord.Message] = None # Сообщение, которое этот View редактирует

        for region_name in REGIONS:
            self.add_item(RegionButton(region_name, self))
    
    async def on_timeout(self):
        if self.message:
            try:
                # Убираем кнопки при таймауте
                await self.message.edit(content="Registration step timed out. Please try again.", view=None)
            except discord.HTTPException:
                pass
        self.stop()

class RegionButton(discord.ui.Button['RegionSelectView']):
    def __init__(self, region_name: str, parent_view: RegionSelectView):
        super().__init__(label=region_name, style=discord.ButtonStyle.primary, custom_id=f"smash_region:{region_name.lower()}_v2") # Добавил _v2
        self.region_name = region_name

    async def callback(self, interaction: discord.Interaction):
        # Это interaction от нажатия кнопки выбора региона.
        # Мы должны ответить на него, отредактировав сообщение и заменив View.
        
        server_selection_view = ServerSelectView(self.view.cog, self.region_name)
        
        if self.view.message:
            # Редактируем исходное эфемерное сообщение, заменяя View
            await interaction.response.edit_message(
                content=f"Region **{self.region_name}** selected. Now, please choose your server:",
                view=server_selection_view
            )
            # Передаем то же самое сообщение для редактирования следующему View
            server_selection_view.message = await interaction.original_response() # Получаем обновленное сообщение
        else:
            logger.error("RegionButton callback: self.view.message is None, cannot edit.")
            # Если исходное сообщение не найдено, отправляем новое (хотя это маловероятно)
            await interaction.response.send_message("An error occurred, please try registering again.", ephemeral=True)
            return
        
        self.view.stop() # Останавливаем текущий View


# --- Главный View для объявления турнира (постоянный) ---

class TournamentRegisterView(discord.ui.View):
    """Постоянный View, прикрепленный к главному объявлению турнира."""
    def __init__(self, cog_instance: "SmashKartsCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    @discord.ui.button(label="📝 Register for Tournament", style=discord.ButtonStyle.green, custom_id="smashkarts:register_flow_start_v3")
    async def register_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.cog.tournament_active:
            await interaction.response.send_message("Tournament registration is currently closed.", ephemeral=True)
            return

        if any(reg.user_id == interaction.user.id for reg in self.cog.registrations):
            await interaction.response.send_message("You are already registered for this tournament!", ephemeral=True)
            return

        region_selection_view = RegionSelectView(self.cog)
        await interaction.response.send_message("Please select your region:", view=region_selection_view, ephemeral=True)
        # Сохраняем сообщение для эфемерного View, чтобы его можно было редактировать и завершить по таймауту
        region_selection_view.message = await interaction.original_response()


# --- Реализация Кога ---

class SmashKartsCog(commands.Cog, name="Smash Karts Tournament"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.registrations: List[RegistrationData] = []
        self.announcement_message_id: Optional[int] = None
        self.announcement_channel_id: Optional[int] = None
        self.tournament_active: bool = False
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    async def cog_load(self):
        self.bot.add_view(TournamentRegisterView(self))
        logger.info(f"Cog '{self.__class__.__name__}' initialized. TournamentRegisterView registered.")

    async def finalize_registration(self, interaction: discord.Interaction, message_to_edit: Optional[discord.Message], region: str, server: str):
        user = interaction.user
        guild = interaction.guild

        if not isinstance(user, discord.Member):
            logger.warning(f"User {user.id} is not a Member object in finalize_registration. Cannot assign roles.")
            if message_to_edit:
                try:
                    await message_to_edit.edit(
                        content="⚠️ An error occurred. Could not retrieve your server member information. Please try again or contact an admin.",
                        view=None # Убираем кнопки
                    )
                except discord.HTTPException as e:
                     logger.error(f"Failed to edit message in finalize_registration for non-member: {e}")
            return

        new_registration = RegistrationData(user.id, str(user), region, server)
        self.registrations.append(new_registration)
        logger.info(f"New Smash Karts registration: {new_registration}")

        role_id_to_assign = REGION_ROLES.get(region)
        role_assigned_message = ""

        if role_id_to_assign and guild:
            role = guild.get_role(role_id_to_assign)
            if role:
                try:
                    await user.add_roles(role, reason=f"Smash Karts Tournament Registration - Region: {region}")
                    role_assigned_message = f"\n✅ The **{role.name}** role has been assigned to you."
                    logger.info(f"Assigned role '{role.name}' (ID: {role.id}) to {user.name} for region {region}.")
                except discord.Forbidden:
                    role_assigned_message = f"\n⚠️ I don't have permission to assign the **{role.name}** role. Please contact an admin."
                    logger.error(f"Forbidden to assign role {role.name} (ID: {role.id}) to {user.name}.")
                except discord.HTTPException as e:
                    role_assigned_message = f"\n⚠️ An error occurred while assigning the role: {e}. Please contact an admin."
                    logger.error(f"HTTP error assigning role {role.name} (ID: {role.id}) to {user.name}: {e}")
            else:
                role_assigned_message = f"\n⚠️ The role for region **{region}** (ID: {role_id_to_assign}) was not found on this server. Please contact an admin."
                logger.error(f"Role ID {role_id_to_assign} for region {region} not found in guild {guild.id}.")
        elif not guild:
            role_assigned_message = "\n⚠️ Could not assign roles as this interaction is not in a server."
            logger.warning("Finalize_registration called without a guild context.")
        elif not role_id_to_assign:
            role_assigned_message = f"\n⚠️ No role configured for region **{region}**. Please contact an admin."
            logger.warning(f"No role ID configured for region {region} in REGION_ROLES.")

        # Создаем View с кнопкой-ссылкой
        final_view_with_link = None
        if guild:
            # Убедимся, что TOURNAMENT_CHAT_CHANNEL_ID - это число
            try:
                chat_channel_id = int(TOURNAMENT_CHAT_CHANNEL_ID)
                channel_url = f"https://discord.com/channels/{guild.id}/{chat_channel_id}"
                final_view_with_link = discord.ui.View(timeout=None)
                final_view_with_link.add_item(discord.ui.Button(label="Go to Tournament Chat!", style=discord.ButtonStyle.link, url=channel_url))
            except ValueError:
                logger.error(f"TOURNAMENT_CHAT_CHANNEL_ID ('{TOURNAMENT_CHAT_CHANNEL_ID}') is not a valid integer. Cannot create link button.")
        else:
            logger.warning("Cannot create 'Go to Tournament Chat!' button because guild is not available.")


        if message_to_edit:
            try:
                await message_to_edit.edit(
                    content=f"✅ You have successfully registered for the Smash Karts tournament!\n"
                            f"Region: **{region}**, Server: **{server}**."
                            f"{role_assigned_message}\nGood luck! 🏎️",
                    view=final_view_with_link # Заменяем view на новый с кнопкой-ссылкой (или None)
                )
            except discord.HTTPException as e:
                logger.error(f"Failed to edit final registration message: {e}")
        else:
            logger.error("message_to_edit was None in finalize_registration. Cannot update user.")


    async def _remove_tournament_roles(self, guild: discord.Guild):
        removed_count = 0
        if not self.registrations:
            return removed_count

        all_tournament_role_ids = [role_id for role_id in REGION_ROLES.values() if role_id is not None]
        
        if not all_tournament_role_ids:
            logger.info("No tournament role IDs configured to remove during cleanup.")
            return removed_count

        for reg_data in self.registrations:
            try:
                member = guild.get_member(reg_data.user_id) or await guild.fetch_member(reg_data.user_id)
                if member:
                    user_region_role_id = REGION_ROLES.get(reg_data.region)
                    if user_region_role_id:
                        user_role_to_remove = guild.get_role(user_region_role_id)
                        if user_role_to_remove and user_role_to_remove in member.roles:
                            try:
                                await member.remove_roles(user_role_to_remove, reason="Smash Karts Tournament Ended")
                                removed_count += 1
                                logger.info(f"Removed role '{user_role_to_remove.name}' from {member.name}")
                            except discord.Forbidden:
                                logger.warning(f"No permission to remove role '{user_role_to_remove.name}' from {member.name}.")
                            except discord.HTTPException as e:
                                logger.error(f"Failed to remove role '{user_role_to_remove.name}' from {member.name}: {e}")
            except discord.NotFound:
                logger.warning(f"Member {reg_data.user_id} not found in guild during role cleanup.")
            except Exception as e:
                logger.error(f"Error processing member {reg_data.user_id} for role cleanup: {e}", exc_info=True)
        logger.info(f"Attempted to remove tournament roles. Actual roles removed: {removed_count}")
        return removed_count


    @app_commands.command(name="smash_karts_tournament", description="Starts a Smash Karts tournament event.")
    @app_commands.describe(target_channel_id="ID of the channel to send the announcement to.")
    @app_commands.checks.has_any_role(RANGER_ROLE_NAME)
    async def smash_karts_tournament_slash(self, interaction: discord.Interaction, target_channel_id: str):
        if self.tournament_active:
            await interaction.response.send_message("A tournament is already active. Please end it first using `/end_smash_karts_tournament`.", ephemeral=True)
            return

        try:
            channel_id = int(target_channel_id)
            announce_channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            if not isinstance(announce_channel, discord.TextChannel):
                await interaction.response.send_message("Target channel ID must be for a text channel.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("Invalid target channel ID format.", ephemeral=True)
            return
        except discord.NotFound:
            await interaction.response.send_message("Target announcement channel not found.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permissions to access the target announcement channel.", ephemeral=True)
            return

        if not interaction.guild:
             await interaction.response.send_message("This command must be used within a server.", ephemeral=True)
             return

        await interaction.response.defer(ephemeral=True)
        
        embed = discord.Embed(
            title="🏁 Smash Karts Tournament Day 2 Registration is ON! 🏁",
            description=(
                f"Get your karts ready for an epic showdown! Fame, glory, and fun await!\n\n"
                f"**How to Join:**\n"
                f"1. Click the '📝 Register for Tournament' button below.\n"
                f"2. Select your **Region** (Asia or EU).\n"
                f"3. Select your **Server** (Camp or Curvance).\n"
                f"*You will be assigned a temporary role based on your region.*\n\n"
                f"Good luck to all participants!"
            ),
            color=discord.Color.orange()
        )
        embed.set_image(url=TOURNAMENT_IMAGE_URL)
        embed.set_footer(text="Tournament registration is now open! Click below to start.")

        try:
            view = TournamentRegisterView(self)
            ann_msg = await announce_channel.send(embed=embed, view=view)
            self.announcement_message_id = ann_msg.id
            self.announcement_channel_id = announce_channel.id
            self.tournament_active = True
            self.registrations.clear()
            
            await interaction.followup.send(f"Smash Karts tournament announcement sent to <#{announce_channel.id}>!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("I don't have permission to send messages in the target announcement channel.", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"Failed to send tournament announcement: {e}", ephemeral=True)

    @app_commands.command(name="end_smash_karts_tournament", description="Ends the current Smash Karts tournament and sends a report as a file.")
    @app_commands.checks.has_any_role(RANGER_ROLE_NAME)
    async def end_smash_karts_tournament_slash(self, interaction: discord.Interaction):
        if not self.tournament_active:
            await interaction.response.send_message("No Smash Karts tournament is currently active to end.", ephemeral=True)
            return

        if not interaction.guild:
             await interaction.response.send_message("This command must be used within a server.", ephemeral=True)
             return

        await interaction.response.defer(ephemeral=False)
        
        region_counts = Counter(reg.region for reg in self.registrations)

        report_lines = [f"🏆 Smash Karts Tournament Report - Concluded: {discord.utils.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"]
        report_lines.append("--- Registrations by Region ---")
        for region_name in REGIONS:
            count = region_counts.get(region_name, 0)
            report_lines.append(f"{region_name}: {count} participant(s)")
        report_lines.append("-------------------------------")
        report_lines.append(f"Total Registrations: {len(self.registrations)}\n")


        if self.registrations:
            report_lines.append("📋 Registered Participants:")
            report_lines.append("--------------------------------------------------")
            report_lines.append(f"{'No.':<4} | {'Username':<35} | {'Region':<10} | {'Server':<10}")
            report_lines.append("--------------------------------------------------")
            for i, reg_data in enumerate(self.registrations):
                report_lines.append(f"{i+1:<4} | {reg_data.user_name:<35} | {reg_data.region:<10} | {reg_data.server:<10}")
            report_lines.append("--------------------------------------------------")
        else:
            report_lines.append("ℹ️ No participants registered for this tournament.")

        report_content = "\n".join(report_lines)

        report_file_bytes = report_content.encode('utf-8')
        report_file_stream = io.BytesIO(report_file_bytes)
        filename = f"smash_karts_report_{discord.utils.utcnow().strftime('%Y%m%d_%H%M')}.txt"
        discord_report_file = discord.File(fp=report_file_stream, filename=filename)

        try:
            await interaction.followup.send(
                f"Smash Karts tournament has ended. The full report is attached below.",
                file=discord_report_file
            )
            logger.info(f"Smash Karts tournament report sent as a file to channel {interaction.channel.id if interaction.channel else 'DM'}")
        except discord.Forbidden:
            logger.error(f"Forbidden to send Smash Karts report file in channel {interaction.channel.id if interaction.channel else 'DM'}")
            return 
        except discord.HTTPException as e:
            logger.error(f"HTTP error sending Smash Karts report file: {e}")
            return

        if self.announcement_channel_id and self.announcement_message_id:
            try:
                ann_channel = interaction.guild.get_channel(self.announcement_channel_id) or \
                              await self.bot.fetch_channel(self.announcement_channel_id)
                if ann_channel and isinstance(ann_channel, discord.TextChannel):
                    ann_msg = await ann_channel.fetch_message(self.announcement_message_id)
                    if ann_msg.embeds:
                        embed = ann_msg.embeds[0]
                        embed.title = "🏁 Smash Karts Tournament Registration - ENDED 🏁"
                        embed.description = "Check the tournament chat."
                        embed.color = discord.Color.dark_grey()
                        embed.set_footer(text="Registration is closed.")
                        await ann_msg.edit(embed=embed, view=None)
                        logger.info(f"Edited announcement message {self.announcement_message_id} to indicate tournament end.")
                    else:
                         await ann_msg.edit(content="Registration is closed.", view=None)
            except discord.NotFound:
                logger.warning(f"Original announcement message {self.announcement_message_id} in channel {self.announcement_channel_id} not found for editing.")
            except discord.Forbidden:
                logger.warning(f"No permission to edit announcement message {self.announcement_message_id} in channel {self.announcement_channel_id}.")
            except Exception as e:
                logger.error(f"Error editing original announcement message: {e}", exc_info=True)
        
        #removed_roles_count = await self._remove_tournament_roles(interaction.guild)
        #logger.info(f"Attempted removal of {removed_roles_count} tournament roles.")

        self.registrations.clear()
        self.announcement_message_id = None
        self.announcement_channel_id = None
        self.tournament_active = False

        logger.info(f"Smash Karts tournament ended by {interaction.user.name}.")


    @smash_karts_tournament_slash.error
    @end_smash_karts_tournament_slash.error
    async def smash_karts_error_handler(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingAnyRole):
            await interaction.response.send_message(f"You don't have the '{RANGER_ROLE_NAME}' role to use this command.", ephemeral=True)
        elif isinstance(error, app_commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
            await interaction.response.send_message(f"I'm missing permissions to perform an action. Details: {error.original.text}", ephemeral=True)
        else:
            logger.error(f"Error in Smash Karts command '{interaction.command.name if interaction.command else 'Unknown'}': {error}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message("An unexpected error occurred while processing the command.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(SmashKartsCog(bot))