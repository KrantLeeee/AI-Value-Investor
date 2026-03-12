"""Contrarian Agent — dialectical analysis to challenge consensus.

The Contrarian Agent identifies when multiple investment agents agree
(bullish/bearish consensus) and generates counter-arguments to test
the robustness of the investment thesis.

Methodology:
  1. Determine consensus from other agents (≥60% agreement = consensus)
  2. Select analysis mode: Challenge consensus or explore mixed signals
  3. Generate contrarian arguments using LLM
  4. Return structured output with confidence scoring

Signal thresholds:
  Consensus ≥ 60%  → Challenge mode (contrarian analysis)
  Consensus < 60%  → Mixed mode (explore uncertainty)
"""

import json
from datetime import datetime
from typing import Any

from src.data.database import insert_agent_signal
from src.data.models import AgentSignal, SignalType, QualityReport, MarketType
from src.utils.logger import get_logger

logger = get_logger(__name__)

AGENT_NAME = "contrarian"


def safe_format(value, fmt="{}", default="N/A"):
    """
    Safe formatting that handles None values and format errors.

    Args:
        value: The value to format
        fmt: Format string (default: "{}")
        default: Value to return if formatting fails (default: "N/A")

    Returns:
        Formatted string or default value
    """
    if value is None:
        return default
    try:
        return fmt.format(value)
    except (ValueError, TypeError, KeyError, IndexError):
        return default


def _determine_consensus(signals: dict[str, AgentSignal | None]) -> tuple[str, float]:
    """
    Determine if there's a bullish/bearish consensus among agent signals.

    Args:
        signals: Dict mapping agent names to AgentSignal objects

    Returns:
        Tuple of (direction, strength):
        - direction: "bullish", "bearish", or "mixed"
        - strength: ratio of agents agreeing (0.0 to 1.0)

    Logic:
        - Count bullish/bearish signals (exclude None and neutral)
        - Calculate bull_ratio = bullish_count / total_count
        - Calculate bear_ratio = bearish_count / total_count
        - If bull_ratio >= 0.6 → ("bullish", bull_ratio)
        - If bear_ratio >= 0.6 → ("bearish", bear_ratio)
        - Otherwise → ("mixed", max(bull_ratio, bear_ratio))
        - Empty signals → ("mixed", 0.0)
    """
    # Filter out None values and extract AgentSignal objects
    valid_signals = [s for s in signals.values() if s is not None]

    if not valid_signals:
        return ("mixed", 0.0)

    total_count = len(valid_signals)
    bullish_count = sum(1 for s in valid_signals if s.signal == "bullish")
    bearish_count = sum(1 for s in valid_signals if s.signal == "bearish")

    bull_ratio = bullish_count / total_count
    bear_ratio = bearish_count / total_count

    # Consensus threshold: 60%
    if bull_ratio >= 0.6:
        return ("bullish", round(bull_ratio, 3))
    elif bear_ratio >= 0.6:
        return ("bearish", round(bear_ratio, 3))
    else:
        max_ratio = max(bull_ratio, bear_ratio)
        return ("mixed", round(max_ratio, 3))


def _select_mode(consensus_direction: str, consensus_strength: float) -> tuple[str, SignalType]:
    """
    Select analysis mode based on consensus direction.

    Args:
        consensus_direction: "bullish", "bearish", or "mixed"
        consensus_strength: Strength of consensus (0.0 to 1.0)

    Returns:
        Tuple of (mode, signal):
        - mode: "bear_case", "bull_case", or "critical_questions"
        - signal: "bearish", "bullish", or "neutral"

    Logic:
        - Bullish consensus → Challenge with bear_case mode (bearish signal)
        - Bearish consensus → Challenge with bull_case mode (bullish signal)
        - Mixed consensus → Explore with critical_questions mode (neutral signal)
    """
    if consensus_direction == "bullish":
        return ("bear_case", "bearish")
    elif consensus_direction == "bearish":
        return ("bull_case", "bullish")
    else:  # mixed
        return ("critical_questions", "neutral")


def _format_quality_context(quality_report: QualityReport) -> str:
    """Format quality report into human-readable context."""
    lines = []
    lines.append(f"质量分数: {quality_report.overall_quality_score:.2f}")
    lines.append(f"完整度: {quality_report.data_completeness:.2%}")

    if quality_report.flags:
        lines.append(f"\n发现 {len(quality_report.flags)} 个数据质量问题:")
        for flag in quality_report.flags[:3]:  # Limit to top 3
            lines.append(f"- [{flag.severity.upper()}] {flag.detail}")

    if not quality_report.flags:
        lines.append("数据质量良好，无重大问题。")

    return "\n".join(lines)


