import discord
from discord import app_commands
from datetime import timedelta, timezone
import gspread
from google.oauth2.service_account import Credentials
import datetime
import pytz
import os

# ─── Config ───────────────────────────────────────────────
BOT_TOKEN            = os.environ["BOT_TOKEN"]
SHEET_NAME           = os.environ.get("SHEET_NAME", "WA | Moderation Logs")
SERVICE_ACCOUNT_FILE = os.environ.get("SERVICE_ACCOUNT_FILE", "winstree-moderation-bot-b1041820b277.json")
# ──────────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds         = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
gc            = gspread.authorize(creds)
timeout_sheet = gc.open(SHEET_NAME).worksheet("Timeout Logs")
warn_sheet    = gc.open(SHEET_NAME).worksheet("Warn Logs")
kick_sheet    = gc.open(SHEET_NAME).worksheet("Kick Logs")
ban_sheet     = gc.open(SHEET_NAME).worksheet("Ban Logs")
action_sheet  = gc.open(SHEET_NAME).worksheet("Moderator Action Log")

intents         = discord.Intents.default()
intents.members = True

bot  = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

UK_TZ = pytz.timezone("Europe/London")


# ─── Helpers ──────────────────────────────────────────────

def get_next_row(worksheet):
    all_values = worksheet.col_values(2)  # Column B
    return max(5, len(all_values) + 1)


def parse_date(date_str):
    try:
        return datetime.datetime.strptime(date_str, "%d/%m/%Y").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def log_action(moderator, command: str, reason: str = "N/A"):
    """Log every executed command to the Moderator Action Log sheet."""
    next_row = get_next_row(action_sheet)
    now_uk   = datetime.datetime.now(UK_TZ)
    date_str = now_uk.strftime("%d/%m/%Y")
    time_str = now_uk.strftime("%H:%M:%S")

    action_sheet.update(
        values=[
            [str(moderator), str(moderator.id), date_str, time_str, command, reason]
        ],
        range_name=f"B{next_row}:G{next_row}"
    )


# ─── Log Functions ────────────────────────────────────────

def log_timeout(moderator, target, duration, unit, reason):
    next_row     = get_next_row(timeout_sheet)
    date_str     = datetime.datetime.now(timezone.utc).strftime("%d/%m/%Y")
    duration_str = f"{duration} {unit}"

    timeout_sheet.update(
        values=[
            [str(target), str(target.id), date_str, reason, duration_str, str(moderator.id)]
        ],
        range_name=f"B{next_row}:G{next_row}"
    )


def log_warn(moderator, target, reason):
    next_row = get_next_row(warn_sheet)
    date_str = datetime.datetime.now(timezone.utc).strftime("%d/%m/%Y")

    warn_sheet.update(
        values=[
            [str(target), str(target.id), date_str, reason, str(moderator.id)]
        ],
        range_name=f"B{next_row}:F{next_row}"
    )


def log_kick(moderator, target, reason):
    next_row = get_next_row(kick_sheet)
    date_str = datetime.datetime.now(timezone.utc).strftime("%d/%m/%Y")

    kick_sheet.update(
        values=[
            [str(target), str(target.id), date_str, reason, str(moderator.id)]
        ],
        range_name=f"B{next_row}:F{next_row}"
    )


def log_ban(moderator, target, reason):
    next_row = get_next_row(ban_sheet)
    date_str = datetime.datetime.now(timezone.utc).strftime("%d/%m/%Y")

    ban_sheet.update(
        values=[
            [str(target), str(target.id), date_str, reason, str(moderator.id)]
        ],
        range_name=f"B{next_row}:F{next_row}"
    )


# ─── Warn Utilities ───────────────────────────────────────

def get_warn_count(target_id: int) -> int:
    all_ids = warn_sheet.col_values(3)
    return sum(1 for uid in all_ids[4:] if uid == str(target_id))


def get_warn_reasons(target_id: int) -> list[str]:
    rows = warn_sheet.get_all_values()
    reasons = []
    for row in rows[4:]:
        if len(row) >= 5 and row[2] == str(target_id):
            reasons.append(row[3])
    return reasons


def remove_latest_warn(target_id: int) -> bool:
    all_ids  = warn_sheet.col_values(3)
    last_row = None

    for i in range(len(all_ids) - 1, 3, -1):
        if all_ids[i] == str(target_id):
            last_row = i + 1
            break

    if last_row is None:
        return False

    warn_sheet.delete_rows(last_row)
    return True


# ─── Timeout Utilities ────────────────────────────────────

