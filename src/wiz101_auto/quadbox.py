"""Quadbox: 3 questers and a booster, one bot per game (quadbox-v2.bat).

    wiz101-auto quadbox            start the four bots (or pick up ones still running) and show them
    wiz101-auto quadbox logins     save each wizard's game login (frozen/crashed games restart by themselves)
    wiz101-auto quadbox status     one line per wizard
    wiz101-auto quadbox stop       stop all four cleanly

Each wizard's bot runs in its own folder under quad/ (git-ignored), so its
state, logs and completed-quest list are its own:

    quad/q1, quad/q2, quad/q3, quad/booster   the bots' folders
    quad/shared                               where they post their whereabouts (quadlink.py)
    quad/roles.json                           which game is which wizard

Which game is which: the booster is the highest level of the four. The
questers are remembered by school, level and max health; the first time (or
when those can't tell them apart) they are q1, q2, q3 in the order their
games were opened.

The bots run detached from this window, each under its own supervisor
(restarts after crashes, game restarts with the saved logins). The stop key
(Ctrl+Shift+Q) stops all four; closing this window with the X also asks them
to stop, but the window may close before they finish.
"""

from __future__ import annotations

import itertools
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .quadlink import QUESTERS, ROLES

VERSION = "Quadbox v2"
QUAD = Path("quad")
SHARED = QUAD / "shared"
ROLES_FILE = QUAD / "roles.json"
SPOILER_DLL = Path(r"C:\ProgramData\KingsIsle Entertainment\Wizard101\Bin\MSIMG32.dll")
START_GAP = 8.0  # seconds between starting the bots (hooking four games at once is heavy)
STOP_WAIT = 45.0  # for the bots to stop cleanly before they are ended outright
IDENTIFY_TIMEOUT = 240.0
ADOPT_AFTER = 45.0  # a new game window this old, with no wizard, goes to the wizard whose game closed
SHOW_EVERY = 5.0
STALE_HEARTBEAT = 60.0
PLACEHOLDER_NAMES = {"name one", "name two", "name three", ""}


def role_dir(role: str) -> Path:
    return QUAD / role


def _state(role: str) -> Path:
    return role_dir(role) / "state"


# --- which game is which wizard (pure) -------------------------------------------

@dataclass
class Game:
    pid: int
    started: float = 0.0  # when its process started (opening order)
    level: int = 0
    school: str = ""
    max_hp: int = 0


def _cost(game: Game, seen: dict) -> float:
    """How unlike the wizard remembered in `seen` this game's is (inf: not them)."""
    if not seen or not seen.get("school"):
        return float("inf")
    if game.school.lower() != str(seen["school"]).lower():
        return float("inf")
    level = int(seen.get("level") or 0)
    if game.level < level:
        return float("inf")  # (levels never go down)
    return (game.level - level) * 10 + abs(game.max_hp - int(seen.get("max_hp") or 0)) / 50


def match_questers(games: list[Game], memory: dict) -> list[Game] | None:
    """The three questers' games in q1, q2, q3 order, as remembered; None
    when the memory can't tell (none yet, or two equally likely)."""
    if not all(memory.get(r) for r in QUESTERS):
        return None
    scored = []
    for perm in itertools.permutations(games):
        cost = sum(_cost(g, memory[r]) for g, r in zip(perm, QUESTERS, strict=True))
        scored.append((cost, perm))
    scored.sort(key=lambda x: x[0])
    best = scored[0][0]
    if best == float("inf"):
        return None
    if len(scored) > 1 and scored[1][0] - best < 1e-6:
        return None  # (two ways fit as well: the opening order decides)
    return list(scored[0][1])


def assign_roles(games: list[Game], memory: dict) -> tuple[dict[str, int], str]:
    """{role: pid} for four games, and how the questers were told apart.
    ValueError (with what to do) when it can't be done."""
    if len(games) != 4:
        raise ValueError(f"{len(games)} Wizard101 game(s) are open; quadbox needs exactly 4 "
                         "(3 questers and the booster), each logged in and standing in the world")
    top = max(g.level for g in games)
    highest = [g for g in games if g.level == top]
    if len(highest) == 1:
        booster = highest[0]
    else:
        remembered = memory.get("booster") or {}
        fits = [g for g in highest if _cost(g, remembered) < float("inf")]
        if len(fits) != 1:
            raise ValueError(f"{len(highest)} wizards are level {top}: the booster must be the "
                             "highest level of the 4")
        booster = fits[0]
    rest = [g for g in games if g is not booster]
    order = match_questers(rest, memory)
    how = "remembered"
    if order is None:
        order = sorted(rest, key=lambda g: (g.started, g.pid))
        how = "the order their games were opened"
    roles = {r: g.pid for r, g in zip(QUESTERS, order, strict=True)}
    roles["booster"] = booster.pid
    return roles, how


