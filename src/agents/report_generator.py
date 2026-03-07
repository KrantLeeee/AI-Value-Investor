"""Report Generator Agent — LLM-powered Chinese research report writer.

Synthesises all agent signals into a structured 800-1200 word research report.
Saves to output/reports/{ticker}_{YYYY-MM-DD}.md

In --quick mode (no LLM), generates a data-only report from numerical results.
"""

import json
from datetime import date
from pathlib import Path

from src.data.database import (
    get_income_statements,
    get_balance_sheets,
    get_financial_metrics,
    insert_agent_signal,
)
from src.data.models import AgentSignal
from src.utils.config import get_project_root
from src.utils.logger import get_logger

logger = get_logger(__name__)

AGENT_NAME = "report_generator"


def _safe(x) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _format_yuan(v: float | None, unit: str = "亿") -> str:
    if v is None:
        return "N/A"
    if unit == "亿":
        return f"¥{v/1e8:.2f}亿"
    return f"¥{v:.2f}"


def _build_financial_snapshot(ticker: str) -> str:
    """Build a compact markdown table of the latest annual financials."""
    income = get_income_statements(ticker, limit=1, period_type="annual")
    balance = get_balance_sheets(ticker, limit=1, period_type="annual")
    metrics = get_financial_metrics(ticker, limit=1)

    lines = []
    if income:
        r = income[0]
        lines.append(f"- 营业收入: {_format_yuan(_safe(r.get('revenue')))}")
        lines.append(f"- 净利润: {_format_yuan(_safe(r.get('net_income')))}")
        lines.append(f"- EPS: {_safe(r.get('eps'))}")
    if balance:
        r = balance[0]
        lines.append(f"- 总资产: {_format_yuan(_safe(r.get('total_assets')))}")
        lines.append(f"- 总负债: {_format_yuan(_safe(r.get('total_liabilities')))}")
        lines.append(f"- 股东权益: {_format_yuan(_safe(r.get('total_equity')))}")
    if metrics:
        r = metrics[0]
        lines.append(f"- ROE: {_safe(r.get('roe'))}%")
        lines.append(f"- P/E: {_safe(r.get('pe_ratio'))}")
        lines.append(f"- P/B: {_safe(r.get('pb_ratio'))}")
    return "\n".join(lines) if lines else "财务数据不足"


def _signal_emoji(s: str) -> str:
    return {"bullish": "🟢", "neutral": "🟡", "bearish": "🔴"}.get(s, "❓")


def _quick_report(
    ticker: str,
    market: str,
    signals: dict[str, AgentSignal],
    analysis_date: str,
) -> str:
    """Generate a data-only report (no LLM) from agent signals."""
    fund  = signals.get("fundamentals")
    val   = signals.get("valuation")
    buff  = signals.get("warren_buffett")
    gram  = signals.get("ben_graham")
    sent  = signals.get("sentiment")

    lines = [
        f"# {ticker} 投资研究快报（数据版）",
        f"**报告日期**: {analysis_date}  |  **市场**: {market}",
        "",
        "---",
        "## 1. Agent 信号汇总",
        "",
        "| Agent | 信号 | 置信度 |",
        "|:------|:-----|:-------|",
    ]

    for name, sig in signals.items():
        if sig:
            emoji = _signal_emoji(sig.signal)
            lines.append(f"| {name} | {emoji} {sig.signal} | {sig.confidence:.0%} |")

    lines += ["", "---", "## 2. 基本面评分"]
    if fund:
        score = fund.metrics.get("total_score", "N/A")
        lines.append(f"**总评分**: {score}/100（{fund.signal}）")
        lines.append("")
        lines.append(fund.reasoning)

    lines += ["", "---", "## 3. 估值数据"]
    if val:
        dcf = val.metrics.get("dcf_per_share")
        gn  = val.metrics.get("graham_number")
        mos = val.metrics.get("margin_of_safety")
        cp  = val.metrics.get("current_price")
        lines.append(f"- DCF 内在价值（基准）: ¥{dcf:.2f}" if dcf else "- DCF: 数据不足")
        lines.append(f"- Graham Number: ¥{gn:.2f}" if gn else "- Graham Number: 数据不足")
        lines.append(f"- 当前价格: ¥{cp:.2f}" if cp else "- 当前价格: 未知")
        lines.append(f"- 安全边际: {mos*100:.1f}%" if mos else "- 安全边际: 无法计算")

    lines += ["", "---", "## 4. 关键财务指标（最新年度）", ""]
    lines.append(_build_financial_snapshot(ticker))

    lines += ["", "---", "*本报告为数据版（--quick模式），未包含LLM定性分析。*"]

    # Overall signal (simple majority vote)
    sig_list = [s.signal for s in signals.values() if s]
    from collections import Counter
    counts = Counter(sig_list)
    overall = counts.most_common(1)[0][0] if counts else "neutral"
    avg_conf = sum(s.confidence for s in signals.values() if s) / max(1, len([s for s in signals.values() if s]))
    lines.append(f"\n**综合信号: {overall.upper()} | 置信度: {avg_conf:.2f}**")

    return "\n".join(lines)


