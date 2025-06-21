# cogs/account_checker_cog.py
import discord
from discord.ext import commands
from discord import app_commands
import io
import re
from datetime import datetime, timedelta, timezone
import logging
import asyncio
from typing import Optional, List, Dict, Any, Tuple
from collections import defaultdict # Для группировки
from utils.checks import is_admin_in_guild # <--- ИМПОРТ

logger = logging.getLogger(__name__)

def parse_min_age_to_timedelta(age_str: Optional[str]) -> Optional[timedelta]:
    if not age_str:
        return None
    
    age_str_lower = age_str.lower()
    match = re.fullmatch(r"(\d+)([dmy])", age_str_lower)
    if not match:
        return None
    
    value = int(match.group(1))
    unit = match.group(2)
    
    if unit == 'd':
        return timedelta(days=value)
    elif unit == 'm':
        return timedelta(days=value * 30.4375) 
    elif unit == 'y':
        return timedelta(days=value * 365.2425)
    return None

class AccountCheckerCog(commands.Cog, name="Account Age Checker"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    async def cog_load(self):
        logger.info(f"Cog '{self.__class__.__name__}' successfully initialized by bot.")

    @app_commands.command(name="check_accounts", description="Checks account ages from a .txt file, groups by creation date.")
    @app_commands.describe(
        id_file="A .txt file containing user IDs.",
        min_age="Optional: Minimum account age (e.g., '30d', '6m', '1y').",
        group_threshold="Minimum users per creation date to highlight as a group (default: 2)."
    )
    @is_admin_in_guild() # <--- ИЗМЕНЕНИЕ
    async def check_accounts_slash_command(
        self, 
        interaction: discord.Interaction, 
        id_file: discord.Attachment,
        min_age: Optional[str] = None,
        group_threshold: int = 2
    ):
        # ... (код команды без изменений) ...
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not id_file.filename.lower().endswith(".txt"):
            await interaction.followup.send("⚠️ Invalid file type. Please upload a .txt file.", ephemeral=True)
            return
        
        if group_threshold < 1:
            group_threshold = 1 # Минимальный порог для группы

        min_age_delta: Optional[timedelta] = None
        min_age_display_str = "N/A"
        if min_age:
            min_age_delta = parse_min_age_to_timedelta(min_age)
            if min_age_delta is None:
                await interaction.followup.send(
                    f"⚠️ Invalid format for `min_age` ('{min_age}'). Use formats like '30d', '6m', '1y'.",
                    ephemeral=True
                )
                return
            min_age_display_str = f"{min_age} (approx. {min_age_delta.days} days)"

        try:
            file_content_bytes = await id_file.read()
            file_content_str = file_content_bytes.decode('utf-8', errors='replace')
        except Exception as e:
            logger.error(f"Error reading file {id_file.filename}: {e}", exc_info=True)
            await interaction.followup.send(f"⚠️ Error reading file: {e}", ephemeral=True)
            return

        potential_ids = re.findall(r"\b(\d{17,19})\b", file_content_str)
        
        if not potential_ids:
            await interaction.followup.send("ℹ️ No valid-looking User IDs found in the file.", ephemeral=True)
            return

        unique_ids_str = list(set(potential_ids))
        logger.info(f"User {interaction.user.name} ({interaction.user.id}) initiated account check. Found {len(unique_ids_str)} unique potential IDs in '{id_file.filename}'. Min age: {min_age_display_str}. Group threshold: {group_threshold}")

        fetched_users_data: List[Dict[str, Any]] = [] 
        not_found_ids_list: List[str] = []
        failed_to_fetch_ids_dict: Dict[str, str] = {}
        
        current_time_utc = datetime.now(timezone.utc)
        processed_count_in_loop = 0

        for user_id_str_val in unique_ids_str:
            processed_count_in_loop += 1
            if processed_count_in_loop % 20 == 0 or processed_count_in_loop == 1:
                logger.info(f"Processing ID {processed_count_in_loop}/{len(unique_ids_str)}: {user_id_str_val}")
                try: 
                    if not interaction.is_expired():
                         await interaction.edit_original_response(content=f"⏳ Processing IDs... {processed_count_in_loop}/{len(unique_ids_str)}")
                except discord.HTTPException:
                    logger.warning(f"Could not edit original interaction response for progress update (ID: {interaction.id}).")
                except Exception as e_edit:
                    logger.warning(f"Unexpected error editing original interaction for progress: {e_edit}")

            if interaction.is_expired():
                logger.warning(f"Interaction {interaction.id} expired during user fetching loop. Aborting further processing.")
                break 

            try:
                user_id_int_val = int(user_id_str_val)
                user = await self.bot.fetch_user(user_id_int_val) 
                if user:
                    created_at_utc = user.created_at 
                    account_age_delta = current_time_utc - created_at_utc
                    fetched_users_data.append({
                        "id_str": user_id_str_val, "user_obj": user, "name_tag": str(user), 
                        "created_at_dt": created_at_utc, "age_td": account_age_delta
                    })
            except discord.NotFound:
                not_found_ids_list.append(user_id_str_val)
            except discord.HTTPException as http_err:
                logger.warning(f"HTTP error fetching user ID {user_id_str_val}: {http_err}")
                failed_to_fetch_ids_dict[user_id_str_val] = f"HTTP Error {http_err.status}"
                if http_err.status == 429: 
                    retry_after = getattr(http_err, 'retry_after', 5.0) 
                    logger.warning(f"Rate limited. Retrying {user_id_str_val} after {retry_after:.2f}s")
                    await asyncio.sleep(retry_after)
                    try: # Retry logic
                        user_retry = await self.bot.fetch_user(user_id_int_val)
                        if user_retry:
                            created_at_utc_retry = user_retry.created_at 
                            account_age_delta_retry = current_time_utc - created_at_utc_retry
                            fetched_users_data.append({
                                "id_str": user_id_str_val, "user_obj": user_retry, "name_tag": str(user_retry), 
                                "created_at_dt": created_at_utc_retry, "age_td": account_age_delta_retry
                            })
                        else: not_found_ids_list.append(user_id_str_val)
                    except discord.NotFound: not_found_ids_list.append(user_id_str_val)
                    except Exception as e_retry:
                        logger.error(f"Error on retry {user_id_str_val}: {e_retry}")
                        failed_to_fetch_ids_dict[user_id_str_val] = f"Retry Error: {str(e_retry)[:50]}"
            except ValueError: failed_to_fetch_ids_dict[user_id_str_val] = "Invalid ID format."
            except Exception as e:
                 logger.error(f"Unexpected error fetching {user_id_str_val}: {e}", exc_info=True)
                 failed_to_fetch_ids_dict[user_id_str_val] = f"Unexpected: {str(e)[:50]}"
            
            await asyncio.sleep(0.35)

        if min_age_delta:
            users_to_process = [ud for ud in fetched_users_data if ud["age_td"] >= min_age_delta]
        else:
            users_to_process = fetched_users_data
        
        logger.info(f"Finished fetching. Total users fetched: {len(fetched_users_data)}. Users meeting age criteria (if any): {len(users_to_process)}. Not Found: {len(not_found_ids_list)}. Failed: {len(failed_to_fetch_ids_dict)}")

        if interaction.is_expired() and not users_to_process and not not_found_ids_list and not failed_to_fetch_ids_dict:
             logger.info(f"Processing aborted early for interaction {interaction.id}. No data to report.")
             return

        grouped_by_creation_date: Dict[datetime.date, List[Dict[str, Any]]] = defaultdict(list)
        for user_data_item in users_to_process:
            creation_date_only = user_data_item["created_at_dt"].date()
            grouped_by_creation_date[creation_date_only].append(user_data_item)

        sorted_grouped_dates = sorted(grouped_by_creation_date.keys())

        output_lines: List[str] = []
        output_lines.append(f"Account Age & Creation Date Grouping Report")
        output_lines.append(f"Source File: {id_file.filename} (Processed {len(unique_ids_str)} unique IDs from input)")
        output_lines.append(f"Report Generated: {current_time_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        output_lines.append(f"Minimum Age Criterion: {min_age_display_str}")
        output_lines.append(f"Highlight Groups with >= {group_threshold} accounts created on the same day.")
        output_lines.append("-" * 60)

        total_matching_users_in_report = 0

        if not sorted_grouped_dates:
            output_lines.append("\nNo accounts matched the criteria or no accounts were successfully fetched.")
        else:
            for creation_date in sorted_grouped_dates:
                users_on_this_date = grouped_by_creation_date[creation_date]
                users_on_this_date.sort(key=lambda u: u["created_at_dt"])
                
                date_str_formatted = creation_date.strftime('%Y-%m-%d')
                is_highlighted_group = len(users_on_this_date) >= group_threshold
                
                group_header = f"\n--- Creation Date: {date_str_formatted} ({len(users_on_this_date)} account(s))"
                if is_highlighted_group:
                    group_header += " [POTENTIAL SIBYL GROUP]"
                output_lines.append(group_header)
                output_lines.append("-" * len(group_header.strip()))


                for user_data_item in users_on_this_date:
                    total_matching_users_in_report +=1
                    age_days_val = user_data_item['age_td'].days
                    age_display_str = f"{age_days_val} days"
                    if age_days_val >= 365.2425:
                        years = int(age_days_val / 365.2425)
                        remaining_days_after_years = age_days_val % 365.2425
                        months = int(remaining_days_after_years / 30.4375)
                        age_display_str = f"{years}y {months}m ({age_days_val}d)"
                    elif age_days_val >= 30.4375:
                        months = int(age_days_val / 30.4375)
                        days = int(age_days_val % 30.4375)
                        age_display_str = f"{months}m {days}d ({age_days_val}d)"
                    
                    output_lines.append(
                        f"  User: {user_data_item['name_tag']} (ID: {user_data_item['user_obj'].id})\n"
                        f"    Created At (UTC): {user_data_item['created_at_dt'].strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"    Current Age: {age_display_str}"
                    )
        
        output_lines.append("-" * 60)
        output_lines.append(f"\nTotal accounts listed in report (after filters): {total_matching_users_in_report}")

        if not_found_ids_list:
            output_lines.append(f"\n--- User IDs Not Found During Discord API Fetch ({len(not_found_ids_list)}) ---")
            for i, user_id_str_val in enumerate(not_found_ids_list):
                output_lines.append(f"ID: {user_id_str_val}")
                if i > 50 and len(not_found_ids_list) > 55:
                    output_lines.append(f"...and {len(not_found_ids_list) - 50 -1} more not found IDs.")
                    break

        if failed_to_fetch_ids_dict:
            output_lines.append(f"\n--- Failed to Fetch Some User IDs ({len(failed_to_fetch_ids_dict)}) ---")
            count_failed = 0
            for user_id_str_val, error_msg_val in failed_to_fetch_ids_dict.items():
                output_lines.append(f"ID: {user_id_str_val} - Error: {error_msg_val}")
                count_failed += 1
                if count_failed > 50 and len(failed_to_fetch_ids_dict) > 55:
                    output_lines.append(f"...and {len(failed_to_fetch_ids_dict) - 50 -1} more failed fetches.")
                    break

        output_content_str = "\n".join(output_lines)
        
        if not output_content_str.strip():
            output_content_str = "No data to report after processing."

        file_to_send = discord.File(io.BytesIO(output_content_str.encode('utf-8')), filename="account_check_grouped_results.txt")
        
        try:
            if not interaction.is_expired():
                await interaction.followup.send(
                    f"✅ Account check complete. Found {total_matching_users_in_report} matching accounts. Results grouped by creation date in the attached file:", 
                    file=file_to_send, 
                    ephemeral=True
                )
            else:
                 logger.warning(f"Interaction {interaction.id} expired before sending final results. File was not sent.")
        except discord.HTTPException as e:
            logger.error(f"Failed to send followup with file for account check: {e}", exc_info=True)
            try:
                if not interaction.is_expired():
                    await interaction.followup.send("⚠️ Failed to send the results file due to an error. Please check logs or try a smaller ID list.", ephemeral=True)
            except discord.HTTPException:
                logger.error("Also failed to send error message as followup.")

    @check_accounts_slash_command.error
    async def check_accounts_slash_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        logger.error(f"Error in /check_accounts command by {interaction.user.name}: {error}", exc_info=True)
        # --- НАЧАЛО ИЗМЕНЕНИЙ В ОБРАБОТЧИКЕ ОШИБОК ---
        if isinstance(error, app_commands.NoPrivateMessage):
            error_message_to_user = "⛔ This command can only be used on the official server."
        elif isinstance(error, app_commands.CheckFailure):
            error_message_to_user = "⛔ This command is not available on this server."
        elif isinstance(error, app_commands.MissingRole):
            error_message_to_user = f"⛔ You do not have the required '{error.missing_role}' role to use this command."
        # --- КОНЕЦ ИЗМЕНЕНИЙ В ОБРАБОТЧИКЕ ОШИБОК ---
        elif isinstance(error, app_commands.CommandInvokeError):
            original_error = error.original
            if isinstance(original_error, discord.Forbidden):
                 error_message_to_user = "⚠️ The bot lacks permissions. This could be to read attachments, fetch user data, or send followup messages. Please check bot permissions."
            else:
                error_message_to_user = f"⚙️ Command execution error: {str(original_error)[:200]}"
        else:
             error_message_to_user = "⚙️ An unexpected error occurred with the `/check_accounts` command."
        
        try:
            if interaction.response.is_done(): 
                await interaction.followup.send(error_message_to_user, ephemeral=True)
            else: 
                await interaction.response.send_message(error_message_to_user, ephemeral=True)
        except discord.HTTPException as e_http:
            logger.error(f"Failed to send error response for /check_accounts to {interaction.user.name}: {e_http}")

async def setup(bot: commands.Bot):
    await bot.add_cog(AccountCheckerCog(bot))
    logger.info("AccountCheckerCog setup complete.")