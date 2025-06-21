# cogs/art_collector_cog.py
import discord
from discord.ext import commands
import logging
import datetime
import asyncio
import io
from typing import Optional, Dict, List, Tuple
from collections import defaultdict
from utils.checks import is_prefix_admin_in_guild

logger = logging.getLogger(__name__)

# Поддерживаемые расширения изображений
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}

class ArtCollectorModal(discord.ui.Modal, title="Collect Art Contributors"):
    channel_id_input = discord.ui.TextInput(
        label="Channel ID",
        placeholder="Enter numeric channel ID",
        required=True,
        style=discord.TextStyle.short,
        min_length=17,
        max_length=20
    )
    start_date_input = discord.ui.TextInput(
        label="Start Date (YYYY-MM-DD)",
        placeholder="Example: 2025-05-01",
        required=True,
        style=discord.TextStyle.short,
        min_length=10,
        max_length=10
    )
    end_date_input = discord.ui.TextInput(
        label="End Date (YYYY-MM-DD)",
        placeholder="Example: 2025-05-31",
        required=True,
        style=discord.TextStyle.short,
        min_length=10,
        max_length=10
    )
    message_limit_input = discord.ui.TextInput(
        label="Message Limit (Optional)",
        placeholder="Example: 1000 (default: all)",
        required=False,
        style=discord.TextStyle.short,
        max_length=7
    )
    contributor_limit_input = discord.ui.TextInput(
        label="Contributor Limit (Optional)",
        placeholder="Example: 5 (default: 10, max: 50)",
        required=False,
        style=discord.TextStyle.short,
        max_length=2
    )

    def __init__(self, cog_instance: "ArtCollectorCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        channel_id_str = self.channel_id_input.value.strip()
        start_date_str = self.start_date_input.value.strip()
        end_date_str = self.end_date_input.value.strip()
        message_limit_str = self.message_limit_input.value.strip()
        contributor_limit_str = self.contributor_limit_input.value.strip()

        await self.cog.process_art_collection(
            interaction, channel_id_str, start_date_str, end_date_str, message_limit_str, contributor_limit_str
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in ArtCollectorModal: {error}", exc_info=True)
        await interaction.followup.send("An error occurred in the modal.", ephemeral=True)

class ArtCollectorPanelView(discord.ui.View):
    def __init__(self, cog_instance: "ArtCollectorCog"):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def _check_ranger_role(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command is server-only.", ephemeral=True)
            return False
        ranger_role = discord.utils.get(interaction.guild.roles, name="Ranger")
        if not ranger_role:
            await interaction.response.send_message("⛔ 'Ranger' role not found.", ephemeral=True)
            return False
        if ranger_role not in interaction.user.roles:
            await interaction.response.send_message("⛔ You lack the 'Ranger' role.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🎨 Collect Art Contributors", style=discord.ButtonStyle.primary, custom_id="artcollect:open_modal")
    async def collect_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_ranger_role(interaction):
            return
        modal = ArtCollectorModal(self.cog)
        await interaction.response.send_modal(modal)

class ArtCollectorCog(commands.Cog, name="Art Collector"):
    """Collects art contributors from a channel and generates an HTML report."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info(f"Cog '{self.__class__.__name__}' loaded.")

    async def cog_load(self):
        logger.info(f"Cog '{self.__class__.__name__}' initialized.")

    async def process_art_collection(
        self,
        interaction: discord.Interaction,
        channel_id_str: str,
        start_date_str: str,
        end_date_str: str,
        message_limit_str: Optional[str],
        contributor_limit_str: Optional[str]
    ):
        # Валидация ID канала
        try:
            channel_id = int(channel_id_str)
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                await interaction.followup.send(f"⚠️ ID `{channel_id}` is not a text channel.", ephemeral=True)
                return
        except ValueError:
            await interaction.followup.send("⚠️ Channel ID must be a number.", ephemeral=True)
            return
        except discord.NotFound:
            await interaction.followup.send(f"⚠️ Channel `{channel_id}` not found.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send(f"⛔ No permission to access channel `{channel_id}`.", ephemeral=True)
            return

        # Валидация дат
        try:
            start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
            end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc) + \
                       datetime.timedelta(days=1) - datetime.timedelta(microseconds=1)
            if start_date >= end_date:
                await interaction.followup.send("⚠️ Start date must be before end date.", ephemeral=True)
                return
        except ValueError:
            await interaction.followup.send("⚠️ Invalid date format. Use YYYY-MM-DD.", ephemeral=True)
            return

        # Валидация лимита сообщений
        message_limit = None
        if message_limit_str:
            try:
                limit_val = int(message_limit_str)
                if limit_val > 0:
                    message_limit = limit_val
            except ValueError:
                await interaction.followup.send("⚠️ Message limit must be a number.", ephemeral=True)
                return

        # Валидация лимита контрибуторов
        contributor_limit = 10  # Default
        if contributor_limit_str:
            try:
                contributor_limit = int(contributor_limit_str)
                if not 1 <= contributor_limit <= 50:
                    await interaction.followup.send("⚠️ Contributor limit must be between 1 and 50.", ephemeral=True)
                    return
            except ValueError:
                await interaction.followup.send("⚠️ Contributor limit must be a number.", ephemeral=True)
                return

        await interaction.followup.send(
            f"⏳ Collecting art from `{channel.name}` for {start_date_str} to {end_date_str} (Top {contributor_limit} contributors)...",
            ephemeral=True
        )

        # Сбор статистики
        user_stats: Dict[int, Tuple[int, int, List[str]]] = defaultdict(lambda: [0, 0, []])  # user_id: [post_count, reaction_count, [image_urls]]
        messages_scanned = 0

        try:
            async for message in channel.history(limit=message_limit, after=start_date, before=end_date, oldest_first=False):
                messages_scanned += 1
                if message.author.bot:
                    continue
                for attachment in message.attachments:
                    if any(attachment.filename.lower().endswith(ext) for ext in IMAGE_EXTENSIONS):
                        user_stats[message.author.id][0] += 1
                        user_stats[message.author.id][1] += sum(r.count for r in message.reactions)
                        if len(user_stats[message.author.id][2]) < 5:  # Лимит 5 превью
                            user_stats[message.author.id][2].append(attachment.url)
                if messages_scanned % 200 == 0:
                    await interaction.edit_original_response(content=f"⏳ Scanned {messages_scanned} messages...")

        except discord.Forbidden:
            await interaction.edit_original_response(content=f"⛔ No permission to read history in `{channel.name}`.")
            return
        except discord.HTTPException as e:
            logger.error(f"HTTP error reading channel {channel_id} history: {e}", exc_info=True)
            await interaction.edit_original_response(content="⚠️ An HTTP error occurred.")
            return
        except Exception as e:
            logger.error(f"Error reading channel {channel_id} history: {e}", exc_info=True)
            await interaction.edit_original_response(content="⚠️ An unexpected error occurred.")
            return

        if not user_stats:
            await interaction.edit_original_response(content=f"ℹ No art posts found in `{channel.name}` for the specified period.")
            return

        # Формирование HTML-отчета
        sorted_stats = sorted(user_stats.items(), key=lambda x: x[1][0], reverse=True)[:contributor_limit]  # Топ-N
        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Art Contributors Report</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    margin: 20px;
                    background-color: #f4f4f9;
                    color: #333;
                }}
                h1 {{
                    color: #ff4500;
                    text-align: center;
                }}
                p {{
                    text-align: center;
                    font-size: 1.1em;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin: 20px 0;
                    box-shadow: 0 2px 5px rgba(0,0,0,0.1);
                }}
                th, td {{
                    border: 1px solid #ddd;
                    padding: 12px;
                    text-align: left;
                }}
                th {{
                    background-color: #ff4500;
                    color: white;
                    font-weight: bold;
                }}
                tr:nth-child(even) {{
                    background-color: #f9f9f9;
                }}
                tr:hover {{
                    background-color: #f1f1f1;
                }}
                .art-gallery {{
                    display: flex;
                    gap: 10px;
                    flex-wrap: wrap;
                }}
                .art-gallery img {{
                    max-width: 150px;
                    border-radius: 5px;
                    border: 1px solid #ddd;
                }}
                .footer {{
                    text-align: center;
                    margin-top: 20px;
                    font-size: 0.9em;
                    color: #777;
                }}
            </style>
        </head>
        <body>
            <h1>Art Contributors Report</h1>
            <p>Channel: #{channel.name} (ID: {channel.id})<br>
               Period: {start_date_str} to {end_date_str}<br>
               Messages Scanned: {messages_scanned}<br>
               Total Contributors: {len(sorted_stats)}</p>
            <table>
                <tr>
                    <th>Rank</th>
                    <th>Discord Handle</th>
                    <th>Posts</th>
                    <th>Reactions</th>
                    <th>Sample Art</th>
                </tr>
        """

        for rank, (user_id, (post_count, reaction_count, image_urls)) in enumerate(sorted_stats, 1):
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            discord_handle = user.name if user.discriminator == '0' else f"{user.name}#{user.discriminator}"
            images_html = "".join(f'<img src="{url}" alt="art">' for url in image_urls) if image_urls else "No images"
            html_content += f"""
                <tr>
                    <td>{rank}</td>
                    <td>{discord_handle}</td>
                    <td>{post_count}</td>
                    <td>{reaction_count}</td>
                    <td><div class="art-gallery">{images_html}</div></td>
                </tr>
            """

        html_content += f"""
            </table>
            <div class="footer">
                Generated by ArtCollectorCog at {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
            </div>
        </body>
        </html>
        """

        # Сохранение и отправка HTML
        buffer = io.BytesIO(html_content.encode('utf-8'))
        filename = f"art_contributors_{channel.id}_{start_date_str.replace('-', '')}.html"
        discord_file = discord.File(fp=buffer, filename=filename)

        embed = discord.Embed(
            title="Art Contributors Report",
            description=f"Generated for #{channel.name} ({start_date_str} to {end_date_str}).\n"
                        f"Showing top {len(sorted_stats)} contributors.\n"
                        f"Download and open the HTML file in your browser to view the report.",
            color=discord.Color.orange(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_footer(text="ArtCollectorCog")

        await interaction.edit_original_response(
            content="✅ Report generated!",
            embed=embed,
            attachments=[discord_file]
        )
        logger.info(f"Art collection HTML report generated for channel {channel.id}. Contributors: {len(sorted_stats)}")

    @commands.command(name="send_art_panel")
    @is_prefix_admin_in_guild()
    async def send_art_panel(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Art Contributors Panel",
            description="Click to collect art contributors from a channel for a specific period.",
            color=discord.Color.orange()
        )
        view = ArtCollectorPanelView(self)
        await ctx.send(embed=embed, view=view)
        logger.info(f"ArtCollector panel sent by {ctx.author.name} to channel {ctx.channel.id}")

    @send_art_panel.error
    async def send_art_panel_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingAnyRole):
            await ctx.send("⛔ You lack the 'Ranger' role.")
        else:
            logger.error(f"Error in send_art_panel: {error}", exc_info=True)
            await ctx.send("⚙️ An error occurred.")

async def setup(bot: commands.Bot):
    cog = ArtCollectorCog(bot)
    await bot.add_cog(cog)
    bot.add_view(ArtCollectorPanelView(cog))
    logger.info("ArtCollectorCog loaded with persistent view.")