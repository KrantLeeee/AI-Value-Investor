"""Tushare Pro data source adapter — enterprise-grade China financial data.

Supports three client modes:
  - fast:  fast HTTP mirror, optimized for ordinary high-volume calls
  - super: comprehensive HTTP gateway, reserved for deeper research calls
  - auto:  ordinary Tushare Pro calls use fast first, then super fallback

Legacy official tushare-python remains available as a final fallback when configured.

Key APIs:
- daily: daily OHLCV price data (trade_date, open, high, low, close, vol)
- income: income statement (end_date, total_revenue, n_income_attr_p, etc.)
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

import pandas as pd
import requests
from dotenv import load_dotenv

from src.data.base_source import BaseDataSource
from src.data.models import (
    BalanceSheet,
    CashFlow,
    DailyPrice,
    FinancialMetrics,
    IncomeStatement,
    MarketType,
)
from src.utils.config import get_project_root
from src.utils.logger import get_logger

logger = get_logger(__name__)

load_dotenv(get_project_root() / ".env")

TUSHARE_CLIENT_MODE = os.environ.get("TUSHARE_CLIENT_MODE", "auto").lower()
TUSHARE_FAST_API_URL = os.environ.get(
    "TUSHARE_FAST_API_URL",
    "https://fastapic.stockai888.top",
)
TUSHARE_FAST_TOKEN = (
    os.environ.get("TUSHARE_FAST_TOKEN")
    or os.environ.get("TUSHARE_TOKEN")
    or ""
).strip()
TUSHARE_SUPER_API_URL = os.environ.get(
    "TUSHARE_SUPER_API_URL",
    "https://ai-tool.indevs.in/tushare/pro",
)
TUSHARE_SUPER_API_KEY = os.environ.get("TUSHARE_SUPER_API_KEY", "").strip()
TUSHARE_TIMEOUT = int(os.environ.get("TUSHARE_TIMEOUT", "30"))
TUSHARE_DISABLE_PROXY = os.environ.get("TUSHARE_DISABLE_PROXY", "false").lower() in (
    "1",
    "true",
    "yes",
)

# Legacy official tushare-python settings.
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "").strip()
TUSHARE_API_URL = os.environ.get("TUSHARE_API_URL", "").strip()


class TushareSource(BaseDataSource):
    """Tushare Pro data source (A-share only, premium quality).

    Note: Tushare is an optional dependency. If not installed, all methods
    return empty results gracefully.
    """

    source_name = "tushare"

    def __init__(self):
        self._api = None
        self._available = None  # Cached availability check
        self._session = requests.Session()
        self._session.trust_env = not TUSHARE_DISABLE_PROXY
        if TUSHARE_DISABLE_PROXY:
            self._session.proxies = {"http": "", "https": ""}

    def _is_available(self) -> bool:
        """Check if any Tushare client path is configured."""
        if self._available is None:
            has_fast = bool(TUSHARE_FAST_TOKEN)
            has_super = bool(TUSHARE_SUPER_API_KEY)
            has_official = bool(TUSHARE_TOKEN) and self._official_package_available()
            self._available = has_fast or has_super or has_official
            if not self._available:
                logger.debug(
                    "[Tushare] No configured client. Set TUSHARE_FAST_TOKEN, "
                    "TUSHARE_SUPER_API_KEY, or legacy TUSHARE_TOKEN."
                )
        return self._available

    def _official_package_available(self) -> bool:
        try:
            import tushare  # noqa: F401
            return True
        except ImportError:
            return False

    def _get_official_api(self):
        """Lazy init legacy tushare-python connection."""
        if self._api is None:
            try:
                import tushare as ts
                ts.set_token(TUSHARE_TOKEN)
                self._api = ts.pro_api()

                if TUSHARE_API_URL:
                    self._api._DataApi__token = TUSHARE_TOKEN
                    self._api._DataApi__http_url = TUSHARE_API_URL
                    logger.info("[Tushare] Using legacy custom endpoint: %s", TUSHARE_API_URL)
            except Exception as e:
                logger.error("[Tushare] Failed to initialize official API: %s", e)
                return None
        return self._api

    def _provider_order(self, api_name: str) -> list[str]:
        mode = TUSHARE_CLIENT_MODE
        if mode in {"fast", "super", "official"}:
            return [mode]
        if mode != "auto":
            logger.warning("[Tushare] Unknown TUSHARE_CLIENT_MODE=%s, using auto", mode)

        # Fast mirror is preferred for ordinary high-volume Tushare Pro calls.
        # Super remains a fallback and can still be forced with TUSHARE_CLIENT_MODE=super.
        if api_name in {
            "daily",
            "stock_basic",
            "income",
            "balancesheet",
            "cashflow",
            "fina_indicator",
        }:
            return ["fast", "super", "official"]
        return ["super", "fast", "official"]

    def _financial_date_params(self, ticker: str, limit: int) -> dict:
        """Build a bounded announcement-date window for Tushare financial APIs."""
        today = date.today()
        years_back = max(limit + 3, 6)
        start_year = today.year - years_back
        return {
            "ts_code": ticker,
            "start_date": f"{start_year}0101",
            "end_date": today.strftime("%Y%m%d"),
        }

    def _statement_params(self, ticker: str, limit: int) -> dict:
        params = self._financial_date_params(ticker, limit)
        # report_type=1 is consolidated statements, the right default for equity valuation.
        params["report_type"] = "1"
        return params

    @staticmethod
    def _safe_float(value) -> float | None:
        if value is None or value == "":
            return None
        try:
            if pd.isna(value):
                return None
        except TypeError:
            pass
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _first_float(self, row, *fields: str) -> float | None:
        for field in fields:
            if field in row.index:
                value = self._safe_float(row.get(field))
                if value is not None:
                    return value
        return None

    @staticmethod
    def _filter_period(df: pd.DataFrame, period_type: str) -> pd.DataFrame:
        if "end_date" not in df.columns:
            return df
        end_dates = df["end_date"].astype(str)
        if period_type == "annual":
            return df[end_dates.str.endswith("1231")]
        if period_type == "quarterly":
            return df[~end_dates.str.endswith("1231")]
        return df

    def _post_http(
        self,
        provider: str,
        api_name: str,
        params: dict,
        fields: list[str] | None,
    ) -> pd.DataFrame:
        if provider == "fast":
            if not TUSHARE_FAST_TOKEN:
                raise RuntimeError("TUSHARE_FAST_TOKEN is not set")
            url = TUSHARE_FAST_API_URL
            payload = {
                "api_name": api_name,
                "token": TUSHARE_FAST_TOKEN,
                "params": params,
            }
            headers = {}
        elif provider == "super":
            if not TUSHARE_SUPER_API_KEY:
                raise RuntimeError("TUSHARE_SUPER_API_KEY is not set")
            url = TUSHARE_SUPER_API_URL
            payload = {
                "api_name": api_name,
                "params": params,
            }
            headers = {"X-API-Key": TUSHARE_SUPER_API_KEY}
        else:
            raise RuntimeError(f"Unsupported HTTP provider: {provider}")

        if fields:
            payload["fields"] = ",".join(fields)

        response = self._session.post(
            url,
            json=payload,
            headers=headers,
            timeout=TUSHARE_TIMEOUT,
        )
        response.raise_for_status()
        return self._response_to_dataframe(response.json())

    def _post_official(
        self,
        api_name: str,
        params: dict,
        fields: list[str] | None,
    ) -> pd.DataFrame:
        if not TUSHARE_TOKEN:
            raise RuntimeError("TUSHARE_TOKEN is not set")
        api = self._get_official_api()
        if api is None:
            raise RuntimeError("Official tushare API unavailable")
        fn = getattr(api, api_name)
        kwargs = dict(params)
        if fields:
            kwargs["fields"] = ",".join(fields)
        df = fn(**kwargs)
        return df if df is not None else pd.DataFrame()

    def _query(
        self,
        api_name: str,
        params: dict,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        last_error: Exception | None = None

        for provider in self._provider_order(api_name):
            try:
                if provider in {"fast", "super"}:
                    df = self._post_http(provider, api_name, params, fields)
                else:
                    df = self._post_official(api_name, params, fields)

                if df is not None and not df.empty:
                    logger.debug(
                        "[Tushare] %s via %s: %d rows",
                        api_name,
                        provider,
                        len(df),
                    )
                    return df
                logger.debug("[Tushare] %s via %s returned empty", api_name, provider)
            except Exception as e:
                last_error = e
                logger.warning("[Tushare] %s via %s failed: %s", api_name, provider, e)

        if last_error:
            raise last_error
        return pd.DataFrame()

    def _response_to_dataframe(self, payload) -> pd.DataFrame:
        """Normalize common Tushare proxy response shapes into a DataFrame."""
        if payload is None:
            return pd.DataFrame()

        if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0"):
            raise RuntimeError(payload.get("msg") or payload.get("message") or payload)

        data = payload.get("data") if isinstance(payload, dict) else payload

        if isinstance(data, dict):
            fields = data.get("fields") or data.get("columns")
            items = data.get("items") or data.get("rows") or data.get("data")
            if fields and items is not None:
                return pd.DataFrame(items, columns=fields)
            if "items" in data and isinstance(data["items"], list):
                return pd.DataFrame(data["items"])
            return pd.DataFrame([data])

        if isinstance(data, list):
            return pd.DataFrame(data)

        return pd.DataFrame()

    def supports_market(self, market: MarketType) -> bool:
        """Only supports A-share market."""
        return market == "a_share" and self._is_available()

    def health_check(self) -> bool:
        """Test API connectivity with a lightweight call."""
        if not self._is_available():
            return False
        try:
            df = self._query(
                "daily",
                {
                    "ts_code": "000001.SZ",
                    "start_date": "20240101",
                    "end_date": "20240102",
                },
                ["ts_code", "trade_date", "close"],
            )
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

        if not self._is_available():
            return []
        ts_code = ticker  # e.g. "601808.SH" (already in Tushare format)
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")
        results: list[DailyPrice] = []

        try:
            df = self._query(
                "daily",
                {
                    "ts_code": ts_code,
                    "start_date": start_str,
                    "end_date": end_str,
                },
                ["ts_code", "trade_date", "open", "high", "low", "close", "vol"],
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

        if not self._is_available():
            return []
        ts_code = ticker
        results: list[IncomeStatement] = []

        try:
            # Tushare income API values are in CNY (元).
            # n_income_attr_p is parent-company net profit, the equity valuation default.
            fields = [
                "ts_code",
                "ann_date",
                "f_ann_date",
                "end_date",
                "report_type",
                "comp_type",
                "total_revenue",
                "revenue",
                "oper_cost",
                "operate_profit",
                "n_income_attr_p",
                "n_income",
                "basic_eps",
                "diluted_eps",
                "total_share",
            ]
            df = self._query("income", self._statement_params(ts_code, limit), fields)

            if df is None or df.empty:
                return []

            # Sort by end_date descending
            df = df.sort_values("end_date", ascending=False)

            df = self._filter_period(df, period_type)

            df = df.iloc[:limit]

            for _, row in df.iterrows():
                end_date_str = str(row["end_date"])
                period_end = datetime.strptime(end_date_str, "%Y%m%d").date()

                revenue = self._first_float(row, "total_revenue", "revenue")
                cost = self._safe_float(row.get("oper_cost"))
                gross = (revenue - cost) if (revenue and cost) else None

                # Extract shares outstanding when available.
                # Tushare's total_share is in 万股 (10k shares), need to convert to shares
                total_share = None
                total_share_raw = self._safe_float(row.get("total_share"))
                if total_share_raw is not None:
                    total_share = total_share_raw * 10000  # 万股 → 股

                results.append(IncomeStatement(
                    ticker=ticker,
                    period_end_date=period_end,
                    period_type=period_type,
                    revenue=revenue,
                    cost_of_revenue=cost,
                    gross_profit=gross,
                    operating_income=self._safe_float(row.get("operate_profit")),
                    net_income=self._first_float(row, "n_income_attr_p", "n_income"),
                    eps=self._safe_float(row.get("basic_eps")),
                    eps_diluted=self._safe_float(row.get("diluted_eps")),
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

        if not self._is_available():
            return []
        ts_code = ticker
        results: list[BalanceSheet] = []

        try:
            # Request V3 fields: inventories, adv_receipts, fix_assets, comp_type
            # Also request bank/insurance indicator fields for validation
            fields = [
                "ts_code", "ann_date", "f_ann_date", "end_date", "report_type", "comp_type",
                "total_assets", "total_liab", "total_hldr_eqy_exc_min_int",
                "total_cur_assets", "total_cur_liab", "money_cap", "total_share",
                "st_borr", "lt_borr",
                # V3 industry detection fields
                "inventories", "adv_receipts", "fix_assets",
                # Bank indicator fields
                "decr_in_disbur", "cb_borr", "depos_ib_deposits",
                # Insurance indicator fields
                "rsrv_insur_cont", "reser_une_prem", "reser_lins_liab",
            ]
            df = self._query("balancesheet", self._statement_params(ts_code, limit), fields)

            if df is None or df.empty:
                return []

            df = df.sort_values("end_date", ascending=False)

            df = self._filter_period(df, period_type)

            df = df.iloc[:limit]

            for _, row in df.iterrows():
                end_date_str = str(row["end_date"])
                period_end = datetime.strptime(end_date_str, "%Y%m%d").date()

                st_debt = self._safe_float(row.get("st_borr")) or 0
                lt_debt = self._safe_float(row.get("lt_borr")) or 0
                total_debt = (st_debt + lt_debt) if (st_debt or lt_debt) else None

                # V3 Industry Detection via comp_type
                # comp_type: 1=工商业, 2=银行, 3=保险, 4=证券
                comp_type = int(self._safe_float(row.get("comp_type")) or 1)
                has_loan_loss_provision = comp_type == 2  # Bank
                has_insurance_reserve = comp_type == 3    # Insurance

                # Additional validation: check if bank/insurance fields have values
                if not has_loan_loss_provision:
                    # Double-check with bank-specific fields
                    bank_fields = ["decr_in_disbur", "cb_borr", "depos_ib_deposits"]
                    bank_hits = sum(
                        1 for f in bank_fields if (self._safe_float(row.get(f)) or 0) > 0
                    )
                    if bank_hits >= 2:
                        has_loan_loss_provision = True
                        logger.debug(
                            "[Tushare] %s detected as bank via balance sheet fields",
                            ticker,
                        )

                if not has_insurance_reserve:
                    # Double-check with insurance-specific fields
                    ins_fields = ["rsrv_insur_cont", "reser_une_prem", "reser_lins_liab"]
                    ins_hits = sum(
                        1 for f in ins_fields if (self._safe_float(row.get(f)) or 0) > 0
                    )
                    if ins_hits >= 2:
                        has_insurance_reserve = True
                        logger.debug(
                            "[Tushare] %s detected as insurance via balance sheet fields",
                            ticker,
                        )

                results.append(BalanceSheet(
                    ticker=ticker,
                    period_end_date=period_end,
                    period_type=period_type,
                    total_assets=self._safe_float(row.get("total_assets")),
                    total_liabilities=self._safe_float(row.get("total_liab")),
                    total_equity=(
                        self._safe_float(row.get("total_hldr_eqy_exc_min_int"))
                    ),
                    current_assets=self._safe_float(row.get("total_cur_assets")),
                    current_liabilities=self._safe_float(row.get("total_cur_liab")),
                    cash_and_equivalents=self._safe_float(row.get("money_cap")),
                    total_debt=total_debt,
                    # V3 fields
                    inventory=self._safe_float(row.get("inventories")),
                    advance_receipts=self._safe_float(row.get("adv_receipts")),
                    fixed_assets=self._safe_float(row.get("fix_assets")),
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

        if not self._is_available():
            return []
        ts_code = ticker
        results: list[CashFlow] = []

        try:
            fields = [
                "ts_code",
                "ann_date",
                "f_ann_date",
                "end_date",
                "report_type",
                "n_cashflow_act",
                "n_cashflow_inv_act",
                "c_pay_acq_const_fiolta",
                "depr_fa_coga_dpba",
            ]
            df = self._query("cashflow", self._statement_params(ts_code, limit), fields)

            if df is None or df.empty:
                return []

            df = df.sort_values("end_date", ascending=False)

            df = self._filter_period(df, period_type)

            df = df.iloc[:limit]

            for _, row in df.iterrows():
                end_date_str = str(row["end_date"])
                period_end = datetime.strptime(end_date_str, "%Y%m%d").date()

                op_cf = self._safe_float(row.get("n_cashflow_act"))
                inv_cf = self._safe_float(row.get("n_cashflow_inv_act"))
                capex = self._safe_float(row.get("c_pay_acq_const_fiolta"))
                depreciation = self._safe_float(row.get("depr_fa_coga_dpba"))

                # Prefer classic FCF = operating CF - capital expenditure.
                # Fall back to OCF + investing CF when capex is unavailable.
                fcf = None
                if op_cf is not None and capex is not None:
                    fcf = op_cf - capex
                elif op_cf is not None and inv_cf is not None:
                    fcf = op_cf + inv_cf
                elif op_cf is not None:
                    fcf = op_cf

                results.append(CashFlow(
                    ticker=ticker,
                    period_end_date=period_end,
                    period_type=period_type,
                    operating_cash_flow=op_cf,
                    capital_expenditure=-capex if capex is not None else None,
                    free_cash_flow=fcf,
                    depreciation=depreciation,
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

        if not self._is_available():
            return []
        ts_code = ticker
        results: list[FinancialMetrics] = []

        try:
            fields = [
                'ts_code', 'ann_date', 'end_date',
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
            ]
            df = self._query("fina_indicator", self._financial_date_params(ts_code, limit), fields)

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
