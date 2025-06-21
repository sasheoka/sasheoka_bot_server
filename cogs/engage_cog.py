import discord
from discord.ext import commands
import json
import os
import logging
from typing import Optional, List
import re

# --- Constants ---
SUBMIT_ROLE_IDS = [
    1253785633621540895,  # Camp Guide ID
    11614978609745879483   # Camp Network Staff ID
]
SUBMISSIONS_ROLE_IDS = [
    1161497860974587947,    # Senior Camp Guide ID
    11614978609745879483   # Camp Network Staff ID
]
ENGAGE_CHANNEL_ID = 1384466812766257193

logger = logging.getLogger(__name__)

def check_submit_roles(interaction: discord.Interaction) -> bool:
    """Check if the user has one of the submit roles or is an admin."""
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        roles = [role.id for role in interaction.user.roles] if isinstance(interaction.user, discord.Member) else []
        return any(role_id in roles for role_id in SUBMIT_ROLE_IDS)
    return True

def check_submissions_roles(interaction: discord.Interaction) -> bool:
    """Check if the user has one of the submissions roles or is an admin."""
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        roles = [role.id for role in interaction.user.roles] if isinstance(interaction.user, discord.Member) else []
        return any(role_id in roles for role_id in SUBMISSIONS_ROLE_IDS)
    return True

