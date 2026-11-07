# No built-in authentication on the web UI

The web UI has no login or password, even though it controls the Account and holds its Twitch token. It is meant to be reached only on the owner's local network. If it is ever exposed beyond that, the reverse proxy in front of it handles both HTTPS and authentication. This is deliberate. Do not add an auth layer to the app itself.