def get_timeout_count_this_week(target_id: int) -> int:
    all_values = timeout_sheet.get_all_values()
    now        = datetime.datetime.now(timezone.utc)
    one_week   = now - timedelta(weeks=1)
    count      = 0

    for row in all_values[4:]:
        if len(row) >= 4 and row[2] == str(target_id):
            date = parse_date(row[3])
            if date and date >= one_week:
                count += 1

    return count


# ─── Kick Utilities ───────────────────────────────────────

def get_kick_count_this_month(target_id: int) -> int:
    all_values = kick_sheet.get_all_values()
    now        = datetime.datetime.now(timezone.utc)
    one_month  = now - timedelta(days=30)
    count      = 0

    for row in all_values[4:]:
        if len(row) >= 4 and row[2] == str(target_id):
            date = parse_date(row[3])
            if date and date >= one_month:
                count += 1

    return count


# ─── View Log Utility ─────────────────────────────────────

def get_user_log(target_id: int) -> dict:
    result = {"warns": [], "timeouts": [], "kicks": [], "bans": []}

    for row in warn_sheet.get_all_values()[4:]:
        if len(row) >= 5 and row[2] == str(target_id):
            result["warns"].append({"date": row[3], "reason": row[3], "mod": row[5] if len(row) > 5 else "N/A"})

    for row in timeout_sheet.get_all_values()[4:]:
        if len(row) >= 6 and row[2] == str(target_id):
            result["timeouts"].append({"date": row[3], "reason": row[3], "duration": row[4], "mod": row[5] if len(row) > 5 else "N/A"})

    for row in kick_sheet.get_all_values()[4:]:
        if len(row) >= 5 and row[2] == str(target_id):
            result["kicks"].append({"date": row[3], "reason": row[3], "mod": row[4] if len(row) > 4 else "N/A"})

    for row in ban_sheet.get_all_values()[4:]:
        if len(row) >= 5 and row[2] == str(target_id):
            result["bans"].append({"date": row[3], "reason": row[3], "mod": row[4] if len(row) > 4 else "N/A"})

    return result


# ─── Events ───────────────────────────────────────────────

@bot.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Slash commands synced.")


# ─── Commands ─────────────────────────────────────────────

