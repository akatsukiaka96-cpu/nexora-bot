import asyncio
import os, re, sqlite3, logging
from datetime import datetime, timezone
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = os.getenv("DATABASE_PATH", "nexora.db")
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
OWNER_IDS = {int(x) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip().isdigit()}
TIERS = {"normal": 0, "bronze": 5, "silver": 10, "platinum": 25, "premium": 50}
INVITE_RE = re.compile(r"^(https?://)?(www\.)?(discord\.gg|discord\.com/invite)/[A-Za-z0-9-]+/?$", re.I)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nexora")
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

def ts(): return datetime.now(timezone.utc).isoformat()
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c
def init_db():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY,tier TEXT NOT NULL DEFAULT 'normal',verified_joins INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS listings(server_id INTEGER PRIMARY KEY,owner_id INTEGER NOT NULL,invite_url TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',description TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS joins(id INTEGER PRIMARY KEY AUTOINCREMENT,server_id INTEGER NOT NULL,member_id INTEGER NOT NULL,joined_at TEXT NOT NULL,UNIQUE(server_id,member_id));
        CREATE TABLE IF NOT EXISTS cooldowns(user_id INTEGER PRIMARY KEY,last_submit TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT,actor_id INTEGER NOT NULL,action TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL);
        """)
        c.commit()
def ensure_user(uid):
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO users(user_id,created_at) VALUES(?,?)",(uid,ts()))
        c.commit()
def is_admin(ctx):
    # These commands control the network-wide directory, so only configured owners may use them.
    return ctx.author.id in OWNER_IDS
def audit(uid,action,details):
    with conn() as c:
        c.execute("INSERT INTO logs(actor_id,action,details,created_at) VALUES(?,?,?,?)",(uid,action,details[:400],ts()))
        c.commit()

@bot.event
async def on_ready():
    init_db()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching,name="Discover. Connect. Belong."))
    log.info("NEXORA ready: %s",bot.user)

@bot.event
async def on_member_join(member):
    # Discord only provides this event in servers where the bot is installed.
    with conn() as c:
        row=c.execute("SELECT owner_id FROM listings WHERE server_id=? AND status='approved'",(member.guild.id,)).fetchone()
        if row:
            cur=c.execute("INSERT OR IGNORE INTO joins(server_id,member_id,joined_at) VALUES(?,?,?)",(member.guild.id,member.id,ts()))
            if cur.rowcount: c.execute("UPDATE users SET verified_joins=verified_joins+1 WHERE user_id=?",(row["owner_id"],))
        c.commit()

@bot.command(name="dhelp")
async def dhelp(ctx):
    e=discord.Embed(title="NEXORA | Commands",description="Discover. Connect. Belong.",color=0x00D2FF)
    entries=[("!djoin SERVER_ID INVITE_URL","Submit a server for review"),("!dstatus","Check your listing status"),("!dremove [SERVER_ID]","Request listing removal"),("!dinfo SERVER_ID","View an approved listing"),("!dquota","Check your tier target"),("!dservers","Browse approved communities"),("!dprofile","View your profile"),("!dleaderboard","Network statistics")]
    for n,v in entries:e.add_field(name=n,value=v,inline=False)
    e.set_footer(text="All joins are voluntary. No join counts are guaranteed.")
    await ctx.send(embed=e)


async def delete_djoin_command_later(message):
    await asyncio.sleep(10)
    try:
        await message.delete()
    except discord.HTTPException:
        pass

async def djoin_reply(ctx, *args, **kwargs):
    kwargs.setdefault("delete_after", 10)
    response = await ctx.reply(*args, **kwargs)
    asyncio.create_task(delete_djoin_command_later(ctx.message))
    return response

@bot.command(name="djoin")
async def djoin(ctx,server_id:int,invite_url:str):
    if not 10<=len(str(server_id))<=25 or not INVITE_RE.fullmatch(invite_url):
        return await djoin_reply("Usage: !djoin SERVER_ID valid_discord_invite")
    guild=bot.get_guild(server_id)
    if guild is None:return await djoin_reply("NEXORA must be installed in that server to verify its listing.")
    ensure_user(ctx.author.id)
    with conn() as c:
        old=c.execute("SELECT last_submit FROM cooldowns WHERE user_id=?",(ctx.author.id,)).fetchone()
        if old:
            elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(old["last_submit"])).total_seconds()
            if elapsed<300:return await djoin_reply(f"Please wait {int(300-elapsed)} seconds before submitting again.")
        prior=c.execute("SELECT owner_id FROM listings WHERE server_id=?",(server_id,)).fetchone()
        if prior and prior["owner_id"]!=ctx.author.id and not is_admin(ctx):return await djoin_reply("That server already has a listing owner.")
        c.execute("""INSERT INTO listings(server_id,owner_id,invite_url,status,description,created_at,updated_at) VALUES(?,?,?,'pending',?,?,?)
        ON CONFLICT(server_id) DO UPDATE SET invite_url=excluded.invite_url,status='pending',updated_at=excluded.updated_at""",(server_id,ctx.author.id,invite_url,guild.description or "",ts(),ts()))
        c.execute("INSERT INTO cooldowns(user_id,last_submit) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET last_submit=excluded.last_submit",(ctx.author.id,ts()))
        c.commit()
    audit(ctx.author.id,"listing_submitted",str(server_id))
    await djoin_reply(f"**{guild.name}** submitted for review. Members can choose to join using its invite. No one is automatically added.")

@bot.command(name="dstatus")
async def dstatus(ctx):
    with conn() as c: rows=c.execute("SELECT server_id,status FROM listings WHERE owner_id=? ORDER BY created_at DESC",(ctx.author.id,)).fetchall()
    if not rows:return await ctx.reply("No listings yet. Use !djoin SERVER_ID INVITE_URL.")
    shown=rows[:15]
    message="\n".join(f"• {r['server_id']} · {r['status'].title()}" for r in shown)
    if len(rows)>15: message += f"\nShowing the 15 most recent of {len(rows)} listings."
    await ctx.reply(message)

@bot.command(name="dremove")
async def dremove(ctx,server_id:int=None):
    with conn() as c:
        if server_id is None:
            r=c.execute("SELECT server_id FROM listings WHERE owner_id=? AND status!='removal_requested' ORDER BY created_at DESC LIMIT 1",(ctx.author.id,)).fetchone()
            server_id=r["server_id"] if r else None
        if server_id is None:return await ctx.reply("No listing found.")
        cur=c.execute("UPDATE listings SET status='removal_requested',updated_at=? WHERE server_id=? AND owner_id=?",(ts(),server_id,ctx.author.id))
        c.commit()
    if cur.rowcount:
        audit(ctx.author.id,"removal_requested",str(server_id))
        await ctx.reply("Removal requested. An administrator will review it.")
    else:await ctx.reply("No listing you own matched that ID.")

@bot.command(name="dinfo")
async def dinfo(ctx,server_id:int):
    with conn() as c:r=c.execute("SELECT invite_url,description FROM listings WHERE server_id=? AND status='approved'",(server_id,)).fetchone()
    if not r:return await ctx.reply("No approved listing found.")
    g=bot.get_guild(server_id)
    e=discord.Embed(title=g.name if g else f"Community {server_id}",description=r["description"] or "Community listing",color=0x00D2FF)
    e.add_field(name="Invite",value=r["invite_url"])
    await ctx.reply(embed=e)

@bot.command(name="dquota")
async def dquota(ctx):
    ensure_user(ctx.author.id)
    with conn() as c:r=c.execute("SELECT tier FROM users WHERE user_id=?",(ctx.author.id,)).fetchone()
    await ctx.reply(f"Tier: **{r['tier'].title()}** Â· Up to **{TIERS[r['tier']]}** voluntary joins as a target per cycle. Not guaranteed. Five-minute submission cooldown.")

@bot.command(name="dservers")
async def dservers(ctx):
    with conn() as c:rows=c.execute("SELECT server_id,invite_url,description FROM listings WHERE status='approved' ORDER BY created_at DESC LIMIT 10").fetchall()
    if not rows:return await ctx.reply("No approved communities listed yet.")
    e=discord.Embed(title="NEXORA | Discover",color=0x00D2FF)
    for r in rows:
        g=bot.get_guild(r["server_id"])
        e.add_field(name=g.name if g else f"Community {r['server_id']}",value=f"{r['description'][:120] or 'Community listing'}\n{r['invite_url']}",inline=False)
    await ctx.reply(embed=e)

@bot.command(name="dprofile")
async def dprofile(ctx):
    ensure_user(ctx.author.id)
    with conn() as c:
        u=c.execute("SELECT tier,verified_joins FROM users WHERE user_id=?",(ctx.author.id,)).fetchone()
        n=c.execute("SELECT COUNT(*) n FROM listings WHERE owner_id=?",(ctx.author.id,)).fetchone()["n"]
    e=discord.Embed(title=f"{ctx.author.display_name} | NEXORA",color=0x00D2FF)
    e.add_field(name="Tier",value=u["tier"].title());e.add_field(name="Listings",value=str(n));e.add_field(name="Verified joins",value=str(u["verified_joins"]))
    await ctx.reply(embed=e)

@bot.command(name="dleaderboard")
async def dleaderboard(ctx):
    with conn() as c:rows=c.execute("SELECT user_id,verified_joins FROM users ORDER BY verified_joins DESC LIMIT 10").fetchall()
    if not rows:return await ctx.reply("Leaderboard is empty.")
    e=discord.Embed(title="NEXORA | Leaderboard",description="\n".join(f"{i}. <@{r['user_id']}> Â· {r['verified_joins']} verified joins" for i,r in enumerate(rows,1)),color=0x00D2FF)
    await ctx.reply(embed=e)

@bot.command(name="dapprove")
async def dapprove(ctx,server_id:int):
    if not is_admin(ctx):return await ctx.reply("Administrator permission required.")
    with conn() as c:
        cur=c.execute("UPDATE listings SET status='approved',updated_at=? WHERE server_id=? AND status='pending'",(ts(),server_id));c.commit()
    if cur.rowcount:audit(ctx.author.id,"listing_approved",str(server_id))
    await ctx.reply("Listing approved." if cur.rowcount else "Pending listing not found.")

@bot.command(name="dreject")
async def dreject(ctx,server_id:int,*,reason="No reason supplied"):
    if not is_admin(ctx):return await ctx.reply("Administrator permission required.")
    with conn() as c:
        cur=c.execute("UPDATE listings SET status='rejected',updated_at=? WHERE server_id=?",(ts(),server_id));c.commit()
    if cur.rowcount:audit(ctx.author.id,"listing_rejected",f"{server_id}: {reason}")
    await ctx.reply("Listing rejected." if cur.rowcount else "Listing not found.")

@bot.command(name="dtier")
async def dtier(ctx,user_id:int,tier:str):
    if not is_admin(ctx):return await ctx.reply("Administrator permission required.")
    tier=tier.lower()
    if tier not in TIERS:return await ctx.reply("Use Normal, Bronze, Silver, Platinum, or Premium.")
    ensure_user(user_id)
    with conn() as c:c.execute("UPDATE users SET tier=? WHERE user_id=?",(tier,user_id));c.commit()
    audit(ctx.author.id,"tier_changed",f"{user_id}: {tier}")
    await ctx.reply(f"Tier updated to **{tier.title()}**.")

@bot.command(name="dadminhelp")
async def dadminhelp(ctx):
    if not is_admin(ctx):return await ctx.reply("Administrator permission required.")
    await ctx.reply("Admin: !dapprove SERVER_ID | !dreject SERVER_ID [reason] | !dtier USER_ID TIER")

@bot.event
async def on_command_error(ctx,error):
    if isinstance(error,commands.CommandNotFound):return
    if isinstance(error,commands.MissingRequiredArgument):return await ctx.reply("Missing information. Use !dhelp.")
    if isinstance(error,commands.BadArgument):return await ctx.reply("Invalid ID or argument.")
    log.error("Command error: %r",error)
    await ctx.reply("An error occurred and was logged.")

if not TOKEN:raise RuntimeError("DISCORD_TOKEN environment variable is required.")
init_db()
bot.run(TOKEN)