def adopt_window(role_pids: dict[str, int], alive: set[int], windows: dict[int, float],
                 now: float) -> tuple[str, int] | None:
    """A wizard's game closed and one new game is open (the player started
    it again): (role, new pid). Only when it's clear: one wizard without a
    game, one unclaimed game, open ADOPT_AFTER seconds (a supervisor
    restarting that game claims it itself sooner)."""
    missing = [r for r, pid in role_pids.items() if pid not in alive]
    claimed = set(role_pids.values())
    unclaimed = [pid for pid, started in windows.items() if pid not in claimed]
    if len(missing) != 1 or len(unclaimed) != 1:
        return None
    if now - windows[unclaimed[0]] < ADOPT_AFTER:
        return None
    return missing[0], unclaimed[0]


def check_names(names: dict) -> str:
    """'' if the questers' names are filled in, else what to fix."""
    bad = [r for r in QUESTERS if str(names.get(r, "")).strip().lower() in PLACEHOLDER_NAMES]
    if not bad:
        return ""
    return ("Put your questers' names in config.yaml first (" + ", ".join(bad) + " missing). At the "
            "bottom, under quadbox:, exactly as the Friends list shows them:\n"
            '  names: {q1: "Name One", q2: "Name Two", q3: "Name Three"}\n'
            "Keep the spaces at the start of the line, then save with Ctrl+S.")


# --- files -----------------------------------------------------------------------

def load_memory() -> dict:
    try:
        return dict(json.loads(ROLES_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return {}


def save_memory(memory: dict) -> None:
    try:
        QUAD.mkdir(exist_ok=True)
        tmp = ROLES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(memory, indent=1), encoding="utf-8")
        tmp.replace(ROLES_FILE)
    except OSError:
        pass


def _read_json(path: Path) -> dict:
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return {}


def game_pid(role: str) -> int:
    """The game this wizard's bot plays (its supervisor updates it after a game restart)."""
    try:
        return int(_read_json(_state(role) / "game_client.json").get("pid") or 0)
    except (TypeError, ValueError):
        return 0


def set_game_pid(role: str, pid: int) -> None:
    _state(role).mkdir(parents=True, exist_ok=True)
    (_state(role) / "game_client.json").write_text(json.dumps({"pid": pid, "hwnd": 0}), encoding="utf-8")


def bot_pid(role: str) -> int:
    from .service import pid_alive

    try:
        pid = int((_state(role) / "bot.pid").read_text().strip())
    except (OSError, ValueError):
        return 0
    return pid if pid_alive(pid) else 0


def _tail(path: Path, n: int = 1) -> list[str]:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4000))
            return [ln.decode("utf-8", "replace").rstrip() for ln in f.read().splitlines()[-n:]]
    except OSError:
        return []


# --- Windows ---------------------------------------------------------------------

def _process_started(pid: int) -> float:
    """When the process started (seconds since the epoch; 0 if unknown)."""
    if sys.platform != "win32":
        return 0.0
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return 0.0
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return 0.0
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return ticks / 1e7 - 11644473600.0  # (FILETIME: 100 ns since 1601)
    finally:
        k32.CloseHandle(handle)


def game_windows() -> dict[int, float]:
    """Open Wizard101 games: {pid: when it started}."""
    from .gamerestart import all_game_windows, window_pid

    pids = {window_pid(h) for h in all_game_windows()} - {0}
    return {pid: _process_started(pid) for pid in pids}


def _alive(pid: int) -> bool:
    from .service import pid_alive

    return pid_alive(pid)


# --- reading one game's wizard ------------------------------------------------------

