"""CLI entry point — all user-facing commands."""

import sys
from datetime import date, timedelta
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich import print as rprint

from src.data.database import init_db
from src.utils.config import get_project_root, load_watchlist

console = Console()


@click.group()
@click.version_option("0.1.0")
def cli():
    """
    AI Value Investor — automated research assistant for value investing.

    Run `invest COMMAND --help` for usage on any command.
    """
    # Ensure the database and output directories are ready
    init_db()


# ─── fetch ────────────────────────────────────────────────────────────────────

@cli.command("fetch")
@click.option("--ticker", "-t", default=None, help="Single ticker, e.g. 601808.SH")
@click.option("--market", "-m",
              type=click.Choice(["a_share", "hk", "us"], case_sensitive=False),
              default=None, help="Market type (required with --ticker)")
@click.option("--all", "fetch_all_flag", is_flag=True,
              help="Fetch all tickers in watchlist")
@click.option("--days", "-d", default=None, type=int,
              help="Number of days of price history (default: 3 years)")
def fetch(ticker, market, fetch_all_flag, days):
    """Fetch market data and financial statements."""
    from src.data.fetcher import Fetcher

    fetcher = Fetcher()

    if fetch_all_flag:
        console.print("[bold cyan]Fetching all watchlist tickers...[/bold cyan]")
        watchlist = load_watchlist()
        results = fetcher.fetch_watchlist(watchlist)
        _print_fetch_summary(results)
        return

    if not ticker:
        raise click.UsageError("Provide --ticker or --all")
    if not market:
        # Auto-detect market from ticker suffix
        market = _detect_market(ticker)
        if not market:
            raise click.UsageError("Cannot auto-detect market. Please provide --market")

    start = (date.today() - timedelta(days=days)) if days else None
    console.print(f"[bold]Fetching:[/bold] {ticker} ({market})")
    result = fetcher.fetch_all(ticker, market, start_date=start)
    _print_fetch_result(result)


def _detect_market(ticker: str) -> str | None:
    t = ticker.upper()
    if t.endswith(".SH") or t.endswith(".SZ") or t.endswith(".BJ"):
        return "a_share"
    if t.endswith(".HK"):
        return "hk"
    # Assume pure-alpha tickers are US
    if ticker.isalpha():
        return "us"
    return None


def _print_fetch_result(result: dict):
    table = Table(show_header=False, box=None)
    for k, v in result.items():
        table.add_row(f"  [cyan]{k}[/cyan]", str(v))
    console.print(table)


def _print_fetch_summary(results: list[dict]):
    table = Table("Ticker", "Market", "Prices", "Income", "Balance", "Cashflow", "Status")
    for r in results:
        status = "[red]ERROR[/red]" if "error" in r else "[green]OK[/green]"
        table.add_row(
            r.get("ticker", "?"), r.get("market", "?"),
            str(r.get("prices", "-")), str(r.get("income", "-")),
            str(r.get("balance", "-")), str(r.get("cashflow", "-")),
            status,
        )
    console.print(table)


# ─── ingest ───────────────────────────────────────────────────────────────────

@cli.command("ingest")
@click.option("--ticker", "-t", default=None,
              help="Ingest files for a specific ticker only")
def ingest(ticker):
    """Parse and store manually uploaded financial documents."""
    from src.data.manual_source import ingest_all, ingest_ticker_dir

    if ticker:
        console.print(f"[bold]Ingesting files for {ticker}...[/bold]")
        docs = ingest_ticker_dir(ticker)
    else:
        console.print("[bold]Ingesting all manual files...[/bold]")
        docs = ingest_all()

    for doc in docs:
        icon = "[green]✓[/green]" if doc.status == "success" else "[red]✗[/red]"
        console.print(f"  {icon} {doc.ticker}/{doc.file_name} "
                      f"({doc.text_length:,} chars) [{doc.status}]")

    console.print(f"\n[bold]Done:[/bold] {len(docs)} file(s) processed")


# ─── scan ─────────────────────────────────────────────────────────────────────

