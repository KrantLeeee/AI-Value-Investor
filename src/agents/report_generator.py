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

# Agent name mapping for human-readable report language
# Maps internal agent names to professional investment terminology
AGENT_NAME_MAPPING = {
    "fundamentals": "基本面分析",
    "valuation": "估值模型",
    "warren_buffett": "价值投资框架",
    "ben_graham": "防御性投资准则",
    "sentiment": "市场情绪监测",
    "contrarian": "辩证分析",
}


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
    Uses actual computed values from metrics_snapshot.
    """
    lines = ["## 3. 财务质量评估", ""]

    if fundamentals_signal:
        m = fundamentals_signal.metrics
        total_score = m.get('total_score', 'N/A')
        lines.append(f"**基本面评分**: {total_score}/100  |  信号: {_signal_emoji(fundamentals_signal.signal)} {fundamentals_signal.signal.upper()}")
        lines.append("")

        # ── 核心财务指标表（直接显示计算值，不再显示 N/A 分段）
        lines.append("### 核心财务指标")
        lines.append("")
        lines.append("| 指标 | 数值 | 状态 |")
        lines.append("|:-----|:-----|:-----|")

        def _fmt(val, fmt=".1f", suffix=""):
            return f"{val:{fmt}}{suffix}" if val is not None else "N/A"

        roe    = m.get('roe')
        nm     = m.get('net_margin_pct')
        rev_yoy = m.get('revenue_yoy_pct')
        ni_yoy  = m.get('net_income_yoy_pct')
        de     = m.get('debt_to_equity')
        cr     = m.get('current_ratio')
        fcf_ni = m.get('fcf_to_net_income')

        def _status(val, thresholds, icons=("✓", "△", "✗")):
            """Map value to status icon based on thresholds (high→low)."""
            if val is None:
                return "❓ 数据缺失"
            if val >= thresholds[0]: return f"{icons[0]} 优秀"
            if val >= thresholds[1]: return f"{icons[1]} 良好" if len(thresholds) > 1 else f"{icons[1]} 合格"
            return f"{icons[2]} 偏弱"

        lines.append(f"| ROE（净资产收益率）| {_fmt(roe, '.1f', '%')} | {_status(roe, (20, 10))} |")
        lines.append(f"| 净利率 | {_fmt(nm, '.1f', '%')} | {_status(nm, (15, 8))} |")
        lines.append(f"| 营收YoY（同比增长）| {_fmt(rev_yoy, '+.1f', '%')} | {_status(rev_yoy, (10, 0))} |")
        lines.append(f"| 净利YoY（同比增长）| {_fmt(ni_yoy, '+.1f', '%')} | {_status(ni_yoy, (10, 0))} |")
        lines.append(f"| 负债/权益（D/E）| {_fmt(de, '.2f', 'x')} | {'✓ 低负债' if de and de <= 0.5 else ('△ 中等' if de and de <= 1.0 else '✗ 高负债') if de else '❓ 数据缺失'} |")
        lines.append(f"| 流动比率 | {_fmt(cr, '.2f', 'x')} | {'✓ 充裕' if cr and cr >= 2.0 else ('△ 合格' if cr and cr >= 1.0 else '⚠ 偏低') if cr else '❓ 数据缺失'} |")
        lines.append(f"| FCF/净利覆盖率 | {_fmt(fcf_ni, '.2f', 'x')} | {_status(fcf_ni, (0.8, 0.5))} |")
        lines.append("")

        # ── 评分明细（原始文字）
        lines.append("### 评分明细")
        lines.append("")
        lines.append(f"> {fundamentals_signal.reasoning}")
        lines.append("")
    else:
        lines.append("基本面分析未运行，数据不可用。")
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
        for flag in quality_report.flags[:5]:
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
    Shows all 4 methods: DCF, Graham, EV/EBITDA, P/B.
    """
    lines = ["## 4. 估值分析与敏感性测试", ""]

    if not valuation_signal:
        lines.append("估值模型未运行，数据不可用。")
        return "\n".join(lines)

    metrics = valuation_signal.metrics
    dcf     = metrics.get("dcf_per_share")
    graham  = metrics.get("graham_number")
    ev_ebps = metrics.get("ev_ebitda_per_share")
    pb_tgt  = metrics.get("pb_target")
    current = metrics.get("current_price")
    mos     = metrics.get("margin_of_safety")
    bvps    = metrics.get("bvps")
    wacc    = metrics.get("wacc")
    tg      = metrics.get("terminal_growth")

    def _mos(intrinsic, price):
        if intrinsic and price:
            return f"{(intrinsic - price) / intrinsic * 100:+.1f}%"
        return "N/A"

    # Valuation summary table - use actual validated weights from valuation agent
    validation = metrics.get("validation", {})
    validated_methods = validation.get("validated_methods", [])
    valid_methods = validation.get("valid_methods", [])
    excluded_methods = validation.get("excluded_methods", [])

    # Build method lookup dict
    method_lookup = {m["method"]: m for m in validated_methods}

    # Define display order and original weights
    method_display = [
        ("DCF", 0.40, dcf, "DCF折现现金流"),
        ("Graham", 0.25, graham, "Graham Number下限"),
        ("EV/EBITDA", 0.20, ev_ebps, "EV/EBITDA（6x行业倍数）"),
        ("P/B", 0.15, pb_tgt, f"P/B（1.8x BVPS={bvps:.2f}）" if bvps else "P/B"),
    ]

    # Calculate normalized weights for valid methods
    valid_orig_weights = {m: w for m, w, _, _ in method_display if m in valid_methods}
    total_weight = sum(valid_orig_weights.values())
    normalized_weights = {m: (w / total_weight if total_weight > 0 else 0)
                         for m, w in valid_orig_weights.items()}

    lines.append("### 多方法估值汇总")
    lines.append("")
    if excluded_methods:
        lines.append(f"⚠ **注**: {', '.join(excluded_methods)} 已因异常检测被排除，以下为调整后权重")
        lines.append("")
    lines.append("| 估值方法 | 原始权重 | 调整后权重 | 每股隐含价值 | 当前价格 | 安全边际 |")
    lines.append("|:---------|:--------|:---------|:------------|:---------|:---------|")

    for method_name, orig_weight, price_value, display_name in method_display:
        orig_w_str = f"{orig_weight*100:.0f}%"

        if method_name in excluded_methods:
            # Excluded method
            norm_w_str = "⚠ 已排除"
            price_str = f"¥{price_value:.2f}" if price_value else "N/A"
        elif method_name in valid_methods:
            # Valid method with normalized weight
            norm_weight = normalized_weights.get(method_name, 0)
            norm_w_str = f"**{norm_weight*100:.1f}%**"
            price_str = f"¥{price_value:.2f}" if price_value else "N/A"
        else:
            # Method not in validation results (shouldn't happen)
            norm_w_str = "—"
            price_str = "N/A"

        current_str = f"¥{current:.2f}" if current else "—"
        mos_str = _mos(price_value, current)

        lines.append(
            f"| {display_name} | {orig_w_str} | {norm_w_str} | {price_str} | {current_str} | {mos_str} |"
        )

    lines.append("")

    # WACC assumptions box
    if wacc:
        lines.append(f"> **折现率假设**: WACC={wacc:.1f}% | 终值增长率={tg:.1f}% | 注：较乐观假设会高估DCF")
        lines.append("")

    # Upside/downside summary - use validated weighted target
    validation = metrics.get("validation", {})
    w_tgt = validation.get("weighted_target")

    if w_tgt and current:
        upside = (w_tgt - current) / current * 100
        excluded_methods = validation.get("excluded_methods", [])
        exclusion_note = f"（已排除: {', '.join(excluded_methods)}）" if excluded_methods else ""

        if upside < -15:
            lines.append(f"**综合结论**: 加权目标价约 ¥{w_tgt:.2f}{exclusion_note}，较当前价 ¥{current:.2f} **下行{abs(upside):.0f}%**，估值偏贵。")
        elif upside > 15:
            lines.append(f"**综合结论**: 加权目标价约 ¥{w_tgt:.2f}{exclusion_note}，较当前价 ¥{current:.2f} **上行{upside:.0f}%**，有低估空间。")
        else:
            lines.append(f"**综合结论**: 加权目标价约 ¥{w_tgt:.2f}{exclusion_note}，与当前价 ¥{current:.2f} 相近，估值合理。")
        lines.append("")

    # Sensitivity scenarios - only if DCF is valid
    excluded_methods = validation.get("excluded_methods", [])
    dcf_excluded = "DCF" in excluded_methods

    lines.append("### 敏感性分析")
    lines.append("")

    if dcf_excluded:
        lines.append("> ⚠ DCF模型因异常检测被排除，敏感性分析不适用。最终目标价基于有效方法的加权计算。")
        lines.append("")
    elif dcf and w_tgt:
        lines.append("| 情景 | 假设 | DCF估值 | 加权目标价区间 |")
        lines.append("|:-----|:-----|:--------|:-------------|")
        # Use validated weighted target as base, apply ±10% for scenarios
        wacc_str = f"WACC={wacc:.1f}%" if wacc else "当前假设"
        lines.append(f"| 乐观情景 | 油价>$80/桶，Capex扩张10% | ¥{dcf*1.2:.2f} | ¥{w_tgt*0.90:.2f}-¥{w_tgt*1.10:.2f} |")
        lines.append(f"| 基准情景 | {wacc_str} | ¥{dcf:.2f} | ¥{w_tgt:.2f} |")
        lines.append(f"| 悲观情景 | 油价<$60/桶，南海风险触发 | ¥{dcf*0.8:.2f} | ¥{w_tgt*0.80:.2f}-¥{w_tgt:.2f} |")
        lines.append("")
        lines.append("> 注：敏感性分析仅供参考。加权目标价基于有效估值方法的归一化权重。")
        lines.append("")
    else:
        lines.append("| 情景 | 假设 | DCF估值 | 加权目标价 |")
        lines.append("|:-----|:-----|:--------|:----------|")
        lines.append("| - | 数据不足 | - | - |")
        lines.append("")
    lines.append(f"**估值模型完整评估**: {valuation_signal.reasoning}")
    lines.append("")

    return "\n".join(lines)