def identify_main(pid: int) -> int:
    """`quad-identify --pid N` (run by quadbox in quad/_identify): hook that
    game, print its wizard's level, school and max health as JSON, unhook."""
    import asyncio

    from .gamerestart import remember_game

    remember_game(pid)

    async def read() -> dict:
        from .bot import close_handler, connect, new_handler
        from .deck import current_school

        handler = new_handler()
        try:
            client = await connect(handler)
            stats = client.stats
            return {"pid": pid, "level": int(await stats.reference_level()),
                    "school": await current_school(client), "max_hp": int(await stats.max_hitpoints()),
                    "zone": await client.zone_name() or ""}
        finally:
            await close_handler(handler)

    try:
        info = asyncio.run(read())
    except BaseException as exc:  # (SystemExit from connect: its message says why)
        print(json.dumps({"pid": pid, "error": str(exc) or repr(exc)}))
        return 1
    print(json.dumps(info))
    return 0


def identify(pid: int) -> Game:
    folder = QUAD / "_identify" / str(pid)
    (folder / "state").mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "WIZ101_ROLE"}
    proc = subprocess.run([sys.executable, "-m", "wiz101_auto", "quad-identify", "--pid", str(pid)],
                          cwd=folder, env=env, capture_output=True, text=True, timeout=IDENTIFY_TIMEOUT)
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("{"):
            data = json.loads(line)
            if data.get("error"):
                raise RuntimeError(f"game {pid}: {data['error']}")
            return Game(pid, _process_started(pid), int(data["level"]), str(data["school"]),
                        int(data["max_hp"]))
    raise RuntimeError(f"game {pid}: couldn't read its wizard ({(proc.stdout + proc.stderr)[-400:]})")


# --- starting and stopping ------------------------------------------------------------

def start_bot(role: str, config: Path) -> int:
    folder = role_dir(role)
    (folder / "state").mkdir(parents=True, exist_ok=True)
    (folder / "docs").mkdir(exist_ok=True)  # (its own CompletedQuests.txt)
    env = os.environ.copy()
    env.update(WIZ101_ROLE=role, WIZ101_SHARED=str(SHARED.resolve()),
               WIZ101_DOCS=str(Path("docs").resolve()))
    cmd = [sys.executable, "-m", "wiz101_auto", "supervise", "-c", str(config.resolve()), "--role", role]
    # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW: this window closing doesn't take the bots with it
    flags = 0x00000008 | 0x00000200 | 0x08000000 if sys.platform == "win32" else 0
    with open(folder / "state" / "bot.out", "a", encoding="utf-8") as out:
        out.write(f"\n===== quadbox start {time.strftime('%Y-%m-%d %H:%M:%S')} {role} =====\n")
        out.flush()
        proc = subprocess.Popen(cmd, cwd=folder, env=env, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=flags)
    (folder / "state" / "bot.pid").write_text(str(proc.pid))
    return proc.pid


def ask_stop(roles=ROLES) -> None:
    for r in roles:
        try:
            _state(r).mkdir(parents=True, exist_ok=True)
            (_state(r) / "stop.request").write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
        except OSError:
            pass


def stop_all(wait: float = STOP_WAIT) -> int:
    """Stop all four cleanly; 0 if they all did."""
    running = {r: bot_pid(r) for r in ROLES if bot_pid(r)}
    if not running:
        print("no quadbox bots running.")
        return 0
    ask_stop(running)
    print(f"stopping {', '.join(running)} (they unhook their games; up to {wait:.0f}s)...",
          end="", flush=True)
    end = time.monotonic() + wait
    while time.monotonic() < end and any(_alive(p) for p in running.values()):
        time.sleep(1.0)
        print(".", end="", flush=True)
    stuck = [r for r, p in running.items() if _alive(p)]
    if not stuck:
        print(" all stopped.")
        for r in running:
            (_state(r) / "bot.pid").unlink(missing_ok=True)
        return 0
    print(f" {', '.join(stuck)} still running; ending them. Restart those games before the next run "
          "(their hooks were left in).")
    for r in stuck:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(running[r]), "/T", "/F"], capture_output=True)
        (_state(r) / "bot.pid").unlink(missing_ok=True)
    return 2


# --- showing them -------------------------------------------------------------------

def status_lines(names: dict) -> list[str]:
    rows = [f"{'wizard':<9}{'name':<20}{'bot':<10}{'lvl':>4}  {'health':<12}{'zone':<26}doing"]
    for r in ROLES:
        st = _read_json(_state(r) / "status.json")
        running = bool(bot_pid(r))
        state = str(st.get("state", "")) if running else "stopped"
        beat = float(st.get("time") or 0)
        try:
            started = (_state(r) / "bot.pid").stat().st_mtime
        except OSError:
            started = 0.0
        if running and beat < started:
            state = "starting"  # (the status is last session's: not connected yet)
        elif running and time.time() - beat > STALE_HEARTBEAT:
            state = "no beat"
        zone = str(st.get("zone") or "").split("/")[-1][:24]
        doing = "fighting" if st.get("in_battle") else str(st.get("objective") or st.get("reason") or "")
        if r == "booster" and running and not st.get("in_battle"):
            doing = "with the questers"
        name = names.get(r, "booster" if r == "booster" else "?")
        rows.append(f"{r:<9}{name[:19]:<20}{state[:9]:<10}{st.get('level', ''):>4}  "
                    f"{str(st.get('health', ''))[:11]:<12}{zone:<26}{doing[:60]}")
    return rows


