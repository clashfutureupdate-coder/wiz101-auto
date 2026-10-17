"""Where the bot's shipped data lives.

Everything the bot writes is relative to the folder it runs in (state/, the
logs, docs/CompletedQuests.txt). The shipped data in docs/ (quest lists,
guides, card art) is read from WIZ101_DOCS when set: quadbox runs each
wizard's bot in its own folder (quad/q1...) and points them all at the one
docs/ folder instead of four copies of it.
"""

from __future__ import annotations

import os
from pathlib import Path

DOCS = Path(os.environ.get("WIZ101_DOCS") or "docs")
