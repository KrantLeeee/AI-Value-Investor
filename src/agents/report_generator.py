"""Report Generator Agent — LLM-powered Chinese research report writer.

Synthesises all agent signals into a structured 800-1200 word research report.
Saves to output/reports/{ticker}_{YYYY-MM-DD}.md

In --quick mode (no LLM), generates a data-only report from numerical results.
"""

import json
from datetime import date
from pathlib import Path

from jinja2 import Template

from src.data.database import (
    get_income_statements,
    get_balance_sheets,
    get_financial_metrics,
    insert_agent_signal,
)
from src.data.models import AgentSignal, QualityReport
from src.utils.config import get_project_root
from src.utils.logger import get_logger
from src.agents.report_config import CHAPTERS, validate_chapter

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


def _build_financial_quality_table(
    ticker: str,
    fundamentals_signal: AgentSignal | None,
    quality_report: QualityReport,
) -> str:
    """
    Build Chapter 3: Financial Quality Assessment (code-based).

    Args:
        ticker: Stock ticker
        fundamentals_signal: Fundamentals agent result
        quality_report: Data quality report from P0-①

    Returns:
        Chapter 3 markdown text
    """
    lines = ["## 3. 财务质量评估", ""]

    # Fundamentals scoring breakdown
    if fundamentals_signal:
        lines.append(f"**基本面评分**: {fundamentals_signal.metrics.get('total_score', 'N/A')}/100")
        lines.append("")
        lines.append("| 维度 | 得分 | 说明 |")
        lines.append("|:-----|:-----|:-----|")
        lines.append(f"| 营收质量 | {fundamentals_signal.metrics.get('revenue_score', 'N/A')}/25 | 增长稳定性与规模 |")
        lines.append(f"| 盈利能力 | {fundamentals_signal.metrics.get('profitability_score', 'N/A')}/25 | ROE与净利率 |")
        lines.append(f"| 杠杆健康 | {fundamentals_signal.metrics.get('leverage_score', 'N/A')}/25 | 负债水平 |")
        lines.append(f"| 现金流质量 | {fundamentals_signal.metrics.get('cash_flow_score', 'N/A')}/25 | FCF与OCF |")
        lines.append("")
        lines.append(f"**评估**: {fundamentals_signal.reasoning}")
        lines.append("")
    else:
        lines.append("基本面Agent未运行，数据不可用。")
        lines.append("")

    # Data quality section
    lines.append("### 数据质量评估")
    lines.append("")
    lines.append(f"- **整体质量评分**: {quality_report.overall_quality_score:.2f}/1.0")
    lines.append(f"- **数据完整度**: {quality_report.data_completeness:.0%}")
    lines.append("")

    if quality_report.flags:
        lines.append(f"**发现 {len(quality_report.flags)} 个数据质量问题：**")
        lines.append("")
        for flag in quality_report.flags[:5]:  # Top 5 flags
            lines.append(f"- [{flag.severity.upper()}] {flag.detail}")
        if len(quality_report.flags) > 5:
            lines.append(f"- ... 及其他 {len(quality_report.flags) - 5} 个问题")
        lines.append("")
    else:
        lines.append("✅ 数据质量良好，未发现重大问题。")
        lines.append("")

    return "\n".join(lines)


