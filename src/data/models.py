"""Pydantic data models for the entire system."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


# ── Market types ──────────────────────────────────────────────────────────────

MarketType = Literal["a_share", "hk", "us"]
PeriodType = Literal["annual", "quarterly"]
SignalType = Literal["bullish", "neutral", "bearish"]
DocType = Literal["annual_report", "research_report", "news", "other"]
IngestStatus = Literal["pending", "success", "partial", "failed"]


# ── Raw market data ───────────────────────────────────────────────────────────

class DailyPrice(BaseModel):
    ticker: str
    market: MarketType
    date: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    volume: int | None = None
    source: str


class IncomeStatement(BaseModel):
    ticker: str
    period_end_date: date
    period_type: PeriodType
    revenue: float | None = None
    cost_of_revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    ebitda: float | None = None
    eps: float | None = None
    eps_diluted: float | None = None
    shares_outstanding: float | None = None
    source: str


class BalanceSheet(BaseModel):
    ticker: str
    period_end_date: date
    period_type: PeriodType
    total_assets: float | None = None
    total_liabilities: float | None = None
    total_equity: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    cash_and_equivalents: float | None = None
    total_debt: float | None = None
    book_value_per_share: float | None = None
    source: str


class CashFlow(BaseModel):
    ticker: str
    period_end_date: date
    period_type: PeriodType
    operating_cash_flow: float | None = None
    capital_expenditure: float | None = None
    free_cash_flow: float | None = None
    dividends_paid: float | None = None
    depreciation: float | None = None
    source: str


class FinancialMetrics(BaseModel):
    """Key financial ratios and metrics — computed from statements or from APIs."""
    ticker: str
    date: date
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    ps_ratio: float | None = None
    roe: float | None = None
    roa: float | None = None
    debt_to_equity: float | None = None
    current_ratio: float | None = None
    dividend_yield: float | None = None
    operating_margin: float | None = None
    gross_margin: float | None = None
    revenue_growth: float | None = None
    net_income_growth: float | None = None
    fcf_per_share: float | None = None
    market_cap: float | None = None
    enterprise_value: float | None = None
    source: str


# ── News / Sentiment ──────────────────────────────────────────────────────────

class NewsItem(BaseModel):
    ticker: str
    title: str
    publish_date: datetime
    url: str | None = None
    source: str
    sentiment: Literal["positive", "negative", "neutral"] | None = None
    sentiment_score: float | None = None  # -1.0 to 1.0


# ── Manual documents ──────────────────────────────────────────────────────────

class ManualDoc(BaseModel):
    ticker: str
    file_name: str
    file_path: str
    doc_type: DocType = "other"
    extracted_text: str | None = None
    text_length: int = 0
    status: IngestStatus = "pending"


# ── Agent signals ─────────────────────────────────────────────────────────────

class AgentSignal(BaseModel):
    ticker: str
    agent_name: str
    signal: SignalType
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = ""
    metrics: dict = Field(default_factory=dict)  # key metrics snapshot
    created_at: datetime = Field(default_factory=datetime.now)


# ── Screening signal ──────────────────────────────────────────────────────────

class ScreeningSignal(BaseModel):
    ticker: str
    name: str = ""
    rule_name: str
    signal: Literal["opportunity", "alert"] = "opportunity"
    description: str = ""
    triggered_at: datetime = Field(default_factory=datetime.now)
    metrics: dict = Field(default_factory=dict)
    dcf_intrinsic_value: float | None = None
    margin_of_safety: float | None = None


# ── Portfolio ─────────────────────────────────────────────────────────────────

class Transaction(BaseModel):
    date: date
    action: Literal["buy", "sell", "add"]
    amount: float
    price: float
    shares: float


class Position(BaseModel):
    ticker: str
    market: MarketType
    total_invested: float
    avg_cost: float
    shares: float
    transactions: list[Transaction] = Field(default_factory=list)


class InvestorProfile(BaseModel):
    total_capital: float
    monthly_addition: float
    max_single_position_pct: float = 0.15
    max_total_risk_pct: float = 0.30
    investment_horizon: Literal["short", "medium", "long"] = "long"


class PositionRecommendation(BaseModel):
    ticker: str
    recommendation: Literal["invest", "hold", "wait"]
    suggested_amount: float
    suggested_pct: float
    position_limit: float
    annualized_volatility: float
    concentration_after: float
    volatility_exposure_after: float
    llm_reasoning: str = ""
    confidence: float = 0.0
