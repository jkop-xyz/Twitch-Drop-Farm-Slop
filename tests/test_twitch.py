"""Twitch parsing and client error classification, without touching the network."""

import json
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from dropbot.domain import Game
from dropbot.twitch import AuthError, ProtocolError, TransientError, protocol
from dropbot.twitch.client import TwitchClient
from dropbot.twitch.parse import (
    extract_token,
    merge,
    parse_campaign,
    parse_directory,
    parse_points_context,
    parse_stream_info,
    slugify,
)


def campaign_json(**over):
    data = {
        "id": "camp1",
        "name": "Winter Drops",
        "status": "ACTIVE",
        "startAt": "2026-11-01T00:00:00Z",
        "endAt": "2026-11-20T00:00:00Z",
        "accountLinkURL": "https://example.com/link",
        "self": {"isAccountConnected": True},
        "game": {
            "id": "1001",
            "displayName": "Some Game",
            "slug": "some-game",
            "boxArtURL": "https://cdn/x-285x380.jpg",
        },
        "allow": {"isEnabled": True, "channels": [{"id": "9", "name": "streamer9"}]},
        "timeBasedDrops": [
            {
                "id": "d1",
                "name": "Hat",
                "startAt": "2026-11-01T00:00:00Z",
                "endAt": "2026-11-20T00:00:00Z",
                "requiredMinutesWatched": 120,
                "preconditionDrops": None,
                "benefitEdges": [
                    {
                        "benefit": {
                            "id": "b1",
                            "name": "Cool Hat",
                            "distributionType": "DIRECT_ENTITLEMENT",
                            "imageAssetURL": "https://cdn/hat.png",
                        }
                    }
                ],
                "self": {"currentMinutesWatched": 30, "isClaimed": False, "dropInstanceID": None},
            },
            {
                "id": "d2",
                "name": "Sub reward",
                "startAt": "2026-11-01T00:00:00Z",
                "endAt": "2026-11-20T00:00:00Z",
                "requiredMinutesWatched": 0,
                "preconditionDrops": [{"id": "d1"}],
                "benefitEdges": [],
            },
        ],
    }
    data.update(over)
    return data


# --- parsing ---------------------------------------------------------------------------


def test_parse_campaign():
    c = parse_campaign(campaign_json(), {})
    assert c.game == Game("1001", "Some Game", "some-game")
    assert c.requires_account_link and c.account_linked
    assert c.allowed_channel_ids == frozenset({"9"}) and c.allowed_logins == ("streamer9",)
    assert c.image_url == "https://cdn/x.jpg"
    d1, d2 = c.drops
    assert (d1.required_minutes, d1.current_minutes, d1.claimed) == (120, 30, False)
    assert d1.rewards == ("Cool Hat",)
    assert not d2.farmable_by_watching and d2.precondition_ids == ("d1",)


def test_badge_campaign_needs_no_account_link():
    data = campaign_json(self={"isAccountConnected": False})
    data["timeBasedDrops"][0]["benefitEdges"][0]["benefit"]["distributionType"] = "BADGE"
    c = parse_campaign(data, {})
    assert not c.requires_account_link and c.link_ok


def test_disabled_allow_list_means_any_channel():
    c = parse_campaign(campaign_json(allow={"isEnabled": False, "channels": [{"id": "9"}]}), {})
    assert c.allowed_channel_ids is None


def test_claimed_inferred_from_benefits_without_self_edge():
    from datetime import UTC, datetime

    data = campaign_json()
    del data["timeBasedDrops"][0]["self"]
    c = parse_campaign(data, {"b1": datetime(2026, 11, 5, tzinfo=UTC)})
    assert c.drops[0].claimed and c.drops[0].current_minutes == 120


def test_merge_prefers_primary_and_keeps_both_sides():
    assert merge({"a": 1, "n": {"x": 1}}, {"a": 2, "b": 3, "n": {"y": 2}}) == {
        "a": 1,
        "b": 3,
        "n": {"x": 1, "y": 2},
    }


def test_parse_stream_info_live_and_offline():
    user = {
        "id": "5",
        "login": "abc",
        "displayName": "Abc",
        "stream": {"id": "777", "viewersCount": 42},
        "broadcastSettings": {"title": "hi", "game": {"id": "1001", "name": "Some Game"}},
    }
    _, s = parse_stream_info(user)
    assert (s.channel_id, s.broadcast_id, s.game_id, s.viewers) == ("5", "777", "1001", 42)
    u, s = parse_stream_info({**user, "stream": None})
    assert u["login"] == "abc" and s is None
    assert parse_stream_info(None) == (None, None)


