"""QVeris iFinD Data Source — A-share financial data via QVeris API.

Uses the THS iFinD (同花顺) tools available through QVeris to retrieve:
  - Company name, main business, registered capital (company_basics)
  - Income statements (利润表)
  - Balance sheets (资产负债表)
  - Cash flow statements (现金流量表)

This source serves as a fallback/supplement for AKShare when data is stale
or missing (e.g., shares_outstanding, current_assets, current_liabilities).

Tool IDs:
  - ths_ifind.company_basics.v1
  - ths_ifind.financial_statements.v1   (statement_type: income/balance/cashflow)

QVeris API endpoint: https://qveris.ai/api/v1
"""

import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from src.data.base_source import BaseDataSource
from src.data.models import (
    BalanceSheet,
    CashFlow,
    DailyPrice,
    FinancialMetrics,
    IncomeStatement,
    MarketType,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Path to the QVeris tool script
_QVERIS_SCRIPT = Path.home() / ".openclaw" / "skills" / "qveris" / "scripts" / "qveris_tool.mjs"

# Known search IDs for tool discovery (refresh periodically)
# Format: { tool_id: search_id } — allows calling execute without re-searching
_KNOWN_TOOLS = {
    "ths_ifind.financial_statements.v1": "f4db1467-a715-4369-b0a8-e76d69d3497f",
    "ths_ifind.company_basics.v1": "f4db1467-a715-4369-b0a8-e76d69d3497f",
}

# Period mapping: year → period string for annual annual reports
_ANNUAL_PERIOD = "1231"  # 年度报告截止日期
_EXHAUSTED_KEYS = set()  # Circuit breaker for exhausted API keys


def _get_api_keys() -> list[str]:
    """Get QVERIS_API_KEYS from env as a list."""
    # Try env var first (runtime), then .env file (supports both KEYS and KEY)
    keys_str = os.environ.get("QVERIS_API_KEYS") or os.environ.get("QVERIS_API_KEY")
    if not keys_str:
        # Try loading from configuration
        try:
            from src.utils.config import get_settings
            settings = get_settings()
            keys_str = getattr(settings, "qveris_api_keys", None) or getattr(settings, "qveris_api_key", None)
        except Exception:
            pass
            
    if not keys_str:
        return []
        
    return [k.strip() for k in keys_str.split(",") if k.strip()]


def _call_qveris(tool_id: str, params: dict, search_id: str | None = None) -> dict | None:
    """
    Execute a QVeris tool via the Node.js CLI script.
    Rotates through available API keys until one succeeds.

    Returns:
        Parsed JSON result dict, or None on failure.
    """
    api_keys = _get_api_keys()
    if not api_keys:
        logger.warning("[QVeris] No QVERIS API keys found, skipping")
        return None

    if not _QVERIS_SCRIPT.exists():
        logger.warning("[QVeris] Tool script not found at %s", _QVERIS_SCRIPT)
        return None

    for api_key in api_keys:
        if api_key in _EXHAUSTED_KEYS:
            continue

        cmd = [
            "node", str(_QVERIS_SCRIPT),
            "execute", tool_id,
            "--params", json.dumps(params),
            "--json",
        ]
        if search_id:
            cmd += ["--search-id", search_id]

        env = {**os.environ, "QVERIS_API_KEY": api_key}

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30, env=env
            )
            if result.returncode != 0:
                err_msg = result.stderr.strip()
                if "HTTP Error: 402" in err_msg or "Insufficient credits" in err_msg:
                    logger.error("[QVeris] Credits exhausted (402) for key %s...", api_key[:6])
                    _EXHAUSTED_KEYS.add(api_key)
                    continue # Try next key
                else:
                    logger.warning("[QVeris] CLI error with key %s...: %s", api_key[:6], err_msg[:200])
                    continue # Try next key
            
            data = json.loads(result.stdout)
            # The --json flag returns the raw API response
            if isinstance(data, dict) and data.get("result"):
                return data["result"]
            return data
            
        except subprocess.TimeoutExpired:
            logger.warning("[QVeris] Tool %s timed out with key %s...", tool_id, api_key[:6])
            continue
        except json.JSONDecodeError as e:
            logger.warning("[QVeris] JSON parse error: %s", e)
            continue
        except Exception as e:
            logger.warning("[QVeris] Unexpected error: %s", e)
            continue
            
    # If we get here, all keys failed or were exhausted
    logger.debug("[QVeris] All available API keys failed or exhausted for tool %s", tool_id)
    return None


