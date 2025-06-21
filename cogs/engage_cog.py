import discord
from discord.ext import commands
import logging
import asyncio
import os
import re
from typing import List, Optional

logger = logging.getLogger(__name__)

# --- Constants ---
SUBMIT_ROLE_IDS = [
    1253785633621540895,  # Camp Guide ID
    11614978609745879483   # Shortcut ID
]
SUBMISSIONS_ROLE_IDS = [
    1161497860974587947    # Shortcut ID
]
ENGAGE_CHANNEL_ID = 1384466812766257193
# CHANGED: Place submissions.txt in the parent directory of cogs/ (same as bot.py)
SUBMISSIONS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "submissions.txt")
URL_PATTERN = re.compile(r"^https?://[^\s/$.?#].*$")

class SubmissionsView(discord.ui.View):
    """
    Постоянный View, который работает напрямую с состоянием кога.
    """
    def __init__(self, cog_instance: "EngageCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance
        self.update_buttons()

    def update_buttons(self):
        """Обновляет состояние кнопок, используя данные из кога."""
        logger.debug("View: Updating button states.")
        submissions = self.cog.submissions
        current_index = self.cog.current_index_for_view

        self.prev_button.disabled = current_index == 0
        self.next_button.disabled = current_index >= len(submissions) - 1 if submissions else True
        self.delete_button.disabled = not submissions
        self.post_button.disabled = not submissions
        logger.debug(f"View: Buttons updated. Prev disabled: {self.prev_button.disabled}, Next disabled: {self.next_button.disabled}")

    async def _update_embed_and_buttons(self, interaction: discord.Interaction):
        """Централизованный метод для обновления сообщения и кнопок."""
        logger.debug("View: Preparing to update embed and buttons.")
        self.update_buttons()
        await self.cog.update_submission_embed(interaction, self)
        logger.debug("View: Embed and buttons update initiated.")

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, custom_id="engage_persistent:prev_v2")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        logger.debug(f"View: 'Previous' button clicked by {interaction.user.name}.")
        if self.cog.current_index_for_view > 0:
            self.cog.current_index_for_view -= 1
            await self._update_embed_and_buttons(interaction)
        else:
            await interaction.response.defer() # Молчаливый ответ, если действие невозможно

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, custom_id="engage_persistent:next_v2")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        logger.debug(f"View: 'Next' button clicked by {interaction.user.name}.")
        if self.cog.current_index_for_view < len(self.cog.submissions) - 1:
            self.cog.current_index_for_view += 1
            await self._update_embed_and_buttons(interaction)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="engage_persistent:delete_v2")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        logger.debug(f"View: 'Delete' button clicked by {interaction.user.name}.")
        if not self.cog.submissions:
            await interaction.response.send_message("No submissions to delete.", ephemeral=True)
            return

        deleted_link = self.cog.submissions.pop(self.cog.current_index_for_view)
        logger.info(f"Deleted submission '{deleted_link}' from memory by {interaction.user.name}.")
        await self.cog.save_submissions()

        if self.cog.current_index_for_view >= len(self.cog.submissions) and self.cog.submissions:
            self.cog.current_index_for_view = len(self.cog.submissions) - 1

        await self._update_embed_and_buttons(interaction)
        await interaction.followup.send(f"✅ Submission deleted: `{deleted_link}`", ephemeral=True)

    @discord.ui.button(label="Post to Engage", style=discord.ButtonStyle.primary, custom_id="engage_persistent:post_v2")
    async def post_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        logger.debug(f"View: 'Post' button clicked by {interaction.user.name}.")
        if not self.cog.submissions:
            await interaction.response.send_message("No submissions to post.", ephemeral=True)
            return
            
        link_to_post = self.cog.submissions[self.cog.current_index_for_view]
        success = await self.cog.post_to_engage_channel(interaction, link_to_post)

        if success:
            logger.info(f"Successfully posted '{link_to_post}'. Now removing it from submissions.")
            self.cog.submissions.pop(self.cog.current_index_for_view)
            await self.cog.save_submissions()

            if self.cog.current_index_for_view >= len(self.cog.submissions) and self.cog.submissions:
                self.cog.current_index_for_view = len(self.cog.submissions) - 1
            
            await self._update_embed_and_buttons(interaction)


