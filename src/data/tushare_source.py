"""Tushare Pro data source adapter — enterprise-grade China financial data.

Tushare Pro API: https://tushare.pro/document/2
Requires token authentication (free tier: 2000 pts/day, 1pt per call)

Key APIs:
- daily: daily OHLCV price data (trade_date, open, high, low, close, vol)
- income: income statement (end_date, total_revenue, n_income, etc.)
- balancesheet: balance sheet (end_date, total_assets, total_liab, total_equity)
  - comp_type: 1=工商业, 2=银行, 3=保险, 4=证券 (V3 industry detection)
- cashflow: cash flow (end_date, n_cashflow_act, etc.)
- fina_indicator: financial metrics (roe, roa, roic, current_ratio, etc.)

Tushare uses different ticker format:
- A-share: "601808.SH", "000002.SZ" (same as our format)
- Period types: Tushare has no direct "annual" param, we filter by end_date month (Q4 = annual)

V3 Industry Engine Integration:
- comp_type field directly identifies bank/insurance/securities companies
- Balance sheet includes inventory, advance_receipts, fixed_assets for industry detection
"""

import os
from datetime import date, datetime

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

# Tushare Pro token from environment variable (required)
# Register at https://tushare.pro to get your token
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

# Optional: Custom Tushare API endpoint (for mirrors/proxies)
# If not set, uses official Tushare API
TUSHARE_API_URL = os.environ.get("TUSHARE_API_URL", "")


