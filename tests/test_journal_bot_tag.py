"""journal._bot_tag classification — all 8 bots.

Background (Jul 2 2026 day-14 review): journal._bot_tag only knew
Whale/Funding/Momentum, so every Scalp/Crossover/Breakout/Pair/Reversal
trade was stored with bot="Momentum". The 14-day review lumped 45 trades
under Momentum (38 generic "SL Hit" bracket exits actually belonged to
scalp/crossover), and the dashboard's per-bot cards mis-attributed the
same rows. Same latent-classifier bug fixed in kill_switch._bot_of
(commit 961f1e6) — journal.py had its own copy.

Also covers retag_bot_column() — the backfill that recomputes the stored
bot column for existing rows after the classifier fix.

Run: python -m pytest tests/test_journal_bot_tag.py -v
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_DIR = HERE.parent
sys.path.insert(0, str(BOT_DIR))

import journal


# ─── _bot_tag classification ───────────────────────────────────────────────

def test_bot_tag_whale_prefix():
    assert journal._bot_tag("Whale Track BTC LONG") == "Whale"


def test_bot_tag_funding_prefix():
    assert journal._bot_tag("Funding Fade BTC SHORT") == "Funding"


def test_bot_tag_scalp_suffix():
    assert journal._bot_tag("BTC 5m Scalp") == "Scalp"
    assert journal._bot_tag("LINK 5m Scalp") == "Scalp"
    assert journal._bot_tag("Scalp") == "Scalp"  # bare tag fallback


def test_bot_tag_crossover_suffix():
    assert journal._bot_tag("ETH 1h Crossover") == "Crossover"
    assert journal._bot_tag("Crossover") == "Crossover"


def test_bot_tag_breakout_suffix():
    assert journal._bot_tag("BTC 4H Breakout") == "Breakout"
    assert journal._bot_tag("ETH 1H Breakout") == "Breakout"
    assert journal._bot_tag("Breakout") == "Breakout"


def test_bot_tag_pair_suffix():
    assert journal._bot_tag("ETHBTC Pair") == "Pair"
    assert journal._bot_tag("Pair") == "Pair"


def test_bot_tag_reversal_suffix():
    assert journal._bot_tag("BTC 1D Reversal") == "Reversal"
    assert journal._bot_tag("Reversal") == "Reversal"


def test_bot_tag_momentum_default():
    assert journal._bot_tag("BTC 1D Momentum") == "Momentum"
    assert journal._bot_tag("random nonsense") == "Momentum"
    assert journal._bot_tag("") == "Momentum"
    assert journal._bot_tag(None) == "Momentum"


# ─── retag_bot_column backfill ─────────────────────────────────────────────

def _make_db(tmp_path) -> Path:
    """Minimal trades table with intentionally-wrong bot tags."""
    db = tmp_path / "trades.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE trades (
        id INTEGER PRIMARY KEY, strategy TEXT, bot TEXT)""")
    rows = [
        ("BTC 5m Scalp",        "Momentum"),   # wrong — should be Scalp
        ("ETH 1h Crossover",    "Momentum"),   # wrong — should be Crossover
        ("BTC 4H Breakout",     "Momentum"),   # wrong — should be Breakout
        ("Whale Track ETH",     "Whale"),      # already correct
        ("BTC 1D Momentum",     "Momentum"),   # already correct
    ]
    conn.executemany("INSERT INTO trades (strategy, bot) VALUES (?, ?)", rows)
    conn.commit()
    conn.close()
    return db


def test_retag_dry_run_reports_but_does_not_mutate(tmp_path):
    db = _make_db(tmp_path)
    changed = journal.retag_bot_column(db_path=db, apply=False)
    assert changed == 3  # scalp + crossover + breakout rows need fixing
    conn = sqlite3.connect(db)
    bots = [r[0] for r in conn.execute(
        "SELECT bot FROM trades ORDER BY id")]
    conn.close()
    assert bots == ["Momentum", "Momentum", "Momentum", "Whale", "Momentum"]


def test_retag_apply_fixes_rows(tmp_path):
    db = _make_db(tmp_path)
    changed = journal.retag_bot_column(db_path=db, apply=True)
    assert changed == 3
    conn = sqlite3.connect(db)
    rows = list(conn.execute("SELECT strategy, bot FROM trades ORDER BY id"))
    conn.close()
    assert rows == [
        ("BTC 5m Scalp",     "Scalp"),
        ("ETH 1h Crossover", "Crossover"),
        ("BTC 4H Breakout",  "Breakout"),
        ("Whale Track ETH",  "Whale"),
        ("BTC 1D Momentum",  "Momentum"),
    ]


def test_retag_apply_is_idempotent(tmp_path):
    db = _make_db(tmp_path)
    journal.retag_bot_column(db_path=db, apply=True)
    changed_second = journal.retag_bot_column(db_path=db, apply=True)
    assert changed_second == 0
