# NEXORA 2.0.1

Discover. Connect. Belong.

NEXORA is a Discord community discovery directory with owner verification, automatic publishing, an owner dashboard, reviews, partner applications, giveaways, audit logs, anti-spam protection, statistics, maintenance mode, and automatic SQLite backups.

## Core behavior
- Text commands use the `!` prefix and work only in Fire (`1507663008292405339`) and NEXORA (`1571079983596830810`).
- `!djoin SERVER_ID` publishes automatically only after the submitter is verified as the target server owner or has Manage Server permission. NEXORA must be installed in that target server to verify and create an invite.
- Members choose whether to join. NEXORA never force-adds or mass-invites members.
- Submission anti-spam cooldown: five minutes per user.
- Auto-purge removes only the invoking `!djoin` message and its NEXORA response. Default: 10 seconds. The Fire owner can switch it to 3 seconds with `!dpurge 3`, or back with `!dpurge 10`.
- Existing SQLite tables and records are preserved through additive `CREATE TABLE IF NOT EXISTS` migration. No existing table is dropped or renamed.
- The Fire server owner is automatically recognized for network-owner controls. Optional additional owner IDs can be configured with `OWNER_IDS`.

## Community commands
- `!dhelp`
- `!djoin SERVER_ID`
- `!dverify SERVER_ID`
- `!dstatus`
- `!dservers`
- `!dinfo SERVER_ID`
- `!dremove [SERVER_ID]`
- `!dprofile`
- `!ddashboard`
- `!dleaderboard`
- `!dvote SERVER_ID 1-5 [review]`
- `!dpartner SERVER_ID`
- `!dpartners`
- `!dstats`

## Giveaways
- `!dgiveaway 10m 1 Prize name`
- `!dendgiveaway MESSAGE_ID` (network owner)
- `!dreroll MESSAGE_ID` (network owner)

Giveaways are free-entry: users enter by reacting with 🎉. Duration range: 30 seconds to 30 days. Removing the reaction removes the entry.

## Network-owner controls
- `!dapprove SERVER_ID`
- `!dreject SERVER_ID reason`
- `!dtier USER_ID TIER`
- `!daudit [limit]`
- `!dmaintenance on|off`
- `!dpurge 3|10`
- `!dpartnerapprove SERVER_ID`
- `!dbackup` (create an immediate backup)
- `!dadminhelp`

## Environment variables
- `DISCORD_TOKEN`: bot token. Keep it secret.
- `OWNER_IDS`: optional comma-separated additional network-owner Discord IDs. Fire server owner is detected automatically.
- `ALLOWED_GUILD_IDS`: optional comma-separated guild allowlist; defaults to Fire and NEXORA.
- `DATABASE_PATH`: SQLite file path. **Keep the same path and Railway volume already used by the existing bot** to retain its data.
- `BACKUP_DIR`: backup folder. For a Railway volume mounted at `/data`, use `/data/backups`.
- `BACKUP_RETENTION`: number of most recent backups to keep; defaults to 7.

Example `.env` values (do not put a real token in GitHub):
```env
DISCORD_TOKEN=your_discord_bot_token
OWNER_IDS=
ALLOWED_GUILD_IDS=1507663008292405339,1571079983596830810
DATABASE_PATH=/data/nexora.db
BACKUP_DIR=/data/backups
BACKUP_RETENTION=7
```

## Railway and Discord setup
- Deploy using the included Dockerfile.
- Keep the existing Railway volume mounted at the same location. Do not delete or recreate it.
- Keep `DATABASE_PATH` pointing at the existing database file. If the current deployment uses a different path, preserve that exact value instead of copying the example.
- Enable Message Content Intent, Server Members Intent, and Reaction Intent in the Discord Developer Portal.
- Bot permissions needed: View Channels, Send Messages, Embed Links, Read Message History, Add Reactions, and Create Instant Invite in target community servers.

## Backups and data safety
A SQLite backup is created automatically on startup and every 24 hours, using SQLite's online backup API. Old backups beyond the retention count are pruned. `!dbackup` creates an immediate backup. Backups are only durable if `BACKUP_DIR` is on persistent storage.

The migration is additive and keeps the original `users`, `listings`, `joins`, `cooldowns`, and `logs` data. The new tables support verification, reviews, partners, giveaways, giveaway entries, and settings. SQLite is intended for one bot process.
