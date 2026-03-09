"""Chapter Context - Cross-chapter information sharing architecture.

Phase 3 implementation: Aggregates agent signals and quality data into a
structured context object that is injected into chapter prompts.

This solves the problem of chapters being generated in isolation without
knowledge of conclusions from other chapters (e.g., sentiment chapter
saying "bearish" but recommendation chapter saying "bullish").

Usage:
    context = ChapterContext.from_agent_signals(signals, quality_report)
    ch7_prompt = context.inject_into_prompt(REPORT_CH7_USER)
"""

from dataclasses import dataclass, field
from typing import Any

from src.data.models import AgentSignal, QualityReport
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ChapterContext:
    """Aggregated context from all agents for cross-chapter information sharing.

    This context is built after all agents complete and before report generation.
    It provides a consistent view of analysis conclusions across all chapters.
    """

    # Fundamentals context
    fundamental_signal: str = "neutral"  # bullish/neutral/bearish
    fundamental_score: int | None = None  # 0-100 score
    fundamental_key_metrics: dict[str, Any] = field(default_factory=dict)

    # Valuation context
    valuation_signal: str = "neutral"
    valuation_target_price: float | None = None
    valuation_margin_of_safety: float | None = None
    valuation_mode: str = "standard"  # loss_making_tech/growth_stock/financial/cyclical/standard
    valuation_methods_used: list[str] = field(default_factory=list)
    valuation_methods_excluded: list[str] = field(default_factory=list)

    # Sentiment context
    sentiment_direction: str = "neutral"  # positive/negative/neutral
    sentiment_key_events: list[str] = field(default_factory=list)
    sentiment_confidence: float = 0.5

    # Forward-looking data (from sentiment extraction)
    profit_warning: str | None = None  # 预增/预亏/扭亏/None
    profit_warning_details: str | None = None

    # Contrarian context
    contrarian_mode: str | None = None  # bull_case/bear_case/critical_questions/None
    contrarian_key_points: list[str] = field(default_factory=list)

    # Data quality context
    data_quality_score: float = 0.0
    data_quality_critical_flags: list[str] = field(default_factory=list)
    data_quality_warnings: list[str] = field(default_factory=list)

    # Industry context
    industry: str = "default"
    industry_comparable_companies: list[dict] = field(default_factory=list)

    @classmethod
    def from_agent_signals(
        cls,
        signals: dict[str, AgentSignal],
        quality_report: QualityReport | None = None,
    ) -> "ChapterContext":
        """Build ChapterContext from agent signals and quality report.

        Args:
            signals: Dict mapping agent name to AgentSignal
            quality_report: Optional quality report from data layer

        Returns:
            ChapterContext with all fields populated
        """
        context = cls()

        # Extract fundamentals context
        if "fundamentals" in signals:
            fund = signals["fundamentals"]
            context.fundamental_signal = fund.signal
            context.fundamental_score = fund.metrics.get("score")
            context.fundamental_key_metrics = {
                "roe": fund.metrics.get("roe"),
                "net_margin": fund.metrics.get("net_margin"),
                "de_ratio": fund.metrics.get("de_ratio"),
                "revenue_growth": fund.metrics.get("revenue_growth"),
                "operating_cash_flow": fund.metrics.get("operating_cash_flow"),
            }

        # Extract valuation context
        if "valuation" in signals:
            val = signals["valuation"]
            context.valuation_signal = val.signal
            context.valuation_mode = val.metrics.get("valuation_mode", "standard")

            validation = val.metrics.get("validation", {})
            context.valuation_target_price = validation.get("weighted_target")
            context.valuation_methods_used = validation.get("valid_methods", [])
            context.valuation_methods_excluded = validation.get("excluded_methods", [])
            context.valuation_margin_of_safety = val.metrics.get("margin_of_safety")

        # Extract sentiment context
        if "sentiment" in signals:
            sent = signals["sentiment"]
            context.sentiment_confidence = sent.confidence

            # Determine sentiment direction from signal
            if sent.signal == "bullish":
                context.sentiment_direction = "positive"
            elif sent.signal == "bearish":
                context.sentiment_direction = "negative"
            else:
                context.sentiment_direction = "neutral"

            # Extract key events from metrics or reasoning
            context.sentiment_key_events = sent.metrics.get("key_events", [])

            # Extract profit warning if detected
            context.profit_warning = sent.metrics.get("profit_warning")
            context.profit_warning_details = sent.metrics.get("profit_warning_details")

        # Extract contrarian context
        if "contrarian" in signals:
            cont = signals["contrarian"]
            context.contrarian_mode = cont.metrics.get("mode")
            context.contrarian_key_points = cont.metrics.get("key_points", [])

        # Extract data quality context
        if quality_report:
            context.data_quality_score = quality_report.overall_quality_score
            # Extract critical flags and warnings from flags list by severity
            context.data_quality_critical_flags = [
                flag.detail for flag in quality_report.flags
                if flag.severity == "critical"
            ]
            context.data_quality_warnings = [
                flag.detail for flag in quality_report.flags
                if flag.severity == "warning"
            ]

        # Extract industry context from valuation
        if "valuation" in signals:
            val = signals["valuation"]
            context.industry = val.metrics.get("industry", "default")

        logger.info(
            f"[ChapterContext] Built context: "
            f"fundamental={context.fundamental_signal}, "
            f"valuation={context.valuation_signal}, "
            f"sentiment={context.sentiment_direction}, "
            f"profit_warning={context.profit_warning}"
        )

        return context

    def get_ch7_context_block(self) -> str:
        """Generate context block for Ch7 comprehensive recommendation.

        Returns a formatted string that can be injected into the Ch7 prompt.
        """
        lines = []

        # Fundamentals summary
        score_str = f"{self.fundamental_score}/100" if self.fundamental_score else "N/A"
        lines.append(f"**基本面信号**: {self.fundamental_signal} (评分: {score_str})")

        # Valuation summary
        target_str = f"¥{self.valuation_target_price:.2f}" if self.valuation_target_price else "N/A"
        mos_str = f"{self.valuation_margin_of_safety*100:.1f}%" if self.valuation_margin_of_safety else "N/A"
        lines.append(
            f"**估值信号**: {self.valuation_signal} "
            f"(目标价: {target_str}, 安全边际: {mos_str}, 模式: {self.valuation_mode})"
        )
        lines.append(f"  - 使用方法: {', '.join(self.valuation_methods_used)}")
        if self.valuation_methods_excluded:
            lines.append(f"  - 排除方法: {', '.join(self.valuation_methods_excluded)}")

        # Sentiment summary
        lines.append(
            f"**情绪方向**: {self.sentiment_direction} (置信度: {self.sentiment_confidence:.0%})"
        )
        if self.sentiment_key_events:
            events_str = "; ".join(self.sentiment_key_events[:3])  # Limit to 3 events
            lines.append(f"  - 关键事件: {events_str}")

        # Profit warning (CRITICAL - must be mentioned in recommendation)
        if self.profit_warning:
            lines.append(f"**⚠️ 业绩预告**: {self.profit_warning}")
            if self.profit_warning_details:
                lines.append(f"  - 详情: {self.profit_warning_details}")

        # Contrarian context
        if self.contrarian_mode:
            lines.append(f"**辩证分析模式**: {self.contrarian_mode}")
            if self.contrarian_key_points:
                points_str = "; ".join(self.contrarian_key_points[:3])
                lines.append(f"  - 要点: {points_str}")

        # Data quality warnings
        if self.data_quality_critical_flags:
            lines.append(f"**数据质量警告**: {', '.join(self.data_quality_critical_flags)}")

        return "\n".join(lines)

    def get_consistency_requirements(self) -> str:
        """Generate consistency requirements for Ch7.

        Returns rules that the LLM must follow to ensure consistency.
        """
        rules = []

        # Rule 1: Sentiment consistency
        rules.append(
            f"[重要] 情绪方向是【{self.sentiment_direction}】，"
            f"综合建议必须与此一致或明确解释差异原因"
        )

        # Rule 2: Profit warning must be mentioned
        if self.profit_warning:
            rules.append(
                f"[重要] 存在业绩预告信号【{self.profit_warning}】，"
                f"综合建议必须明确提及此信息"
            )

        # Rule 3: Valuation mode awareness
        if self.valuation_mode != "standard":
            mode_desc = {
                "loss_making_tech": "亏损期科技股（使用PS/EV-Sales估值）",
                "growth_stock": "盈利成长股（使用PEG估值）",
                "financial": "金融股（使用P/B-ROE估值）",
                "cyclical": "周期股（使用周期底部倍数）",
            }.get(self.valuation_mode, self.valuation_mode)
            rules.append(
                f"[注意] 该股票属于{mode_desc}，估值方法已针对性调整"
            )

        # Rule 4: Data quality awareness
        if self.data_quality_critical_flags:
            rules.append(
                f"[注意] 数据质量存在问题: {', '.join(self.data_quality_critical_flags)}，"
                f"分析结论需谨慎"
            )

        return "\n".join(rules)

    def inject_into_prompt(self, prompt_template: str) -> str:
        """Inject context into a prompt template.

        Replaces placeholders in the template with context values.

        Supported placeholders:
            {chapter_context} - Full context block
            {consistency_requirements} - Consistency rules
            {fundamental_signal}, {valuation_signal}, etc. - Individual values
        """
        # Build replacement dict
        replacements = {
            "chapter_context": self.get_ch7_context_block(),
            "consistency_requirements": self.get_consistency_requirements(),
            "fundamental_signal": self.fundamental_signal,
            "fundamental_score": str(self.fundamental_score) if self.fundamental_score else "N/A",
            "valuation_signal": self.valuation_signal,
            "valuation_target_price": f"¥{self.valuation_target_price:.2f}" if self.valuation_target_price else "N/A",
            "valuation_mos": f"{self.valuation_margin_of_safety*100:.1f}%" if self.valuation_margin_of_safety else "N/A",
            "valuation_mode": self.valuation_mode,
            "sentiment_direction": self.sentiment_direction,
            "sentiment_key_events": "; ".join(self.sentiment_key_events[:3]) if self.sentiment_key_events else "无",
            "profit_warning": self.profit_warning or "无",
            "contrarian_mode": self.contrarian_mode or "无",
            "data_quality_score": f"{self.data_quality_score:.2f}",
            "industry": self.industry,
        }

        # Replace all placeholders
        result = prompt_template
        for key, value in replacements.items():
            result = result.replace(f"{{{key}}}", str(value))

        return result
