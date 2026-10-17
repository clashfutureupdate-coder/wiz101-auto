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
AFK_NUDGE = 240.0  # standing still this long (free, not paused): a small step
# (the game logs idle players out)
REALM_CHECK_EVERY = 30.0  # a teammate close by in our zone but not in view: another realm?
REALM_NEAR = 1200.0  # ... this close (by their post) and still not seen
REALM_SEEN = 400.0  # a player this close to where they say they are: them
REALM_MISSES = 2  # checks in a row before a friend teleport to their realm
GATHER_WAIT = 180.0  # at a dungeon sigil, waiting for teammates on the same objective
FOLLOW_IN_WAIT = 90.0  # a teammate went in first: this long for their post, then in alone
AT_SIGIL_RANGE = 900.0  # a teammate this close to the sigil (same zone) is there
AHEAD_START = 3  # story quests ahead of the slowest quester: wait and help them
AHEAD_STOP = 1  # ... until only this many ahead
BACKUP_WAIT = 60.0  # a lone Defeat <Name> (a boss): this long for a teammate to come
BACKUP_RANGE = 2500.0  # a teammate this close (same zone) is backup
ARROW_STUCK = 90.0  # the same objective this long: follow the quest arrow
ARROW_FAR = 1500.0  # ... when it points farther than this

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
    at_sigil: tuple[str, float, float] | None = None  # (zone, x, y): waiting at this dungeon sigil
    story: int = 0  # position in the main story (questlist.story_place "overall"; 0: unknown)
    wants_backup: bool = False  # about to take on a boss: the booster (or a teammate) comes

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
            at_sigil=(str(data["at_sigil"][0]), float(data["at_sigil"][1]), float(data["at_sigil"][2]))
            if data.get("at_sigil") else None,
            story=int(data.get("story") or 0),
            wants_backup=bool(data.get("wants_backup")),
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


def keep_awake() -> None:
    """Keep Windows from going to sleep while this process runs (an overnight
    run otherwise ends at the PC's sleep timer). The screen may still turn
    off. Undone by itself when the process ends."""
    import sys

    if sys.platform != "win32":
        return
    try:
        import ctypes

        # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    except Exception as exc:
        logger.debug(f"quadbox: couldn't keep the PC awake: {exc!r}")


_COUNT = __import__("re").compile(r"\(\s*\d+\s+of\s+\d+\s*\)")


def same_objective(a: str, b: str) -> bool:
    """The same objective, whatever its count ('Defeat Lost Soul in Unicorn
    Way (0 of 2)' = '... (1 of 2)': one fight counts for both)."""
    def key(o: str) -> str:
        return " ".join(_COUNT.sub("", o or "").lower().split())
    return bool(key(a)) and key(a) == key(b)


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
    calling = [m for m in questers if m.wants_backup]
    if calling:
        # A quester about to take on a boss: to them (from anywhere, at once).
        calling.sort(key=lambda m: (m.zone != zone, m.role != lead, QUESTERS.index(m.role)))
        m = calling[0]
        if m.zone != zone:
            return "zone", m.role
        return ("near", m.role) if _dist(m.pos, pos) > FOLLOW_RANGE / 2 else None
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
                  join_range: float = 0.0) -> str | None:
    """A quester's teammate fighting for the same Defeat objective in our zone
    (within `join_range`; 0: anywhere in it): the role to join (the
    nearest), or None. Joining beats starting a fight of our own."""
    if not objective.lower().startswith("defeat"):
        return None
    near = [m for r, m in mates.items() if r in QUESTERS and m.available and m.in_battle
            and m.zone == zone and same_objective(m.objective, objective)
            and (join_range <= 0 or _dist(m.pos, pos) <= join_range)]
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
_at_sigil: list = [None]  # (zone, x, y) of the sigil we're waiting at for the team
_wants_backup: list = [False]
_story: list = [0, -1e9]  # (our story position, when read)


def _story_position(quester, zone: str) -> int:
    from .questlist import load_completed, story_place, world_of_zone

    try:
        place = story_place(quester._active_quest or "", load_completed(), world_of_zone(zone))
    except Exception as exc:
        logger.debug(f"quadbox: story position unread: {exc!r}")
        return 0
    return int(place["overall"]) if place else 0


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


def at_this_sigil(m: Mate, zone: str, sigil) -> bool:
    return m.zone == zone and _dist(m.pos, (sigil.x, sigil.y)) <= AT_SIGIL_RANGE


