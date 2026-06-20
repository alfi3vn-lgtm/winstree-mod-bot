import os
import discord
from discord.ext import commands
from discord import app_commands

BOT_TOKEN = os.environ["BOT_TOKEN"]

# Set this to your server ID for instant command registration.
# Leave as None to register globally (can take up to an hour to appear).
GUILD_ID = None  # e.g. 123456789012345678

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)  # prefix unused, but discord.py requires one


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    try:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
        else:
            synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Sync failed: {e}")


@bot.tree.command(name="ban", description="Ban a user by their Discord ID")
@app_commands.describe(user_id="The Discord ID of the user to ban", reason="Reason for the ban")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)

    try:
        uid = int(user_id)
    except ValueError:
        await interaction.followup.send("That doesn't look like a valid Discord ID (should be a number).")
        return

    try:
        await interaction.guild.ban(discord.Object(id=uid), reason=f"{reason} (by {interaction.user})")
        await interaction.followup.send(f"Banned user `{uid}`. Reason: {reason}")
    except discord.Forbidden:
        await interaction.followup.send("I don't have permission to ban that user (check role hierarchy and my ban permission).")
    except discord.HTTPException as e:
        await interaction.followup.send(f"Failed to ban: {e}")


@ban.error
async def ban_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You don't have permission to ban members.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Error: {error}", ephemeral=True)


bot.run(BOT_TOKEN)