def _build_industry_context_block(company_context: dict) -> str:
    """
    Build an industry context block to prepend to the system prompt.
    This forces the LLM to use industry-specific knowledge.
    """
    if not company_context:
        return ""

    company_name = company_context.get('company_name', '')
    main_business = company_context.get('main_business', '')
    concepts = company_context.get('concepts', '')
    business_desc = (main_business + " " + concepts).strip()
    
    lines = [
        "\n\n--- 行业与业务上下文（必须紧密结合，否则分析无效） ---",
        f"公司名称: {company_name}",
        f"所属行业/板块: {company_context.get('sector', '未知')}",
        f"主营业务与概念: {business_desc}",
        "",
        "【行业特定风险推演指令】",
        "作为资深魔鬼代言人，你必须基于上述[所属行业]和[主营业务]，自行推演出由于该行业的特性所带来的根本性风险、周期性陷阱和估值陷阱。例如：",
        "- 如果是强周期行业（如能源/大宗），必须指出其资本开支周期、价格波动、地缘政治影响。且提示DCF模型可能因顶部利润被线性外推而极其不可靠。",
        "- 如果是高杠杆行业（如金融/地产），必须围绕信用周期、资产质量、利差压缩和政策监管进行致命打击。",
        "- 如果是科技/制造行业，必须质疑技术迭代、产能过剩、价格战和客户集中度风险。",
        "不要给我通用的宏观废话，必须一针见血地指出**该特定行业**和**该公司具体业务**最核心的命门。",
        "--- 以上行业背景推演必须融入你的质疑和风险场景 ---"
    ]
    return "\n".join(lines)


def _build_prompt(
    mode: str,
    consensus_direction: str,
    consensus_strength: float,
    signals: dict[str, AgentSignal | None],
    quality_report: QualityReport,
    company_context: dict | None = None,
) -> tuple[str, str]:
    """
    Construct dynamic prompts based on mode and consensus.

    Args:
        mode: "bear_case" | "bull_case" | "critical_questions"
        consensus_direction: "bullish" | "bearish" | "mixed"
        consensus_strength: 0.0-1.0
        signals: Front-running agent signals
        quality_report: Data quality context

    Returns:
        Tuple of (system_prompt, user_prompt)
    """
    from src.llm.prompts import (
        CONTRARIAN_BEAR_CASE_SYSTEM, CONTRARIAN_BEAR_CASE_USER,
        CONTRARIAN_BULL_CASE_SYSTEM, CONTRARIAN_BULL_CASE_USER,
        CONTRARIAN_CRITICAL_QUESTIONS_SYSTEM, CONTRARIAN_CRITICAL_QUESTIONS_USER,
    )

    # Select system prompt (with industry context appended)
    system_prompts = {
        "bear_case": CONTRARIAN_BEAR_CASE_SYSTEM,
        "bull_case": CONTRARIAN_BULL_CASE_SYSTEM,
        "critical_questions": CONTRARIAN_CRITICAL_QUESTIONS_SYSTEM,
    }
    system_prompt = system_prompts[mode]

    # Append industry context block (forces industry-specific analysis)
    if company_context:
        system_prompt = system_prompt + _build_industry_context_block(company_context)

    # Extract strongest arguments
    arguments = []
    valid_signals = [s for s in signals.values() if s is not None]

    if mode == "bear_case":
        # Extract bullish arguments
        for sig in valid_signals:
            if sig.signal == "bullish":
                # Limit to 200 chars
                reasoning = sig.reasoning[:200] if sig.reasoning else "无具体理由"
                arguments.append(f"[{sig.agent_name}] {reasoning}")

    elif mode == "bull_case":
        # Extract bearish arguments
        for sig in valid_signals:
            if sig.signal == "bearish":
                reasoning = sig.reasoning[:200] if sig.reasoning else "无具体理由"
                arguments.append(f"[{sig.agent_name}] {reasoning}")

    else:  # critical_questions
        # Extract all arguments
        for sig in valid_signals:
            reasoning = sig.reasoning[:200] if sig.reasoning else "无具体理由"
            arguments.append(f"[{sig.agent_name}/{sig.signal}] {reasoning}")

    # Format arguments
    if not arguments:
        arguments_text = "（前序分析师未提供明确论据）"
    else:
        arguments_text = "\n".join(arguments)

    # Format quality context
    quality_context = _format_quality_context(quality_report)

    # Select user template and fill
    user_templates = {
        "bear_case": CONTRARIAN_BEAR_CASE_USER,
        "bull_case": CONTRARIAN_BULL_CASE_USER,
        "critical_questions": CONTRARIAN_CRITICAL_QUESTIONS_USER,
    }
    user_template = user_templates[mode]

    # Build macro/industry context for user prompt
    ticker = company_context.get("ticker", "N/A") if company_context else "N/A"
    industry = company_context.get("sector", "未知行业") if company_context else "未知行业"
    analysis_date = company_context.get("analysis_date", "2026-03-08") if company_context else "2026-03-08"

    # Construct macro/industry context block
    macro_context_lines = [
        f"当前时间：{analysis_date}",
        f"标的代码：{ticker}",
        f"标的行业：{industry}",
        "",
        "全球宏观环境提示（2024-2026）：",
        "- 地缘政治：区域冲突持续，逆全球化与贸易保护主义，供应链重构",
        "- 经济周期：全球增长放缓，需求端面临不确定性",
        "- 货币环境：高利率周期的长尾效应与资本流动变化",
        "",
        "**任务要求**：请根据上述行业标签，自动结合该行业当前面临的微观与中观痛点进行分析，切忌说空话。",
    ]

    macro_industry_context = "\n".join(macro_context_lines)

    # Fill user prompt
    if mode in ["bear_case", "bull_case"]:
        user_prompt = user_template.format(
            ticker=ticker,
            industry=industry,
            analysis_date=analysis_date,
            macro_industry_context=macro_industry_context,
            consensus_direction=consensus_direction,
            consensus_strength=consensus_strength,
            strongest_arguments=arguments_text,
            quality_context=quality_context,
        )
    else:  # critical_questions
        user_prompt = user_template.format(
            ticker=ticker,
            industry=industry,
            analysis_date=analysis_date,
            macro_industry_context=macro_industry_context,
            consensus_direction=consensus_direction,
            consensus_strength=consensus_strength,
            all_arguments=arguments_text,
            quality_context=quality_context,
        )

    return system_prompt, user_prompt


