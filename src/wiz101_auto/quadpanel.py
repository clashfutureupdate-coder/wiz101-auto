"""Quadbox v2's quest window: what each wizard is doing, at a glance.

    wiz101-auto quadbox panel

A window that stays on top of the games (untick "On top" to let it go
behind). One card per wizard: its state (running, fighting, paused...), where
it is in the main story ("Wizard City - Quest 5 of 38", with a bar), the
quest's name and the objective it's on (and for how long), a health bar,
level, zone, quests done and deaths, and what the team is doing (helping a
slower quester, waiting at a dungeon sigil, calling for backup). It only
reads the bots' status files (quad/<role>/state/status.json and
quad/shared/<role>.json, every 2 s): opening or closing it never touches the
games or the bots.
"""

from __future__ import annotations

import time

from .quadbox import SHARED, STALE_HEARTBEAT, _read_json, _state, bot_pid, team_roles

REFRESH_MS = 2000

BG = "#14161c"
CARD = "#1f232d"
TEXT = "#eceff4"
DIM = "#9aa3b2"
GOLD = "#e8c46a"
TRACK = "#323846"
STATE_COLORS = {"running": "#4caf50", "fighting": "#ef5350", "paused": "#ffa726",
                "starting": "#42a5f5", "stopped": "#757575"}


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def _health(text: str) -> float | None:
    """'900/1200' -> 0.75 (None if unreadable)."""
    try:
        now, top = (float(x) for x in str(text).split("/"))
        return max(0.0, min(1.0, now / top)) if top else None
    except (TypeError, ValueError):
        return None


def panel_rows(names: dict, now: float | None = None) -> list[dict]:
    """One row per wizard: what the window shows."""
    now = time.time() if now is None else now
    rows = []
    for r in team_roles(names):
        st = _read_json(_state(r) / "status.json")
        link = _read_json(SHARED / f"{r}.json")
        running = bool(bot_pid(r))
        beat = float(st.get("time") or 0)
        if not running:
            state = "stopped"
        elif not st or now - beat > STALE_HEARTBEAT:
            state = "starting"
        elif st.get("state") == "paused":
            state = "paused"
        elif st.get("in_battle"):
            state = "fighting"
        else:
            state = "running"
        story = st.get("story") if isinstance(st.get("story"), dict) else None
        if r == "booster":
            quest, objective, story_line, story_frac = "Booster", "joins the questers' fights", "", None
        else:
            quest = str(st.get("quest") or "")
            objective = str(st.get("objective") or "")
            age = st.get("objective_age_s")
            if objective and isinstance(age, (int, float)):
                objective += f"   ({_age(age)} on it)"
            story_line, story_frac = "", None
            if story:
                story_line = f"{story.get('world', '')}  ·  Quest {story.get('number')} of {story.get('of')}"
                try:
                    story_frac = float(story["number"]) / float(story["of"])
                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                    story_frac = None
                quest = quest or str(story.get("quest") or "")
        note = ""
        if running and link:
            if link.get("helping"):
                note = f"ahead of {link['helping']}: waiting and helping them"
            elif link.get("following"):
                note = f"fighting beside {link['following']} (same objective)"
            elif link.get("at_sigil"):
                note = "at a dungeon entrance: waiting for the team"
            elif link.get("wants_backup"):
                note = "calling for backup before a boss"
        rows.append({
            "role": r,
            "name": (names.get(r) if isinstance(names.get(r), str) else "") or (
                "booster" if r == "booster" else r),
            "state": state,
            "level": str(st.get("level") or ""),
            "health": str(st.get("health") or ""),
            "health_frac": _health(st.get("health") or ""),
            "zone": str(st.get("zone") or "").split("/")[-1],
            "quest": quest or ("-" if running else ""),
            "objective": objective,
            "story_line": story_line,
            "story_frac": story_frac,
            "note": note,
            "done": str(st.get("objectives_completed") or 0) if r != "booster" else "",
            "deaths": str(st.get("deaths") or 0),
        })
    return rows


