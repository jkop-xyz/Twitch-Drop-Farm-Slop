# Spec: Twitch Drop Bot

Status: needs-triage

## Summary

A headless service that runs on a Linux machine and does Passive Farming for one Twitch Account. It earns and claims Drops from Selected Campaigns, and earns Channel Points (including Bonus Chests) on a ranked list of Points Channels. The owner controls it through a web UI on the local network. Terms are used as defined in [GLOSSARY.md](../../GLOSSARY.md).

TwitchDropsMiner (DevilXD) is the reference for how Twitch's private API is spoken. None of its code or GUI design is reused.

## Goals

- Run unattended on headless Linux, packaged as a Docker image.
- Farm only what the owner has explicitly chosen.
- Make the current state obvious at a glance: what each Watch Slot is doing and why.
- Survive restarts without losing selections, the login, or progress already credited by Twitch.

## Non-goals (v1)

- Predictions, betting, or spending Channel Points in any way.
- Farming Drops for campaigns the owner hasn't selected. No automatic "farm everything".
- More than one Account per instance. To farm another account, run another instance.
- Authentication or HTTPS in the app. A reverse proxy handles both ([ADR 0001](../../docs/adr/0001-no-built-in-web-ui-auth.md)).
- Windows support.
- Notifications (Discord, Telegram, etc.).
- ~~Publishing to an image registry or building in CI.~~ Done: GitHub Actions builds and pushes to GHCR (`.github/workflows/ci.yml`).
- Self-healing when Twitch changes its API.

## Behaviour

### Login

Device-code login no longer works for the client ID that sees Drops ([ADR 0003](../../docs/adr/0003-login-by-pasting-redirect-url.md)).

- If no valid token is stored, the Login/Account page shows a Twitch login link that uses the Android app client ID and the implicit grant flow.
- The owner opens the link in their normal browser on their own PC and signs in. They then paste the URL they land on (`https://www.twitch.tv/#access_token=…`), or just the token, into the page.
- The bot validates the token (`id.twitch.tv/oauth2/validate`) and checks that its client ID matches. It then stores the token and the Account identity in the database and shows "logged in as <name>".
- Log out deletes the token and stops all farming.
- If Twitch rejects the token (expired or revoked), farming stops and the UI goes back to the login state.

### Campaign discovery and selection

- The bot periodically fetches the Account's Drop Campaigns from Twitch: the campaigns themselves, their Drops, required minutes, current progress, Account Link status, and participating channels or game.
- A campaign the bot hasn't seen before is stored **unselected** with a "new" badge. The badge clears once the owner has viewed or acted on it.
- The owner selects and deselects campaigns on the Campaigns page, which groups them by game.
- A campaign that needs an Account Link it doesn't have shows a "no account linked" warning. The owner can still select it, but it is **not farmed** until the link is in place. Link status is re-checked on each refresh.
- A Drop that can't be earned by watching (because it needs a subscription, a gifted sub, or another action) is marked "not farmable by watching".
- A Selected Campaign is automatically deselected once it has ended, or once every Drop in it is claimed or not farmable by watching.

### Farmable Campaigns and the Drop slot

- A **Farmable Campaign** is a Selected Campaign that is running, has its required Account Link, and has at least one unclaimed Drop that can be earned by watching.
- When several Farmable Campaigns exist, the bot works on the one whose **end time is soonest**. When it's finished (no longer farmable), the next soonest takes over.
- Choosing the stream for that campaign:
  1. An eligible live stream that is also a Points Channel (the highest-ranked one), so a single slot earns both.
  2. Otherwise, the eligible live stream with the most viewers.
- "Eligible" means the stream counts toward the campaign: it's on the participating-channel list (if the campaign has one), or it's streaming the campaign's game with Drops enabled.
- If the watched stream goes offline, changes game, or stops counting, the bot picks a new stream straight away.
- A Drop is claimed automatically as soon as its required minutes are reached.

### Watch Slots

- The Account has **2 Watch Slots** (the number of streams Twitch will credit at the same time). This is a constant so it can be changed if Twitch's limit changes.
- Drops take priority: if any Farmable Campaign exists, one slot goes to its chosen stream.
- The remaining slots go to the highest-ranked **live** Points Channels that aren't already being watched.
- When a Points Channel ranked above a currently watched one goes live, it takes that slot straight away.
- When nothing is live and no campaign is farmable, the bot sits idle and keeps checking.

### Channel Points

- The owner maintains a ranked list of Points Channels: add by name, remove, reorder. An "import from follows" button copies the Account's followed channels into the list.
- The bot keeps track of which Points Channels are live.
- While a channel is being watched, Bonus Chests are claimed automatically as they appear.
- Points balances and every gain (from watching, Bonus Chests, or watch streaks) are recorded.

### Twitch API failures

- Every Twitch-specific constant (client ID, GQL operation names, persisted query hashes, endpoints, headers) lives in one module.
- If Twitch rejects a request in a way that suggests the protocol has changed (unknown persisted query, schema error, or repeated 4xx errors), the bot **pauses farming** and the Dashboard shows a clear error with the failing operation. It does not try to work around the problem.
- Short-lived network errors are retried with backoff and don't count as a protocol change.

