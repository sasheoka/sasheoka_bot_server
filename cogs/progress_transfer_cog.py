# cogs/progress_transfer_cog.py
import discord
from discord.ext import commands
import logging
import datetime
import os
import asyncio
import io
from typing import Dict, Set, Optional, List, Tuple, Union, Any 
from decimal import Decimal, InvalidOperation 

logger = logging.getLogger(__name__)

MATCHSTICKS_CURRENCY_ID = os.getenv("MATCHSTICKS_CURRENCY_ID", "7f74ae35-a6e2-496a-83ea-5b2e18769560") 

# --- Modal Window for Wallet Addresses ---
class WalletTransferModal(discord.ui.Modal, title="Initiate Wallet Progress Transfer"):
    old_wallet_input = discord.ui.TextInput(
        label="Old (Compromised) Wallet Address",
        placeholder="0x...",
        required=True, style=discord.TextStyle.short, min_length=42, max_length=42
    )
    new_wallet_input = discord.ui.TextInput(
        label="New Wallet Address",
        placeholder="0x...",
        required=True, style=discord.TextStyle.short, min_length=42, max_length=42
    )

    def __init__(self, cog_instance: "ProgressTransferCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        old_wallet = self.old_wallet_input.value.strip().lower()
        new_wallet = self.new_wallet_input.value.strip().lower()

        if not (old_wallet.startswith("0x") and len(old_wallet) == 42 and \
                new_wallet.startswith("0x") and len(new_wallet) == 42):
            await interaction.followup.send("⚠️ Invalid wallet address format. Both must be 42 chars, start with '0x'.", ephemeral=True)
            return
        if old_wallet == new_wallet:
            await interaction.followup.send("⚠️ Old and new wallet addresses cannot be the same.", ephemeral=True)
            return
        await self.cog.gather_pre_transfer_info_and_confirm(interaction, old_wallet, new_wallet)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in WalletTransferModal: {error}", exc_info=True)
        try:
            if interaction.response.is_done(): await interaction.followup.send("An error occurred in the modal.", ephemeral=True)
            else: await interaction.response.send_message("An error occurred in the modal.", ephemeral=True)
        except discord.HTTPException: pass

# --- View for Transfer Confirmation ---
class ConfirmTransferView(discord.ui.View):
    def __init__(self, cog_instance: "ProgressTransferCog", old_wallet: str, new_wallet: str, pre_transfer_data: Dict[str, Any]):
        super().__init__(timeout=300) 
        self.cog = cog_instance
        self.old_wallet = old_wallet
        self.new_wallet = new_wallet
        self.pre_transfer_data = pre_transfer_data
        self.confirmed: Optional[bool] = None
        self.interaction_user_id: Optional[int] = None 
        self.message_to_edit: Optional[discord.Message] = None 

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        self.interaction_user_id = interaction.user.id 
        ranger_role = discord.utils.get(interaction.guild.roles, name="Ranger")
        if not ranger_role or ranger_role not in interaction.user.roles:
            await interaction.response.send_message("⛔ You do not have the 'Ranger' role to confirm/cancel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Confirm Transfer", style=discord.ButtonStyle.danger, custom_id="transfer:confirm_v5")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content="⏳ Initiating transfer process... please wait for the final report.", view=self) 
        self.stop()

    @discord.ui.button(label="❌ Cancel Transfer", style=discord.ButtonStyle.secondary, custom_id="transfer:cancel_v5")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content="ℹ️ Transfer cancelled by user.", view=self)
        self.stop()

    async def on_timeout(self):
        if self.confirmed is None: 
            logger.info(f"ConfirmTransferView timed out for old:{self.old_wallet}, new:{self.new_wallet}")
            if self.message_to_edit: 
                try:
                    for item in self.children: item.disabled = True 
                    await self.message_to_edit.edit(content="ℹ️ Transfer confirmation timed out. Please initiate again if needed.", view=self)
                except discord.HTTPException:
                    logger.warning(f"Could not edit timed out confirmation message for old:{self.old_wallet}")

