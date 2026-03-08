"""BaoStock data source adapter — supplementary A-share quarterly financial data.

Source reference: baostock package evaluation/season_index.py + demo files
Key API facts:
  - All quarterly APIs: query_profit_data / query_balance_data /
                        query_cash_flow_data / query_dupont_data
  - Parameters: code="sh.601808", year=2024, quarter=4 (int, 1-4)
  - year/quarter=None → auto-defaults to current year/quarter (VALID call)
  - Field names returned dynamically via rs.fields (DO NOT hardcode)
  - Confirmed field sets (from BaoStock documentation):
      query_profit_data fields:  code, pubDate, statDate, roeAvg, npMargin,
        gpMargin, netProfit, epsTTM, MBRevenue, totalShare, liqaShare
      query_balance_data fields: code, pubDate, statDate, currentRatio,
        quickRatio, cashRatio, YOYLiability, liabilityToAsset, assetToEquity
      query_cash_flow_data fields: code, pubDate, statDate, CAToAsset,
        NCAToAsset, tangibleAsset, hasDebt, workingCapital, CFOToOR,
        CFOToNP, CFOToGr
      query_dupont_data fields: code, pubDate, statDate, dupontROE,
        dupontAssetStoEquity, dupontAssetTurn, dupontPnitoni,
        dupontNitoni, dupontRoe, dupontAssets
  - BaoStock provides RATIO data, not raw financial statement amounts
  - For actual amounts (revenue, total assets, etc.) use AKShare THS instead
"""

from datetime import date

import pandas as pd

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

_bs_logged_in = False


def _ensure_login():
    global _bs_logged_in
    if not _bs_logged_in:
        import baostock as bs
        result = bs.login()
        if result.error_code == "0":
            _bs_logged_in = True
        else:
            raise RuntimeError(f"BaoStock login failed: {result.error_msg}")


def _to_bs_code(ticker: str) -> str:
    """601808.SH → sh.601808 | 002415.SZ → sz.002415"""
    parts = ticker.upper().split(".")
    if len(parts) != 2:
        raise ValueError(f"Cannot convert ticker to BaoStock format: {ticker!r}")
    code, exchange = parts
    return f"{exchange.lower()}.{code}"


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        s = str(val).strip()
        if s in ("", "None", "nan", "--"):
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def _recent_year_quarters(n: int = 16) -> list[tuple[int, int]]:
    """
    Return the n most recent (year, quarter) tuples in descending order.
    We always pass explicit year+quarter to BaoStock — never 0.
    """
    today = date.today()
    current_q = (today.month - 1) // 3 + 1
    year = today.year
    quarter = current_q

    pairs: list[tuple[int, int]] = []
    for _ in range(n):
        pairs.append((year, quarter))
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1
    return pairs


def _query_bs_quarters(
    query_fn,
    bs_code: str,
    limit: int,
) -> list[dict]:
    """
    Generic helper: query a BaoStock seasonal API over recent year/quarters.
    Returns a list of row dicts (field→value), newest first, up to `limit`.
    Skips quarters that return no data or errors silently.
    """
    import baostock as bs
    _ensure_login()
    seen_dates: set[str] = set()
    collected: list[dict] = []

    for year, quarter in _recent_year_quarters(n=limit * 3):
        if len(collected) >= limit:
            break
        try:
            rs = query_fn(bs_code, year=year, quarter=quarter)
            if rs.error_code != "0":
                continue
            rows_in_quarter = []
            while rs.next():
                row = dict(zip(rs.fields, rs.get_row_data()))
                date_key = row.get("pubDate") or row.get("statDate", "")
                if date_key and date_key not in seen_dates and any(
                    v for v in row.values() if v and v not in ("", "--")
                ):
                    seen_dates.add(date_key)
                    rows_in_quarter.append(row)
            collected.extend(rows_in_quarter)
        except Exception as exc:
            logger.debug("[BaoStock] query_bs_quarters error at %d-Q%d: %s", year, quarter, exc)
            continue

    return collected[:limit]


