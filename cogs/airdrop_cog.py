# cogs/airdrop_cog.py
import discord
from discord import app_commands
from discord.ext import commands
import logging
import os
import io
import re
import asyncio
import json
from typing import List, Optional, Any
from web3 import Web3, HTTPProvider
from utils.checks import is_admin_in_guild # <--- ИМПОРТ

logger = logging.getLogger(__name__)

# ... (остальной код файла без изменений) ...
# --- Загрузка конфигурации ---
RPC_URL = os.getenv('RPC_URL')
BOT_WALLET_ADDRESS = os.getenv('BOT_WALLET_ADDRESS')
BOT_WALLET_PRIVATE_KEY = os.getenv('BOT_WALLET_PRIVATE_KEY')
AIRDROP_CONTRACT_ADDRESS = os.getenv('AIRDROP_CONTRACT_ADDRESS')
CHAIN_ID_STR = os.getenv('CHAIN_ID')

# --- Конфигурация для Camp Network BaseCAMP ---
CAMP_NETWORK_BASECAMP_CHAIN_ID = 123420001114
CAMP_NETWORK_BASECAMP_EXPLORER_TX_PREFIX = "https://basecamp.cloud.blockscout.com/tx/"

CHAIN_ID = int(CHAIN_ID_STR) if CHAIN_ID_STR and CHAIN_ID_STR.isdigit() else None

# --- Путь к файлу ABI ---
ABI_FILE_PATH = "data/abis/SimpleERC1155_abi.json"

# --- Загрузка ABI Контракта из файла ---
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
        logger.critical(f"ABI file for Airdrop contract not found at {path}!")
        return []
    except json.JSONDecodeError:
        logger.critical(f"Could not decode ABI file at {path}! Check JSON validity.")
        return []
    except Exception as e:
        logger.critical(f"An unexpected error occurred while loading ABI from {path}: {e}", exc_info=True)
        return []

CONTRACT_ABI_LIST = load_abi_from_file(ABI_FILE_PATH)