# --- Panel View to Initiate Transfer ---
class ProgressTransferPanelView(discord.ui.View):
    def __init__(self, cog_instance: "ProgressTransferCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def _check_ranger_role(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
             await interaction.response.send_message("This command is server-only.", ephemeral=True); return False
        ranger_role = discord.utils.get(interaction.guild.roles, name="Ranger")
        if not ranger_role:
            await interaction.response.send_message("⛔ 'Ranger' role not found on this server.", ephemeral=True); return False
        if ranger_role not in interaction.user.roles:
            await interaction.response.send_message("⛔ You lack the 'Ranger' role to use this button.", ephemeral=True); return False
        return True

    @discord.ui.button(label="🚀 Initiate Wallet Progress Transfer", style=discord.ButtonStyle.primary, custom_id="ptransfer:initiate_v5")
    async def initiate_transfer_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction): return
        modal = WalletTransferModal(self.cog)
        try:
            await interaction.response.send_modal(modal)
        except discord.errors.NotFound as e:
            if e.code == 10062: 
                logger.error(f"Failed to send modal for interaction {interaction.id}: Unknown interaction.", exc_info=True)
                try: await interaction.followup.send("⚠️ Couldn't open form. Try sending panel command again for a fresh panel.", ephemeral=True)
                except discord.HTTPException: logger.error(f"Failed to send followup for unknown interaction {interaction.id}.")
            else:
                logger.error(f"NotFound error sending modal: {e}", exc_info=True)
                try:
                    if not interaction.response.is_done(): await interaction.response.send_message("⚠️ Error opening form.", ephemeral=True)
                    else: await interaction.followup.send("⚠️ Error opening form.", ephemeral=True)
                except discord.HTTPException: pass 
        except Exception as e:
            logger.error(f"Generic error sending modal: {e}", exc_info=True)
            try:
                if not interaction.response.is_done(): await interaction.response.send_message("⚠️ Error. Try again.", ephemeral=True)
                else: await interaction.followup.send("⚠️ Error. Try again.", ephemeral=True)
            except discord.HTTPException: pass

