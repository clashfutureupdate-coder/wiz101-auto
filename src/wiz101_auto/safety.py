"""Kill switch, pause key and run limits.

Keys are polled with GetAsyncKeyState, so they work while the game window
has focus without installing a global keyboard hook. Hotkeys are written like
"ctrl+shift+q"; any combination of ctrl/shift/alt plus one key works, which
matters on compact keyboards without an F-row.
"""

from __future__ import annotations

import asyncio
import ctypes
import sys
import time
from pathlib import Path

from loguru import logger


class BotStopped(Exception):
    pass


MODIFIERS = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12}
NAMED_KEYS = {
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
    "space": 0x20,
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "capslock": 0x14,
    "pause": 0x13,
    "end": 0x23,
    "home": 0x24,
    "insert": 0x2D,
    "delete": 0x2E,
    "pageup": 0x21,
    "pagedown": 0x22,
    "`": 0xC0,
    "backtick": 0xC0,
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    "\\": 0xDC,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
}


def parse_hotkey(text: str) -> tuple[int, ...]:
    """'ctrl+shift+q' -> virtual-key codes that must all be held."""
    parts = [p.strip().lower() for p in text.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    *mods, key = parts
    codes = []
    for m in mods:
        if m not in MODIFIERS:
            raise ValueError(f"unknown modifier {m!r} in hotkey {text!r} (use ctrl, shift, alt)")
        codes.append(MODIFIERS[m])
    if len(key) == 1 and key.isalnum():
        codes.append(ord(key.upper()))
    elif key in NAMED_KEYS:
        codes.append(NAMED_KEYS[key])
    elif key in MODIFIERS:
        raise ValueError(f"hotkey {text!r} needs a non-modifier key at the end")
    else:
        raise ValueError(f"unknown key {key!r} in hotkey {text!r}")
    return tuple(codes)


def _key_down(vk: int) -> bool:
    if sys.platform != "win32":
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def _combo_down(codes: tuple[int, ...]) -> bool:
    return all(_key_down(c) for c in codes)


GAME_CLASS = "Wizard Graphical Client"  # every Wizard101 window
OWN_TITLES = ("Quadbox v2", "wiz101-auto")  # the launcher windows and the quest window


def needs_focus(codes: tuple[int, ...]) -> bool:
    """Ctrl plus one key (Ctrl+C, Ctrl+P) is everyday copy/print elsewhere:
    such a key only counts over the game or our own windows. With Shift or
    Alt in it (Ctrl+Shift+Q) it counts anywhere, as before."""
    return MODIFIERS["shift"] not in codes and MODIFIERS["alt"] not in codes


def focus_is_ours(class_name: str, title: str) -> bool:
    return class_name == GAME_CLASS or title.startswith(OWN_TITLES)


def _foreground_is_ours() -> bool:
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        title = ctypes.create_unicode_buffer(128)
        user32.GetWindowTextW(hwnd, title, 128)
        return focus_is_ours(cls.value, title.value)
    except Exception:
        return False


def key_pressed(codes: tuple[int, ...]) -> bool:
    """The hotkey is held, where it counts (see needs_focus)."""
    return _combo_down(codes) and (not needs_focus(codes) or _foreground_is_ours())


PAUSED_FILE = Path("state") / "paused.flag"  # the player paused: kept across restarts
STOP_GRACE = 10.0  # seconds a stop key gets before the process ends outright


class Controller:
    def __init__(self, stop_key: str, pause_key: str, max_hours: float):
        self.stop_keys = parse_hotkey(stop_key)
        self.pause_keys = parse_hotkey(pause_key)
        self.deadline = time.monotonic() + max_hours * 3600 if max_hours > 0 else None
        self.deaths = 0
        self.losses = 0  # defeats, and boss fights fled with no attacks left (a loss for the objective)
        self.last_death: tuple[float, str] | None = None  # (monotonic time, zone)
        self.stopped = asyncio.Event()
        self._resume = asyncio.Event()
        self._resume.set()
        self.stop_reason = ""
        self.idle_until = 0.0  # the stall watchdog ignores quiet periods before this
        if PAUSED_FILE.exists():
            # Paused before a restart stays paused (a restart for a code change
            # took the game back from the player mid-play, 2026-10-16).
            self._resume.clear()
            logger.warning("still paused (paused before this start): press the pause key to resume")

    def allow_idle(self, seconds: float):
        """Declare an intentional quiet period (resting, waiting for respawns...)."""
        stack = self.__dict__.setdefault("_idle_stack", [])
        stack.append(self.idle_until)
        del stack[:-50]
        self.idle_until = max(self.idle_until, time.monotonic() + seconds)

    def end_idle(self):
        """End the latest quiet period: back to the one around it, if any (an
        inner end_idle wiped the outer window: the watchdog then closed the
        quest book mid-read and cut the Team Up form short)."""
        stack = self.__dict__.get("_idle_stack") or []
        self.idle_until = stack.pop() if stack else 0.0

    def stop(self, reason: str):
        if not self.stopped.is_set():
            self.stop_reason = reason
            logger.warning(f"stopping: {reason}")
            self.stopped.set()
            self._resume.set()

    @property
    def paused(self) -> bool:
        return not self._resume.is_set()

    def record_death(self, zone: str = ""):
        from . import lifetime
        from .questlist import world_of_zone

        lifetime.load()  # seed the totals from the log before this death is logged
        self.deaths += 1
        self.losses += 1
        self.last_death = (time.monotonic(), zone)
        total = lifetime.add_death(world_of_zone(zone))
        # Deaths never stop the bot: losing fights is handled per objective
        # (setbacks puts a quest aside and the bot levels up elsewhere).
        logger.warning(f"wizard defeated ({self.deaths} this session, {total} in all)")

    def record_boss_flee(self, zone: str = ""):
        """A boss fight fled with no attack cards left: lost, though alive
        (Papa Jacobo: fled again and again, never counted, no Team Up)."""
        self.losses += 1
        self.last_death = (time.monotonic(), zone)
        logger.warning("fled the boss fight with no attacks left: counted as a loss")

    async def checkpoint(self):
        """Await between actions: blocks while paused, raises when stopped."""
        await self._resume.wait()
        if self.stopped.is_set():
            raise BotStopped(self.stop_reason)

    def set_paused(self, paused: bool, how: str = ""):
        """Pause or resume; the pause is kept on disk across restarts."""
        if paused == self.paused:
            return
        if paused:
            self._resume.clear()
            logger.info(f"paused{how} (press again to resume)")
            try:
                PAUSED_FILE.parent.mkdir(exist_ok=True)
                PAUSED_FILE.touch()
            except OSError:
                pass
        else:
            self._resume.set()
            logger.info(f"resumed{how}")
            PAUSED_FILE.unlink(missing_ok=True)

    def start_key_thread(self, loop: asyncio.AbstractEventLoop):
        """The stop and pause keys, watched from their own thread: a bot stuck
        in one long step (13:23, healing before Howling Chaney) never read
        them, and the player couldn't get the game back. Stop that isn't
        honoured within STOP_GRACE seconds ends the process outright."""
        import os
        import threading

        from .service import STOP_FILE

        def run():
            was_pause_down = False
            while True:
                if key_pressed(self.stop_keys):
                    self._key_stop = True  # (before the file: the log names the key, not the terminal)
                    try:
                        STOP_FILE.parent.mkdir(exist_ok=True)
                        STOP_FILE.touch()
                    except OSError:
                        pass
                    loop.call_soon_threadsafe(self.stop, "stop key pressed")
                    end = time.monotonic() + STOP_GRACE
                    while time.monotonic() < end:
                        time.sleep(0.2)
                    # Still here: the loop is stuck. Out, whatever the hooks.
                    try:
                        logger.error("stop key not honoured in time: ending the bot now "
                                     "(restart Wizard101 before the next run)")
                    finally:
                        os._exit(3)
                pause_down = key_pressed(self.pause_keys)
                if pause_down and not was_pause_down:
                    loop.call_soon_threadsafe(self.set_paused, not self.paused)
                was_pause_down = pause_down
                time.sleep(0.05)

        threading.Thread(target=run, name="hotkeys", daemon=True).start()

    async def watch(self):
        from .service import STOP_FILE

        self.start_key_thread(asyncio.get_running_loop())
        ticks = 0
        while not self.stopped.is_set():
            ticks += 1
            if ticks % 10 == 0 and STOP_FILE.exists():
                self.stop("stop key pressed" if getattr(self, "_key_stop", False)
                          else "stop requested from the terminal")
                break
            if ticks % 10 == 0:
                from .control import PAUSE_REQUEST

                if PAUSE_REQUEST.exists():  # the control page's pause / resume button
                    PAUSE_REQUEST.unlink(missing_ok=True)
                    self.set_paused(not self.paused, " (control page)")
            # (The stop and pause keys: start_key_thread. The terminal's stop
            # request, above, also keeps the supervisor from restarting it.)
            if self.deadline and time.monotonic() > self.deadline:
                self.stop("max run time reached")
                break
            await asyncio.sleep(0.05)