def fetch_company_basics(ticker: str) -> dict | None:
    """
    Fetch company basic info from iFinD.

    Returns:
        Dict with company_name, main_business, registered_capital, concepts, etc.
        Or None if unavailable.
    """
    result = _call_qveris(
        "ths_ifind.company_basics.v1",
        {"codes": ticker},
        _KNOWN_TOOLS.get("ths_ifind.company_basics.v1"),
    )

    if not result:
        return None

    try:
        rows = result.get("data", [[]])[0]
        if not rows:
            return None
        row = rows[0]
        return {
            "ticker": ticker,
            "company_name": row.get("ths_corp_cn_name_stock"),
            "main_business": row.get("ths_main_businuess_stock"),
            "main_products": row.get("ths_mo_product_name_stock"),
            "registered_capital": row.get("ths_reg_capital_stock"),
            "established_date": row.get("ths_established_date_stock"),
            "industry_tag": row.get("ths_the_ths_industry_stock"),
            "concepts": row.get("ths_the_concept_stock"),
        }
    except Exception as e:
        logger.warning("[QVeris] company_basics parse error: %s", e)
        return None


def _parse_ifind_financials(
    result: dict,
    ticker: str,
    year: str,
    statement_type: str,
) -> list:
    """Parse iFinD financial statement response into model objects."""
    try:
        rows = result.get("data", [[]])[0]
        if not rows:
            return []
        return rows
    except Exception as e:
        logger.warning("[QVeris] Financial parse error (%s): %s", statement_type, e)
        return []


