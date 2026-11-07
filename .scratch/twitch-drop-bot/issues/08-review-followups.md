# 08 Code review follow-ups (minor)

Status: needs-triage

Left over from the 2026-11-07 code review. Items 1–8 were fixed: the loops survive unexpected errors, the activity list shows claims only, streams that keep failing are skipped, cross-site POSTs are rejected, the game directory is used for allow-listed campaigns, balance refreshes are batched, dependencies are pinned, and (de)selecting a campaign swaps the card in place.

- **No retries for video chunk HEADs.** `_request` retries 5xx with 1s/3s/9s backoff for every chunk, which can stall the watch loop for minutes. Chunk HEADs should not retry at all.
- **Bounded caches.** `TwitchClient._playlists` (per broadcast) and `_spade` (per login) grow forever. Prune entries for streams that are no longer watched.
- **Points attribution after a gap.** The first gain after a channel starts being watched again is measured against a balance up to 30 minutes old, so points earned elsewhere count as farmed. Reset the baseline when a channel enters a slot.
- **Event retention.** `points` events accumulate (a few hundred thousand a year). Prune them after about 90 days.
- **CI hardening.** Actions are pinned to major-version tags rather than commit SHAs. Old `sha-*` images are never cleaned up from GHCR.
- ~~**Account switch.** Logging in as a different Twitch account keeps the previous account's selections and Points Channels.~~ Fixed: data is now kept per account (migration 0002).
