"""
config.py
---------
All knobs in one place, loaded from environment variables / a .env file so you
never hard-code secrets. Flip LIVE_TRADING to go from paper -> live (with a
deliberate safety confirmation in the engine).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass  # dotenv optional


def _bool(name, default="false"):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y")


@dataclass
class Settings:
    # --- Alpaca ---
    api_key: str = os.getenv("ALPACA_API_KEY", "")
    api_secret: str = os.getenv("ALPACA_API_SECRET", "")
    live_trading: bool = field(default_factory=lambda: _bool("LIVE_TRADING"))

    # --- Risk (defaults match risk.RiskConfig) ---
    starting_equity: float = float(os.getenv("STARTING_EQUITY", "500"))
    risk_per_trade_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "0.01"))
    reward_risk_ratio: float = float(os.getenv("REWARD_RISK_RATIO", "2.0"))
    max_open_positions: int = int(os.getenv("MAX_OPEN_POSITIONS", "4"))

    # --- Approval gate ("ask me first") ---
    # Live: queue trades for your approval instead of auto-executing (STRONGLY
    # recommended; set false only when you fully trust it). Paper: auto-execute,
    # unless you want to practice the approval workflow with fake money.
    require_live_approval: bool = field(default_factory=lambda: _bool("REQUIRE_LIVE_APPROVAL", "true"))
    paper_require_approval: bool = field(default_factory=lambda: _bool("PAPER_REQUIRE_APPROVAL", "false"))

    # --- Universe: which stocks to scan. Keep it liquid + lower-priced for $500. ---
    universe: List[str] = field(default_factory=lambda: os.getenv(
        "UNIVERSE",
        "F,SOFI,PLTR,NIO,AMD,INTC,T,BAC,PFE,CSCO,HOOD,RIVN,SNAP,UBER,WBD,GRAB,VALE,RIG,CCL,KVUE"
    ).split(","))

    # --- Dividend universe: income-focused, lower-beta names. ---
    # All priced under ~$55 so they're reachable on a $500 account.
    # VZ, MO, BTI, ET, AGNC, NLY: high-yield income plays.
    # EPD, KMI: energy infrastructure MLPs with 7-8% yield.
    # STAG: monthly-paying industrial REIT.
    # ABBV: dividend-growth pharma (yield ~4%).
    # CVX: oil major with 4%+ yield and buybacks.
    # O: Realty Income "monthly dividend company" (~5% yield).
    dividend_universe: List[str] = field(default_factory=lambda: os.getenv(
        "DIVIDEND_UNIVERSE",
        "VZ,MO,BTI,ET,AGNC,NLY,EPD,KMI,STAG,ABBV,CVX,O"
    ).split(","))

    # Set to false to exclude dividend stocks from the scan.
    include_dividend_stocks: bool = field(default_factory=lambda: _bool("INCLUDE_DIVIDEND_STOCKS", "true"))

    # --- Post-split universe: high-momentum names that are now affordable
    # after large splits. NVDA (10:1), TSLA, AMZN (20:1), GOOG (20:1), SHOP (10:1).
    split_universe: List[str] = field(default_factory=lambda: os.getenv(
        "SPLIT_UNIVERSE",
        "NVDA,TSLA,AMZN,GOOG,SHOP"
    ).split(","))

    # Set to false to exclude post-split stocks from the scan.
    include_split_stocks: bool = field(default_factory=lambda: _bool("INCLUDE_SPLIT_STOCKS", "true"))

    # --- IPO universe: recent IPOs with limited price history.
    # Scanned with a shorter lookback (ipo_lookback_days) so limited history
    # doesn't cause data errors. Populate via IPO_UNIVERSE=TICK1,TICK2 in .env.
    ipo_universe: List[str] = field(default_factory=lambda: [
        s for s in os.getenv("IPO_UNIVERSE", "").split(",") if s.strip()
    ])

    # Set to false to exclude IPO stocks from the scan.
    include_ipo_stocks: bool = field(default_factory=lambda: _bool("INCLUDE_IPO_STOCKS", "true"))

    # Shorter lookback for IPOs — avoids data errors on stocks with < 1 year of history.
    ipo_lookback_days: int = int(os.getenv("IPO_LOOKBACK_DAYS", "180"))

    # --- Data / scheduling ---
    bar_timeframe: str = os.getenv("BAR_TIMEFRAME", "1Day")   # "1Day" swing, "15Min" intraday
    lookback_days: int = int(os.getenv("LOOKBACK_DAYS", "400"))

    # --- Google Sheets (optional) ---
    gsheet_id: str = os.getenv("GSHEET_ID", "")
    gsheet_creds: str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")

    # --- Storage ---
    db_path: str = os.getenv("DB_PATH", "microbot.db")

    def assert_keys(self):
        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "Missing ALPACA_API_KEY / ALPACA_API_SECRET. Copy .env.example "
                "to .env and fill in your PAPER keys first."
            )


settings = Settings()