## Web UI

FastAPI serving server-rendered Jinja templates with htmx for live updates, in the same process as the farming loop. There's no JavaScript build step.

1. **Dashboard**: each Watch Slot (channel, why it's being watched, Drop progress), Channel Points balances for Points Channels, recent claims, login state, and any protocol error banner.
2. **Campaigns**: current Drop Campaigns grouped by game, each with its Drops (required minutes and progress), a select checkbox, and the "new", "no account linked" and "not farmable by watching" badges.
3. **Points Channels**: the ranked list (add, remove, reorder, import from follows), showing who is live now.
4. **Login / Account**: the device code during login, the logged-in account, and log out.

The UI listens on a configurable port, intended for local-network use only.

## Persistence

- One relational database, set with `DATABASE_URL`. Postgres is the primary target. When `DATABASE_URL` is unset, the bot falls back to SQLite at `/data/bot.db` ([ADR 0002](../../docs/adr/0002-postgres-primary-sqlite-fallback.md)).
- Access goes through SQLAlchemy 2 (async: asyncpg / aiosqlite). Alembic migrations run automatically on startup.
- Stored data:
  - the Twitch token and account identity
  - the campaigns the bot has seen (for the "new" badge) and the selection state of each
  - the Points Channels and their rank
  - the claim history: Drops claimed, Bonus Chests claimed, and Channel Points gains, each with a timestamp and channel

## Deployment

- A `Dockerfile` in the repo, based on a slim Python 3.13 image.
- A `compose.yaml` with one service, a `/data` volume, the UI port, and `DATABASE_URL` as an optional environment variable.
- A health endpoint (`/healthz`) for the Docker healthcheck.
- Configuration through environment variables only: `DATABASE_URL`, `PORT`, `LOG_LEVEL`.
- Logs go to stdout in a structured, readable format.

## Architecture sketch

- **Twitch client**: logs in, makes GQL requests, reports watching, handles the websocket and claims. This is the only code that knows Twitch's protocol. Its constants live in one module.
- **Scheduler**: pure decision logic. Given the current campaigns, the live Points Channels, the Watch Slot count and what's being watched now, it returns the desired assignment of streams to Watch Slots and the claims to make. It does no I/O.
- **Farming loop**: periodically refreshes state through the Twitch client, asks the Scheduler what to do, applies the result (switching streams, claiming), and records the outcome.
- **Store**: database access, with SQLAlchemy models and Alembic migrations.
- **Web**: FastAPI routes and templates. It reads from the Store and from the loop's current state, and writes selections and Points Channels.

## Testing

- The Scheduler gets thorough unit tests, using plain data objects with no I/O. Required cases:
  - deadline ordering across several Farmable Campaigns
  - a Selected Campaign without an Account Link is never assigned a slot
  - a campaign whose remaining Drops are all not farmable by watching is treated as done
  - a Points Channel is preferred for the Drop slot, falling back to the eligible stream with the most viewers
  - a Drop is assigned before Points Channels
  - a higher-ranked Points Channel taking over a slot when it goes live
  - switching when the watched stream goes offline or changes game
  - idle when nothing is farmable or live
- The farming loop is tested end-to-end against a **fake Twitch client** with the same interface as the real one.
- The Store is tested against SQLite, and against Postgres when a test `DATABASE_URL` is provided.
- Nothing is tested against live Twitch.

## Protocol notes (verified against TDM source, commit 99d12fd, 2026-10-28)

- Client: the Android app client ID `kd1unb4b3q4t58fwlpcbzcbnm76a8fp`, with an Android app user agent. GQL requests go to `https://gql.twitch.tv/gql` with `Authorization: OAuth <token>`, `Client-Id` and `X-Device-Id`.
- Watching: every ~20s, take the playlist URL (from the PlaybackAccessToken GQL query and `usher.ttvnw.net`) for the lowest quality, GET the chunk list, and send HEAD requests for the chunks. No video is downloaded.
- Campaigns: the `ViewerDropsDashboard` query gives the list, `Inventory` gives progress and claimed benefits, and `DropCampaignDetails` gives the Drops, the allowed channels and `isAccountConnected`. Badge or emote rewards don't need an Account Link.
- Drop claim: `DropsPage_ClaimDropRewards` with `dropInstanceID`, which has the form `<userId>#<campaignId>#<dropId>`.
- Drop progress: `DropCurrentSessionContext`, polled.
- Live status: `VideoPlayerStreamInfoOverlayChannel`, batched up to 20 per request. Drops-enabled: `DropsHighlightService_AvailableDrops`. Game directory: `DirectoryPage_Game` with the `DROPS_ENABLED` filter.
- Channel Points: `ChannelPointsContext` gives the balance and any available Bonus Chest claim, and `ClaimCommunityPoints` claims it. TDM doesn't farm points, so this part has no reference implementation.
- v1 uses **polling only**, with no websocket. Live status, Drop progress and Bonus Chests are all re-checked every 60s.

## Open questions for implementation

- Whether the HLS-HEAD watching method credits Channel Points as well as Drops, and on 2 streams at once. TDM only ever watches one stream. This needs checking against real balances once it's running.
