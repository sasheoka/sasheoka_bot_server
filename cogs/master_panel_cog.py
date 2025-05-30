# cogs/master_panel_cog.py
import discord
from discord.ext import commands
from discord import app_commands 
import logging

from cogs.control_panel_cog import InfoPanelView
from cogs.stage_tracker_cog import StageTrackerView
from cogs.text_collector_cog import TextCollectorPanelView
from cogs.progress_transfer_cog import ProgressTransferPanelView
from cogs.balance_adjustment_cog import BalanceAdjustmentPanelView
from cogs.quest_visibility_cog import QuestIDModal # <-- Измененный импорт на QuestIDModal
from cogs.find_rule_id_cog import RuleNameInputModal

logger = logging.getLogger(__name__)

class MasterPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None) 
        self.bot = bot

    async def _check_ranger_role(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used on a server.",ephemeral=True,delete_after=10)
            return False
        ranger_role = discord.utils.get(interaction.guild.roles, name="Ranger")
        if not ranger_role:
            await interaction.response.send_message("⛔ The 'Ranger' role was not found on this server.",ephemeral=True,delete_after=10)
            return False
        if ranger_role not in interaction.user.roles:
            await interaction.response.send_message("⛔ You do not have the required 'Ranger' role to use this button.",ephemeral=True,delete_after=10)
            return False
        return True

    async def _send_specific_panel(self, interaction: discord.Interaction, cog_name: str, panel_title: str, panel_description: str, PanelViewClass: type[discord.ui.View], panel_color: discord.Color):
        cog_instance = self.bot.get_cog(cog_name)
        if not cog_instance:
            await interaction.followup.send(f"Panel '{panel_title}' is temporarily unavailable (module not loaded).",ephemeral=True)
            logger.error(f"Cog '{cog_name}' not found when trying to send panel from MasterPanel.")
            return
        embed = discord.Embed(title=panel_title, description=panel_description, color=panel_color)
        view_instance = PanelViewClass(cog_instance) 
        try:
            await interaction.followup.send(embed=embed, view=view_instance, ephemeral=True)
            logger.info(f"Sent ephemeral panel '{panel_title}' from MasterPanel by user {interaction.user.name}")
        except Exception as e:
            logger.error(f"Error sending ephemeral panel '{panel_title}' from MasterPanel: {e}", exc_info=True)
            try: 
                await interaction.followup.send(f"⚠️ Could not send panel '{panel_title}'. Please try again or contact an administrator.",ephemeral=True)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="ℹ️ Info Panel", style=discord.ButtonStyle.green, custom_id="masterpanel:info_v1", row=0)
    async def info_panel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=False) 
        await self._send_specific_panel(interaction, "Control Panel", "ℹ️ Snag Loyalty Info Panel", "Use the buttons below to query the Snag Loyalty System.", InfoPanelView, discord.Color.purple())

    @discord.ui.button(label="✈️ Progress Transfer (wip)", style=discord.ButtonStyle.danger, custom_id="masterpanel:transfer_v1", row=1)
    async def progress_transfer_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self._send_specific_panel(interaction, "Progress Transfer", "Wallet Progress Transfer Panel", "Initiate progress transfer. This is a sensitive operation, use with caution.", ProgressTransferPanelView, discord.Color.red())

    @discord.ui.button(label="📊 Balance Adjust", style=discord.ButtonStyle.secondary, custom_id="masterpanel:balance_v1", row=1)
    async def balance_adjustment_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self._send_specific_panel(interaction, "Balance Adjustments", "Wallet Balance Adjustment Panel", "Use the button below to manually adjust a user's Matchsticks balance.", BalanceAdjustmentPanelView, discord.Color.gold())
    
    @discord.ui.button(label="🎙️ Stage Tracker", style=discord.ButtonStyle.blurple, custom_id="masterpanel:stage_v1", row=2)
    async def stage_tracker_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self._send_specific_panel(interaction, "Stage Tracker", "Stage Channel Activity Monitoring Panel", "Use the buttons below to manage Stage channel monitoring.", StageTrackerView, discord.Color.blue())

    @discord.ui.button(label="📝 Text Collector", style=discord.ButtonStyle.grey, custom_id="masterpanel:text_v1", row=2)
    async def text_collector_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self._send_specific_panel(interaction, "Text Chat Collector", "Text Chat Address Collector Panel", "Click the button to specify parameters and start collecting from a text or voice chat.", TextCollectorPanelView, discord.Color.dark_teal())
    
    @discord.ui.button(label="👁️ Quest Visibility", style=discord.ButtonStyle.grey, custom_id="masterpanel:quest_visibility_v1", row=3) 
    async def quest_visibility_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction):
            return
        
        quest_visibility_cog_instance = self.bot.get_cog("Quest Visibility") 
        if not quest_visibility_cog_instance:
            await interaction.response.send_message(
                "Quest Visibility feature is temporarily unavailable (module not loaded).", 
                ephemeral=True
            )
            return # Возвращаемся, если ког не найден
        
        # Передаем interaction в модалку, чтобы она могла использовать followup
        modal = QuestIDModal(quest_visibility_cog_instance, interaction) 
        await interaction.response.send_modal(modal)
        
        # --- НОВАЯ КНОПКА ---
    @discord.ui.button(label="🔎 Find Quest ID", style=discord.ButtonStyle.primary, custom_id="masterpanel:find_quest_id_v1", row=3) # Например, в том же ряду или новом
    async def find_quest_id_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction):
            return
        
        find_rule_cog_instance = self.bot.get_cog("Find Quest ID") # Имя кога "Find Quest ID"
        if not find_rule_cog_instance:
            await interaction.response.send_message(
                "Find Quest ID feature is temporarily unavailable (module not loaded).", 
                ephemeral=True
            )
            logger.error("FindRuleIDCog not found when 'Find Quest ID' button was pressed in MasterPanel.")
            return
        
        modal = RuleNameInputModal(find_rule_cog_instance)
        await interaction.response.send_modal(modal)

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
            description="Select an administrative panel below. The 'Ranger' role is required to use these buttons.",
            color=discord.Color.dark_orange()
        )
        view = MasterPanelView(self.bot)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False) 
        logger.info(f"MasterPanel sent by user {interaction.user.name} via slash command.")

    @master_panel_slash_command.error
    async def master_panel_slash_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingAnyRole):
            if not interaction.response.is_done():
                await interaction.response.send_message("⛔ You do not have the required 'Ranger' role to use this command.",ephemeral=True)
            else:
                await interaction.followup.send("⛔ You do not have the required 'Ranger' role to use this command.",ephemeral=True)
        else:
            logger.error(f"Error in /masterpanel slash command: {error}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message("⚙️ An unexpected error occurred while executing the master panel command.",ephemeral=True)
            else:
                await interaction.followup.send("⚙️ An unexpected error occurred while executing the master panel command.",ephemeral=True)

async def setup(bot: commands.Bot):
    cog = MasterPanelCog(bot)
    await bot.add_cog(cog)