@cli.command("scan")
@click.option("--notify", is_flag=True, help="Send email if signals are found")
def scan(notify):
    """Run factor screening on the watchlist (pure code, no LLM).

    Reads config/screening_rules.yaml rules and evaluates all watchlist tickers.
    Results saved to output/signals/{date}.json.
    """
    from src.strategy.screener import run_scan

    console.print("[bold cyan]Running factor scan...[/bold cyan]")
    watchlist = load_watchlist()
    n_tickers = sum(len(v) for v in watchlist.get("watchlist", {}).values())
    console.print(f"[dim]Watchlist: {n_tickers} tickers[/dim]")

    signals = run_scan(watchlist=watchlist, notify=notify)

    if not signals:
        console.print("[yellow]No signals triggered today.[/yellow]")
        return

    # Print summary table
    opps   = [s for s in signals if s.signal == "opportunity"]
    alerts = [s for s in signals if s.signal == "alert"]

    if opps:
        table = Table("Ticker", "Rule", "PE", "ROE", "价格", "安全边际", title="📈 Investment Opportunities")
        for s in opps:
            m = s.metrics or {}
            table.add_row(
                s.ticker, s.rule_name,
                f"{m.get('pe_ratio', '-')}" if m.get('pe_ratio') else "-",
                f"{m.get('roe', '-')}%" if m.get('roe') else "-",
                f"¥{m.get('current_price', '-')}" if m.get('current_price') else "-",
                f"{m.get('margin_of_safety', 0)*100:.0f}%" if m.get('margin_of_safety') else "-",
            )
        console.print(table)

    if alerts:
        atbl = Table("Ticker", "Rule", "Description", title="⚠️  Risk Alerts", style="red")
        for s in alerts:
            atbl.add_row(s.ticker, s.rule_name, s.description or "")
        console.print(atbl)

    console.print(f"\n[bold]Total:[/bold] {len(opps)} opportunities, {len(alerts)} alerts")
    if notify:
        console.print(f"[green]✓ Email notification sent[/green]" if signals else "")


# ─── report ───────────────────────────────────────────────────────────────────

def _preflight_company_check(ticker: str, market: str) -> dict | None:
    """
    Pre-flight check: verify company info is available before proceeding.

    This prevents generating reports with "hallucinated" company/industry info
    when the data sources fail to return actual company information.
    """
    from src.data.fetcher import Fetcher

    fetcher = Fetcher()
    basics = fetcher.fetch_company_basics(ticker, market)
    return basics


def _display_company_info(basics: dict, ticker: str) -> None:
    """Display company info in a formatted box for user confirmation."""
    from rich.panel import Panel

    company_name = basics.get("company_name") or "未知"
    industry = basics.get("industry") or "未知"
    main_business = basics.get("main_business") or "未提供"
    source = basics.get("source", "API")
    is_financial = basics.get("is_financial", False)

    # Truncate main_business if too long
    if main_business and len(main_business) > 60:
        main_business = main_business[:57] + "..."

    info_text = (
        f"[bold cyan]公司名称:[/bold cyan] {company_name}\n"
        f"[bold cyan]所属行业:[/bold cyan] {industry}"
        + (f" [dim](金融股)[/dim]" if is_financial else "") + "\n"
        f"[bold cyan]主营业务:[/bold cyan] {main_business}\n"
        f"[dim]数据来源: {source}[/dim]"
    )

    console.print(Panel(info_text, title=f"[bold]{ticker} 公司信息确认[/bold]", border_style="green"))


@cli.command("report")
@click.option("--ticker", "-t", default=None, help="Ticker, e.g. 601808.SH")
@click.option("--quick", is_flag=True, help="Data-only report (no LLM, faster)")
@click.option("--model", default=None, help="Override LLM model (e.g. gpt-4o, deepseek-chat)")
@click.option("--watchlist-top", "watchlist_top", default=None, type=int,
              help="Generate reports for top N watchlist tickers (for automation)")
@click.option("--notify", is_flag=True, help="Email the generated report(s) via Brevo")
@click.option("--skip-confirm", "skip_confirm", is_flag=True,
              help="Skip company info confirmation (for automation)")
@click.option("--company-name", "override_company", default=None,
              help="Override company name when auto-detection fails")
