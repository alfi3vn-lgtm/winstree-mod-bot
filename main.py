import discord
from discord import app_commands
from datetime import timedelta, timezone
import gspread
from google.oauth2.service_account import Credentials
import datetime
import pytz
import os
import json
import collections

# ─── Config ───────────────────────────────────────────────
BOT_TOKEN  = os.environ["BOT_TOKEN"]
SHEET_NAME = os.environ.get("SHEET_NAME", "WA | Moderation Logs")

# Load Google credentials from the JSON env var
_service_account_info = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])

# Channel IDs to monitor for message logs
MONITORED_CHANNEL_IDS = {
    1484894338772107446,
    1484894415477801050,
    1485254833299787938,
    1484895222943973416,
    1484895286210855064,
    1485051111751815429,
    1488925835862737189,
    1489738603860066385,
}

# Channel ID where message logs will be sent
MESSAGE_LOG_CHANNEL_ID = 1493645055528014014

# Channel ID where moderation action logs will be sent
ACTION_LOG_CHANNEL_ID = 1493652621473349672

# ─── Spam Detection Config ────────────────────────────────
SPAM_MESSAGE_LIMIT   = 5
SPAM_WINDOW_SECONDS  = 5
SPAM_TIMEOUT_MINUTES = 10

_spam_tracker: dict[int, collections.deque] = {}
_spam_cooldown: set[int] = set()

# Members who were kicked or banned get a fresh session on next join.
# Voluntary leaves do NOT trigger a new session — warns carry over.
_flagged_for_new_session: set[int] = set()
# ──────────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds         = Credentials.from_service_account_info(_service_account_info, scopes=SCOPES)
gc            = gspread.authorize(creds)
timeout_sheet = gc.open(SHEET_NAME).worksheet("Timeout Logs")
warn_sheet    = gc.open(SHEET_NAME).worksheet("Warn Logs")
kick_sheet    = gc.open(SHEET_NAME).worksheet("Kick Logs")
ban_sheet     = gc.open(SHEET_NAME).worksheet("Ban Logs")
action_sheet  = gc.open(SHEET_NAME).worksheet("Moderator Action Log")
session_sheet = gc.open(SHEET_NAME).worksheet("Join Sessions")

intents                 = discord.Intents.default()
intents.members         = True
intents.message_content = True
intents.messages        = True

bot  = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

UK_TZ = pytz.timezone("Europe/London")


# ─── Helpers ──────────────────────────────────────────────

def get_next_row(worksheet):
    all_values = worksheet.col_values(2)
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


async def send_action_log(
    moderator: discord.User | discord.Member,
    command: str,
    reason: str = "N/A",
    target: discord.Member | None = None,
    color: discord.Color = discord.Color.blurple(),
    extra_fields: list[tuple[str, str]] | None = None,
):
    """Send a moderation action embed to the ACTION_LOG_CHANNEL_ID channel."""
    log_channel = bot.get_channel(ACTION_LOG_CHANNEL_ID)
    if log_channel is None:
        return

    now_uk = datetime.datetime.now(UK_TZ)

    embed = discord.Embed(
        title="🛡️ Moderation Action",
        color=color,
        timestamp=datetime.datetime.now(timezone.utc),
    )
    embed.add_field(name="Command",    value=f"`{command}`",                                                             inline=False)
    embed.add_field(name="Moderator",  value=f"{moderator.mention} — {moderator} (`{moderator.id}`)",                   inline=False)
    if target:
        embed.add_field(name="Target", value=f"{target.mention} — {target} (`{target.id}`)",                            inline=False)
    embed.add_field(name="Reason",     value=reason,                                                                     inline=False)
    if extra_fields:
        for name, value in extra_fields:
            embed.add_field(name=name, value=value, inline=False)
    embed.add_field(name="Time",       value=now_uk.strftime("%d/%m/%Y at %H:%M:%S"),                                   inline=False)

    if target:
        embed.set_thumbnail(url=target.display_avatar.url)
    else:
        embed.set_thumbnail(url=moderator.display_avatar.url)

    await log_channel.send(embed=embed)