def test_parse_directory():
    data = {
        "game": {
            "streams": {
                "edges": [
                    {
                        "node": {
                            "id": "1",
                            "viewersCount": 9,
                            "title": "t",
                            "broadcaster": {"id": "5", "login": "abc", "displayName": "Abc"},
                        }
                    },
                    {"node": {"id": "2", "broadcaster": None}},
                ]
            }
        }
    }
    [s] = parse_directory(data, Game("1001", "G"))
    assert s.drops_enabled and s.game_id == "1001" and s.login == "abc"


def test_parse_points_context():
    data = {
        "community": {
            "channel": {
                "self": {"communityPoints": {"balance": 1234, "availableClaim": {"id": "cl1"}}}
            }
        }
    }
    assert parse_points_context(data) == (1234, "cl1", True)


def test_parse_points_context_points_turned_off():
    # Shape seen on real channels without Channel Points (not affiliate/partner).
    channel = {"self": {"communityPoints": None}, "communityPointsSettings": {"isEnabled": False}}
    assert parse_points_context({"community": {"channel": channel}}) == (0, None, False)
    channel["self"]["communityPoints"] = {"balance": 70, "availableClaim": {"id": "x"}}
    assert parse_points_context({"community": {"channel": channel}}) == (70, None, False)
    channel["communityPointsSettings"]["isEnabled"] = True
    assert parse_points_context({"community": {"channel": channel}}) == (70, "x", True)


@pytest.mark.parametrize(
    "pasted,token",
    [
        (
            "https://www.twitch.tv/#access_token=abcdefghij0123456789xyz&scope=&token_type=bearer",
            "abcdefghij0123456789xyz",
        ),
        ("  abcdefghij0123456789xyz  ", "abcdefghij0123456789xyz"),
        ("https://www.twitch.tv/", None),
        ("hello world", None),
    ],
)
def test_extract_token(pasted, token):
    assert extract_token(pasted) == token


def test_slugify():
    assert slugify("Tom Clancy's Rainbow Six: Siege") == "tom-clancys-rainbow-six-siege"


def test_login_url_is_implicit_grant_for_android_client():
    q = parse_qs(urlparse(protocol.LOGIN_URL).query)
    assert q["client_id"] == [protocol.CLIENT_ID]
    inner = parse_qs(unquote(q["redirect_params"][0]))
    assert inner["response_type"] == ["token"]
    assert inner["redirect_uri"] == ["https://www.twitch.tv/"]


def test_gql_op_merges_variables_without_mutating_defaults():
    op = protocol.gql_op("GameDirectory", slug="x", limit=5)
    assert op["variables"]["slug"] == "x" and op["variables"]["options"]["sort"]
    assert protocol.gql_op("GameDirectory")["variables"]["slug"] is None


# --- client error classification -------------------------------------------------------


class Scripted(TwitchClient):
    """A TwitchClient whose HTTP layer replays scripted responses."""

    def __init__(self, *responses):
        super().__init__(retries=2)
        self.responses = list(responses)
        self.calls = []
        self.authenticate("tok", "42", "dev")

    async def _request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        status, body = self.responses.pop(0)
        return status, body if isinstance(body, bytes) else json.dumps(body).encode()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_):
        return None

    monkeypatch.setattr("dropbot.twitch.client.asyncio.sleep", instant)


async def test_gql_success():
    c = Scripted((200, {"data": {"currentUser": {"dropCurrentSession": None}}}))
    assert await c.current_drop("5") is None
    _, url, kw = c.calls[0]
    assert url == protocol.GQL_URL
    assert kw["headers"]["Authorization"] == "OAuth tok"
    assert kw["headers"]["Client-Id"] == protocol.CLIENT_ID


async def test_unknown_gql_error_is_a_protocol_error():
    c = Scripted((200, {"errors": [{"message": "failed integrity check"}]}))
    with pytest.raises(ProtocolError) as e:
        await c.current_drop("5")
    assert e.value.operation == "DropCurrentSessionContext"


async def test_persisted_query_not_found_retries_once_then_fails():
    pqnf = (200, {"errors": [{"message": "PersistedQueryNotFound"}]})
    c = Scripted(pqnf, pqnf)
    with pytest.raises(ProtocolError):
        await c.current_drop("5")
    assert len(c.calls) == 2


async def test_transient_gql_error_retries_then_succeeds():
    c = Scripted(
        (200, {"errors": [{"message": "service timeout"}]}),
        (
            200,
            {
                "data": {
                    "currentUser": {
                        "dropCurrentSession": {"dropID": "d1", "currentMinutesWatched": 7}
                    }
                }
            },
        ),
    )
    s = await c.current_drop("5")
    assert (s.drop_id, s.current_minutes) == ("d1", 7)


