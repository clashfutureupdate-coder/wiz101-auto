"""Walking like a player: W held down the whole way, the wizard steered a
little at a time toward a point ahead on the path (pure pursuit).

WizWalker's `goto` walks one waypoint at a time: it snaps the facing to the
waypoint, holds W for the computed time and lets go, so a path walk stopped
and turned sharply at every waypoint (the player: "it stops and then starts
again, it's jerky"). Here the key stays down and the turns are spread over
several ticks, so corners are rounded like a player's.

`movement.walk: true` (config.yaml) makes ordinary teleports within a zone
walks along the zone's walkable map (safe_teleport), the teleport only the
fallback when there's no path or the walk gets stuck.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time

from loguru import logger
from wizwalker import XYZ, Keycode

TICK = 0.05  # seconds between steering updates
LOOKAHEAD = 140.0  # steer toward the path point this far ahead (250 cut corners into walls)
ARRIVE = 120.0  # this close to the last point: there
MAX_TURN = math.radians(25)  # most the facing turns per tick (a quick mouse turn, not a snap)
STUCK_SECONDS = 0.6  # no progress this long: stuck (1.5 s, and the player watched it walk into a corner)
STUCK_MOVE = 30.0  # less than this moved in STUCK_SECONDS counts as no progress
STUCK_TRIES = 4  # strafes before giving up (the caller teleports)
STRAFE_SECONDS = 0.5  # a strafe held together with forward, round what we're caught on
WIZARD_SPEED = 580.0  # units a second at no speed bonus (WizWalker's constant)
FALL_DROP = 400.0  # a drop this big below the last ground: fell off a ledge (Ravenscar's cliff)


def yaw_to(src: XYZ, dst: XYZ) -> float:
    """The game's yaw facing from `src` toward `dst` (WizWalker's
    calculate_perfect_yaw: 0 along +y... as the game counts it)."""
    from wizwalker.utils import calculate_perfect_yaw

    try:
        return calculate_perfect_yaw(src, dst)
    except (ValueError, ZeroDivisionError):
        # acos of a hair over 1 when the points line up (a crashed step in
        # the Waterworks): a nudge off that line.
        try:
            return calculate_perfect_yaw(src, XYZ(dst.x + 0.5, dst.y + 0.5, dst.z))
        except (ValueError, ZeroDivisionError):
            return 0.0


def turn_toward(current: float, desired: float, most: float = MAX_TURN) -> float:
    """`current` turned toward `desired` by at most `most` (radians, the short way)."""
    diff = (desired - current + math.pi) % (2 * math.pi) - math.pi
    if abs(diff) <= most:
        return desired
    return current + math.copysign(most, diff)


def pursuit_index(path: list[XYZ], pos: XYZ, start: int, lookahead: float = LOOKAHEAD) -> int:
    """The path point to steer at: from `start`, the first one at least
    `lookahead` away (the last one at the end)."""
    def d(a, b) -> float:
        return math.dist((a.x, a.y), (b.x, b.y))

    i = start
    # Past the close points, and past any already behind us (the next one is
    # nearer to us than to it): never steer back toward a passed waypoint.
    while i < len(path) - 1 and (d(pos, path[i]) < lookahead
                                 or d(pos, path[i + 1]) < d(path[i], path[i + 1])):
        i += 1
    return i


def path_length(pos: XYZ, path: list[XYZ]) -> float:
    pts = [(pos.x, pos.y), *((p.x, p.y) for p in path)]
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:], strict=False))


class _HeldKey:
    """A key held down (sent every 50 ms, as WizWalker does) until released."""

    def __init__(self, window: int, key: Keycode = Keycode.W):
        self.window, self.key, self.task = window, key, None

    def press(self):
        from wizwalker.utils import _send_keydown_forever

        if self.task is None:
            self.task = asyncio.create_task(_send_keydown_forever(self.window, self.key))

    async def release(self):
        from wizwalker.utils import _send_keyup

        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        _send_keyup(self.window, self.key)


async def walk_route(client, path: list[XYZ], zone: str | None = None, arrive: float = ARRIVE,
                     abort=None) -> str:
    """Walk the waypoints smoothly. "arrived", "zone" (walked through a door),
    "aborted" (`abort()` said so: a fight, a dialogue), "fell" (off a ledge:
    back on the last ground), "stuck" or "timeout"."""
    if not path:
        return "arrived"
    body = client.body
    controller = getattr(client, "_controller", None)
    start = await body.position()
    try:
        bonus = (await client.client_object.speed_multiplier()) / 100 + 1
    except Exception:
        bonus = 1.0
    deadline = time.monotonic() + path_length(start, path) / (WIZARD_SPEED * bonus) * 2 + 5
    w = _HeldKey(client.window_handle)
    i, stuck_tries = 0, 0
    last_pos, last_move = start, time.monotonic()
    ground = start
    try:
        w.press()
        while time.monotonic() < deadline:
            if controller is not None and (controller.paused or controller.stopped.is_set()):
                await w.release()
                await controller.checkpoint()  # (waits while paused; raises once stopped)
                w.press()
            if zone is not None and await client.zone_name() != zone:
                return "zone"  # walked through a door into the next zone
            if abort is not None and await abort():
                return "aborted"
            pos = await body.position()
            if pos.z < ground.z - FALL_DROP:
                await w.release()
                await client.teleport(ground)
                return "fell"
            if pos.z >= ground.z - 50:
                ground = pos
            goal = path[-1]
            if math.dist((pos.x, pos.y), (goal.x, goal.y)) < arrive:
                return "arrived"
            i = pursuit_index(path, pos, i)
            await body.write_yaw(turn_toward(await body.yaw(), yaw_to(pos, path[i])))
            if math.dist((pos.x, pos.y), (last_pos.x, last_pos.y)) >= STUCK_MOVE:
                last_pos, last_move = pos, time.monotonic()
            elif time.monotonic() - last_move > STUCK_SECONDS:
                stuck_tries += 1
                if stuck_tries > STUCK_TRIES:
                    logger.debug(f"smoothwalk: stuck at ({pos.x:.0f}, {pos.y:.0f}); giving up")
                    return "stuck"
                # Strafe while still walking forward (the player: left, then
                # right), round whatever we're caught on, then on.
                await strafe(client, stuck_tries)
                last_pos, last_move = await body.position(), time.monotonic()
            await asyncio.sleep(TICK)
        return "timeout"
    finally:
        await w.release()


def strafe_key(tries: int) -> Keycode:
    """Left first, then right, alternating."""
    return Keycode.A if tries % 2 else Keycode.D


def stalled(moved: float, seconds: float) -> bool:
    """Walking but barely moving for STUCK_SECONDS: caught on a wall or corner."""
    return seconds > STUCK_SECONDS and moved < STUCK_MOVE


async def strafe(client, tries: int):
    """Hold a strafe key alongside forward (W stays held by the caller) for a moment."""
    side = _HeldKey(client.window_handle, strafe_key(tries))
    try:
        side.press()
        await asyncio.sleep(STRAFE_SECONDS)
    finally:
        await side.release()


async def strafe_while_walking(client, tries: int):
    """Forward and a strafe together for STRAFE_SECONDS (for step-walkers that
    don't hold W themselves)."""
    w = _HeldKey(client.window_handle)
    try:
        w.press()
        await strafe(client, tries)
    finally:
        await w.release()


async def walk_straight(client, target: XYZ, seconds: float, zone: str | None = None, stop=None) -> str:
    """Walk at `target` (re-aiming each tick) for up to `seconds`; strafe off
    walls and corners at once. "arrived", "zone", "stopped" (`stop()` said
    so), "stuck" or "timeout"."""
    body = client.body
    w = _HeldKey(client.window_handle)
    end = time.monotonic() + seconds
    last_pos, last_move, tries = await body.position(), time.monotonic(), 0
    try:
        w.press()
        while time.monotonic() < end:
            if zone is not None and await client.zone_name() != zone:
                return "zone"
            if stop is not None and await stop():
                return "stopped"
            pos = await body.position()
            if math.dist((pos.x, pos.y), (target.x, target.y)) < 40:
                return "arrived"
            await body.write_yaw(yaw_to(pos, target))
            moved = math.dist((pos.x, pos.y), (last_pos.x, last_pos.y))
            if moved >= STUCK_MOVE:
                last_pos, last_move = pos, time.monotonic()
            elif stalled(moved, time.monotonic() - last_move):
                tries += 1
                if tries > STUCK_TRIES:
                    return "stuck"
                logger.debug(f"walk: stuck at ({pos.x:.0f}, {pos.y:.0f}); strafing {strafe_key(tries).name}")
                await strafe(client, tries)
                last_pos, last_move = await body.position(), time.monotonic()
            await asyncio.sleep(TICK)
        return "timeout"
    finally:
        await w.release()


def install_goto(client):
    """WizWalker's goto held W for distance/speed seconds, blind: into a corner
    of the Basstille it walked until the time ran out. Ours re-aims, strafes
    off walls at once, and teleports to the spot when strafing doesn't help."""
    async def goto(x: float, y: float):
        here = await client.body.position()
        target = XYZ(x, y, here.z)
        try:
            bonus = (await client.client_object.speed_multiplier()) / 100 + 1
        except Exception:
            bonus = 1.0
        seconds = math.dist((here.x, here.y), (x, y)) / (WIZARD_SPEED * bonus) * 1.5 + 1
        if await walk_straight(client, target, seconds) == "stuck":
            logger.debug(f"walk: still stuck after strafing; teleporting to ({x:.0f}, {y:.0f})")
            await client.teleport(target)

    client.goto = goto


async def walk_to(client, dest: XYZ, zone: str | None = None, abort=None) -> bool:
    """Walk to `dest` in this zone along its walkable map. False when there's
    no path (or it got stuck): the caller teleports instead."""
    from .walkmap import walk_path

    zone = zone or await client.zone_name() or ""
    here = await client.body.position()
    path = await walk_path(zone, here, dest)
    if not path:
        return False
    return await walk_route(client, path, zone, abort=abort) in ("arrived", "zone")