def gather_plan(me: str, mates: dict[str, Mate], zone: str, sigil, objective: str) -> str:
    """At a dungeon sigil with teammates on the same objective: "wait" (some
    aren't here yet), "lead" (all here and we're the first quester: in we
    go, the others follow by friend teleport), "follow" (all here, another
    leads: wait for them to go in) or "go" (nobody to wait for)."""
    team = [m for r, m in mates.items() if r in QUESTERS and m.available and not m.in_battle
            and same_objective(m.objective, objective)]
    if not objective.strip() or not team:
        return "go"
    if any(not at_this_sigil(m, zone, sigil) for m in team):
        return "wait"
    lead = min([me, *(m.role for m in team)], key=QUESTERS.index)
    return "lead" if lead == me else "follow"


def fight_leader(me: str, mates: dict[str, Mate], objective: str) -> str | None:
    """On a Defeat objective a lower-numbered quester also has: that quester
    leads (starts the fights) and we follow and join, so one fight counts
    for both. Their role, or None (we lead, or nobody shares it)."""
    if me not in QUESTERS or not objective.lower().startswith("defeat"):
        return None
    leads = [m for r, m in mates.items() if r in QUESTERS and m.available
             and QUESTERS.index(r) < QUESTERS.index(me) and same_objective(m.objective, objective)]
    return min(leads, key=lambda m: QUESTERS.index(m.role)).role if leads else None


def story_gap(me_story: int, mates: dict[str, Mate]) -> tuple[int, str | None]:
    """How many story quests we're ahead of the slowest quester, and who that is."""
    behind = [m for r, m in mates.items() if r in QUESTERS and m.available and m.story > 0]
    if me_story <= 0 or not behind:
        return 0, None
    slowest = min(behind, key=lambda m: (m.story, QUESTERS.index(m.role)))
    return me_story - slowest.story, slowest.role


def lone_boss(objective: str) -> bool:
    """'Defeat Lord Nightshade' (one named enemy: a boss, with guards), not
    'Defeat 4 Rattlebones' or 'Defeat Rattlebones (3)'."""
    o = objective.strip()
    if not o.lower().startswith("defeat ") or "(" in o:
        return False
    rest = o[7:].strip()
    return bool(rest) and not rest.split()[0].isdigit()


# --- quester steps (quest.py calls these between steps) ------------------------------

async def gather_at_sigil(quester, outside: str, sigil) -> bool:
    """Before going in by a sigil: wait (GATHER_WAIT at most) for the
    teammates on the same objective, then the first quester goes in and the
    others friend-teleport after it. True if we're in already (followed)."""
    from .upkeep import is_free

    client = quester.client
    objective = quester._last_progress[0] or ""
    me = role()
    if me not in QUESTERS:
        return False
    _at_sigil[0] = (outside, float(sigil.x), float(sigil.y))
    start = time.monotonic()
    said = ""
    quester.controller.allow_idle(GATHER_WAIT + FOLLOW_IN_WAIT + 30)
    try:
        follow_since = None
        while True:
            await quester.controller.checkpoint()
            if await into_mates_dungeon(quester, outside, sigil):
                return True
            mates = read_mates()
            plan = gather_plan(me, mates, outside, sigil, objective)
            waited = time.monotonic() - start
            if plan in ("go", "lead"):
                if plan == "lead":
                    logger.info("quadbox: the team is at the sigil: going in first, they follow")
                return False
            if plan == "follow":
                follow_since = follow_since or time.monotonic()
                if time.monotonic() - follow_since > FOLLOW_IN_WAIT:
                    logger.warning("quadbox: the teammate who leads never went in: going in")
                    return False
            elif waited > GATHER_WAIT:
                logger.info(f"quadbox: waited {GATHER_WAIT:.0f}s at the sigil for the team: going in")
                return False
            if plan != said:
                said = plan
                logger.info("quadbox: at the dungeon sigil, " + (
                    "waiting for the teammates on this objective" if plan == "wait"
                    else "waiting for the first quester to go in"))
            if not await is_free(client):
                return False
            await asyncio.sleep(2.0)
    finally:
        _at_sigil[0] = None
        quester.controller.end_idle()


async def _assist(client, plan, mates) -> None:
    """Carry out a booster-style plan (join a fight, come near, friend teleport)."""
    kind, who = plan
    mate = mates[who]
    zone = await client.zone_name() or ""
    if kind == "fight" and mate.zone == zone:
        await _join_fight(client, mate)
    elif kind == "near":
        await _teleport_onto(client, mate.pos, engage=False)
    else:
        await friend_teleport(client, who, "in a fight" if kind == "fight" else "in another zone")