def format_timestamp(dt: datetime.datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    uk_time = dt.astimezone(UK_TZ)
    return uk_time.strftime("%d/%m/%Y at %H:%M:%S")


# ─── Session Utilities ────────────────────────────────────
# The "Join Sessions" sheet tracks each time a member gets a new session.
# A new session is only created when a member is kicked or banned —
# voluntary leaves carry the same session forward.
# Columns: B=UserID, C=SessionID, D=Date
# Warns are tied to a session so kicks/bans give a fresh 3-warn slate.

def get_current_session_id(target_id: int) -> int:
    """Return the latest session ID for this user, or 1 if they have none."""
    all_values = session_sheet.get_all_values()
    latest = 0
    for row in all_values[4:]:
        if len(row) >= 3 and row[1] == str(target_id):
            try:
                sid = int(row[2])
                if sid > latest:
                    latest = sid
            except ValueError:
                pass
    return latest if latest > 0 else 1


def create_new_session(target_id: int) -> int:
    """
    Create a new session entry for a user.
    Only called when a user rejoins after being kicked or banned.
    Returns the new session ID.
    """
    current = get_current_session_id(target_id)
    new_sid  = current + 1
    next_row = get_next_row(session_sheet)
    date_str = datetime.datetime.now(timezone.utc).strftime("%d/%m/%Y")

    session_sheet.update(
        values=[[str(target_id), str(new_sid), date_str]],
        range_name=f"B{next_row}:D{next_row}"
    )
    return new_sid


def ensure_session_exists(target_id: int) -> int:
    """
    Ensure a user has at least one session on record.
    Used the first time a user is warned so users who joined
    before the session system was added still work correctly.
    Returns the current session ID.
    """
    all_values = session_sheet.get_all_values()
    for row in all_values[4:]:
        if len(row) >= 2 and row[1] == str(target_id):
            return get_current_session_id(target_id)

    # No session found — create session 1
    next_row = get_next_row(session_sheet)
    date_str = datetime.datetime.now(timezone.utc).strftime("%d/%m/%Y")
    session_sheet.update(
        values=[[str(target_id), "1", date_str]],
        range_name=f"B{next_row}:D{next_row}"
    )
    return 1


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
    """Log a warning tied to the user's current session."""
    next_row   = get_next_row(warn_sheet)
    date_str   = datetime.datetime.now(timezone.utc).strftime("%d/%m/%Y")
    session_id = ensure_session_exists(target.id)

    # Columns: B=Username, C=UserID, D=Date, E=Reason, F=ModID, G=SessionID
    warn_sheet.update(
        values=[
            [str(target), str(target.id), date_str, reason, str(moderator.id), str(session_id)]
        ],
        range_name=f"B{next_row}:G{next_row}"
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
    """Count warns for the user's CURRENT session only."""
    session_id = get_current_session_id(target_id)
    all_values = warn_sheet.get_all_values()
    count = 0
    for row in all_values[4:]:
        # Column C (index 1) = UserID, Column G (index 5) = SessionID
        if len(row) >= 6 and row[2] == str(target_id):
            try:
                if int(row[6]) == session_id:
                    count += 1
            except (ValueError, IndexError):
                pass
    return count


def get_warn_reasons(target_id: int) -> list[str]:
    """Get warn reasons for the user's CURRENT session only."""
    session_id = get_current_session_id(target_id)
    rows       = warn_sheet.get_all_values()
    reasons    = []
    for row in rows[4:]:
        if len(row) >= 6 and row[2] == str(target_id):
            try:
                if int(row[6]) == session_id:
                    reasons.append(row[3])
            except (ValueError, IndexError):
                pass
    return reasons


def remove_latest_warn(target_id: int) -> bool:
    """Remove the most recent warn for the user's CURRENT session."""
    session_id = get_current_session_id(target_id)
    all_values = warn_sheet.get_all_values()
    last_row   = None

    for i in range(len(all_values) - 1, 3, -1):
        row = all_values[i]
        if len(row) >= 6 and row[2] == str(target_id):
            try:
                if int(row[6]) == session_id:
                    last_row = i + 1
                    break
            except (ValueError, IndexError):
                pass

    if last_row is None:
        return False

    warn_sheet.delete_rows(last_row)
    return True


def get_all_warn_count(target_id: int) -> int:
    """Count ALL warns across all sessions (for viewlogs)."""
    all_ids = warn_sheet.col_values(3)
    return sum(1 for uid in all_ids[4:] if uid == str(target_id))


def get_all_warn_reasons(target_id: int) -> list[dict]:
    """Get all warns across all sessions with session info (for viewlogs)."""
    rows    = warn_sheet.get_all_values()
    results = []
    for row in rows[4:]:
        if len(row) >= 6 and row[2] == str(target_id):
            results.append({
                "date":       row[3],
                "reason":     row[3],
                "mod":        row[4] if len(row) > 4 else "N/A",
                "session_id": row[5] if len(row) > 5 else "?",
            })
    return results


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
            result["warns"].append({
                "date":       row[3],
                "reason":     row[3],
                "mod":        row[4] if len(row) > 4 else "N/A",
                "session_id": row[5] if len(row) > 5 else "?",
            })

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


# ─── Spam Detection ───────────────────────────────────────

def is_spamming(user_id: int) -> bool:
    now    = datetime.datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=SPAM_WINDOW_SECONDS)

    if user_id not in _spam_tracker:
        _spam_tracker[user_id] = collections.deque()

    dq = _spam_tracker[user_id]
    dq.append(now)

    while dq and dq[0] < cutoff:
        dq.popleft()

    return len(dq) > SPAM_MESSAGE_LIMIT


# ─── Events ───────────────────────────────────────────────

@bot.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Slash commands synced.")


@bot.event
async def on_member_join(member: discord.Member):
    """
    When a member rejoins, only create a new session if they were previously
    kicked or banned. Voluntary leaves carry the same session forward so warns
    are not wiped by simply leaving and rejoining.
    """
    if member.id in _flagged_for_new_session:
        _flagged_for_new_session.discard(member.id)
        new_sid = create_new_session(member.id)
        print(f"[SESSION] New session ({new_sid}) created for {member} ({member.id}) — rejoined after kick/ban.")
    else:
        current_sid = get_current_session_id(member.id)
        print(f"[SESSION] {member} ({member.id}) rejoined voluntarily — keeping session {current_sid}, warns unchanged.")


@bot.event
async def on_message(message: discord.Message):
    """Detects spam in monitored channels and auto-times-out the offender."""
    if message.author.bot:
        return
    if message.channel.id not in MONITORED_CHANNEL_IDS:
        return

    user_id = message.author.id

    if user_id in _spam_cooldown:
        return

    if is_spamming(user_id):
        _spam_cooldown.add(user_id)
        try:
            member = message.guild.get_member(user_id)
            if member is None:
                return

            delta  = timedelta(minutes=SPAM_TIMEOUT_MINUTES)
            reason = f"Auto-timeout: Sent more than {SPAM_MESSAGE_LIMIT} messages in {SPAM_WINDOW_SECONDS} seconds (spam detection)."

            await member.timeout(delta, reason=reason)

            log_timeout(bot.user, member, SPAM_TIMEOUT_MINUTES, "Minutes", reason)
            log_action(bot.user, f"[AUTO-TIMEOUT] @{member} — spam detection", reason)

            await send_action_log(
                moderator=bot.user,
                command=f"[AUTO-TIMEOUT] @{member}",
                reason=reason,
                target=member,
                color=discord.Color.red(),
                extra_fields=[("Duration", f"{SPAM_TIMEOUT_MINUTES} Minutes")],
            )

            await message.channel.send(
                f"🚨 {member.mention} has been timed out for **{SPAM_TIMEOUT_MINUTES} minutes** for spamming.",
                delete_after=10,
            )

            try:
                await member.send(
                    f"You have been timed out in **{message.guild.name}** for **{SPAM_TIMEOUT_MINUTES} minutes**.\n"
                    f"Reason: {reason}"
                )
            except discord.Forbidden:
                pass

            _spam_tracker.pop(user_id, None)

        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass
        finally:
            _spam_cooldown.discard(user_id)


@bot.event
async def on_message_delete(message: discord.Message):
    """Fires when a message is deleted in a monitored channel."""
    if message.author.bot:
        return
    if message.channel.id not in MONITORED_CHANNEL_IDS:
        return

    log_channel = bot.get_channel(MESSAGE_LOG_CHANNEL_ID)
    if log_channel is None:
        return

    now_uk      = datetime.datetime.now(UK_TZ)
    sent_str    = format_timestamp(message.created_at)
    deleted_str = now_uk.strftime("%d/%m/%Y at %H:%M:%S")

    deleted_by = "Unknown"
    try:
        async for entry in message.guild.audit_logs(
            limit=5,
            action=discord.AuditLogAction.message_delete
        ):
            if (
                entry.target.id == message.author.id
                and entry.extra.channel.id == message.channel.id
            ):
                deleted_by = f"{entry.user} (`{entry.user.id}`)"
                break
    except (discord.Forbidden, discord.HTTPException):
        pass

    content = message.content or "*[No text content — may have been an embed or attachment]*"
    if len(content) > 1024:
        content = content[:1021] + "..."

    embed = discord.Embed(
        title="🗑️ Message Deleted",
        color=discord.Color.red(),
        timestamp=datetime.datetime.now(timezone.utc),
    )
    embed.add_field(name="Author",          value=f"{message.author.mention} — {message.author} (`{message.author.id}`)", inline=False)
    embed.add_field(name="Channel",         value=f"{message.channel.mention} (`{message.channel.id}`)", inline=False)
    embed.add_field(name="Deleted By",      value=deleted_by, inline=False)
    embed.add_field(name="Message Content", value=content, inline=False)
    embed.add_field(name="Message Sent",    value=sent_str, inline=True)
    embed.add_field(name="Deleted At",      value=deleted_str, inline=True)

    if message.attachments:
        attachment_links = "\n".join(a.proxy_url for a in message.attachments)
        embed.add_field(name=f"Attachments ({len(message.attachments)})", value=attachment_links[:1024], inline=False)

    embed.set_thumbnail(url=message.author.display_avatar.url)
    embed.set_footer(text=f"Message ID: {message.id}")
    await log_channel.send(embed=embed)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    """Fires when a message is edited in a monitored channel."""
    if before.author.bot:
        return
    if before.channel.id not in MONITORED_CHANNEL_IDS:
        return
    if before.content == after.content:
        return

    log_channel = bot.get_channel(MESSAGE_LOG_CHANNEL_ID)
    if log_channel is None:
        return

    sent_str   = format_timestamp(before.created_at)
    edited_str = format_timestamp(after.edited_at or datetime.datetime.now(timezone.utc))

    before_content = before.content or "*[No text content]*"
    after_content  = after.content  or "*[No text content]*"

    if len(before_content) > 1024:
        before_content = before_content[:1021] + "..."
    if len(after_content) > 1024:
        after_content = after_content[:1021] + "..."

    embed = discord.Embed(
        title="✏️ Message Edited",
        color=discord.Color.orange(),
        timestamp=datetime.datetime.now(timezone.utc),
    )
    embed.add_field(name="Author",          value=f"{before.author.mention} — {before.author} (`{before.author.id}`)", inline=False)
    embed.add_field(name="Channel",         value=f"{before.channel.mention} (`{before.channel.id}`)", inline=False)
    embed.add_field(name="Before",          value=before_content, inline=False)
    embed.add_field(name="After",           value=after_content, inline=False)
    embed.add_field(name="Message Sent",    value=sent_str, inline=True)
    embed.add_field(name="Edited At",       value=edited_str, inline=True)
    embed.add_field(name="Jump to Message", value=f"[Click here]({after.jump_url})", inline=False)

    embed.set_thumbnail(url=before.author.display_avatar.url)
    embed.set_footer(text=f"Message ID: {before.id}")
    await log_channel.send(embed=embed)


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

            # Flag for a fresh session when they rejoin
            _flagged_for_new_session.add(member.id)

            await send_action_log(
                moderator=interaction.user,
                command=f"/timeout @{member} {duration} {unit.name}",
                reason=reason,
                target=member,
                color=discord.Color.red(),
                extra_fields=[
                    ("Duration", f"{duration} {unit.name}"),
                    ("⚠️ Auto-Kick Triggered", f"{member} has been kicked for receiving {timeout_count} timeouts in the last 7 days."),
                ],
            )

            await interaction.followup.send(
                f"Timed out **{member}** for **{duration} {unit.name}**.\nReason: {reason}\n\n"
                f"⚠️ **{member}** has been **kicked** for receiving **{timeout_count} timeouts** in the last 7 days."
            )
        else:
            await send_action_log(
                moderator=interaction.user,
                command=f"/timeout @{member} {duration} {unit.name}",
                reason=reason,
                target=member,
                color=discord.Color.yellow(),
                extra_fields=[("Duration", f"{duration} {unit.name}")],
            )

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

        await send_action_log(
            moderator=interaction.user,
            command=f"/untimeout @{member}",
            reason=reason,
            target=member,
            color=discord.Color.green(),
        )

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
        warn_count = get_warn_count(member.id)  # Only counts current session warns

        if warn_count == 2:
            try:
                await member.timeout(timedelta(hours=1), reason="Reached 2 warnings.")
                log_timeout(interaction.user, member, 1, "Hours", "Reached 2 warnings.")
                log_action(interaction.user, f"/warn @{member} [AUTO-TIMEOUT TRIGGERED]", "Reached 2 warnings")

                await send_action_log(
                    moderator=interaction.user,
                    command=f"/warn @{member}",
                    reason=reason,
                    target=member,
                    color=discord.Color.orange(),
                    extra_fields=[
                        ("Warn Count (This Session)", str(warn_count)),
                        ("⚠️ Auto-Timeout Triggered", f"{member} has been timed out for 1 hour for reaching 2 warnings."),
                    ],
                )

                await interaction.followup.send(
                    f"Warned **{member}**.\nReason: {reason}\n\n"
                    f"⚠️ **{member}** has reached **2 warnings** this session and has been timed out for **1 hour**."
                )
            except discord.Forbidden:
                await send_action_log(
                    moderator=interaction.user,
                    command=f"/warn @{member}",
                    reason=reason,
                    target=member,
                    color=discord.Color.orange(),
                    extra_fields=[
                        ("Warn Count (This Session)", str(warn_count)),
                        ("⚠️ Auto-Timeout Failed", "Missing permissions to timeout."),
                    ],
                )
                await interaction.followup.send(
                    f"Warned **{member}**.\nReason: {reason}\n\n"
                    f"⚠️ **{member}** has reached **2 warnings** but I don't have permission to time them out."
                )

        elif warn_count >= 3:
            reasons      = get_warn_reasons(member.id)
            reasons_text = "\n".join(f"{i+1}. {r}" for i, r in enumerate(reasons))

            try:
                await member.send(
                    f"You have been **kicked** from the server for receiving **3 warnings** this session.\n\n"
                    f"**Your warnings this session:**\n{reasons_text}"
                )
            except discord.Forbidden:
                pass

            await member.kick(reason="Auto-kick: Received 3 warnings this session.")
            log_kick(interaction.user, member, "Auto-kick: Received 3 warnings this session.")
            log_action(interaction.user, f"/warn @{member} [AUTO-KICK TRIGGERED]", "Reached 3 warnings this session")

            # Flag for a fresh session when they rejoin
            _flagged_for_new_session.add(member.id)

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

                # Ban supersedes kick flag — still flagged, flag remains
                _flagged_for_new_session.add(member.id)

                await send_action_log(
                    moderator=interaction.user,
                    command=f"/warn @{member}",
                    reason=reason,
                    target=member,
                    color=discord.Color.dark_red(),
                    extra_fields=[
                        ("Warn Count (This Session)", str(warn_count)),
                        ("⚠️ Auto-Kick Triggered", "Reached 3 warnings this session."),
                        ("⛔ Auto-Ban Triggered", f"Received {kick_count} kicks in the last 30 days."),
                    ],
                )

                await interaction.followup.send(
                    f"Warned **{member}**.\nReason: {reason}\n\n"
                    f"⚠️ **{member}** has reached **3 warnings** this session and has been **kicked**.\n"
                    f"⛔ They have also been **banned** for receiving **{kick_count} kicks** in the last 30 days."
                )
            else:
                await send_action_log(
                    moderator=interaction.user,
                    command=f"/warn @{member}",
                    reason=reason,
                    target=member,
                    color=discord.Color.red(),
                    extra_fields=[
                        ("Warn Count (This Session)", str(warn_count)),
                        ("⚠️ Auto-Kick Triggered", "Reached 3 warnings this session."),
                    ],
                )

                await interaction.followup.send(
                    f"Warned **{member}**.\nReason: {reason}\n\n"
                    f"⚠️ **{member}** has reached **3 warnings** this session and has been **kicked** from the server."
                )

        else:
            await send_action_log(
                moderator=interaction.user,
                command=f"/warn @{member}",
                reason=reason,
                target=member,
                color=discord.Color.yellow(),
                extra_fields=[("Warn Count (This Session)", str(warn_count))],
            )

            await interaction.followup.send(
                f"Warned **{member}**.\nReason: {reason}\n"
                f"They now have **{warn_count}** warning(s) this session."
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

            await send_action_log(
                moderator=interaction.user,
                command=f"/removewarn @{member}",
                reason="N/A",
                target=member,
                color=discord.Color.green(),
                extra_fields=[("Remaining Warnings (This Session)", str(warn_count))],
            )

            await interaction.followup.send(
                f"Removed the most recent warning from **{member}**.\n"
                f"They now have **{warn_count}** warning(s) this session."
            )
        else:
            await interaction.followup.send(f"**{member}** has no warnings on record for their current session.", ephemeral=True)
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

        # Flag for a fresh session when they rejoin
        _flagged_for_new_session.add(member.id)

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

            # Already flagged from kick above; flag remains for the ban too
            _flagged_for_new_session.add(member.id)

            await send_action_log(
                moderator=interaction.user,
                command=f"/kick @{member}",
                reason=reason,
                target=member,
                color=discord.Color.dark_red(),
                extra_fields=[
                    ("⚠️ Auto-Ban Triggered", f"Received {kick_count} kicks in the last 30 days."),
                ],
            )

            await interaction.followup.send(
                f"Kicked **{member}**.\nReason: {reason}\n\n"
                f"⚠️ **{member}** has been **banned** for receiving **{kick_count} kicks** in the last 30 days."
            )
        else:
            await send_action_log(
                moderator=interaction.user,
                command=f"/kick @{member}",
                reason=reason,
                target=member,
                color=discord.Color.red(),
            )

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

        # Flag for a fresh session when they are unbanned and rejoin
        _flagged_for_new_session.add(member.id)

        await send_action_log(
            moderator=interaction.user,
            command=f"/ban @{member}",
            reason=reason,
            target=member,
            color=discord.Color.dark_red(),
        )

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

        await send_action_log(
            moderator=interaction.user,
            command=f"/unban {user_id}",
            reason=reason,
            color=discord.Color.green(),
            extra_fields=[("Unbanned User", f"{user} (`{user.id}`)")],
        )

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

    await send_action_log(
        moderator=interaction.user,
        command=f"/viewlogs @{member}",
        reason="N/A",
        target=member,
        color=discord.Color.blurple(),
    )

    try:
        logs = get_user_log(member.id)

        warns    = logs["warns"]
        timeouts = logs["timeouts"]
        kicks    = logs["kicks"]
        bans     = logs["bans"]

        current_session = get_current_session_id(member.id)

        embed = discord.Embed(
            title=f"Moderation Log — {member}",
            color=discord.Color.orange(),
            timestamp=datetime.datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="📋 Session Info",
            value=f"Current session: **{current_session}** (warns reset on kick/ban, not on voluntary leave)",
            inline=False
        )

        if warns:
            # Show session number alongside each warn
            warn_lines = "\n".join(
                f"`{i+1}.` {w['date']} — {w['reason']} *(Session {w.get('session_id', '?')})*"
                for i, w in enumerate(warns)
            )
            embed.add_field(name=f"⚠️ Warnings ({len(warns)} all-time)", value=warn_lines[:1024], inline=False)
        else:
            embed.add_field(name="⚠️ Warnings (0)", value="None on record.", inline=False)

        if timeouts:
            timeout_lines = "\n".join(f"`{i+1}.` {t['date']} — {t['reason']} ({t['duration']})" for i, t in enumerate(timeouts))
            embed.add_field(name=f"⏱️ Timeouts ({len(timeouts)})", value=timeout_lines[:1024], inline=False)
        else:
            embed.add_field(name="⏱️ Timeouts (0)", value="None on record.", inline=False)

        if kicks:
            kick_lines = "\n".join(f"`{i+1}.` {k['date']} — {k['reason']}" for i, k in enumerate(kicks))
            embed.add_field(name=f"👢 Kicks ({len(kicks)})", value=kick_lines[:1024], inline=False)
        else:
            embed.add_field(name="👢 Kicks (0)", value="None on record.", inline=False)

        if bans:
            ban_lines = "\n".join(f"`{i+1}.` {b['date']} — {b['reason']}" for i, b in enumerate(bans))
            embed.add_field(name=f"⛔ Bans ({len(bans)})", value=ban_lines[:1024], inline=False)
        else:
            embed.add_field(name="⛔ Bans (0)", value="None on record.", inline=False)

        embed.set_footer(text=f"User ID: {member.id}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"Failed to retrieve logs: {e}", ephemeral=True)


bot.run(BOT_TOKEN)
