import os
import discord
from discord.ext import commands

# ==================== CONFIGURATION ====================
# Set this as an environment variable on Railway — never hardcode it.
DISCORD_BOT_TOKEN = os.environ["BOT_TOKEN"]

# ==================== BOT SETUP ====================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")

    try:
        await bot.load_extension("update_cookie")
        print("✅ Loaded cog: update_cookie")
    except Exception as e:
        print(f"❌ Failed to load update_cookie: {e}")

    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"❌ Failed to sync slash commands: {e}")

    print("🚀 Bot is online")


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
