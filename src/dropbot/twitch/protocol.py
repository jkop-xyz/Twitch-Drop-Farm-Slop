"""Every Twitch-specific constant lives here. When Twitch changes something, fix it here.

Values verified against TwitchDropsMiner (MIT, (c) DevilXD), commit 99d12fd (2026-10-28).
The ChannelFollows hash is from Twitch-Channel-Points-Miner-v2 (2026-07).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import quote

# The Android app client: the only one that both still logs in and sees Drop Campaigns
# (docs/adr/0003).
CLIENT_ID = "kd1unb4b3q4t58fwlpcbzcbnm76a8fp"
CLIENT_URL = "https://www.twitch.tv"
USER_AGENT = (
    "Dalvik/2.1.0 (Linux; U; Android 16; SM-S911B Build/TP1A.220624.014) "
    "tv.twitch.android.app/31.8.0/3108006"
)
# Browser user agent for fetching HTML pages (spade URL extraction, device id cookie).
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)

GQL_URL = "https://gql.twitch.tv/gql"
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
USHER_URL = "https://usher.ttvnw.net/api/channel/hls/{login}.m3u8"

# Implicit-grant login link. After login, Twitch redirects to
# https://www.twitch.tv/#access_token=... which the owner pastes back into the UI.
LOGIN_URL = f"https://www.twitch.tv/login?client_id={CLIENT_ID}&redirect_params=" + quote(
    f"client_id={CLIENT_ID}&redirect_uri={quote(CLIENT_URL + '/', safe='')}&response_type=token",
    safe="",
)

SPADE_PATTERN = r'"spade_?url": ?"(https://[.\w\-/]+)"'
SETTINGS_JS_PATTERN = r'src="(https://[\w.]+/config/settings\.[0-9a-f]{32}\.js)"'

# Batch size for GQL requests that take a list of operations.
GQL_BATCH = 20
# Watching cadence: HLS HEAD every 20s, a minute-watched event every 60s.
WATCH_INTERVAL_S = 20
SPADE_INTERVAL_S = 60

# GQL error messages that are worth retrying.
TRANSIENT_GQL_ERRORS = frozenset(
    {
        "service error",
        "service timeout",
        "service unavailable",
        "request cancelled",
        "context deadline exceeded",
        "server error",
    }
)


def _persisted(name: str, sha: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {
        "operationName": name,
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": sha}},
    }
    if variables is not None:
        op["variables"] = variables
    return op


_OPS: dict[str, dict[str, Any]] = {
    "StreamInfo": _persisted(
        "VideoPlayerStreamInfoOverlayChannel",
        "198492e0857f6aedead9665c81c5a06d67b25b58034649687124083ff288597d",
        {"channel": None},
    ),
    "ClaimCommunityPoints": _persisted(
        "ClaimCommunityPoints",
        "46aaeebe02c99afdf4fc97c7c0cba964124bf6b0af229395f1f6d1feed05b3d0",
        {"input": {"claimID": None, "channelID": None}},
    ),
    "ClaimDrop": _persisted(
        "DropsPage_ClaimDropRewards",
        "a455deea71bdc9015b78eb49f4acfbce8baa7ccbedd28e549bb025bd0f751930",
        {"input": {"dropInstanceID": None}},
    ),
    "ChannelPointsContext": _persisted(
        "ChannelPointsContext",
        "374314de591e69925fce3ddc2bcf085796f56ebb8cad67a0daa3165c03adc345",
        {"channelLogin": None},
    ),
    "Inventory": _persisted(
        "Inventory",
        "8337eb8541b314040b0edde0c09c5c7a2783ba1960aa9edfbf3bac16d0fec404",
        {"fetchRewardCampaigns": False},
    ),
    "CurrentDrop": _persisted(
        "DropCurrentSessionContext",
        "4d06b702d25d652afb9ef835d2a550031f1cf762b193523a92166f40ea3d142b",
        {"channelID": None, "channelLogin": ""},
    ),
    "Campaigns": _persisted(
        "ViewerDropsDashboard",
        "c16bb890cc8ce7647a96ee69cd313d423a378a3dedadf630a1017cde18975feb",
        {"fetchRewardCampaigns": False},
    ),
    "CampaignDetails": _persisted(
        "DropCampaignDetails",
        "039277bf98f3130929262cc7c6efd9c141ca3749cb6dca442fc8ead9a53f77c1",
        {"channelLogin": None, "dropID": None},
    ),
    "AvailableDrops": _persisted(
        "DropsHighlightService_AvailableDrops",
        "782dad0f032942260171d2d80a654f88bdd0c5a9dddc392e9bc92218a0f42d20",
        {"channelID": None},
    ),
    "PlaybackAccessToken": _persisted(
        "PlaybackAccessToken",
        "ed230aa1e33e07eebb8928504583da78a5173989fadfb1ac94be06a04f3cdbe9",
        {
            "isLive": True,
            "isVod": False,
            "login": None,
            "platform": "web",
            "playerType": "site",
            "vodID": "",
        },
    ),
    "GameDirectory": _persisted(
        "DirectoryPage_Game",
        "86bcceb4e8b1a51256ff8eed8bd8aae4acacf80d737efe904f84f3aeadf8cafd",
        {
            "limit": 30,
            "slug": None,
            "imageWidth": 50,
            "includeCostreaming": False,
            "options": {
                "broadcasterLanguages": [],
                "freeformTags": None,
                "includeRestricted": ["SUB_ONLY_LIVE"],
                "recommendationsContext": {"platform": "web"},
                "sort": "VIEWER_COUNT",
                "systemFilters": ["DROPS_ENABLED"],
                "tags": [],
                "requestID": "JIRA-VXP-2397",
            },
            "sortTypeIsRecency": False,
        },
    ),
    "ChannelFollows": _persisted(
        "ChannelFollows",
        "eecf815273d3d949e5cf0085cc5084cd8a1b5b7b6f7990cf43cb0beadf546907",
        {"limit": 100, "order": "ASC", "cursor": ""},
    ),
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


def gql_op(key: str, **variables: Any) -> dict[str, Any]:
    """A GQL operation with variables merged into its defaults."""
    op = deepcopy(_OPS[key])
    if variables:
        op.setdefault("variables", {})
        _merge(op["variables"], variables)
    return op
