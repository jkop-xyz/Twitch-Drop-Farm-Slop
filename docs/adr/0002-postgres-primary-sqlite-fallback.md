# Postgres as the primary store, SQLite as the fallback

All persistent state lives in one relational database: the Twitch token, Selected Campaigns, Points Channels, and the claim history. Postgres is the primary target because the owner already runs a dedicated Postgres server on their network. SQLite in the data volume is supported as a fallback for setups without one. Supporting both means sticking to SQL that works on both dialects. Postgres-only features (such as LISTEN/NOTIFY, JSONB operators and arrays) are off-limits unless they have a SQLite equivalent.
