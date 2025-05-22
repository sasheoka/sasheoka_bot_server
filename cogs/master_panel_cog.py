# cogs/master_panel_cog.py
import discord
from discord.ext import commands
from discord import app_commands 
import logging
import os 

from cogs.control_panel_cog import InfoPanelView, ControlPanelCog
from cogs.stage_tracker_cog import StageTrackerView, StageTrackerCog
from cogs.text_collector_cog import TextCollectorPanelView, TextCollectorCog
from cogs.progress_transfer_cog import ProgressTransferPanelView, ProgressTransferCog
from cogs.balance_adjustment_cog import BalanceAdjustmentPanelView, BalanceAdjustmentCog

logger = logging.getLogger(__name__)

class MasterPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None) 
        self.bot = bot

    async def _check_ranger_role(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used on a server.", ephemeral=True, delete_after=10)
            return False
        ranger_role = discord.utils.get(interaction.guild.roles, name="Ranger")
        if not ranger_role:
            await interaction.response.send_message("⛔ The 'Ranger' role was not found on this server.", ephemeral=True, delete_after=10)
            return False
        if ranger_role not in interaction.user.roles:
            await interaction.response.send_message("⛔ You do not have the required 'Ranger' role to use this button.", ephemeral=True, delete_after=10)
            return False
        return True

    async def _send_specific_panel(self, interaction: discord.Interaction, cog_name: str, panel_title: str, panel_description: str, panel_view_class, panel_color: discord.Color):
        cog_instance = self.bot.get_cog(cog_name)
        if not cog_instance:
            await interaction.followup.send(f"Panel '{panel_title}' is currently unavailable (cog not loaded).", ephemeral=True)
            logger.error(f"Cog '{cog_name}' not found when trying to send panel from MasterPanel.")
            return

        embed = discord.Embed(title=panel_title, description=panel_description, color=panel_color)
        view_instance = panel_view_class(cog_instance) 
        
        try:
            await interaction.followup.send(embed=embed, view=view_instance, ephemeral=True)
            logger.info(f"Sent ephemeral '{panel_title}' from MasterPanel by {interaction.user.name}")
        except Exception as e:
            logger.error(f"Error sending ephemeral '{panel_title}' from MasterPanel: {e}", exc_info=True)
            try: 
                await interaction.followup.send(f"⚠️ Could not send the '{panel_title}'. Please try again or contact an admin.", ephemeral=True)
            except: pass


    @discord.ui.button(label="ℹ️ Info Panel", style=discord.ButtonStyle.green, custom_id="masterpanel:info_v1", row=0)
    async def info_panel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=False) 
        await self._send_specific_panel(
            interaction, "Control Panel", "ℹ️ Snag Loyalty Info Panel",
            "Use the buttons below to query Snag Loyalty System.",
            InfoPanelView, discord.Color.purple()
        )

    @discord.ui.button(label="🎙️ Stage Tracker", style=discord.ButtonStyle.blurple, custom_id="masterpanel:stage_v1", row=2)
    async def stage_tracker_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self._send_specific_panel(
            interaction, "Stage Tracker", "Stage Channel Activity Monitoring Panel",
            "Use the buttons below to manage Stage channel monitoring.",
            StageTrackerView, discord.Color.blue()
        )

    @discord.ui.button(label="📝 Text Collector", style=discord.ButtonStyle.grey, custom_id="masterpanel:text_v1", row=2)
    async def text_collector_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self._send_specific_panel(
            interaction, "Text Chat Collector", "Text Chat Address Collector Panel",
            "Click the button to specify parameters and start collecting from a text or voice chat.",
            TextCollectorPanelView, discord.Color.dark_teal()
        )
    
    @discord.ui.button(label="✈️ Progress Transfer", style=discord.ButtonStyle.danger, custom_id="masterpanel:transfer_v1", row=1)
    async def progress_transfer_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self._send_specific_panel(
            interaction, "Progress Transfer", "Wallet Progress Transfer Panel",
            "Initiate progress transfer. Sensitive operation, use with caution.",
            ProgressTransferPanelView, discord.Color.red()
        )

    @discord.ui.button(label="📊 Balance Adjust", style=discord.ButtonStyle.secondary, custom_id="masterpanel:balance_v1", row=1)
    async def balance_adjustment_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self._send_specific_panel(
            interaction, "Balance Adjustments", "Wallet Balance Adjustment Panel",
            "Use the button below to manually adjust a user's Matchsticks balance.",
            BalanceAdjustmentPanelView, discord.Color.gold()
        )

class MasterPanelCog(commands.Cog, name="Master Panel"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    async def cog_load(self):
        self.bot.add_view(MasterPanelView(self.bot)) 
        logger.info(f"Cog '{self.__class__.__name__}' successfully initialized by bot and MasterPanelView registered.")

    @app_commands.command(name="masterpanel", description="Shows the master control panel for bot functions.")
    @app_commands.checks.has_any_role("Ranger") 
    async def master_panel_slash_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🛠️ Master Control Panel 🛠️",
            description="Select an administrative panel below. You need the 'Ranger' role to use these buttons.",
            color=discord.Color.dark_orange()
        )
        view = MasterPanelView(self.bot)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False) 
        logger.info(f"MasterPanel sent by {interaction.user.name} via slash command.")

    @master_panel_slash_command.error
    async def master_panel_slash_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingAnyRole):
            await interaction.response.send_message("⛔ You do not have the required 'Ranger' role to use this command.", ephemeral=True)
        else:
            logger.error(f"Error in /masterpanel slash command: {error}", exc_info=True)
            # Проверяем, был ли уже отправлен ответ, прежде чем пытаться отправить новый
            if not interaction.response.is_done():
                await interaction.response.send_message("⚙️ An unexpected error occurred with the master panel command.", ephemeral=True)
            else:
                await interaction.followup.send("⚙️ An unexpected error occurred with the master panel command.", ephemeral=True)


async def setup(bot: commands.Bot):
    cog = MasterPanelCog(bot)
    await bot.add_cog(cog)
    # Регистрация View происходит в cog_load, чтобы убедиться, что bot уже полностью доступен