class TushareSource(BaseDataSource):
    """Tushare Pro data source (A-share only, premium quality).

    Note: Tushare is an optional dependency. If not installed, all methods
    return empty results gracefully.
    """

    source_name = "tushare"

    def __init__(self):
        self._api = None
        self._available = None  # Cached availability check

    def _is_available(self) -> bool:
        """Check if tushare package is installed."""
        if self._available is None:
            try:
                import tushare  # noqa: F401
                self._available = True
            except ImportError:
                self._available = False
                logger.debug("[Tushare] Package not installed - source disabled")
        return self._available

    def _get_api(self):
        """Lazy init Tushare API connection.

        Supports custom API endpoint via TUSHARE_API_URL env var.
        This is useful for Tushare mirrors/proxies.
        """
        if not self._is_available():
            return None
        if self._api is None:
            try:
                import tushare as ts
                ts.set_token(TUSHARE_TOKEN)
                self._api = ts.pro_api()

                # Support custom API endpoint (mirror/proxy)
                if TUSHARE_API_URL:
                    # Set private attributes as required by some Tushare mirrors
                    self._api._DataApi__token = TUSHARE_TOKEN
                    self._api._DataApi__http_url = TUSHARE_API_URL
                    logger.info("[Tushare] Using custom API endpoint: %s", TUSHARE_API_URL)
            except Exception as e:
                logger.error("[Tushare] Failed to initialize API: %s", e)
                return None
        return self._api

    def supports_market(self, market: MarketType) -> bool:
        """Only supports A-share market (and only if tushare is installed)."""
        return market == "a_share" and self._is_available()

    def health_check(self) -> bool:
        """Test API connectivity with a lightweight call."""
        if not self._is_available():
            return False
        try:
            api = self._get_api()
            if api is None:
                return False
            # Test with a simple query (1pt)
            df = api.trade_cal(exchange='SSE', start_date='20240101', end_date='20240102')
            return df is not None and not df.empty
        except Exception as e:
            logger.warning("[Tushare] health_check failed: %s", e)
            return False

    def get_daily_prices(
        self, ticker: str, market: MarketType,
        start_date: date, end_date: date,
    ) -> list[DailyPrice]:
        """Fetch daily OHLCV price data from Tushare Pro."""
        if market != "a_share":
            return []

        api = self._get_api()
        if api is None:
            return []
        ts_code = ticker  # e.g. "601808.SH" (already in Tushare format)
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")
        results: list[DailyPrice] = []

        try:
            df = api.daily(
                ts_code=ts_code,
                start_date=start_str,
                end_date=end_str,
            )

            if df is None or df.empty:
                logger.warning("[Tushare] No price data for %s", ticker)
                return []

            # Tushare columns: trade_date, open, high, low, close, vol (volume in shares)
            for _, row in df.iterrows():
                trade_date_str = str(row["trade_date"])
                trade_date = datetime.strptime(trade_date_str, "%Y%m%d").date()

                results.append(DailyPrice(
                    ticker=ticker,
                    market=market,
                    date=trade_date,
                    open=float(row["open"]) if row["open"] else None,
                    high=float(row["high"]) if row["high"] else None,
                    low=float(row["low"]) if row["low"] else None,
                    close=float(row["close"]),
                    volume=int(row["vol"]) if row["vol"] else 0,
                    source=self.source_name,
                ))

            logger.info("[Tushare] %s: fetched %d price rows", ticker, len(results))
        except Exception as e:
            logger.warning("[Tushare] get_daily_prices failed for %s: %s", ticker, e)

        return results

    def get_income_statements(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[IncomeStatement]:
        """Fetch income statements from Tushare Pro."""
        if market != "a_share":
            return []

        api = self._get_api()
        if api is None:
            return []
        ts_code = ticker
        results: list[IncomeStatement] = []

        try:
            # Tushare income API: end_date, total_revenue, revenue, operate_profit, n_income, etc.
            # Values are in CNY (元), need to check API docs for actual units
            # Note: total_share might not be available in income API, will silently ignore if missing
            df = api.income(ts_code=ts_code, fields=[
                'ts_code', 'end_date', 'total_revenue', 'revenue',
                'oper_cost', 'operate_profit', 'n_income', 'basic_eps', 'total_share'
            ])

            if df is None or df.empty:
                return []

            # Sort by end_date descending
            df = df.sort_values("end_date", ascending=False)

            # Filter for annual reports if requested (end_date ends with 1231)
            if period_type == "annual":
                df = df[df["end_date"].astype(str).str.endswith("1231")]

            df = df.iloc[:limit]

            for _, row in df.iterrows():
                end_date_str = str(row["end_date"])
                period_end = datetime.strptime(end_date_str, "%Y%m%d").date()

                # Fix: Check for non-zero explicitly to avoid treating 0.0 as None
                revenue = None
                if row["total_revenue"] is not None and row["total_revenue"] != 0:
                    revenue = float(row["total_revenue"])
                elif row["revenue"] is not None:
                    revenue = float(row["revenue"])

                cost = float(row["oper_cost"]) if row["oper_cost"] else None
                gross = (revenue - cost) if (revenue and cost) else None

                # Extract shares outstanding if available (unit: shares, 万股 in Tushare = 10,000 shares)
                # Tushare's total_share is in 万股 (10k shares), need to convert to shares
                total_share = None
                if "total_share" in row.index and row["total_share"] is not None:
                    total_share = float(row["total_share"]) * 10000  # 万股 → 股

                results.append(IncomeStatement(
                    ticker=ticker,
                    period_end_date=period_end,
                    period_type=period_type,
                    revenue=revenue,
                    cost_of_revenue=cost,
                    gross_profit=gross,
                    operating_income=float(row["operate_profit"]) if row["operate_profit"] else None,
                    net_income=float(row["n_income"]) if row["n_income"] else None,
                    eps=float(row["basic_eps"]) if row["basic_eps"] else None,
                    shares_outstanding=total_share,
                    source=self.source_name,
                ))

            logger.info("[Tushare] %s income: %d rows", ticker, len(results))
        except Exception as e:
            logger.warning("[Tushare] get_income_statements failed for %s: %s", ticker, e)

        return results

    def get_balance_sheets(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[BalanceSheet]:
        """Fetch balance sheets from Tushare Pro.

        V3 Industry Engine Integration:
        - comp_type: 1=工商业, 2=银行, 3=保险, 4=证券
        - Sets has_loan_loss_provision=True for banks (comp_type=2)
        - Sets has_insurance_reserve=True for insurance (comp_type=3)
        - Extracts inventory, advance_receipts, fixed_assets for industry detection
        """
        if market != "a_share":
            return []

        api = self._get_api()
        if api is None:
            return []
        ts_code = ticker
        results: list[BalanceSheet] = []

        try:
            # Request V3 fields: inventories, adv_receipts, fix_assets, comp_type
            # Also request bank/insurance indicator fields for validation
            df = api.balancesheet(ts_code=ts_code, fields=[
                'ts_code', 'end_date', 'comp_type',
                'total_assets', 'total_liab', 'total_hldr_eqy_exc_min_int',
                'total_cur_assets', 'total_cur_liab', 'money_cap', 'total_share',
                'st_borr', 'lt_borr',
                # V3 industry detection fields
                'inventories', 'adv_receipts', 'fix_assets',
                # Bank indicator fields
                'decr_in_disbur', 'cb_borr', 'depos_ib_deposits',
                # Insurance indicator fields
                'rsrv_insur_cont', 'reser_une_prem', 'reser_lins_liab',
            ])

            if df is None or df.empty:
                return []

            df = df.sort_values("end_date", ascending=False)

            if period_type == "annual":
                df = df[df["end_date"].astype(str).str.endswith("1231")]

            df = df.iloc[:limit]

            for _, row in df.iterrows():
                end_date_str = str(row["end_date"])
                period_end = datetime.strptime(end_date_str, "%Y%m%d").date()

                st_debt = float(row["st_borr"]) if row.get("st_borr") else 0
                lt_debt = float(row["lt_borr"]) if row.get("lt_borr") else 0
                total_debt = (st_debt + lt_debt) if (st_debt or lt_debt) else None

                # V3 Industry Detection via comp_type
                # comp_type: 1=工商业, 2=银行, 3=保险, 4=证券
                comp_type = int(row["comp_type"]) if row.get("comp_type") else 1
                has_loan_loss_provision = comp_type == 2  # Bank
                has_insurance_reserve = comp_type == 3    # Insurance

                # Additional validation: check if bank/insurance fields have values
                if not has_loan_loss_provision:
                    # Double-check with bank-specific fields
                    bank_fields = ['decr_in_disbur', 'cb_borr', 'depos_ib_deposits']
                    bank_hits = sum(1 for f in bank_fields if row.get(f) and float(row[f]) > 0)
                    if bank_hits >= 2:
                        has_loan_loss_provision = True
                        logger.debug("[Tushare] %s detected as bank via balance sheet fields", ticker)

                if not has_insurance_reserve:
                    # Double-check with insurance-specific fields
                    ins_fields = ['rsrv_insur_cont', 'reser_une_prem', 'reser_lins_liab']
                    ins_hits = sum(1 for f in ins_fields if row.get(f) and float(row[f]) > 0)
                    if ins_hits >= 2:
                        has_insurance_reserve = True
                        logger.debug("[Tushare] %s detected as insurance via balance sheet fields", ticker)

                results.append(BalanceSheet(
                    ticker=ticker,
                    period_end_date=period_end,
                    period_type=period_type,
                    total_assets=float(row["total_assets"]) if row.get("total_assets") else None,
                    total_liabilities=float(row["total_liab"]) if row.get("total_liab") else None,
                    total_equity=float(row["total_hldr_eqy_exc_min_int"]) if row.get("total_hldr_eqy_exc_min_int") else None,
                    current_assets=float(row["total_cur_assets"]) if row.get("total_cur_assets") else None,
                    current_liabilities=float(row["total_cur_liab"]) if row.get("total_cur_liab") else None,
                    cash_and_equivalents=float(row["money_cap"]) if row.get("money_cap") else None,
                    total_debt=total_debt,
                    # V3 fields
                    inventory=float(row["inventories"]) if row.get("inventories") else None,
                    advance_receipts=float(row["adv_receipts"]) if row.get("adv_receipts") else None,
                    fixed_assets=float(row["fix_assets"]) if row.get("fix_assets") else None,
                    has_loan_loss_provision=has_loan_loss_provision,
                    has_insurance_reserve=has_insurance_reserve,
                    source=self.source_name,
                ))

            logger.info("[Tushare] %s balance: %d rows (financial=%s)",
                       ticker, len(results),
                       has_loan_loss_provision or has_insurance_reserve)
        except Exception as e:
            logger.warning("[Tushare] get_balance_sheets failed for %s: %s", ticker, e)

        return results

    def get_cash_flows(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[CashFlow]:
        """Fetch cash flow statements from Tushare Pro."""
        if market != "a_share":
            return []

        api = self._get_api()
        if api is None:
            return []
        ts_code = ticker
        results: list[CashFlow] = []

        try:
            df = api.cashflow(ts_code=ts_code, fields=[
                'ts_code', 'end_date', 'n_cashflow_act', 'n_cashflow_inv_act'
            ])

            if df is None or df.empty:
                return []

            df = df.sort_values("end_date", ascending=False)

            if period_type == "annual":
                df = df[df["end_date"].astype(str).str.endswith("1231")]

            df = df.iloc[:limit]

            for _, row in df.iterrows():
                end_date_str = str(row["end_date"])
                period_end = datetime.strptime(end_date_str, "%Y%m%d").date()

                op_cf = float(row["n_cashflow_act"]) if row["n_cashflow_act"] else None
                inv_cf = float(row["n_cashflow_inv_act"]) if row["n_cashflow_inv_act"] else None

                # FCF = operating CF + investing CF (investing is usually negative)
                fcf = None
                if op_cf is not None and inv_cf is not None:
                    fcf = op_cf + inv_cf
                elif op_cf is not None:
                    fcf = op_cf

                results.append(CashFlow(
                    ticker=ticker,
                    period_end_date=period_end,
                    period_type=period_type,
                    operating_cash_flow=op_cf,
                    free_cash_flow=fcf,
                    source=self.source_name,
                ))

            logger.info("[Tushare] %s cashflow: %d rows", ticker, len(results))
        except Exception as e:
            logger.warning("[Tushare] get_cash_flows failed for %s: %s", ticker, e)

        return results

    def get_financial_metrics(
        self, ticker: str, market: MarketType, limit: int = 10,
    ) -> list[FinancialMetrics]:
        """Fetch financial metrics from Tushare Pro fina_indicator API.

        Tushare fina_indicator provides comprehensive metrics:
        - Profitability: roe, roa, roic, grossprofit_margin, netprofit_margin
        - Liquidity: current_ratio, quick_ratio
        - Leverage: debt_to_assets, debt_to_eqt
        - Growth: netprofit_yoy, or_yoy
        - Valuation helpers: ebitda, bps
        """
        if market != "a_share":
            return []

        api = self._get_api()
        if api is None:
            return []
        ts_code = ticker
        results: list[FinancialMetrics] = []

        try:
            df = api.fina_indicator(ts_code=ts_code, fields=[
                'ts_code', 'end_date',
                # Profitability
                'roe', 'roa', 'roic',
                'grossprofit_margin', 'netprofit_margin',
                # Liquidity
                'current_ratio', 'quick_ratio',
                # Leverage
                'debt_to_assets', 'debt_to_eqt',
                # Growth
                'netprofit_yoy', 'or_yoy', 'tr_yoy',
                # Valuation helpers
                'ebitda', 'bps', 'eps',
                # Cash flow
                'ocfps', 'fcff', 'fcfe',
            ])

            if df is None or df.empty:
                logger.info("[Tushare] %s metrics: 0 rows", ticker)
                return []

            df = df.sort_values("end_date", ascending=False)

            # Filter for annual reports (Q4)
            df = df[df["end_date"].astype(str).str.endswith("1231")]
            df = df.iloc[:limit]

            for _, row in df.iterrows():
                end_date_str = str(row["end_date"])
                period_end = datetime.strptime(end_date_str, "%Y%m%d").date()

                # Helper to safely get float value
                def safe_float(field: str) -> float | None:
                    val = row.get(field)
                    if val is None or (isinstance(val, float) and val != val):  # NaN check
                        return None
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        return None

                # Convert percentages (Tushare returns as %, we store as decimal)
                def pct_to_decimal(field: str) -> float | None:
                    val = safe_float(field)
                    return val / 100 if val is not None else None

                results.append(FinancialMetrics(
                    ticker=ticker,
                    date=period_end,
                    # Profitability (convert % to decimal)
                    roe=pct_to_decimal("roe"),
                    roa=pct_to_decimal("roa"),
                    roic=pct_to_decimal("roic"),
                    gross_margin=pct_to_decimal("grossprofit_margin"),
                    operating_margin=pct_to_decimal("netprofit_margin"),  # Use net margin as proxy
                    # Liquidity
                    current_ratio=safe_float("current_ratio"),
                    # Leverage (debt_to_eqt is D/E ratio, convert % to decimal)
                    debt_to_equity=pct_to_decimal("debt_to_eqt"),
                    # Growth (convert % to decimal)
                    revenue_growth=pct_to_decimal("or_yoy"),
                    net_income_growth=pct_to_decimal("netprofit_yoy"),
                    source=self.source_name,
                ))

            logger.info("[Tushare] %s metrics: %d rows", ticker, len(results))
        except Exception as e:
            logger.warning("[Tushare] get_financial_metrics failed for %s: %s", ticker, e)

        return results
