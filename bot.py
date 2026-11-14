import asyncio
import logging
import os
import random
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

TOKEN = os.getenv("DISCORD_TOKEN")
VERSION = "2.0.0"
ALLOWED_GUILD_IDS = {int(x.strip()) for x in os.getenv("ALLOWED_GUILD_IDS", "1507663008292405339,1571079983596830810").split(",") if x.strip().isdigit()}
OWNER_IDS = {int(x.strip()) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip().isdigit()}
DB_PATH = os.getenv("DATABASE_PATH", "nexora.db")
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

BLACK, SILVER, DARK_SILVER = 0x111318, 0xC0C5CE, 0x343A40
PURGE_OPTIONS = {3, 10}
COOLDOWN_SECONDS = 300

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("nexora")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


def now():
    return datetime.now(timezone.utc)


def stamp():
    return now().isoformat()


def db():
    connection = sqlite3.connect(DB_PATH, timeout=20)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    # Additive migration only: existing users, listings, joins, cooldowns and logs remain intact.
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY,tier TEXT NOT NULL DEFAULT 'normal',verified_joins INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS listings(server_id INTEGER PRIMARY KEY,owner_id INTEGER NOT NULL,invite_url TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',description TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS joins(id INTEGER PRIMARY KEY AUTOINCREMENT,server_id INTEGER NOT NULL,member_id INTEGER NOT NULL,joined_at TEXT NOT NULL,UNIQUE(server_id,member_id));
        CREATE TABLE IF NOT EXISTS cooldowns(user_id INTEGER PRIMARY KEY,last_submit TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT,actor_id INTEGER NOT NULL,action TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS verified_servers(server_id INTEGER PRIMARY KEY,owner_id INTEGER NOT NULL,verified_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,server_id INTEGER NOT NULL,user_id INTEGER NOT NULL,rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),review TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,UNIQUE(server_id,user_id));
        CREATE TABLE IF NOT EXISTS partners(server_id INTEGER PRIMARY KEY,owner_id INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS giveaways(message_id INTEGER PRIMARY KEY,channel_id INTEGER NOT NULL,guild_id INTEGER NOT NULL,host_id INTEGER NOT NULL,prize TEXT NOT NULL,winners_count INTEGER NOT NULL DEFAULT 1,ends_at TEXT NOT NULL,ended INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS giveaway_entries(message_id INTEGER NOT NULL,user_id INTEGER NOT NULL,PRIMARY KEY(message_id,user_id));
        CREATE TABLE IF NOT EXISTS settings(setting_key TEXT PRIMARY KEY,setting_value TEXT NOT NULL);
        """)
        c.execute("INSERT OR IGNORE INTO settings(setting_key,setting_value) VALUES('purge_seconds','10')")
        c.execute("INSERT OR IGNORE INTO settings(setting_key,setting_value) VALUES('maintenance','off')")
        c.commit()


def get_setting(key, default=None):
    with db() as c:
        row = c.execute("SELECT setting_value FROM settings WHERE setting_key=?", (key,)).fetchone()
    return row["setting_value"] if row else default


def set_setting(key, value):
    with db() as c:
        c.execute("INSERT INTO settings(setting_key,setting_value) VALUES(?,?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value", (key, str(value)))
        c.commit()


def ensure_user(user_id):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO users(user_id,created_at) VALUES(?,?)", (user_id, stamp()))
        c.commit()


def is_owner(ctx):
    return ctx.author.id in OWNER_IDS


def audit(actor_id, action, details=""):
    with db() as c:
        c.execute("INSERT INTO logs(actor_id,action,details,created_at) VALUES(?,?,?,?)", (actor_id, action, str(details)[:400], stamp()))
        c.commit()


async def reply_embed(ctx, title, description="", *, fields=None, color=BLACK):
    embed = discord.Embed(title=f"NEXORA | {title}", description=str(description)[:4000], color=color, timestamp=now())
    embed.set_author(name="NEXORA COMMUNITY NETWORK")
    embed.set_footer(text=f"BLACK + SILVER  |  v{VERSION}  |  Discover. Connect. Belong.")
    for name, value, inline in fields or []:
        embed.add_field(name=str(name)[:256], value=str(value)[:1024], inline=inline)
    return await ctx.reply(embed=embed, mention_author=False)


async def error_embed(ctx, text):
    return await reply_embed(ctx, "Notice", text, color=DARK_SILVER)


async def purge_submission(command_message, response_message):
    await asyncio.sleep(int(get_setting("purge_seconds", 10)))
    for message in (command_message, response_message):
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


async def djoin_reply(ctx, title, text):
    response = await reply_embed(ctx, title, text)
    asyncio.create_task(purge_submission(ctx.message, response))
    return response


def parse_duration(value):
    match = re.fullmatch(r"(\d+)(s|m|h|d)", value.lower())
    if not match:
        return None
    seconds = int(match.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    return seconds if 30 <= seconds <= 30 * 86400 else None


@bot.check
async def authorized_server_check(ctx):
    if ctx.guild is None or ctx.guild.id not in ALLOWED_GUILD_IDS:
        raise commands.CheckFailure("Restricted server.")
    if get_setting("maintenance", "off") == "on" and not is_owner(ctx):
        raise commands.CheckFailure("Maintenance mode.")
    return True


@bot.event
async def on_ready():
    init_db()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Serving Fire + NEXORA"))
    if not giveaway_watcher.is_running():
        giveaway_watcher.start()
    log.info("NEXORA %s online as %s", VERSION, bot.user)


@bot.event
async def on_member_join(member):
    if member.guild.id not in ALLOWED_GUILD_IDS:
        return
    with db() as c:
        row = c.execute("SELECT owner_id FROM listings WHERE server_id=? AND status='approved'", (member.guild.id,)).fetchone()
        if row:
            cur = c.execute("INSERT OR IGNORE INTO joins(server_id,member_id,joined_at) VALUES(?,?,?)", (member.guild.id, member.id, stamp()))
            if cur.rowcount:
                c.execute("UPDATE users SET verified_joins=verified_joins+1 WHERE user_id=?", (row["owner_id"],))
        c.commit()


@bot.event
async def on_raw_reaction_add(payload):
    if str(payload.emoji) != "🎉" or (bot.user and payload.user_id == bot.user.id):
        return
    with db() as c:
        row = c.execute("SELECT ended FROM giveaways WHERE message_id=?", (payload.message_id,)).fetchone()
        if row and not row["ended"]:
            c.execute("INSERT OR IGNORE INTO giveaway_entries(message_id,user_id) VALUES(?,?)", (payload.message_id, payload.user_id))
            c.commit()


async def finish_giveaway(message_id, reroll=False):
    with db() as c:
        giveaway = c.execute("SELECT * FROM giveaways WHERE message_id=?", (message_id,)).fetchone()
        if not giveaway:
            return None, "Giveaway not found."
        if giveaway["ended"] and not reroll:
            return None, "That giveaway has already ended."
        entries = c.execute("SELECT user_id FROM giveaway_entries WHERE message_id=?", (message_id,)).fetchall()
        pool = [row["user_id"] for row in entries]
        random.shuffle(pool)
        winners = pool[:giveaway["winners_count"]]
        if not reroll:
            c.execute("UPDATE giveaways SET ended=1 WHERE message_id=?", (message_id,))
        c.commit()
    channel = bot.get_channel(giveaway["channel_id"])
    if channel:
        try:
            message = await channel.fetch_message(message_id)
            embed = message.embeds[0] if message.embeds else discord.Embed()
            embed.title = "NEXORA | Giveaway Ended"
            embed.description = f"Prize: {giveaway['prize']}\\nEntries: {len(entries)}"
            embed.color = SILVER
            embed.add_field(name="Winner(s)", value=", ".join(f"<@{uid}>" for uid in winners) if winners else "No valid entries", inline=False)
            await message.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    return winners, None


@tasks.loop(seconds=20)
async def giveaway_watcher():
    with db() as c:
        rows = c.execute("SELECT message_id FROM giveaways WHERE ended=0 AND ends_at<=?", (stamp(),)).fetchall()
    for row in rows:
        await finish_giveaway(row["message_id"])


@bot.command(name="dhelp")
async def dhelp(ctx):
    fields = [
        ("!djoin SERVER_ID", "Verify and automatically publish a server."),
        ("!dverify SERVER_ID", "Check target-server owner/manager permissions."),
        ("!dstatus", "View your listings."),
        ("!dservers", "Browse published communities."),
        ("!dinfo SERVER_ID", "View a community and its rating."),
        ("!dremove [SERVER_ID]", "Request removal of your listing."),
        ("!dprofile", "View your owner profile."),
        ("!ddashboard", "Open your owner dashboard."),
        ("!dleaderboard", "View the network leaderboard."),
        ("!dvote SERVER_ID 1-5 [review]", "Rate a published community."),
        ("!dpartner SERVER_ID", "Apply for partner status."),
        ("!dpartners", "View network partners."),
        ("!dgiveaway 10m 1 Prize", "Create a giveaway; 30 seconds to 30 days."),
        ("!dstats", "View network statistics."),
        ("!dpurge 3|10", "Set auto-purge delay (network owner)."),
        ("Owner tools", "!dapprove, !dreject, !dtier, !daudit, !dmaintenance, !dendgiveaway, !dreroll"),
    ]
    if bot.user:
        fields.append(("Invite NEXORA", f"[Add the bot](https://discord.com/oauth2/authorize?client_id={bot.user.id}&scope=bot%20applications.commands&permissions=93184)"))
    await reply_embed(ctx, "Command Guide", "Black + silver community network.", fields=fields)


@bot.command(name="djoin")
async def djoin(ctx, server_id: str = ""):
    if not server_id.strip().isdigit():
        return await djoin_reply(ctx, "Submission Not Accepted", "Usage: !djoin SERVER_ID (digits only).")
    server_id = int(server_id.strip())
    guild = bot.get_guild(server_id)
    if guild is None:
        return await djoin_reply(ctx, "Verification Required", "NEXORA must be installed in the target server to verify permissions and create its invite.")
    member = guild.get_member(ctx.author.id)
    if member is None or not (member.id == guild.owner_id or member.guild_permissions.manage_guild):
        return await djoin_reply(ctx, "Verification Failed", "You must own the target server or have Manage Server permission there.")
    with db() as c:
        old = c.execute("SELECT last_submit FROM cooldowns WHERE user_id=?", (ctx.author.id,)).fetchone()
        if old:
            elapsed = (now() - datetime.fromisoformat(old["last_submit"])).total_seconds()
            if elapsed < COOLDOWN_SECONDS:
                return await djoin_reply(ctx, "Anti-Spam Cooldown", f"Try again in {int(COOLDOWN_SECONDS-elapsed)} seconds.")
        prior = c.execute("SELECT owner_id FROM listings WHERE server_id=?", (server_id,)).fetchone()
        if prior and prior["owner_id"] != ctx.author.id and not is_owner(ctx):
            return await djoin_reply(ctx, "Already Listed", "This server already has a different listing owner.")
    channel = next((ch for ch in guild.text_channels if ch.permissions_for(guild.me).view_channel and ch.permissions_for(guild.me).create_instant_invite), None)
    if channel is None:
        return await djoin_reply(ctx, "Invite Permission Missing", "Give NEXORA View Channel and Create Instant Invite in one target-server text channel.")
    try:
        invite = await channel.create_invite(max_age=0, max_uses=0, unique=True, reason=f"NEXORA listing verified by {ctx.author}")
    except discord.HTTPException:
        return await djoin_reply(ctx, "Invite Creation Failed", "I couldn't create an invite. Check my permissions and try again.")
    description = (guild.description or "Community listing")[:1000]
    with db() as c:
        c.execute("""INSERT INTO listings(server_id,owner_id,invite_url,status,description,created_at,updated_at)
        VALUES(?,?,?,'approved',?,?,?) ON CONFLICT(server_id) DO UPDATE SET owner_id=excluded.owner_id,invite_url=excluded.invite_url,status='approved',description=excluded.description,updated_at=excluded.updated_at""",
        (server_id, ctx.author.id, invite.url, description, stamp(), stamp()))
        c.execute("INSERT INTO verified_servers(server_id,owner_id,verified_at) VALUES(?,?,?) ON CONFLICT(server_id) DO UPDATE SET owner_id=excluded.owner_id,verified_at=excluded.verified_at", (server_id, ctx.author.id, stamp()))
        c.execute("INSERT INTO cooldowns(user_id,last_submit) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET last_submit=excluded.last_submit", (ctx.author.id, stamp()))
        c.commit()
    ensure_user(ctx.author.id)
    audit(ctx.author.id, "listing_published", server_id)
    return await djoin_reply(ctx, "Listing Published", f"**{guild.name}** is now live.\\nServer ID: {server_id}\\n[Join community]({invite.url})\\n\\nMembers choose whether to join. No one is added automatically.")


@bot.command(name="dverify")
async def dverify(ctx, server_id: int):
    guild = bot.get_guild(server_id)
    member = guild.get_member(ctx.author.id) if guild else None
    if not member or not (member.id == guild.owner_id or member.guild_permissions.manage_guild):
        return await error_embed(ctx, "NEXORA must be installed in that server, and you must own it or have Manage Server permission.")
    await reply_embed(ctx, "Server Verified", f"{guild.name} is verified for your account. Use !djoin {server_id} to publish.")


@bot.command(name="dstatus")
async def dstatus(ctx):
    with db() as c:
        rows = c.execute("SELECT server_id,status,updated_at FROM listings WHERE owner_id=? ORDER BY updated_at DESC LIMIT 15", (ctx.author.id,)).fetchall()
    if not rows:
        return await reply_embed(ctx, "Your Listings", "No listings yet. Use !djoin SERVER_ID.")
    await reply_embed(ctx, "Your Listings", "Latest listings.", fields=[(str(r["server_id"]), f"Status: {r['status'].title()}\\nUpdated: {r['updated_at'][:10]}", False) for r in rows])


@bot.command(name="dservers")
async def dservers(ctx):
    with db() as c:
        rows = c.execute("""SELECT l.server_id,l.invite_url,l.description,COALESCE(AVG(r.rating),0) rating,COUNT(r.id) review_count
        FROM listings l LEFT JOIN reviews r ON r.server_id=l.server_id WHERE l.status='approved'
        GROUP BY l.server_id ORDER BY rating DESC,l.created_at DESC LIMIT 15""").fetchall()
    if not rows:
        return await reply_embed(ctx, "Community Directory", "No communities have been published yet.")
    fields = []
    for r in rows:
        guild = bot.get_guild(r["server_id"])
        name = guild.name if guild else f"Community {r['server_id']}"
        rating = f"{r['rating']:.1f}/5 ({r['review_count']} reviews)" if r["review_count"] else "Not rated"
        fields.append((name, f"{r['description'][:160] or 'Community listing'}\\nRating: {rating}\\n[Join]({r['invite_url']})", False))
    await reply_embed(ctx, "Community Directory", "Joining is always voluntary.", fields=fields)


@bot.command(name="dinfo")
async def dinfo(ctx, server_id: int):
    with db() as c:
        r = c.execute("""SELECT l.*,COALESCE(AVG(v.rating),0) rating,COUNT(v.id) review_count
        FROM listings l LEFT JOIN reviews v ON v.server_id=l.server_id
        WHERE l.server_id=? AND l.status='approved' GROUP BY l.server_id""", (server_id,)).fetchone()
    if not r:
        return await error_embed(ctx, "No published listing matches that server ID.")
    guild = bot.get_guild(server_id)
    rating = f"{r['rating']:.1f}/5 ({r['review_count']} reviews)" if r["review_count"] else "Not rated"
    await reply_embed(ctx, guild.name if guild else f"Community {server_id}", r["description"] or "Community listing",
        fields=[("Server ID", str(server_id), True), ("Rating", rating, True), ("Invite", f"[Join community]({r['invite_url']})", False)])


@bot.command(name="dremove")
async def dremove(ctx, server_id: int = None):
    with db() as c:
        if server_id is None:
            r = c.execute("SELECT server_id FROM listings WHERE owner_id=? AND status!='removal_requested' ORDER BY updated_at DESC LIMIT 1", (ctx.author.id,)).fetchone()
            server_id = r["server_id"] if r else None
        if server_id is None:
            return await error_embed(ctx, "You have no listing to remove.")
        cur = c.execute("UPDATE listings SET status='removal_requested',updated_at=? WHERE server_id=? AND owner_id=?", (stamp(), server_id, ctx.author.id))
        c.commit()
    if not cur.rowcount:
        return await error_embed(ctx, "No listing you own matches that ID.")
    audit(ctx.author.id, "removal_requested", server_id)
    await reply_embed(ctx, "Removal Requested", f"Listing {server_id} is hidden from discovery and its removal request has been recorded.")


@bot.command(name="dprofile")
async def dprofile(ctx):
    ensure_user(ctx.author.id)
    with db() as c:
        u = c.execute("SELECT tier,verified_joins FROM users WHERE user_id=?", (ctx.author.id,)).fetchone()
        n = c.execute("SELECT COUNT(*) n FROM listings WHERE owner_id=?", (ctx.author.id,)).fetchone()["n"]
        reviews = c.execute("SELECT COUNT(*) n FROM reviews WHERE user_id=?", (ctx.author.id,)).fetchone()["n"]
    await reply_embed(ctx, f"{ctx.author.display_name}'s Profile", "NEXORA account overview.",
        fields=[("Tier", u["tier"].title(), True), ("Listings", str(n), True), ("Verified joins", str(u["verified_joins"]), True), ("Reviews written", str(reviews), True)])


@bot.command(name="ddashboard")
async def ddashboard(ctx):
    with db() as c:
        total = c.execute("SELECT COUNT(*) n FROM listings WHERE owner_id=?", (ctx.author.id,)).fetchone()["n"]
        live = c.execute("SELECT COUNT(*) n FROM listings WHERE owner_id=? AND status='approved'", (ctx.author.id,)).fetchone()["n"]
        partners = c.execute("SELECT COUNT(*) n FROM partners WHERE owner_id=? AND status='approved'", (ctx.author.id,)).fetchone()["n"]
        ratings = c.execute("SELECT COALESCE(AVG(r.rating),0) avg,COUNT(r.id) n FROM reviews r JOIN listings l ON l.server_id=r.server_id WHERE l.owner_id=?", (ctx.author.id,)).fetchone()
    await reply_embed(ctx, "Owner Dashboard", "Your community network at a glance.",
        fields=[("Total listings", str(total), True), ("Published", str(live), True), ("Partners", str(partners), True), ("Average rating", f"{ratings['avg']:.1f}/5 ({ratings['n']} reviews)", True)])


@bot.command(name="dleaderboard")
async def dleaderboard(ctx):
    with db() as c:
        rows = c.execute("SELECT user_id,verified_joins FROM users ORDER BY verified_joins DESC LIMIT 10").fetchall()
    text = "\\n".join(f"**{i}.** <@{r['user_id']}> | {r['verified_joins']} verified joins" for i, r in enumerate(rows, 1))
    await reply_embed(ctx, "Community Leaderboard", text or "No activity recorded yet.")


@bot.command(name="dvote")
async def dvote(ctx, server_id: int, rating: int, *, review: str = ""):
    if rating not in range(1, 6):
        return await error_embed(ctx, "Rating must be a whole number from 1 to 5.")
    with db() as c:
        exists = c.execute("SELECT 1 FROM listings WHERE server_id=? AND status='approved'", (server_id,)).fetchone()
        if not exists:
            return await error_embed(ctx, "You can review only a published community.")
        c.execute("""INSERT INTO reviews(server_id,user_id,rating,review,created_at) VALUES(?,?,?,?,?)
        ON CONFLICT(server_id,user_id) DO UPDATE SET rating=excluded.rating,review=excluded.review,created_at=excluded.created_at""",
        (server_id, ctx.author.id, rating, review[:500], stamp()))
        c.commit()
    audit(ctx.author.id, "community_reviewed", f"{server_id}: {rating}/5")
    await reply_embed(ctx, "Review Saved", f"Your {rating}/5 rating for {server_id} has been saved.")


@bot.command(name="dpartner")
async def dpartner(ctx, server_id: int):
    with db() as c:
        own = c.execute("SELECT 1 FROM listings WHERE server_id=? AND owner_id=? AND status='approved'", (server_id, ctx.author.id)).fetchone()
        if not own:
            return await error_embed(ctx, "You can apply only with a published listing that you own.")
        c.execute("""INSERT INTO partners(server_id,owner_id,status,created_at,updated_at) VALUES(?,?,'pending',?,?)
        ON CONFLICT(server_id) DO UPDATE SET owner_id=excluded.owner_id,status='pending',updated_at=excluded.updated_at""",
        (server_id, ctx.author.id, stamp(), stamp()))
        c.commit()
    audit(ctx.author.id, "partner_application", server_id)
    await reply_embed(ctx, "Partner Application", f"Application for {server_id} has been recorded for network review.")


@bot.command(name="dpartners")
async def dpartners(ctx):
    with db() as c:
        rows = c.execute("SELECT server_id FROM partners WHERE status='approved' ORDER BY updated_at DESC LIMIT 15").fetchall()
    await reply_embed(ctx, "Network Partners", "\\n".join(f"• {r['server_id']}" for r in rows) if rows else "No partner communities yet.")


@bot.command(name="dstats")
async def dstats(ctx):
    with db() as c:
        counts = [
            ("Published communities", c.execute("SELECT COUNT(*) n FROM listings WHERE status='approved'").fetchone()["n"]),
            ("Registered owners", c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]),
            ("Partners", c.execute("SELECT COUNT(*) n FROM partners WHERE status='approved'").fetchone()["n"]),
            ("Reviews", c.execute("SELECT COUNT(*) n FROM reviews").fetchone()["n"]),
            ("Giveaways created", c.execute("SELECT COUNT(*) n FROM giveaways").fetchone()["n"]),
        ]
    await reply_embed(ctx, "Network Statistics", "NEXORA network metrics.", fields=[(k, str(v), True) for k, v in counts])


@bot.command(name="dpurge")
async def dpurge(ctx, seconds: int = None):
    if seconds is None:
        return await reply_embed(ctx, "Auto-Purge", f"Current delay: {get_setting('purge_seconds', 10)} seconds. Options: !dpurge 3 or !dpurge 10.")
    if not is_owner(ctx):
        return await error_embed(ctx, "Only the configured network owner can change the global purge delay.")
    if seconds not in PURGE_OPTIONS:
        return await error_embed(ctx, "Choose 3 or 10 seconds.")
    set_setting("purge_seconds", seconds)
    audit(ctx.author.id, "purge_delay_changed", seconds)
    await reply_embed(ctx, "Auto-Purge Updated", f"New submission purge delay: {seconds} seconds.")


@bot.command(name="dgiveaway")
async def dgiveaway(ctx, duration: str, winners_count: int, *, prize: str):
    seconds = parse_duration(duration)
    if seconds is None:
        return await error_embed(ctx, "Use 30s to 30d, for example 10m, 2h, or 1d.")
    if not 1 <= winners_count <= 10 or not prize.strip():
        return await error_embed(ctx, "Choose 1-10 winners and provide a prize. Example: !dgiveaway 10m 1 Nitro.")
    ends = now() + timedelta(seconds=seconds)
    embed = discord.Embed(title="NEXORA | Giveaway", description=f"**Prize:** {prize[:200]}\\n**Winners:** {winners_count}\\n**Ends:** <t:{int(ends.timestamp())}:R>\\n\\nReact with 🎉 to enter!", color=SILVER, timestamp=now())
    embed.set_footer(text=f"Hosted by {ctx.author} | NEXORA v{VERSION}")
    message = await ctx.send(embed=embed)
    await message.add_reaction("🎉")
    with db() as c:
        c.execute("INSERT INTO giveaways(message_id,channel_id,guild_id,host_id,prize,winners_count,ends_at,ended) VALUES(?,?,?,?,?,?,?,0)",
            (message.id, message.channel.id, ctx.guild.id, ctx.author.id, prize[:200], winners_count, ends.isoformat()))
        c.commit()
    audit(ctx.author.id, "giveaway_created", message.id)
    await reply_embed(ctx, "Giveaway Created", f"Giveaway is live. Message ID: {message.id}")


@bot.command(name="dendgiveaway")
async def dendgiveaway(ctx, message_id: int):
    if not is_owner(ctx):
        return await error_embed(ctx, "Only the configured network owner can end giveaways manually.")
    winners, error = await finish_giveaway(message_id)
    if error:
        return await error_embed(ctx, error)
    audit(ctx.author.id, "giveaway_ended", message_id)
    await reply_embed(ctx, "Giveaway Ended", "Winner selection completed.")


@bot.command(name="dreroll")
async def dreroll(ctx, message_id: int):
    if not is_owner(ctx):
        return await error_embed(ctx, "Only the configured network owner can reroll giveaways.")
    winners, error = await finish_giveaway(message_id, reroll=True)
    if error:
        return await error_embed(ctx, error)
    audit(ctx.author.id, "giveaway_rerolled", message_id)
    await reply_embed(ctx, "Giveaway Rerolled", "Winner selection refreshed.")


@bot.command(name="dapprove")
async def dapprove(ctx, server_id: int):
    if not is_owner(ctx):
        return await error_embed(ctx, "Only the configured network owner can approve listings.")
    with db() as c:
        cur = c.execute("UPDATE listings SET status='approved',updated_at=? WHERE server_id=?", (stamp(), server_id))
        c.commit()
    if cur.rowcount:
        audit(ctx.author.id, "listing_approved_manually", server_id)
    await reply_embed(ctx, "Listing Review", "Listing approved." if cur.rowcount else "Listing not found.")


@bot.command(name="dreject")
async def dreject(ctx, server_id: int, *, reason="No reason supplied"):
    if not is_owner(ctx):
        return await error_embed(ctx, "Only the configured network owner can reject listings.")
    with db() as c:
        cur = c.execute("UPDATE listings SET status='rejected',updated_at=? WHERE server_id=?", (stamp(), server_id))
        c.commit()
    if cur.rowcount:
        audit(ctx.author.id, "listing_rejected", f"{server_id}: {reason}")
    await reply_embed(ctx, "Listing Review", "Listing rejected." if cur.rowcount else "Listing not found.")


@bot.command(name="dtier")
async def dtier(ctx, user_id: int, tier: str):
    if not is_owner(ctx):
        return await error_embed(ctx, "Only the configured network owner can manage tiers.")
    tier = tier.lower()
    if tier not in {"normal", "bronze", "silver", "platinum", "premium"}:
        return await error_embed(ctx, "Choose Normal, Bronze, Silver, Platinum, or Premium.")
    ensure_user(user_id)
    with db() as c:
        c.execute("UPDATE users SET tier=? WHERE user_id=?", (tier, user_id))
        c.commit()
    audit(ctx.author.id, "tier_changed", f"{user_id}: {tier}")
    await reply_embed(ctx, "Tier Updated", f"User {user_id} is now {tier.title()}.")


@bot.command(name="daudit")
async def daudit(ctx, limit: int = 10):
    if not is_owner(ctx):
        return await error_embed(ctx, "Only the configured network owner can view audit logs.")
    limit = max(1, min(limit, 15))
    with db() as c:
        rows = c.execute("SELECT actor_id,action,details,created_at FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    fields = [(r["action"], f"Actor: {r['actor_id']}\\n{r['details']}\\n{r['created_at'][:19]}", False) for r in rows]
    await reply_embed(ctx, "Audit Log", f"Latest {len(rows)} events.", fields=fields)


@bot.command(name="dmaintenance")
async def dmaintenance(ctx, mode: str):
    if not is_owner(ctx):
        return await error_embed(ctx, "Only the configured network owner can change maintenance mode.")
    mode = mode.lower()
    if mode not in {"on", "off"}:
        return await error_embed(ctx, "Usage: !dmaintenance on or !dmaintenance off.")
    set_setting("maintenance", mode)
    audit(ctx.author.id, "maintenance_changed", mode)
    await reply_embed(ctx, "Maintenance Mode", f"Maintenance mode is now {mode.upper()}.")


@bot.command(name="dpartnerapprove")
async def dpartnerapprove(ctx, server_id: int):
    if not is_owner(ctx):
        return await error_embed(ctx, "Only the configured network owner can manage partner status.")
    with db() as c:
        cur = c.execute("UPDATE partners SET status='approved',updated_at=? WHERE server_id=?", (stamp(), server_id))
        c.commit()
    if cur.rowcount:
        audit(ctx.author.id, "partner_approved", server_id)
    await reply_embed(ctx, "Partner Review", "Partner approved." if cur.rowcount else "Application not found.")


@bot.command(name="dadminhelp")
async def dadminhelp(ctx):
    if not is_owner(ctx):
        return await error_embed(ctx, "Only the configured network owner can view owner commands.")
    await reply_embed(ctx, "Owner Commands", "!dapprove SERVER_ID\\n!dreject SERVER_ID reason\\n!dtier USER_ID TIER\\n!daudit [limit]\\n!dmaintenance on|off\\n!dpurge 3|10\\n!dendgiveaway MESSAGE_ID\\n!dreroll MESSAGE_ID\\n!dpartnerapprove SERVER_ID")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        return await error_embed(ctx, "Commands are restricted to the Fire and NEXORA servers. Maintenance mode may also be active.")
    if isinstance(error, commands.MissingRequiredArgument):
        return await error_embed(ctx, "Required information is missing. Use !dhelp for command formats.")
    if isinstance(error, commands.BadArgument):
        return await error_embed(ctx, "One of the values is invalid. Check !dhelp for the correct format.")
    log.error("Command error: %r", error)
    await error_embed(ctx, "An unexpected error occurred and was logged.")


init_db()
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is required.")
bot.run(TOKEN)
