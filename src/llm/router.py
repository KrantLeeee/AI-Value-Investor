"""LLM Router — routes tasks to the correct LLM provider and handles retry/fallback.

Config-driven via config/llm_config.yaml:
  task_routing:
    report_writing:
      provider: openai
      model: gpt-4o
      max_tokens: 4000
      temperature: 0.3
    news_sentiment:
      provider: deepseek
      model: deepseek-chat

Fallback chain: OpenAI → Anthropic → DeepSeek (configurable).
Retries once with 10s backoff before trying fallback providers.

Network: Uses src.utils.network for proxy-aware HTTP clients.
LLM API domains bypass proxy by default (faster, more reliable).
"""

import os
import time
from typing import Any

from src.utils.config import load_llm_config, get_settings
from src.utils.logger import get_logger
from src.utils.network import create_openai_client, create_anthropic_client

logger = get_logger(__name__)


class LLMError(Exception):
    """Raised when all LLM providers/retries are exhausted."""


# ── Default routing table (used if llm_config.yaml is missing) ───────────────

_DEFAULT_TASK_MAP: dict[str, dict] = {
    "report_writing":      {"provider": "openai",   "model": "gpt-4o",         "max_tokens": 4000, "temperature": 0.3},
    "buffett_analysis":    {"provider": "openai",   "model": "gpt-4o",         "max_tokens": 2000, "temperature": 0.2},
    "graham_analysis":     {"provider": "openai",   "model": "gpt-4o",         "max_tokens": 2000, "temperature": 0.2},
    "valuation_interpret": {"provider": "openai",   "model": "gpt-4o",         "max_tokens": 1500, "temperature": 0.2},
    "portfolio_judgment":  {"provider": "openai",   "model": "gpt-4o",         "max_tokens": 1500, "temperature": 0.2},
    "news_sentiment":      {"provider": "deepseek", "model": "deepseek-chat",  "max_tokens": 500,  "temperature": 0.1},
    "document_extraction": {"provider": "deepseek", "model": "deepseek-chat",  "max_tokens": 2000, "temperature": 0.1},
}

_DEFAULT_FALLBACK: dict[str, list[str]] = {
    "openai":    ["anthropic", "deepseek"],
    "deepseek":  ["openai"],
    "anthropic": ["openai", "deepseek"],
}


def _load_config() -> tuple[dict, dict, dict]:
    """Load task routing, fallback chain, and retry config from yaml."""
    try:
        cfg = load_llm_config()
        task_map = cfg.get("task_routing", _DEFAULT_TASK_MAP)
        fallback  = cfg.get("fallback", _DEFAULT_FALLBACK)
        retry_cfg = cfg.get("retry", {"max_attempts": 2, "backoff_seconds": 10})
        return task_map, fallback, retry_cfg
    except Exception:
        return _DEFAULT_TASK_MAP, _DEFAULT_FALLBACK, {"max_attempts": 2, "backoff_seconds": 10}


def _call_openai(model: str, system_prompt: str, user_prompt: str,
                  max_tokens: int, temperature: float) -> str:
    api_key = get_settings().openai_api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise LLMError("OPENAI_API_KEY not set")
    # Use network-aware client (bypasses proxy for api.openai.com)
    client = create_openai_client(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


def _call_deepseek(model: str, system_prompt: str, user_prompt: str,
                    max_tokens: int, temperature: float) -> str:
    api_key = get_settings().deepseek_api_key or os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise LLMError("DEEPSEEK_API_KEY not set")
    # Use network-aware client (bypasses proxy for api.deepseek.com)
    client = create_openai_client(
        api_key=api_key,
        base_url="https://api.deepseek.com/v1",
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


def _call_anthropic(model: str, system_prompt: str, user_prompt: str,
                     max_tokens: int, temperature: float) -> str:
    api_key = get_settings().anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMError("ANTHROPIC_API_KEY not set")
    # Use network-aware client (bypasses proxy for api.anthropic.com)
    client = create_anthropic_client(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return resp.content[0].text if resp.content else ""


_PROVIDER_FN = {
    "openai":    _call_openai,
    "deepseek":  _call_deepseek,
    "anthropic": _call_anthropic,
}


def call_llm(
    task: str,
    system_prompt: str,
    user_prompt: str,
    *,
    override_provider: str | None = None,
    override_model: str | None = None,
) -> str:
    """
    Call the LLM for a given task, with retry and fallback.

    Args:
        task: Task key from llm_config.yaml, e.g. "buffett_analysis".
        system_prompt: System-level instruction for the LLM.
        user_prompt: User-level content/data to analyse.
        override_provider: Force a specific provider (for testing).
        override_model: Force a specific model name.

    Returns:
        The LLM response text.

    Raises:
        LLMError: If all providers and retries fail.
    """
    task_map, fallback_map, retry_cfg = _load_config()
    task_cfg = task_map.get(task, _DEFAULT_TASK_MAP.get(task, {}))

    primary_provider = override_provider or task_cfg.get("provider", "openai")
    model            = override_model    or task_cfg.get("model", "gpt-4o")
    max_tokens       = task_cfg.get("max_tokens", 2000)
    temperature      = task_cfg.get("temperature", 0.3)
    max_attempts     = retry_cfg.get("max_attempts", 2)
    backoff          = retry_cfg.get("backoff_seconds", 10)

    # Build provider chain: primary + fallbacks
    fallbacks = fallback_map.get(primary_provider, [])
    provider_chain: list[tuple[str, str]] = [(primary_provider, model)]
    for fb_provider in fallbacks:
        fb_model = _DEFAULT_TASK_MAP.get(task, {}).get("model", "gpt-4o")
        provider_chain.append((fb_provider, fb_model))

    last_error: Exception = LLMError("No providers configured")

    for provider, mdl in provider_chain:
        fn = _PROVIDER_FN.get(provider)
        if fn is None:
            logger.warning("[LLM] Unknown provider: %s, skipping", provider)
            continue

        for attempt in range(1, max_attempts + 1):
            try:
                logger.info("[LLM] %s → %s/%s (attempt %d)", task, provider, mdl, attempt)
                result = fn(mdl, system_prompt, user_prompt, max_tokens, temperature)
                logger.info("[LLM] %s completed (%d chars)", task, len(result))
                return result
            except Exception as e:
                last_error = e
                logger.warning("[LLM] %s/%s attempt %d failed: %s", provider, mdl, attempt, e)
                if attempt < max_attempts:
                    time.sleep(backoff)

        logger.warning("[LLM] Provider %s exhausted, trying fallback", provider)

    raise LLMError(f"All LLM providers failed for task '{task}'. Last error: {last_error}") from last_error