class SubmitModal(discord.ui.Modal, title="Submit Twitter Post"):
    twitter_link = discord.ui.TextInput(
        label="Twitter Post Link",
        placeholder="Enter Twitter post URL (e.g., https://x.com/username/status/123456789)",
        required=True,
        style=discord.TextStyle.short,
        max_length=200
    )

    def __init__(self, cog_instance: "EngageCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        discord_handle = f"{interaction.user.name}#{interaction.user.discriminator}"
        twitter_link = self.twitter_link.value.strip()
        await self.cog.process_submission(interaction, discord_handle, twitter_link)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in SubmitModal: {error}", exc_info=True)
        await interaction.followup.send("An error occurred in the modal.", ephemeral=True)

class SubmissionsView(discord.ui.View):
    def __init__(self, cog_instance: "EngageCog", interaction: discord.Interaction, submissions: List[dict]):
        super().__init__(timeout=None)
        self.cog = cog_instance
        self.interaction = interaction
        self.submissions = submissions
        self.current_index = 0
        self.message: Optional[discord.Message] = None
        self.update_buttons()

    def update_buttons(self):
        self.children[0].disabled = self.current_index == 0  # Previous button
        self.children[1].disabled = self.current_index >= len(self.submissions) - 1  # Next button
        self.children[2].disabled = not self.submissions  # Delete button
        self.children[3].disabled = not self.submissions  # Post to Engage button

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.primary, custom_id="submissions:previous")
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_index > 0:
            self.current_index -= 1
            await self.update_message(interaction)
        self.update_buttons()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, custom_id="submissions:next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_index < len(self.submissions) - 1:
            self.current_index += 1
            await self.update_message(interaction)
        self.update_buttons()

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="submissions:delete")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.submissions:
            deleted_submission = self.submissions.pop(self.current_index)
            self.cog._save_submissions()
            if self.current_index >= len(self.submissions):
                self.current_index = max(0, len(self.submissions) - 1)
            await self.update_message(interaction)
            logger.info(f"Deleted submission: {deleted_submission['twitter_link']} by {deleted_submission['discord_handle']}")
        self.update_buttons()

    @discord.ui.button(label="Post to Engage", style=discord.ButtonStyle.success, custom_id="submissions:post")
    async def post_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.submissions:
            channel = self.cog.bot.get_channel(ENGAGE_CHANNEL_ID)
            if channel and isinstance(channel, discord.TextChannel):
                await channel.send(f"<{self.submissions[self.current_index]['twitter_link']}>")
                await interaction.response.send_message("✅ Posted to Engage channel!", ephemeral=True)
                logger.info(f"Posted {self.submissions[self.current_index]['twitter_link']} to channel {ENGAGE_CHANNEL_ID}")
            else:
                await interaction.response.send_message("⚠️ Engage channel not found or inaccessible.", ephemeral=True)
                logger.error(f"Failed to post to ENGAGE_CHANNEL_ID {ENGAGE_CHANNEL_ID}")

    async def update_message(self, interaction: discord.Interaction):
        if self.submissions:
            submission = self.submissions[self.current_index]
            embed = discord.Embed(
                title="Current Submission",
                description=f"**Discord Handle:** {submission['discord_handle']}\n**Twitter Link:** {submission['twitter_link']}",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            embed.set_footer(text=f"Submission {self.current_index + 1} of {len(self.submissions)}")
            if self.message:
                await self.message.edit(embed=embed, view=self)
            else:
                self.message = await interaction.followup.send(embed=embed, view=self, ephemeral=True)
        else:
            embed = discord.Embed(
                title="No Submissions",
                description="No submissions available.",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            if self.message:
                await self.message.edit(embed=embed, view=self)
            else:
                self.message = await interaction.followup.send(embed=embed, view=self, ephemeral=True)

class EngageCog(commands.Cog, name="Engage"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.submissions_file = "data/engage/submissions.json"
        os.makedirs(os.path.dirname(self.submissions_file), exist_ok=True)
        self._load_submissions()

    def _load_submissions(self):
        if os.path.exists(self.submissions_file):
            try:
                with open(self.submissions_file, 'r', encoding='utf-8') as f:
                    self.submissions_data = json.load(f)
            except json.JSONDecodeError:
                logger.warning(f"Corrupted {self.submissions_file}. Initializing with empty list.")
                self.submissions_data = []
        else:
            self.submissions_data = []

    def _save_submissions(self):
        try:
            with open(self.submissions_file, 'w', encoding='utf-8') as f:
                json.dump(self.submissions_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"Failed to save submissions to {self.submissions_file}: {e}", exc_info=True)

    async def process_submission(self, interaction: discord.Interaction, discord_handle: str, twitter_link: str):
        if not twitter_link.startswith("https://x.com/"):
            await interaction.followup.send("⚠️ Only Twitter post links (https://x.com/...) are accepted.", ephemeral=True)
            return

        if any(sub["twitter_link"] == twitter_link for sub in self.submissions_data):
            await interaction.followup.send("⚠️ This link has already been submitted.", ephemeral=True)
            return

        submission = {"discord_handle": discord_handle, "twitter_link": twitter_link}
        self.submissions_data.append(submission)
        self._save_submissions()
        logger.info(f"New submission from {discord_handle}: {twitter_link}")
        await interaction.followup.send("✅ Link submitted successfully!", ephemeral=True)

    @discord.app_commands.command(name="submit", description="Submit a Twitter post link for review.")
    @discord.app_commands.check(check_submit_roles)
    async def submit_slash_command(self, interaction: discord.Interaction):
        modal = SubmitModal(self)
        await interaction.response.send_modal(modal)

    @submit_slash_command.error
    async def submit_slash_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.CheckFailure):
            await interaction.response.send_message("⛔ You do not have the required role to use this command.", ephemeral=True)
        else:
            logger.error(f"Error in /submit command by {interaction.user.name}: {error}", exc_info=True)
            await interaction.response.send_message("⚙️ An unexpected error occurred.", ephemeral=True)

    @discord.app_commands.command(name="submissions", description="View and manage submitted Twitter posts.")
    @discord.app_commands.check(check_submissions_roles)
    async def submissions_slash_command(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        view = SubmissionsView(self, interaction, self.submissions_data)
        await view.update_message(interaction)

    @submissions_slash_command.error
    async def submissions_slash_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.CheckFailure):
            await interaction.response.send_message("⛔ You do not have the required role to use this command.", ephemeral=True)
        else:
            logger.error(f"Error in /submissions command by {interaction.user.name}: {error}", exc_info=True)
            await interaction.response.send_message("⚙️ An unexpected error occurred.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(EngageCog(bot))