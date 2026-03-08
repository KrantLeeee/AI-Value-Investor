"""Agent Registry — orchestrates all analysis agents for a given ticker.

Usage:
    from src.agents.registry import run_all_agents
    signals, report_path = run_all_agents("601808.SH", "a_share", quick=True)

Execution order (serial to share data between agents):
    Phase 1 (pure code, no LLM):
        1. Fundamentals Agent
        2. Valuation Agent
    Phase 2 (LLM agents — receive Phase 1 results as context):
        3. Buffett Agent
        4. Graham Agent
        5. Sentiment Agent  (independent of Phase 1 results)
    Phase 2.5 (contrarian analysis):
        6. Contrarian Agent  (analyzes consensus from all previous agents)
    Phase 3:
        7. Report Generator
"""

from datetime import date
from pathlib import Path

from src.data.models import AgentSignal
from src.utils.logger import get_logger

logger = get_logger(__name__)


def run_all_agents(
    ticker: str,
    market: str,
    *,
    quick: bool = False,
    use_llm: bool = True,
    analysis_date: str | None = None,
) -> tuple[dict[str, AgentSignal], Path]:
    """
    Orchestrate all agents for a given ticker.

    Args:
        ticker:    Full ticker with suffix, e.g. "601808.SH"
        market:    Market type: "a_share" | "hk" | "us"
        quick:     If True, skip all LLM calls (data-only report)
        use_llm:   Set to False to force no-LLM mode even for non-quick runs
        analysis_date: Override report date (default: today)

    Returns:
        (signals dict, report file Path)
    """
    if analysis_date is None:
        analysis_date = str(date.today())

    _use_llm = use_llm and not quick
    signals: dict[str, AgentSignal] = {}

    logger.info("[Registry] Starting analysis for %s (%s) | quick=%s llm=%s",
                ticker, market, quick, _use_llm)

    # ── Phase 0: Data Quality ─────────────────────────────────────────────────
    from src.data import database
    from src.data.quality import run_quality_checks
    from src.data.models import QualityReport

    try:
        logger.info("[Registry] Running data quality checks...")
        from src.data.models import IncomeStatement, BalanceSheet, CashFlow, DailyPrice

        def _to_models(rows: list[dict], model_cls):
            """Convert DB dicts to Pydantic model objects, skipping invalid rows."""
            result = []
            for row in rows:
                try:
                    result.append(model_cls(**{
                        k: v for k, v in row.items()
                        if k in model_cls.model_fields
                    }))
                except Exception:
                    pass
            return result

        raw_data = {
            'income':   _to_models(database.get_income_statements(ticker, limit=10), IncomeStatement),
            'balance':  _to_models(database.get_balance_sheets(ticker, limit=10), BalanceSheet),
            'cashflow': _to_models(database.get_cash_flows(ticker, limit=10), CashFlow),
            'prices':   _to_models(database.get_latest_prices(ticker, limit=10), DailyPrice),
        }

        quality_report = run_quality_checks(ticker, market, raw_data)
        logger.info(f"[Registry] Quality score: {quality_report.overall_quality_score:.2f}, "
                   f"completeness: {quality_report.data_completeness:.2%}")
    except Exception as e:
        logger.error(f"[Registry] Quality checks failed: {e}")
        # Create empty quality report as fallback
        quality_report = QualityReport(
            ticker=ticker,
            market=market,
            flags=[],
            overall_quality_score=0.5,
            data_completeness=0.0,
            stale_fields=[],
            records_checked={}
        )

    # ── Phase -1: Company Context (runs BEFORE all agents) ───────────────────
    # Fetch company basics from QVeris iFinD so all downstream agents know
    # the company name, industry, main business before analysis begins.
    company_context: dict = {}
    try:
        from src.data.fetcher import Fetcher
        fetcher = Fetcher()
        basics = fetcher.fetch_company_basics(ticker, market)
        if basics:
            company_context = basics
            logger.info(
                "[Registry] Company context: %s | business: %s",
                basics.get('company_name', ticker),
                (basics.get('main_business') or '')[:30],
            )
        else:
            logger.warning("[Registry] Company basics not available (QVeris returned None)")
    except Exception as e:
        logger.warning("[Registry] Company context fetch failed: %s", e)


    try:
        from src.agents import fundamentals
        logger.info("[Registry] Running Fundamentals Agent...")
        signals["fundamentals"] = fundamentals.run(ticker, market)
    except Exception as e:
        logger.error("[Registry] Fundamentals Agent failed: %s", e)

    try:
        from src.agents import valuation
        logger.info("[Registry] Running Valuation Agent...")
        signals["valuation"] = valuation.run(ticker, market, use_llm=_use_llm)
    except Exception as e:
        logger.error("[Registry] Valuation Agent failed: %s", e)

    # ── Phase 2: LLM agents ───────────────────────────────────────────────────
    try:
        from src.agents import warren_buffett
        logger.info("[Registry] Running Buffett Agent...")
        signals["warren_buffett"] = warren_buffett.run(
            ticker, market,
            fundamentals_signal=signals.get("fundamentals"),
            valuation_signal=signals.get("valuation"),
            use_llm=_use_llm,
        )
    except Exception as e:
        logger.error("[Registry] Buffett Agent failed: %s", e)

    try:
        from src.agents import ben_graham
        logger.info("[Registry] Running Graham Agent...")
        signals["ben_graham"] = ben_graham.run(
            ticker, market,
            valuation_signal=signals.get("valuation"),
            use_llm=_use_llm,
        )
    except Exception as e:
        logger.error("[Registry] Graham Agent failed: %s", e)

    try:
        from src.agents import sentiment
        logger.info("[Registry] Running Sentiment Agent...")
        signals["sentiment"] = sentiment.run(ticker, market, use_llm=_use_llm)
    except Exception as e:
        logger.error("[Registry] Sentiment Agent failed: %s", e)

    # ── Phase 2.5: Contrarian Agent ───────────────────────────────────────────
    try:
        from src.agents import contrarian
        logger.info("[Registry] Running Contrarian Agent...")
        signals["contrarian"] = contrarian.run(
            ticker=ticker,
            market=market,
            signals=signals,
            quality_report=quality_report,
            use_llm=_use_llm,
            company_context=company_context,  # NEW: inject industry context
        )
    except Exception as e:
        logger.error("[Registry] Contrarian Agent failed: %s", e)

    # ── Phase 3: Report Generator ─────────────────────────────────────────────
    try:
        from src.agents import report_generator
        logger.info("[Registry] Generating report...")
        _, report_path = report_generator.run(
            ticker, market,
            signals=signals,
            quality_report=quality_report,
            analysis_date=analysis_date,
            use_llm=_use_llm,
            company_context=company_context,  # NEW: inject company context
        )
    except Exception as e:
        logger.error("[Registry] Report generation failed: %s", e)
        from src.utils.config import get_project_root
        report_path = get_project_root() / "output" / "reports" / f"{ticker}_{analysis_date}_error.md"

    logger.info("[Registry] Analysis complete for %s → %s", ticker, report_path)
    return signals, report_path
