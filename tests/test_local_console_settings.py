from src.web import local_console


def test_write_env_updates_preserves_comments_and_backs_up(tmp_path, monkeypatch):
    monkeypatch.setattr(local_console, "PROJECT_ROOT", tmp_path)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# demo\nOPENAI_API_KEY=old-key\nTUSHARE_CLIENT_MODE=auto\n",
        encoding="utf-8",
    )

    backup = local_console._write_env_updates(
        {
            "OPENAI_API_KEY": "new-key",
            "TUSHARE_CLIENT_MODE": "super",
            "UNSUPPORTED_KEY": "ignored",
        }
    )

    content = env_path.read_text(encoding="utf-8")
    assert "# demo" in content
    assert "OPENAI_API_KEY=new-key" in content
    assert "TUSHARE_CLIENT_MODE=super" in content
    assert "UNSUPPORTED_KEY" not in content
    assert backup.exists()
    assert "old-key" in backup.read_text(encoding="utf-8")


def test_parse_env_values_strips_wrapping_quotes(tmp_path, monkeypatch):
    monkeypatch.setattr(local_console, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text('OPENAI_API_KEY="quoted-key"\n', encoding="utf-8")

    values = local_console._parse_env_values()

    assert values["OPENAI_API_KEY"] == "quoted-key"