# --- Cog Class ---
class ProgressTransferCog(commands.Cog, name="Progress Transfer"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snag_client = getattr(bot, 'snag_client', None)
        self.processed_transfers: Dict[str, datetime.datetime] = {} 
        self._transfer_lock = asyncio.Lock()
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    async def cog_load(self):
        logger.info(f"Cog '{self.__class__.__name__}' successfully initialized by bot.")
        if not self.snag_client or not self.snag_client._api_key:
            logger.error(f"{self.__class__.__name__}: Main Snag API client is not available/API key missing. Transfer features will fail.")
        if not hasattr(self.snag_client, 'complete_loyalty_rule'):
             logger.error(f"{self.__class__.__name__}: SnagApiClient is missing 'complete_loyalty_rule' method. This is CRUCIAL for correct quest transfer.")


    async def _get_wallet_balance(self, wallet_address: str, currency_id: str) -> Decimal:
        # ... (без изменений, как в предыдущем ответе) ...
        if not self.snag_client: return Decimal('0')
        try:
            accounts_data = await self.snag_client.get_all_accounts_for_wallet(wallet_address, limit=100)
            if accounts_data and isinstance(accounts_data.get("data"), list):
                for acc in accounts_data["data"]:
                    if acc.get("loyaltyCurrencyId") == currency_id and acc.get("amount") is not None:
                        return Decimal(str(acc["amount"]))
            return Decimal('0')
        except Exception as e:
            logger.error(f"Error fetching balance for {wallet_address}, currency {currency_id}: {e}", exc_info=True)
            return Decimal('0')

    async def _get_quest_transactions_info(self, wallet_address: str, currency_id_filter: str) -> Tuple[Decimal, List[Dict[str, Any]]]:
        # ... (без изменений, как в предыдущем ответе) ...
        if not self.snag_client: return Decimal('0'), []
        total_quest_points = Decimal('0')
        quest_transactions_details: List[Dict[str, Any]] = []
        starting_after = None; has_more = True; page_count = 0; max_pages = 20
        while has_more and page_count < max_pages:
            page_count +=1
            try:
                page_data = await self.snag_client.get_transaction_entries(
                    wallet_address=wallet_address, limit=100, starting_after=starting_after
                )
                if not page_data or not isinstance(page_data.get("data"), list): has_more = False; break
                transactions_on_page = page_data["data"]
                if not transactions_on_page: has_more = False; break
                for tx in transactions_on_page:
                    if tx.get("direction") != "credit": continue
                    if tx.get("loyaltyCurrencyId") != currency_id_filter: continue
                    rule_info = tx.get("loyaltyTransaction", {}).get("loyaltyRule", {})
                    if not isinstance(rule_info, dict) or not rule_info.get("id") or not rule_info.get("name"): continue
                    try:
                        amount = Decimal(str(tx.get("amount", "0")))
                        if amount <= Decimal('0'): continue
                        total_quest_points += amount
                        quest_transactions_details.append({
                            "ruleId": rule_info["id"], "amount": amount, "currencyId": currency_id_filter,
                            "originalTxId": tx.get("id", "N/A"), "description": rule_info["name"].strip()
                        })
                    except (InvalidOperation, TypeError): logger.warning(f"Invalid amount in tx {tx.get('id')} for {wallet_address}")
                has_more = page_data.get("hasNextPage", False)
                if has_more and transactions_on_page: starting_after = transactions_on_page[-1].get("id")
                else: has_more = False
                if has_more: await asyncio.sleep(0.4)
            except Exception as e:
                logger.error(f"Error fetching tx entries for {wallet_address} (page {page_count}): {e}", exc_info=True)
                has_more = False
        logger.info(f"For {wallet_address}, found {len(quest_transactions_details)} quest txns totaling {total_quest_points} {currency_id_filter} pts.")
        return total_quest_points, quest_transactions_details

    async def gather_pre_transfer_info_and_confirm(self, interaction: discord.Interaction, old_wallet: str, new_wallet: str):
        # ... (без изменений, как в предыдущем ответе) ...
        if not self.snag_client or not self.snag_client._api_key:
            await interaction.followup.send("⚠️ Main Snag API client not configured. Cannot proceed.", ephemeral=True); return
        msg_to_edit = await interaction.followup.send(f"⏳ Gathering data for old wallet `{old_wallet}`...", ephemeral=True)
        old_wallet_total_balance = await self._get_wallet_balance(old_wallet, MATCHSTICKS_CURRENCY_ID)
        old_wallet_quest_points, old_wallet_quest_txs = await self._get_quest_transactions_info(old_wallet, MATCHSTICKS_CURRENCY_ID)
        pre_transfer_data = {
            "old_wallet_total_balance": old_wallet_total_balance,
            "old_wallet_quest_points": old_wallet_quest_points,
            "old_wallet_quest_txs_details": old_wallet_quest_txs,
        }
        embed = discord.Embed(title="🚨 Confirm Progress Transfer 🚨", color=discord.Color.orange(),
            description=(
                f"Please review and confirm the transfer.\n**This action is irreversible!**\n\n"
                f"**From (Old):** `{old_wallet}`\n**To (New):** `{new_wallet}`"
            ))
        embed.add_field(name=f"Old Wallet - Total Balance (Matchsticks)", value=f"{old_wallet_total_balance} Points", inline=False)
        embed.add_field(name=f"Old Wallet - Points from Quests (Matchsticks)", value=f"{old_wallet_quest_points} Points (from {len(old_wallet_quest_txs)} quest completions)", inline=False)
        embed.set_footer(text="Confirm or Cancel. Times out in 5 minutes.")
        confirm_view = ConfirmTransferView(self, old_wallet, new_wallet, pre_transfer_data)
        confirm_view.message_to_edit = msg_to_edit 
        try: await msg_to_edit.edit(content=None, embed=embed, view=confirm_view)
        except discord.HTTPException as e: logger.warning(f"Could not edit pre-transfer msg {msg_to_edit.id}. Error: {e}")
        await confirm_view.wait()
        if confirm_view.confirmed is True:
            original_channel_id = interaction.channel_id 
            await interaction.followup.send(
                f"⏳ Confirmed. Starting transfer: `{old_wallet}` → `{new_wallet}`. Report in <#{original_channel_id}>.", ephemeral=True 
            )
            self.bot.loop.create_task(self.execute_transfer_process(old_wallet, new_wallet, pre_transfer_data, original_channel_id, interaction.user))
        elif confirm_view.confirmed is False: logger.info(f"Transfer {old_wallet}→{new_wallet} cancelled by {interaction.user.name}.")
        else: logger.info(f"Transfer confirmation timed out for {old_wallet}→{new_wallet}.")

    async def execute_transfer_process(self, old_wallet: str, new_wallet: str, pre_transfer_data: Dict[str, Any], report_channel_id: int, initiated_by: discord.User):
        transfer_key = f"{old_wallet}_{new_wallet}"
        async with self._transfer_lock:
            if transfer_key in self.processed_transfers:
                # ... (код предотвращения дублирования) ...
                try:
                    channel = self.bot.get_channel(report_channel_id) or await self.bot.fetch_channel(report_channel_id)
                    if channel: await channel.send(f"ℹ️ Transfer `{old_wallet}`→`{new_wallet}` was already processed on {self.processed_transfers[transfer_key].strftime('%Y-%m-%d %H:%M:%S UTC')}. No new action taken.")
                except Exception as e: logger.error(f"Failed to send duplicate process info to {report_channel_id}: {e}")
                return

            report_lines = [
                f"**Wallet Progress Transfer Report**",
                f"Initiated by: {initiated_by.name} (`{initiated_by.id}`) at {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
                f"Old Wallet: `{old_wallet}`", f"New Wallet: `{new_wallet}`",
                f"--- Pre-Transfer State (Old Wallet) ---",
                f"Total Balance (Matchsticks): {pre_transfer_data['old_wallet_total_balance']}",
                f"Points from Quests (Matchsticks): {pre_transfer_data['old_wallet_quest_points']} (from {len(pre_transfer_data['old_wallet_quest_txs_details'])} quest completions)",
                "--- Transfer Operations (to New Wallet) ---"
            ]
            
            successful_completion_ops = 0
            total_points_from_quests_actually_credited = Decimal('0') 
            errors_on_new_wallet_ops = []
            all_new_wallet_ops_successful = True
            rules_completed_this_run = set() 

            report_lines.append("\n**Quest Completion Transfer (Matchsticks):**")
            if pre_transfer_data['old_wallet_quest_txs_details']:
                for quest_tx in pre_transfer_data['old_wallet_quest_txs_details']:
                    if quest_tx['currencyId'] != MATCHSTICKS_CURRENCY_ID: continue
                    
                    rule_id = quest_tx['ruleId']
                    if rule_id in rules_completed_this_run:
                        logger.info(f"Rule {rule_id} already processed for completion for {new_wallet} in this run.")
                        report_lines.append(f"  ℹ️ Quest '{quest_tx['description']}' ({quest_tx['amount']} pts) - Rule ID already processed in this transfer session.")
                        continue
                    
                    original_amount_for_rule = quest_tx['amount']

                    try:
                        complete_payload = { "walletAddress": new_wallet }
                        # НЕ ПЕРЕДАЕМ 'amount' и 'idempotencyKey' для complete_loyalty_rule,
                        # так как API их не принимает для не-external правил, либо вызывает ошибку
                        
                        logger.debug(f"Attempting to complete rule {rule_id} for {new_wallet} with payload: {complete_payload}")
                        response = await self.snag_client.complete_loyalty_rule(rule_id=rule_id, data=complete_payload) 
                        
                        if response and not response.get("error") and response.get("message"):
                            report_lines.append(f"  ✅ Quest '{quest_tx['description']}' ({original_amount_for_rule} pts) -> Completion request sent (API Msg: {response.get('message')}).")
                            successful_completion_ops += 1
                            total_points_from_quests_actually_credited += original_amount_for_rule 
                            rules_completed_this_run.add(rule_id)
                        else:
                            all_new_wallet_ops_successful = False
                            err_msg = f"  ❌ Quest '{quest_tx['description']}' ({original_amount_for_rule} pts) -> Failed to send completion. API Resp: {str(response)[:150]}"
                            report_lines.append(err_msg); errors_on_new_wallet_ops.append(err_msg)
                            logger.error(f"Failed to complete rule {rule_id} for {new_wallet}. Resp: {response}")
                        # Увеличиваем задержку, так как были рейт-лимиты на этот эндпоинт
                        await asyncio.sleep(1.5) # Было 0.8
                    except Exception as e_complete:
                        all_new_wallet_ops_successful = False
                        err_msg = f"  ❌ Quest '{quest_tx['description']}' ({original_amount_for_rule} pts) -> Failed to send completion. Exc: {str(e_complete)[:100]}"
                        report_lines.append(err_msg); errors_on_new_wallet_ops.append(err_msg)
                        logger.exception(f"Exception completing rule {rule_id} for {new_wallet}")
            else: report_lines.append("  No Matchsticks quest data to process via rule completion.")

            report_lines.append("\n**Balance Adjustment (Matchsticks):**")
            old_total_balance = pre_transfer_data['old_wallet_total_balance']
            balance_difference = old_total_balance - total_points_from_quests_actually_credited 
            
            if balance_difference > Decimal('0'):
                # ... (логика корректировки баланса через create_transaction - БЕЗ ИЗМЕНЕНИЙ) ...
                report_lines.append(f"  Old wallet total ({old_total_balance}) > Credited quest pts ({total_points_from_quests_actually_credited}). Adjusting by +{balance_difference} Pts.")
                try:
                    single_adj_entry_data = {
                        "walletAddress": new_wallet, 
                        "amount": float(balance_difference) if '.' in str(balance_difference) else int(balance_difference), 
                        "loyaltyCurrencyId": MATCHSTICKS_CURRENCY_ID, "direction": "credit", 
                        "description": "Balance adjustment from old wallet progress transfer",
                        "externalId": f"transfer_adj_{old_wallet[2:9]}_{new_wallet[2:9]}_{int(datetime.datetime.utcnow().timestamp())}"}
                    final_adj_payload_for_api = {
                        "entries": [single_adj_entry_data],
                        "description": "Progress transfer balance adjustment (manual)"
                    }
                    if self.snag_client._organization_id: final_adj_payload_for_api['organizationId'] = self.snag_client._organization_id
                    if self.snag_client._website_id: final_adj_payload_for_api['websiteId'] = self.snag_client._website_id
                    response = await self.snag_client.create_transaction(tx_data=final_adj_payload_for_api)
                    is_successful_adj_creation = False
                    if response and not response.get("error") and response.get("id") and isinstance(response.get("entries"), list):
                        for entry in response.get("entries",[]):
                            if entry.get("direction") == "credit" and \
                               entry.get("loyaltyAccount", {}).get("user", {}).get("walletAddress","").lower() == new_wallet and \
                               Decimal(str(entry.get("amount","0"))) == balance_difference and \
                               entry.get("loyaltyCurrencyId") == MATCHSTICKS_CURRENCY_ID:
                                is_successful_adj_creation = True; break
                    if is_successful_adj_creation:
                        new_adj_tx_batch_id = response.get("id", "N/A")
                        report_lines.append(f"  ✅ Adjustment of +{balance_difference} Pts successful (Batch TxID: ...{new_adj_tx_batch_id[-6:]}).")
                    else:
                        all_new_wallet_ops_successful = False
                        err_msg = f"  ❌ Adjustment +{balance_difference} Pts failed. API Resp: {str(response)[:150]}"
                        report_lines.append(err_msg); errors_on_new_wallet_ops.append(err_msg)
                        logger.error(f"Failed to create/verify balance adjustment tx for {new_wallet}. Resp: {response}")
                except Exception as e_adj:
                    all_new_wallet_ops_successful = False
                    err_msg = f"  ❌ Adjustment +{balance_difference} Pts failed. Exc: {str(e_adj)[:100]}"
                    report_lines.append(err_msg); errors_on_new_wallet_ops.append(err_msg)
                    logger.exception(f"Exception creating balance adjustment tx for {new_wallet}")
            elif balance_difference < Decimal('0'):
                 report_lines.append(f"  Old total ({old_total_balance}) < Credited quest pts ({total_points_from_quests_actually_credited}). Delta: {balance_difference}. No negative adjustment made.")
                 logger.warning(f"Balance diff negative for transfer {old_wallet}->{new_wallet}. Old: {old_total_balance}, NewQPts Credited: {total_points_from_quests_actually_credited}")
            else: report_lines.append("  No balance adjustment needed for Matchsticks.")


            if all_new_wallet_ops_successful:
                # ... (логика обнуления старого кошелька - БЕЗ ИЗМЕНЕНИЙ) ...
                report_lines.append("\n**Zeroing Out Old Wallet Balance (Matchsticks):**")
                await asyncio.sleep(0.5) 
                current_old_wallet_balance_before_zeroing = await self._get_wallet_balance(old_wallet, MATCHSTICKS_CURRENCY_ID)
                if current_old_wallet_balance_before_zeroing > Decimal('0'):
                    report_lines.append(f"  Current balance on old wallet `{old_wallet}` is {current_old_wallet_balance_before_zeroing} Matchsticks. Attempting to debit.")
                    try:
                        single_zeroing_entry_data = {
                            "walletAddress": old_wallet, 
                            "amount": float(current_old_wallet_balance_before_zeroing) if '.' in str(current_old_wallet_balance_before_zeroing) else int(current_old_wallet_balance_before_zeroing), 
                            "loyaltyCurrencyId": MATCHSTICKS_CURRENCY_ID, "direction": "debit", 
                            "description": f"Balance zeroed due to progress transfer to {new_wallet}",
                            "externalId": f"zeroing_{old_wallet[2:9]}_{new_wallet[2:9]}_{int(datetime.datetime.utcnow().timestamp())}"}
                        final_zeroing_payload_for_api = {
                            "entries": [single_zeroing_entry_data],
                            "description": f"Zeroing old wallet {old_wallet} after transfer"
                        }
                        if self.snag_client._organization_id: final_zeroing_payload_for_api['organizationId'] = self.snag_client._organization_id
                        if self.snag_client._website_id: final_zeroing_payload_for_api['websiteId'] = self.snag_client._website_id
                        response = await self.snag_client.create_transaction(tx_data=final_zeroing_payload_for_api)
                        is_successful_zeroing = False
                        if response and not response.get("error") and response.get("id") and isinstance(response.get("entries"), list):
                            for entry in response.get("entries",[]):
                                if entry.get("direction") == "debit" and \
                                   entry.get("loyaltyAccount", {}).get("user", {}).get("walletAddress","").lower() == old_wallet and \
                                   Decimal(str(entry.get("amount","0"))) == current_old_wallet_balance_before_zeroing and \
                                   entry.get("loyaltyCurrencyId") == MATCHSTICKS_CURRENCY_ID:
                                    is_successful_zeroing = True; break
                        if is_successful_zeroing:
                            zero_tx_batch_id = response.get("id", "N/A")
                            report_lines.append(f"  ✅ Successfully debited {current_old_wallet_balance_before_zeroing} Pts from old wallet (Batch TxID: ...{zero_tx_batch_id[-6:]}).")
                        else:
                            err_msg = f"  ⚠️ Failed to zero out old wallet or unexpected API Resp: {str(response)[:150]}"
                            report_lines.append(err_msg) 
                            logger.error(f"Failed to create/verify zeroing transaction for {old_wallet}. Resp: {response}")
                    except Exception as e_zero:
                        err_msg = f"  ⚠️ Failed to zero out old wallet. Exc: {str(e_zero)[:100]}"
                        report_lines.append(err_msg)
                        logger.exception(f"Exception creating zeroing transaction for {old_wallet}")
                else:
                    report_lines.append(f"  Old wallet `{old_wallet}` already has zero or non-positive Matchsticks balance ({current_old_wallet_balance_before_zeroing}). No zeroing needed.")
            elif errors_on_new_wallet_ops:
                 report_lines.append("\n**Zeroing Out Old Wallet Balance (Matchsticks):**")
                 report_lines.append("  Skipped due to errors during the transfer operations to the new wallet.")
            
            report_lines.append("\nWaiting for potential asynchronous reward processing before final balance check...")
            await asyncio.sleep(5) 
            
            final_new_wallet_balance = await self._get_wallet_balance(new_wallet, MATCHSTICKS_CURRENCY_ID)
            report_lines.append("\n--- Post-Transfer State (New Wallet) ---")
            report_lines.append(f"Final Total Balance (Matchsticks): {final_new_wallet_balance}")

            if errors_on_new_wallet_ops:
                report_lines.append("\n--- Errors Encountered During Rule Completion/Adjustment (to New Wallet) ---")
                report_lines.extend(errors_on_new_wallet_ops)
            
            if all_new_wallet_ops_successful:
                report_lines.append("\n**All rule completion requests and adjustments for the new wallet were sent successfully.**")
                self.processed_transfers[transfer_key] = datetime.datetime.utcnow()
            else:
                report_lines.append("\n**Process attempted. Some operations for the new wallet encountered errors. Please review the report.**")
                logger.warning(f"Transfer {transfer_key} to new wallet had errors, not marking as fully processed for retry purposes (manual check advised).")
            
            report_lines.append("Transfer process execution cycle finished.")

        final_report_content = "\n".join(report_lines)
        try:
            channel_to_report = self.bot.get_channel(report_channel_id) or await self.bot.fetch_channel(report_channel_id)
            if channel_to_report:
                if len(final_report_content) > 1950 : 
                    report_bytes = final_report_content.encode('utf-8')
                    report_stream = io.BytesIO(report_bytes)
                    report_filename = f"transfer_report_{old_wallet[2:8]}_{new_wallet[2:8]}.txt"
                    await channel_to_report.send(f"📝 Transfer `{old_wallet}`→`{new_wallet}` executed. Report attached.", file=discord.File(fp=report_stream, filename=report_filename)) 
                else: await channel_to_report.send(final_report_content)
            else: logger.error(f"Could not find channel {report_channel_id} for report.")
        except Exception as e_final_report:
            logger.error(f"Failed to send final report to {report_channel_id}: {e_final_report}", exc_info=True)

    @commands.command(name="send_transfer_panel")
    @commands.has_any_role("Ranger") 
    async def send_transfer_panel_command(self, ctx: commands.Context):
        embed = discord.Embed(title="Wallet Progress Transfer Panel", color=discord.Color.red(),
            description="Initiate progress transfer. Sensitive operation, use with caution.")
        view = ProgressTransferPanelView(self)
        await ctx.send(embed=embed, view=view) 
        logger.info(f"ProgressTransfer panel sent by {ctx.author.name} to {ctx.channel.id}")

    @send_transfer_panel_command.error
    async def send_transfer_panel_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingAnyRole): await ctx.send("⛔ You lack 'Ranger' role for this.")
        else:
            logger.error(f"Error in send_transfer_panel: {error}", exc_info=True)
            await ctx.send("⚙️ Panel command error.")

async def setup(bot: commands.Bot):
    if not getattr(bot, 'snag_client', None) or not getattr(bot.snag_client, '_api_key', None):
        logger.error("CRITICAL: Main Snag API client (or key) missing. ProgressTransferCog WONT load.")
        return
    cog = ProgressTransferCog(bot)
    if not hasattr(bot.snag_client, 'complete_loyalty_rule'): # Проверка наличия метода
        logger.error("CRITICAL: SnagApiClient is missing 'complete_loyalty_rule' method. ProgressTransferCog WONT load.")
        return 
    await bot.add_cog(cog)
    bot.add_view(ProgressTransferPanelView(cog))
    logger.info("Registered persistent View for ProgressTransferPanelView.")