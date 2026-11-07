# Twitch Drop Farm (Slop)

[![CI](https://github.com/jkop-xyz/Twitch-Drop-Farm-Slop/actions/workflows/ci.yml/badge.svg)](https://github.com/jkop-xyz/Twitch-Drop-Farm-Slop/actions/workflows/ci.yml)

> [!WARNING]
> **This project is AI-generated ("slop").** Essentially all of the code, tests, docs and design notes were written by an AI coding agent (Claude, via Claude Code). A human described what they wanted, answered design questions, and tested the result. Nobody has reviewed it line by line. Read the code before you trust it with your Twitch account, and expect rough edges.

A headless Twitch farmer for **one account**. It runs on a Linux box or in Docker:

- It earns and claims **Drops** from the Drop Campaigns *you* pick.
- It earns **Channel Points** on a ranked list of channels you choose, and claims Bonus Chests automatically.
- You control it through a small **web UI** on your local network.

It only farms passively. It never spends, bets or predicts anything. It's inspired by [TwitchDropsMiner](https://github.com/DevilXD/TwitchDropsMiner), and its knowledge of Twitch's private API comes from there (MIT).

> [!CAUTION]
> Automated watching is against Twitch's terms of service. Use it at your own risk, and only on your own account.

## Features

- **You choose what to farm.** New campaigns appear unselected with a "new" badge. Campaigns that need a linked game account say so, and aren't farmed until you link it.
- **Deadline first.** With several selected campaigns, the one ending soonest is farmed first. Each finished Drop is claimed automatically.
- **Two Watch Slots.** Twitch credits about 2 streams at once. Drops get the first slot, and your highest-ranked live Points Channels get the rest. A stream that counts for both earns both.
- **Channel Points.** Balances for each channel and what the bot earned per week. You can check your balance on any channel and import your follows.
- **Search** across 100+ campaigns, by game, campaign or reward.
- **Postgres or SQLite.** Point it at your Postgres server, or let it use SQLite in the data volume.
- **No video is downloaded.** Watching is simulated with lightweight playlist and analytics requests.

## Quick start

```sh
curl -O https://raw.githubusercontent.com/jkop-xyz/Twitch-Drop-Farm-Slop/main/compose.yaml
docker compose up -d        # pulls ghcr.io/jkop-xyz/twitch-drop-farm-slop:latest
```

Open `http://<host>:8080`, then:

1. **Account**: open the login link *on your own PC*, sign in to Twitch, and paste the address you land on (`https://www.twitch.tv/#access_token=…`) into the box. If the page has already changed the address, look in your browser history. (Twitch blocked device-code login for this client in 2026; see [ADR 0003](docs/adr/0003-login-by-pasting-redirect-url.md).)
2. **Campaigns**: select the campaigns you want.
3. **Points Channels**: add channels or import your follows, then put them in order.

The **Dashboard** shows what each Watch Slot is watching, your Drop progress, your points and recent claims.

### Configuration

All configuration is through environment variables, for example in a `.env` next to `compose.yaml` (see [`.env.example`](.env.example)):

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | SQLite at `/data/bot.db` | e.g. `postgresql://user:pass@host:5432/dropbot` |
| `PORT` | `8080` | web UI port |
| `LOG_LEVEL` | `INFO` | |

Database migrations run automatically on startup.

### Images

GitHub Actions publishes `ghcr.io/jkop-xyz/twitch-drop-farm-slop` for `linux/amd64` and `linux/arm64`:

- `latest` for every push to `main`
- `sha-<commit>` for each commit
- `X.Y.Z` and `X.Y` for `vX.Y.Z` tags

Each tagged version also gets a [GitHub release](https://github.com/jkop-xyz/Twitch-Drop-Farm-Slop/releases) whose notes come from [CHANGELOG.md](CHANGELOG.md).

### Rootless

The image runs as an unprivileged user by default: numeric uid `10001`, gid `0`. It never needs root:

- `compose.yaml` additionally makes the root filesystem read-only, drops all Linux capabilities, and sets `no-new-privileges`.
- To run as a different uid, use `--user <uid>:0` (or `user:` in compose). `/data` is group-writable for gid 0.
- To bind-mount a host directory instead of a volume, run as the directory's owner, e.g. `--user "$(id -u):$(id -g)"`.
- It should also work under rootless Docker or Podman, but that hasn't been tested yet.

## Things to know

- **The web UI has no login.** Keep it on your local network, or put a reverse proxy with auth in front of it ([ADR 0001](docs/adr/0001-no-built-in-web-ui-auth.md)). The database holds your Twitch token, so treat it like a password.
- **DNS or ad blockers.** Channel Points rely on "minute-watched" reports to `spade.twitch.tv`, which blocklists often block (Pi-hole, Control D, …). If the dashboard warns about failing minute-watched reports, allowlist that host. Drops aren't affected.
- **Don't watch the same account in a browser at the same time.** Twitch's progress tracking gets confused.
- **When Twitch changes something**, the bot pauses and the dashboard names the failing operation. Every Twitch constant (client ID, GQL query hashes, URLs) lives in [`src/dropbot/twitch/protocol.py`](src/dropbot/twitch/protocol.py). Compare it against [TwitchDropsMiner's `constants.py`](https://github.com/DevilXD/TwitchDropsMiner/blob/master/constants.py), update it, and press **Retry**.

## Development

```sh
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                         # set TEST_DATABASE_URL to also run against Postgres
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
DATABASE_URL=sqlite:///./dev.db .venv/bin/python -m dropbot
docker compose up --build                # build the image locally
./scripts/lock.sh                        # after changing dependencies: re-pin requirements.lock
```

**Releasing:**
1. Move the `[Unreleased]` notes in `CHANGELOG.md` under a new `## [X.Y.Z] - date` heading.
2. Bump `version` in `pyproject.toml`.
3. Commit, then `git tag vX.Y.Z && git push --tags`.

CI checks that the tag, the version and the changelog agree, then publishes the image, then posts the release.

Runtime dependencies are pinned in `requirements.lock`, which the image and CI install from, so builds are reproducible.

| Path | What |
|---|---|
| `src/dropbot/scheduler.py` | Pure decision logic: what each Watch Slot watches, what to claim, what to deselect |
| `src/dropbot/farmer.py` | The loop that refreshes state from Twitch, applies the Scheduler's plan, and records results |
| `src/dropbot/twitch/` | The only code that talks to Twitch (all constants in `protocol.py`) |
| `src/dropbot/store/` | SQLAlchemy models and Alembic migrations (Postgres and SQLite) |
| `src/dropbot/web/` | FastAPI + Jinja + htmx UI |

The design notes behind the code are in the repo:
- [GLOSSARY.md](GLOSSARY.md) defines the terms.
- [docs/adr/](docs/adr/) records the non-obvious decisions.
- [.scratch/twitch-drop-bot/spec.md](.scratch/twitch-drop-bot/spec.md) is the original spec.