def run_panel(names: dict) -> int:
    try:
        import tkinter as tk
    except ImportError:
        print("the quest window needs tkinter (part of the python.org installer: "
              "'tcl/tk and IDLE'); the quadbox window shows the same")
        return 1

    root = tk.Tk()
    root.title("Quadbox v2 - quests")
    root.attributes("-topmost", True)
    root.configure(bg=BG)
    root.minsize(460, 200)
    top = tk.BooleanVar(value=True)

    head = tk.Frame(root, bg=BG)
    head.pack(fill="x", padx=12, pady=(10, 4))
    tk.Label(head, text="QUADBOX v2", font=("Segoe UI", 13, "bold"), fg=GOLD, bg=BG).pack(side="left")
    tk.Checkbutton(head, text="On top", variable=top, bg=BG, fg=DIM, selectcolor=CARD,
                   activebackground=BG, activeforeground=TEXT,
                   command=lambda: root.attributes("-topmost", top.get())).pack(side="right")
    clock = tk.Label(head, text="", fg=DIM, bg=BG, font=("Segoe UI", 9))
    clock.pack(side="right", padx=10)
    tk.Label(root, text="Ctrl+P pause / play    Ctrl+C stop    (over a game or this window)",
             fg=DIM, bg=BG, font=("Segoe UI", 8)).pack(anchor="w", padx=12)

    def bar(parent, width=200, height=8):
        c = tk.Canvas(parent, width=width, height=height, bg=CARD, highlightthickness=0)
        c.create_rectangle(0, 0, width, height, fill=TRACK, width=0)
        fill = c.create_rectangle(0, 0, 0, height, fill=GOLD, width=0)
        return c, fill, width, height

    def set_bar(b, frac, color):
        c, fill, width, height = b
        if frac is None:
            c.coords(fill, 0, 0, 0, height)
            return
        c.coords(fill, 0, 0, max(2, int(width * max(0.0, min(1.0, frac)))), height)
        c.itemconfig(fill, fill=color)

    cards = []
    for _ in team_roles(names):
        outer = tk.Frame(root, bg=BG)
        outer.pack(fill="x", padx=12, pady=5)
        stripe = tk.Frame(outer, width=5, bg=STATE_COLORS["stopped"])
        stripe.pack(side="left", fill="y")
        f = tk.Frame(outer, bg=CARD)
        f.pack(side="left", fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        title = tk.Label(f, font=("Segoe UI", 11, "bold"), fg=TEXT, bg=CARD, anchor="w")
        title.grid(row=0, column=0, sticky="w", padx=10, pady=(8, 0))
        badge = tk.Label(f, font=("Segoe UI", 8, "bold"), fg="white", padx=8, pady=1)
        badge.grid(row=0, column=1, sticky="e", padx=10, pady=(8, 0))
        story = tk.Label(f, font=("Segoe UI", 9, "bold"), fg=GOLD, bg=CARD, anchor="w")
        story.grid(row=1, column=0, sticky="w", padx=10, pady=(4, 0))
        sbar = bar(f, 180, 6)
        sbar[0].grid(row=1, column=1, sticky="e", padx=10, pady=(4, 0))
        quest = tk.Label(f, font=("Segoe UI", 11), fg=TEXT, bg=CARD, anchor="w", justify="left",
                         wraplength=420)
        quest.grid(row=2, column=0, columnspan=2, sticky="w", padx=10)
        objective = tk.Label(f, font=("Segoe UI", 9), fg=DIM, bg=CARD, anchor="w", justify="left",
                             wraplength=420)
        objective.grid(row=3, column=0, columnspan=2, sticky="w", padx=10)
        note = tk.Label(f, font=("Segoe UI", 9, "italic"), fg=STATE_COLORS["starting"], bg=CARD, anchor="w")
        note.grid(row=4, column=0, columnspan=2, sticky="w", padx=10)
        info_row = tk.Frame(f, bg=CARD)
        info_row.grid(row=5, column=0, columnspan=2, sticky="we", padx=10, pady=(4, 8))
        hp_label = tk.Label(info_row, font=("Segoe UI", 8), fg=DIM, bg=CARD)
        hp_label.pack(side="left")
        hbar = bar(info_row, 120, 6)
        hbar[0].pack(side="left", padx=(4, 10))
        info = tk.Label(info_row, font=("Segoe UI", 8), fg=DIM, bg=CARD, anchor="w")
        info.pack(side="left")
        cards.append((stripe, title, badge, story, sbar, quest, objective, note, hp_label, hbar, info))

    def refresh():
        try:
            for row, card in zip(panel_rows(names), cards, strict=True):
                stripe, title, badge, story, sbar, quest, objective, note, hp_label, hbar, info = card
                color = STATE_COLORS.get(row["state"], DIM)
                stripe.config(bg=color)
                title.config(text=f"{row['role'].upper()}   {row['name']}"
                             + (f"   ·   lvl {row['level']}" if row["level"] else ""))
                badge.config(text=row["state"].upper(), bg=color)
                story.config(text=row["story_line"] or ("" if row["role"] == "booster" else "Main story"))
                set_bar(sbar, row["story_frac"], GOLD)
                quest.config(text=row["quest"])
                objective.config(text=row["objective"])
                note.config(text=row["note"])
                hf = row["health_frac"]
                hp_label.config(text=f"HP {row['health']}" if row["health"] else "HP -")
                hp_color = "#4caf50" if (hf or 0) > 0.5 else "#ffa726" if (hf or 0) > 0.25 else "#ef5350"
                set_bar(hbar, hf, hp_color)
                bits = [b for b in (row["zone"], f"{row['done']} done" if row["done"] else "",
                                    f"{row['deaths']} deaths") if b]
                info.config(text="   ·   ".join(bits))
            clock.config(text=time.strftime("%H:%M:%S"))
        except Exception as exc:  # (a half-written status file: next time)
            clock.config(text=f"read error: {exc!r}"[:60])
        root.after(REFRESH_MS, refresh)

    refresh()
    root.mainloop()
    return 0