def _build_valuation_analysis(valuation_signal: AgentSignal | None) -> str:
    """
    Build Chapter 4: Valuation Analysis (code-based).

    Args:
        valuation_signal: Valuation agent result

    Returns:
        Chapter 4 markdown text
    """
    lines = ["## 4. 估值分析与敏感性测试", ""]

    if not valuation_signal:
        lines.append("估值Agent未运行，数据不可用。")
        return "\n".join(lines)

    metrics = valuation_signal.metrics
    dcf = metrics.get("dcf_per_share")
    graham = metrics.get("graham_number")
    current = metrics.get("current_price")
    mos = metrics.get("margin_of_safety")

    # Valuation summary table
    lines.append("### 估值指标")
    lines.append("")
    lines.append("| 估值方法 | 内在价值 | 当前价格 | 安全边际 |")
    lines.append("|:---------|:---------|:---------|:---------|")
    lines.append(f"| DCF现金流折现 | ¥{dcf:.2f}/股 | ¥{current:.2f}/股 | {mos*100:+.1f}% |" if dcf else "| DCF现金流折现 | 数据不足 | - | - |")
    lines.append(f"| Graham Number | ¥{graham:.2f}/股 | ¥{current:.2f}/股 | {((current-graham)/graham)*100:+.1f}% |" if graham else "| Graham Number | 数据不足 | - | - |")
    lines.append("")

    # Valuation interpretation
    if dcf and mos:
        if mos > 0.20:
            lines.append(f"**解读**: DCF显示 {mos*100:.0f}% 安全边际，当前价格低估。")
        elif mos < -0.20:
            lines.append(f"**解读**: DCF显示 {abs(mos)*100:.0f}% 溢价，当前价格高估。")
        else:
            lines.append(f"**解读**: DCF显示 {abs(mos)*100:.0f}% {'安全边际' if mos > 0 else '溢价'}，估值合理。")
        lines.append("")

    # Sensitivity scenarios (simple 3-scenario analysis)
    lines.append("### 敏感性分析")
    lines.append("")
    lines.append("不同假设下的估值区间：")
    lines.append("")
    lines.append("| 情景 | 假设 | 估值 |")
    lines.append("|:-----|:-----|:-----|")

    if dcf:
        # Simple sensitivity: ±20% on DCF
        lines.append(f"| 乐观情景 | 增长率+2%或WACC-1% | ¥{dcf*1.2:.2f}/股 |")
        lines.append(f"| 基准情景 | 当前假设 | ¥{dcf:.2f}/股 |")
        lines.append(f"| 悲观情景 | 增长率-2%或WACC+1% | ¥{dcf*0.8:.2f}/股 |")
    else:
        lines.append("| - | 数据不足 | - |")

    lines.append("")

    # Add reasoning from valuation agent
    lines.append(f"**Agent评估**: {valuation_signal.reasoning}")
    lines.append("")

    return "\n".join(lines)


def _render_contrarian_chapter(contrarian_signal: AgentSignal | None) -> str:
    """
    Build Chapter 5: Risk Factors (Contrarian template).

    Args:
        contrarian_signal: Contrarian agent result

    Returns:
        Chapter 5 markdown text
    """
    if not contrarian_signal or not contrarian_signal.metrics:
        return """## 5. 风险因素与辩证分析

辩证分析暂不可用。请结合其他章节自行评估风险。
"""

    mode = contrarian_signal.metrics.get("mode")
    if not mode:
        return """## 5. 风险因素与辩证分析

辩证分析数据格式错误。
"""

    # Load mode-specific template
    template_path = Path(__file__).parent.parent.parent / "templates" / "contrarian_templates" / f"{mode}.md"

    if not template_path.exists():
        logger.warning(f"[Report] Contrarian template not found: {template_path}")
        return f"""## 5. 风险因素与辩证分析

模板文件缺失 ({mode}.md)。

**辩证分析结果**: {contrarian_signal.reasoning}
"""

    # Load and render template
    with open(template_path, "r", encoding="utf-8") as f:
        template = Template(f.read())

    # Prepare template context - add reasoning if not in metrics
    context = dict(contrarian_signal.metrics)
    if "reasoning" not in context:
        context["reasoning"] = contrarian_signal.reasoning

    return template.render(**context)


