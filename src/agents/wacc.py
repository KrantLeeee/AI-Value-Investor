"""WACC (Weighted Average Cost of Capital) Calculation Module.

Implements industry-adapted WACC calculation per PROJECT_ROADMAP.md P2-⑦:

Formula: WACC = E/(E+D) × re + D/(E+D) × rd × (1-Tc)

Where:
- E = Market value of equity (shares outstanding × current price)
- D = Interest-bearing debt
- re = Cost of equity (CAPM: rf + β × MRP)
- rd = Cost of debt
- Tc = Effective tax rate

Based on A-share empirical research:
- MRP = 5.5% (market risk premium)
- β calculated from 60-month regression vs 沪深300
- Fallback to industry default β for new stocks
"""

import math
from datetime import date, timedelta
from typing import Optional

import numpy as np

from src.data.database import get_latest_prices, get_balance_sheets, get_income_statements
from src.agents.industry_classifier import get_scoring_thresholds
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Constants based on A-share empirical research
MRP = 0.055  # Market Risk Premium for A-shares (5.5%)
RF_FALLBACK = 0.028  # Fallback 10-year treasury yield if API fails (2.8%)
BETA_WINSORIZE_PCT = 0.01  # Trim 1% extremes when calculating beta


def _safe(x) -> float | None:
    """Safe float conversion with NaN/Inf handling."""
    if x is None:
        return None
    try:
        f = float(x)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def get_risk_free_rate() -> float:
    """
    Get 10-year treasury yield as risk-free rate.

    TODO: Integrate with AKShare API: ak.bond_zh_us_rate()
    For now, returns fallback value.

    Returns:
        Risk-free rate as decimal (e.g., 0.028 for 2.8%)
    """
    # TODO: Implement AKShare API call
    # try:
    #     import akshare as ak
    #     bond_data = ak.bond_zh_us_rate()
    #     # Extract 10-year CN treasury yield
    #     rf = bond_data[...] / 100
    #     return rf
    # except Exception as e:
    #     logger.warning(f"[WACC] Failed to fetch risk-free rate from AKShare: {e}")

    logger.debug(f"[WACC] Using fallback risk-free rate: {RF_FALLBACK*100:.2f}%")
    return RF_FALLBACK


def calculate_beta(ticker: str, months: int = 60) -> Optional[float]:
    """
    Calculate stock beta from regression vs 沪深300 index.

    Beta = Cov(stock_returns, market_returns) / Var(market_returns)

    Args:
        ticker: Stock ticker
        months: Lookback period (default 60 months)

    Returns:
        Beta value, or None if insufficient data

    TODO: Implement 60-month regression
    - Fetch monthly returns for stock and 沪深300
    - Perform linear regression
    - Apply 1% winsorization to trim extremes
    """
    # TODO: Implement beta calculation
    # For now, return None to trigger fallback to industry default
    logger.debug(f"[WACC] Beta calculation not yet implemented for {ticker}, will use industry default")
    return None


def get_interest_bearing_debt(ticker: str) -> float:
    """
    Calculate interest-bearing debt from balance sheet.

    D = Short-term loans + Long-term loans + Bonds payable +
        Lease liabilities + Current portion of long-term debt

    Args:
        ticker: Stock ticker

    Returns:
        Total interest-bearing debt, or 0.0 if unavailable
    """
    balance_rows = get_balance_sheets(ticker, limit=1, period_type="annual")

    if not balance_rows:
        logger.warning(f"[WACC] No balance sheet data for {ticker}")
        return 0.0

    bs = balance_rows[0]

    # Extract debt components
    short_term_debt = _safe(bs.get("short_term_debt")) or 0.0
    long_term_debt = _safe(bs.get("long_term_debt")) or 0.0
    bonds_payable = _safe(bs.get("bonds_payable")) or 0.0
    # Note: lease_liabilities and current_portion_lt_debt may not be in our schema
    # Fallback to total_liabilities if specific debt items unavailable

    total_debt = short_term_debt + long_term_debt + bonds_payable

    # If total_debt is 0, try using total_liabilities as approximation
    if total_debt == 0:
        total_liabilities = _safe(bs.get("total_liabilities")) or 0.0
        total_equity = _safe(bs.get("total_equity")) or 0.0
        # Use conservative estimate: assume 50% of liabilities are interest-bearing
        # (rest are payables, deferred revenue, etc.)
        if total_liabilities > 0 and total_equity > 0:
            total_debt = total_liabilities * 0.5
            logger.debug(
                f"[WACC] Using 50% of total liabilities as debt estimate: {total_debt/1e8:.2f}亿"
            )

    logger.debug(f"[WACC] {ticker} interest-bearing debt: {total_debt/1e8:.2f}亿元")
    return total_debt


