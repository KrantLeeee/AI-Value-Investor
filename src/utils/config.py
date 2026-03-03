"""AI Value Investor — Settings and Configuration Loader."""

import os
from pathlib import Path
from functools import lru_cache

import yaml
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

# Load .env from project root
_ROOT = Path(__file__).parent.parent.parent
load_dotenv(_ROOT / ".env")


class Settings(BaseSettings):
    """All configuration loaded from .env / environment variables."""

    # ── LLM Keys ─────────────────────────────────────────
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")

    # ── Data Source Keys ─────────────────────────────────
    fmp_api_key: str = Field(default="", alias="FMP_API_KEY")

    # ── Email ─────────────────────────────────────────────
    brevo_api_key: str = Field(default="", alias="BREVO_API_KEY")
    email_recipient: str = Field(default="", alias="EMAIL_RECIPIENT")
    email_sender_name: str = Field(default="AI Value Investor", alias="EMAIL_SENDER_NAME")
    email_sender_email: str = Field(default="", alias="EMAIL_SENDER_EMAIL")

    model_config = {"populate_by_name": True}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


def get_project_root() -> Path:
    return _ROOT


def get_data_dir() -> Path:
    d = _ROOT / "data"
    d.mkdir(exist_ok=True)
    return d


def get_db_path() -> Path:
    p = _ROOT / "data" / "db"
    p.mkdir(parents=True, exist_ok=True)
    return p / "market.db"


def get_cache_dir() -> Path:
    d = _ROOT / "data" / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_manual_dir(ticker: str | None = None) -> Path:
    base = _ROOT / "data" / "manual"
    if ticker:
        d = base / ticker
        d.mkdir(parents=True, exist_ok=True)
        return d
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_output_dir(subdir: str | None = None) -> Path:
    base = _ROOT / "output"
    if subdir:
        d = base / subdir
        d.mkdir(parents=True, exist_ok=True)
        return d
    base.mkdir(parents=True, exist_ok=True)
    return base


def load_yaml(path: Path) -> dict:
    """Load a YAML config file. Returns empty dict if file doesn't exist."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_watchlist() -> dict:
    return load_yaml(_ROOT / "config" / "watchlist.yaml")


def load_screening_rules() -> list[dict]:
    data = load_yaml(_ROOT / "config" / "screening_rules.yaml")
    return data.get("rules", [])


def load_investor_profile() -> dict:
    return load_yaml(_ROOT / "config" / "investor_profile.yaml")


def load_llm_config() -> dict:
    return load_yaml(_ROOT / "config" / "llm_config.yaml")
