"""Sina Finance realtime price source — free, fast realtime quotes.

Sina Finance API (free, no auth required):
- URL pattern: http://hq.sinajs.cn/list={symbol}
- Returns comma-separated values string
- A-share symbols: "sh601808", "sz000002" (lowercase prefix)
- Fast response, good for realtime price checks
- Does NOT provide financial statements (raises NotImplementedError)

Response format (A-share):
var hq_str_sh601808="中国海油,22.50,22.48,22.45,22.52,22.40,22.44,22.45,..."
Fields: [0]name, [1]open, [2]prev_close, [3]current, [4]high, [5]low, [6]bid, [7]ask, [8]volume, ...
"""

import re
from datetime import date, datetime

import requests

from src.data.base_source import BaseDataSource
from src.data.models import (
    BalanceSheet,
    CashFlow,
    DailyPrice,
    IncomeStatement,
    MarketType,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

SINA_API_URL = "http://hq.sinajs.cn/list="


class SinaRealtimeSource(BaseDataSource):
    """Sina Finance realtime price source (prices only, no financials)."""

    source_name = "sina_realtime"

    def supports_market(self, market: MarketType) -> bool:
        """Supports A-share and HK markets."""
        return market in ("a_share", "hk")

    def health_check(self) -> bool:
        """Test API connectivity with SSE index query."""
        try:
            # Test with Shanghai Composite Index (000001)
            response = requests.get(f"{SINA_API_URL}s_sh000001", timeout=5)
            return response.status_code == 200 and len(response.text) > 50
        except Exception as e:
            logger.warning("[Sina] health_check failed: %s", e)
            return False

    def _convert_ticker(self, ticker: str, market: MarketType) -> str:
        """
        Convert standard ticker to Sina format.

        A-share examples:
        - "601808.SH" → "sh601808"
        - "000002.SZ" → "sz000002"

        HK examples:
        - "0883.HK" → "hk00883"
        """
        if market == "a_share":
            parts = ticker.upper().split(".")
            if len(parts) == 2:
                code, exchange = parts
                prefix = exchange.lower()  # "sh" or "sz"
                return f"{prefix}{code}"
            return ticker
        elif market == "hk":
            code = ticker.upper().replace(".HK", "")
            # HK codes are 5 digits, zero-padded
            return f"hk{code.zfill(5)}"
        return ticker

    def _parse_response(self, text: str, ticker: str, market: MarketType) -> DailyPrice | None:
        """
        Parse Sina API response to extract price data.

        Response format:
        var hq_str_sh601808="中国海油,22.50,22.48,22.45,22.52,22.40,22.44,22.45,12345678,..."

        Fields (A-share):
        [0] name, [1] open, [2] prev_close, [3] current, [4] high, [5] low,
        [6] bid, [7] ask, [8] volume, [9] amount, [30] date, [31] time
        """
        # Extract the quoted string content
        match = re.search(r'="([^"]+)"', text)
        if not match:
            logger.warning("[Sina] Failed to parse response for %s", ticker)
            return None

        data_str = match.group(1)
        fields = data_str.split(",")

        if len(fields) < 9:
            logger.warning("[Sina] Insufficient fields in response for %s", ticker)
            return None

        try:
            # Parse fields
            open_price = float(fields[1]) if fields[1] else None
            high_price = float(fields[4]) if fields[4] else None
            low_price = float(fields[5]) if fields[5] else None
            close_price = float(fields[3]) if fields[3] else None
            volume = int(float(fields[8])) if fields[8] else 0

            # Parse date (field 30 if available, otherwise use today)
            trade_date = date.today()
            if len(fields) > 30 and fields[30]:
                try:
                    trade_date = datetime.strptime(fields[30], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    pass

            if close_price is None or close_price == 0:
                logger.warning("[Sina] Invalid price data for %s", ticker)
                return None

            return DailyPrice(
                ticker=ticker,
                market=market,
                date=trade_date,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume,
                source=self.source_name,
            )
        except (ValueError, IndexError) as e:
            logger.warning("[Sina] Failed to parse price fields for %s: %s", ticker, e)
            return None

    def get_daily_prices(
        self, ticker: str, market: MarketType,
        start_date: date, end_date: date,
    ) -> list[DailyPrice]:
        """
        Fetch realtime/latest price data from Sina.

        Note: Sina only provides current day data, not historical ranges.
        This method returns single-day data (latest available).
        """
        if market not in ("a_share", "hk"):
            return []

        sina_symbol = self._convert_ticker(ticker, market)
        results: list[DailyPrice] = []

        try:
            response = requests.get(f"{SINA_API_URL}{sina_symbol}", timeout=10)
            response.raise_for_status()

            price = self._parse_response(response.text, ticker, market)
            if price:
                results.append(price)
                logger.info("[Sina] %s: fetched realtime price ¥%.2f", ticker, price.close)
            else:
                logger.warning("[Sina] No valid price data for %s", ticker)

        except Exception as e:
            logger.warning("[Sina] get_daily_prices failed for %s: %s", ticker, e)

        return results

    def get_income_statements(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[IncomeStatement]:
        """Sina does not provide financial statements."""
        raise NotImplementedError("[Sina] Financial statements not available from realtime price source")

    def get_balance_sheets(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[BalanceSheet]:
        """Sina does not provide financial statements."""
        raise NotImplementedError("[Sina] Financial statements not available from realtime price source")

    def get_cash_flows(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[CashFlow]:
        """Sina does not provide financial statements."""
        raise NotImplementedError("[Sina] Financial statements not available from realtime price source")
