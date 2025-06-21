# cogs/engage_cog.py
import discord
from discord import app_commands
from discord.ext import commands
import os
import logging
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure
from typing import List, Dict, Optional

# --- Константы и Логгер ---
logger = logging.getLogger(__name__)
SUBMIT_ROLE_IDS = [1253785633621540895, 1161497860974587947]  # ID ролей для !submit
SUBMISSIONS_ROLE_IDS = [1161497860974587947]  # ID ролей для !submissions
ENGAGE_CHANNEL_ID = 1384466812766257193

# --- Модальное окно для сабмита ---
class SubmitModal(discord.ui.Modal, title="Submit Twitter Post"):
    twitter_link = discord.ui.TextInput(
        label="Twitter Post Link",
        placeholder="https://x.com/username/status/123...",
        required=True
    )

    def __init__(self, cog_instance: "EngageCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def on_submit(self, interaction: discord.Interaction):
        # defer() теперь внутри process_submission, чтобы избежать ошибок
        await self.cog.process_submission(interaction, self.twitter_link.value)

# --- View для просмотра сабмишенов ---
class SubmissionsView(discord.ui.View):
    def __init__(self, cog_instance: "EngageCog", submissions: List[Dict]):
        super().__init__(timeout=300) # Таймаут, чтобы View не висел вечно
        self.cog = cog_instance
        self.submissions = submissions
        self.current_index = 0
        self.message: Optional[discord.Message] = None
        self._update_buttons()

    def _update_buttons(self):
        self.children[0].disabled = self.current_index == 0
        self.children[1].disabled = self.current_index >= len(self.submissions) - 1
        self.children[2].disabled = not self.submissions
        self.children[3].disabled = not self.submissions

    def _create_embed(self) -> discord.Embed:
        if not self.submissions:
            return discord.Embed(title="Submissions Queue", description="No submissions found.", color=discord.Color.red())
        
        submission = self.submissions[self.current_index]
        embed = discord.Embed(
            title=f"Submission {self.current_index + 1}/{len(self.submissions)}",
            description=f"**Submitted by:** {submission.get('discord_handle', 'N/A')}\n"
                        f"**Link:** <{submission['twitter_link']}>",
            color=discord.Color.blue(),
            timestamp=submission.get('submitted_at')
        )
        return embed

    async def _update_message(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        self._update_buttons()
        embed = self._create_embed()
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, custom_id="engage_db:prev")
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_index > 0:
            self.current_index -= 1
            await self._update_message(interaction)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, custom_id="engage_db:next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_index < len(self.submissions) - 1:
            self.current_index += 1
            await self._update_message(interaction)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="engage_db:delete")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.submissions:
            await interaction.response.send_message("No submissions to delete.", ephemeral=True)
            return

        submission_to_delete = self.submissions.pop(self.current_index)
        self.cog._delete_submission(submission_to_delete['twitter_link'])
        
        if self.current_index >= len(self.submissions) and self.submissions:
            self.current_index = len(self.submissions) - 1
        
        logger.info(f"Deleted submission {submission_to_delete['twitter_link']} by {interaction.user}")
        await self._update_message(interaction)

    @discord.ui.button(label="Post to Engage", style=discord.ButtonStyle.green, custom_id="engage_db:post")
    async def post_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.submissions:
            await interaction.response.send_message("No submissions to post.", ephemeral=True)
            return
            
        submission_to_post = self.submissions[self.current_index]
        channel = self.cog.bot.get_channel(ENGAGE_CHANNEL_ID)
        
        if channel and isinstance(channel, discord.TextChannel):
            try:
                await channel.send(f"<{submission_to_post['twitter_link']}>")
                await interaction.response.send_message("✅ Posted to Engage channel!", ephemeral=True)
                logger.info(f"Posted {submission_to_post['twitter_link']} to channel {ENGAGE_CHANNEL_ID}")
            except discord.Forbidden:
                await interaction.response.send_message("⚠️ I don't have permission to post in the engage channel.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Engage channel not found or inaccessible.", ephemeral=True)

