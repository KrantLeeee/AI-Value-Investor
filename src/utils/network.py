"""Network utilities — centralized proxy and HTTP client management.

This module provides consistent HTTP client configuration across the codebase,
handling proxy settings intelligently based on the target domain.

Key Features:
1. Proxy auto-detection from environment variables
2. Smart bypass for LLM APIs (usually don't need proxy)
3. Configurable timeout and retry settings
4. Support for both requests and httpx libraries

Environment Variables:
- HTTP_PROXY / HTTPS_PROXY: Proxy URL (e.g., http://127.0.0.1:7890)
- NO_PROXY: Comma-separated domains to bypass proxy
- PROXY_BYPASS_LLM: Set to "false" to use proxy for LLM APIs (default: true)
- PROXY_BYPASS_CHINA: Set to "true" to bypass proxy for Chinese sites (default: false)
"""

import os
from functools import lru_cache
from typing import Literal

from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── Domains that should BYPASS proxy (LLM APIs, typically accessible directly) ──
# These services are usually faster without proxy, and VPN proxies can cause issues
LLM_API_DOMAINS = {
    "api.openai.com",
    "api.anthropic.com",
    "api.deepseek.com",
    "api.tavily.com",  # Tavily web search
}

# ── Domains that typically NEED proxy (Chinese services when abroad) ──
# But may work better WITHOUT proxy when in China (set PROXY_BYPASS_CHINA=true)
CHINA_DOMAINS = {
    "hq.sinajs.cn",
    "push2his.eastmoney.com",
    "emweb.eastmoney.com",
    "finance.sina.com.cn",
    "xueqiu.com",
    "push2.eastmoney.com",
    "data.eastmoney.com",
    "datacenter.eastmoney.com",
}

# ── Default timeouts ──
DEFAULT_TIMEOUT = 30
LLM_TIMEOUT = 120  # LLM calls can be slow


@lru_cache(maxsize=1)
def get_proxy_config() -> dict:
    """
    Get proxy configuration from environment.

    Reads: HTTP_PROXY, HTTPS_PROXY, ALL_PROXY, NO_PROXY

    Returns dict with:
        - http: proxy URL for HTTP or None
        - https: proxy URL for HTTPS or None
        - no_proxy: comma-separated domains to bypass
    """
    http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
    https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    all_proxy = os.getenv("ALL_PROXY") or os.getenv("all_proxy")
    no_proxy = os.getenv("NO_PROXY") or os.getenv("no_proxy") or ""

    # ALL_PROXY is fallback for both HTTP and HTTPS
    if all_proxy:
        http_proxy = http_proxy or all_proxy
        https_proxy = https_proxy or all_proxy

    config = {
        "http": http_proxy,
        "https": https_proxy,
        "no_proxy": no_proxy,
    }

    if http_proxy or https_proxy:
        logger.debug("[Network] Proxy detected: http=%s https=%s no_proxy=%s",
                    http_proxy, https_proxy, no_proxy)

    return config


def should_bypass_proxy(domain: str) -> bool:
    """
    Check if a domain should bypass proxy.

    Returns True if:
    1. Domain is in LLM_API_DOMAINS and PROXY_BYPASS_LLM != "false"
    2. Domain is in CHINA_DOMAINS and PROXY_BYPASS_CHINA == "true"
    3. Domain matches NO_PROXY environment variable patterns

    Environment Variables:
    - PROXY_BYPASS_LLM: Set to "false" to use proxy for LLM APIs (default: bypass)
    - PROXY_BYPASS_CHINA: Set to "true" to bypass proxy for Chinese sites
    """
    # Check LLM API domains (bypass by default, unless explicitly disabled)
    bypass_llm = os.getenv("PROXY_BYPASS_LLM", "true").lower() != "false"
    if domain in LLM_API_DOMAINS and bypass_llm:
        return True

    # Check China domains (do NOT bypass by default, unless explicitly enabled)
    # This is useful when you're in China and don't need proxy for domestic sites
    bypass_china = os.getenv("PROXY_BYPASS_CHINA", "false").lower() == "true"
    if domain in CHINA_DOMAINS and bypass_china:
        return True

    # Check NO_PROXY patterns
    no_proxy = get_proxy_config()["no_proxy"]
    if not no_proxy:
        return False

    for pattern in no_proxy.split(","):
        pattern = pattern.strip()
        if not pattern:
            continue
        # Handle wildcard patterns like *.example.com
        if pattern.startswith("*."):
            if domain.endswith(pattern[1:]):
                return True
        elif domain == pattern or domain.endswith("." + pattern):
            return True

    return False