def calculate_cost_of_debt(ticker: str) -> float:
    """
    Calculate cost of debt: rd = (interest expense + capitalized interest) / average debt.

    Args:
        ticker: Stock ticker

    Returns:
        Cost of debt as decimal, or fallback 5% if unavailable
    """
    income_rows = get_income_statements(ticker, limit=2, period_type="annual")

    if not income_rows:
        logger.warning(f"[WACC] No income statement for {ticker}, using fallback rd=5%")
        return 0.05

    # Get interest expense (may be stored as financial_expenses)
    interest_expense = _safe(income_rows[0].get("interest_expense"))
    financial_expenses = _safe(income_rows[0].get("financial_expenses"))

    # Use whichever is available
    interest = interest_expense if interest_expense else (financial_expenses or 0.0)
    interest = abs(interest)  # Ensure positive

    # Get average debt (current + previous year) / 2
    current_debt = get_interest_bearing_debt(ticker)
    # Approximation: assume previous year debt is similar (could fetch from historical BS)
    avg_debt = current_debt  # Simplified: use current year only

    if avg_debt > 0 and interest > 0:
        rd = interest / avg_debt
        logger.debug(f"[WACC] {ticker} cost of debt: {rd*100:.2f}%")
        return min(0.15, rd)  # Cap at 15% to avoid outliers
    else:
        logger.debug(f"[WACC] {ticker} using fallback rd=5% (no debt or interest data)")
        return 0.05


def calculate_effective_tax_rate(ticker: str) -> float:
    """
    Calculate effective tax rate: Tc = actual tax paid / profit before tax.

    NOT the statutory 25% rate — use actual tax paid.

    Args:
        ticker: Stock ticker

    Returns:
        Effective tax rate as decimal, or 0.25 fallback if unavailable
    """
    income_rows = get_income_statements(ticker, limit=1, period_type="annual")

    if not income_rows:
        logger.warning(f"[WACC] No income statement for {ticker}, using Tc=25% fallback")
        return 0.25

    profit_before_tax = _safe(income_rows[0].get("profit_before_tax"))
    income_tax_expense = _safe(income_rows[0].get("income_tax_expense"))

    if profit_before_tax and income_tax_expense and profit_before_tax > 0:
        tc = abs(income_tax_expense) / profit_before_tax
        # Cap at 40% to avoid outliers from special tax situations
        tc = min(0.40, max(0.0, tc))
        logger.debug(f"[WACC] {ticker} effective tax rate: {tc*100:.1f}%")
        return tc
    else:
        logger.debug(f"[WACC] {ticker} using statutory tax rate: 25%")
        return 0.25


def calculate_cost_of_equity(
    ticker: str,
    industry: str = "default",
    beta: Optional[float] = None,
) -> float:
    """
    Calculate cost of equity using CAPM: re = rf + β × MRP.

    Args:
        ticker: Stock ticker
        industry: Industry classification for default beta
        beta: Pre-calculated beta (if None, will calculate or use default)

    Returns:
        Cost of equity as decimal
    """
    rf = get_risk_free_rate()

    # Get beta (calculated or default)
    if beta is None:
        beta = calculate_beta(ticker)

    # Fallback to industry default beta if calculation failed
    if beta is None:
        thresholds = get_scoring_thresholds(industry)
        beta = thresholds.get("default_beta", 1.0)
        logger.debug(f"[WACC] {ticker} using industry default beta: {beta}")

    re = rf + beta * MRP
    logger.debug(
        f"[WACC] {ticker} cost of equity: {re*100:.2f}% "
        f"(rf={rf*100:.2f}%, β={beta:.2f}, MRP={MRP*100:.1f}%)"
    )
    return re


