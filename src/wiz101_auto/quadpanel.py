"""Quadbox v2's quest window: what each wizard is doing, at a glance.

    wiz101-auto quadbox panel

A small window that stays on top of the games (untick "On top" to let it go
behind) and shows, for each wizard, its quest and objective, level, health,
zone and whether its bot is running, fighting or paused. It only reads the
bots' status files (quad/<role>/state/status.json, every 2 s): opening or
closing it never touches the games or the bots.
"""

from __future__ import annotations

import time

from .quadbox import STALE_HEARTBEAT, _read_json, _state, bot_pid, team_roles

REFRESH_MS = 2000


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def panel_rows(names: dict, now: float | None = None) -> list[dict]:
    """One row per wizard: the texts the window shows."""
    now = time.time() if now is None else now
    rows = []
    for r in team_roles(names):
        st = _read_json(_state(r) / "status.json")
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
        if r == "booster":
            quest = "(booster: joins the questers' fights)"
            objective = ""
        else:
            quest = str(st.get("quest") or "")
            objective = str(st.get("objective") or "")
            age = st.get("objective_age_s")
            if objective and isinstance(age, (int, float)):
                objective += f"   ({_age(age)} on it)"
        rows.append({
            "role": r,
            "name": (names.get(r) if isinstance(names.get(r), str) else "") or (
                "booster" if r == "booster" else r),
            "state": state,
            "level": str(st.get("level") or ""),
            "health": str(st.get("health") or ""),
            "zone": str(st.get("zone") or "").split("/")[-1],
            "quest": quest or ("-" if running else ""),
            "objective": objective,
            "done": str(st.get("objectives_completed") or 0) if r != "booster" else "",
            "deaths": str(st.get("deaths") or 0),
        })
    return rows


STATE_COLORS = {"running": "#2e7d32", "fighting": "#c62828", "paused": "#ef6c00",
                "starting": "#1565c0", "stopped": "#757575"}


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
    root.configure(bg="#fafafa")
    top = tk.BooleanVar(value=True)
    head = tk.Frame(root, bg="#fafafa")
    head.pack(fill="x", padx=8, pady=(6, 0))
    tk.Label(head, text="Quadbox v2", font=("Segoe UI", 11, "bold"), bg="#fafafa").pack(side="left")
    tk.Checkbutton(head, text="On top", variable=top, bg="#fafafa",
                   command=lambda: root.attributes("-topmost", top.get())).pack(side="right")
    clock = tk.Label(head, text="", fg="#757575", bg="#fafafa")
    clock.pack(side="right", padx=8)

    cards = []
    for _ in team_roles(names):
        f = tk.Frame(root, bg="white", highlightbackground="#e0e0e0", highlightthickness=1)
        f.pack(fill="x", padx=8, pady=4)
        title = tk.Label(f, font=("Segoe UI", 10, "bold"), bg="white", anchor="w")
        title.grid(row=0, column=0, sticky="w", padx=6, pady=(4, 0))
        state = tk.Label(f, font=("Segoe UI", 9, "bold"), bg="white", anchor="e")
        state.grid(row=0, column=1, sticky="e", padx=6)
        quest = tk.Label(f, font=("Segoe UI", 10), bg="white", anchor="w", justify="left", wraplength=420)
        quest.grid(row=1, column=0, columnspan=2, sticky="w", padx=6)
        objective = tk.Label(f, font=("Segoe UI", 9), fg="#424242", bg="white", anchor="w",
                             justify="left", wraplength=420)
        objective.grid(row=2, column=0, columnspan=2, sticky="w", padx=6)
        info = tk.Label(f, font=("Segoe UI", 8), fg="#757575", bg="white", anchor="w")
        info.grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))
        f.columnconfigure(0, weight=1)
        cards.append((title, state, quest, objective, info))

    def refresh():
        try:
            for row, (title, state, quest, objective, info) in zip(panel_rows(names), cards, strict=True):
                title.config(text=f"{row['role']}  {row['name']}"
                             + (f"   lvl {row['level']}" if row["level"] else ""))
                state.config(text=row["state"], fg=STATE_COLORS.get(row["state"], "#424242"))
                quest.config(text=row["quest"])
                objective.config(text=row["objective"])
                bits = [b for b in (f"health {row['health']}" if row["health"] else "", row["zone"],
                                    f"{row['done']} objectives done" if row["done"] else "",
                                    f"{row['deaths']} deaths") if b]
                info.config(text="   ".join(bits))
            clock.config(text=time.strftime("%H:%M:%S"))
        except Exception as exc:  # (a half-written status file: next time)
            clock.config(text=f"read error: {exc!r}"[:60])
        root.after(REFRESH_MS, refresh)

    refresh()
    root.mainloop()
    return 0
