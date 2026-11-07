# Twitch Drop Bot

A headless service that watches Twitch streams on behalf of one Twitch account, to earn Drops and Channel Points with no human present.

## Language

**Account**:
The single Twitch account an instance farms on behalf of.
_Avoid_: User, profile

**Drop Campaign**:
A time-limited Twitch promotion for one game that awards one or more Drops for watching participating channels.
_Avoid_: Campaign (unqualified), event

**Drop**:
A single reward inside a Drop Campaign, earned by accumulating a required number of watched minutes and then claimed.
_Avoid_: Reward, item, benefit

**Selected Campaign**:
A Drop Campaign the owner has explicitly chosen to farm. Selecting a campaign is necessary for it to be farmed, but not sufficient (see Farmable Campaign).
_Avoid_: Active campaign, enabled campaign

**Farmable Campaign**:
A Selected Campaign that is currently running, has its required Account Link in place, and still has at least one unclaimed Drop that can be earned by watching. Only Farmable Campaigns get a Watch Slot.
_Avoid_: Eligible campaign, ready campaign

**Account Link**:
The connection between the Account and a game publisher's account, which some Drop Campaigns require before their Drops can be claimed.
_Avoid_: Connection, integration

**Channel Points**:
The per-channel currency an Account earns by watching that channel while it is live.
_Avoid_: Points (unqualified), coins

**Bonus Chest**:
A claimable bundle of extra Channel Points that appears periodically while watching a channel.
_Avoid_: Bonus, chest, community points claim

**Points Channel**:
A channel on the owner's chosen list for earning Channel Points.
_Avoid_: Followed channel, favorite

**Watch Slot**:
One of the limited number of streams the Account can be credited for watching at the same time.
_Avoid_: Stream slot, watcher

**Passive Farming**:
Earning Drops and Channel Points purely by being counted as watching. Spending or wagering anything is never Passive Farming.
_Avoid_: Mining, botting
