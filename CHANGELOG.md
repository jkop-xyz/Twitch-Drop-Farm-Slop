# Changelog

All notable changes to this project are documented here. Each version's section is posted
as its GitHub release when a `vX.Y.Z` tag is pushed. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Fixed
- **Account data is now kept per Twitch account.**
  - Points Channels, balances, claim history and campaign selections belong to the account that made them.
  - After logging out the UI shows none of it, and logging back in as the same account brings it all back.
  - A different account starts empty and never sees another account's data.
  - Database migration `0002` assigns existing data to the account that's currently stored. If nobody is logged in during the upgrade, the next account to log in gets it.

### Security
- Added anti-framing headers (`X-Frame-Options: DENY`, CSP `frame-ancestors 'none'`). Without them, another site could frame the UI and trick clicks past the cross-site check.
- Added `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`, so the bot's LAN address isn't sent to sites the UI links to.
- The OpenAPI schema (`/openapi.json`) is no longer exposed.

### Changed
- **Redesigned web UI.**
  - Header: logo and favicon, a live status pill ("Farming", "Idle", "Paused", "Logged out") on every page, and icon navigation that shortens on phones.
  - Dashboard: a stats row, Watch Slot cards with reward art, a bolder progress bar with time left, and a compact activity feed.
  - Campaigns: box art, toggle switches, reward images with progress per Drop, filter chips (Selected, Not selected, Needs account link) and a consistent grid.
  - Points Channels: avatars, lighter rows, icon buttons that show on hover, a totals row, and a layout that fits on phones.
  - Friendlier time labels ("just now", "3h ago") and better dark-mode contrast.
- The image is built in two stages: only the installed wheel ships, with no source tree or build leftovers.
- `requirements.lock` is renamed `requirements.txt`, so Dependabot recognises it.
- Dependabot keeps the Python dependencies, GitHub Actions and the Docker base image up to date with weekly grouped PRs. Dependabot alerts and automatic security-fix PRs are enabled for known CVEs.

## [0.1.0] - 2026-11-07

First release. AI-generated; see the README.

### Added
- Headless farming for one Twitch account:
  - Drops from the campaigns you select, claimed automatically, soonest-ending campaign first
  - Channel Points on a ranked list of channels, with Bonus Chests claimed automatically
- Two Watch Slots. Drops take priority, and a stream that counts for both earns both.
- Web UI (FastAPI + htmx):
  - Dashboard with what each slot is watching, Drop progress, Channel Points and recent claims
  - Campaign selection with search and "new" / "no account linked" badges
  - Points Channels: add, reorder, import follows, refresh balances, check your balance on any channel
  - Account login by pasting the Twitch redirect URL (device-code login is blocked by Twitch)
- Postgres via `DATABASE_URL`, falling back to SQLite in `/data`. Migrations run automatically.
- Multi-arch Docker image (`linux/amd64`, `linux/arm64`) published to GHCR by CI.
- The bot pauses with a clear message when Twitch changes its API, and keeps running through other errors.
- Streams that keep failing to watch are skipped for 10 minutes.
- Cross-site form posts to the UI are rejected.
- Runtime dependencies are pinned in `requirements.lock`.

### Security
- The image runs **rootless by default**: numeric uid `10001`, gid `0`.
  - It also runs under any `--user <uid>:0`, or with a bind mount owned by your own user.
  - `compose.yaml` additionally sets a read-only root filesystem, drops all capabilities, and sets `no-new-privileges`.

### Upgrade notes
- Images built before this release ran as uid 1000. A data volume created by one of those is owned by uid 1000, and the new image can't write to it. Re-own it once:
  `docker run --rm -u 0 -v <volume>:/data alpine chown -R 10001:0 /data`
