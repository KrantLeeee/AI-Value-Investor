"""Email Sender — sends signal alerts and weekly reports via Brevo (Sendinblue) API.

Requires:
  BREVO_API_KEY     — from Brevo dashboard -> API Keys
  EMAIL_RECIPIENT   — your personal email address
  EMAIL_SENDER_EMAIL — verified sender in Brevo
  EMAIL_SENDER_NAME  — display name (default: "AI Value Investor")

If BREVO_API_KEY is not set, functions log a warning and return False gracefully.
"""

import html
import json
from datetime import date
from pathlib import Path

from src.utils.config import get_settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _get_brevo_client():
    """Return configured Brevo API client, or raise if key is missing."""
    import sib_api_v3_sdk
    from sib_api_v3_sdk.rest import ApiException
    api_key = get_settings().brevo_api_key
    if not api_key:
        raise ValueError("BREVO_API_KEY is not set in .env")
    config = sib_api_v3_sdk.Configuration()
    config.api_key["api-key"] = api_key
    return sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(config))


def _get_sender_and_recipient() -> tuple[dict, str]:
    settings = get_settings()
    sender = {
        "name":  settings.email_sender_name or "AI Value Investor",
        "email": settings.email_sender_email or settings.email_recipient,
    }
    recipient = settings.email_recipient
    if not recipient:
        raise ValueError("EMAIL_RECIPIENT is not set in .env")
    return sender, recipient


# ── Signal alert email ────────────────────────────────────────────────────────

def send_signal_alert(signals: list, scan_date: str | None = None) -> bool:
    """
    Send an HTML email summarising triggered screening signals.

    Args:
        signals: list of ScreeningSignal objects.
        scan_date: date string (default: today).

    Returns:
        True if email sent successfully, False otherwise.
    """
    if not signals:
        logger.info("[Email] No signals to send.")
        return False

    if scan_date is None:
        scan_date = str(date.today())

    # Group by signal type
    opportunities = [s for s in signals if s.signal == "opportunity"]
    alerts        = [s for s in signals if s.signal == "alert"]

    subject = f"[AI投研] {scan_date} 机会信号 ({len(signals)}条)"

    # Build HTML table for opportunities
    def _build_table(signal_list: list, title: str, color: str) -> str:
        if not signal_list:
            return ""
        rows = ""
        for s in signal_list:
            m = s.metrics or {}
            pe  = f"{m.get('pe_ratio', 'N/A'):.1f}" if isinstance(m.get('pe_ratio'), float) else "N/A"
            roe = f"{m.get('roe', 'N/A')}%" if m.get('roe') is not None else "N/A"
            mos = f"{m.get('margin_of_safety', 0)*100:.0f}%" if m.get('margin_of_safety') is not None else "N/A"
            price = f"¥{m.get('current_price', 'N/A')}" if m.get('current_price') is not None else "N/A"
            rows += f"""
            <tr>
              <td style="padding:8px;border-bottom:1px solid #eee"><b>{html.escape(s.ticker)}</b></td>
              <td style="padding:8px;border-bottom:1px solid #eee">{html.escape(s.rule_name)}</td>
              <td style="padding:8px;border-bottom:1px solid #eee">{pe}</td>
              <td style="padding:8px;border-bottom:1px solid #eee">{roe}</td>
              <td style="padding:8px;border-bottom:1px solid #eee">{mos}</td>
              <td style="padding:8px;border-bottom:1px solid #eee">{price}</td>
            </tr>"""
        return f"""
        <h3 style="color:{color};margin-top:24px">{title}（{len(signal_list)}条）</h3>
        <table style="border-collapse:collapse;width:100%;font-family:sans-serif;font-size:14px">
          <thead>
            <tr style="background:{color};color:white">
              <th style="padding:10px;text-align:left">标的</th>
              <th style="padding:10px;text-align:left">触发规则</th>
              <th style="padding:10px;text-align:left">PE</th>
              <th style="padding:10px;text-align:left">ROE</th>
              <th style="padding:10px;text-align:left">安全边际</th>
              <th style="padding:10px;text-align:left">当前价</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>"""

    html_body = f"""
    <div style="font-family:sans-serif;max-width:700px;margin:0 auto">
      <h2 style="color:#1a1a2e">🤖 AI 投资研究助手 — 今日扫描结果</h2>
      <p style="color:#666">扫描日期：{scan_date} | 共触发 <b>{len(signals)}</b> 个信号</p>
      {_build_table(opportunities, "📈 投资机会", "#2e7d32")}
      {_build_table(alerts, "⚠️ 风险预警", "#c62828")}
      <hr style="margin-top:32px;border:none;border-top:1px solid #eee">
      <p style="color:#999;font-size:12px">
        ※ 以上数据由系统自动计算（纯代码），均基于已存储的历史财务数据，仅供参考，不构成投资建议。<br>
        由 AI Value Investor 自动发送
      </p>
    </div>"""

    return _send_email(subject, html_body)


