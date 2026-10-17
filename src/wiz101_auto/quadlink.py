"""Quadbox: 3 questers and a booster, one bot per game, working together.

quadbox.py starts a bot for each game, each in its own folder (quad/q1,
quad/q2, quad/q3, quad/booster), with its role in WIZ101_ROLE and the shared
folder in WIZ101_SHARED. Without those nothing here runs and the bot is the
usual single bot.

Each bot posts where its wizard is (zone, position, fighting, health,
objective, the dungeon it went into) to <shared>/<role>.json once a second,
and reads the others':

- the booster (mode boost) never quests: it joins any quester's fight (a
  teleport onto them in the same zone, a friend teleport from another zone)
  and otherwise stays beside the lead quester (`quadbox.follow`, q1);
- a quester at a dungeon sigil whose teammate is already inside goes to them
  by friend teleport (the same copy of the dungeon) instead of its own copy;
- a quester whose teammate is fighting nearby on the same objective joins
  that fight rather than pulling another.

Friend teleports go by the questers' names (`quadbox.names`, exactly as the
Friends list shows them), so the booster must be friends with all three
questers and the questers with each other.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

ROLES = ("q1", "q2", "q3", "booster")
QUESTERS = ("q1", "q2", "q3")
STALE = 15.0  # a teammate's post older than this is ignored (its bot stopped, its game froze)
PUBLISH_EVERY = 1.0
BOOST_TICK = 1.0
ZONE_SETTLE = 8.0  # a quester this long in a zone before the booster follows it there (not passing through)
FOLLOW_RANGE = 1500.0  # the booster farther than this from the lead (same zone): back beside them
FIGHT_JOIN_WAIT = 5.0  # seconds a teleport onto a fight gets to put us in it
FRIEND_TP_GAP = 45.0  # seconds between friend teleports to the same teammate
FRIEND_TP_BACKOFF = 600.0  # after FRIEND_TP_FAILS failures in a row to one teammate
FRIEND_TP_FAILS = 3
FRIEND_TP_TIMEOUT = 30.0  # for the Friends-list clicks
ARRIVE_WAIT = 20.0  # for the friend teleport's loading screen / move
JOIN_GAP = 20.0  # seconds between tries to join the same teammate's fight
SIGIL_MATCH = 800.0  # a teammate went in by a sigil this close to ours: the same dungeon
UPKEEP_EVERY = 30.0  # the booster's potions and wisps, between fights

_NAMES: dict[str, str] = {}
_cfg = None


def role() -> str:
    r = os.environ.get("WIZ101_ROLE", "").strip().lower()
    return r if r in ROLES else ""


def active() -> bool:
    return bool(role())


def is_booster() -> bool:
    return role() == "booster"


def shared_dir() -> Path:
    return Path(os.environ.get("WIZ101_SHARED") or Path("..") / "shared")


def configure(cfg) -> None:
    """The run's quadbox settings (names, follow, join_fights...)."""
    global _cfg
    _cfg = cfg
    _NAMES.clear()
    _NAMES.update({k: v.strip() for k, v in (cfg.names or {}).items() if isinstance(v, str) and v.strip()})


def _setting(name: str, default):
    return getattr(_cfg, name, default) if _cfg is not None else default


# --- what each bot posts -------------------------------------------------------

@dataclass
class Mate:
    role: str
    zone: str = ""
    zone_since: float = 0.0  # wall-clock time it came into this zone
    pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    in_battle: bool = False
    loading: bool = False
    health: float = 1.0  # share of max health
    objective: str = ""
    entered: tuple[str, float, float] | None = None  # (outside zone, sigil x, y) of the dungeon it's in
    state: str = "running"  # running | paused | stopped
    time: float = 0.0

    @classmethod
    def from_json(cls, role: str, data: dict) -> Mate:
        entered = data.get("entered")
        return cls(
            role=role,
            zone=str(data.get("zone") or ""),
            zone_since=float(data.get("zone_since") or 0.0),
            pos=tuple(float(v) for v in (data.get("pos") or (0, 0, 0))[:3]),
            in_battle=bool(data.get("in_battle")),
            loading=bool(data.get("loading")),
            health=float(data.get("health", 1.0)),
            objective=str(data.get("objective") or ""),
            entered=(str(entered[0]), float(entered[1]), float(entered[2])) if entered else None,
            state=str(data.get("state") or "running"),
            time=float(data.get("time") or 0.0),
        )

    @property
    def available(self) -> bool:
        """Playing (not paused, stopped or between zones)."""
        return self.state == "running" and not self.loading and bool(self.zone)


