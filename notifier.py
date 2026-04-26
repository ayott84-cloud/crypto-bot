"""Email notifier — sends trade alerts on open and close events.

Uses SMTP (Gmail App Password recommended) to email trade details.
All sends are fire-and-forget: failures are logged but never block trading.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from config import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS,
    NOTIFY_EMAIL, NOTIFY_ENABLED,
)

logger = logging.getLogger("crypto_bot.notifier")


# ─── Low-level sender ──────────────────────────────────────────────────────

def _resolve_ipv4(host: str) -> str:
    """Resolve a hostname to an IPv4 address explicitly.

    DigitalOcean droplets without IPv6 enabled still receive AAAA records
    from DNS for hosts like smtp.gmail.com, and Python's default smtplib
    will try them first and get ENETUNREACH ('Network is unreachable').
    Pre-resolving to IPv4 avoids the dead path entirely.
    """
    import socket
    return socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]


def _send_email(subject: str, html_body: str) -> bool:
    """Send an HTML email. Returns True on success."""
    if not NOTIFY_ENABLED:
        logger.debug("Notifications disabled, skipping email")
        return False

    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, NOTIFY_EMAIL]):
        logger.warning("SMTP not fully configured, skipping email")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = NOTIFY_EMAIL
        msg.attach(MIMEText(html_body, "html"))

        # Resolve to IPv4 to bypass broken IPv6 routing on cloud droplets,
        # but pass the original hostname for TLS SNI / cert validation.
        ipv4 = _resolve_ipv4(SMTP_HOST)
        context = ssl.create_default_context()

        if SMTP_PORT == 465:
            # SMTPS — TLS from connection start. Most cloud providers (incl.
            # DigitalOcean) leave 465 outbound open while blocking 587.
            with smtplib.SMTP_SSL(ipv4, SMTP_PORT, timeout=15,
                                   context=context, server_hostname=SMTP_HOST) as server:
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_USER, NOTIFY_EMAIL, msg.as_string())
        else:
            # Submission + STARTTLS (port 587 by convention).
            with smtplib.SMTP(ipv4, SMTP_PORT, timeout=15) as server:
                server.ehlo()
                server.starttls(context=context, server_hostname=SMTP_HOST)
                server.ehlo()
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_USER, NOTIFY_EMAIL, msg.as_string())

        logger.info("Email sent: %s", subject)
        return True

    except Exception as e:
        logger.error("Failed to send email (%s): %s", subject, e)
        return False


# ─── HTML template helpers ──────────────────────────────────────────────────

_STYLE = """
<style>
  body { font-family: 'Segoe UI', Arial, sans-serif; background: #0f0f23; color: #e0e0e0; margin: 0; padding: 20px; }
  .card { background: #1a1a3e; border-radius: 12px; padding: 24px; max-width: 520px; margin: 0 auto; border: 1px solid #2a2a5e; }
  h2 { margin: 0 0 16px 0; font-size: 1.3em; }
  .open-header { color: #00c853; }
  .close-header { color: #64b5f6; }
  table { width: 100%; border-collapse: collapse; margin: 12px 0; }
  td { padding: 8px 4px; border-bottom: 1px solid #2a2a5e; font-size: 0.95em; }
  td:first-child { color: #aaa; width: 45%; }
  td:last-child { font-weight: 600; text-align: right; }
  .green { color: #00c853; }
  .red { color: #ff1744; }
  .blue { color: #64b5f6; }
  .divider { border-top: 1px solid #2a2a5e; margin: 14px 0; }
  .footer { color: #666; font-size: 0.75em; text-align: center; margin-top: 16px; }
  .badge { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 0.8em; font-weight: 700; }
  .badge-green { background: #00c85322; color: #00c853; }
  .badge-red { background: #ff174422; color: #ff1744; }
  .badge-blue { background: #64b5f622; color: #64b5f6; }
  .badge-yellow { background: #ffc10722; color: #ffc107; }
</style>
"""


def _fmt(value: float, decimals: int = 2) -> str:
    """Format number with comma separators."""
    return f"{value:,.{decimals}f}"


def _pnl_color(value: float) -> str:
    return "green" if value >= 0 else "red"


def _pnl_sign(value: float, decimals: int = 2) -> str:
    return f"{value:+,.{decimals}f}"


# ─── Trade Open Notification ───────────────────────────────────────────────

def notify_trade_opened(
    symbol: str,
    entry_price: float,
    quantity: str,
    leverage: int,
    sl_price: float,
    tp1_price: float,
    tp2_price: float,
    atr_at_entry: float,
    strategy: str,
    entry_reason: str = "",
    direction: str = "LONG",
) -> bool:
    """Send email when a new trade is opened.

    direction: "LONG" or "SHORT" — affects subject, badge color, and the sign
    of the profit/loss math at the TP and SL targets.
    """
    qty = float(quantity)
    notional = entry_price * qty
    margin = notional / leverage

    # Direction-aware: LONG profits when price rises, SHORT profits when price falls.
    sign = 1 if direction == "LONG" else -1
    profit_tp1 = (tp1_price - entry_price) * qty * sign
    profit_tp2 = (tp2_price - entry_price) * qty * sign
    loss_sl = (entry_price - sl_price) * qty * sign  # always positive when SL is set correctly

    # Percentages relative to margin
    pct_tp1 = (profit_tp1 / margin * 100) if margin > 0 else 0
    pct_tp2 = (profit_tp2 / margin * 100) if margin > 0 else 0
    pct_sl = (loss_sl / margin * 100) if margin > 0 else 0

    subject = f"TRADE OPENED: {symbol} {direction} @ {_fmt(entry_price)}"

    # Determine price decimal places from entry price
    pdec = len(str(entry_price).split(".")[-1]) if "." in str(entry_price) else 2

    badge_class = "badge-green" if direction == "LONG" else "badge-red"
    html = f"""{_STYLE}
<div class="card">
  <h2 class="open-header">TRADE OPENED <span class="badge {badge_class}">{direction}</span></h2>
  <table>
    <tr><td>Asset</td><td>{symbol}</td></tr>
    <tr><td>Strategy</td><td class="blue">{strategy}</td></tr>
    <tr><td>Entry Price</td><td>${_fmt(entry_price, pdec)}</td></tr>
    <tr><td>Position Size</td><td>{quantity}</td></tr>
    <tr><td>Notional Value</td><td>${_fmt(notional)}</td></tr>
    <tr><td>Margin Used</td><td>${_fmt(margin)}</td></tr>
    <tr><td>Leverage</td><td>{leverage}x</td></tr>
  </table>

  <div class="divider"></div>
  <table>
    <tr><td>Stop Loss</td><td class="red">${_fmt(sl_price, pdec)}</td></tr>
    <tr><td>Take Profit 1 (50%)</td><td class="green">${_fmt(tp1_price, pdec)}</td></tr>
    <tr><td>Take Profit 2 (full)</td><td class="green">${_fmt(tp2_price, pdec)}</td></tr>
  </table>

  <div class="divider"></div>
  <table>
    <tr><td>Expected Profit (TP1)</td><td class="green">+${_fmt(profit_tp1)} ({pct_tp1:+.1f}% ROE)</td></tr>
    <tr><td>Expected Profit (TP2)</td><td class="green">+${_fmt(profit_tp2)} ({pct_tp2:+.1f}% ROE)</td></tr>
    <tr><td>Potential Loss (SL)</td><td class="red">-${_fmt(loss_sl)} ({pct_sl:-.1f}% ROE)</td></tr>
  </table>

  <div class="divider"></div>
  <table>
    <tr><td>Entry Reason</td><td style="font-size:0.85em;">{entry_reason}</td></tr>
    <tr><td>ATR at Entry</td><td>{_fmt(atr_at_entry, 4)}</td></tr>
  </table>

  <div class="footer">
    Crypto Trading Bot &bull; {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
  </div>
</div>"""

    return _send_email(subject, html)


# ─── Trade Close Notification ──────────────────────────────────────────────

def notify_trade_closed(
    symbol: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    quantity: float,
    leverage: int,
    sl_price: float,
    tp1_price: float,
    tp2_price: float,
    exit_reason: str,
    strategy: str,
    portfolio_value: float,
    is_partial: bool = False,
    notes: str = "",
) -> bool:
    """Send email when a trade is closed (partial or full)."""
    notional = entry_price * quantity
    margin = notional / leverage

    # Direction-aware PnL: LONG profits when exit > entry, SHORT profits when exit < entry.
    sign = 1 if direction == "LONG" else -1
    pnl = (exit_price - entry_price) * quantity * sign
    pnl_pct = (pnl / margin * 100) if margin > 0 else 0

    # Color-code the exit reason badge
    if "TP" in exit_reason:
        badge_class = "badge-green"
    elif "SL" in exit_reason:
        badge_class = "badge-red"
    elif "Stale" in exit_reason:
        badge_class = "badge-yellow"
    else:
        badge_class = "badge-blue"

    close_type = "PARTIAL CLOSE (TP1)" if is_partial else "TRADE CLOSED"
    pnl_word = "Profit" if pnl >= 0 else "Loss"
    pnl_emoji_label = "WIN" if pnl >= 0 else "LOSS"

    subject = f"{close_type}: {symbol} | {exit_reason} | ${_pnl_sign(pnl)} ({pnl_pct:+.1f}%)"

    pdec = len(str(entry_price).split(".")[-1]) if "." in str(entry_price) else 2

    html = f"""{_STYLE}
<div class="card">
  <h2 class="close-header">{close_type} <span class="badge {badge_class}">{exit_reason}</span></h2>
  <table>
    <tr><td>Asset</td><td>{symbol}</td></tr>
    <tr><td>Strategy</td><td class="blue">{strategy}</td></tr>
    <tr><td>Direction</td><td>{direction}</td></tr>
    <tr><td>Entry Price</td><td>${_fmt(entry_price, pdec)}</td></tr>
    <tr><td>Exit Price</td><td>${_fmt(exit_price, pdec)}</td></tr>
    <tr><td>Position Size</td><td>{_fmt(quantity, 6)}</td></tr>
    <tr><td>Leverage</td><td>{leverage}x</td></tr>
  </table>

  <div class="divider"></div>
  <table>
    <tr><td>Stop Loss</td><td class="red">${_fmt(sl_price, pdec)}</td></tr>
    <tr><td>Take Profit 1</td><td class="green">${_fmt(tp1_price, pdec)}</td></tr>
    <tr><td>Take Profit 2</td><td class="green">${_fmt(tp2_price, pdec)}</td></tr>
  </table>

  <div class="divider"></div>
  <table>
    <tr><td>Actual {pnl_word}</td><td class="{_pnl_color(pnl)}" style="font-size:1.2em;">${_pnl_sign(pnl)}</td></tr>
    <tr><td>ROE %</td><td class="{_pnl_color(pnl)}">{pnl_pct:+.1f}%</td></tr>
  </table>

  <div class="divider"></div>
  <table>
    <tr><td>Portfolio Value</td><td style="font-size:1.1em;">${_fmt(portfolio_value)}</td></tr>
  </table>

  {"<p style='color:#aaa;font-size:0.85em;margin-top:10px;'>" + notes + "</p>" if notes else ""}

  <div class="footer">
    Crypto Trading Bot &bull; {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
  </div>
</div>"""

    return _send_email(subject, html)
