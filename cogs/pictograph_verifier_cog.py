# cogs/pictograph_verifier_cog.py
import discord
from discord.ext import commands
import logging
import os
import asyncio
import json
from typing import List, Optional, Any

from web3 import Web3, HTTPProvider

logger = logging.getLogger(__name__)

# --- Константы для кога ---
PICTOGRAPH_CONTRACT_ADDRESS = "0x37Cbfa07386dD09297575e6C699fe45611AC12FE"
PICTOGRAPH_RULE_ID = "52c572dd-424f-47eb-8f61-43788d923d49"
ABI_FILE_PATH = "data/abis/PictographsMemoryCard_abi.json"
NULL_ADDRESS = "0x0000000000000000000000000000000000000000"
BLOCK_SCAN_CHUNK_SIZE = 99999

# --- Загрузка конфигурации из .env ---
RPC_URL = os.getenv('RPC_URL')
CHAIN_ID_STR = os.getenv('CHAIN_ID')
CHAIN_ID = int(CHAIN_ID_STR) if CHAIN_ID_STR and CHAIN_ID_STR.isdigit() else None
BASECAMP_EXPLORER_TX_PREFIX = "https://basecamp.cloud.blockscout.com/tx/"

def load_abi_from_file(path: str) -> List[Any]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            abi_data = json.load(f)
            if isinstance(abi_data, list):
                logger.info(f"Successfully loaded ABI from {path}")
                return abi_data
            else:
                logger.critical(f"ABI data in {path} is not a list. ABI loading failed.")
                return []
    except FileNotFoundError:
        logger.critical(f"ABI file for Pictograph contract not found at {path}!")
        return []
    except json.JSONDecodeError:
        logger.critical(f"Could not decode ABI file at {path}! Check JSON validity.")
        return []
    except Exception as e:
        logger.critical(f"An unexpected error occurred while loading ABI from {path}: {e}", exc_info=True)
        return []

CONTRACT_ABI = load_abi_from_file(ABI_FILE_PATH)

# --- View для подтверждения зачисления ---
class RequestBackfillView(discord.ui.View):
    def __init__(self, cog_instance: "PictographVerifierCog", wallet_address: str):
        super().__init__(timeout=300.0)
        self.cog = cog_instance
        self.wallet_address = wallet_address
        self.message: Optional[discord.Message] = None

    @discord.ui.button(label="✅ Yes, award points!", style=discord.ButtonStyle.success, custom_id="picto:backfill_v3") # Обновил ID для надежности
    async def backfill_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.clear_items()
        await interaction.response.edit_message(content="⏳ Processing your request to award points...", view=self)
        await self.cog.handle_backfill_request(interaction, self.wallet_address)
        self.stop()

    async def on_timeout(self):
        self.clear_items()
        if self.message:
            try:
                await self.message.edit(content="Request timed out. Please start the verification again if needed.", view=self)
            except discord.HTTPException: pass
        self.stop()

