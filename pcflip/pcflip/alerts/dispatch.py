"""Alert channels. Each `send(title, body, url, fields)` returns True on success. No channel raises."""
from __future__ import annotations

import json
import sys
from typing import Any, Callable

import httpx

from ..config import Settings
from ..listings.models import Listing
from ..valuation.value import Valuation, format_summary

VERDICT_COLORS = {"STRONG_BUY": 0x2ECC71, "BUY": 0x27AE60, "WATCH": 0xF1C40F, "UNPRICED": 0x3498DB, "PASS": 0x95A5A6}
VERDICT_EMOJI = {"STRONG_BUY": "🟢🟢", "BUY": "🟢", "WATCH": "🟡", "UNPRICED": "🔵", "PASS": "⚪", "UNKNOWN": "⚫"}


def _post(url: str, **kwargs: Any) -> bool:
    try:
        r = httpx.post(url, timeout=15.0, **kwargs)
        return 200 <= r.status_code < 300
    except httpx.HTTPError as e:  # network problems must never kill the watch loop
        print(f"[alerts] post failed: {e}", file=sys.stderr)
        return False


def build_message(listing: Listing, v: Valuation) -> dict[str, Any]:
    emoji = VERDICT_EMOJI.get(v.verdict, "")
    ask = f"${listing.price:,.0f}" if listing.price is not None else "no price"
    head = f"{emoji} {v.verdict}  {listing.title}".strip()
    lines = [f"Ask {ask}"]
    if v.best_exit is not None:
        lines.append(f"Exit ${v.best_exit:,.0f} via {v.exit_strategy} | max buy ${v.max_buy:,.0f}"
                     + (f" | spread ${v.spread:,.0f} ({v.spread_pct:+.0%})" if v.spread is not None and v.spread_pct is not None else ""))
    if v.offer is not None:
        lines.append(f"Open at ${v.offer:,.0f}")
    parts = [f"{it.label} ${it.value:,.0f}" for it in v.items if it.value is not None and it.key != "other-parts"]
    if parts:
        lines.append("Parts: " + "; ".join(parts))
    if v.flags.get("hard"):
        lines.append("RED FLAGS: " + ", ".join(v.flags["hard"]))
    if v.flags.get("soft"):
        lines.append("Notes: " + ", ".join(v.flags["soft"][:6]))
    if v.reasons:
        lines.append(v.reasons[0])
    lines.append(f"Confidence {v.confidence} | {listing.location or ''} | {listing.source}")
    return {"title": head, "body": "\n".join(lines), "url": listing.url, "color": VERDICT_COLORS.get(v.verdict, 0x95A5A6),
            "image": listing.image_url}


# ---------------------------------------------------------------------------- channels
def send_console(msg: dict[str, Any], listing: Listing, v: Valuation) -> bool:
    print("\n" + format_summary(v, listing.title))
    if listing.url:
        print(f"  {listing.url}")
    return True


def send_discord(msg: dict[str, Any], settings: Settings) -> bool:
    url = settings.env("DISCORD_WEBHOOK_URL")
    if not url:
        return False
    embed: dict[str, Any] = {"title": msg["title"][:256], "description": msg["body"][:4000], "color": msg["color"]}
    if msg.get("url"):
        embed["url"] = msg["url"]
    if msg.get("image"):
        embed["thumbnail"] = {"url": msg["image"]}
    return _post(url, json={"embeds": [embed]})


def send_telegram(msg: dict[str, Any], settings: Settings) -> bool:
    token, chat = settings.env("TELEGRAM_BOT_TOKEN"), settings.env("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    text = f"{msg['title']}\n{msg['body']}" + (f"\n{msg['url']}" if msg.get("url") else "")
    return _post(f"https://api.telegram.org/bot{token}/sendMessage",
                 json={"chat_id": chat, "text": text[:4000], "disable_web_page_preview": False})


def send_ntfy(msg: dict[str, Any], settings: Settings) -> bool:
    topic = settings.env("NTFY_TOPIC")
    if not topic:
        return False
    server = settings.alerts.get("ntfy_server", "https://ntfy.sh").rstrip("/")
    headers = {"Title": msg["title"].encode("ascii", "ignore").decode()[:200], "Priority": "high" if "STRONG" in msg["title"] else "default"}
    if msg.get("url"):
        headers["Click"] = msg["url"]
    return _post(f"{server}/{topic}", content=msg["body"].encode("utf-8"), headers=headers)


CHANNELS: dict[str, Callable[..., bool]] = {
    "discord": lambda msg, listing, v, settings: send_discord(msg, settings),
    "telegram": lambda msg, listing, v, settings: send_telegram(msg, settings),
    "ntfy": lambda msg, listing, v, settings: send_ntfy(msg, settings),
    "console": lambda msg, listing, v, settings: send_console(msg, listing, v),
}


def dispatch(listing: Listing, v: Valuation, settings: Settings, channels: list[str] | None = None) -> list[str]:
    """Send to every configured channel; returns the names that succeeded."""
    names = channels or list(settings.alerts.get("channels", ["console"]))
    msg = build_message(listing, v)
    ok: list[str] = []
    for name in names:
        fn = CHANNELS.get(name)
        if not fn:
            print(f"[alerts] unknown channel {name!r}", file=sys.stderr)
            continue
        if fn(msg, listing, v, settings):
            ok.append(name)
    return ok


def test_channels(settings: Settings) -> dict[str, bool]:
    """Fire a hello-world to every configured channel so the user can confirm wiring."""
    from ..valuation.value import Valuation
    listing = Listing("pcflip", "test", "pcflip test alert: wiring works", 123.0, url="https://example.com", location="test")
    v = Valuation(ask=123.0, kind="single", verdict="WATCH", reasons=["this is a test alert"], est_value=200.0,
                  best_exit=190.0, exit_strategy="local", max_buy=90.0, target_profit=100.0, spread=67.0, spread_pct=0.54,
                  offer=80.0, confidence="low")
    names = list(settings.alerts.get("channels", ["console"]))
    msg = build_message(listing, v)
    return {n: bool(CHANNELS[n](msg, listing, v, settings)) for n in names if n in CHANNELS}
