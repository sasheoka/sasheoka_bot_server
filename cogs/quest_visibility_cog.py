# cogs/quest_visibility_cog.py
import discord
from discord.ext import commands
import logging
from typing import Optional, Dict, Any, List
# from decimal import Decimal, InvalidOperation # Убрали, так как amount пробуем не передавать или передавать как есть
import json 
import datetime

logger = logging.getLogger(__name__)

class QuestIDModal(discord.ui.Modal, title="Enter Quest ID"):
    rule_id_input = discord.ui.TextInput(
        label="Loyalty Rule ID (Quest ID)",
        placeholder="Enter the ID of the quest...",
        required=True, style=discord.TextStyle.short, min_length=5, max_length=50,
        row=0
    )
    def __init__(self, cog_instance: "QuestVisibilityCog", original_interaction: discord.Interaction):
        super().__init__(timeout=None)
        self.cog = cog_instance
        self.original_interaction = original_interaction

    async def on_submit(self, modal_interaction: discord.Interaction):
        rule_id = self.rule_id_input.value.strip()
        if not rule_id:
            await modal_interaction.response.send_message("⚠️ Quest ID cannot be empty.", ephemeral=True, delete_after=10)
            return
        try:
            await modal_interaction.response.send_message(f"Processing Quest ID: `{rule_id}`...", ephemeral=True, delete_after=3)
        except discord.NotFound:
            logger.warning(f"Could not send initial response to modal_interaction for rule_id {rule_id}.")
        
        view = ConfirmVisibilityActionView(self.cog, rule_id, self.original_interaction)
        try:
            message_with_buttons = await self.original_interaction.followup.send(
                f"Choose visibility for Quest ID: `{rule_id}`", 
                view=view, 
                ephemeral=True
            )
            view.message = message_with_buttons
        except discord.HTTPException as e:
            logger.error(f"Failed to send ConfirmVisibilityActionView for rule {rule_id} via original_interaction.followup: {e}")
            try:
                if self.original_interaction.channel:
                    message_with_buttons = await self.original_interaction.channel.send(
                        f"{self.original_interaction.user.mention} Choose visibility for Quest ID: `{rule_id}`",
                        view=view, delete_after=view.timeout # Используем таймаут View
                    )
                    view.message = message_with_buttons
                    await self.original_interaction.followup.send("Action buttons sent as a new message.",ephemeral=True, delete_after=10)
                else:
                     await self.original_interaction.followup.send("⚠️ Error: Could not display action buttons (channel unavailable).",ephemeral=True)
            except Exception as e2:
                 logger.error(f"Fallback send to channel also failed for rule {rule_id}: {e2}")
                 await self.original_interaction.followup.send("⚠️ Error: Could not display action buttons.",ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in QuestIDModal: {error}", exc_info=True)
        if not interaction.response.is_done():
            try: await interaction.response.send_message("An error occurred in the modal. Please try again.", ephemeral=True)
            except discord.HTTPException: pass
        else:
            try: await interaction.followup.send("An error occurred after submitting the modal.", ephemeral=True)
            except discord.HTTPException: pass

class ConfirmVisibilityActionView(discord.ui.View):
    def __init__(self, cog_instance: "QuestVisibilityCog", rule_id: str, original_interaction: discord.Interaction):
        super().__init__(timeout=180.0) # Явно указываем float для таймаута
        self.cog = cog_instance
        self.rule_id = rule_id
        self.original_interaction = original_interaction 
        self.message: Optional[discord.Message] = None 

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(content=f"Quest visibility action for ID `{self.rule_id}` timed out. Buttons removed.", view=None)
            except discord.HTTPException: 
                logger.warning(f"Could not edit message on timeout for rule {self.rule_id}")
        try:
            await self.original_interaction.followup.send(
                f"Action for quest ID `{self.rule_id}` timed out. Please try again if needed.", 
                ephemeral=True
            )
        except discord.HTTPException:
            logger.warning(f"Could not send timeout followup for original_interaction on rule {self.rule_id}")
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message("You are not allowed to use these buttons.", ephemeral=True, delete_after=10)
            return False
        return True

    async def _disable_all_buttons(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                logger.warning("Could not edit message to disable buttons.")
        self.stop()

    async def _handle_action(self, button_interaction: discord.Interaction, hide_flag: bool):
        try:
            # Редактируем сообщение с кнопками, чтобы убрать их и показать, что действие принято
            await button_interaction.response.edit_message(content=f"Processing visibility change for Quest ID `{self.rule_id}`...", view=None)
        except discord.HTTPException as e:
            logger.warning(f"Could not edit message for button_interaction: {e}. This might leave 'thinking' state if defer was used before.")
            # Если edit_message не удался, и предыдущий ответ был defer(thinking=True), то "is thinking" останется.
            # Если предыдущего defer не было (как сейчас), то это просто означает, что сообщение с кнопками не обновилось.

        action_gerund = "hiding" if hide_flag else "showing"
        action_verb_past = "hidden" if hide_flag else "shown"
        
        await self.cog.toggle_quest_visibility_action(self.original_interaction, self.rule_id, hide_flag, action_gerund, action_verb_past)
        
        # View свою работу выполнил. Кнопки уже убраны через edit_message выше.
        self.stop()
    
    @discord.ui.button(label="🙈 Hide from UI", style=discord.ButtonStyle.danger, custom_id="quest_vis:hide")
    async def hide_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        logger.info(f"User {interaction.user.name} chose to HIDE quest ID: {self.rule_id}")
        if self.message is None and interaction.message is not None : self.message = interaction.message
        await self._handle_action(interaction, True)

    @discord.ui.button(label="👁️ Show in UI", style=discord.ButtonStyle.success, custom_id="quest_vis:show")
    async def show_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        logger.info(f"User {interaction.user.name} chose to SHOW quest ID: {self.rule_id}")
        if self.message is None and interaction.message is not None: self.message = interaction.message
        await self._handle_action(interaction, False)

class QuestVisibilityCog(commands.Cog, name="Quest Visibility"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snag_client = getattr(bot, 'snag_client', None)
        if not self.snag_client:
            logger.error(f"{self.__class__.__name__}: SnagApiClient (bot.snag_client) not found! Functionality will be disabled.")
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    async def cog_load(self):
        logger.info(f"Cog '{self.__class__.__name__}' successfully initialized by bot.")
        if not self.snag_client or not self.snag_client._api_key:
             logger.error(f"{self.__class__.__name__}: Snag API client is unavailable or API key is missing. Functionality will be disabled.")

    async def toggle_quest_visibility_action(self, original_interaction: discord.Interaction, rule_id_to_modify: str, hide_ui_flag: bool, action_gerund: str, action_verb_past_tense: str):
        current_rule_details_response = await self.snag_client.get_loyalty_rule_details(rule_id_to_modify)

        if not current_rule_details_response or current_rule_details_response.get("error"):
            status = current_rule_details_response.get("status", "N/A") if current_rule_details_response else "N/A"
            message = current_rule_details_response.get("message", "No API response or error fetching rule details.") if current_rule_details_response else "No API response."
            await original_interaction.followup.send(f"❌ Failed to retrieve quest details for ID `{rule_id_to_modify}`. Status: {status}. Error: {message}", ephemeral=True)
            return
        
        current_rule_details = current_rule_details_response
        payload_to_update: Dict[str, Any] = {}

        payload_to_update["name"] = current_rule_details.get("name")
        if payload_to_update["name"] is None:
            await original_interaction.followup.send(f"❌ Critical error: 'name' is missing for quest `{rule_id_to_modify}`. Update aborted.", ephemeral=True)
            return

        api_endTime_str = current_rule_details.get("endTime")
        if api_endTime_str is None:
            payload_to_update["endTime"] = "9999-12-31T23:59:00Z" 
        elif isinstance(api_endTime_str, str):
            try:
                if api_endTime_str.endswith('Z'): dt_obj_naive = datetime.datetime.fromisoformat(api_endTime_str[:-1])
                else: dt_obj_naive = datetime.datetime.fromisoformat(api_endTime_str)
                if dt_obj_naive.tzinfo is None: dt_obj_utc = dt_obj_naive.replace(tzinfo=datetime.timezone.utc)
                else: dt_obj_utc = dt_obj_naive.astimezone(datetime.timezone.utc)
                dt_obj_rounded = dt_obj_utc.replace(second=0, microsecond=0)
                payload_to_update["endTime"] = dt_obj_rounded.strftime('%Y-%m-%dT%H:%M:00Z')
            except ValueError:
                payload_to_update["endTime"] = "9999-12-31T23:59:00Z"
        else:
            payload_to_update["endTime"] = "9999-12-31T23:59:00Z"
            
        # --- ИЗМЕНЕНИЕ ДЛЯ AMOUNT: НЕ ПЕРЕДАЕМ ЕГО, ЕСЛИ НЕ МЕНЯЕМ ---
        # Поле 'amount' помечено как 'required' в документации для POST (обновления).
        # Однако, ошибка "Invalid input" сильнее.
        # Если API требует его даже если оно не меняется, он должен принять то, что ему пришло в GET.
        # Попробуем передавать 'amount' ТОЧНО ТАКИМ, каким оно пришло из GET-запроса.
        if "amount" in current_rule_details: # Только если поле вообще существует в ответе GET
            payload_to_update["amount"] = current_rule_details["amount"] 
            logger.info(f"Rule {rule_id_to_modify}: Carrying over 'amount': '{payload_to_update['amount']}' (type: {type(payload_to_update['amount'])}).")
        else:
            # Если amount вообще не пришел в GET, а он required для POST, это проблема.
            # Но мы должны сначала попытаться с тем, что есть.
            logger.warning(f"Rule {rule_id_to_modify}: 'amount' field not found in GET response. API might require it for update.")
            # payload_to_update["amount"] = 0 # Или None, если это допустимо по "Amount · null"
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
            
        payload_to_update["hideInUi"] = hide_ui_flag

        optional_fields_to_carry_over = [
            "description", "startTime", "effectiveStartTime", "effectiveEndTime",
            "customRewardsCsvUrl", "customRewardsApiUrl", "subscriptionIdentifier",
            "metadata", "network", "collectionAddress", "collections", "isRequired",
            "oauthCredentialsId", "rewardType", "frequency", "interval",
            "loyaltyRuleGroupId", "mediaUrl", "loyaltyCurrencyId", "type", "data"
        ]
        for field_key in optional_fields_to_carry_over:
            if field_key in current_rule_details: # Копируем только если поле существует в ответе GET
                # Не перезаписываем amount, если он уже установлен или мы его специально обработали
                if field_key == "amount" and "amount" in payload_to_update:
                    continue 
                payload_to_update[field_key] = current_rule_details[field_key]
        
        if payload_to_update.get("amount") is None: # Если amount в итоге null или отсутствует
            if "loyaltyCurrencyId" in payload_to_update and payload_to_update.get("loyaltyCurrencyId") is not None:
                payload_to_update["loyaltyCurrencyId"] = None 

        logger.debug(f"Payload for updating rule {rule_id_to_modify}: {json.dumps(payload_to_update, indent=2)}")

        try:
            update_response = await self.snag_client.update_loyalty_rule(rule_id_to_modify, payload_to_update)
            
            if update_response and not update_response.get("error"):
                updated_rule_data = update_response.get("data", update_response) 
                if isinstance(updated_rule_data, dict) and updated_rule_data.get("hideInUi") == hide_ui_flag and updated_rule_data.get("id") == rule_id_to_modify:
                    await original_interaction.followup.send(f"✅ Quest ID `{rule_id_to_modify}` successfully marked as {action_verb_past_tense} in UI.", ephemeral=True)
                elif update_response.get("message"):
                     await original_interaction.followup.send(f"✅ Request for {action_gerund} quest `{rule_id_to_modify}` sent. API Response: {update_response.get('message')}", ephemeral=True)
                else:
                    await original_interaction.followup.send(f"✅ Request for {action_gerund} quest `{rule_id_to_modify}` sent successfully. API Response: `{str(update_response)[:500]}`", ephemeral=True)
            elif update_response and update_response.get("error"):
                status = update_response.get("status", "N/A")
                message = update_response.get("message", "No additional error information.")
                raw_resp_part = update_response.get("raw_response", "")[:300]
                error_msg_for_user = f"❌ Failed to {action_verb_past_tense} quest `{rule_id_to_modify}`."
                if isinstance(status, int) and status == 400 and "issues" in str(raw_resp_part).lower():
                    error_msg_for_user += f" API reported invalid input. Details: `{raw_resp_part}...` Check logs and payload."
                else:
                    error_msg_for_user += f" Status: {status}. Error: {message}. API Raw: `{raw_resp_part}...`"
                await original_interaction.followup.send(error_msg_for_user, ephemeral=True)
            else:
                await original_interaction.followup.send(f"❌ Failed to {action_verb_past_tense} quest `{rule_id_to_modify}`. Unexpected or no response from API. Check logs.", ephemeral=True)
        except Exception as e:
            logger.exception(f"Exception while trying to {action_verb_past_tense} quest {rule_id_to_modify}:")
            await original_interaction.followup.send(f"⚙️ An unexpected error occurred: `{str(e)[:1000]}`", ephemeral=True)

async def setup(bot: commands.Bot):
    snag_api_client = getattr(bot, 'snag_client', None)
    if not snag_api_client or not getattr(snag_api_client, '_api_key', None):
        logger.critical("CRITICAL: Main Snag API client (or its API key) is missing. QuestVisibilityCog will NOT be loaded.")
        return
    if not hasattr(snag_api_client, 'update_loyalty_rule') or not hasattr(snag_api_client, 'get_loyalty_rules'):
        logger.critical("CRITICAL: SnagApiClient is missing 'update_loyalty_rule' or 'get_loyalty_rules' method. QuestVisibilityCog will NOT be loaded.")
        return
    await bot.add_cog(QuestVisibilityCog(bot))