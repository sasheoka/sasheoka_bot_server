# cogs/wl_checker_cog.py
import discord
from discord.ext import commands
from discord import app_commands
import logging
import asyncio
import re
from typing import Dict, Optional, Tuple, List

logger = logging.getLogger(__name__)

# --- Константы для кога ---
WL_FILE_PATH = "wl.txt"
PANEL_CHANNEL_ID = 1382759518399893574
LOG_CHANNEL_ID = 1382760639209934869
REQUIRED_ROLE_IDS = [
    1235689801697722428, # Campion
    1253785633621540895, # Camp Giude
    1161497860974587947, # Ranger
]
ADMIN_ROLE_NAME = "Ranger"
PANEL_IMAGE_URL = "https://media.discordapp.net/attachments/1293845765738991619/1382848869301358622/image.png?ex=684ca5de&is=684b545e&hm=3f52407ea9c880f794a71f723febe5461f2da853b2a203910711edde7e1ce55b&=&format=webp&quality=lossless"

EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")

# --- Вспомогательный класс для хранения данных ---
class WLEntry:
    def __init__(self, address: str, handle: Optional[str] = None, user_id: Optional[int] = None):
        self.address = address
        self.handle = handle
        self.user_id = user_id

# --- Модальное окно для ввода адреса ---
class AddressInputModal(discord.ui.Modal, title="Check Your WL Address"):
    wallet_address_input = discord.ui.TextInput(
        label="Your EVM Address", placeholder="0x...", required=True,
        style=discord.TextStyle.short, min_length=42, max_length=42
    )

    def __init__(self, cog_instance: "WlCheckerCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        submitted_address = self.wallet_address_input.value.strip().lower()

        if not EVM_ADDRESS_PATTERN.match(submitted_address):
            await interaction.followup.send("⚠️ Invalid EVM address format.", ephemeral=True)
            return
        await self.cog.process_address_check(interaction, submitted_address)

# --- View для подтверждения смены адреса ---
class ConfirmChangeView(discord.ui.View):
    def __init__(self, cog_instance: "WlCheckerCog", user: discord.User, old_address: str, new_address: str):
        super().__init__(timeout=180.0)
        self.cog = cog_instance; self.user = user; self.old_address = old_address
        self.new_address = new_address; self.message: Optional[discord.Message] = None

    async def _disable_buttons(self, interaction: discord.Interaction, content: str):
        for item in self.children:
            if isinstance(item, discord.ui.Button): item.disabled = True
        try: await interaction.response.edit_message(content=content, view=self)
        except discord.HTTPException as e: logger.error(f"Failed to edit confirmation message: {e}")
        self.stop()

    @discord.ui.button(label="Change to New Address", style=discord.ButtonStyle.danger, custom_id="wl_change_new_en_v5")
    async def change_to_new_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.update_wl_entry(self.user, self.new_address)
        await self._disable_buttons(interaction, f"✅ Your address has been updated to `{self.new_address}`.")

    @discord.ui.button(label="Keep Old Address", style=discord.ButtonStyle.secondary, custom_id="wl_keep_old_en_v5")
    async def keep_old_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._disable_buttons(interaction, "✅ Understood. We are keeping your old address on the list.")

    async def on_timeout(self):
        if self.message:
            try:
                for item in self.children:
                     if isinstance(item, discord.ui.Button): item.disabled = True
                await self.message.edit(content="The selection has timed out. Your address has not been changed.", view=self)
            except discord.HTTPException: pass
        self.stop()

# --- Главный View для панели ---
class WlCheckerView(discord.ui.View):
    def __init__(self, cog_instance: "WlCheckerCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    @discord.ui.button(label="Check Address", style=discord.ButtonStyle.primary, custom_id="wl_checker:check_address_v1_en")
    async def check_address_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This button can only be used on the server.", ephemeral=True)
            return
        user_role_ids = {role.id for role in interaction.user.roles}
        if not set(REQUIRED_ROLE_IDS).intersection(user_role_ids):
            await interaction.response.send_message("⛔ You do not have the required role to check your address.", ephemeral=True)
            return
        await interaction.response.send_modal(AddressInputModal(self.cog))

# --- Класс Кога ---
class WlCheckerCog(commands.Cog, name="WL Checker"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.wl_data: Dict[str, WLEntry] = {}
        self.userid_to_address: Dict[int, str] = {}
        self._file_lock = asyncio.Lock()
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    async def cog_load(self):
        self.bot.add_view(WlCheckerView(self))
        self.bot.loop.create_task(self.initial_data_load_and_backfill())
        logger.info(f"Cog '{self.__class__.__name__}' initialized.")
        
    async def initial_data_load_and_backfill(self):
        await self.bot.wait_until_ready()
        entries_to_update = await self._load_wl_data()
        if entries_to_update:
            await self._backfill_user_ids(entries_to_update)

    async def _send_log_message(self, message: str, user: discord.User, color: discord.Color = discord.Color.blue()):
        try:
            log_channel = await self.bot.fetch_channel(LOG_CHANNEL_ID)
            embed = discord.Embed(description=message, color=color, timestamp=discord.utils.utcnow())
            embed.set_author(name=f"{user.name} (ID: {user.id})", icon_url=user.display_avatar.url)
            await log_channel.send(content=user.mention, embed=embed)
        except Exception as e:
            logger.error(f"Failed to send WL log message: {e}", exc_info=True)

    async def _load_wl_data(self) -> List[Tuple[str, str]]:
        handles_needing_id = []
        async with self._file_lock:
            self.wl_data.clear()
            self.userid_to_address.clear()
            try:
                with open(WL_FILE_PATH, 'r', encoding='utf-8') as f:
                    lines = f.readlines()

                for line in lines:
                    line = line.strip()
                    if not line: continue
                    
                    parts = re.split(r'\s+', line)
                    address, handle, user_id = None, None, None

                    # Ищем адрес
                    for part in parts:
                        if EVM_ADDRESS_PATTERN.match(part):
                            address = part.lower()
                            break
                    
                    if not address:
                        # Если адреса нет, сохраняем как есть в "неполном" виде
                        self.wl_data[line] = WLEntry(address=line) # Используем всю строку как ключ
                        continue

                    # Ищем ID и хендл
                    other_parts = [p for p in parts if p.lower() != address]
                    if other_parts:
                        potential_id = other_parts[-1]
                        if potential_id.isdigit():
                            user_id = int(potential_id)
                            handle = " ".join(other_parts[:-1]) if len(other_parts) > 1 else None
                        else:
                            handle = " ".join(other_parts)

                    entry = WLEntry(address, handle, user_id)
                    self.wl_data[address] = entry
                    if user_id: self.userid_to_address[user_id] = address
                    if handle and not user_id: handles_needing_id.append((address, handle))

                logger.info(f"Loaded {len(self.wl_data)} entries. Found {len(handles_needing_id)} handles to backfill.")
                return handles_needing_id
            except FileNotFoundError:
                logger.error(f"WL file not found at {WL_FILE_PATH}.")
                return []
            except Exception as e:
                logger.error(f"Error loading WL data: {e}", exc_info=True)
                return []

    async def _backfill_user_ids(self, entries_to_process: List[Tuple[str, str]]):
        await asyncio.sleep(10)
        logger.info(f"Starting background task to backfill {len(entries_to_process)} user IDs.")
        
        try:
            panel_channel = await self.bot.fetch_channel(PANEL_CHANNEL_ID)
            guild = panel_channel.guild
        except Exception as e:
            logger.error(f"Failed to get guild for backfill task: {e}")
            return

        member_map = {str(member).lower(): member for member in guild.members}
        changes_made = False

        for address, handle in entries_to_process:
            if handle.startswith(('http:', 'https:', '@')): continue

            member = member_map.get(handle.lower())
            if member and address in self.wl_data:
                entry = self.wl_data[address]
                if not entry.user_id:
                    if member.id in self.userid_to_address:
                        logger.warning(f"Backfill skipped for '{handle}': User ID {member.id} is already linked.")
                        continue
                    
                    entry.user_id = member.id
                    entry.handle = str(member) # Обновляем хендл на актуальный
                    self.userid_to_address[member.id] = address
                    logger.info(f"Backfilled: Found ID {member.id} for handle '{handle}' with address {address}.")
                    changes_made = True

        if changes_made:
            logger.info("Backfill complete. Saving updated data to file.")
            await self._save_wl_data()
        else:
            logger.info("Backfill task finished. No new IDs were added.")

    async def _save_wl_data(self):
        async with self._file_lock:
            try:
                # Читаем существующий файл, чтобы ничего не удалить
                try:
                    with open(WL_FILE_PATH, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                except FileNotFoundError:
                    lines = ["EVM Wallet\tDiscord ID/Twitter Handle\n"]

                # Создаем словарь для быстрого обновления
                updated_lines = {}
                new_entries = self.wl_data.copy()

                for i, line in enumerate(lines):
                    line_strip = line.strip()
                    if not line_strip: continue
                    
                    parts = re.split(r'\s+', line_strip)
                    address_in_line = None
                    for part in parts:
                        if EVM_ADDRESS_PATTERN.match(part):
                            address_in_line = part.lower()
                            break
                    
                    if address_in_line and address_in_line in self.wl_data:
                        entry = self.wl_data[address_in_line]
                        new_line_parts = [entry.address]
                        if entry.handle: new_line_parts.append(entry.handle)
                        if entry.user_id: new_line_parts.append(str(entry.user_id))
                        updated_lines[i] = "\t".join(new_line_parts) + "\n"
                        del new_entries[address_in_line] # Удаляем, чтобы не добавить в конец как дубликат

                # Применяем обновления к строкам
                for i, new_line in updated_lines.items():
                    lines[i] = new_line
                
                # Добавляем абсолютно новые записи в конец
                for address, entry in new_entries.items():
                    if not EVM_ADDRESS_PATTERN.match(address): continue # Пропускаем "неполные" записи без адреса
                    new_line_parts = [entry.address]
                    if entry.handle: new_line_parts.append(entry.handle)
                    if entry.user_id: new_line_parts.append(str(entry.user_id))
                    lines.append("\t".join(new_line_parts) + "\n")

                with open(WL_FILE_PATH, 'w', encoding='utf-8') as f:
                    f.writelines(lines)

                logger.info("Successfully saved WL data to wl.txt using non-destructive method.")
            except Exception as e:
                logger.error(f"Failed to save WL data: {e}", exc_info=True)


    async def process_address_check(self, interaction: discord.Interaction, submitted_address: str):
        user = interaction.user

        entry = self.wl_data.get(submitted_address)
        if entry:
            if entry.user_id == user.id:
                await interaction.followup.send("✅ Your address is already on our list. You're all set!", ephemeral=True)
            elif entry.user_id is None:
                await self.link_id_to_existing_address(user, submitted_address)
                await interaction.followup.send(f"✅ Your address was on our list. We've now linked your Discord account to it.", ephemeral=True)
            else:
                await interaction.followup.send(f"⚠️ This address is registered to another user. Contact an admin if you believe this is an error.", ephemeral=True)
            return

        if user.id in self.userid_to_address:
            old_address = self.userid_to_address[user.id]
            view = ConfirmChangeView(self, user, old_address, submitted_address)
            message = await interaction.followup.send(
                f"We found your Discord account on our list, but with a different address:\n"
                f"**Old:** `{old_address}`\n"
                f"**New:** `{submitted_address}`\n\n"
                f"Do you want to change the address to the new one?",
                view=view, ephemeral=True
            )
            view.message = message
            return

        await self.add_wl_entry(user, submitted_address)
        await interaction.followup.send("✅ You are a new participant! Your address has been added to the whitelist.", ephemeral=True)

    async def add_wl_entry(self, user: discord.User, address: str):
        new_entry = WLEntry(address, str(user), user.id)
        self.wl_data[address] = new_entry
        self.userid_to_address[user.id] = address
        await self._send_log_message(f"**new address**\n**Address:** `{address}`", user, discord.Color.green())
        await self._save_wl_data()

    async def update_wl_entry(self, user: discord.User, new_address: str):
        old_address = self.userid_to_address.get(user.id)
        if old_address and old_address in self.wl_data:
            del self.wl_data[old_address]

        self.wl_data[new_address] = WLEntry(new_address, str(user), user.id)
        self.userid_to_address[user.id] = new_address
        await self._send_log_message(f"**changed**\n**Old Address:** `{old_address}`\n**New Address:** `{new_address}`", user, discord.Color.orange())
        await self._save_wl_data()

    async def link_id_to_existing_address(self, user: discord.User, address: str):
        entry = self.wl_data.get(address)
        if entry:
            # Проверяем, не занят ли ID пользователя уже другим адресом
            if user.id in self.userid_to_address:
                await self._send_log_message(f"**link failed**\nUser tried to link address `{address}`, but their ID `{user.id}` is already associated with another address.", user, discord.Color.red())
                # Отправляем сообщение пользователю
                await user.send(f"You tried to claim the address `{address}`, but your Discord account is already linked to another address in our whitelist. Please contact an admin if you need to make changes.")
                return

            entry.user_id = user.id
            entry.handle = str(user)
            self.userid_to_address[user.id] = address
            await self._send_log_message(f"**handle and id added**\n**To Address:** `{address}`", user, discord.Color.purple())
            await self._save_wl_data()
            
    @app_commands.command(name="send_wl_panel", description="Sends the WL checker panel.")
    @app_commands.checks.has_any_role(ADMIN_ROLE_NAME)
    async def send_wl_panel_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            target_channel = await self.bot.fetch_channel(PANEL_CHANNEL_ID)
            embed = discord.Embed(
                title="Whitelist Checker",
                description=(
                    "To check if your address is on the Whitelist, press the button below.\n\n"
                   # f"You must have one of the following roles to use this feature: " + " ".join([f"<@&{role_id}>" for role_id in REQUIRED_ROLE_IDS])
                ), color=discord.Color.blue()
            )
            embed.set_image(url=PANEL_IMAGE_URL)
            await target_channel.send(embed=embed, view=WlCheckerView(self))
            await interaction.followup.send(f"✅ WL checker panel sent successfully to <#{PANEL_CHANNEL_ID}>.", ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to send WL panel: {e}", exc_info=True)
            await interaction.followup.send(f"⚙️ An unexpected error occurred: {e}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(WlCheckerCog(bot))