class BaoStockSource(BaseDataSource):
    source_name = "baostock"

    def supports_market(self, market: MarketType) -> bool:
        return market == "a_share"

    def health_check(self) -> bool:
        try:
            _ensure_login()
            return True
        except Exception as e:
            logger.warning("[BaoStock] health_check failed: %s", e)
            return False

    # ── Daily Prices ──────────────────────────────────────────────────────────

    def get_daily_prices(
        self, ticker: str, market: MarketType,
        start_date: date, end_date: date,
    ) -> list[DailyPrice]:
        """
        BaoStock price API: query_history_k_data_plus
        adjustflag: "1"=后复权, "2"=前复权, "3"=不复权
        Fields requested: date, open, high, low, close, volume, amount, adjustflag
        """
        if market != "a_share":
            return []
        import baostock as bs

        _ensure_login()
        bs_code = _to_bs_code(ticker)
        results: list[DailyPrice] = []

        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,volume,amount",
                start_date=str(start_date),
                end_date=str(end_date),
                frequency="d",
                adjustflag="2",  # 前复权 (forward-adjusted)
            )
            while rs.error_code == "0" and rs.next():
                row = dict(zip(rs.fields, rs.get_row_data()))
                close = _safe_float(row.get("close"))
                if close is None:
                    continue
                results.append(DailyPrice(
                    ticker=ticker, market=market,
                    date=date.fromisoformat(row["date"]),
                    open=_safe_float(row.get("open")),
                    high=_safe_float(row.get("high")),
                    low=_safe_float(row.get("low")),
                    close=close,
                    volume=int(float(row["volume"])) if row.get("volume") else None,
                    source=self.source_name,
                ))
            logger.info("[BaoStock] %s: fetched %d price rows", ticker, len(results))
        except Exception as e:
            logger.warning("[BaoStock] get_daily_prices failed for %s: %s", ticker, e)

        return results

    # ── Income Statement ───────────────────────────────────────────────────────

    def get_income_statements(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[IncomeStatement]:
        """
        BaoStock query_profit_data — provides RATIOS not absolute amounts.
        Fields: code, pubDate, statDate, roeAvg, npMargin, gpMargin,
                netProfit, epsTTM, MBRevenue, totalShare, liqaShare
        Note: netProfit and MBRevenue are absolute (yuan), others are ratios.
        """
        if market != "a_share":
            return []
        import baostock as bs

        bs_code = _to_bs_code(ticker)
        results: list[IncomeStatement] = []

        try:
            rows = _query_bs_quarters(bs.query_profit_data, bs_code, limit)
            for row in rows:
                period_str = row.get("statDate") or row.get("pubDate", "")
                if not period_str:
                    continue
                try:
                    p_date = date.fromisoformat(period_str)
                except ValueError:
                    continue

                # Extract shares outstanding (总股本) - unit: shares
                total_share = _safe_float(row.get("totalShare"))

                results.append(IncomeStatement(
                    ticker=ticker,
                    period_end_date=p_date,
                    period_type=period_type,
                    revenue=_safe_float(row.get("MBRevenue")),     # 主营业务收入
                    net_income=_safe_float(row.get("netProfit")),   # 净利润 (yuan)
                    eps=_safe_float(row.get("epsTTM")),             # EPS TTM
                    shares_outstanding=total_share,                 # 总股本 (shares)
                    # Margin fields as fractional ratios (0-1 scale from BaoStock)
                    # These get stored in IncomeStatement if model supports them
                    source=self.source_name,
                ))

            logger.info("[BaoStock] %s income: %d rows", ticker, len(results))
        except Exception as e:
            logger.warning("[BaoStock] get_income_statements failed for %s: %s", ticker, e)

        return results

    # ── Balance Sheet ──────────────────────────────────────────────────────────

    def get_balance_sheets(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[BalanceSheet]:
        """
        BaoStock query_balance_data — solvency ratios ONLY (not abs amounts).
        Fields: code, pubDate, statDate, currentRatio, quickRatio, cashRatio,
                YOYLiability, liabilityToAsset, assetToEquity
        Since we need absolute values, this is a thin stub; prefer AKShare.
        """
        if market != "a_share":
            return []
        import baostock as bs

        bs_code = _to_bs_code(ticker)
        results: list[BalanceSheet] = []

        try:
            rows = _query_bs_quarters(bs.query_balance_data, bs_code, limit)
            for row in rows:
                period_str = row.get("statDate") or row.get("pubDate", "")
                if not period_str:
                    continue
                try:
                    p_date = date.fromisoformat(period_str)
                except ValueError:
                    continue

                # BaoStock only has ratios for balance data, not absolute amounts
                # We store what we can; absolute amounts come from AKShare THS
                results.append(BalanceSheet(
                    ticker=ticker,
                    period_end_date=p_date,
                    period_type=period_type,
                    # No absolute values available from BaoStock balance API
                    source=self.source_name,
                ))

            logger.info("[BaoStock] %s balance: %d rows", ticker, len(results))
        except Exception as e:
            logger.warning("[BaoStock] get_balance_sheets failed for %s: %s", ticker, e)

        return results

    # ── Cash Flow ──────────────────────────────────────────────────────────────

    def get_cash_flows(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[CashFlow]:
        """
        BaoStock query_cash_flow_data — cash flow coverage ratios.
        Fields: code, pubDate, statDate, CAToAsset, NCAToAsset, tangibleAsset,
                hasDebt, workingCapital, CFOToOR, CFOToNP, CFOToGr
        Note: These are ratios (CFO/Revenue, CFO/Net Profit, etc.), not absolute.
        """
        if market != "a_share":
            return []
        import baostock as bs

        bs_code = _to_bs_code(ticker)
        results: list[CashFlow] = []

        try:
            rows = _query_bs_quarters(bs.query_cash_flow_data, bs_code, limit)
            for row in rows:
                period_str = row.get("statDate") or row.get("pubDate", "")
                if not period_str:
                    continue
                try:
                    p_date = date.fromisoformat(period_str)
                except ValueError:
                    continue

                # BaoStock cash flow data is ratios only — stub record for now
                results.append(CashFlow(
                    ticker=ticker,
                    period_end_date=p_date,
                    period_type=period_type,
                    source=self.source_name,
                ))

            logger.info("[BaoStock] %s cashflow: %d rows", ticker, len(results))
        except Exception as e:
            logger.warning("[BaoStock] get_cash_flows failed for %s: %s", ticker, e)

        return results

    # ── Financial Metrics ──────────────────────────────────────────────────────

    def get_financial_metrics(
        self, ticker: str, market: MarketType, limit: int = 10,
    ) -> list[FinancialMetrics]:
        """
        BaoStock query_dupont_data — DuPont decomposition metrics.
        Fields: code, pubDate, statDate,
          dupontROE       → ROE (weighted avg)
          dupontAssetStoEquity → equity multiplier (assets/equity)
          dupontAssetTurn → asset turnover
          dupontPnitoni   → net profit / total income
          dupontNitoni    → net income / total income
          dupontRoe       → ROE (alternative calc)
          dupontAssets    → total assets

        Also enriched with profit ratios from query_profit_data:
          roeAvg, npMargin (net profit margin), gpMargin (gross profit margin)
        """
        if market != "a_share":
            return []
        import baostock as bs

        bs_code = _to_bs_code(ticker)
        results: list[FinancialMetrics] = []

        try:
            dupont_rows = _query_bs_quarters(bs.query_dupont_data, bs_code, limit)
            # Also get profit ratios to enrich metrics
            profit_rows = _query_bs_quarters(bs.query_profit_data, bs_code, limit)
            # Build a lookup by statDate
            profit_by_date: dict[str, dict] = {}
            for pr in profit_rows:
                k = pr.get("statDate") or pr.get("pubDate", "")
                if k:
                    profit_by_date[k] = pr

            for row in dupont_rows:
                period_str = row.get("statDate") or row.get("pubDate", "")
                if not period_str:
                    continue
                try:
                    p_date = date.fromisoformat(period_str)
                except ValueError:
                    continue

                profit = profit_by_date.get(period_str, {})
                roe_val = _safe_float(row.get("dupontROE") or row.get("dupontRoe"))
                # BaoStock ROE is in fraction form (0-1), convert to % for consistency
                if roe_val is not None and abs(roe_val) < 2:  # likely fraction
                    roe_val = roe_val * 100

                np_margin = _safe_float(profit.get("npMargin"))
                if np_margin is not None and abs(np_margin) < 2:
                    np_margin = np_margin * 100

                gp_margin = _safe_float(profit.get("gpMargin"))
                if gp_margin is not None and abs(gp_margin) < 2:
                    gp_margin = gp_margin * 100

                roe_avg = _safe_float(profit.get("roeAvg"))
                if roe_avg is not None and abs(roe_avg) < 2:
                    roe_avg = roe_avg * 100

                results.append(FinancialMetrics(
                    ticker=ticker,
                    date=p_date,
                    roe=roe_val or roe_avg,
                    operating_margin=np_margin,  # net profit margin as proxy
                    gross_margin=gp_margin,
                    source=self.source_name,
                ))

            logger.info("[BaoStock] %s metrics: %d rows", ticker, len(results))
        except Exception as e:
            logger.warning("[BaoStock] get_financial_metrics failed for %s: %s", ticker, e)

        return results
