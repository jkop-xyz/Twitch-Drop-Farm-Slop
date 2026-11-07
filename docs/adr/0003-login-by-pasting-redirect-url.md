# Login by pasting the implicit-grant redirect URL

Twitch stopped accepting device-code login for the Android app client ID around September 2026 (TwitchDropsMiner issue #1165). Other client IDs that still allow device-code login get an empty list of Drop Campaigns, so they can't be used. Twitch also rejects browsers that are remote-controlled from a server. The token therefore has to come from a real browser on the owner's PC. The web UI shows an implicit-grant login link for the Android app client ID. The owner opens it in their normal browser, signs in, and pastes the resulting `https://www.twitch.tv/#access_token=…` URL (or just the token) back into the UI.

## Considered Options

- **Device-code login**: blocked by Twitch for the only usable client ID.
- **A browser inside the container (noVNC)**: Twitch rejects it.
- **A desktop helper script** that drives Chrome/Edge and uploads the token, as the TDM forks do: proven to work, but needs Python and Chrome/Edge on the owner's PC. This is the fallback if pasting turns out to be unreliable.