def _build_appendix(
    signals: dict[str, AgentSignal],
    quality_report: QualityReport,
) -> str:
    """
    Build Appendix: Technical Details (code-based).

    Args:
        signals: All agent signals
        quality_report: Data quality report

    Returns:
        Appendix markdown text
    """
    lines = ["## 附录：数据质量与技术说明", ""]

    # Agent signals summary table
    lines.append("### Agent信号汇总")
    lines.append("")
    lines.append("| Agent | 信号 | 置信度 | 关键指标 |")
    lines.append("|:------|:-----|:-------|:---------|")

    for agent_name, signal in signals.items():
        if signal:
            emoji = _signal_emoji(signal.signal)
            # Extract key metric from each agent
            key_metric = ""
            if agent_name == "fundamentals":
                key_metric = f"得分: {signal.metrics.get('total_score', 'N/A')}/100"
            elif agent_name == "valuation":
                mos = signal.metrics.get('margin_of_safety')
                key_metric = f"安全边际: {mos*100:+.1f}%" if mos else "N/A"
            elif agent_name == "warren_buffett":
                key_metric = f"护城河: {signal.metrics.get('moat_type', 'N/A')}"
            elif agent_name == "ben_graham":
                passed = signal.metrics.get('standards_passed', 0)
                key_metric = f"通过: {passed}/7标准"
            elif agent_name == "sentiment":
                score = signal.metrics.get('sentiment_score')
                key_metric = f"情绪: {score:.2f}" if score else "N/A"
            elif agent_name == "contrarian":
                mode = signal.metrics.get('mode', 'N/A')
                key_metric = f"模式: {mode}"

            lines.append(f"| {agent_name} | {emoji} {signal.signal} | {signal.confidence:.0%} | {key_metric} |")

    lines.append("")

    # Data quality details
    lines.append("### 数据质量详情")
    lines.append("")
    lines.append(f"- **整体质量评分**: {quality_report.overall_quality_score:.2f}/1.0")
    lines.append(f"- **数据完整度**: {quality_report.data_completeness:.0%}")
    lines.append(f"- **过期字段数**: {len(quality_report.stale_fields)}")
    lines.append("")

    if quality_report.flags:
        lines.append(f"**质量标记 ({len(quality_report.flags)} 个):**")
        lines.append("")
        for flag in quality_report.flags:
            lines.append(f"- [{flag.severity.upper()}] {flag.flag}: {flag.detail}")
        lines.append("")
    else:
        lines.append("✅ 所有质量检查通过。")
        lines.append("")

    # Technical notes
    lines.append("### 技术说明")
    lines.append("")
    lines.append("**估值假设:**")
    lines.append("- DCF折现率(WACC): 基于行业平均成本")
    lines.append("- 永续增长率: 3% (保守估计)")
    lines.append("- Graham Number: 基于EPS和每股净资产")
    lines.append("")
    lines.append("**数据来源:**")
    lines.append("- 财务数据: AKShare API")
    lines.append("- 市场数据: 实时行情接口")
    lines.append("- 新闻数据: 东方财富/新浪财经")
    lines.append("")

    return "\n".join(lines)


def _generate_llm_chapter(
    chapter_key: str,
    ticker: str,
    market: str,
    signals: dict[str, AgentSignal],
    quality_report: QualityReport,
    industry_context: str,
) -> str:
    """
    Generate a single LLM chapter with validation and retry.

    Args:
        chapter_key: Chapter identifier (ch1_industry, ch2_competitive, etc.)
        ticker: Stock ticker
        market: Market type
        signals: All agent signals
        quality_report: Data quality report
        industry_context: Industry background from watchlist

    Returns:
        Chapter markdown text (with warning marker if validation failed)
    """
    from src.llm.router import call_llm
    from src.llm.prompts import (
        REPORT_CH1_SYSTEM, REPORT_CH1_USER,
        REPORT_CH2_SYSTEM, REPORT_CH2_USER,
        REPORT_CH6_SYSTEM, REPORT_CH6_USER,
        REPORT_CH7_SYSTEM, REPORT_CH7_USER,
    )

    config = CHAPTERS[chapter_key]

    # Select prompts based on chapter
    prompt_map = {
        "ch1_industry": (REPORT_CH1_SYSTEM, REPORT_CH1_USER),
        "ch2_competitive": (REPORT_CH2_SYSTEM, REPORT_CH2_USER),
        "ch6_sentiment": (REPORT_CH6_SYSTEM, REPORT_CH6_USER),
        "ch7_recommendation": (REPORT_CH7_SYSTEM, REPORT_CH7_USER),
    }

    system_prompt, user_template = prompt_map[chapter_key]

    # Build user prompt (chapter-specific data injection)
    user_prompt = _build_chapter_user_prompt(
        chapter_key, user_template, ticker, market, signals, quality_report, industry_context
    )

    # Retry loop with validation
    for attempt in range(config["max_retries"] + 1):
        try:
            text = call_llm(config["task_name"], system_prompt, user_prompt)
        except Exception as e:
            logger.error(f"[Report] {chapter_key} LLM call failed: {e}")
            return f"## {config['title']}\n\n⚠️ LLM调用失败: {str(e)}"

        # Validate
        issues = validate_chapter(text, config)

        if not issues:
            logger.info(f"[Report] {chapter_key} passed validation (attempt {attempt+1})")
            return f"## {config['title']}\n\n{text}"

        # Log issues and retry
        if attempt < config["max_retries"]:
            logger.warning(f"[Report] {chapter_key} validation failed (attempt {attempt+1}): {issues}")
            user_prompt += f"\n\n[重试要求] 上次输出未通过验证: {', '.join(issues)}。请修正。"
        else:
            logger.error(f"[Report] {chapter_key} validation failed after {config['max_retries']+1} attempts")

    # Failed after all retries
    return f"## {config['title']}\n\n{text}\n\n> ⚠️ 质量验证未通过: {', '.join(issues)}"


