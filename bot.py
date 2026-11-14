import os, re, sqlite3, logging
from datetime import datetime, timezone
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = os.getenv("DATABASE_PATH", "nexora.db")
OWNER_IDS = {int(v) for v in os.getenv("OWNER_IDS", "").split(",") if v.strip().isdigit()}
TIERS = {"normal": 0, "bronze": 5, "silver": 10, "platinum": 25, "premium": 50}
INVITE_RE = re.compile(r"^(https?://)?(www\.)?(discord\.gg|discord\.com/invite)/[A-Za-z0-9-]+/?$", re.I)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nexora")
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

def now():
    return datetime.now(timezone.utc).isoformat()

def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, tier TEXT NOT NULL DEFAULT 'normal', created_at TEXT NOT NULL, verified_joins INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS listings(server_id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, invite_url TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS verified_joins(id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER NOT NULL, member_id INTEGER NOT NULL, joined_at TEXT NOT NULL, UNIQUE(server_id,member_id));
        CREATE TABLE IF NOT EXISTS cooldowns(user_id INTEGER PRIMARY KEY, last_submit TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS moderation_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
        """)
        con.commit()

def ensure_user(uid):
    with connect() as con:
        con.execute("INSERT OR IGNORE INTO users(user_id,created_at) VALUES(?,?)", (uid,now()))
        con.commit()

def admin(ctx):
    return ctx.author.id in OWNER_IDS or bool(ctx.guild and ctx.author.guild_permissions.manage_guild)

def audit(uid, action, details):
    with connect() as con:
        con.execute("INSERT INTO moderation_logs(actor_id,action,details,created_at) VALUES(?,?,?,?)",(uid,action,details[:400],now()))
        con.commit()

@bot.event
async def on_ready():
    init_db()
    log.info("NEXORA ready as %s", bot.user)
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching,name="Discover. Connect. Belong."))

@bot.event
async def on_member_join(member):
    # Counts only join events visible in guilds where NEXORA is installed.
    with connect() as con:
        listing = con.execute("SELECT owner_id FROM listings WHERE server_id=? AND status='approved'",(member.guild.id,)).fetchone()
        if listing:
            cur = con.execute("INSERT OR IGNORE INTO verified_joins(server_id,member_id,joined_at) VALUES(?,?,?)",(member.guild.id,member.id,now()))
            if cur.rowcount:
                con.execute("UPDATE users SET verified_joins=verified_joins+1 WHERE user_id=?",(listing["owner_id"],))
        con.commit()

@bot.command(name="dhelp")
async def dhelp(ctx):
    e=discord.Embed(title="NEXORA | Commands",description="Discover. Connect. Belong.",color=0x00D2FF)
    e.add_field(name="!djoin SERVER_ID INVITE_URL",value="Submit a server listing for review.",inline=False)
    for n,d in [("!dstatus","Check listing status"),("!dremove [SERVER_ID]","Request removal"),("!dinfo SERVER_ID","View an approved listing"),("!dquota","View tier target"),("!dservers","Browse communities"),("!dprofile","View your profile"),("!dleaderboard","View network stats")]:
        e.add_field(name=n,value=d,inline=True)
    e.set_footer(text="All joins are voluntary. No join count is guaranteed.")
    await ctx.send(embed=e)

@bot.command(name="djoin")
async def djoin(ctx, server_id:int, invite_url:str):
    if not 10 <= len(str(server_id)) <= 25 or not INVITE_RE.fullmatch(invite_url):
        await ctx.reply("Usage: !djoin SERVER_ID valid_discord_invite")
        return
    guild=bot.get_guild(server_id)
    if guild is None:
        await ctx.reply("NEXORA must be installed in that server to verify its listing.")
        return
    ensure_user(ctx.author.id)
    with connect() as con:
        previous=con.execute("SELECT last_submit FROM cooldowns WHERE user_id=?",(ctx.author.id,)).fetchone()
        if previous:
            elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(previous["last_submit"])).total_seconds()
            if elapsed < 300:
                await ctx.reply(f"Please wait {int(300-elapsed)} seconds before submitting again.")
                return
        existing=con.execute("SELECT owner_id FROM listings WHERE server_id=?",(server_id,)).fetchone()
        if existing and existing["owner_id"] != ctx.author.id and not admin(ctx):
            await ctx.reply("That server already has a listing owner.")
            return
        con.execute("""INSERT INTO listings(server_id,owner_id,invite_url,status,description,created_at,updated_at)
        VALUES(?,?,?,'pending',?,?,?) ON CONFLICT(server_id) DO UPDATE SET invite_url=excluded.invite_url,status='pending',updated_at=excluded.updated_at""",
        (server_id,ctx.author.id,invite_url,guild.description or "",now(),now()))
        con.execute("INSERT INTO cooldowns(user_id,last_submit) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET last_submit=excluded.last_submit",(ctx.author.id,now()))
        con.commit()
    audit(ctx.author.id,"listing_submitted",str(server_id))
    await ctx.reply(f"**{guild.name}** submitted for review. No members are automatically added.")

@bot.command(name="dstatus")
async def dstatus(ctx):
    with connect() as con:
        rows=con.execute("SELECT server_id,status FROM listings WHERE owner_id=? ORDER BY created_at DESC",(ctx.author.id,)).fetchall()
    if not rows: return await ctx.reply("No listings yet. Use !djoin SERVER_ID INVITE_URL.")
    await ctx.reply("\n".join(f"• {r['server_id']} · {r['status'].title()}" for r in rows))

@bot.command(name="dremove")
async def dremove(ctx, server_id:int=None):
    with connect() as con:
        if server_id is None:
            row=con.execute("SELECT server_id FROM listings WHERE owner_id=? AND status!='removal_requested' ORDER BY created_at DESC LIMIT 1",(ctx.author.id,)).fetchone()
            server_id=row["server_id"] if row else None
        if server_id is None: return await ctx.reply("No listing found.")
        cur=con.execute("UPDATE listings SET status='removal_requested',updated_at=? WHERE server_id=? AND owner_id=?",(now(),server_id,ctx.author.id))
        con.commit()
    if cur.rowcount:
        audit(ctx.author.id,"removal_requested",str(server_id))
        await ctx.reply("Removal request submitted for admin review.")
    else: await ctx.reply("No listing you own matched that server ID.")

@bot.command(name="dinfo")
async def dinfo(ctx, server_id:int):
    with connect() as con:
        r=con.execute("SELECT invite_url,description FROM listings WHERE server_id=? AND status='approved'",(server_id,)).fetchone()
    if not r: return await ctx.reply("No approved listing found.")
    g=bot.get_guild(server_id)
    e=discord.Embed(title=g.name if g else f"Community {server_id}",description=r["description"] or "Community listing",color=0x00D2FF)
    e.add_field(name="Invite",value=r["invite_url"])
    await ctx.reply(embed=e)

@bot.command(name="dquota")
async def dquota(ctx):
    ensure_user(ctx.author.id)
    with connect() as con: r=con.execute("SELECT tier FROM users WHERE user_id=?",(ctx.author.id,)).fetchone()
    tier=r["tier"]
    await ctx.reply(f"Tier: **{tier.title()}** | Up to **{TIERS[tier]}** voluntary joins as a target per cycle. Not guaranteed. Five-minute cooldown.")

@bot.command(name="dservers")
async def dservers(ctx):
    with connect() as con: rows=con.execute("SELECT server_id,invite_url,description FROM listings WHERE status='approved' ORDER BY created_at DESC LIMIT 10").fetchall()
    if not rows: return await ctx.reply("No approved communities listed yet.")
    e=discord.Embed(title="NEXORA | Discover",color=0x00D2FF)
    for r in rows:
        g=bot.get_guild(r["server_id"])
        e.add_field(name=g.name if g else f"Community {r['server_id']}",value=f"{r['description'][:120] or 'Community listing'}\n{r['invite_url']}",inline=False)
    await ctx.reply(embed=e)

@bot.command(name="dprofile")
async def dprofile(ctx):
    ensure_user(ctx.author.id)
    with connect() as con:
        u=con.execute("SELECT tier,verified_joins FROM users WHERE user_id=?",(ctx.author.id,)).fetchone()
        n=con.execute("SELECT COUNT(*) AS n FROM listings WHERE owner_id=?",(ctx.author.id,)).fetchone()["n"]
    e=discord.Embed(title=f"{ctx.author.display_name} | NEXORA",color=0x00D2FF)
    e.add_field(name="Tier",value=u["tier"].title())
    e.add_field(name="Listings",value=str(n))
    e.add_field(name="Verified joins",value=str(u["verified_joins"]))
    await ctx.reply(embed=e)

@bot.command(name="dleaderboard")
async def dleaderboard(ctx):
    with connect() as con: rows=con.execute("SELECT user_id,verified_joins FROM users ORDER BY verified_joins DESC LIMIT 10").fetchall()
    if not rows: return await ctx.reply("Leaderboard is empty.")
    await ctx.reply(embed=discord.Embed(title="NEXORA | Leaderboard",description="\n".join(f"{i}. <@{r['user_id']}> · {r['verified_joins']} verified joins" for i,r in enumerate(rows,1)),color=0x00D2FF))

@bot.command(name="dapprove")
async def dapprove(ctx, server_id:int):
    if not admin(ctx): return await ctx.reply("Administrator permission required.")
    with connect() as con:
        cur=con.execute("UPDATE listings SET status='approved',updated_at=? WHERE server_id=? AND status IN ('pending','removal_requested')",(now(),server_id))
        con.commit()
    if cur.rowcount: audit(ctx.author.id,"listing_approved",str(server_id))
    await ctx.reply("Listing approved." if cur.rowcount else "Pending listing not found.")

@bot.command(name="dreject")
async def dreject(ctx, server_id:int, *, reason="No reason supplied"):
    if not admin(ctx): return await ctx.reply("Administrator permission required.")
    with connect() as con:
        cur=con.execute("UPDATE listings SET status='rejected',updated_at=? WHERE server_id=?",(now(),server_id))
        con.commit()
    if cur.rowcount: audit(ctx.author.id,"listing_rejected",f"{server_id}: {reason}")
    await ctx.reply("Listing rejected." if cur.rowcount else "Listing not found.")

@bot.command(name="dtier")
async def dtier(ctx, user_id:int, tier:str):
    if not admin(ctx): return await ctx.reply("Administrator permission required.")
    tier=tier.lower()
    if tier not in TIERS: return await ctx.reply("Use Normal, Bronze, Silver, Platinum, or Premium.")
    ensure_user(user_id)
    with connect() as con:
        con.execute("UPDATE users SET tier=? WHERE user_id=?",(tier,user_id))
        con.commit()
    audit(ctx.author.id,"tier_changed",f"{user_id}: {tier}")
    await ctx.reply(f"Tier updated to **{tier.title()}**.")

@bot.command(name="dadminhelp")
async def dadminhelp(ctx):
    if not admin(ctx): return await ctx.reply("Administrator permission required.")
    await ctx.reply("Admin commands: !dapprove SERVER_ID | !dreject SERVER_ID [reason] | !dtier USER_ID TIER")

@bot.event
async def on_command_error(ctx,error):
    if isinstance(error,commands.CommandNotFound): return
    if isinstance(error,commands.MissingRequiredArgument): return await ctx.reply("Missing information. Use !dhelp.")
    if isinstance(error,commands.BadArgument): return await ctx.reply("Invalid ID or argument.")
    log.exception("Command failed",exc_info=error)
    await ctx.reply("An error occurred and was logged.")

if not TOKEN: raise RuntimeError("DISCORD_TOKEN environment variable is required.")
init_db()
bot.run(TOKEN)
