"""Per-regime expectancy report.

Slices closed trades by (strategy, direction, regime tags) and reports
WR / PF / avg-R / count / net P&L per slice. Output:

  - Markdown file at `reports/expectancy_<date>.md` (kept for history)
  - HTML fragment for embedding into the dashboard

Regime tags come from the journal columns `btc_trend_at_entry` and
`atr_regime_at_entry`. If those weren't populated at entry time (legacy
trades from the JSONL era), the row is bucketed under "unknown".

CLI usage:
    venv/bin/python expectancy_report.py
    venv/bin/python expectancy_report.py --bot whale
    venv/bin/python expectancy_report.py --since 2026-04-01
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("crypto_bot.expectancy")


REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def _load_closed_trades(since: Optional[datetime] = None) -> List[dict]:
    from journal import read_trades
    trades = read_trades(max_rows=10000)
    closed = [t for t in trades if t.get("result") in ("WIN", "LOSS")]
    if since is not None:
        closed = [t for t in closed if _close_dt(t) and _close_dt(t) >= since]
    return closed


def _close_dt(t: dict) -> Optional[datetime]:
    raw = t.get("date_closed")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except (TypeError, ValueError):
        return None


def _slice_metrics(trades: List[dict]) -> dict:
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "avg_R": 0.0, "net_pnl": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0}
    wins = [t for t in trades if t.get("result") == "WIN"]
    losses = [t for t in trades if t.get("result") == "LOSS"]
    pnls = [float(t.get("net_pnl") or 0) for t in trades]
    gross_w = sum(float(t.get("net_pnl") or 0) for t in wins)
    gross_l = abs(sum(float(t.get("net_pnl") or 0) for t in losses))
    pf = gross_w / gross_l if gross_l > 0 else float("inf")
    avg_w = gross_w / len(wins) if wins else 0.0
    avg_l = gross_l / len(losses) if losses else 0.0
    # avg_R: net P&L per trade as a multiple of average loss size
    avg_R = sum(pnls) / len(pnls) / avg_l if avg_l > 0 else 0.0
    return {
        "n": len(trades),
        "wr": len(wins) / len(trades) * 100,
        "pf": pf,
        "avg_R": avg_R,
        "net_pnl": sum(pnls),
        "avg_win": avg_w,
        "avg_loss": avg_l,
    }


def _md_table(headers: List[str], rows: List[List[str]]) -> str:
    out = "| " + " | ".join(headers) + " |\n"
    out += "|" + "|".join(["---" if i == 0 else "---:" for i in range(len(headers))]) + "|\n"
    for r in rows:
        out += "| " + " | ".join(str(c) for c in r) + " |\n"
    return out


def _slice_row(label: str, m: dict) -> List[str]:
    pf_s = "∞" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
    return [
        label,
        str(m["n"]),
        f"{m['wr']:.1f}%",
        pf_s,
        f"{m['avg_R']:+.2f}",
        f"${m['net_pnl']:+.2f}",
        f"${m['avg_win']:+.2f}",
        f"${m['avg_loss']:+.2f}",
    ]


def build_report(bot: Optional[str] = None, since: Optional[datetime] = None) -> str:
    """Build the full Markdown report. Returns the markdown string."""
    trades = _load_closed_trades(since=since)
    if bot:
        trades = [t for t in trades if t.get("bot", "").lower() == bot.lower()]

    out = ["# Expectancy Report\n"]
    out.append(f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
               f"closed trades: {len(trades)}"
               + (f" · bot={bot}" if bot else "")
               + (f" · since={since.date()}" if since else "")
               + "_\n\n")

    if not trades:
        out.append("No closed trades match the filter. Nothing to report.\n")
        return "".join(out)

    HEADERS = ["Slice", "n", "WR", "PF", "avg R", "Net PnL", "avg Win", "avg Loss"]

    # Overall
    out.append("## Overall\n\n")
    out.append(_md_table(HEADERS, [_slice_row("All", _slice_metrics(trades))]))
    out.append("\n")

    # By bot
    by_bot: Dict[str, List[dict]] = defaultdict(list)
    for t in trades:
        by_bot[t.get("bot", "Unknown")].append(t)
    rows = [_slice_row(b, _slice_metrics(ts)) for b, ts in sorted(by_bot.items())]
    out.append("## By Bot\n\n" + _md_table(HEADERS, rows) + "\n")

    # By direction
    by_dir: Dict[str, List[dict]] = defaultdict(list)
    for t in trades:
        by_dir[t.get("direction", "?")].append(t)
    rows = [_slice_row(d, _slice_metrics(ts)) for d, ts in sorted(by_dir.items())]
    out.append("## By Direction\n\n" + _md_table(HEADERS, rows) + "\n")

    # By bot × direction (catches the "all whale shorts losing" pattern)
    by_bd: Dict[str, List[dict]] = defaultdict(list)
    for t in trades:
        key = f"{t.get('bot', '?')} {t.get('direction', '?')}"
        by_bd[key].append(t)
    rows = [_slice_row(k, _slice_metrics(ts)) for k, ts in sorted(by_bd.items())]
    out.append("## By Bot × Direction\n\n" + _md_table(HEADERS, rows) + "\n")

    # By symbol
    by_sym: Dict[str, List[dict]] = defaultdict(list)
    for t in trades:
        by_sym[t.get("symbol", "?")].append(t)
    rows_sym = sorted(
        [_slice_row(s, _slice_metrics(ts)) for s, ts in by_sym.items()],
        key=lambda r: float(r[5].replace("$", "").replace("+", "").replace(",", "")),
        reverse=True,
    )
    out.append("## By Symbol\n\n" + _md_table(HEADERS, rows_sym) + "\n")

    # By exit reason
    by_exit: Dict[str, List[dict]] = defaultdict(list)
    for t in trades:
        # Normalize the exit_reason to its leading verb
        raw = (t.get("exit_reason") or "").strip()
        if "TP" in raw:
            tag = "TP"
        elif "SL" in raw:
            tag = "SL"
        elif "BE" in raw:
            tag = "BE"
        elif "Stale" in raw or "stale" in raw:
            tag = "Stale"
        elif "signal flip" in raw.lower():
            tag = "Signal flip"
        elif "Rotated" in raw:
            tag = "Rotation"
        else:
            tag = "Other"
        by_exit[tag].append(t)
    rows = [_slice_row(k, _slice_metrics(ts)) for k, ts in sorted(by_exit.items())]
    out.append("## By Exit Reason\n\n" + _md_table(HEADERS, rows) + "\n")

    # By regime (BTC trend × ATR regime), only if data is populated
    has_regime = any(t.get("btc_trend_at_entry") for t in trades)
    if has_regime:
        by_reg: Dict[str, List[dict]] = defaultdict(list)
        for t in trades:
            btc = t.get("btc_trend_at_entry") or "?"
            atr = t.get("atr_regime_at_entry") or "?"
            by_reg[f"BTC={btc} · ATR={atr}"].append(t)
        rows = [_slice_row(k, _slice_metrics(ts)) for k, ts in sorted(by_reg.items())]
        out.append("## By Regime (BTC trend × ATR)\n\n" + _md_table(HEADERS, rows) + "\n")
    else:
        out.append("## By Regime\n\n_No regime tags recorded yet — entries logged "
                   "before the regime-tag schema went live. Future trades will populate._\n\n")

    return "".join(out)


def write_report(bot: Optional[str] = None, since: Optional[datetime] = None) -> Path:
    """Generate report and write to disk. Returns the path."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    md = build_report(bot=bot, since=since)
    suffix = ""
    if bot:
        suffix += f"_{bot.lower()}"
    if since:
        suffix += f"_since-{since.strftime('%Y%m%d')}"
    fname = f"expectancy_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}.md"
    path = REPORTS_DIR / fname
    path.write_text(md, encoding="utf-8")
    logger.info("Expectancy report written to %s", path)
    return path