# ── Report email ──────────────────────────────────────────────────────────────

def send_report_email(
    ticker: str,
    report_path: Path,
    signals_summary: dict | None = None,
) -> bool:
    """
    Send a research report as an email.

    Args:
        ticker: The ticker this report is for.
        report_path: Path to the .md report file.
        signals_summary: Optional dict with agent signal results.

    Returns:
        True if sent successfully, False otherwise.
    """
    if not report_path.exists():
        logger.warning("[Email] Report file not found: %s", report_path)
        return False

    report_md = report_path.read_text(encoding="utf-8")
    report_date = str(date.today())

    # Convert key markdown formatting to HTML (simple version)
    report_html = report_md
    for md, tag in [("**", "b"), ("##", "h3"), ("#", "h2"), ("---", "<hr>")]:
        pass  # simple line-by-line conversion below

    html_lines = []
    for line in report_md.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            html_lines.append(f"<h2>{html.escape(stripped[2:])}</h2>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h3 style='color:#2e7d32'>{html.escape(stripped[3:])}</h3>")
        elif stripped.startswith("---"):
            html_lines.append("<hr style='border:none;border-top:1px solid #eee'>")
        elif stripped.startswith("- "):
            html_lines.append(f"<li>{html.escape(stripped[2:])}</li>")
        elif stripped.startswith("**综合信号"):
            # Highlight the final signal line
            esc = html.escape(stripped)
            html_lines.append(f"<p style='font-size:16px;font-weight:bold;color:#1a1a2e;background:#f5f5f5;padding:12px;border-radius:6px'>{esc}</p>")
        elif stripped:
            # Replace inline bold **text**
            import re
            converted = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(stripped))
            html_lines.append(f"<p style='margin:4px 0'>{converted}</p>")

    report_html = "\n".join(html_lines)

    # Overall signal from summary
    overall_badge = ""
    if signals_summary:
        sig_counts = {}
        for s in signals_summary.values():
            if s:
                sig_counts[s.signal] = sig_counts.get(s.signal, 0) + 1
        overall = max(sig_counts, key=sig_counts.get) if sig_counts else "neutral"
        colors = {"bullish": "#2e7d32", "neutral": "#f57c00", "bearish": "#c62828"}
        emoji  = {"bullish": "📈", "neutral": "📊", "bearish": "📉"}
        color = colors.get(overall, "#666")
        overall_badge = f"<span style='background:{color};color:white;padding:4px 12px;border-radius:4px;font-size:14px'>{emoji.get(overall,'')} {overall.upper()}</span>"

    html_body = f"""
    <div style="font-family:sans-serif;max-width:700px;margin:0 auto">
      <h2 style="color:#1a1a2e">🤖 AI 投研报告 — {html.escape(ticker)}</h2>
      <p style="color:#666">报告日期：{report_date} &nbsp; 综合信号：{overall_badge}</p>
      <hr style="border:none;border-top:1px solid #eee">
      {report_html}
      <hr style="margin-top:32px;border:none;border-top:1px solid #eee">
      <p style="color:#999;font-size:12px">
        由 AI Value Investor 自动生成。仅供参考，不构成投资建议。
      </p>
    </div>"""

    subject = f"[AI投研] {ticker} 研究报告 — {report_date}"
    return _send_email(subject, html_body)


# ── Test email ────────────────────────────────────────────────────────────────

def send_test_email() -> bool:
    """Send a test email to verify Brevo connection."""
    html_body = """
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
      <h2>✅ AI Value Investor — 邮件配置测试</h2>
      <p>如果你收到这封邮件，说明 Brevo 邮件服务已正确配置！</p>
      <p style="color:#666">Brevo API Key、发件人、收件人均设置正确。</p>
    </div>"""
    return _send_email("[AI投研] 邮件配置测试", html_body)


# ── Core send function ────────────────────────────────────────────────────────

def _send_email(subject: str, html_content: str) -> bool:
    """Internal: send email via Brevo API."""
    try:
        import sib_api_v3_sdk
        api = _get_brevo_client()
        sender, recipient = _get_sender_and_recipient()

        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
            to=[{"email": recipient}],
            sender=sender,
            subject=subject,
            html_content=html_content,
        )
        response = api.send_transac_email(send_smtp_email)
        logger.info("[Email] Sent '%s' → %s (messageId: %s)",
                    subject, recipient, getattr(response, "message_id", "?"))
        return True

    except ImportError:
        logger.warning("[Email] sib-api-v3-sdk not installed. Run: pip install sib-api-v3-sdk")
        return False
    except Exception as e:
        logger.error("[Email] Failed to send '%s': %s", subject, e)
        return False