def publish(info: dict, folder: Path | None = None, me: str | None = None) -> None:
    me = me or role()
    if not me:
        return
    folder = folder or shared_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{me}.tmp"
        tmp.write_text(json.dumps({**info, "time": time.time()}), encoding="utf-8")
        tmp.replace(folder / f"{me}.json")
    except OSError as exc:
        logger.debug(f"quadbox: could not post our status: {exc!r}")


def read_mates(folder: Path | None = None, me: str | None = None,
               now: float | None = None) -> dict[str, Mate]:
    """The other bots' fresh posts, by role."""
    me = me if me is not None else role()
    folder = folder or shared_dir()
    now = time.time() if now is None else now
    mates = {}
    for r in ROLES:
        if r == me:
            continue
        try:
            m = Mate.from_json(r, json.loads((folder / f"{r}.json").read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, IndexError):
            continue
        if now - m.time <= STALE:
            mates[r] = m
    return mates


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# --- decisions (pure) ----------------------------------------------------------

def booster_plan(mates: dict[str, Mate], zone: str, pos, lead: str = "q1",
                 now: float | None = None) -> tuple[str, str] | None:
    """What the booster does next: ("fight", role) join that quester's fight,
    ("zone", role) friend-teleport to that quester (another zone), ("near",
    role) back beside them (same zone, far off), or None (stay)."""
    now = time.time() if now is None else now
    questers = [m for r, m in mates.items() if r in QUESTERS and m.available]
    fighting = [m for m in questers if m.in_battle]
    if fighting:
        # The nearest fight first: one in our zone, then the lead's, then by order.
        fighting.sort(key=lambda m: (m.zone != zone, _dist(m.pos, pos) if m.zone == zone else 0.0,
                                     m.role != lead, QUESTERS.index(m.role)))
        return "fight", fighting[0].role
    order = [lead, *(r for r in QUESTERS if r != lead)]
    by_role = {m.role: m for m in questers}
    leader = next((by_role[r] for r in order if r in by_role), None)
    if leader is None:
        return None
    if leader.zone != zone:
        if now - leader.zone_since < ZONE_SETTLE:
            return None  # (passing through: wait until it stays)
        return "zone", leader.role
    if _dist(leader.pos, pos) > FOLLOW_RANGE:
        return "near", leader.role
    return None


def fight_to_join(mates: dict[str, Mate], zone: str, pos, objective: str,
                  join_range: float = 3000.0) -> str | None:
    """A quester's teammate fighting nearby for the same Defeat objective:
    the role to join (the nearest), or None."""
    if not objective.lower().startswith("defeat"):
        return None
    want = objective.strip().lower()
    near = [m for r, m in mates.items() if r in QUESTERS and m.available and m.in_battle
            and m.zone == zone and m.objective.strip().lower() == want and _dist(m.pos, pos) <= join_range]
    near.sort(key=lambda m: _dist(m.pos, pos))
    return near[0].role if near else None


def mate_inside(mates: dict[str, Mate], outside: str, sigil) -> str | None:
    """A quester who went into the dungeon of this sigil and is in there now:
    their role (go to them, the same copy), or None."""
    for r in QUESTERS:
        m = mates.get(r)
        if m is None or not m.available or m.entered is None:
            continue
        their_outside, x, y = m.entered
        if their_outside == outside and m.zone != outside and _dist((x, y), (sigil.x, sigil.y)) < SIGIL_MATCH:
            return r
    return None


class FriendTeleports:
    """When a friend teleport to a teammate may be tried (not too often; a
    long pause after several failures: not friends, or their zone is closed)."""

    def __init__(self):
        self.last: dict[str, float] = {}
        self.fails: dict[str, int] = {}

    def may_try(self, who: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        gap = FRIEND_TP_BACKOFF if self.fails.get(who, 0) >= FRIEND_TP_FAILS else FRIEND_TP_GAP
        return now - self.last.get(who, -1e9) >= gap

    def note(self, who: str, ok: bool, now: float | None = None) -> None:
        self.last[who] = time.monotonic() if now is None else now
        if ok:
            self.fails[who] = 0
        else:
            self.fails[who] = self.fails.get(who, 0) + 1
            if self.fails[who] == FRIEND_TP_FAILS:
                logger.warning(f"quadbox: {FRIEND_TP_FAILS} friend teleports to {who} "
                               f"({_NAMES.get(who, '?')}) "
                               f"failed; trying again in {FRIEND_TP_BACKOFF / 60:.0f} min. Check that "
                               "they're on this wizard's Friends list under exactly that name")


_teleports = FriendTeleports()
_entered: list = [None]  # (outside zone, sigil x, y) of the dungeon we went in by, or a teammate's


def note_entered(outside: str, sigil) -> None:
    """We went into a dungeon by the sigil at `sigil` in `outside`."""
    _entered[0] = (outside, float(sigil.x), float(sigil.y))


# --- game actions ----------------------------------------------------------------

async def _position(client):
    p = await client.body.position()
    return (p.x, p.y, p.z)


async def _close_friends_list(client) -> None:
    try:
        from wizwalker.memory.memory_objects.enums import WindowFlags

        for w in await client.root_window.get_windows_with_name("NewFriendsListWindow"):
            if await w.is_visible():
                await w.write_flags(WindowFlags.disabled)
    except Exception as exc:
        logger.debug(f"quadbox: closing the Friends list failed: {exc!r}")


async def _dismiss_box(client) -> str:
    """A message box after a friend teleport ("busy", "can't teleport
    there"): close it; its text ('' if none)."""
    from . import ui

    box = await ui.modal_box(client)
    if box is None:
        return ""
    text = await ui.modal_text(box)
    for name in ("rightButton", "centerButton", "leftButton"):
        try:
            if await ui.press_modal_button(client, box, name):
                break
        except Exception:
            continue
    return text or "a message box"


async def friend_teleport(client, who: str, why: str) -> bool:
    """Teleport to teammate `who` from the Friends list. True once we've moved."""
    from wizwalker.extensions.scripting import teleport_to_friend_from_list

    from .upkeep import wait_for_loading

    if not _setting("friend_teleports", True):
        return False
    name = _NAMES.get(who, "")
    if not name:
        if _teleports.may_try(who):
            _teleports.note(who, False)
            logger.warning(f"quadbox: no name for {who} in config.yaml (quadbox: names:); "
                           "can't friend teleport")
        return False
    if not _teleports.may_try(who):
        return False
    zone_before = await client.zone_name() or ""
    pos_before = await _position(client)
    logger.info(f"quadbox: friend teleport to {who} ({name}): {why}")
    try:
        async with client.mouse_handler:
            await asyncio.wait_for(teleport_to_friend_from_list(client, name=name), FRIEND_TP_TIMEOUT)
    except Exception as exc:
        logger.warning(f"quadbox: friend teleport to {name} didn't go: {exc!r}")
        await _close_friends_list(client)
        _teleports.note(who, False)
        return False
    end = time.monotonic() + ARRIVE_WAIT
    moved = False
    while time.monotonic() < end:
        await asyncio.sleep(0.5)
        if await client.is_loading():
            await wait_for_loading(client)
            moved = True
            break
        here = await client.zone_name() or ""
        if here != zone_before or _dist(await _position(client), pos_before) > 300:
            moved = True
            break
        async with client.mouse_handler:
            refused = await _dismiss_box(client)
        if refused:
            logger.warning(f"quadbox: friend teleport to {name} refused: {refused[:120]!r}")
            break
    await _close_friends_list(client)
    _teleports.note(who, moved)
    if moved:
        logger.success(f"quadbox: with {who} ({name}) in {(await client.zone_name() or '?').split('/')[-1]}")
    return moved


async def _teleport_onto(client, pos, engage: bool) -> None:
    from wizwalker import XYZ

    from .safe_teleport import allow_engage

    if engage:
        allow_engage(client, FIGHT_JOIN_WAIT + 2)
    await client.teleport(XYZ(*pos))


async def _join_fight(client, mate: Mate) -> bool:
    """Teleport onto a teammate in a fight (same zone): in it within a few
    seconds, or not (the circle full, the fight over)."""
    await _teleport_onto(client, mate.pos, engage=True)
    end = time.monotonic() + FIGHT_JOIN_WAIT
    while time.monotonic() < end:
        if await client.in_battle():
            logger.success(f"quadbox: joined {mate.role}'s fight")
            return True
        await asyncio.sleep(0.3)
    return False


# --- loops -----------------------------------------------------------------------

async def publish_loop(client, controller, quester=None) -> None:
    """Post this wizard's whereabouts for the others, every second."""
    from .upkeep import max_health

    zone_seen, zone_since = "", time.time()
    while not controller.stopped.is_set():
        info: dict = {"state": "paused" if controller.paused else "running"}
        try:
            zone = await client.zone_name() or ""
            if zone != zone_seen:
                zone_seen, zone_since = zone, time.time()
            hp, top = await client.stats.current_hitpoints(), await max_health(client)
            info.update(
                zone=zone, zone_since=zone_since, pos=await _position(client),
                in_battle=await client.in_battle(), loading=await client.is_loading(),
                health=round(hp / top, 3) if top else 1.0,
            )
            if quester is not None:
                info["objective"] = quester._last_progress[0] or ""
                inside = getattr(quester, "_dungeon", None)
                if _entered[0] and (not inside or zone == _entered[0][0]):
                    _entered[0] = None  # (out of it again)
                if _entered[0]:
                    info["entered"] = _entered[0]
        except Exception as exc:
            info["read_error"] = repr(exc)
        publish(info)
        await asyncio.sleep(PUBLISH_EVERY)
    publish({"state": "stopped"})


async def join_teammate_fight(quester) -> bool:
    """Between quest steps: a teammate fighting nearby on our Defeat
    objective: join them. True if we did."""
    if not _setting("join_fights", True):
        return False
    client = quester.client
    objective = quester._last_progress[0] or ""
    if not objective.lower().startswith("defeat"):
        return False
    mates = read_mates()
    who = fight_to_join(mates, await client.zone_name() or "", await _position(client), objective,
                        _setting("join_range", 3000.0))
    if not who:
        return False
    last = quester.__dict__.setdefault("_quad_join_at", {})
    if time.monotonic() - last.get(who, -1e9) < JOIN_GAP:
        return False
    last[who] = time.monotonic()
    logger.info(f"quadbox: {who} is fighting for {objective!r} nearby: joining them")
    return await _join_fight(client, mates[who])


async def into_mates_dungeon(quester, outside: str, sigil) -> bool:
    """At a dungeon sigil: a teammate already in that dungeon: friend
    teleport to them (the same copy). True if we're in."""
    who = mate_inside(read_mates(), outside, sigil)
    if not who:
        return False
    client = quester.client
    why = f"already in the dungeon of this sigil ({outside.split('/')[-1]})"
    if not await friend_teleport(client, who, why):
        return False
    note_entered(outside, sigil)
    quester._dungeon = (outside, await client.zone_name() or "")
    return True


async def boost_loop(client, upkeep_cfg, controller) -> None:
    """The booster: into the questers' fights, otherwise beside the lead."""
    from .upkeep import is_free, maintain

    lead = _setting("follow", "q1")
    last_upkeep = 0.0
    last_plan = None
    logger.info(f"quadbox: booster; joining the questers' fights, otherwise staying with {lead} "
                f"({_NAMES.get(lead, 'name not set')})")
    while not controller.stopped.is_set():
        await controller.checkpoint()
        await asyncio.sleep(BOOST_TICK)
        try:
            if not await is_free(client):
                continue  # (in a fight, loading or a dialogue: the other loops have it)
            mates = read_mates()
            zone = await client.zone_name() or ""
            pos = await _position(client)
            plan = booster_plan(mates, zone, pos, lead)
            if plan != last_plan:
                logger.debug(f"quadbox: booster plan {plan}")
                last_plan = plan
            if plan is None:
                if time.monotonic() - last_upkeep > UPKEEP_EVERY:
                    last_upkeep = time.monotonic()
                    await maintain(client, upkeep_cfg)
                continue
            kind, who = plan
            mate = mates[who]
            if kind == "fight" and mate.zone == zone:
                await _join_fight(client, mate)
            elif kind == "near":
                await _teleport_onto(client, mate.pos, engage=False)
            else:
                await friend_teleport(client, who, "in a fight" if kind == "fight" else "in another zone")
        except Exception as exc:
            if controller.stopped.is_set():
                break
            from .safety import BotStopped

            if isinstance(exc, BotStopped):
                raise
            logger.opt(exception=exc).warning("quadbox: booster step failed; going on")
            await asyncio.sleep(3.0)