def _show(names: dict, keys: str) -> None:
    os.system("cls" if sys.platform == "win32" else "clear")
    print(f"{VERSION}  -  {keys}   (quest window: quadbox-v2.bat option 4)")
    print("Don't close this window with the X: stop with the stop key.\n")
    print("\n".join(status_lines(names)))
    print()
    for r in ROLES:
        last = _tail(role_dir(r) / "activity.log", 1)
        if last:
            print(f"{r:<8} {last[0][:110]}")


# --- the commands -----------------------------------------------------------------------

def _load(config: Path):
    from .config import load_config

    return load_config(config)


def _prechecks(config: Path) -> str:
    if sys.platform != "win32":
        return "quadbox runs on Windows, next to the four Wizard101 games."
    if not config.exists():
        return f"{config} not found: run quadbox-v2.bat (it makes one), or copy configs/quadbox.yaml to it"
    if SPOILER_DLL.exists():
        return (f"Spiral Spoiler's MSIMG32.dll is in {SPOILER_DLL.parent}: move it out of that folder "
                "(and restart the games) or the bot can't hook into the game.")
    try:
        cfg = _load(config)
    except Exception as exc:
        return f"config.yaml has a problem: {exc}"
    return check_names(cfg.quadbox.names)


def run(config: Path) -> int:
    from .safety import _combo_down, parse_hotkey
    from .service import running_pid
    from .service import stop as stop_single

    problem = _prechecks(config)
    if problem:
        print(problem)
        return 1
    cfg = _load(config)
    names = dict(cfg.quadbox.names)
    if running_pid():
        print("the single bot (wiz101.bat) is running: stopping it first")
        stop_single()
    QUAD.mkdir(exist_ok=True)
    SHARED.mkdir(exist_ok=True)
    memory = load_memory()
    windows = game_windows()
    running = {r for r in ROLES if bot_pid(r)}
    if running:
        print(f"already running: {', '.join(sorted(running))} (picked up again)")
    todo = [r for r in ROLES if r not in running]
    if todo:
        pids = {r: game_pid(r) or int((memory.get(r) or {}).get("pid") or 0) for r in ROLES}
        known = {r: p for r, p in pids.items() if p in windows}
        if len(set(known.values())) == 4:
            roles = known  # (the same four games as last time)
        else:
            free = [pid for pid in windows if pid not in {pids[r] for r in running}]
            if len(free) != len(todo) or len(windows) != 4:
                print(f"{len(windows)} Wizard101 game(s) are open; quadbox needs exactly 4, each logged "
                      "in and standing in the world.")
                return 1
            if running:
                print("some bots are running and the other games changed: stop them all (quadbox stop) "
                      "and start again")
                return 1
            print("reading which wizard is in which game (about 20 s per game)...")
            games = []
            for pid in sorted(windows, key=lambda p: windows[p]):
                try:
                    g = identify(pid)
                except Exception as exc:
                    print(f"  {exc}")
                    return 1
                print(f"  game {pid}: level {g.level} {g.school}, {g.max_hp} max health")
                games.append(g)
            try:
                roles, how = assign_roles(games, memory)
            except ValueError as exc:
                print(exc)
                return 1
            print(f"booster: game {roles['booster']}; questers by {how}: "
                  + ", ".join(f"{r} = game {roles[r]}" for r in QUESTERS))
            for g in games:
                r = next(k for k, v in roles.items() if v == g.pid)
                memory[r] = {"pid": g.pid, "level": g.level, "school": g.school, "max_hp": g.max_hp}
            save_memory(memory)
        for r in todo:
            set_game_pid(r, roles[r])
        for i, r in enumerate(todo):
            if i:
                time.sleep(START_GAP)
            print(f"starting {r} ({names.get(r, 'booster')}) on game {roles[r]}")
            start_bot(r, config)
    return monitor(config, names, parse_hotkey(cfg.safety.stop_key), _combo_down,
                   f"{cfg.safety.stop_key} stops all 4, {cfg.safety.pause_key} pauses")


