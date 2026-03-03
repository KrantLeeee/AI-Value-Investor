# AI Value Investor

AI-powered investment research assistant for value investing.

Automates: fundamental analysis, deep research reports, factor screening, portfolio management.

## Quick Start

```bash
# Install dependencies
poetry install

# Set up API keys
cp .env.example .env
# Edit .env with your API keys

# Fetch data for a stock
invest fetch --ticker 601808.SH

# Generate a research report
invest report --ticker 601808.SH

# Run opportunity scan
invest scan

# View portfolio
invest portfolio
```

## Architecture

See `References/Docs/Tech Design/tech-design-v1.md` for full technical design.

## Commands

| Command | Description |
|---------|-------------|
| `fetch` | Fetch market data and financial statements |
| `ingest` | Parse manually uploaded documents |
| `scan` | Run factor screening on watchlist |
| `report` | Generate deep research report |
| `invest` | Get position sizing recommendation |
| `profile` | Manage investor profile |
| `portfolio` | View current holdings |
| `status` | Show system status and data freshness |
| `backtest` | Run factor backtests |
