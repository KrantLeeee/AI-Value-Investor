from datetime import date

from src.data import tushare_source as ts_mod
from src.data.tushare_source import TushareSource


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_tushare_fast_daily_http_payload(monkeypatch):
    calls = []
    monkeypatch.setattr(ts_mod, "TUSHARE_CLIENT_MODE", "fast")
    monkeypatch.setattr(ts_mod, "TUSHARE_FAST_TOKEN", "fast-token")

    source = TushareSource()

    def fake_post(url, json, headers, timeout):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _FakeResponse({
            "code": 0,
            "data": {
                "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "vol"],
                "items": [["000001.SZ", "20260102", 10.0, 11.0, 9.5, 10.8, 12345]],
            },
        })

    source._session.post = fake_post

    prices = source.get_daily_prices(
        "000001.SZ",
        "a_share",
        date(2026, 1, 1),
        date(2026, 1, 10),
    )

    assert len(prices) == 1
    assert prices[0].close == 10.8
    assert calls[0]["url"] == ts_mod.TUSHARE_FAST_API_URL
    assert calls[0]["headers"] == {}
    assert calls[0]["json"]["api_name"] == "daily"
    assert calls[0]["json"]["token"] == "fast-token"
    assert calls[0]["json"]["params"]["ts_code"] == "000001.SZ"


def test_tushare_super_income_uses_api_key_header(monkeypatch):
    calls = []
    monkeypatch.setattr(ts_mod, "TUSHARE_CLIENT_MODE", "super")
    monkeypatch.setattr(ts_mod, "TUSHARE_SUPER_API_KEY", "super-key")

    source = TushareSource()

    def fake_post(url, json, headers, timeout):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _FakeResponse({
            "code": 0,
            "data": {
                "fields": [
                    "ts_code", "ann_date", "f_ann_date", "end_date", "report_type",
                    "comp_type", "total_revenue", "revenue", "oper_cost",
                    "operate_profit", "n_income_attr_p", "n_income", "basic_eps",
                    "diluted_eps", "total_share",
                ],
                "items": [[
                    "000001.SZ", "20260430", "20260430", "20251231", "1",
                    "1", 100000000.0, None, 60000000.0, 20000000.0,
                    12000000.0, 15000000.0, 1.2, 1.1, "",
                ]],
            },
        })

    source._session.post = fake_post

    statements = source.get_income_statements("000001.SZ", "a_share", limit=1)

    assert len(statements) == 1
    assert statements[0].revenue == 100000000.0
    assert statements[0].net_income == 12000000.0
    assert statements[0].eps_diluted == 1.1
    assert statements[0].shares_outstanding is None
    assert calls[0]["url"] == ts_mod.TUSHARE_SUPER_API_URL
    assert calls[0]["headers"] == {"X-API-Key": "super-key"}
    assert calls[0]["json"]["api_name"] == "income"
    assert calls[0]["json"]["params"]["ts_code"] == "000001.SZ"
    assert calls[0]["json"]["params"]["report_type"] == "1"
    assert "start_date" in calls[0]["json"]["params"]
    assert "end_date" in calls[0]["json"]["params"]