# --- Основная View для панели ---
class PictographPanelView(discord.ui.View):
    def __init__(self, cog_instance: "PictographVerifierCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    @discord.ui.button(label="✅ Verify My Pictograph Mint", style=discord.ButtonStyle.primary, custom_id="picto:verify_mint_v3") # Обновил ID
    async def verify_mint_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_verification_request(interaction)


class PictographVerifierCog(commands.Cog, name="Pictograph Verifier"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snag_client = getattr(bot, 'snag_client', None)
        self.w3: Optional[Web3] = None
        self.contract: Optional[Web3.eth.contract.Contract] = None # type: ignore
        self.is_web3_configured = False

        if not all([RPC_URL, CHAIN_ID, CONTRACT_ABI]):
            logger.error(f"{self.__class__.__name__}: Missing Web3 configurations. Verifier disabled.")
            return

        if not self.snag_client or not self.snag_client._api_key:
            logger.error(f"{self.__class__.__name__}: Main Snag API Client not configured. Verifier disabled.")
            return

        try:
            self.w3 = Web3(HTTPProvider(RPC_URL))
            if not self.w3.is_connected(): # type: ignore
                logger.error(f"{self.__class__.__name__}: Failed to connect to Web3 provider at {RPC_URL}.")
                return
            
            checksum_address = self.w3.to_checksum_address(PICTOGRAPH_CONTRACT_ADDRESS)
            self.contract = self.w3.eth.contract(address=checksum_address, abi=CONTRACT_ABI) # type: ignore
            self.is_web3_configured = True
            logger.info(f"{self.__class__.__name__}: Successfully configured for chain {CHAIN_ID} and contract {PICTOGRAPH_CONTRACT_ADDRESS}.")

        except Exception as e:
            logger.error(f"{self.__class__.__name__}: Failed to initialize Web3 or contract instance: {e}", exc_info=True)

    async def cog_load(self):
        self.bot.add_view(PictographPanelView(self))
        logger.info(f"Cog '{self.__class__.__name__}' loaded and persistent view registered.")

    @commands.command(name="send_pictograph_panel")
    @commands.has_any_role("Ranger")
    async def send_pictograph_panel_command(self, ctx: commands.Context):
        if not self.is_web3_configured or not self.snag_client:
            await ctx.send("⚠️ The verifier service is not properly configured. Please check logs.")
            return

        embed = discord.Embed(
            title="Pictographs Memory Card Mint Verification",
            description="Have you minted the Pictographs Memory Card NFT in the past?\n\n"
                        "Click the button below to verify your mint. The bot will check your linked wallet and "
                        "find your mint transaction on the blockchain.",
            color=discord.Color.blue()
        )
        embed.set_footer(text="Ensure your Discord account is linked to your wallet on the loyalty platform.")
        view = PictographPanelView(self)
        await ctx.send(embed=embed, view=view)

    async def handle_verification_request(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not self.is_web3_configured or not self.snag_client:
            await interaction.followup.send("⚠️ The verification service is currently unavailable.", ephemeral=True)
            return

        user = interaction.user
        discord_handle = user.name if user.discriminator == '0' else f"{user.name}#{user.discriminator}"
        account_response = await self.snag_client.get_account_by_social("discordUser", discord_handle)

        if not account_response or not isinstance(account_response.get("data"), list) or not account_response["data"]:
            await interaction.followup.send("❌ **Wallet Not Found!**\nPlease link your Discord account.", ephemeral=True)
            return
        
        user_info = account_response["data"][0].get("user", {})
        wallet_address = user_info.get("walletAddress")
        if not wallet_address:
            await interaction.followup.send("❌ Could not retrieve a valid wallet address from your linked account.", ephemeral=True)
            return

        logger.info(f"Checking blockchain for mint event from {NULL_ADDRESS} to {wallet_address}")
        try:
            checksum_wallet = self.w3.to_checksum_address(wallet_address)
            latest_block = await asyncio.to_thread(self.w3.eth.get_block_number)
            mint_event = None
            
            for end_block in range(latest_block, 0, -BLOCK_SCAN_CHUNK_SIZE):
                start_block = max(0, end_block - BLOCK_SCAN_CHUNK_SIZE + 1)
                logger.info(f"Scanning for mint from block {start_block} to {end_block} for wallet {wallet_address}")
                try: await interaction.edit_original_response(content=f"⏳ Scanning blockchain... (block {start_block})")
                except discord.HTTPException: pass
                
                events_in_chunk = await asyncio.to_thread(
                    self.contract.events.Transfer.get_logs, # type: ignore
                    {"from": NULL_ADDRESS, "to": checksum_wallet},
                    start_block, end_block
                )

                if events_in_chunk:
                    mint_event = events_in_chunk[0]; break

            if not mint_event:
                logger.warning(f"No mint event found for wallet {wallet_address} after full scan.")
                await interaction.followup.send("❌ **Mint Not Found.**\nWe couldn't find a mint transaction for this NFT.", ephemeral=True)
                return
            
            tx_hash_hex = mint_event['transactionHash'].hex()
            tx_hash_with_prefix = f"0x{tx_hash_hex}"
            tx_url = f"{BASECAMP_EXPLORER_TX_PREFIX}{tx_hash_with_prefix}"
            logger.info(f"Found mint event for wallet {wallet_address} in transaction {tx_hash_with_prefix}.")

            backfill_view = RequestBackfillView(self, wallet_address)
            msg_content = (
                f"✅ **Mint Transaction Found!**\n\n"
                f"We found a mint transaction for your wallet `{wallet_address}`.\n"
                f"**Transaction Hash:** `{tx_hash_with_prefix}`\n"
                f"**Explorer Link:** [Click to view]({tx_url})\n\n"
                f"If this is correct, press the button below to receive your points."
            )
            message = await interaction.followup.send(msg_content, view=backfill_view, ephemeral=True)
            backfill_view.message = message

        except Exception as e:
            logger.error(f"Error checking blockchain for wallet {wallet_address}: {e}", exc_info=True)
            await interaction.followup.send("⚙️ An error occurred while communicating with the blockchain. Please try again later.", ephemeral=True)

    async def handle_backfill_request(self, interaction: discord.Interaction, wallet_address: str):
        logger.info(f"User confirmed. Checking existing completions for wallet {wallet_address} and rule {PICTOGRAPH_RULE_ID}")
        history_response = await self.snag_client.get_transaction_entries(
            wallet_address=wallet_address, rule_id=PICTOGRAPH_RULE_ID, limit=1
        )
        if history_response and isinstance(history_response.get("data"), list) and history_response["data"]:
            logger.info(f"Backfill requested, but quest already completed for {wallet_address}.")
            await interaction.edit_original_response(content="✅ You have already received points for this quest.", view=None)
            return

        logger.info(f"Completing rule {PICTOGRAPH_RULE_ID} for wallet {wallet_address}")
        
        # --- ИСПРАВЛЕНИЕ: Укорачиваем idempotencyKey ---
        # Используем ID правила и последние 12 символов адреса кошелька для уникальности и соблюдения лимита
        safe_wallet_part = wallet_address.lower()[-12:]
        idempotency_key = f"{PICTOGRAPH_RULE_ID}-{safe_wallet_part}"
        
        completion_payload = {
            "walletAddress": wallet_address,
            "idempotencyKey": idempotency_key
        }
        
        logger.debug(f"Attempting rule completion with idempotencyKey: {idempotency_key} (length: {len(idempotency_key)})")

        completion_response = await self.snag_client.complete_loyalty_rule(PICTOGRAPH_RULE_ID, completion_payload)

        if completion_response and not completion_response.get("error"):
            await interaction.edit_original_response(content="🎉 **Success!** Your past mint has been verified and points have been awarded.", view=None)
            logger.info(f"Successfully completed rule {PICTOGRAPH_RULE_ID} for {wallet_address}. Response: {completion_response}")
        else:
            error_msg = completion_response.get("message", "Unknown error.") if completion_response else "No response."
            logger.error(f"Failed to complete rule for {wallet_address}. Response: {completion_response}")
            await interaction.edit_original_response(content=f"⚙️ We verified your mint, but an error occurred while awarding points: `{error_msg}`. Please contact an admin.", view=None)

async def setup(bot: commands.Bot):
    if not all([RPC_URL, CHAIN_ID, CONTRACT_ABI]):
        logger.critical("PictographVerifierCog will NOT be loaded due to missing RPC/Chain/ABI configuration.")
        return
    if not getattr(bot, 'snag_client', None) or not bot.snag_client._api_key:
        logger.critical("PictographVerifierCog will NOT be loaded due to missing Snag API client or key.")
        return
    
    await bot.add_cog(PictographVerifierCog(bot))