async def follow_the_fight_leader(quester) -> bool:
    """Our Defeat objective is a lower-numbered quester's too: stay beside
    them and join the fights they start, rather than start our own (both get
    the credit at once). True while following (the quest step waits)."""
    if not _setting("join_fights", True):
        return False
    client = quester.client
    objective = quester._last_progress[0] or ""
    mates = read_mates()
    lead = fight_leader(role(), mates, objective)
    if lead is None:
        if getattr(quester, "_quad_following", None):
            quester._quad_following = None
        return False
    if getattr(quester, "_quad_following", None) != lead:
        quester._quad_following = lead
        logger.info(f"quadbox: {lead} has {objective!r} too: fighting beside them, not alone")
    quester.controller.allow_idle(30)
    try:
        m = mates[lead]
        zone, pos = await client.zone_name() or "", await _position(client)
        if m.zone != zone:
            if time.time() - m.zone_since >= ZONE_SETTLE:
                await friend_teleport(client, lead, "to fight beside them")
        elif m.in_battle:
            await _join_fight(client, m)
        elif _dist(m.pos, pos) > FOLLOW_RANGE / 3:
            await _teleport_onto(client, m.pos, engage=False)
        else:
            await asyncio.sleep(2.0)
        quester._last_progress_time = time.monotonic()  # (waiting on purpose: no stall)
    finally:
        quester.controller.end_idle()
    return True


async def help_the_slowest(quester) -> bool:
    """AHEAD_START story quests ahead of the slowest quester: stop questing
    and help them (their fights, beside them) until AHEAD_STOP ahead. True
    while helping (the quest step waits)."""
    client = quester.client
    mates = read_mates()
    gap, slow = story_gap(_story[0], mates)
    helping = getattr(quester, "_quad_helping", None)
    if helping and (slow is None or gap <= AHEAD_STOP):
        logger.success(f"quadbox: {helping} caught up: back to our own quest")
        quester._quad_helping = None
        return False
    if not helping and (slow is None or gap < AHEAD_START):
        return False
    if helping != slow:
        logger.info(f"quadbox: {gap} story quests ahead of {slow}: waiting and helping them")
        quester._quad_helping = slow
    quester.controller.allow_idle(30)
    try:
        here, pos = await client.zone_name() or "", await _position(client)
        plan = booster_plan({slow: mates[slow]}, here, pos, slow)
        if plan is not None:
            await _assist(client, plan, mates)
        else:
            await asyncio.sleep(3.0)
        quester._last_progress_time = time.monotonic()  # (waiting on purpose: no stall)
    finally:
        quester.controller.end_idle()
    return True


async def wait_for_backup(quester) -> bool:
    """A lone named Defeat target (a boss and its guards) with no teammate
    near: call for backup and wait up to BACKUP_WAIT, once per objective.
    True while waiting."""
    client = quester.client
    objective = quester._last_progress[0] or ""
    if not lone_boss(objective) or getattr(quester, "_quad_backup_for", None) == objective:
        return False
    quester._quad_backup_for = objective
    zone = await client.zone_name() or ""
    pos = await _position(client)

    def backup_here() -> bool:
        return any(m.available and m.zone == zone and _dist(m.pos, pos) <= BACKUP_RANGE
                   for m in read_mates().values())

    if backup_here():
        return False
    logger.info(f"quadbox: {objective!r}: calling for backup (up to {BACKUP_WAIT:.0f}s)")
    _wants_backup[0] = True
    quester.controller.allow_idle(BACKUP_WAIT + 15)
    try:
        end = time.monotonic() + BACKUP_WAIT
        while time.monotonic() < end:
            await quester.controller.checkpoint()
            if backup_here():
                logger.success("quadbox: backup is here: taking it on")
                break
            if await client.in_battle():
                break
            await asyncio.sleep(2.0)
        else:
            logger.info("quadbox: no backup came: taking it on anyway")
    finally:
        _wants_backup[0] = False
        quester.controller.end_idle()
        quester._last_progress_time = time.monotonic()
    return True


async def follow_arrow_if_stuck(quester) -> bool:
    """The same objective for ARROW_STUCK seconds and the quest arrow far
    off: go where it points (once per ARROW_STUCK per objective)."""
    from wizwalker import XYZ

    objective = quester._last_progress[0] or ""
    since = getattr(quester, "_last_progress_time", time.monotonic())
    if not objective or time.monotonic() - since < ARROW_STUCK:
        return False
    tried = quester.__dict__.setdefault("_quad_arrow_at", {})
    if time.monotonic() - tried.get(objective, -1e9) < ARROW_STUCK:
        return False
    client = quester.client
    marker = await client.quest_position.position()
    if math.hypot(marker.x, marker.y) < 1:
        return False  # (no arrow)
    me = await client.body.position()
    if me.distance(marker) < ARROW_FAR:
        return False
    tried[objective] = time.monotonic()
    logger.info(f"quadbox: {ARROW_STUCK:.0f}s on {objective!r}: following the quest arrow "
                f"({me.distance(marker):.0f} away)")
    await quester.travel(XYZ(marker.x, marker.y, marker.z))
    return True