def calculate_wacc(
    ticker: str,
    market: str,
    industry: str = "default",
    current_price: Optional[float] = None,
) -> dict:
    """
    Calculate WACC and return detailed breakdown.

    WACC = E/(E+D) × re + D/(E+D) × rd × (1-Tc)

    Args:
        ticker: Stock ticker
        market: Market code (for fetching shares outstanding)
        industry: Industry classification
        current_price: Current stock price (if None, will fetch)

    Returns:
        Dictionary with:
        - wacc: Final WACC value
        - re: Cost of equity
        - rd: Cost of debt
        - tc: Effective tax rate
        - beta: Beta used
        - equity_value: Market value of equity (E)
        - debt_value: Interest-bearing debt (D)
        - equity_weight: E/(E+D)
        - debt_weight: D/(E+D)
        - fallback_used: Boolean indicating if fallback was used
    """
    # Get current price
    if current_price is None:
        price_rows = get_latest_prices(ticker, limit=1)
        if price_rows:
            current_price = _safe(price_rows[0].get("close"))

    if not current_price or current_price <= 0:
        logger.error(f"[WACC] {ticker} has invalid price, cannot calculate WACC")
        # Return industry midpoint as fallback
        thresholds = get_scoring_thresholds(industry)
        wacc_range = thresholds.get("wacc_range", [0.08, 0.10])
        fallback_wacc = sum(wacc_range) / 2
        return {
            "wacc": fallback_wacc,
            "re": None,
            "rd": None,
            "tc": None,
            "beta": None,
            "equity_value": None,
            "debt_value": None,
            "equity_weight": None,
            "debt_weight": None,
            "fallback_used": True,
            "note": "Price unavailable, using industry midpoint WACC",
        }

    # Get shares outstanding
    income_rows = get_income_statements(ticker, limit=1, period_type="annual")
    shares = None
    if income_rows:
        shares = _safe(income_rows[0].get("shares_outstanding"))
        # Derive from net_income / eps if not stored
        if not shares:
            ni = _safe(income_rows[0].get("net_income"))
            eps = _safe(income_rows[0].get("eps"))
            if ni and eps and eps != 0:
                shares = ni / eps

    if not shares or shares <= 0:
        logger.error(f"[WACC] {ticker} shares outstanding unavailable")
        thresholds = get_scoring_thresholds(industry)
        wacc_range = thresholds.get("wacc_range", [0.08, 0.10])
        fallback_wacc = sum(wacc_range) / 2
        return {
            "wacc": fallback_wacc,
            "re": None,
            "rd": None,
            "tc": None,
            "beta": None,
            "equity_value": None,
            "debt_value": None,
            "equity_weight": None,
            "debt_weight": None,
            "fallback_used": True,
            "note": "Shares outstanding unavailable, using industry midpoint WACC",
        }

    # Calculate E and D
    equity_value = shares * current_price  # Market value of equity
    debt_value = get_interest_bearing_debt(ticker)

    total_capital = equity_value + debt_value

    if total_capital == 0:
        logger.error(f"[WACC] {ticker} total capital is zero")
        thresholds = get_scoring_thresholds(industry)
        wacc_range = thresholds.get("wacc_range", [0.08, 0.10])
        fallback_wacc = sum(wacc_range) / 2
        return {
            "wacc": fallback_wacc,
            "re": None,
            "rd": None,
            "tc": None,
            "beta": None,
            "equity_value": equity_value,
            "debt_value": debt_value,
            "equity_weight": None,
            "debt_weight": None,
            "fallback_used": True,
            "note": "Total capital is zero, using industry midpoint WACC",
        }

    equity_weight = equity_value / total_capital
    debt_weight = debt_value / total_capital

    # Calculate components
    beta = calculate_beta(ticker)
    re = calculate_cost_of_equity(ticker, industry, beta)
    rd = calculate_cost_of_debt(ticker)
    tc = calculate_effective_tax_rate(ticker)

    # If beta calculation failed, get from industry
    if beta is None:
        thresholds = get_scoring_thresholds(industry)
        beta = thresholds.get("default_beta", 1.0)

    # Calculate WACC
    wacc = equity_weight * re + debt_weight * rd * (1 - tc)

    # Validate against industry range
    thresholds = get_scoring_thresholds(industry)
    wacc_range = thresholds.get("wacc_range", [0.06, 0.12])

    if wacc < wacc_range[0] or wacc > wacc_range[1]:
        logger.warning(
            f"[WACC] {ticker} calculated WACC {wacc*100:.2f}% outside industry range "
            f"{wacc_range[0]*100:.1f}%-{wacc_range[1]*100:.1f}%"
        )

    logger.info(
        f"[WACC] {ticker} final WACC: {wacc*100:.2f}% "
        f"(E={equity_weight*100:.0f}%, D={debt_weight*100:.0f}%, "
        f"re={re*100:.2f}%, rd={rd*100:.2f}%, Tc={tc*100:.0f}%)"
    )

    return {
        "wacc": wacc,
        "re": re,
        "rd": rd,
        "tc": tc,
        "beta": beta,
        "equity_value": equity_value,
        "debt_value": debt_value,
        "equity_weight": equity_weight,
        "debt_weight": debt_weight,
        "fallback_used": False,
        "note": None,
    }