class QVerisSource(BaseDataSource):
    """
    Data source backed by QVeris iFinD API.
    Implements BaseDataSource interface for plug-in fallback support.

    Priority in a_share chain:
        akshare → baostock → qveris  (added as tertiary fallback)
    """

    source_name = "qveris"

    def supports_market(self, market: MarketType) -> bool:
        """QVeris iFinD only covers A-share market."""
        return market == "a_share"

    def health_check(self) -> bool:
        """Check if QVeris API key is set and script exists."""
        return bool(_get_api_keys()) and _QVERIS_SCRIPT.exists()

    # ── Income Statements ──────────────────────────────────────────────────────

    def get_income_statements(
        self,
        ticker: str,
        market: MarketType,
        period_type: str = "annual",
        limit: int = 5,
    ) -> list[IncomeStatement]:
        """Fetch income statements for last `limit` annual periods."""
        if market != "a_share":
            return []  # iFinD only covers A-shares

        results = []
        # Annual reports for year Y published in Apr of Y+1.
        # Use search_limit = limit+1 to compensate for the unfilled most-recent year.
        start_year = date.today().year - 1
        search_limit = limit + 1

        for i in range(search_limit):
            year = str(start_year - i)
            raw = _call_qveris(
                "ths_ifind.financial_statements.v1",
                {
                    "statement_type": "income",
                    "codes": ticker,
                    "year": year,
                    "period": _ANNUAL_PERIOD,
                    "type": "1",  # 合并报表
                },
                _KNOWN_TOOLS.get("ths_ifind.financial_statements.v1"),
            )
            if not raw:
                continue

            rows = _parse_ifind_financials(raw, ticker, year, "income")
            for row in rows:
                try:
                    rev = (
                        row.get("ths_revenue_stock")
                        or row.get("ths_operating_total_revenue_stock")
                    )
                    ni = (
                        row.get("ths_np_atoopc_stock")
                        or row.get("ths_np_stock")
                    )
                    # Skip empty placeholders (year not yet published)
                    if rev is None and ni is None:
                        continue
                    results.append(IncomeStatement(
                        ticker=ticker,
                        period_end_date=date(int(year), 12, 31),
                        period_type="annual",
                        revenue=rev,
                        gross_profit=None,
                        operating_income=row.get("ths_op_stock"),
                        net_income=ni,
                        ebitda=None,
                        eps=row.get("ths_basic_eps_stock"),
                        shares_outstanding=None,
                        source="qveris_ifind",
                    ))
                except Exception as e:
                    logger.debug("[QVeris] Income row parse error: %s", e)

        logger.info("[QVeris] %s income statements: %d records", ticker, len(results))
        return results

    # ── Balance Sheets ─────────────────────────────────────────────────────────

    def get_balance_sheets(
        self,
        ticker: str,
        market: MarketType,
        period_type: str = "annual",
        limit: int = 5,
    ) -> list[BalanceSheet]:
        """Fetch balance sheets for last `limit` annual periods."""
        if market != "a_share":
            return []

        results = []
        # A-share annual reports for year Y are published in Mar-Apr of Y+1.
        # On 2026-03-08, the 2025 annual report is NOT yet available.
        # Start from (today.year - 1) but try up to (limit + 1) years back
        # so we always retrieve at least `limit` rows of actual data.
        today = date.today()
        start_year = today.year - 1  # try most recent first, skip if empty
        search_limit = limit + 1     # request one extra year to compensate empty years

        for i in range(search_limit):
            year = str(start_year - i)
            raw = _call_qveris(
                "ths_ifind.financial_statements.v1",
                {
                    "statement_type": "balance",
                    "codes": ticker,
                    "year": year,
                    "period": _ANNUAL_PERIOD,
                    "type": "1",
                },
                _KNOWN_TOOLS.get("ths_ifind.financial_statements.v1"),
            )
            if not raw:
                continue

            rows = _parse_ifind_financials(raw, ticker, year, "balance")
            for row in rows:
                try:
                    total_assets = row.get("ths_total_assets_stock")
                    # Skip empty placeholders (annual report not yet published)
                    if total_assets is None:
                        continue
                    results.append(BalanceSheet(
                        ticker=ticker,
                        period_end_date=date(int(year), 12, 31),
                        period_type="annual",
                        total_assets=total_assets,
                        total_liabilities=row.get("ths_total_liab_stock"),
                        total_equity=row.get("ths_total_owner_equity_stock"),
                        current_assets=row.get("ths_total_current_assets_stock"),
                        current_liabilities=row.get("ths_total_current_liab_stock"),
                        cash_and_equivalents=row.get("ths_currency_fund_stock"),
                        total_debt=row.get("ths_total_liab_stock"),
                        book_value_per_share=None,
                        source="qveris_ifind",
                    ))
                except Exception as e:
                    logger.debug("[QVeris] Balance row parse error: %s", e)

        logger.info("[QVeris] %s balance sheets: %d records", ticker, len(results))
        return results

    # ── Cash Flows ─────────────────────────────────────────────────────────────

    def get_cash_flows(
        self,
        ticker: str,
        market: MarketType,
        period_type: str = "annual",
        limit: int = 5,
    ) -> list[CashFlow]:
        """Fetch cash flow statements for last `limit` annual periods."""
        if market != "a_share":
            return []

        results = []
        today = date.today()
        start_year = today.year - 1
        search_limit = limit + 1

        for i in range(search_limit):
            year = str(start_year - i)
            raw = _call_qveris(
                "ths_ifind.financial_statements.v1",
                {
                    "statement_type": "cash_flow",  # API requires underscore
                    "codes": ticker,
                    "year": year,
                    "period": _ANNUAL_PERIOD,
                    "type": "1",
                },
                _KNOWN_TOOLS.get("ths_ifind.financial_statements.v1"),
            )
            if not raw:
                continue

            rows = _parse_ifind_financials(raw, ticker, year, "cashflow")
            for row in rows:
                try:
                    ocf = row.get("ths_ncf_from_oa_stock")
                    capex = row.get("ths_purchase_of_fixed_assets_stock")
                    # Skip empty placeholders
                    if ocf is None and row.get("ths_ncf_from_ia_stock") is None:
                        continue

                    # Calculate FCF if both OCF and CapEx available
                    # FCF = Operating Cash Flow - Capital Expenditure
                    # Note: CapEx is typically negative or positive outflow in Chinese accounting
                    fcf = None
                    if ocf is not None and capex is not None:
                        fcf = ocf - abs(capex)  # Ensure capex is treated as outflow
                    elif ocf is not None:
                        fcf = ocf  # Use OCF as proxy if no CapEx data

                    results.append(CashFlow(
                        ticker=ticker,
                        period_end_date=date(int(year), 12, 31),
                        period_type="annual",
                        operating_cash_flow=ocf,
                        investing_cash_flow=row.get("ths_ncf_from_ia_stock"),
                        financing_cash_flow=row.get("ths_ncf_from_fa_stock"),
                        free_cash_flow=fcf,
                        capital_expenditure=capex,
                        source="qveris_ifind",
                    ))
                except Exception as e:
                    logger.debug("[QVeris] Cashflow row parse error: %s", e)

        logger.info("[QVeris] %s cash flows: %d records", ticker, len(results))
        return results

    # ── Not supported by iFinD API ─────────────────────────────────────────────

    def get_daily_prices(self, ticker: str, market: MarketType, **kwargs) -> list[DailyPrice]:
        """Price data: not fetched via QVeris iFinD (use AKShare instead)."""
        return []

    def get_financial_metrics(self, ticker: str, market: MarketType, **kwargs) -> list[FinancialMetrics]:
        """Ratio metrics: not fetched via QVeris iFinD (use AKShare instead)."""
        return []