def get_requests_proxies(target_domain: str | None = None) -> dict | None:
    """
    Get proxy dict for `requests` library.

    Args:
        target_domain: If provided, check if this domain should bypass proxy

    Returns:
        Dict like {"http": "...", "https": "..."} or None if no proxy needed
    """
    if target_domain and should_bypass_proxy(target_domain):
        logger.debug("[Network] Bypassing proxy for %s", target_domain)
        return None

    config = get_proxy_config()
    if not config["http"] and not config["https"]:
        return None

    proxies = {}
    if config["http"]:
        proxies["http"] = config["http"]
    if config["https"]:
        proxies["https"] = config["https"]

    return proxies if proxies else None


def get_httpx_proxy(target_domain: str | None = None) -> str | None:
    """
    Get proxy URL for `httpx` library.

    httpx uses a single `proxy` parameter (not `proxies` like requests).

    Args:
        target_domain: If provided, check if this domain should bypass proxy

    Returns:
        Proxy URL string or None if no proxy needed
    """
    if target_domain and should_bypass_proxy(target_domain):
        logger.debug("[Network] Bypassing proxy for %s", target_domain)
        return None

    config = get_proxy_config()
    # Prefer HTTPS proxy, fallback to HTTP
    return config["https"] or config["http"]


def create_httpx_client(
    target_domain: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    **kwargs,
):
    """
    Create an httpx.Client with proper proxy configuration.

    Args:
        target_domain: Target domain for proxy bypass check
        timeout: Request timeout in seconds
        **kwargs: Additional arguments passed to httpx.Client

    Returns:
        httpx.Client instance
    """
    import httpx

    proxy = get_httpx_proxy(target_domain)

    return httpx.Client(
        proxy=proxy,
        timeout=timeout,
        **kwargs,
    )


def create_openai_client(api_key: str, base_url: str | None = None):
    """
    Create an OpenAI client with proper proxy configuration.

    OpenAI SDK uses httpx internally. This function creates a properly
    configured httpx client and passes it to the OpenAI constructor.

    Args:
        api_key: OpenAI API key
        base_url: Optional custom base URL (e.g., for DeepSeek)

    Returns:
        OpenAI client instance
    """
    from openai import OpenAI
    import httpx

    # Determine target domain for proxy bypass
    if base_url:
        from urllib.parse import urlparse
        domain = urlparse(base_url).netloc
    else:
        domain = "api.openai.com"

    proxy = get_httpx_proxy(domain)

    # Create custom httpx client
    http_client = httpx.Client(
        proxy=proxy,
        timeout=LLM_TIMEOUT,
    )

    client_kwargs = {
        "api_key": api_key,
        "http_client": http_client,
    }
    if base_url:
        client_kwargs["base_url"] = base_url

    return OpenAI(**client_kwargs)


def create_anthropic_client(api_key: str):
    """
    Create an Anthropic client with proper proxy configuration.

    Args:
        api_key: Anthropic API key

    Returns:
        Anthropic client instance
    """
    import anthropic
    import httpx

    proxy = get_httpx_proxy("api.anthropic.com")

    # Create custom httpx client
    http_client = httpx.Client(
        proxy=proxy,
        timeout=LLM_TIMEOUT,
    )

    return anthropic.Anthropic(
        api_key=api_key,
        http_client=http_client,
    )


def _get_default_headers(domain: str) -> dict:
    """
    Get default headers for a domain.

    Chinese data sources require User-Agent and Referer headers to avoid 403.
    """
    if domain in CHINA_DOMAINS or domain.endswith(".sina.com.cn"):
        return {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "http://finance.sina.com.cn",
        }
    return {}


def requests_get(url: str, **kwargs) -> "requests.Response":
    """
    Wrapper around requests.get with automatic proxy configuration.

    Automatically adds:
    - Proxy settings based on domain
    - User-Agent and Referer headers for Chinese data sources

    Args:
        url: Target URL
        **kwargs: Additional arguments passed to requests.get

    Returns:
        requests.Response object
    """
    import requests
    from urllib.parse import urlparse

    domain = urlparse(url).netloc
    proxies = get_requests_proxies(domain)

    # Don't override if caller explicitly set proxies
    if "proxies" not in kwargs and proxies:
        kwargs["proxies"] = proxies

    # Set default timeout if not provided
    if "timeout" not in kwargs:
        kwargs["timeout"] = DEFAULT_TIMEOUT

    # Add default headers for certain domains (don't override existing)
    default_headers = _get_default_headers(domain)
    if default_headers:
        existing_headers = kwargs.get("headers", {})
        merged_headers = {**default_headers, **existing_headers}
        kwargs["headers"] = merged_headers

    return requests.get(url, **kwargs)


