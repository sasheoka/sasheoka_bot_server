# cogs/find_rule_id_cog.py
import discord
from discord.ext import commands
import logging
from typing import Optional, Dict, Any, List
import asyncio 
import json # Для проверки json.loads(rule_data_field)

logger = logging.getLogger(__name__)

MAX_RULES_TO_DISPLAY = 15 # Max rules to show in one Discord message

# Конкретные ID для фильтрации
TARGET_ORGANIZATION_ID = "26a1764f-5637-425e-89fa-2f3fb86e758c"
TARGET_WEBSITE_ID = "32afc5c9-f0fb-4938-9572-775dee0b4a2b"
REQUIRED_LOYALTY_CURRENCY_ID = "7f74ae35-a6e2-496a-83ea-5b2e18769560"

class RuleNameInputModal(discord.ui.Modal, title="Find Quest ID by Name"):
    rule_name_substring_input = discord.ui.TextInput(
        label="Quest Name (or part of it)",
        placeholder="Enter keywords from the quest name...",
        required=True,
        style=discord.TextStyle.short,
        min_length=3 
    )

    def __init__(self, cog_instance: "FindRuleIDCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        name_substring = self.rule_name_substring_input.value.strip()

        if len(name_substring) < 3:
            await interaction.followup.send("⚠️ Please enter at least 3 characters to search.", ephemeral=True)
            return

        logger.info(f"User {interaction.user.name} ({interaction.user.id}) searching for quest ID with name containing: '{name_substring}' for org_id={TARGET_ORGANIZATION_ID}, site_id={TARGET_WEBSITE_ID}")
        await self.cog.find_and_display_rule_ids(interaction, name_substring)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in RuleNameInputModal: {error}", exc_info=True)
        if not interaction.response.is_done():
            try:
                await interaction.response.send_message("An error occurred in the modal. Please try again.", ephemeral=True)
            except discord.HTTPException:
                pass
        else:
            try:
                await interaction.followup.send("An error occurred after submitting the modal.", ephemeral=True)
            except discord.HTTPException:
                pass

class FindRuleIDCog(commands.Cog, name="Find Quest ID"):
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

    async def find_and_display_rule_ids(self, interaction: discord.Interaction, name_substring: str):
        if not self.snag_client or not self.snag_client._api_key:
            await interaction.followup.send("⚠️ Snag API client is not configured. Cannot perform search.", ephemeral=True)
            return

        all_matching_rules: List[Dict[str, Any]] = []
        has_more_pages = True
        starting_after: Optional[str] = None
        pages_fetched = 0
        max_pages_to_fetch = 10 
        
        original_message_for_edit: Optional[discord.WebhookMessage] = None
        try:
            original_message_for_edit = await interaction.original_response() 
            await original_message_for_edit.edit(content=f"⏳ Searching for quests containing '{name_substring}'...")
        except discord.NotFound:
            logger.warning(f"Could not fetch original response for interaction {interaction.id} to edit for search. Will send new followups.")
        except discord.HTTPException as e:
            logger.error(f"Failed to edit original response for search start: {e}. Will send new followups.")

        while has_more_pages and pages_fetched < max_pages_to_fetch:
            pages_fetched += 1
            logger.info(f"Fetching page {pages_fetched} of loyalty rules for name '{name_substring}' (org: {TARGET_ORGANIZATION_ID}, site: {TARGET_WEBSITE_ID}, after: {starting_after})")
            
            rules_response = await self.snag_client.get_loyalty_rules(
                limit=1000, 
                starting_after=starting_after,
                include_deleted=False,
                organization_id_filter=TARGET_ORGANIZATION_ID, 
                website_id_filter=TARGET_WEBSITE_ID           
            )

            if not rules_response or rules_response.get("error"):
                error_msg = rules_response.get("message", "Failed to fetch quest list.") if rules_response else "No API response."
                content_to_send = f"❌ Error fetching quest list: {error_msg}"
                if original_message_for_edit: await original_message_for_edit.edit(content=content_to_send, embed=None, view=None)
                else: await interaction.followup.send(content=content_to_send, ephemeral=True)
                return

            rules_on_page: List[Dict[str, Any]] = rules_response.get("data", [])
            if not rules_on_page:
                has_more_pages = False; break

            for rule in rules_on_page:
                if not (isinstance(rule, dict) and isinstance(rule.get("name"), str) and name_substring.lower() in rule["name"].lower()):
                    continue

                # --- ПРОВЕРКА ПОЛЯ "data" ---
                rule_data_field = rule.get("data")
                has_valid_and_non_empty_data = False
                if isinstance(rule_data_field, str) and rule_data_field.strip(): 
                    try:
                        parsed_data = json.loads(rule_data_field)
                        if isinstance(parsed_data, dict) and parsed_data: # Проверка, что это непустой словарь
                            has_valid_and_non_empty_data = True
                        else:
                            logger.debug(f"Rule ID {rule.get('id')} ('{rule.get('name')}'): 'data' field is valid JSON but resulted in an empty dict or non-dict. Parsed type: {type(parsed_data)}. Skipping.")
                    except json.JSONDecodeError:
                        logger.debug(f"Rule ID {rule.get('id')} ('{rule.get('name')}'): 'data' field is not valid JSON: {rule_data_field[:100]}... Skipping.")
                elif isinstance(rule_data_field, dict) and rule_data_field: # Если API вернул уже распарсенный непустой объект
                    has_valid_and_non_empty_data = True
                
                if not has_valid_and_non_empty_data:
                    logger.debug(f"Rule ID {rule.get('id')} ('{rule.get('name')}'): Skipped due to missing, empty, or invalid 'data' field.")
                    continue # Пропускаем это правило

                # --- ПРОВЕРКА НА loyaltyCurrencyId ---
                if rule.get("loyaltyCurrencyId") != REQUIRED_LOYALTY_CURRENCY_ID:
                    logger.debug(f"Rule ID {rule.get('id')} ('{rule.get('name')}'): Skipped due to loyaltyCurrencyId mismatch (is '{rule.get('loyaltyCurrencyId')}', expected '{REQUIRED_LOYALTY_CURRENCY_ID}').")
                    continue 
                
                all_matching_rules.append(rule)
            
            has_more_pages = rules_response.get("hasNextPage", False)
            if has_more_pages and rules_on_page:
                starting_after = rules_on_page[-1].get("id")
                if pages_fetched < max_pages_to_fetch : await asyncio.sleep(0.3) 
            else:
                has_more_pages = False
        
        if pages_fetched >= max_pages_to_fetch and has_more_pages:
            logger.warning(f"Reached max_pages_to_fetch ({max_pages_to_fetch}) for rule search '{name_substring}'. Results might be incomplete.")

        if not all_matching_rules:
            message_content = f"ℹ️ No quests found meeting all criteria (name containing '{name_substring}', valid 'data' field, and currency ID `...{REQUIRED_LOYALTY_CURRENCY_ID[-6:]}`) for the specified Organization/Website."
            if original_message_for_edit: await original_message_for_edit.edit(content=message_content, embed=None, view=None)
            else: await interaction.followup.send(content=message_content, ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Quests Found Matching '{name_substring}'",
            description=f"For Org ID: `...{TARGET_ORGANIZATION_ID[-8:]}` & Site ID: `...{TARGET_WEBSITE_ID[-8:]}`\n(Filtered by required currency ID & non-empty 'data' field)",
            color=discord.Color.blue()
        )
        
        description_lines = []
        for i, rule_item in enumerate(all_matching_rules):
            if i >= MAX_RULES_TO_DISPLAY:
                description_lines.append(f"\n...and {len(all_matching_rules) - MAX_RULES_TO_DISPLAY} more.")
                break
            rule_name = rule_item.get("name", "N/A")
            rule_id = rule_item.get("id", "N/A")
            visibility_status = "❓ Unknown" # Изменено на более короткое
            if "hideInUi" in rule_item: # Явная проверка наличия ключа
                visibility_status = "🙈 Hidden" if rule_item["hideInUi"] else "👁️ Visible"
            description_lines.append(f"**{i+1}. {rule_name}** ({visibility_status})\n   ID: `{rule_id}`")

        final_embed_description = embed.description or "" 
        if description_lines: final_embed_description += "\n\n" + "\n".join(description_lines)
        elif not all_matching_rules : 
             final_embed_description += "\n\nNo suitable quests found to display after all filters."
        embed.description = final_embed_description
        
        footer_text = f"Found {len(all_matching_rules)} matching quest(s) meeting all criteria."
        if len(all_matching_rules) > MAX_RULES_TO_DISPLAY or (pages_fetched >= max_pages_to_fetch and has_more_pages):
            footer_text += f" Displaying up to {MAX_RULES_TO_DISPLAY}."
        if pages_fetched >= max_pages_to_fetch and has_more_pages:
            footer_text += " Max pages fetched, list may be incomplete."
        embed.set_footer(text=footer_text)

        try:
            if original_message_for_edit: await original_message_for_edit.edit(content=None, embed=embed, view=None)
            else: await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.HTTPException as e:
            logger.error(f"Failed to edit original response for quest search results: {e}")
            await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    snag_api_client = getattr(bot, 'snag_client', None)
    if not snag_api_client or not getattr(snag_api_client, '_api_key', None):
        logger.critical("CRITICAL: FindRuleIDCog will NOT be loaded. Snag API client missing or no API key.")
        return
    if not hasattr(snag_api_client, 'get_loyalty_rules'):
        logger.critical("CRITICAL: FindRuleIDCog will NOT be loaded. SnagApiClient missing 'get_loyalty_rules' method.")
        return
        
    await bot.add_cog(FindRuleIDCog(bot))