class AirdropCog(commands.Cog, name="NFT Airdrop"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.w3: Optional[Web3] = None
        self.contract: Optional[Web3.eth.contract.Contract] = None # type: ignore
        self.is_web3_configured = False
        self.block_explorer_tx_prefix = ""

        if not all([RPC_URL, BOT_WALLET_ADDRESS, BOT_WALLET_PRIVATE_KEY, AIRDROP_CONTRACT_ADDRESS, CHAIN_ID is not None, CONTRACT_ABI_LIST]):
            logger.error(f"{self.__class__.__name__}: Missing configurations. Airdrop disabled.")
            return
        
        if CHAIN_ID != CAMP_NETWORK_BASECAMP_CHAIN_ID:
            logger.error(f"{self.__class__.__name__}: .env CHAIN_ID ({CHAIN_ID}) != expected ({CAMP_NETWORK_BASECAMP_CHAIN_ID}). Airdrop disabled.")
            return
        
        self.block_explorer_tx_prefix = CAMP_NETWORK_BASECAMP_EXPLORER_TX_PREFIX

        try:
            self.w3 = Web3(HTTPProvider(RPC_URL))

            if not self.w3.is_connected(): # type: ignore
                logger.error(f"{self.__class__.__name__}: Failed to connect to Web3: {RPC_URL}.")
                return

            if not self.w3.is_address(str(AIRDROP_CONTRACT_ADDRESS)): # type: ignore
                logger.error(f"{self.__class__.__name__}: Invalid contract address: {AIRDROP_CONTRACT_ADDRESS}.")
                return
            
            self.contract = self.w3.eth.contract(address=Web3.to_checksum_address(str(AIRDROP_CONTRACT_ADDRESS)), abi=CONTRACT_ABI_LIST) # type: ignore
            self.is_web3_configured = True
            logger.info(f"{self.__class__.__name__}: Configured for Camp Network BaseCAMP (ID: {CHAIN_ID}). RPC: {RPC_URL}. Contract: {AIRDROP_CONTRACT_ADDRESS}.")
            asyncio.create_task(self.check_airdrop_admin_permission())

        except Exception as e:
            logger.error(f"{self.__class__.__name__}: Init error: {e}", exc_info=True)

    async def check_airdrop_admin_permission(self):
        if not self.is_web3_configured or not self.contract or not self.w3: return
        try:
            current_airdrop_admin = await asyncio.to_thread(self.contract.functions.airdropAdmin().call) # type: ignore
            checksum_bot_wallet = Web3.to_checksum_address(str(BOT_WALLET_ADDRESS))
            if Web3.to_checksum_address(current_airdrop_admin) == checksum_bot_wallet:
                logger.info(f"Bot wallet {BOT_WALLET_ADDRESS} IS airdropAdmin.")
            else:
                logger.warning(f"Bot wallet {BOT_WALLET_ADDRESS} NOT airdropAdmin. Current: {current_airdrop_admin}.")
        except Exception as e:
            logger.error(f"AirdropAdmin check error: {e}", exc_info=True)

    @app_commands.command(name="airdropnft", description="Airdrop ERC1155 NFTs (Camp Network BaseCAMP).")
    @app_commands.describe(
        token_id="Token ID.", amount_per_recipient="Amount per recipient.",
        recipients_file=".txt file with addresses.", data_hex="Optional hex data (0x...)."
    )
    @is_admin_in_guild() # <--- ИЗМЕНЕНИЕ
    async def airdropnft_slash_command(
        self, interaction: discord.Interaction, token_id: int, amount_per_recipient: int,
        recipients_file: discord.Attachment, data_hex: Optional[str] = None
    ):
        # ... (код команды без изменений) ...
        if not self.is_web3_configured or not self.w3 or not self.contract:
            await interaction.response.send_message("⚠️ Airdrop service not configured.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not BOT_WALLET_PRIVATE_KEY:
            await interaction.followup.send("⚠️ Bot wallet PK not set.", ephemeral=True); return
        if token_id < 0:
            await interaction.followup.send("⚠️ Token ID < 0.", ephemeral=True); return
        if amount_per_recipient <= 0:
            await interaction.followup.send("⚠️ Amount <= 0.", ephemeral=True); return
        if not recipients_file.filename.lower().endswith(".txt"):
            await interaction.followup.send("⚠️ Need .txt file.", ephemeral=True); return

        try:
            file_content_str = (await recipients_file.read()).decode('utf-8')
        except Exception as e:
            logger.error(f"File read error: {e}"); await interaction.followup.send("⚠️ File read error.", ephemeral=True); return

        address_pattern = re.compile(r"0x[a-fA-F0-9]{40}", re.IGNORECASE)
        potential_addresses = [m.group(0) for line in file_content_str.splitlines() if (m := address_pattern.search(line.strip()))]
        if not potential_addresses:
            await interaction.followup.send("⚠️ No address patterns in file.", ephemeral=True); return

        unique_valid_recipients = sorted(list(set(self.w3.to_checksum_address(addr) for addr in potential_addresses if self.w3.is_address(addr)))) # type: ignore
        if not unique_valid_recipients:
            await interaction.followup.send("⚠️ No valid addresses post-validation.", ephemeral=True); return
        
        MAX_RECIPIENTS_PER_TX = 200
        if len(unique_valid_recipients) > MAX_RECIPIENTS_PER_TX:
            await interaction.followup.send(f"⚠️ Max {MAX_RECIPIENTS_PER_TX} recipients. Got {len(unique_valid_recipients)}.", ephemeral=True); return

        airdrop_data_bytes = b''
        if data_hex:
            try: airdrop_data_bytes = bytes.fromhex(data_hex[2:] if data_hex.startswith("0x") else data_hex)
            except ValueError: await interaction.followup.send("⚠️ Invalid data_hex.", ephemeral=True); return

        try:
            checksum_bot_wallet_address = Web3.to_checksum_address(str(BOT_WALLET_ADDRESS))
            nonce = await asyncio.to_thread(self.w3.eth.get_transaction_count, checksum_bot_wallet_address) # type: ignore
            
            gas_price_strategy = {}
            try:
                latest_block = await asyncio.to_thread(self.w3.eth.get_block, 'latest') # type: ignore
                base_fee_from_block = latest_block.get('baseFeePerGas') # type: ignore

                if base_fee_from_block is not None: 
                    priority_fee_gwei_val = 2 
                    max_priority_fee = self.w3.to_wei(priority_fee_gwei_val, 'gwei') # type: ignore
                    base_fee_multiplier_val = 2.0 
                    max_fee = int(base_fee_from_block * base_fee_multiplier_val) + max_priority_fee # type: ignore
                    gas_price_strategy = {
                        'maxFeePerGas': max_fee,
                        'maxPriorityFeePerGas': max_priority_fee
                    }
                    logger.info(f"Using EIP-1559 gas: maxFee={self.w3.from_wei(max_fee, 'gwei')} Gwei, priorityFee={priority_fee_gwei_val} Gwei") # type: ignore
                else: 
                    legacy_gas_price = await asyncio.to_thread(self.w3.eth.gas_price) # type: ignore
                    legacy_price_multiplier_val = 1.2 
                    gas_price_strategy = {'gasPrice': int(legacy_gas_price * legacy_price_multiplier_val)} 
                    logger.info(f"Using legacy gas: price={self.w3.from_wei(gas_price_strategy['gasPrice'], 'gwei')} Gwei") # type: ignore
            except Exception as e_gas_strat:
                logger.warning(f"Gas strategy error: {e_gas_strat}. Fallback.")
                fallback_gwei_val = 10
                gas_price_strategy = {'gasPrice': self.w3.to_wei(fallback_gwei_val, 'gwei')} # type: ignore
                logger.info(f"Using fallback gas: price={fallback_gwei_val} Gwei")

            tx_params = {
                'from': checksum_bot_wallet_address, 'nonce': nonce,
                'chainId': CHAIN_ID, **gas_price_strategy
            }
            
            airdrop_function = self.contract.functions.airdrop( # type: ignore
                unique_valid_recipients, token_id, amount_per_recipient, airdrop_data_bytes
            )
            
            try:
                gas_estimate_params = tx_params.copy()
                if 'gas' in gas_estimate_params: del gas_estimate_params['gas'] # type: ignore
                estimated_gas = await asyncio.to_thread(airdrop_function.estimate_gas, gas_estimate_params)
                gas_limit_multiplier_val = 1.3 
                tx_params['gas'] = int(estimated_gas * gas_limit_multiplier_val) # type: ignore
                logger.info(f"Estimated gas: {estimated_gas}, using limit: {tx_params['gas']}")
            except Exception as e_gas:
                logger.error(f"Gas estimation error: {e_gas}", exc_info=True)
                await interaction.followup.send(f"⚠️ Gas estimation error: {str(e_gas)[:500]}. Aborted.", ephemeral=True)
                return

            built_tx = await asyncio.to_thread(airdrop_function.build_transaction, tx_params) # type: ignore
            signed_tx = await asyncio.to_thread(
                self.w3.eth.account.sign_transaction, built_tx, str(BOT_WALLET_PRIVATE_KEY) # type: ignore
            )
            
            tx_hash_bytes = await asyncio.to_thread(self.w3.eth.send_raw_transaction, signed_tx.raw_transaction) # type: ignore
            tx_hash_hex_string = tx_hash_bytes.hex()
            
            if not tx_hash_hex_string.startswith('0x'): tx_hash_for_url = '0x' + tx_hash_hex_string
            else: tx_hash_for_url = tx_hash_hex_string

            display_tx_hash = tx_hash_for_url 

            logger.info(f"TX sent to Camp Network BaseCAMP. Hash: {display_tx_hash}")

            await interaction.followup.send(
                f"✅ Airdrop transaction sent to Camp Network BaseCAMP!\n"
                f"Recipients: {len(unique_valid_recipients)}, Token ID: {token_id}, Amount: {amount_per_recipient}\n"
                f"Transaction Hash: `{display_tx_hash}`\n"
                f"View on explorer: {self.block_explorer_tx_prefix}{tx_hash_for_url}\n\n"
                f"Please monitor the transaction status on the block explorer. It may take some time to be confirmed.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Airdrop process error: {e}", exc_info=True)
            error_message_short = str(e)[:1000]
            if "insufficient funds" in error_message_short.lower(): error_message_short = "Insufficient funds for gas (CAMP token). Check bot wallet."
            elif "nonce too low" in error_message_short.lower(): error_message_short = "Nonce too low. Try again."
            elif "intrinsic gas too low" in error_message_short.lower(): error_message_short = "Intrinsic gas too low."
            elif "transaction underpriced" in error_message_short.lower() or "replacement transaction underpriced" in error_message_short.lower(): error_message_short = "TX underpriced. Network busy or gas too low."
            await interaction.followup.send(f"❌ Error: {error_message_short}. See logs.",ephemeral=True)

    @airdropnft_slash_command.error
    async def airdropnft_slash_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        # --- НАЧАЛО ИЗМЕНЕНИЙ В ОБРАБОТЧИКЕ ОШИБОК ---
        if isinstance(error, app_commands.NoPrivateMessage):
            msg_to_send = "⛔ This command can only be used on the official server."
        elif isinstance(error, app_commands.CheckFailure):
            msg_to_send = "⛔ This command is not available on this server."
        elif isinstance(error, app_commands.MissingRole):
            msg_to_send = f"⛔ You do not have the required '{error.missing_role}' role."
        # --- КОНЕЦ ИЗМЕНЕНИЙ В ОБРАБОТЧИКЕ ОШИБОК ---
        elif isinstance(error, app_commands.CommandInvokeError):
            logger.error(f"Invoke error /airdropnft by {interaction.user.name}: {error.original}", exc_info=True)
            oe_str = str(error.original)
            if "insufficient funds" in oe_str.lower(): msg_to_send = "❌ Error: Insufficient funds for gas."
            else: msg_to_send = f"⚙️ Invoke error: {oe_str[:300]}..."
        else:
            logger.error(f"Unhandled error /airdropnft by {interaction.user.name}: {error}", exc_info=True)
            msg_to_send = "⚙️ Unexpected error."
        try:
            if not interaction.response.is_done(): await interaction.response.send_message(msg_to_send, ephemeral=True)
            else: await interaction.followup.send(msg_to_send, ephemeral=True)
        except discord.HTTPException as e_http: logger.error(f"Failed to send error response: {e_http}")

async def setup(bot: commands.Bot):
    if CHAIN_ID != CAMP_NETWORK_BASECAMP_CHAIN_ID:
        logger.critical(f"CRITICAL: AirdropCog CHAIN_ID mismatch. Env: {CHAIN_ID}, Expected: {CAMP_NETWORK_BASECAMP_CHAIN_ID}. Cog NOT loaded.")
        return
    if not all([RPC_URL, BOT_WALLET_ADDRESS, BOT_WALLET_PRIVATE_KEY, AIRDROP_CONTRACT_ADDRESS, CONTRACT_ABI_LIST]):
        logger.critical("CRITICAL: AirdropCog missing config or ABI. Cog NOT loaded.")
        return
    await bot.add_cog(AirdropCog(bot))