@tree.command(name="timeout", description="Time out a member.")
@app_commands.describe(
    member="The member to time out",
    duration="How long (as a number)",
    unit="Unit of time",
    reason="Reason for the timeout",
)
@app_commands.choices(unit=[
    app_commands.Choice(name="Seconds", value="s"),
    app_commands.Choice(name="Minutes", value="m"),
    app_commands.Choice(name="Hours",   value="h"),
    app_commands.Choice(name="Days",    value="d"),
])
async def timeout_member(
    interaction: discord.Interaction,
    member: discord.Member,
    duration: int,
    unit: app_commands.Choice[str],
    reason: str,
):
    await interaction.response.defer()
    log_action(interaction.user, f"/timeout @{member} {duration} {unit.name}", reason)

    unit_map = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
    delta    = timedelta(**{unit_map[unit.value]: duration})

    if delta.total_seconds() > 60 * 60 * 24 * 28:
        await interaction.followup.send("Timeout duration cannot exceed 28 days.", ephemeral=True)
        return

    try:
        await member.timeout(delta, reason=reason)
        log_timeout(interaction.user, member, duration, unit.name, reason)

        timeout_count = get_timeout_count_this_week(member.id)

        if timeout_count >= 5:
            try:
                await member.send(
                    f"You have been **kicked** from the server.\n"
                    f"Reason: You have been timed out **{timeout_count} times** in the last 7 days."
                )
            except discord.Forbidden:
                pass

            await member.kick(reason="Auto-kick: 5 timeouts in 1 week.")
            log_kick(interaction.user, member, "Auto-kick: 5 timeouts in 1 week.")
            log_action(interaction.user, f"/timeout @{member} [AUTO-KICK TRIGGERED]", "5 timeouts in 1 week")
            await interaction.followup.send(
                f"Timed out **{member}** for **{duration} {unit.name}**.\nReason: {reason}\n\n"
                f"⚠️ **{member}** has been **kicked** for receiving **{timeout_count} timeouts** in the last 7 days."
            )
        else:
            await interaction.followup.send(
                f"Timed out **{member}** for **{duration} {unit.name}**.\nReason: {reason}"
            )

    except discord.Forbidden:
        await interaction.followup.send("I don't have permission to timeout that member.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"Failed to timeout member: {e}", ephemeral=True)


@tree.command(name="untimeout", description="Remove a timeout from a member.")
@app_commands.describe(
    member="The member to un-timeout",
    reason="Reason for removing the timeout",
)
async def remove_timeout(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str,
):
    await interaction.response.defer()
    log_action(interaction.user, f"/untimeout @{member}", reason)

    if member.timed_out_until is None:
        await interaction.followup.send(f"**{member}** is not currently timed out.", ephemeral=True)
        return

    try:
        await member.timeout(None, reason=reason)
        await interaction.followup.send(f"Removed timeout from **{member}**.\nReason: {reason}")
    except discord.Forbidden:
        await interaction.followup.send("I don't have permission to modify that member.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"Failed to remove timeout: {e}", ephemeral=True)


@tree.command(name="warn", description="Warn a member.")
@app_commands.describe(
    member="The member to warn",
    reason="Reason for the warning",
)
async def warn_member(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str,
):
    await interaction.response.defer()
    log_action(interaction.user, f"/warn @{member}", reason)

    try:
        log_warn(interaction.user, member, reason)
        warn_count = get_warn_count(member.id)

        if warn_count == 2:
            try:
                await member.timeout(timedelta(hours=1), reason="Reached 2 warnings.")
                log_timeout(interaction.user, member, 1, "Hours", "Reached 2 warnings.")
                log_action(interaction.user, f"/warn @{member} [AUTO-TIMEOUT TRIGGERED]", "Reached 2 warnings")
                await interaction.followup.send(
                    f"Warned **{member}**.\nReason: {reason}\n\n"
                    f"⚠️ **{member}** has reached **2 warnings** and has been timed out for **1 hour**."
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    f"Warned **{member}**.\nReason: {reason}\n\n"
                    f"⚠️ **{member}** has reached **2 warnings** but I don't have permission to time them out."
                )

        elif warn_count >= 3:
            reasons      = get_warn_reasons(member.id)
            reasons_text = "\n".join(f"{i+1}. {r}" for i, r in enumerate(reasons))

            try:
                await member.send(
                    f"You have been **kicked** from the server for receiving **3 warnings**.\n\n"
                    f"**Your warnings:**\n{reasons_text}"
                )
            except discord.Forbidden:
                pass

            await member.kick(reason="Auto-kick: Received 3 warnings.")
            log_kick(interaction.user, member, "Auto-kick: Received 3 warnings.")
            log_action(interaction.user, f"/warn @{member} [AUTO-KICK TRIGGERED]", "Reached 3 warnings")

            kick_count = get_kick_count_this_month(member.id)

            if kick_count >= 3:
                try:
                    await member.send(
                        f"You have also been **banned** from the server.\n"
                        f"Reason: You have been kicked **{kick_count} times** in the last 30 days."
                    )
                except discord.Forbidden:
                    pass

                await interaction.guild.ban(member, reason="Auto-ban: 3 kicks in 1 month.")
                log_ban(interaction.user, member, "Auto-ban: 3 kicks in 1 month.")
                log_action(interaction.user, f"/warn @{member} [AUTO-BAN TRIGGERED]", "3 kicks in 1 month")
                await interaction.followup.send(
                    f"Warned **{member}**.\nReason: {reason}\n\n"
                    f"⚠️ **{member}** has reached **3 warnings** and has been **kicked**.\n"
                    f"⛔ They have also been **banned** for receiving **{kick_count} kicks** in the last 30 days."
                )
            else:
                await interaction.followup.send(
                    f"Warned **{member}**.\nReason: {reason}\n\n"
                    f"⚠️ **{member}** has reached **3 warnings** and has been **kicked** from the server."
                )

        else:
            await interaction.followup.send(
                f"Warned **{member}**.\nReason: {reason}\n"
                f"They now have **{warn_count}** warning(s)."
            )

    except Exception as e:
        await interaction.followup.send(f"Failed to log warning: {e}", ephemeral=True)


@tree.command(name="removewarn", description="Remove the most recent warning from a member.")
@app_commands.describe(
    member="The member to remove the warning from",
)
async def remove_warn(
    interaction: discord.Interaction,
    member: discord.Member,
):
    await interaction.response.defer()
    log_action(interaction.user, f"/removewarn @{member}", "N/A")

    try:
        removed = remove_latest_warn(member.id)

        if removed:
            warn_count = get_warn_count(member.id)
            await interaction.followup.send(
                f"Removed the most recent warning from **{member}**.\n"
                f"They now have **{warn_count}** warning(s)."
            )
        else:
            await interaction.followup.send(f"**{member}** has no warnings on record.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Failed to remove warning: {e}", ephemeral=True)


@tree.command(name="kick", description="Kick a member from the server.")
@app_commands.describe(
    member="The member to kick",
    reason="Reason for the kick",
)
async def kick_member(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str,
):
    await interaction.response.defer()
    log_action(interaction.user, f"/kick @{member}", reason)

    try:
        await member.kick(reason=reason)
        log_kick(interaction.user, member, reason)

        kick_count = get_kick_count_this_month(member.id)

        if kick_count >= 3:
            try:
                await member.send(
                    f"You have been **banned** from the server.\n"
                    f"Reason: You have been kicked **{kick_count} times** in the last 30 days."
                )
            except discord.Forbidden:
                pass

            await interaction.guild.ban(member, reason="Auto-ban: 3 kicks in 1 month.")
            log_ban(interaction.user, member, "Auto-ban: 3 kicks in 1 month.")
            log_action(interaction.user, f"/kick @{member} [AUTO-BAN TRIGGERED]", "3 kicks in 1 month")
            await interaction.followup.send(
                f"Kicked **{member}**.\nReason: {reason}\n\n"
                f"⚠️ **{member}** has been **banned** for receiving **{kick_count} kicks** in the last 30 days."
            )
        else:
            await interaction.followup.send(f"Kicked **{member}**.\nReason: {reason}")

    except discord.Forbidden:
        await interaction.followup.send("I don't have permission to kick that member.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"Failed to kick member: {e}", ephemeral=True)


@tree.command(name="ban", description="Ban a member from the server.")
@app_commands.describe(
    member="The member to ban",
    reason="Reason for the ban",
)
async def ban_member(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str,
):
    await interaction.response.defer()
    log_action(interaction.user, f"/ban @{member}", reason)

    try:
        await member.ban(reason=reason)
        log_ban(interaction.user, member, reason)
        await interaction.followup.send(f"Banned **{member}**.\nReason: {reason}")
    except discord.Forbidden:
        await interaction.followup.send("I don't have permission to ban that member.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"Failed to ban member: {e}", ephemeral=True)


@tree.command(name="unban", description="Unban a user from the server.")
@app_commands.describe(
    user_id="The ID of the user to unban",
    reason="Reason for the unban",
)
async def unban_member(
    interaction: discord.Interaction,
    user_id: str,
    reason: str,
):
    await interaction.response.defer()
    log_action(interaction.user, f"/unban {user_id}", reason)

    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=reason)
        await interaction.followup.send(f"Unbanned **{user}**.\nReason: {reason}")
    except ValueError:
        await interaction.followup.send("Invalid user ID provided.", ephemeral=True)
    except discord.NotFound:
        await interaction.followup.send("That user is not banned or does not exist.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("I don't have permission to unban members.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"Failed to unban member: {e}", ephemeral=True)


@tree.command(name="viewlogs", description="View the full moderation log for a user.")
@app_commands.describe(
    member="The member to view logs for",
)
async def view_logs(
    interaction: discord.Interaction,
    member: discord.Member,
):
    await interaction.response.defer(ephemeral=True)
    log_action(interaction.user, f"/viewlogs @{member}", "N/A")

    try:
        logs = get_user_log(member.id)

        warns    = logs["warns"]
        timeouts = logs["timeouts"]
        kicks    = logs["kicks"]
        bans     = logs["bans"]

        embed = discord.Embed(
            title=f"Moderation Log — {member}",
            color=discord.Color.orange(),
            timestamp=datetime.datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        if warns:
            warn_lines = "\n".join(f"`{i+1}.` {w['date']} — {w['reason']}" for i, w in enumerate(warns))
            embed.add_field(name=f"⚠️ Warnings ({len(warns)})", value=warn_lines, inline=False)
        else:
            embed.add_field(name="⚠️ Warnings (0)", value="None on record.", inline=False)

        if timeouts:
            timeout_lines = "\n".join(f"`{i+1}.` {t['date']} — {t['reason']} ({t['duration']})" for i, t in enumerate(timeouts))
            embed.add_field(name=f"⏱️ Timeouts ({len(timeouts)})", value=timeout_lines, inline=False)
        else:
            embed.add_field(name="⏱️ Timeouts (0)", value="None on record.", inline=False)

        if kicks:
            kick_lines = "\n".join(f"`{i+1}.` {k['date']} — {k['reason']}" for i, k in enumerate(kicks))
            embed.add_field(name=f"👢 Kicks ({len(kicks)})", value=kick_lines, inline=False)
        else:
            embed.add_field(name="👢 Kicks (0)", value="None on record.", inline=False)

        if bans:
            ban_lines = "\n".join(f"`{i+1}.` {b['date']} — {b['reason']}" for i, b in enumerate(bans))
            embed.add_field(name=f"⛔ Bans ({len(bans)})", value=ban_lines, inline=False)
        else:
            embed.add_field(name="⛔ Bans (0)", value="None on record.", inline=False)

        embed.set_footer(text=f"User ID: {member.id}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"Failed to retrieve logs: {e}", ephemeral=True)


bot.run(BOT_TOKEN)
