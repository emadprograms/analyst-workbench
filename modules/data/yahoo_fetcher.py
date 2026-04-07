"""
Yahoo Finance Data Fetcher for Temp Stock Cards.

Fetches 5-minute intraday bars from Yahoo Finance and transforms them
into an Impact Context Card-compatible structure so the AI prompt format
stays consistent with the regular company card pipeline.
"""
from __future__ import annotations

import time
import json
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta, time as dt_time
from pytz import timezone as pytz_timezone

from modules.core.logger import AppLogger
from modules.analysis.impact_engine import (
    _calculate_volume_profile,
    _analyze_slice_migration,
    _find_key_volume_events,
    _detect_impact_levels,
)

US_EASTERN = pytz_timezone("US/Eastern")
MARKET_OPEN_TIME = dt_time(9, 30)
MARKET_CLOSE_TIME = dt_time(16, 0)

# Delay between Yahoo Finance requests to avoid rate limiting
YAHOO_REQUEST_DELAY_SECONDS = 1.5


def _download_intraday(ticker: str, logger: AppLogger) -> pd.DataFrame | None:
    """
    Downloads 5-minute intraday bars for the past 5 trading days from Yahoo Finance.
    Returns a DataFrame with columns: timestamp, Open, High, Low, Close, Volume, dt_eastern.
    """
    import yfinance as yf

    try:
        logger.log(f"   📡 Fetching 5-min bars from Yahoo Finance for {ticker}...")
        data = yf.download(
            ticker,
            period="5d",
            interval="5m",
            progress=False,
            auto_adjust=True,
        )

        if data is None or data.empty:
            logger.log(f"   ⚠️ Yahoo Finance returned no data for {ticker}")
            return None

        # Handle MultiIndex columns (yfinance can return multi-level columns)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        df = data.reset_index()
        df.rename(columns={"Datetime": "timestamp", "Date": "timestamp"}, inplace=True)

        # Ensure timezone-aware timestamps
        if df["timestamp"].dt.tz is None:
            df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
        else:
            df["timestamp"] = df["timestamp"].dt.tz_convert("UTC")

        df["dt_eastern"] = df["timestamp"].dt.tz_convert(US_EASTERN)

        # Ensure numeric columns
        for col in ["Open", "High", "Low", "Close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df.dropna(subset=["Close"], inplace=True)

        logger.log(
            f"   ✅ Got {len(df)} bars spanning "
            f"{df['dt_eastern'].iloc[0].strftime('%Y-%m-%d')} → "
            f"{df['dt_eastern'].iloc[-1].strftime('%Y-%m-%d')}"
        )
        return df.reset_index(drop=True)

    except Exception as e:
        logger.log(f"   ❌ Yahoo Finance error for {ticker}: {e}")
        return None


def _split_by_date(df: pd.DataFrame, target_date: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Splits a DataFrame into historical context (days before target_date)
    and today's data (target_date only).
    """
    df_date = df["dt_eastern"].dt.date

    today_df = df[df_date == target_date].copy()
    historical_df = df[df_date < target_date].copy()

    return historical_df, today_df


def _analyze_session(df: pd.DataFrame, ref_close: float = 0) -> dict:
    """
    Analyzes a session slice (pre-market, RTH, post-market) using the
    same algorithms as the Impact Engine.
    """
    if df.empty:
        return {"status": "No Data"}

    avg_p = df["Close"].mean()
    rejections = _detect_impact_levels(df, avg_p)
    migration = _analyze_slice_migration(df)

    high = df["High"].max()
    low = df["Low"].min()
    vol = df["Volume"].sum() if "Volume" in df.columns else 0

    session_open = df["Open"].iloc[0]
    gap_pct = None
    if ref_close and ref_close > 0:
        gap_pct = round(((session_open - ref_close) / ref_close) * 100, 2)

    poc, vah, val = _calculate_volume_profile(df)
    key_vol_events = _find_key_volume_events(df)

    res = {
        "status": "Active",
        "session_open": round(float(session_open), 2),
        "high": round(float(high), 2),
        "low": round(float(low), 2),
        "volume_approx": int(vol),
        "volume_profile": {"POC": poc, "VAH": vah, "VAL": val},
        "key_volume_events": key_vol_events,
        "key_levels": rejections,
        "value_migration": migration,
    }
    if gap_pct is not None:
        res["gap_pct"] = gap_pct

    return res


def _build_today_impact_card(today_df: pd.DataFrame, prev_close: float, ticker: str, date_str: str) -> dict:
    """
    Builds an Impact Context Card from today's intraday data,
    matching the format produced by the regular Impact Engine.
    """
    if today_df.empty:
        return {"status": "No Data", "meta": {"ticker": ticker, "date": date_str}}

    # Slice sessions by Eastern Time
    df_pre = today_df[today_df["dt_eastern"].dt.time < MARKET_OPEN_TIME].copy()
    df_rth = today_df[
        (today_df["dt_eastern"].dt.time >= MARKET_OPEN_TIME)
        & (today_df["dt_eastern"].dt.time < MARKET_CLOSE_TIME)
    ].copy()
    df_post = today_df[today_df["dt_eastern"].dt.time >= MARKET_CLOSE_TIME].copy()

    context_card = {
        "meta": {
            "ticker": ticker,
            "date": date_str,
            "data_points": len(today_df),
            "source": "Yahoo Finance (5-min intraday)",
        },
        "reference": {
            "yesterday_close": round(float(prev_close), 2) if prev_close else 0,
        },
        "sessions": {
            "pre_market": _analyze_session(df_pre, ref_close=prev_close),
            "regular_hours": _analyze_session(df_rth, ref_close=prev_close),
            "post_market": _analyze_session(df_post, ref_close=prev_close),
        },
    }
    return context_card


def _build_historical_summary(historical_df: pd.DataFrame, ticker: str) -> list[dict]:
    """
    Builds a per-day summary of the historical context (4 prior trading days).
    Each entry contains: date, OHLCV, change_pct, detected key levels.
    """
    if historical_df.empty:
        return []

    summaries = []
    grouped = historical_df.groupby(historical_df["dt_eastern"].dt.date)

    prev_close = None
    for day_date, day_df in sorted(grouped):
        day_open = float(day_df["Open"].iloc[0])
        day_high = float(day_df["High"].max())
        day_low = float(day_df["Low"].min())
        day_close = float(day_df["Close"].iloc[-1])
        day_vol = int(day_df["Volume"].sum()) if "Volume" in day_df.columns else 0
        change_pct = round(((day_close - day_open) / day_open) * 100, 2) if day_open > 0 else 0

        # Detect key levels from 5-min bars
        avg_p = day_df["Close"].mean()
        key_levels = _detect_impact_levels(day_df, avg_p)

        # Volume profile
        poc, vah, val = _calculate_volume_profile(day_df)

        gap_pct = None
        if prev_close and prev_close > 0:
            gap_pct = round(((day_open - prev_close) / prev_close) * 100, 2)

        entry = {
            "date": str(day_date),
            "open": round(day_open, 2),
            "high": round(day_high, 2),
            "low": round(day_low, 2),
            "close": round(day_close, 2),
            "volume": day_vol,
            "change_pct": change_pct,
            "volume_profile": {"POC": poc, "VAH": vah, "VAL": val},
            "key_levels": key_levels[:4],  # Top 4 levels per day
        }
        if gap_pct is not None:
            entry["gap_pct"] = gap_pct

        summaries.append(entry)
        prev_close = day_close

    return summaries


def _numpy_safe(obj):
    """JSON serializer for numpy types."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def fetch_intraday_data_for_temp_card(
    ticker: str,
    target_date: date,
    logger: AppLogger,
) -> dict | None:
    """
    Main entry point: fetches Yahoo Finance data and returns a structured dict
    ready to be plugged into the temp company card AI prompt.

    Returns:
        {
            "today_impact_card": dict (Impact Context Card for target_date),
            "historical_summary": list[dict] (per-day OHLCV for prior 4 days),
            "is_partial": bool (True if market hasn't closed yet for target_date),
            "data_range": str (e.g., "2026-04-01 → 2026-04-04"),
        }
        or None if fetching fails.
    """
    df = _download_intraday(ticker, logger)
    if df is None or df.empty:
        return None

    # Add a delay to avoid Yahoo Finance rate limiting
    time.sleep(YAHOO_REQUEST_DELAY_SECONDS)

    # Split into historical vs today
    historical_df, today_df = _split_by_date(df, target_date)

    if today_df.empty:
        logger.log(f"   ⚠️ No data for {ticker} on {target_date}. Available dates: "
                    f"{sorted(df['dt_eastern'].dt.date.unique())}")
        # Try the most recent available date instead
        available_dates = sorted(df["dt_eastern"].dt.date.unique())
        if available_dates:
            fallback_date = available_dates[-1]
            logger.log(f"   🔄 Falling back to most recent date: {fallback_date}")
            historical_df, today_df = _split_by_date(df, fallback_date)
            if today_df.empty:
                return None
        else:
            return None

    # Determine previous close from historical data or today's first bar
    prev_close = 0
    if not historical_df.empty:
        prev_close = float(historical_df["Close"].iloc[-1])
    
    # Check if market has closed for today
    last_bar_time = today_df["dt_eastern"].iloc[-1]
    is_partial = last_bar_time.time() < MARKET_CLOSE_TIME

    # Build outputs
    historical_summary = _build_historical_summary(historical_df, ticker)
    today_impact_card = _build_today_impact_card(
        today_df, prev_close, ticker, target_date.isoformat()
    )

    # Data range string
    all_dates = sorted(df["dt_eastern"].dt.date.unique())
    data_range = f"{all_dates[0]} → {all_dates[-1]}" if all_dates else "N/A"

    result = {
        "today_impact_card": today_impact_card,
        "historical_summary": historical_summary,
        "is_partial": is_partial,
        "data_range": data_range,
    }

    if is_partial:
        logger.log(
            f"   ⚠️ PARTIAL DATA: Market has not closed for {target_date}. "
            f"Last bar at {last_bar_time.strftime('%H:%M %Z')}."
        )
    else:
        logger.log(f"   ✅ Full session data available for {ticker} on {target_date}")

    return result


def fetch_movers_snapshot(tickers: list[str], logger: AppLogger) -> dict[str, dict]:
    """
    Lightweight batch fetch of daily bars for multiple tickers.

    Returns a dict keyed by ticker with programmatically calculated:
        prev_close, last_price, gap_pct, volume, avg_volume, rvol

    Uses a single yfinance batch download for speed (~2-3s for 15 tickers).
    All numerical calculations are done in Python — never by AI.
    """
    import yfinance as yf

    if not tickers:
        return {}

    results: dict[str, dict] = {}

    try:
        logger.log(f"   📡 Batch-fetching daily bars for {len(tickers)} tickers from Yahoo Finance...")

        # Single batch download — much faster than individual calls
        data = yf.download(
            tickers,
            period="5d",
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker" if len(tickers) > 1 else "column",
        )

        if data is None or data.empty:
            logger.log("   ⚠️ Yahoo Finance returned no data for batch download")
            return {}

        for ticker in tickers:
            try:
                # Extract per-ticker data depending on single vs multi-ticker format
                if len(tickers) == 1:
                    ticker_df = data.copy()
                else:
                    if ticker not in data.columns.get_level_values(0):
                        logger.log(f"   ⚠️ No data for {ticker} in batch result")
                        continue
                    ticker_df = data[ticker].copy()

                # Handle MultiIndex columns
                if isinstance(ticker_df.columns, pd.MultiIndex):
                    ticker_df.columns = ticker_df.columns.get_level_values(0)

                ticker_df = ticker_df.dropna(subset=["Close"])

                if len(ticker_df) < 2:
                    logger.log(f"   ⚠️ Insufficient data for {ticker} (need at least 2 days)")
                    continue

                # Last completed day's close = second-to-last row
                # Today's / most recent data = last row
                prev_close = float(ticker_df["Close"].iloc[-2])
                last_price = float(ticker_df["Close"].iloc[-1])
                today_volume = int(ticker_df["Volume"].iloc[-1]) if "Volume" in ticker_df.columns else 0

                # Average volume from prior days (excluding today)
                prior_volumes = ticker_df["Volume"].iloc[:-1] if "Volume" in ticker_df.columns else pd.Series([0])
                avg_volume = int(prior_volumes.mean()) if len(prior_volumes) > 0 and prior_volumes.mean() > 0 else 0

                # Programmatic calculations — never AI
                gap_pct = round(((last_price - prev_close) / prev_close) * 100, 2) if prev_close > 0 else 0.0
                rvol = round(today_volume / avg_volume, 1) if avg_volume > 0 else 0.0

                results[ticker] = {
                    "prev_close": round(prev_close, 2),
                    "last_price": round(last_price, 2),
                    "gap_pct": gap_pct,
                    "volume": today_volume,
                    "avg_volume": avg_volume,
                    "rvol": rvol,
                }

            except Exception as e:
                logger.log(f"   ⚠️ Error processing {ticker}: {e}")
                continue

        logger.log(f"   ✅ Got market data for {len(results)}/{len(tickers)} tickers")

    except Exception as e:
        logger.log(f"   ❌ Yahoo Finance batch error: {e}")

    return results
