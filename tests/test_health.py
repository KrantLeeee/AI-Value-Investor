from src.web import health


def test_save_and_load_latest_health(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "HEALTH_DIR", tmp_path)
    monkeypatch.setattr(health, "LATEST_HEALTH_PATH", tmp_path / "latest.json")

    results = [
        {
            "name": "OpenAI",
            "status": "ok",
            "detail": "已配置",
            "checked_at": "2026-07-16 10:00:00",
        }
    ]

    path = health.save_health_results(results)

    assert path.exists()
    assert health.load_latest_health() == results


def test_run_health_checks_persists_results_without_real_network(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "HEALTH_DIR", tmp_path)
    monkeypatch.setattr(health, "LATEST_HEALTH_PATH", tmp_path / "latest.json")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    monkeypatch.setattr(
        health,
        "_tushare_health",
        lambda: health._result("Tushare", "ok", "轻量连通性检查通过"),
    )
    monkeypatch.setattr(
        health,
        "_qveris_health",
        lambda: health._result("QVeris iFinD", "missing", "未配置 QVeris API key"),
    )
    monkeypatch.setattr(
        health,
        "_fmp_health",
        lambda: health._result("FMP", "missing", "未配置 FMP_API_KEY"),
    )
    monkeypatch.setattr(
        health,
        "_telegram_health",
        lambda: health._result("Telegram", "missing", "未配置 TELEGRAM_BOT_TOKEN"),
    )

    results = health.run_health_checks()

    by_name = {item["name"]: item for item in results}
    assert by_name["OpenAI"]["status"] == "ok"
    assert by_name["DeepSeek"]["status"] == "missing"
    assert by_name["Tushare"]["status"] == "ok"
    assert health.load_latest_health() == results