class EngageCog(commands.Cog, name="Engage"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.submissions: List[str] = []
        self.current_index_for_view: int = 0
        self._lock = asyncio.Lock()
        self.load_submissions()
        # Регистрируем View как постоянную. Это нужно делать один раз.
        self.bot.add_view(SubmissionsView(self))
        logger.info(f"Cog '{self.__class__.__name__}' loaded and persistent view registered.")

    def _save_submissions_sync(self):
        """Синхронная версия сохранения файла."""
        logger.debug(f"SYNC SAVE: Attempting to save {len(self.submissions)} submissions to {SUBMISSIONS_FILE}")
        try:
            # NEW: Create parent directory if it doesn't exist
            os.makedirs(os.path.dirname(SUBMISSIONS_FILE), exist_ok=True)
            with open(SUBMISSIONS_FILE, 'w', encoding='utf-8') as f:
                for link in self.submissions:
                    logger.debug(f"SYNC SAVE: Writing link: {link}")
                    f.write(f"{link}\n")
            logger.info(f"SYNC SAVE: Successfully saved {len(self.submissions)} submissions.")
        except Exception as e:
            logger.error(f"SYNC SAVE: Critical error while writing to {SUBMISSIONS_FILE}: {e}", exc_info=True)
            raise  # NEW: Temporarily re-raise for debugging

    def cog_unload(self):
        """Гарантированное сохранение при выключении бота."""
        logger.info("EngageCog is unloading, triggering final save...")
        self._save_submissions_sync()

    def load_submissions(self):
        """Загрузка данных из файла при старте."""
        # NEW: Log file path details
        logger.info(f"Current working directory: {os.getcwd()}")
        logger.info(f"Resolved path for SUBMISSIONS_FILE: {os.path.abspath(SUBMISSIONS_FILE)}")
        try:
            if os.path.exists(SUBMISSIONS_FILE):
                with open(SUBMISSIONS_FILE, 'r', encoding='utf-8') as f:
                    # Фильтруем пустые строки и невалидные URL
                    self.submissions = [line.strip() for line in f if line.strip() and URL_PATTERN.match(line.strip())]
                logger.info(f"Loaded {len(self.submissions)} valid submissions from file.")
            else:
                logger.info("Submissions file not found. Starting with an empty list.")
        except Exception as e:
            logger.error(f"Failed to load submissions: {e}", exc_info=True)

    async def save_submissions(self):
        """Асинхронный враппер для сохранения."""
        async with self._lock:
            self._save_submissions_sync()

    async def update_submission_embed(self, interaction: discord.Interaction, view: SubmissionsView):
        """Обновляет исходное сообщение с embed."""
        if not interaction.response.is_done():
            await interaction.response.defer()

        index = self.current_index_for_view
        if not self.submissions:
            embed = discord.Embed(title="Submissions Queue", description="No submissions available.", color=discord.Color.red())
        else:
            embed = discord.Embed(
                title="Submissions Queue",
                description=f"**Link {index + 1}/{len(self.submissions)}**\n{self.submissions[index]}",
                color=discord.Color.blue()
            )
            embed.set_footer(text=f"Submission {index + 1} of {len(self.submissions)}")
        
        embed.timestamp = discord.utils.utcnow()
        await interaction.edit_original_response(embed=embed, view=view)

    async def post_to_engage_channel(self, interaction: discord.Interaction, link: str) -> bool:
        """Постит ссылку в канал. Возвращает True в случае успеха."""
        channel = self.bot.get_channel(ENGAGE_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            logger.error(f"Engage channel (ID: {ENGAGE_CHANNEL_ID}) is not found or is not a TextChannel.")
            await interaction.followup.send("⚠️ Engage channel is misconfigured.", ephemeral=True)
            return False
        try:
            await channel.send(f"New submission from {interaction.user.mention}: {link}")
            await interaction.followup.send(f"✅ Posted to <#{ENGAGE_CHANNEL_ID}>.", ephemeral=True)
            return True
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Failed to post to engage channel {ENGAGE_CHANNEL_ID}: {e}", exc_info=True)
            await interaction.followup.send("⚠️ Failed to post to the engage channel. Check bot permissions.", ephemeral=True)
            return False

    @commands.command(name="submit")
    @commands.has_any_role(*SUBMIT_ROLE_IDS)
    async def submit_command(self, ctx: commands.Context, *, link: str):
        """Submit a link to be stored."""
        # NEW: Enhanced logging
        logger.info(f"!submit invoked by {ctx.author.name} (ID: {ctx.author.id}) with link: {link}")
        # Log user roles to verify role check
        if isinstance(ctx.author, discord.Member):
            roles = [r.id for r in ctx.author.roles]
            logger.debug(f"User roles: {roles}, Required roles: {SUBMIT_ROLE_IDS}")
        else:
            logger.warning(f"User {ctx.author.name} is not a Member (not in guild context)")
            await ctx.send("⚠️ This command can only be used in a server.")
            return

        try:
            # Validate URL
            logger.debug(f"Validating link: {link}")
            if not URL_PATTERN.match(link):
                await ctx.send("⚠️ Invalid link format. Please provide a valid URL.")
                return

            async with self._lock:
                # Check for duplicates
                logger.debug(f"Checking for duplicate link: {link}")
                if link in self.submissions:
                    await ctx.send("⚠️ This link has already been submitted.")
                    return

                # Append link
                logger.debug(f"Appending link: {link}")
                self.submissions.append(link)
                logger.info(f"Added link to memory. Total submissions now: {len(self.submissions)}")

                # Save to file
                logger.debug(f"Calling save_submissions for link: {link}")
                await self.save_submissions()

                # Send success message
                await ctx.send(f"✅ Link submitted! Total submissions in queue: {len(self.submissions)}")
                logger.info(f"Link submitted successfully by {ctx.author.name}: {link}")

        except Exception as e:
            # NEW: Log and notify user of unexpected errors
            logger.error(f"Unexpected error in !submit for {ctx.author.name} with link {link}: {e}", exc_info=True)
            await ctx.send("⚠️ An unexpected error occurred while processing your submission. Please contact an admin.")

    @submit_command.error
    async def submit_command_error(self, ctx: commands.Context, error: commands.CommandError):
        # NEW: Enhanced error logging
        logger.error(f"Error in !submit for {ctx.author.name}: {error}", exc_info=True)
        if isinstance(error, commands.MissingAnyRole):
            logger.warning(f"User {ctx.author.name} failed role check for '!submit'. Required roles: {SUBMIT_ROLE_IDS}")
            await ctx.send("⛔ You lack the required role to submit links.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("⚠️ Please provide a link. Usage: `!submit <link>`")
        else:
            await ctx.send("⚙️ An unexpected error occurred.")
            logger.error(f"Unhandled error in submit_command: {error}", exc_info=True)

    @commands.command(name="submissions")
    @commands.has_any_role(*SUBMISSIONS_ROLE_IDS)
    async def submissions_command(self, ctx: commands.Context):
        """Display submissions in an embed with navigation buttons."""
        # NEW: Log command invocation
        logger.info(f"!submissions invoked by {ctx.author.name} (ID: {ctx.author.id})")
        # Log user roles
        if isinstance(ctx.author, discord.Member):
            roles = [r.id for r in ctx.author.roles]
            logger.debug(f"User roles: {roles}, Required roles: {SUBMISSIONS_ROLE_IDS}")
        
        await ctx.defer(ephemeral=True)
        
        async with self._lock:
            view = SubmissionsView(self)
            
            if self.submissions:
                self.current_index_for_view = len(self.submissions) - 1
            else:
                self.current_index_for_view = 0
            
            index = self.current_index_for_view
            if not self.submissions:
                embed = discord.Embed(title="Submissions Queue", description="No submissions available.", color=discord.Color.red())
            else:
                embed = discord.Embed(
                    title="Submissions Queue",
                    description=f"**Link {index + 1}/{len(self.submissions)}**\n{self.submissions[index]}",
                    color=discord.Color.blue()
                )
                embed.set_footer(text=f"Submission {index + 1} of {len(self.submissions)}")

            embed.timestamp = discord.utils.utcnow()
            await ctx.followup.send(embed=embed, view=view)
            logger.info(f"Sent submissions panel to {ctx.author.name}.")

    @submissions_command.error
    async def submissions_command_error(self, ctx: commands.Context, error: commands.CommandError):
        # NEW: Enhanced error logging
        logger.error(f"Error in !submissions for {ctx.author.name}: {error}", exc_info=True)
        if isinstance(error, commands.MissingAnyRole):
            logger.warning(f"User {ctx.author.name} failed role check for '!submissions'. Required roles: {SUBMISSIONS_ROLE_IDS}")
            await ctx.send("⛔ You lack the required role to view submissions.", ephemeral=True)
        else:
            await ctx.send("⚙️ An unexpected error occurred.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(EngageCog(bot))
    logger.info("EngageCog loaded successfully.")