@click.option("--industry", "override_industry", default=None,
              help="Override industry when auto-detection fails")
def report(ticker, quick, model, watchlist_top, notify, skip_confirm, override_company, override_industry):
    """Generate a deep research report for one ticker or top-N watchlist tickers.

    Use --quick for fast data-only report without LLM.
    Use --watchlist-top N for automated weekly reports (GitHub Actions).
    Use --notify to email the report via Brevo.
    Use --skip-confirm to skip company info confirmation (automation mode).
    Use --company-name and --industry to override when auto-detection fails.
    Requires data to be fetched first: invest fetch --ticker TICKER
    """
    from src.agents.registry import run_all_agents
    from src.notification.telegram_notifier import send_report_message as _notify_report

    # ── watchlist-top mode (GitHub Actions automation) ────────────────────────
    if watchlist_top:
        wl = load_watchlist()
        tickers_markets: list[tuple[str, str]] = []
        for market_key, items in wl.get("watchlist", {}).items():
            for item in items:
                t = item.get("ticker") if isinstance(item, dict) else str(item)
                tickers_markets.append((t, market_key))
        tickers_markets = tickers_markets[:watchlist_top]
        console.print(f"[bold]Generating reports for top {watchlist_top} watchlist tickers...[/bold]")

        failed_tickers = []
        for t, m in tickers_markets:
            mode = "[yellow]quick[/yellow]" if quick else "[cyan]full LLM[/cyan]"
            console.print(f"  → {t} ({mode})")

            # Pre-flight check for watchlist mode (silent, auto-skip if fails)
            basics = _preflight_company_check(t, m)
            if not basics or not basics.get("company_name"):
                console.print(f"    [yellow]⚠ 公司信息不可用，跳过[/yellow]")
                failed_tickers.append(t)
                continue

            try:
                sigs, rpath = run_all_agents(t, m, quick=quick)
                if notify:
                    _notify_report(t, rpath, sigs)
                    console.print(f"    [green]✓ Telegram 通知已发送[/green]")
                else:
                    console.print(f"    [green]✓ {rpath}[/green]")
            except Exception as e:
                console.print(f"    [red]✗ {e}[/red]")
                failed_tickers.append(t)

        if failed_tickers:
            console.print(f"\n[yellow]⚠ {len(failed_tickers)} 只股票因数据问题跳过: {', '.join(failed_tickers)}[/yellow]")
        return

    # ── single ticker mode ────────────────────────────────────────────────────
    if not ticker:
        raise click.UsageError("Provide --ticker TICKER or --watchlist-top N")

    market = _detect_market(ticker)
    if not market:
        raise click.UsageError(f"Cannot auto-detect market for '{ticker}'. "
                               "Use standard format: 601808.SH / 0700.HK / AAPL")

    # ── Phase 0: Pre-flight Company Info Check ────────────────────────────────
    console.print(f"\n[bold]🔍 Pre-flight Check: {ticker}[/bold]")

    basics = _preflight_company_check(ticker, market)

    # Handle manual override
    if override_company or override_industry:
        if not basics:
            basics = {}
        if override_company:
            basics["company_name"] = override_company
        if override_industry:
            basics["industry"] = override_industry
        basics["source"] = "manual_override"

    # Check if we have valid company info
    if not basics or not basics.get("company_name"):
        console.print("\n[red]❌ 无法获取公司基本信息[/red]")
        console.print("[dim]已尝试的数据源: QVeris iFinD → 本地缓存 → AKShare → Web Search → LLM[/dim]")
        console.print("\n[yellow]解决方案:[/yellow]")
        console.print("  1. 检查网络/代理设置")
        console.print("  2. 使用 --company-name 和 --industry 参数手动指定:")
        console.print(f"     invest report -t {ticker} --company-name \"公司名\" --industry \"行业\"")
        console.print("  3. 联系开发者添加到本地映射表")
        raise SystemExit(1)

    # Display company info for confirmation
    _display_company_info(basics, ticker)

    # User confirmation (unless skipped)
    if not skip_confirm:
        if not click.confirm("\n信息正确？继续生成报告？", default=True):
            console.print("[yellow]已取消报告生成[/yellow]")
            return

    # ── Phase 1: Generate Report ──────────────────────────────────────────────
    mode_label = "[yellow]快速数据版[/yellow]" if quick else "[cyan]完整版（含LLM分析）[/cyan]"
    console.print(f"\n[bold]Generating report for {ticker}[/bold] ({mode_label})")
    if not quick:
        console.print("[dim]需要 OPENAI_API_KEY / DEEPSEEK_API_KEY 环境变量。无 Key 请加 --quick[/dim]")

    # Set model override in environment (router.py reads from config, not env directly,
    # but a future extension can pick this up)
    if model:
        import os
        os.environ["LLM_MODEL_OVERRIDE"] = model

    try:
        signals, report_path = run_all_agents(ticker, market, quick=quick, company_context_override=basics)
    except Exception as e:
        console.print(f"[red]Report generation failed: {e}[/red]")
        raise SystemExit(1)

    # Print signal summary table
    table = Table("Agent", "Signal", "Confidence", box=None)
    _SIGNAL_COLORS = {"bullish": "green", "neutral": "yellow", "bearish": "red"}
    for name, sig in signals.items():
        if sig:
            color = _SIGNAL_COLORS.get(sig.signal, "white")
            table.add_row(
                name.replace("_", " ").title(),
                f"[{color}]{sig.signal.upper()}[/{color}]",
                f"{sig.confidence:.0%}",
            )
    console.print("\n[bold]Agent Signals:[/bold]")
    console.print(table)
    console.print(f"\n[green]✓ Report saved:[/green] {report_path}")
    if notify:
        ok = _notify_report(ticker, report_path, signals)
        if ok:
            console.print("[green]✓ Telegram 通知已发送[/green]")
        else:
            console.print("[yellow]⚠ Telegram 发送失败 (检查 .env 中的 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)[/yellow]")


