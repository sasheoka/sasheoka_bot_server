# cogs/master_panel_cog.py
import discord
from discord.ext import commands
from discord import app_commands 
import logging
import os

# Импорты для всех панелей и модальных окон
from cogs.control_panel_cog import InfoPanelView
from cogs.stage_tracker_cog import StageTrackerView
from cogs.text_collector_cog import TextCollectorPanelView
from cogs.progress_transfer_cog import ProgressTransferPanelView
from cogs.balance_adjustment_cog import BalanceAdjustmentPanelView
from cogs.quest_visibility_cog import QuestIDModal
from cogs.find_rule_id_cog import RuleNameInputModal
from cogs.quest_completer_cog import QuestCompleteModal

from utils.checks import is_admin_in_guild
from utils.checks import is_admin_in_guild, ADMIN_GUILD_ID, RANGER_ROLE_ID # <--- ВАЖНО: импортируем переменные

logger = logging.getLogger(__name__)

class MasterPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None) 
        self.bot = bot
        self.cog_instance: "MasterPanelCog" = bot.get_cog("Master Panel")

    # ... (все остальные кнопки остаются без изменений) ...

    @discord.ui.button(label="ℹ️ Info Panel", style=discord.ButtonStyle.green, custom_id="masterpanel:info_v5", row=0)
    async def info_panel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.cog_instance or not await self.cog_instance.check_admin_permissions(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=False) 
        await self.cog_instance._send_specific_panel(interaction, "Control Panel", "ℹ️ Snag Loyalty Info Panel", "Use the buttons below to query the Snag Loyalty System.", InfoPanelView, discord.Color.purple())

    @discord.ui.button(label="✈️ Progress Transfer", style=discord.ButtonStyle.danger, custom_id="masterpanel:transfer_v4", row=1)
    async def progress_transfer_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.cog_instance or not await self.cog_instance.check_admin_permissions(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.cog_instance._send_specific_panel(interaction, "Progress Transfer", "Wallet Progress Transfer Panel", "Initiate progress transfer. This is a sensitive operation, use with caution.", ProgressTransferPanelView, discord.Color.red())

    @discord.ui.button(label="📊 Balance Adjust", style=discord.ButtonStyle.secondary, custom_id="masterpanel:balance_v4", row=1)
    async def balance_adjustment_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.cog_instance or not await self.cog_instance.check_admin_permissions(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.cog_instance._send_specific_panel(interaction, "Balance Adjustments", "Wallet Balance Adjustment Panel", "Use the button below to manually adjust a user's Matchsticks balance.", BalanceAdjustmentPanelView, discord.Color.gold())
    
    @discord.ui.button(label="🎙️ Stage Tracker", style=discord.ButtonStyle.blurple, custom_id="masterpanel:stage_v4", row=2)
    async def stage_tracker_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.cog_instance or not await self.cog_instance.check_admin_permissions(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.cog_instance._send_specific_panel(interaction, "Stage Tracker", "Stage Channel Activity Monitoring Panel", "Use the buttons below to manage Stage channel monitoring.", StageTrackerView, discord.Color.blue())

    @discord.ui.button(label="📝 Text Collector", style=discord.ButtonStyle.grey, custom_id="masterpanel:text_v4", row=2)
    async def text_collector_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.cog_instance or not await self.cog_instance.check_admin_permissions(interaction): return
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.cog_instance._send_specific_panel(interaction, "Text Chat Collector", "Text Chat Address Collector Panel", "Click the button to specify parameters and start collecting from a text or voice chat.", TextCollectorPanelView, discord.Color.dark_teal())
    
    @discord.ui.button(label="👁️ Quest Visibility", style=discord.ButtonStyle.grey, custom_id="masterpanel:quest_visibility_v4", row=3) 
    async def quest_visibility_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.cog_instance or not await self.cog_instance.check_admin_permissions(interaction): return
        target_cog = self.bot.get_cog("Quest Visibility") 
        if not target_cog:
            await interaction.response.send_message("Quest Visibility feature is temporarily unavailable.", ephemeral=True)
            return
        modal = QuestIDModal(target_cog, interaction) 
        await interaction.response.send_modal(modal)
        
    @discord.ui.button(label="🔎 Find Quest ID", style=discord.ButtonStyle.primary, custom_id="masterpanel:find_quest_id_v4", row=3)
    async def find_quest_id_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.cog_instance or not await self.cog_instance.check_admin_permissions(interaction): return
        target_cog = self.bot.get_cog("Find Quest ID")
        if not target_cog:
            await interaction.response.send_message("Find Quest ID feature is temporarily unavailable.", ephemeral=True)
            return
        modal = RuleNameInputModal(target_cog)
        await interaction.response.send_modal(modal)

    # --- ИСПРАВЛЕННАЯ КНОПКА ---
    @discord.ui.button(label="✅ Complete Quest", style=discord.ButtonStyle.danger, custom_id="masterpanel:complete_quest_v2", row=4)
    async def complete_quest_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.cog_instance or not await self.cog_instance.check_admin_permissions(interaction): return
        
        # Правильное название кога, как в файле quest_completer_cog.py
        target_cog = self.bot.get_cog("Quest Completer") 
        
        if not target_cog:
            # Правильное сообщение об ошибке, которое вы видели
            await interaction.response.send_message("Quest Completer feature is temporarily unavailable.", ephemeral=True)
            return
        
        # Правильное модальное окно из quest_completer_cog.py
        modal = QuestCompleteModal(target_cog) 
        await interaction.response.send_modal(modal)


class MasterPanelCog(commands.Cog, name="Master Panel"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    async def _send_specific_panel(self, interaction: discord.Interaction, cog_name: str, panel_title: str, panel_description: str, PanelViewClass: type[discord.ui.View], panel_color: discord.Color):
        cog_instance = self.bot.get_cog(cog_name)
        if not cog_instance:
            await interaction.followup.send(f"Panel '{panel_title}' is temporarily unavailable (module not loaded).",ephemeral=True)
            logger.error(f"Cog '{cog_name}' not found when trying to send panel from MasterPanel.")
            return
        embed = discord.Embed(title=panel_title, description=panel_description, color=panel_color)
        view_instance = PanelViewClass(cog_instance) 
        await interaction.followup.send(embed=embed, view=view_instance, ephemeral=True)
        logger.info(f"Sent ephemeral panel '{panel_title}' from MasterPanel by user {interaction.user.name}")

    async def check_admin_permissions(self, interaction: discord.Interaction) -> bool:
        """
        Проверяет права для кнопок на панели.
        Использует переменные, импортированные из checks.py
        """
        if not interaction.guild:
            if not interaction.response.is_done():
                await interaction.response.send_message("⛔ This action can only be used on a server.", ephemeral=True)
            return False

        # 1. Проверка ID сервера
        if interaction.guild.id != ADMIN_GUILD_ID:
            if not interaction.response.is_done():
                await interaction.response.send_message("⛔ This command is not available on this server.", ephemeral=True)
            return False

        if not isinstance(interaction.user, discord.Member):
            return False

        # 2. Проверка ID роли
        if not any(role.id == RANGER_ROLE_ID for role in interaction.user.roles):
            if not interaction.response.is_done():
                # Сообщение об отсутствии роли
                role_name_or_id = f"ID: {RANGER_ROLE_ID}"
                try:
                    role_obj = interaction.guild.get_role(RANGER_ROLE_ID)
                    if role_obj:
                        role_name_or_id = f"'{role_obj.name}'"
                except:
                    pass
                await interaction.response.send_message(f"⛔ You do not have the required role: {role_name_or_id}", ephemeral=True)
            return False
            
        return True

    async def cog_load(self):
        self.bot.add_view(MasterPanelView(self.bot)) 
        logger.info(f"Cog '{self.__class__.__name__}' successfully initialized and MasterPanelView registered.")

    @app_commands.command(name="masterpanel", description="Shows the master control panel for bot functions.")
    @is_admin_in_guild()
    async def master_panel_slash_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🛠️ Master Control Panel 🛠️",
            description="Select an administrative panel below. Only authorized users on the official server can use these buttons.",
            color=discord.Color.dark_orange()
        )
        view = MasterPanelView(self.bot)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False) 
        logger.info(f"MasterPanel sent by user {interaction.user.name} via slash command.")

    @master_panel_slash_command.error
    async def master_panel_slash_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.NoPrivateMessage):
            msg = "⛔ This command can only be used on the official server."
        elif isinstance(error, app_commands.CheckFailure):
            msg = f"⛔ This command is not available on this server."
        elif isinstance(error, app_commands.MissingRole):
            msg = f"⛔ You do not have the required role: '{error.missing_role}'."
        else:
            logger.error(f"Error in /masterpanel slash command: {error}", exc_info=True)
            msg = "⚙️ An unexpected error occurred."
        
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
        except discord.HTTPException:
            pass

async def setup(bot: commands.Bot):
    await bot.add_cog(MasterPanelCog(bot))