def _validate_json(json_str: str, mode: str) -> tuple[bool, dict[str, Any] | None]:
    """
    Validate JSON output from LLM.

    Args:
        json_str: Raw JSON string from LLM
        mode: Expected mode ("bear_case" | "bull_case" | "critical_questions")

    Returns:
        Tuple of (is_valid, parsed_data or None)
    """
    try:
        # Strip markdown code fences if present (LLMs often wrap JSON in ```json...```)
        cleaned = json_str.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first line (```json or ```) and last line (```)
            lines = [l for l in lines[1:] if l.strip() != "```"]
            cleaned = "\n".join(lines)
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning(f"[Contrarian] JSON parse error: {e}")
        return False, None

    # Check required fields
    if not isinstance(data, dict):
        logger.warning("[Contrarian] JSON is not a dict")
        return False, None

    if data.get("mode") != mode:
        logger.warning(f"[Contrarian] Mode mismatch: expected {mode}, got {data.get('mode')}")
        return False, None

    # Mode-specific validation
    if mode == "bear_case":
        required = ["consensus", "assumption_challenges", "risk_scenarios", "reasoning"]
    elif mode == "bull_case":
        required = ["consensus", "overlooked_positives", "reasoning"]
    else:  # critical_questions
        required = ["consensus", "core_contradiction", "questions", "reasoning"]

    for field in required:
        if field not in data:
            logger.warning(f"[Contrarian] Missing required field: {field}")
            return False, None

    return True, data


def _call_llm(system_prompt: str, user_prompt: str) -> str:
    """
    Call LLM with contrarian_analysis task.

    Args:
        system_prompt: System instruction
        user_prompt: User query

    Returns:
        LLM response text

    Raises:
        Exception: If LLM call fails
    """
    from src.llm.router import call_llm

    response = call_llm(
        "contrarian_analysis",
        system_prompt,
        user_prompt,
    )

    return response


def run(
    ticker: str,
    market: MarketType,
    *,
    signals: dict[str, AgentSignal | None],
    quality_report: QualityReport,
    use_llm: bool = True,
    company_context: dict | None = None,  # NEW: industry context from QVeris
) -> AgentSignal:
    """
    Run Contrarian Agent with dynamic mode switching.

    Args:
        ticker: Stock ticker
        market: Market type
        signals: Front-running agent signals
        quality_report: Data quality context
        use_llm: Whether to use LLM (False for quick mode)

    Returns:
        AgentSignal with dialectical analysis
    """
    logger.info(f"[Contrarian] Starting analysis for {ticker}")

    # Handle case: no signals available
    if not signals or all(s is None for s in signals.values()):
        agent_signal = AgentSignal(
            ticker=ticker,
            agent_name=AGENT_NAME,
            signal="neutral",
            confidence=0.20,
            reasoning="无可用信号，辩证分析无法运行。",
            metrics={"error": "no_signals"},
        )
        insert_agent_signal(agent_signal)
        logger.warning(f"[Contrarian] {ticker}: no signals, returning neutral")
        return agent_signal

    # Step 1: Calculate consensus
    consensus_direction, consensus_strength = _determine_consensus(signals)
    logger.info(f"[Contrarian] Consensus: {consensus_direction} ({consensus_strength:.0%})")

    # Step 2: Select mode
    mode, signal_output = _select_mode(consensus_direction, consensus_strength)
    logger.info(f"[Contrarian] Selected mode: {mode}, signal: {signal_output}")

    # Handle case: LLM disabled
    if not use_llm:
        agent_signal = AgentSignal(
            ticker=ticker,
            agent_name=AGENT_NAME,
            signal=signal_output,
            confidence=0.30,
            reasoning=f"LLM分析暂不可用。共识:{consensus_direction}({consensus_strength:.0%}), 模式:{mode}",
            metrics={
                "mode": mode,
                "consensus": {
                    "direction": consensus_direction,
                    "strength": consensus_strength
                },
                "llm_disabled": True
            },
        )
        insert_agent_signal(agent_signal)
        return agent_signal

    # Step 3: Build prompts
    try:
        system_prompt, user_prompt = _build_prompt(
            mode, consensus_direction, consensus_strength, signals, quality_report,
            company_context=company_context,  # NEW
        )
    except Exception as e:
        logger.error(f"[Contrarian] Prompt construction failed: {e}")
        agent_signal = AgentSignal(
            ticker=ticker,
            agent_name=AGENT_NAME,
            signal="neutral",
            confidence=0.30,
            reasoning=f"提示构建失败: {str(e)}",
            metrics={"error": "prompt_construction_failed"},
        )
        insert_agent_signal(agent_signal)
        return agent_signal

    # Step 4: Call LLM
    try:
        llm_response = _call_llm(system_prompt, user_prompt)
    except Exception as e:
        logger.error(f"[Contrarian] LLM call failed: {e}")
        agent_signal = AgentSignal(
            ticker=ticker,
            agent_name=AGENT_NAME,
            signal="neutral",
            confidence=0.30,
            reasoning=f"LLM调用失败: {str(e)}",
            metrics={
                "mode": mode,
                "consensus": {
                    "direction": consensus_direction,
                    "strength": consensus_strength
                },
                "error": "llm_call_failed"
            },
        )
        insert_agent_signal(agent_signal)
        return agent_signal

    # Step 5: Validate JSON
    is_valid, parsed_data = _validate_json(llm_response, mode)

    if not is_valid:
        # Fallback: extract text reasoning
        reasoning_text = llm_response[:500] if llm_response else "JSON解析失败"
        agent_signal = AgentSignal(
            ticker=ticker,
            agent_name=AGENT_NAME,
            signal=signal_output,
            confidence=0.40,
            reasoning=f"JSON验证失败。原始输出: {reasoning_text}",
            metrics={
                "mode": mode,
                "consensus": {
                    "direction": consensus_direction,
                    "strength": consensus_strength
                },
                "json_invalid": True
            },
        )
        insert_agent_signal(agent_signal)
        logger.warning(f"[Contrarian] {ticker}: JSON validation failed")
        return agent_signal

    # Step 6: Build successful AgentSignal
    reasoning_text = parsed_data.get("reasoning", "（无综合论述）")

    # MVP: Fixed confidence 0.60 (marked as uncalibrated)
    confidence = 0.60

    agent_signal = AgentSignal(
        ticker=ticker,
        agent_name=AGENT_NAME,
        signal=signal_output,
        confidence=confidence,
        reasoning=reasoning_text,
        metrics=parsed_data,  # Full JSON as metrics
    )

    insert_agent_signal(agent_signal)
    logger.info(f"[Contrarian] {ticker}: {signal_output} (mode={mode}, confidence={confidence:.2f})")

    return agent_signal