def generate_sensitivity_matrix(
    base_fcf: float,
    wacc_current: float,
    shares: float,
    wacc_range: tuple[float, float] = (0.06, 0.12),
    growth_range: tuple[float, float] = (0.0, 0.15),
    terminal_growth: float = 0.03,
    years: int = 10,
) -> dict:
    """
    Generate sensitivity matrix: DCF value per share at different WACC × FCF growth.

    Args:
        base_fcf: Base free cash flow
        wacc_current: Current WACC assumption
        shares: Shares outstanding
        wacc_range: (min, max) WACC to test
        growth_range: (min, max) FCF growth to test
        terminal_growth: Terminal growth rate
        years: Projection years

    Returns:
        Dictionary with:
        - matrix: 2D array of DCF values per share
        - wacc_values: WACC values tested
        - growth_values: Growth rates tested
        - current_wacc: Highlighted current WACC
        - current_growth: Highlighted current growth
    """
    # Generate test values (7 points each)
    wacc_values = np.linspace(wacc_range[0], wacc_range[1], 7)
    growth_values = np.linspace(growth_range[0], growth_range[1], 7)

    # Calculate DCF for each combination
    matrix = np.zeros((len(wacc_values), len(growth_values)))

    for i, wacc in enumerate(wacc_values):
        for j, growth in enumerate(growth_values):
            # DCF calculation
            pv = 0.0
            fcf = base_fcf
            for yr in range(1, years + 1):
                fcf *= (1 + growth)
                pv += fcf / ((1 + wacc) ** yr)

            # Terminal value
            terminal_fcf = fcf * (1 + terminal_growth)
            if wacc > terminal_growth:
                terminal_value = terminal_fcf / (wacc - terminal_growth)
                pv += terminal_value / ((1 + wacc) ** years)

            # Per share value
            matrix[i, j] = pv / shares if shares > 0 else 0.0

    return {
        "matrix": matrix.tolist(),
        "wacc_values": wacc_values.tolist(),
        "growth_values": growth_values.tolist(),
        "current_wacc": wacc_current,
    }


def generate_sensitivity_heatmap(
    base_fcf: float,
    shares: float,
    current_price: float,
    wacc_range: tuple[float, float] = (0.06, 0.14),
    growth_range: tuple[float, float] = (0.00, 0.08),
    terminal_growth: float = 0.025,
    years: int = 10,
    grid_size: int = 7,
) -> dict:
    """
    Generate 2D WACC × Growth heatmap with implied market assumptions.

    P2 Enhancement: Shows what WACC/growth combination the market is implying
    at the current stock price. Helps investors understand "what the market is betting on".

    Args:
        base_fcf: Base free cash flow (latest year)
        shares: Shares outstanding
        current_price: Current market price per share
        wacc_range: (min, max) WACC range to test
        growth_range: (min, max) perpetual growth range to test
        terminal_growth: Terminal growth rate for explicit period
        years: Explicit forecast period
        grid_size: Number of grid points per axis

    Returns:
        Dictionary with:
        - matrix: 2D array of DCF values [wacc_idx][growth_idx]
        - wacc_axis: WACC values tested
        - growth_axis: Growth values tested
        - implied_wacc: WACC implied by current market price
        - implied_growth: Growth implied by current market price
        - implied_cell: (wacc_idx, growth_idx) of cell closest to current price
        - current_price: Input current price
        - valuation_zones: Classification of each cell (undervalued/fair/overvalued)
    """
    wacc_steps = np.linspace(wacc_range[0], wacc_range[1], grid_size)
    growth_steps = np.linspace(growth_range[0], growth_range[1], grid_size)

    matrix = np.zeros((grid_size, grid_size))
    valuation_zones = [[None for _ in range(grid_size)] for _ in range(grid_size)]

    for i, wacc in enumerate(wacc_steps):
        for j, growth in enumerate(growth_steps):
            # DCF calculation with explicit forecast + terminal value
            pv = 0.0
            fcf = base_fcf

            # Explicit period (use terminal_growth for FCF growth during explicit period)
            for yr in range(1, years + 1):
                fcf *= (1 + terminal_growth)
                pv += fcf / ((1 + wacc) ** yr)

            # Terminal value using perpetual growth (j-axis value)
            if wacc > growth:
                terminal_fcf = fcf * (1 + growth)
                terminal_value = terminal_fcf / (wacc - growth)
                pv += terminal_value / ((1 + wacc) ** years)
            else:
                # Invalid: growth >= wacc leads to infinite value
                pv = float('inf')

            # Per share value
            if shares > 0 and not np.isinf(pv):
                per_share = pv / shares
            else:
                per_share = 0.0

            matrix[i, j] = per_share

            # Classify valuation zone
            if per_share > 0:
                premium = (per_share - current_price) / current_price
                if premium > 0.20:
                    valuation_zones[i][j] = "undervalued"  # >20% upside
                elif premium < -0.20:
                    valuation_zones[i][j] = "overvalued"   # >20% downside
                else:
                    valuation_zones[i][j] = "fair"          # ±20%

    # Find implied assumptions (cell closest to current price)
    valid_matrix = np.where(np.isinf(matrix), np.nan, matrix)
    diff = np.abs(valid_matrix - current_price)
    min_idx = np.unravel_index(np.nanargmin(diff), diff.shape)

    implied_wacc = wacc_steps[min_idx[0]]
    implied_growth = growth_steps[min_idx[1]]

    logger.info(
        f"[WACC] Sensitivity heatmap: current_price=¥{current_price:.2f}, "
        f"implied WACC={implied_wacc*100:.1f}%, implied growth={implied_growth*100:.1f}%"
    )

    return {
        "matrix": matrix.tolist(),
        "wacc_axis": wacc_steps.tolist(),
        "growth_axis": growth_steps.tolist(),
        "implied_wacc": float(implied_wacc),
        "implied_growth": float(implied_growth),
        "implied_cell": (int(min_idx[0]), int(min_idx[1])),
        "current_price": current_price,
        "valuation_zones": valuation_zones,
    }


