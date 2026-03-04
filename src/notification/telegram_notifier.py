"""Telegram Notifier — sends signal alerts and research reports via Telegram Bot API.

Setup:
  1. Message @BotFather → /newbot → get BOT_TOKEN
  2. Send any message to your bot
  3. Open https://api.telegram.org/bot{TOKEN}/getUpdates → get chat.id

Required .env keys:
  TELEGRAM_BOT_TOKEN  — from @BotFather
  TELEGRAM_CHAT_ID    — your personal chat ID (integer)

No third-party library needed — uses stdlib urllib only.
"""

import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import date
from pathlib import Path

from src.utils.config import get_settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_MSG_LEN  = 4096   # Telegram hard limit per message


def _get_credentials() -> tuple[str, str]:
    """Return (bot_token, chat_id) or raise ValueError."""
    settings = get_settings()
    token = getattr(settings, "telegram_bot_token", None) or ""
    chat_id = getattr(settings, "telegram_chat_id", None) or ""
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set in .env")
    if not chat_id:
        raise ValueError("TELEGRAM_CHAT_ID not set in .env")
    return token, str(chat_id)


def _send_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    Send a message via Telegram Bot API.
    Automatically splits messages > 4096 chars.
    Returns True on success.
    """
    try:
        token, chat_id = _get_credentials()
    except ValueError as e:
        logger.warning("[Telegram] %s", e)
        return False

    # Split long messages
    chunks = [text[i:i+_MAX_MSG_LEN] for i in range(0, len(text), _MAX_MSG_LEN)]
    success = True

    for chunk in chunks:
        payload = json.dumps({
            "chat_id":    chat_id,
            "text":       chunk,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }).encode("utf-8")

        url = _TELEGRAM_API.format(token=token, method="sendMessage")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
                if not result.get("ok"):
                    logger.error("[Telegram] API error: %s", result)
                    success = False
                else:
                    logger.info("[Telegram] Message sent (chat_id=%s, %d chars)", chat_id, len(chunk))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            logger.error("[Telegram] HTTP %d: %s", e.code, body)
            success = False
        except Exception as e:
            logger.error("[Telegram] Send failed: %s", e)
            success = False

    return success


# ── Signal alert ──────────────────────────────────────────────────────────────

def send_signal_alert(signals: list, scan_date: str | None = None) -> bool:
    """
    Send a formatted signal alert message to Telegram.

    Args:
        signals: list of ScreeningSignal objects.
        scan_date: date string (default: today).
    """
    if not signals:
        return False
    if scan_date is None:
        scan_date = str(date.today())

    opps   = [s for s in signals if s.signal == "opportunity"]
    alerts = [s for s in signals if s.signal == "alert"]

    lines = [
        f"🤖 <b>AI 投研 — 今日扫描结果</b>",
        f"📅 {scan_date}  |  共触发 <b>{len(signals)}</b> 个信号",
        "",
    ]

    if opps:
        lines.append("📈 <b>投资机会</b>")
        for s in opps:
            m = s.metrics or {}
            pe  = f"{m.get('pe_ratio'):.1f}" if m.get('pe_ratio') else "N/A"
            roe = f"{m.get('roe')}%" if m.get('roe') is not None else "N/A"
            mos = f"{m.get('margin_of_safety',0)*100:.0f}%" if m.get('margin_of_safety') is not None else "N/A"
            price = f"¥{m.get('current_price'):.2f}" if m.get('current_price') else "N/A"
            lines.append(
                f"  • <b>{s.ticker}</b> [{s.rule_name}]\n"
                f"    PE={pe}  ROE={roe}  安全边际={mos}  当前价={price}"
            )
        lines.append("")

    if alerts:
        lines.append("⚠️ <b>风险预警</b>")
        for s in alerts:
            lines.append(f"  • <b>{s.ticker}</b> [{s.rule_name}] — {s.description}")
        lines.append("")

    lines.append("<i>※ 数据由代码自动计算，仅供参考，不构成投资建议。</i>")

    return _send_message("\n".join(lines))


# ── Research report ───────────────────────────────────────────────────────────

def send_report_message(
    ticker: str,
    report_path: Path,
    signals_summary: dict | None = None,
) -> bool:
    """
    Send a research report as Telegram messages.
    Large reports are automatically split into multiple messages.

    Args:
        ticker: Ticker symbol.
        report_path: Path to the .md report file.
        signals_summary: Optional dict of AgentSignal objects.
    """
    if not report_path.exists():
        logger.warning("[Telegram] Report file not found: %s", report_path)
        return False

    report_md = report_path.read_text(encoding="utf-8")
    report_date = str(date.today())

    # Overall signal badge
    overall_emoji = "📊"
    if signals_summary:
        from collections import Counter
        counts = Counter(s.signal for s in signals_summary.values() if s)
        if counts:
            overall = counts.most_common(1)[0][0]
            overall_emoji = {"bullish": "📈", "neutral": "📊", "bearish": "📉"}.get(overall, "📊")

    # Convert markdown to Telegram HTML (just handle the most common patterns)
    import re
    html_lines = []
    for line in report_md.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            html_lines.append(f"<b>{'=' * 20}</b>")
            html_lines.append(f"<b>{stripped[2:]}</b>")
        elif stripped.startswith("## "):
            html_lines.append(f"\n<b>▌ {stripped[3:]}</b>")
        elif stripped.startswith("---"):
            continue
        elif stripped.startswith("| "):
            # table rows — keep as plain text
            html_lines.append(f"<code>{stripped}</code>")
        else:
            # inline bold **text** → <b>text</b>
            converted = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", stripped)
            html_lines.append(converted)

    header = (
        f"{overall_emoji} <b>AI 投研报告 — {ticker}</b>\n"
        f"📅 {report_date}\n"
        f"{'─' * 30}\n\n"
    )
    body = "\n".join(html_lines)
    full_text = header + body

    return _send_message(full_text)


# ── Test message ──────────────────────────────────────────────────────────────

def send_test_message() -> bool:
    """Send a test message to verify Telegram bot configuration."""
    text = (
        "✅ <b>AI Value Investor — Telegram 配置测试</b>\n\n"
        "如果你看到这条消息，说明 Telegram Bot 已正确配置！\n\n"
        "<i>BOT_TOKEN 和 CHAT_ID 均设置正确。</i>"
    )
    ok = _send_message(text)
    if ok:
        logger.info("[Telegram] Test message sent successfully")
    return ok