# ─── invest ───────────────────────────────────────────────────────────────────

@cli.command("invest")
@click.option("--ticker", "-t", required=True, help="Ticker to evaluate")
@click.option("--confirm", is_flag=True, help="Confirm investment execution")
@click.option("--amount", type=float, default=None, help="Actual amount invested (with --confirm)")
def invest(ticker, confirm, amount):
    """Generate position sizing recommendation and record executions."""
    console.print(f"[bold]Portfolio analysis for {ticker}...[/bold]")
    # Placeholder: portfolio/portfolio_manager.py will be implemented in Week 4
    console.print("[yellow]⚠ Portfolio Manager not yet implemented (Week 4)[/yellow]")


# ─── profile ──────────────────────────────────────────────────────────────────

@cli.command("profile")
@click.option("--setup", is_flag=True, help="Interactively set up investor profile")
@click.option("--show", is_flag=True, help="Show current investor profile")
def profile(setup, show):
    """Manage the investor profile (capital, risk tolerance, etc.)."""
    from src.utils.config import load_investor_profile, get_project_root
    import yaml

    profile_path = get_project_root() / "config" / "investor_profile.yaml"

    if show:
        p = load_investor_profile()
        if not p:
            console.print("[yellow]No profile found. Run: invest profile --setup[/yellow]")
            return
        for k, v in p.items():
            console.print(f"  [cyan]{k}:[/cyan] {v}")
        return

    if setup:
        console.print("[bold]Setting up Investor Profile[/bold]\n")
        total_capital = click.prompt("Total investment capital (CNY ¥)", type=float)
        monthly_addition = click.prompt("Monthly additional funds (CNY ¥)", type=float, default=0)
        max_single = click.prompt("Max single position % (e.g. 0.15 = 15%)", type=float, default=0.15)
        max_risk = click.prompt("Max total risk exposure % (e.g. 0.30 = 30%)", type=float, default=0.30)
        horizon = click.prompt("Investment horizon",
                               type=click.Choice(["short", "medium", "long"]), default="long")

        data = {
            "total_capital": total_capital,
            "monthly_addition": monthly_addition,
            "max_single_position_pct": max_single,
            "max_total_risk_pct": max_risk,
            "investment_horizon": horizon,
        }
        profile_path.parent.mkdir(exist_ok=True)
        with open(profile_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        console.print(f"\n[green]✓ Profile saved to {profile_path}[/green]")


# ─── portfolio ────────────────────────────────────────────────────────────────

@cli.command("portfolio")
def portfolio():
    """Show current portfolio holdings and performance."""
    import json
    portfolio_path = get_project_root() / "data" / "portfolio.json"
    if not portfolio_path.exists():
        console.print("[yellow]No portfolio data yet. Execute an investment first.[/yellow]")
        return
    with open(portfolio_path) as f:
        data = json.load(f)

    positions = data.get("positions", [])
    if not positions:
        console.print("[yellow]No active positions.[/yellow]")
        return

    table = Table("Ticker", "Invested", "Avg Cost", "Shares")
    for pos in positions:
        table.add_row(
            pos["ticker"],
            f"¥{pos['total_invested']:,.0f}",
            f"¥{pos['avg_cost']:.2f}",
            f"{pos['shares']:,.0f}",
        )
    console.print(table)
    console.print(f"\n[bold]Cash available:[/bold] ¥{data.get('cash_available', 0):,.0f}")


# ─── status ───────────────────────────────────────────────────────────────────

@cli.command("status")
def status():
    """Show system status — data freshness, source health, watchlist size."""
    from src.data.database import get_latest_prices, get_income_statements
    from src.data.akshare_source import AKShareSource
    from src.data.baostock_source import BaoStockSource
    from src.data.yfinance_source import YFinanceSource
    from src.data.fmp_source import FMPSource

    console.print("[bold]System Status[/bold]\n")

    # Watchlist size
    watchlist = load_watchlist()
    all_tickers = []
    for market, items in watchlist.get("watchlist", {}).items():
        all_tickers.extend(items)
    console.print(f"[cyan]Watchlist:[/cyan] {len(all_tickers)} tickers")

    # Data source health
    console.print("\n[bold]Data Sources:[/bold]")
    for name, SourceClass in [
        ("AKShare", AKShareSource),
        ("BaoStock", BaoStockSource),
        ("yfinance", YFinanceSource),
        ("FMP API", FMPSource),
    ]:
        try:
            ok = SourceClass().health_check()
            icon = "[green]✓[/green]" if ok else "[yellow]⚠[/yellow]"
            console.print(f"  {icon} {name}")
        except Exception:
            console.print(f"  [red]✗[/red] {name}")


# ─── network ─────────────────────────────────────────────────────────────────

@cli.command("network")
@click.option("--test", "-t", is_flag=True, help="Run connectivity tests")
def network_cmd(test: bool):
    """Diagnose network configuration and proxy settings.

    Shows current proxy configuration and which domains bypass proxy.
    Use --test to run connectivity tests against LLM APIs and data sources.
    """
    from src.utils.network import diagnose_network, should_bypass_proxy
    from rich.panel import Panel

    console.print("[bold]Network Configuration[/bold]\n")

    diag = diagnose_network()

    # Show proxy config
    proxy_config = diag["proxy_config"]
    if proxy_config["http_proxy"] or proxy_config["https_proxy"]:
        console.print("[cyan]Proxy Settings:[/cyan]")
        console.print(f"  HTTP_PROXY:  {proxy_config['http_proxy'] or '(not set)'}")
        console.print(f"  HTTPS_PROXY: {proxy_config['https_proxy'] or '(not set)'}")
        console.print(f"  NO_PROXY:    {proxy_config['no_proxy'] or '(not set)'}")
    else:
        console.print("[cyan]Proxy:[/cyan] Not configured (direct connections)")

    # Show bypass domains
    console.print("\n[cyan]LLM API Domains (bypass proxy):[/cyan]")
    for domain in diag["llm_bypass_domains"]:
        console.print(f"  [green]✓[/green] {domain}")

    if test:
        console.print("\n[bold]Connectivity Tests:[/bold]")
        import httpx

        # Test LLM APIs
        test_urls = [
            ("OpenAI", "https://api.openai.com/v1/models"),
            ("Anthropic", "https://api.anthropic.com"),
            ("DeepSeek", "https://api.deepseek.com/v1/models"),
            ("Tavily", "https://api.tavily.com"),
        ]

        for name, url in test_urls:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
            bypass = should_bypass_proxy(domain)
            try:
                # Use httpx directly with explicit proxy handling
                from src.utils.network import get_httpx_proxy
                proxy = get_httpx_proxy(domain)
                with httpx.Client(proxy=proxy, timeout=10) as client:
                    resp = client.get(url)
                    status = resp.status_code
                    icon = "[green]✓[/green]" if status < 500 else "[yellow]⚠[/yellow]"
                    proxy_note = "(direct)" if bypass else "(via proxy)"
                    console.print(f"  {icon} {name}: HTTP {status} {proxy_note}")
            except Exception as e:
                console.print(f"  [red]✗[/red] {name}: {str(e)[:50]}")

        # Test Chinese data sources
        console.print("\n[cyan]Data Source Tests:[/cyan]")
        china_tests = [
            ("Sina Finance", "http://hq.sinajs.cn/list=s_sh000001"),
        ]

        for name, url in china_tests:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
            bypass = should_bypass_proxy(domain)
            try:
                from src.utils.network import requests_get
                resp = requests_get(url, timeout=10)
                status = resp.status_code
                icon = "[green]✓[/green]" if status == 200 else "[yellow]⚠[/yellow]"
                proxy_note = "(direct)" if bypass else "(via proxy)"
                console.print(f"  {icon} {name}: HTTP {status} {proxy_note}")
            except Exception as e:
                console.print(f"  [red]✗[/red] {name}: {str(e)[:50]}")

    console.print("\n[dim]Tip: Set HTTP_PROXY/HTTPS_PROXY env vars for proxy. "
                  "LLM APIs bypass proxy by default.[/dim]")


# ─── backtest ─────────────────────────────────────────────────────────────────

@cli.command("backtest")
@click.option("--rule", required=True, help="Rule name from screening_rules.yaml")
@click.option("--start", type=int, required=True, help="Start year, e.g. 2015")
@click.option("--end", type=int, required=True, help="End year, e.g. 2024")
@click.option("--hold", type=int, default=3, help="Hold period in years")
def backtest(rule, start, end, hold):
    """Run a factor backtest (pure code, no LLM).

    Example: invest backtest --rule "安全边际" --start 2020 --end 2024 --hold 3
    Requires historical price + financial data (invest fetch --all first).
    """
    from src.strategy.backtester import run_factor_backtest

    console.print(f"[bold]Backtesting rule '{rule}' ({start}-{end}, hold {hold}y)...[/bold]")
    try:
        results = run_factor_backtest(rule_name=rule, start=start, end=end, hold=hold)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1)

    if "error" in results:
        console.print(f"[yellow]⚠ {results['error']}[/yellow]")
        return

    # Print statistics table
    stats = Table(show_header=False, box=None, title=f"Backtest Results: {rule}")
    stats.add_row("年份范围",         f"{start} – {end} (持有{hold}年)")
    stats.add_row("筛选到的仓位数",    str(results.get("n_screened", 0)))
    stats.add_row("有价格数据的仓位",   str(results.get("n_with_price_data", 0)))
    stats.add_row("胜率",             f"{results.get('win_rate', 0):.1f}%")
    stats.add_row("平均持有期回报",     f"{results.get('avg_return_pct', 0):.1f}%")
    stats.add_row("平均CAGR",         f"{results.get('avg_cagr_pct', 0):.1f}%")
    stats.add_row("Sharpe Ratio",     f"{results.get('sharpe', 0):.3f}")
    stats.add_row("最大回撤",          f"{results.get('max_drawdown_pct', 0):.1f}%")
    console.print(stats)

    best = results.get("best_position")
    worst = results.get("worst_position")
    if best:
        console.print(f"\n[green]最佳仓位:[/green] {best['ticker']} {best['buy_year']}→{best['sell_year']} 回报 {best['return']*100:.1f}%")
    if worst:
        console.print(f"[red]最差仓位:[/red] {worst['ticker']} {worst['buy_year']}→{worst['sell_year']} 回报 {worst['return']*100:.1f}%")


if __name__ == "__main__":
    cli()