def _build_chapter_user_prompt(
    chapter_key: str,
    user_template: str,
    ticker: str,
    market: str,
    signals: dict[str, AgentSignal],
    quality_report: QualityReport,
    industry_context: str,
) -> str:
    """Build user prompt for LLM chapter with data injection."""

    # Extract common data
    fund = signals.get("fundamentals")
    val = signals.get("valuation")
    buff = signals.get("warren_buffett")
    gram = signals.get("ben_graham")
    sent = signals.get("sentiment")
    contr = signals.get("contrarian")

    # Chapter-specific formatting
    if chapter_key == "ch1_industry":
        return user_template.format(
            ticker=ticker,
            sector=market,  # Simplified - would need actual sector from watchlist
            sub_industry="",
            industry_context=industry_context or "（用户未提供，请根据财务数据推测）",
            revenue=_format_yuan(fund.metrics.get("revenue")) if fund else "N/A",
            growth_rate=f"{fund.metrics.get('revenue_growth', 0)*100:.1f}%" if fund else "N/A",
            roe=f"{fund.metrics.get('roe', 0):.1f}" if fund else "N/A",
            debt_ratio=f"{fund.metrics.get('debt_ratio', 0):.1f}" if fund else "N/A",
        )

    elif chapter_key == "ch2_competitive":
        return user_template.format(
            buffett_signal=buff.signal if buff else "未运行",
            moat_type=buff.metrics.get("moat_type", "N/A") if buff else "N/A",
            management_quality=buff.metrics.get("management_quality", "N/A") if buff else "N/A",
            has_pricing_power=buff.metrics.get("has_pricing_power", False) if buff else False,
            buffett_reasoning=buff.reasoning if buff else "未分析",
            graham_signal=gram.signal if gram else "未运行",
            graham_standards_passed=gram.metrics.get("standards_passed", 0) if gram else 0,
            graham_reasoning=gram.reasoning if gram else "未分析",
        )

    elif chapter_key == "ch6_sentiment":
        return user_template.format(
            sentiment_signal=sent.signal if sent else "未运行",
            sentiment_score=f"{sent.metrics.get('sentiment_score', 0):.2f}" if sent else "N/A",
            sentiment_reasoning=sent.reasoning if sent else "暂无新闻数据",
            news_summary=sent.reasoning[:500] if sent else "（无）",
        )

    elif chapter_key == "ch7_recommendation":
        # Get DCF values
        dcf_base = val.metrics.get("dcf_per_share", 0) if val else 0
        dcf_optimistic = dcf_base * 1.2 if dcf_base else 0
        dcf_pessimistic = dcf_base * 0.8 if dcf_base else 0
        current_price = val.metrics.get("current_price", 0) if val else 0

        # Extract contrarian risks summary
        contrarian_risks = "（辩证分析未运行）"
        if contr and contr.metrics:
            mode = contr.metrics.get("mode")
            if mode == "bear_case":
                risks = contr.metrics.get("risk_scenarios", [])
                contrarian_risks = "\n".join([f"- {r.get('scenario', '')}" for r in risks[:3]])
            elif mode == "bull_case":
                contrarian_risks = "（当前共识看空，辩证分析聚焦上行机会）"
            else:
                contrarian_risks = contr.metrics.get("core_contradiction", "（信号分歧，关键不确定性待解决）")

        return user_template.format(
            fundamentals_signal=fund.signal if fund else "未运行",
            fundamentals_confidence=f"{fund.confidence:.0%}" if fund else "N/A",
            valuation_signal=val.signal if val else "未运行",
            valuation_confidence=f"{val.confidence:.0%}" if val else "N/A",
            buffett_signal=buff.signal if buff else "未运行",
            buffett_confidence=f"{buff.confidence:.0%}" if buff else "N/A",
            graham_signal=gram.signal if gram else "未运行",
            graham_confidence=f"{gram.confidence:.0%}" if gram else "N/A",
            sentiment_signal=sent.signal if sent else "未运行",
            sentiment_confidence=f"{sent.confidence:.0%}" if sent else "N/A",
            contrarian_signal=contr.signal if contr else "未运行",
            contrarian_confidence=f"{contr.confidence:.0%}" if contr else "N/A",
            dcf_base=f"{dcf_base:.2f}",
            dcf_optimistic=f"{dcf_optimistic:.2f}",
            dcf_pessimistic=f"{dcf_pessimistic:.2f}",
            current_price=f"{current_price:.2f}",
            contrarian_risks=contrarian_risks,
        )

    return "（章节配置错误）"


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
    quality_report: QualityReport | None = None,  # NEW: P0-① quality report (optional for backward compat)
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