def run(
    ticker: str,
    market: str,
    *,
    signals: dict[str, AgentSignal],
    quality_report=None,  # NEW: P0-① quality report (optional for backward compat)
    analysis_date: str | None = None,
    use_llm: bool = True,
) -> tuple[str, Path]:
    """
    Generate the final research report.

    Returns:
        (report_markdown_text, report_file_path)
    """
    # TODO(P0-③): Use quality_report in report appendix
    # For now, quality_report is passed but not yet used

    if analysis_date is None:
        analysis_date = str(date.today())

    fund  = signals.get("fundamentals")
    val   = signals.get("valuation")
    buff  = signals.get("warren_buffett")
    gram  = signals.get("ben_graham")
    sent  = signals.get("sentiment")

    # Prepare output directory
    output_dir = get_project_root() / "output" / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_ticker = ticker.replace(".", "_")
    report_path = output_dir / f"{safe_ticker}_{analysis_date}.md"

    if not use_llm:
        report_text = _quick_report(ticker, market, signals, analysis_date)
        report_path.write_text(report_text, encoding="utf-8")
        logger.info("[Report] Quick report saved: %s", report_path)
        return report_text, report_path

    # ── LLM report generation ─────────────────────────────────────────────────
    try:
        from src.llm.router import call_llm, LLMError
        from src.llm.prompts import REPORT_SYSTEM_PROMPT, REPORT_USER_TEMPLATE

        def _sig_text(sig: AgentSignal | None, default="未运行") -> tuple[str, str, float, str]:
            if sig is None:
                return default, default, 0.0, default
            return sig.signal, sig.signal, sig.confidence, sig.reasoning

        val_detail = ""
        if val:
            dcf = val.metrics.get("dcf_per_share")
            gn  = val.metrics.get("graham_number")
            mos = val.metrics.get("margin_of_safety")
            val_detail = (
                f"DCF基准: ¥{dcf:.2f}/股\n" if dcf else ""
                f"Graham Number: ¥{gn:.2f}/股\n" if gn else ""
                f"安全边际: {mos*100:.1f}%" if mos else "安全边际: 数据不足"
            )

        fund_detail = (fund.reasoning if fund else "未运行") or "N/A"

        user_msg = REPORT_USER_TEMPLATE.format(
            ticker=ticker,
            market=market,
            analysis_date=analysis_date,
            fundamentals_score=fund.metrics.get("total_score", "N/A") if fund else "N/A",
            fundamentals_signal=fund.signal if fund else "未运行",
            fundamentals_detail=fund_detail,
            valuation_signal=val.signal if val else "未运行",
            valuation_confidence=f"{val.confidence:.0%}" if val else "N/A",
            valuation_detail=val_detail or (val.reasoning if val else "未运行"),
            buffett_signal=buff.signal if buff else "未运行",
            buffett_confidence=f"{buff.confidence:.0%}" if buff else "N/A",
            buffett_reasoning=buff.reasoning if buff else "LLM 分析未运行",
            graham_signal=gram.signal if gram else "未运行",
            graham_confidence=f"{gram.confidence:.0%}" if gram else "N/A",
            graham_reasoning=gram.reasoning if gram else "LLM 分析未运行",
            sentiment_signal=sent.signal if sent else "未运行",
            sentiment_score=sent.metrics.get("sentiment_score", "N/A") if sent else "N/A",
            sentiment_reasoning=sent.reasoning if sent else "暂无新闻数据",
            financial_snapshot=_build_financial_snapshot(ticker),
        )

        report_text = call_llm("report_writing", REPORT_SYSTEM_PROMPT, user_msg)

    except Exception as e:
        logger.warning("[Report] LLM failed, falling back to quick report: %s", e)
        report_text = _quick_report(ticker, market, signals, analysis_date)
        report_text += f"\n\n---\n*LLM报告生成失败: {e}。已输出数据版报告。*"

    report_path.write_text(report_text, encoding="utf-8")
    logger.info("[Report] Report saved: %s", report_path)
    return report_text, report_path