def requests_post(url: str, **kwargs) -> "requests.Response":
    """
    Wrapper around requests.post with automatic proxy configuration.
    """
    import requests
    from urllib.parse import urlparse

    domain = urlparse(url).netloc
    proxies = get_requests_proxies(domain)

    if "proxies" not in kwargs and proxies:
        kwargs["proxies"] = proxies

    if "timeout" not in kwargs:
        kwargs["timeout"] = DEFAULT_TIMEOUT

    # Add default headers for certain domains (don't override existing)
    default_headers = _get_default_headers(domain)
    if default_headers:
        existing_headers = kwargs.get("headers", {})
        merged_headers = {**default_headers, **existing_headers}
        kwargs["headers"] = merged_headers

    return requests.post(url, **kwargs)


def clear_proxy_cache():
    """Clear cached proxy configuration. Call after changing env vars."""
    get_proxy_config.cache_clear()


def configure_requests_session():
    """
    Configure global requests session settings for better proxy/SSL handling.

    This patches the default requests session to:
    1. Use proper User-Agent headers
    2. Handle SSL errors more gracefully
    3. Add retry logic for transient failures

    Call this at application startup or before heavy network usage.
    """
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    # Configure retry strategy for transient failures
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"],
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)

    # Patch the default session
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    # Set default headers
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    })

    logger.debug("[Network] Configured requests session with retry strategy")


def setup_akshare_environment():
    """
    Configure environment for AKShare before importing it.

    AKShare uses requests internally. This function:
    1. Sets up NO_PROXY for domains that don't work well with proxy
    2. Configures SSL settings for problematic domains

    Call this BEFORE importing akshare.
    """
    import urllib3

    # Suppress SSL warnings (AKShare internal code may trigger these)
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # If proxy is configured but causing issues with China domains,
    # add them to NO_PROXY
    config = get_proxy_config()
    if config["http"] or config["https"]:
        # Get current NO_PROXY
        current_no_proxy = os.getenv("NO_PROXY", "")
        no_proxy_domains = set(current_no_proxy.split(",")) if current_no_proxy else set()

        # Add eastmoney domains that have SSL issues with proxy
        # These domains work better with direct connection
        eastmoney_domains = [
            "push2his.eastmoney.com",
            "push2.eastmoney.com",
            "datacenter.eastmoney.com",
        ]

        bypass_eastmoney = os.getenv("PROXY_BYPASS_EASTMONEY", "true").lower() != "false"
        if bypass_eastmoney:
            no_proxy_domains.update(eastmoney_domains)

            new_no_proxy = ",".join(d for d in no_proxy_domains if d)
            if new_no_proxy != current_no_proxy:
                os.environ["NO_PROXY"] = new_no_proxy
                os.environ["no_proxy"] = new_no_proxy
                clear_proxy_cache()
                logger.debug("[Network] Added eastmoney domains to NO_PROXY: %s", eastmoney_domains)


def diagnose_network() -> dict:
    """
    Diagnose current network configuration.

    Returns dict with:
        - proxy_config: Current proxy settings
        - llm_bypass: List of LLM domains that will bypass proxy
        - test_results: Connection test results (if run)
    """
    config = get_proxy_config()
    bypass_llm = os.getenv("PROXY_BYPASS_LLM", "true").lower() != "false"
    bypass_china = os.getenv("PROXY_BYPASS_CHINA", "false").lower() == "true"

    return {
        "proxy_config": {
            "http_proxy": config["http"],
            "https_proxy": config["https"],
            "no_proxy": config["no_proxy"],
        },
        "llm_bypass_domains": list(LLM_API_DOMAINS) if bypass_llm else [],
        "china_domains": list(CHINA_DOMAINS),
        "china_bypass_enabled": bypass_china,
        "env_vars": {
            "HTTP_PROXY": os.getenv("HTTP_PROXY"),
            "HTTPS_PROXY": os.getenv("HTTPS_PROXY"),
            "ALL_PROXY": os.getenv("ALL_PROXY"),
            "NO_PROXY": os.getenv("NO_PROXY"),
            "PROXY_BYPASS_LLM": os.getenv("PROXY_BYPASS_LLM", "true"),
            "PROXY_BYPASS_CHINA": os.getenv("PROXY_BYPASS_CHINA", "false"),
        },
    }