def _render_contrarian_chapter(contrarian_signal: AgentSignal | None) -> str:
    """Build Chapter 5: Risk Factors & Dialectical Analysis."""
    lines = ["## 5. 风险因素与辩证分析", ""]

    if not contrarian_signal:
        lines.append("辩证分析未运行。")
        return "\n".join(lines)

    mode = contrarian_signal.metrics.get("mode", "unknown") if contrarian_signal.metrics else "unknown"
    consensus = contrarian_signal.metrics.get("consensus", {}) if contrarian_signal.metrics else {}

    # Format mode label
    mode_label = {
        "bear_case": "Bear Case (挑战多头)",
        "bull_case": "Bull Case (挑战空头)",
        "critical_questions": "Critical Questions (核心矛盾)"
    }.get(mode, mode)

    lines.append(f"**分析模式**: {mode_label} | **共识方向**: {consensus.get('direction', 'N/A')} ({consensus.get('strength', 0):.0%})")
    lines.append("")

    if mode == "bear_case":
        lines.append("### 看多论点的挑战（Devil's Advocate看空视角）")
        lines.append("")
        challenges = contrarian_signal.metrics.get("assumption_challenges", []) if contrarian_signal.metrics else []
        for i, c in enumerate(challenges[:3], 1):
            sev = c.get("severity", "medium").upper()
            sev_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(sev, "❓")
            lines.append(f"{i}. {sev_icon} **[{sev}] {c.get('original_claim', '')}**")
            lines.append(f"   - 依赖假设: {c.get('assumption', '')}")
            lines.append(f"   - 质疑理由: {c.get('challenge', '')}")
            lines.append(f"   - 若假设错误: {c.get('impact_if_wrong', '')}")
            lines.append("")

        lines.append("### 关键风险情景")
        lines.append("")
        lines.append("| # | 风险场景 | 触发概率 | 利润影响 | 历史先例 |")
        lines.append("|:--|:---------|:---------|:---------|:---------|")
        risks = contrarian_signal.metrics.get("risk_scenarios", []) if contrarian_signal.metrics else []
        for i, r in enumerate(risks[:4], 1):
            prob = r.get('probability', 'N/A')
            # Normalize probability labels
            prob_norm = prob.upper().replace('中', 'MED').replace('高', 'HIGH').replace('低', 'LOW')
            lines.append(f"| {i} | {r.get('scenario', '')} | {prob_norm} | {r.get('impact', '')} | {r.get('precedent', '-')} |")
        lines.append("")

        bear_price = contrarian_signal.metrics.get("bear_case_target_price")
        if bear_price:
            lines.append(f"**辩证分析悲观目标价**: ¥{bear_price:.2f}/股")
            lines.append("")

    elif mode == "bull_case":
        lines.append("### 被忽视的上行因素（Devil's Advocate看多视角）")
        lines.append("")
        positives = contrarian_signal.metrics.get("overlooked_positives", []) if contrarian_signal.metrics else []
        for i, p in enumerate(positives[:3], 1):
            lines.append(f"{i}. **{p.get('factor', '')}**")
            lines.append(f"   {p.get('description', '')}")
            lines.append(f"   *潜在影响: {p.get('potential_impact', '')}*")
            lines.append("")

        survival = contrarian_signal.metrics.get("survival_advantage", "")
        if survival:
            lines.append(f"**周期生存优势**: {survival}")
            lines.append("")

        bull_price = contrarian_signal.metrics.get("bull_case_target_price")
        if bull_price:
            lines.append(f"**辩证分析乐观目标价**: ¥{bull_price:.2f}/股")
            lines.append("")

    else:  # critical_questions
        lines.append("### 核心矛盾与关键问题")
        lines.append("")
        contradiction = contrarian_signal.metrics.get("core_contradiction", "") if contrarian_signal.metrics else ""
        if contradiction:
            lines.append(f"> ⚠️ **核心矛盾**: {contradiction}")
            lines.append("")

        questions = contrarian_signal.metrics.get("questions", []) if contrarian_signal.metrics else []
        if questions:
            lines.append("**在形成最终结论前，必须回答以下关键问题：**")
            lines.append("")
            lines.append("| # | 关键问题 | 初步判断 | 需要查证 |")
            lines.append("|:--|:---------|:---------|:---------|")
            for i, q in enumerate(questions[:4], 1):
                lines.append(f"| {i} | {q.get('question', '')} | {q.get('preliminary_judgment', '-')} | {q.get('evidence_needed', '-')} |")
            lines.append("")

    lines.append("### 综合辩证论述")
    lines.append("")
    lines.append(f"> {contrarian_signal.reasoning}")
    lines.append("")

    return "\n".join(lines)



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

    # Analysis dimensions summary table (use human-readable names)
    lines.append("### 分析维度信号汇总")
    lines.append("")
    lines.append("| 分析维度 | 信号 | 置信度 | 关键指标 |")
    lines.append("|:---------|:-----|:-------|:---------|")

    for agent_name, signal in signals.items():
        if signal:
            emoji = _signal_emoji(signal.signal)
            # Use human-readable name from mapping
            display_name = AGENT_NAME_MAPPING.get(agent_name, agent_name)

            # Extract key metric from each dimension
            key_metric = ""
            if agent_name == "fundamentals":
                key_metric = f"得分: {signal.metrics.get('total_score', 'N/A')}/100"
            elif agent_name == "valuation":
                mos = signal.metrics.get('margin_of_safety')
                key_metric = f"安全边际: {mos*100:+.1f}%" if mos else "N/A"
            elif agent_name == "warren_buffett":
                key_metric = f"护城河: {signal.metrics.get('moat_type', 'N/A')}"
            elif agent_name == "ben_graham":
                passed = signal.metrics.get('criteria_passed', 0)
                total = signal.metrics.get('criteria_total', 6)
                criteria_details = signal.metrics.get('criteria_details', [])
                missing_count = sum(1 for c in criteria_details if "数据缺失" in c)
                if missing_count > 0:
                    key_metric = f"通过: {passed}/{total}标准 ({missing_count}条数据缺失)"
                else:
                    key_metric = f"通过: {passed}/{total}标准"
            elif agent_name == "sentiment":
                score = signal.metrics.get('sentiment_score')
                key_metric = f"情绪: {score:.2f}" if score else "N/A"
            elif agent_name == "contrarian":
                mode = signal.metrics.get('mode', 'N/A')
                key_metric = f"模式: {mode}"

            lines.append(f"| {display_name} | {emoji} {signal.signal} | {signal.confidence:.0%} | {key_metric} |")

    lines.append("")

    # Graham criteria details (if Graham agent ran)
    graham_signal = signals.get("ben_graham")
    if graham_signal and graham_signal.metrics.get("criteria_details"):
        lines.append("### 防御性投资准则详情")
        lines.append("")
        lines.append("格雷厄姆标准检验结果（6条标准）：")
        lines.append("")
        for criterion in graham_signal.metrics["criteria_details"]:
            lines.append(f"- {criterion}")
        lines.append("")

    # Data quality details
    lines.append("### 数据质量详情")
    lines.append("")
    lines.append(f"- **整体质量评分**: {quality_report.overall_quality_score:.2f}/1.0")
    lines.append(f"- **数据完整度**: {quality_report.data_completeness:.0%}")
    lines.append(f"- **过期字段数**: {len(quality_report.stale_fields)}")
    lines.append("")

    # QVeris status (premium data source)
    from src.data.qveris_source import _CREDITS_EXHAUSTED
    if _CREDITS_EXHAUSTED:
        lines.append("> ⚠ **付费数据源状态**: QVeris iFinD 额度耗尽，部分深度财务数据（如流动比率、资产负债表细项）可能缺失。建议充值或接受数据完整度下降。")
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
    company_context: dict | None = None,  # NEW
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
        chapter_key, user_template, ticker, market, signals, quality_report, industry_context,
        company_context=company_context,
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
    company_context: dict | None = None,  # NEW
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
        from datetime import date as _date
        ctx = company_context or {}
        cc = ctx.get("registered_capital")
        reg_cap_yi = f"{cc/1e8:.1f}" if cc else "未知"
        # Extract financial metrics for Ch1
        revenue_str = _format_yuan(fund.metrics.get("revenue")) if fund else "N/A"
        growth_str = f"{fund.metrics.get('revenue_yoy_pct', 0):.1f}%" if fund else "N/A"
        roe_str = f"{fund.metrics.get('roe', 0):.1f}" if fund else "N/A"
        net_margin_str = f"{fund.metrics.get('net_margin_pct', 0):.1f}" if fund else "N/A"
        cr = fund.metrics.get('current_ratio') if fund else None
        cr_str = f"{cr:.2f}" if cr else "N/A"
        return user_template.format(
            ticker=ticker,
            analysis_date=str(_date.today()),
            company_name=ctx.get("company_name", ticker),
            main_business=ctx.get("main_business", "（未获取）"),
            main_products=ctx.get("main_products", "（未获取）"),
            established_date=ctx.get("established_date", "未知"),
            reg_capital_yi=reg_cap_yi,
            concepts=ctx.get("concepts", "未获取"),
            revenue=revenue_str,
            growth_rate=growth_str,
            roe=roe_str,
            net_margin=net_margin_str,
            current_ratio=cr_str,
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
        # ── Handle valuation agent failure ────────────────────────────────────
        if not val:
            # Valuation failed - cannot generate recommendation without target price
            return (
                "## 综合建议与投资决策\n\n"
                "⚠️ **估值模型未能完成分析**，无法计算目标价。可能原因：\n"
                "- 财务数据严重缺失（数据完整度 < 30%）\n"
                "- 价格数据不可用\n"
                "- 关键财务指标异常\n\n"
                "**建议**: 等待更完整的财务数据披露后再进行估值分析。\n\n"
                f"**基本面信号**: {fund.signal if fund else '未运行'} ({fund.confidence:.0%})\n"
                f"**价值投资框架**: {buff.signal if buff else '未运行'} ({buff.confidence:.0%})\n"
                f"**防御性投资准则**: {gram.signal if gram else '未运行'} ({gram.confidence:.0%})\n"
            )

        # ── Use weighted target from valuation agent ──────────────────────────
        # CRITICAL: Do NOT recalculate with different weights. Use the validated
        # weighted_target that already applied outlier detection and normalization.
        validation = val.metrics.get("validation", {})
        w_target = validation.get("weighted_target") or 0

        # Get individual method prices for reference
        dcf_base = val.metrics.get("dcf_per_share", 0)
        dcf_optimistic = dcf_base * 1.2 if dcf_base else 0
        dcf_pessimistic = dcf_base * 0.8 if dcf_base else 0
        current_price = val.metrics.get("current_price", 0)
        graham_number = val.metrics.get("graham_number") or 0
        ev_ebitda_target = val.metrics.get("ev_ebitda_per_share") or 0
        pb_target = val.metrics.get("pb_target") or 0

        # Target range: ±10% around weighted target
        w_target_low = round(w_target * 0.90, 2) if w_target else dcf_pessimistic
        w_target_high = round(w_target * 1.10, 2) if w_target else dcf_optimistic

        # Upside/downside vs current price
        upside_pct = 0
        if current_price and w_target:
            upside_pct = round((w_target - current_price) / current_price * 100, 1)

        # Data completeness → confidence cap
        completeness = quality_report.data_completeness * 100
        conf_cap = 0.50 if completeness < 50 else (0.60 if completeness < 70 else 0.75)

        # Extract contrarian risks summary
        contrarian_risks = "（辩证分析未运行）"
        if contr and contr.metrics:
            mode = contr.metrics.get("mode")
            if mode == "bear_case":
                risks = contr.metrics.get("risk_scenarios", [])
                contrarian_risks = "\n".join([f"- {r.get('scenario', '')}（触发概率: {r.get('probability', '?')}）" for r in risks[:3]])
            elif mode == "bull_case":
                contrarian_risks = "（当前共识看空，辩证分析聚焦上行机会）"
            else:
                contrarian_risks = contr.metrics.get("core_contradiction", "（信号分歧）")

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
            # Multi-method valuation fields
            ev_ebitda_target=f"{ev_ebitda_target:.2f}",
            pb_target=f"{pb_target:.2f}" if pb_target else "N/A",
            dcf_base=f"{dcf_base:.2f}",
            dcf_optimistic=f"{dcf_optimistic:.2f}",
            dcf_pessimistic=f"{dcf_pessimistic:.2f}",
            graham_number=f"{graham_number:.2f}" if graham_number else "N/A",
            weighted_target_low=f"{w_target_low:.2f}",
            weighted_target_high=f"{w_target_high:.2f}",
            current_price=f"{current_price:.2f}",
            upside_to_target=f"{upside_pct:+.1f}",
            data_completeness=f"{completeness:.0f}",
            confidence_cap=f"{conf_cap:.2f}",
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
    quality_report: QualityReport | None = None,
    analysis_date: str | None = None,
    use_llm: bool = True,
    company_context: dict | None = None,  # NEW: injected from registry Phase -1
) -> tuple[str, Path]:
    """
    Generate the final research report (restructured with chapters).

    Args:
        ticker: Stock ticker
        market: Market type
        signals: All agent signals (including contrarian from P0-②)
        quality_report: Data quality report from P0-①
        analysis_date: Report date (defaults to today)
        use_llm: Whether to use LLM (False = quick mode)

    Returns:
        (report_markdown_text, report_file_path)
    """
    from datetime import datetime

    if analysis_date is None:
        analysis_date = str(date.today())

    # Prepare output directory
    output_dir = get_project_root() / "output" / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_ticker = ticker.replace(".", "_")
    report_path = output_dir / f"{safe_ticker}_{analysis_date}.md"

    # Quick mode: use existing code-only report
    if not use_llm:
        report_text = _quick_report(ticker, market, signals, analysis_date)
        report_path.write_text(report_text, encoding="utf-8")
        logger.info("[Report] Quick report saved: %s", report_path)
        return report_text, report_path

    # ── New Chapter-by-Chapter Generation ─────────────────────────────────────

    logger.info("[Report] Generating structured report for %s", ticker)

    # Get industry context from watchlist (if available)
    # For MVP, we'll use empty string and let LLM infer
    industry_context = ""

    # Ensure quality_report exists for fallback
    if quality_report is None:
        from src.data.models import QualityReport
        quality_report = QualityReport(
            ticker=ticker,
            market=market,
            flags=[],
            overall_quality_score=0.0,
            data_completeness=0.0,
            stale_fields=[],
            records_checked={},
        )

    # Generate all 8 chapters
    chapters = {}

    try:
        # Ch1: Industry Background (LLM)
        logger.info("[Report] Generating Ch1: Industry Background")
        chapters["ch1_industry"] = _generate_llm_chapter(
            "ch1_industry", ticker, market, signals, quality_report, industry_context,
            company_context=company_context,
        )

        # Ch2: Competitive Analysis (LLM)
        logger.info("[Report] Generating Ch2: Competitive Analysis")
        chapters["ch2_competitive"] = _generate_llm_chapter(
            "ch2_competitive", ticker, market, signals, quality_report, industry_context,
            company_context=company_context,
        )

        # Ch3: Financial Quality (Code)
        logger.info("[Report] Generating Ch3: Financial Quality")
        chapters["ch3_financial"] = _build_financial_quality_table(
            ticker, signals.get("fundamentals"), quality_report
        )

        # Ch4: Valuation Analysis (Code)
        logger.info("[Report] Generating Ch4: Valuation Analysis")
        chapters["ch4_valuation"] = _build_valuation_analysis(signals.get("valuation"))

        # Ch5: Risk Factors (Contrarian Template)
        logger.info("[Report] Generating Ch5: Risk Factors (Contrarian)")
        chapters["ch5_risks"] = _render_contrarian_chapter(signals.get("contrarian"))

        # Ch6: Market Sentiment (LLM)
        logger.info("[Report] Generating Ch6: Market Sentiment")
        chapters["ch6_sentiment"] = _generate_llm_chapter(
            "ch6_sentiment", ticker, market, signals, quality_report, industry_context,
            company_context=company_context,
        )

        # Ch7: Investment Recommendation (LLM)
        logger.info("[Report] Generating Ch7: Investment Recommendation")
        chapters["ch7_recommendation"] = _generate_llm_chapter(
            "ch7_recommendation", ticker, market, signals, quality_report, industry_context,
            company_context=company_context,
        )

        # Ch8: Appendix (Code)
        logger.info("[Report] Generating Appendix")
        chapters["appendix"] = _build_appendix(signals, quality_report)

    except Exception as e:
        logger.error("[Report] Chapter generation failed: %s", e)
        # Fall back to quick report
        report_text = _quick_report(ticker, market, signals, analysis_date)
        report_text += f"\n\n---\n*报告生成失败: {e}。已输出快速报告。*"
        report_path.write_text(report_text, encoding="utf-8")
        return report_text, report_path

    # Render main template
    try:
        template_path = get_project_root() / "templates" / "report_template.md"
        with open(template_path, "r", encoding="utf-8") as f:
            template = Template(f.read())

        report_text = template.render(
            ticker=ticker,
            market=market,
            analysis_date=analysis_date,
            quality_score=quality_report.overall_quality_score,
            generation_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **chapters  # ch1_industry, ch2_competitive, etc.
        )

    except Exception as e:
        logger.critical("[Report] Template rendering failed: %s", e)
        # Emergency fallback
        report_text = _quick_report(ticker, market, signals, analysis_date)
        report_text += f"\n\n---\n*模板渲染失败: {e}。已输出快速报告。*"
        report_path.write_text(report_text, encoding="utf-8")
        return report_text, report_path

    # Save report
    report_path.write_text(report_text, encoding="utf-8")
    logger.info("[Report] Structured report saved: %s (%d chars)", report_path, len(report_text))

    return report_text, report_path