# --- Основной класс кога ---
class EngageCog(commands.Cog, name="Engage"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_client = None
        self.submissions_collection = None
        
        mongo_uri = os.getenv("MONGO_URI")
        if not mongo_uri:
            logger.critical("MONGO_URI environment variable not found! EngageCog will be disabled.")
            return

        try:
            # Подключаемся к MongoDB
            self.db_client = MongoClient(mongo_uri)
            # Проверка соединения (вызовет ошибку, если не удалось подключиться)
            self.db_client.admin.command('ping')
            # Получаем доступ к базе данных и коллекции
            self.db = self.db_client.get_database("discord_bot_db") # Можете назвать БД как угодно
            self.submissions_collection = self.db.get_collection("submissions")
            logger.info("Successfully connected to MongoDB for EngageCog.")
        except ConnectionFailure as e:
            logger.critical(f"Failed to connect to MongoDB: {e}")
            self.db_client = None # Явно отключаем, если соединение не удалось
        except Exception as e:
            logger.critical(f"An unexpected error occurred during MongoDB initialization: {e}")
            self.db_client = None

    def _is_db_connected(self) -> bool:
        """Проверяет, активно ли соединение с БД."""
        return self.db_client is not None and self.submissions_collection is not None

    def _get_all_submissions(self) -> List[Dict]:
        """Загружает все сабмишены из БД."""
        if not self._is_db_connected(): return []
        # Сортируем по времени добавления, от старых к новым
        return list(self.submissions_collection.find().sort("submitted_at", 1))

    def _delete_submission(self, twitter_link: str):
        """Удаляет сабмишен из БД по ссылке."""
        if not self._is_db_connected(): return
        try:
            self.submissions_collection.delete_one({"twitter_link": twitter_link})
        except OperationFailure as e:
            logger.error(f"Failed to delete submission {twitter_link} from MongoDB: {e}")

    async def process_submission(self, interaction: discord.Interaction, twitter_link: str):
        """Обрабатывает новый сабмишен и сохраняет в БД."""
        await interaction.response.defer(ephemeral=True)
        
        if not self._is_db_connected():
            await interaction.followup.send("⚠️ Database connection is not available. Please contact an admin.", ephemeral=True)
            return

        twitter_link = twitter_link.strip()
        if not twitter_link.startswith("https://x.com/"):
            await interaction.followup.send("⚠️ Only Twitter post links (https://x.com/...) are accepted.", ephemeral=True)
            return
        
        try:
            # Проверка на дубликат в БД
            if self.submissions_collection.find_one({"twitter_link": twitter_link}):
                await interaction.followup.send("⚠️ This link has already been submitted.", ephemeral=True)
                return

            # Создаем документ для вставки в БД
            submission_doc = {
                "discord_user_id": interaction.user.id,
                "discord_handle": str(interaction.user),
                "twitter_link": twitter_link,
                "submitted_at": discord.utils.utcnow()
            }
            
            # Вставляем документ
            self.submissions_collection.insert_one(submission_doc)
            logger.info(f"New submission from {interaction.user}: {twitter_link}")
            await interaction.followup.send("✅ Link submitted successfully!", ephemeral=True)

        except OperationFailure as e:
            logger.error(f"MongoDB operation failed for user {interaction.user}: {e}")
            await interaction.followup.send("⚙️ A database error occurred. Please try again later.", ephemeral=True)

    @app_commands.command(name="submit", description="Submit a Twitter post link for review.")
    async def submit_slash_command(self, interaction: discord.Interaction):
        # Проверка ролей
        user_role_ids = {role.id for role in interaction.user.roles}
        if not set(SUBMIT_ROLE_IDS).intersection(user_role_ids) and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ You do not have the required role to use this command.", ephemeral=True)
            return
            
        modal = SubmitModal(self)
        await interaction.response.send_modal(modal)

    @app_commands.command(name="submissions", description="View and manage submitted Twitter posts.")
    async def submissions_slash_command(self, interaction: discord.Interaction):
        # Проверка ролей
        user_role_ids = {role.id for role in interaction.user.roles}
        if not set(SUBMISSIONS_ROLE_IDS).intersection(user_role_ids) and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ You do not have the required role to use this command.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        
        if not self._is_db_connected():
            await interaction.followup.send("⚠️ Database connection is not available.", ephemeral=True)
            return

        all_submissions = self._get_all_submissions()
        view = SubmissionsView(self, all_submissions)
        
        # Отправляем первое сообщение
        embed = view._create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        view.message = message

async def setup(bot: commands.Bot):
    await bot.add_cog(EngageCog(bot))