def latest_report_html() -> str:
    """Cheap HTML conversion (table-only) for embedding in dashboard."""
    md = build_report()
    # naive markdown→html for the table sections
    lines = md.split("\n")
    html = []
    in_table = False
    for line in lines:
        if line.startswith("## "):
            html.append(f"<h3>{line[3:].strip()}</h3>")
            in_table = False
        elif line.startswith("# "):
            continue  # title is rendered separately
        elif line.startswith("|") and line.strip().endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not in_table:
                html.append('<table style="font-size:0.85em;width:100%;border-collapse:collapse;">')
                html.append("<tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr>")
                in_table = True
            elif all(set(c) <= {"-", ":"} for c in cells if c):
                continue  # separator row
            else:
                html.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        elif line.strip() == "":
            if in_table:
                html.append("</table>")
                in_table = False
        else:
            if in_table:
                html.append("</table>")
                in_table = False
            if line.strip():
                html.append(f"<p>{line}</p>")
    if in_table:
        html.append("</table>")
    return "\n".join(html)


def _parse_since(s: str) -> datetime:
    return datetime.fromisoformat(s)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Generate the per-regime expectancy report")
    p.add_argument("--bot", choices=["momentum", "whale"], help="Limit to one bot")
    p.add_argument("--since", type=_parse_since, help="ISO date — only trades closed since this date")
    p.add_argument("--print", action="store_true", help="Print to stdout instead of writing to disk")
    args = p.parse_args()

    md = build_report(bot=args.bot, since=args.since)
    if args.print:
        print(md)
    else:
        path = write_report(bot=args.bot, since=args.since)
        print(f"Report written to: {path}")


if __name__ == "__main__":
    main()