def monitor(config: Path, names: dict, stop_keys, combo_down, keys: str) -> int:
    asked = []

    def on_signal(*_):
        asked.append(1)
        ask_stop()  # (the window closing: the bots stop even if this process can't wait)

    signal.signal(signal.SIGINT, on_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, on_signal)  # (the console window's X)
    from .quadlink import keep_awake

    keep_awake()
    open_panel(config)
    last_show = 0.0
    while True:
        if asked or combo_down(stop_keys):
            print("\nstop key: stopping all four bots")
            return stop_all()
        now = time.time()
        if now - last_show >= SHOW_EVERY:
            last_show = now
            try:
                _keep_up(now)
                _show(names, keys)
                if not any(bot_pid(r) for r in ROLES):
                    print("\nall four bots have stopped.")
                    return 0
            except Exception as exc:
                # (Never let a hiccup here end the watch over an overnight run:
                # the bots go on either way; this tries again in a few seconds.)
                _log(f"monitor: {exc!r}")
        time.sleep(0.1)


PANEL_PID = QUAD / "panel.pid"


def open_panel(config: Path) -> None:
    """The quest window (quadpanel.py), in its own process, unless it's open."""
    try:
        if _alive(int(PANEL_PID.read_text().strip())):
            return
    except (OSError, ValueError):
        pass
    try:
        flags = 0x08000000 if sys.platform == "win32" else 0  # NO_WINDOW (its own window still shows)
        proc = subprocess.Popen([sys.executable, "-m", "wiz101_auto", "quadbox", "panel", "-c", str(config)],
                                creationflags=flags, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        PANEL_PID.write_text(str(proc.pid))
    except OSError as exc:
        _log(f"could not open the quest window: {exc!r}")


def _log(text: str) -> None:
    try:
        QUAD.mkdir(exist_ok=True)
        with (QUAD / "quadbox.log").open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


def _keep_up(now: float) -> None:
    """Hand a re-opened game to the wizard whose game closed, and remember
    each wizard's level as it goes up (for telling them apart next time)."""
    memory = load_memory()
    changed = False
    for r in ROLES:
        st = _read_json(_state(r) / "status.json")
        pid = game_pid(r)
        seen = memory.setdefault(r, {})
        if pid and seen.get("pid") != pid:
            seen["pid"], changed = pid, True
        try:
            level = int(st.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        if level and level > int(seen.get("level") or 0):
            seen["level"], changed = level, True
    windows = game_windows()
    hit = adopt_window({r: game_pid(r) for r in ROLES}, set(windows), windows, now)
    if hit:
        role, pid = hit
        set_game_pid(role, pid)
        memory.setdefault(role, {})["pid"], changed = pid, True
        print(f"{role}: its game was closed; game {pid} (opened since) is {role}'s now")
    if changed:
        save_memory(memory)


def logins() -> int:
    import getpass

    from .gamerestart import load_login, save_login

    if sys.platform != "win32":
        print("logins are kept in Windows Credential Manager: run this on Windows")
        return 1
    names = {}
    try:
        names = dict(_load(Path("config.yaml")).quadbox.names)
    except Exception:
        pass
    print("Each wizard's login is kept in Windows Credential Manager, for this Windows user only.")
    print("With them saved, a frozen or crashed game is closed, started and logged in by itself.")
    print("Leave the username empty to skip one.\n")
    for r in ROLES:
        who = names.get(r) or ("the booster" if r == "booster" else r)
        user = input(f"{r} ({who}) username: ").strip()
        if not user:
            continue
        pw = getpass.getpass(f"{r} ({who}) password (not shown): ")
        ok = bool(pw) and save_login(user, pw, r) and (load_login(r) or ("", ""))[0] == user
        print("  saved." if ok else "  not saved.")
    return 0


def main(action: str, config: str) -> int:
    path = Path(config)
    if action == "logins":
        return logins()
    if action == "stop":
        return stop_all()
    if action == "panel":
        from .quadpanel import run_panel

        try:
            names = dict(_load(path).quadbox.names)
        except Exception:
            names = {}
        return run_panel(names)
    if action == "status":
        try:
            names = dict(_load(path).quadbox.names)
        except Exception:
            names = {}
        print("\n".join(status_lines(names)))
        return 0
    return run(path)
