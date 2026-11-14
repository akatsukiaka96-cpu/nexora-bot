# NEXORA 2.0

Discover. Connect. Belong.

NEXORA is a Discord community directory with owner verification, automatic publishing, reviews, partner applications, giveaways, owner dashboards, audit logs, anti-spam controls, and SQLite persistence.

## Behavior
- Commands are restricted to Fire and NEXORA by default.
- A listing auto-publishes only after the submitting user is verified as the target server owner or has Manage Server permission.
- NEXORA must be installed in the target server to verify permissions and create an invite.
- Members choose whether to join. NEXORA never force-adds or mass-invites members.
- Submission cooldown: five minutes per user.
- Only the invoking djoin message and NEXORA's reply are auto-deleted, after 3 or 10 seconds.
- Existing SQLite tables and records are preserved. New tables are additive.

## Community commands
- !dhelp
- !djoin SERVER_ID
- !dverify SERVER_ID
- !dstatus
- !dservers
- !dinfo SERVER_ID
- !dremove [SERVER_ID]
- !dprofile
- !ddashboard
- !dleaderboard
- !dvote SERVER_ID 1-5 [review]
- !dpartner SERVER_ID
- !dpartners
- !dstats

## Giveaways
- !dgiveaway 10m 1 Prize name
- !dendgiveaway MESSAGE_ID (owner)
- !dreroll MESSAGE_ID (owner)

Duration range: 30 seconds to 30 days. Participants react with 🎉.

## Owner controls
- !dapprove SERVER_ID
- !dreject SERVER_ID reason
- !dtier USER_ID TIER
- !daudit [limit]
- !dmaintenance on|off
- !dpurge 3|10
- !dpartnerapprove SERVER_ID
- !dadminhelp

Owner-only commands require the network owner's Discord user ID in OWNER_IDS.

## Environment
- DISCORD_TOKEN: bot token. Keep it secret.
- OWNER_IDS: comma-separated Discord user IDs authorized for network administration. Add the Fire owner ID here.
- ALLOWED_GUILD_IDS: optional comma-separated guild allowlist. Defaults to Fire and NEXORA.
- DATABASE_PATH: SQLite path. For Railway persistent storage use /data/nexora.db.

Enable Message Content Intent, Server Members Intent, and Reaction Intent in the Discord Developer Portal. The bot needs View Channels, Send Messages, Embed Links, Read Message History, Add Reactions, and Create Instant Invite in target servers where it verifies and publishes listings.

## Railway
Deploy with the included Dockerfile. Attach a persistent volume mounted at /data and set DATABASE_PATH=/data/nexora.db. Set DISCORD_TOKEN and OWNER_IDS as Railway variables. Never commit secrets or share the bot token in chat.

## Data safety and limitations
NEXORA 2.0 creates tables for verified servers, reviews, partners, giveaways, giveaway entries, and settings. It does not drop or rename existing tables. Keep the Railway volume attached to preserve current data.

Giveaway winners are selected randomly from users who reacted before the giveaway ended. Auto-purge affects only the invoking djoin command and its corresponding NEXORA reply. SQLite is intended for a single bot process; larger deployments should plan backups and a tested restore procedure.
