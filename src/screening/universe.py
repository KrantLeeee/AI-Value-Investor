"""Stock universe loaders for valuation scans."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Literal

from src.data import database
from src.utils.config import load_watchlist
from src.utils.logger import get_logger

logger = get_logger(__name__)

UniverseSource = Literal["watchlist", "a_share", "hk", "all"]


@dataclass(frozen=True)
class UniverseItem:
    ticker: str
    market: str
    name: str = ""
    sector: str = ""
    board: str = ""
    exchange: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _a_share_suffix(code: str) -> str:
    clean = str(code).strip().upper().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    if clean.startswith(("6", "9")):
        return f"{clean}.SH"
    if clean.startswith(("0", "2", "3")):
        return f"{clean}.SZ"
    if clean.startswith(("4", "8")):
        return f"{clean}.BJ"
    return clean


def _hk_suffix(code: str) -> str:
    clean = str(code).strip().upper().replace(".HK", "")
    if clean.isdigit():
        clean = clean.zfill(4)
    return f"{clean}.HK"


def _is_excluded_name(name: str) -> bool:
    upper = (name or "").upper()
    return any(token in upper for token in ["ST", "*ST", "退"])


def _skip_akshare() -> bool:
    return os.getenv("SKIP_AKSHARE", "true").lower() in {"true", "1", "yes"}


def _a_share_universe_from_tushare(
    *,
    limit: int | None = None,
    exclude_risk: bool = True,
) -> list[UniverseItem]:
    try:
        from src.data.tushare_source import TushareSource

        source = TushareSource()
        df = source._query(
            "stock_basic",
            {"exchange": "", "list_status": "L"},
            [
                "ts_code",
                "symbol",
                "name",
                "area",
                "industry",
                "market",
                "exchange",
                "list_status",
                "list_date",
                "delist_date",
                "is_hs",
            ],
        )
    except Exception as e:
        logger.warning("[Universe] Failed to fetch A-share universe from Tushare: %s", e)
        return []

    rows: list[UniverseItem] = []
    db_rows: list[dict] = []
    for _, row in df.iterrows():
        ticker = row.get("ts_code") or row.get("代码")
        name = row.get("name") or row.get("名称") or ""
        sector = row.get("industry") or row.get("行业") or ""
        if not ticker:
            continue
        if exclude_risk and _is_excluded_name(str(name)):
            continue
        item = {
            "ticker": str(ticker).upper(),
            "symbol": str(row.get("symbol") or "").strip(),
            "name": str(name),
            "market": "a_share",
            "exchange": str(row.get("exchange") or ""),
            "board": str(row.get("market") or ""),
            "sector": str(sector) if sector else "",
            "area": str(row.get("area") or ""),
            "list_status": str(row.get("list_status") or "L"),
            "list_date": str(row.get("list_date") or ""),
            "delist_date": str(row.get("delist_date") or ""),
            "is_hs": str(row.get("is_hs") or ""),
            "source": "tushare_stock_basic",
        }
        db_rows.append(item)
        rows.append(
            UniverseItem(
                ticker=item["ticker"],
                market="a_share",
                name=item["name"],
                sector=item["sector"],
                board=item["board"],
                exchange=item["exchange"],
            )
        )
        if limit and len(rows) >= limit:
            continue
    database.upsert_stock_universe(db_rows)
    if limit:
        return rows[:limit]
    return rows


def refresh_a_share_universe_from_tushare() -> int:
    """Refresh and persist the full active A-share master list from Tushare."""
    rows = _a_share_universe_from_tushare(limit=None, exclude_risk=False)
    return len(rows)


def _stock_universe_cache_is_fresh(max_age_days: int = 7) -> bool:
    stats = database.get_stock_universe_stats()
    if not stats.get("total") or not stats.get("updated_at"):
        return False
    try:
        updated = datetime.fromisoformat(str(stats["updated_at"]))
    except ValueError:
        return False
    return datetime.now() - updated < timedelta(days=max_age_days)


def _a_share_universe_from_cache(
    *,
    limit: int | None = None,
    exclude_risk: bool = True,
    board: str | None = None,
    sector: str | None = None,
) -> list[UniverseItem]:
    rows = database.get_stock_universe(
        market="a_share",
        board=board or None,
        sector=sector or None,
        limit=limit,
        active_only=True,
    )
    result: list[UniverseItem] = []
    for row in rows:
        if exclude_risk and _is_excluded_name(str(row.get("name") or "")):
            continue
        result.append(
            UniverseItem(
                ticker=str(row.get("ticker")),
                market="a_share",
                name=str(row.get("name") or ""),
                sector=str(row.get("sector") or ""),
                board=str(row.get("board") or ""),
                exchange=str(row.get("exchange") or ""),
            )
        )
        if limit and len(result) >= limit:
            return result
    return result


def watchlist_universe(
    *,
    markets: set[str] | None = None,
    limit: int | None = None,
) -> list[UniverseItem]:
    data = load_watchlist()
    rows: list[UniverseItem] = []
    for market, items in data.get("watchlist", {}).items():
        if markets and market not in markets:
            continue
        for item in items:
            if isinstance(item, dict):
                ticker = item.get("ticker")
                name = item.get("name", "")
                sector = item.get("sector") or item.get("industry") or ""
            else:
                ticker = str(item)
                name = ""
                sector = ""
            if ticker:
                rows.append(UniverseItem(ticker=ticker, market=market, name=name, sector=sector))
            if limit and len(rows) >= limit:
                return rows
    return rows


def a_share_universe(
    *,
    limit: int | None = None,
    exclude_risk: bool = True,
    board: str | None = None,
    sector: str | None = None,
) -> list[UniverseItem]:
    cached_rows = _a_share_universe_from_cache(
        limit=limit,
        exclude_risk=exclude_risk,
        board=board,
        sector=sector,
    )
    if cached_rows and _stock_universe_cache_is_fresh():
        return cached_rows

    tushare_rows = _a_share_universe_from_tushare(limit=None, exclude_risk=False)
    if tushare_rows:
        return _a_share_universe_from_cache(
            limit=limit,
            exclude_risk=exclude_risk,
            board=board,
            sector=sector,
        )

    if _skip_akshare():
        logger.warning("[Universe] Using stale/partial local A-share universe")
        return cached_rows

    try:
        import akshare as ak

        df = ak.stock_info_a_code_name()
    except Exception as e:
        logger.warning("[Universe] Failed to fetch A-share universe from AKShare: %s", e)
        return []

    rows: list[UniverseItem] = []
    for _, row in df.iterrows():
        code = row.get("code") or row.get("证券代码") or row.get("代码")
        name = row.get("name") or row.get("证券简称") or row.get("名称") or ""
        if not code:
            continue
        if exclude_risk and _is_excluded_name(str(name)):
            continue
        rows.append(
            UniverseItem(
                ticker=_a_share_suffix(str(code)),
                market="a_share",
                name=str(name),
            )
        )
        if limit and len(rows) >= limit:
            return rows
    return rows


def hk_universe(*, limit: int | None = None, exclude_risk: bool = True) -> list[UniverseItem]:
    if _skip_akshare():
        logger.warning(
            "[Universe] Full HK universe currently requires AKShare. "
            "Use watchlist HK names or set SKIP_AKSHARE=false to enable it."
        )
        return []

    try:
        import akshare as ak

        df = ak.stock_hk_spot_em()
    except Exception as e:
        logger.warning("[Universe] Failed to fetch HK universe from AKShare: %s", e)
        return []

    rows: list[UniverseItem] = []
    for _, row in df.iterrows():
        code = row.get("代码") or row.get("code") or row.get("symbol")
        name = row.get("名称") or row.get("name") or ""
        if not code:
            continue
        if exclude_risk and _is_excluded_name(str(name)):
            continue
        rows.append(
            UniverseItem(
                ticker=_hk_suffix(str(code)),
                market="hk",
                name=str(name),
            )
        )
        if limit and len(rows) >= limit:
            return rows
    return rows


def load_universe(
    source: UniverseSource = "watchlist",
    *,
    markets: set[str] | None = None,
    limit: int | None = None,
    exclude_risk: bool = True,
    board: str | None = None,
    sector: str | None = None,
) -> list[dict]:
    if source == "watchlist":
        items = watchlist_universe(markets=markets, limit=limit)
    elif source == "a_share":
        items = a_share_universe(
            limit=limit,
            exclude_risk=exclude_risk,
            board=board,
            sector=sector,
        )
    elif source == "hk":
        items = hk_universe(limit=limit, exclude_risk=exclude_risk)
    elif source == "all":
        a_limit = limit
        hk_limit = None
        if limit:
            a_limit = max(1, limit // 2)
            hk_limit = max(1, limit - a_limit)
        items = [
            *a_share_universe(
                limit=a_limit,
                exclude_risk=exclude_risk,
                board=board,
                sector=sector,
            ),
            *hk_universe(limit=hk_limit, exclude_risk=exclude_risk),
        ]
        if limit:
            items = items[:limit]
    else:
        raise ValueError(f"Unknown universe source: {source}")

    if markets:
        items = [item for item in items if item.market in markets]
    return [item.to_dict() for item in items]
