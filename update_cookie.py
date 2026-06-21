"""
update_cookie.py — /updatecookie slash command

Lets an authorized user push a fresh Roblox .ROBLOSECURITY cookie to the
cubano-ranker backend on Render, without redeploying anything.

Add this file alongside your existing /ban cog and load it the same way
your bot already loads cogs (see SETUP NOTES at the bottom of this file).
"""

import os
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp

# ==================== CONFIGURATION ====================
# Set these as environment variables on Railway (same place you set your
# Discord bot token). Do NOT hardcode them in this file.

BACKEND_URL = os.environ["BACKEND_URL"]          # e.g. https://cubano-ranker.onrender.com
BACKEND_API_KEY = os.environ["BACKEND_API_KEY"]  # must match API_KEY on Render

# Comma-separated list of Discord user IDs allowed to run this command.
# Set this as an env var, e.g. ALLOWED_USER_IDS=123456789012345678,987654321098765432
ALLOWED_USER_IDS = {
    int(uid.strip())
    for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
}


class UpdateCookieCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="updatecookie",
        description="Update the Roblox cookie used by the ranking backend (restricted)."
    )
    @app_commands.describe(
        new_cookie="The full .ROBLOSECURITY value, starting with _|WARNING:-DO-NOT-SHARE-THIS."
    )
    async def updatecookie(self, interaction: discord.Interaction, new_cookie: str):
        # Always reply ephemerally so nobody else in the channel sees the response
        await interaction.response.defer(ephemeral=True, thinking=True)

        # ---- Authorization check ----
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.followup.send(
                "❌ You are not authorized to use this command.",
                ephemeral=True
            )
            return

        new_cookie = new_cookie.strip()

        if not new_cookie.startswith("_|WARNING:-DO-NOT-SHARE-THIS."):
            await interaction.followup.send(
                "❌ That doesn't look like a valid `.ROBLOSECURITY` cookie "
                "(it should start with `_|WARNING:-DO-NOT-SHARE-THIS.`). Nothing was sent.",
                ephemeral=True
            )
            return

        # ---- Send to backend ----
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{BACKEND_URL}/update-cookie",
                    json={"newCookie": new_cookie},
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": BACKEND_API_KEY
                    },
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    data = await resp.json()

                    if resp.status == 200 and data.get("success"):
                        roblox_user = data.get("user", {})
                        username = roblox_user.get("name", "Unknown")
                        await interaction.followup.send(
                            f"✅ Cookie updated and validated successfully.\n"
                            f"Logged in as: **{username}**",
                            ephemeral=True
                        )
                    else:
                        error_msg = data.get("error", "Unknown error")
                        await interaction.followup.send(
                            f"❌ Backend rejected the cookie: {error_msg}",
                            ephemeral=True
                        )

        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to reach backend: {e}",
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(UpdateCookieCog(bot))


# ==================== SETUP NOTES ====================
#
# 1. This file expects three environment variables on Railway:
#       BACKEND_URL       e.g. https://cubano-ranker.onrender.com  (no trailing slash)
#       BACKEND_API_KEY   must match the API_KEY env var on your Render backend
#       ALLOWED_USER_IDS  comma-separated Discord user IDs, e.g. 123456789012345678
#
# 2. Make sure aiohttp is in your requirements.txt (add a line: aiohttp)
#
# 3. Load this cog the same way your bot loads update_cookie's sibling, /ban.
#    If your bot uses a cogs/ folder with automatic loading, just drop this
#    file in there. If you load cogs manually in your main bot file, add:
#
#       await bot.load_extension("update_cookie")
#
#    near where you load your /ban cog.
#
# 4. Slash commands need to be synced with Discord after adding a new one.
#    If your bot doesn't already sync commands on startup, add this once
#    in your on_ready event (safe to leave in permanently):
#
#       await bot.tree.sync()
#
# 5. To get your own Discord user ID for ALLOWED_USER_IDS: enable Developer
#    Mode in Discord (Settings → Advanced → Developer Mode), then right-click
#    your own name/avatar anywhere and choose "Copy User ID".