def format_sensitivity_heatmap(heatmap_data: dict) -> str:
    """
    Format sensitivity heatmap as markdown table with visual indicators.

    Visual indicators:
    - 🟢 Green: >20% above current price (undervalued)
    - 🟡 Yellow: within ±20% of current price (fair)
    - 🔴 Red: >20% below current price (overvalued)
    - ⭐ Star: implied assumptions cell (closest to current price)

    Args:
        heatmap_data: Output from generate_sensitivity_heatmap()

    Returns:
        Markdown formatted heatmap string
    """
    matrix = heatmap_data["matrix"]
    wacc_axis = heatmap_data["wacc_axis"]
    growth_axis = heatmap_data["growth_axis"]
    implied_cell = heatmap_data["implied_cell"]
    current_price = heatmap_data["current_price"]
    zones = heatmap_data["valuation_zones"]

    lines = []
    lines.append("### 敏感性热图 (WACC × 永续增长率)\n")

    # Header row with growth values
    header = "| WACC \\ 增长 |"
    for g in growth_axis:
        header += f" {g*100:.1f}% |"
    lines.append(header)

    # Separator
    sep = "|:------------|"
    for _ in growth_axis:
        sep += ":-------:|"
    lines.append(sep)

    # Data rows
    for i, wacc in enumerate(wacc_axis):
        row = f"| **{wacc*100:.1f}%** |"
        for j, _ in enumerate(growth_axis):
            value = matrix[i][j]
            zone = zones[i][j]
            is_implied = (i, j) == tuple(implied_cell)

            # Format cell value
            if value == 0 or value == float('inf'):
                cell = "—"
            else:
                # Add indicator
                if is_implied:
                    indicator = "⭐"
                elif zone == "undervalued":
                    indicator = "🟢"
                elif zone == "overvalued":
                    indicator = "🔴"
                else:
                    indicator = "🟡"

                cell = f"{indicator}¥{value:.0f}"

            row += f" {cell} |"
        lines.append(row)

    # Add legend and implied assumptions
    lines.append("")
    lines.append(f"**当前市价**: ¥{current_price:.2f}")
    lines.append(
        f"**市场隐含假设**: WACC={heatmap_data['implied_wacc']*100:.1f}%, "
        f"永续增长={heatmap_data['implied_growth']*100:.1f}% (⭐标记)"
    )
    lines.append("")
    lines.append("图例: 🟢低估(>20%上涨空间) | 🟡合理(±20%) | 🔴高估(>20%下跌风险)")

    return "\n".join(lines)