async def test_persistent_transient_error_raises_transient():
    t = (200, {"errors": [{"message": "service unavailable"}]})
    c = Scripted(t, t, t)
    with pytest.raises(TransientError):
        await c.current_drop("5")


async def test_http_401_is_auth_error():
    with pytest.raises(AuthError):
        await Scripted((401, b"")).current_drop("5")


async def test_validate_rejects_token_from_other_client():
    c = Scripted((200, {"user_id": "42", "login": "me", "client_id": "someoneelse"}))
    with pytest.raises(AuthError):
        await c.validate("tok")


async def test_validate_ok():
    c = Scripted((200, {"user_id": "42", "login": "me", "client_id": protocol.CLIENT_ID}))
    info = await c.validate("tok")
    assert (info.user_id, info.login) == ("42", "me")


async def test_claim_drop_builds_instance_id():
    c = Scripted((200, {"data": {"claimDropRewards": {"status": "ELIGIBLE_FOR_ALL"}}}))
    assert await c.claim_drop("camp1", "d1")
    body = c.calls[0][2]["json"]
    assert body["variables"]["input"]["dropInstanceID"] == "42#camp1#d1"


async def test_live_streams_marks_drops_enabled():
    live_user = {
        "id": "5",
        "login": "abc",
        "stream": {"id": "1", "viewersCount": 3},
        "broadcastSettings": {"game": {"id": "1001", "name": "G"}},
    }
    c = Scripted(
        (
            200,
            [
                {"data": {"user": live_user}},
                {"data": {"user": {"id": "6", "login": "off", "stream": None}}},
            ],
        ),
        (200, [{"data": {"channel": {"id": "5", "viewerDropCampaigns": [{"id": "camp1"}]}}}]),
    )
    out = await c.live_streams(["ABC", "off", "abc"])
    assert list(out) == ["5"] and out["5"].drops_enabled
    assert len(c.calls[0][2]["json"]) == 2  # deduplicated, lowercased


async def test_watch_heads_every_chunk():
    c = Scripted(
        (200, {"data": {"streamPlaybackAccessToken": {"value": "v", "signature": "s"}}}),
        (
            200,
            b"#EXTM3U\n#EXT-X-STREAM-INF\nhttps://hls/high.m3u8\n#EXT-X-STREAM-INF\nhttps://hls/low.m3u8\n",
        ),
        (200, b"#EXTM3U\n#EXTINF:2\nhttps://seg/1.ts\n#EXTINF:2\nhttps://seg/2.ts\n"),
        (200, b""),
        (200, b""),
    )
    from .factories import stream

    assert await c.watch(stream("5"))
    methods = [(m, u) for m, u, _ in c.calls[1:]]
    assert methods == [
        ("GET", protocol.USHER_URL.format(login="user5")),
        ("GET", "https://hls/low.m3u8"),
        ("HEAD", "https://seg/1.ts"),
        ("HEAD", "https://seg/2.ts"),
    ]


async def test_unexpected_response_shape_becomes_protocol_error():
    c = Scripted((200, {"data": {"currentUser": {}}}))  # no "inventory"
    with pytest.raises(ProtocolError) as e:
        await c.campaigns()
    assert "shape" in e.value.message


async def test_points_many_is_batched():
    def ctx(bal):
        return {
            "data": {
                "community": {
                    "channel": {
                        "self": {"communityPoints": {"balance": bal, "availableClaim": None}}
                    }
                }
            }
        }

    c = Scripted((200, [ctx(5), {"data": {"community": None}}]))
    out = await c.points_many(["a", "b", "a"])
    assert out["a"].balance == 5 and out["b"] is None
    assert len(c.calls) == 1 and len(c.calls[0][2]["json"]) == 2


async def test_minute_watched_reports_the_web_player():
    from base64 import b64decode

    from .factories import stream

    c = Scripted((204, b""))
    c._spade["user1"] = "https://spade.example/track"
    assert await c.send_minute_watched(stream("1"))
    method, url, kw = c.calls[0]
    assert (method, url) == ("POST", "https://spade.example/track")
    assert kw["headers"]["User-Agent"] == protocol.BROWSER_USER_AGENT
    [event] = json.loads(b64decode(kw["data"]["data"]))
    props = event["properties"]
    assert event["event"] == "minute-watched"
    assert props["player"] == "site"  # without it Twitch credits no Channel Points
    assert props["user_id"] == "42" and props["channel_id"] == "1" and props["live"] is True
