import pandas as pd

from src.screening import universe
from src.screening.universe import _a_share_suffix, _hk_suffix, _is_excluded_name


def test_a_share_suffix_mapping():
    assert _a_share_suffix("600519") == "600519.SH"
    assert _a_share_suffix("000001") == "000001.SZ"
    assert _a_share_suffix("300750") == "300750.SZ"
    assert _a_share_suffix("830799") == "830799.BJ"


def test_hk_suffix_mapping():
    assert _hk_suffix("700") == "0700.HK"
    assert _hk_suffix("0883.HK") == "0883.HK"


def test_excluded_name_detection():
    assert _is_excluded_name("*ST未来")
    assert _is_excluded_name("退市整理")
    assert not _is_excluded_name("贵州茅台")


def test_a_share_universe_prefers_tushare(monkeypatch):
    saved_rows = []

    class FakeTushare:
        def _query(self, api_name, params, fields=None):
            assert api_name == "stock_basic"
            return pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "name": "平安银行",
                        "industry": "银行",
                    }
                ]
            )

    monkeypatch.setattr(universe, "_skip_akshare", lambda: True)
    monkeypatch.setattr(universe, "_stock_universe_cache_is_fresh", lambda: False)
    def fake_cache(**kwargs):
        return [
            universe.UniverseItem(
                ticker=row["ticker"],
                market=row["market"],
                name=row["name"],
                sector=row["sector"],
            )
            for row in saved_rows
        ]

    monkeypatch.setattr(universe, "_a_share_universe_from_cache", fake_cache)
    monkeypatch.setattr(
        universe.database,
        "upsert_stock_universe",
        lambda rows: saved_rows.extend(rows),
    )
    monkeypatch.setattr("src.data.tushare_source.TushareSource", FakeTushare)

    rows = universe.a_share_universe(limit=1)

    assert rows[0].ticker == "000001.SZ"
    assert rows[0].sector == "银行"
    assert saved_rows[0]["source"] == "tushare_stock_basic"


def test_hk_universe_skips_akshare_by_default(monkeypatch):
    monkeypatch.setattr(universe, "_skip_akshare", lambda: True)

    assert universe.hk_universe(limit=1) == []
