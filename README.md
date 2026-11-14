# NEXORA Bot

Discover. Connect. Belong.

A Python Discord community-discovery bot with reviewed listings, voluntary invite links, tiers, cooldowns, moderation logs, and SQLite persistence.

## Important behavior

NEXORA does not pull, scrape, force-add, or mass-invite members. !djoin submits a server listing for review; members choose whether to join via its invite. Premium's up-to-50 figure is a target, never a promise. Join counts are recorded only in guilds where the bot is installed and Discord delivers a member-join event.

## Commands

- !djoin SERVER_ID INVITE_URL
- !dstatus, !dremove [SERVER_ID], !dinfo SERVER_ID
- !dquota, !dservers, !dprofile, !dleaderboard, !dhelp
- Admin: !dapprove SERVER_ID, !dreject SERVER_ID [reason], !dtier USER_ID TIER, !dadminhelp

New listings remain pending until approved. NEXORA must be installed in a server to verify its listing.

## Setup

1. Create a Discord application and bot in the Discord Developer Portal.
2. Enable Message Content Intent and Server Members Intent.
3. Invite it with View Channels, Send Messages, Embed Links, and Read Message History.
4. Set DISCORD_TOKEN and OWNER_IDS as environment variables. Never commit secrets.
5. Install: pip install -r requirements.txt. Run: python bot.py.

## Railway

Deploy as a worker using the included Dockerfile. Set DISCORD_TOKEN and OWNER_IDS as Railway variables. Attach a persistent volume at /data and set DATABASE_PATH=/data/nexora.db.

## Limitations

Invite validation checks URL shape, not invite activity or server ownership. Join tracking only works in guilds where the bot is installed, has the member intent, and receives the event; it cannot attribute joins to a promotion. Tier targets and cooldowns do not create traffic. SQLite is for early testing; migrate to PostgreSQL and add backups for multi-instance production.