async def same_realm(client, controller) -> None:
    """Every REALM_CHECK_EVERY: a lower-numbered teammate posting a spot near
    ours in our zone, but no player there in view: they're in another realm.
    After REALM_MISSES checks in a row, friend teleport to them (their realm)."""
    from .teamup import teammates
    from .upkeep import is_free

    me = role()
    misses: dict[str, int] = {}
    while not controller.stopped.is_set():
        await asyncio.sleep(REALM_CHECK_EVERY)
        try:
            if controller.paused or not await is_free(client):
                continue
            zone = await client.zone_name() or ""
            pos = await client.body.position()
            seen = await teammates(client, pos)
            for r, m in read_mates().items():
                if r == me or ROLES.index(r) > ROLES.index(me) or not m.available or m.in_battle:
                    continue  # (the higher-numbered one moves: q2 to q1, the booster to anyone)
                gap = _dist(m.pos, (pos.x, pos.y))
                if m.zone != zone or gap > REALM_NEAR or gap < 150:
                    # (Right on top of us, they'd read as ourselves: can't tell.)
                    misses.pop(r, None)
                    continue
                if any(_dist(m.pos, (p.x, p.y)) <= REALM_SEEN for p in seen):
                    misses.pop(r, None)
                    continue
                misses[r] = misses.get(r, 0) + 1
                if misses[r] >= REALM_MISSES:
                    misses[r] = 0
                    logger.warning(f"quadbox: {r} should be right here but isn't in view: another realm; "
                                   "friend teleporting to them")
                    await friend_teleport(client, r, "into their realm")
                break
        except Exception as exc:
            from .safety import BotStopped

            if isinstance(exc, BotStopped):
                raise
            logger.debug(f"quadbox: realm check failed: {exc!r}")


async def anti_afk(client, controller) -> None:
    """A small step when the wizard has stood still AFK_NUDGE seconds (free,
    not paused): the game logs idle players out (the booster, waiting
    beside q1, was disconnected)."""
    from wizwalker import Keycode

    from .upkeep import is_free

    last_pos, still_since = None, time.monotonic()
    while not controller.stopped.is_set():
        await asyncio.sleep(15.0)
        try:
            if controller.paused:
                still_since = time.monotonic()  # (the player has it)
                continue
            p = await client.body.position()
            here = (p.x, p.y)
            if last_pos is None or _dist(here, last_pos) > 30 or await client.in_battle():
                last_pos, still_since = here, time.monotonic()
                continue
            if time.monotonic() - still_since < AFK_NUDGE or not await is_free(client):
                continue
            logger.debug("quadbox: standing still a while: a small step (anti-AFK)")
            await client.send_key(Keycode.S, 0.15)
            await asyncio.sleep(0.3)
            await client.send_key(Keycode.W, 0.15)
            still_since = time.monotonic()
        except Exception as exc:
            from .safety import BotStopped

            if isinstance(exc, BotStopped):
                raise
            logger.debug(f"quadbox: anti-AFK step failed: {exc!r}")


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
                if _at_sigil[0]:
                    info["at_sigil"] = _at_sigil[0]
                if _wants_backup[0]:
                    info["wants_backup"] = True
                if time.monotonic() - _story[1] > 30:
                    _story[0], _story[1] = _story_position(quester, zone), time.monotonic()
                info["story"] = _story[0]
                if getattr(quester, "_quad_helping", None):
                    info["helping"] = quester._quad_helping
                if getattr(quester, "_quad_following", None):
                    info["following"] = quester._quad_following
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
                        _setting("join_range", 0.0))
    if not who:
        return False
    last = quester.__dict__.setdefault("_quad_join_at", {})
    if time.monotonic() - last.get(who, -1e9) < JOIN_GAP:
        return False
    last[who] = time.monotonic()
    logger.info(f"quadbox: {who} is fighting for {objective!r} here: joining them, not starting our own")
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
            await _assist(client, plan, mates)
        except Exception as exc:
            if controller.stopped.is_set():
                break
            from .safety import BotStopped

            if isinstance(exc, BotStopped):
                raise
            logger.opt(exception=exc).warning("quadbox: booster step failed; going on")
            await asyncio.sleep(3.0)
