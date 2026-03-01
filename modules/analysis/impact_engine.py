import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timedelta, time as dt_time
from pytz import timezone as pytz_timezone
from modules.core.logger import AppLogger
from modules.data.db_utils import get_price_db_connection # <-- NEW

US_EASTERN = pytz_timezone('US/Eastern')
MARKET_OPEN_TIME = dt_time(9, 30)
MARKET_CLOSE_TIME = dt_time(16, 0)

# --- DB FETCHING UTILITIES ---

def get_latest_price_details(client_unused, ticker: str, cutoff_str: str, logger: AppLogger) -> tuple[float | None, str | None]:
    query = "SELECT close, timestamp FROM market_data WHERE symbol = ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1"
    conn = None
    try:
        conn = get_price_db_connection()
        if not conn: return None, None
        rs = conn.execute(query, [ticker, cutoff_str])
        if rs.rows:
            return rs.rows[0][0], rs.rows[0][1]
        return None, None
    except Exception as e:
        logger.log(f"DB Read Error {ticker}: {e}")
        return None, None
    finally:
        if conn: conn.close()

def get_session_bars_from_db(client_unused, epic: str, benchmark_date: str, cutoff_str: str, logger: AppLogger) -> pd.DataFrame | None:
    try:
        # We fetch the FULL day (00:00 to 23:59)
        query = """
            SELECT timestamp, open, high, low, close, volume, session
            FROM market_data
            WHERE symbol = ? AND date(timestamp) = ? AND timestamp <= ?
            ORDER BY timestamp ASC
        """
        conn = get_price_db_connection()
        if not conn: return None
        rs = conn.execute(query, [epic, benchmark_date, cutoff_str])
        if not rs.rows:
            conn.close()
            return None
        df = pd.DataFrame(
            rs.rows,
            columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'session_db'],
        )
        conn.close()

        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(str).str.replace('Z', '').str.replace(' ', 'T'))
        
        if df['timestamp'].dt.tz is None:
            df['timestamp'] = df['timestamp'].dt.tz_localize(pytz_timezone('UTC'))

        # dt_eastern is the display time (New York)
        df['dt_eastern'] = df['timestamp'].dt.tz_convert(US_EASTERN)
        
        for col in ['open', 'high', 'low', 'close']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        df.dropna(subset=['close'], inplace=True)
        
        # Normalize columns for the Engine
        df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}, inplace=True)
        return df.reset_index(drop=True)
    except Exception as e:
        logger.log(f"Data Error ({epic}): {e}")
        return None

def get_previous_session_stats(client_unused, ticker: str, current_date_str: str, logger: AppLogger) -> dict:
    """
    Fetches Yesterday's High, Low, and Close for context.
    """
    conn = None
    try:
        date_query = "SELECT DISTINCT date(timestamp) as d FROM market_data WHERE symbol = ? AND date(timestamp) < ? ORDER BY d DESC LIMIT 1"
        conn = get_price_db_connection()
        if not conn: return {"yesterday_close": 0, "yesterday_high": 0, "yesterday_low": 0}
        
        rs_date = conn.execute(date_query, [ticker, current_date_str])
        
        if not rs_date.rows:
            return {"yesterday_close": 0, "yesterday_high": 0, "yesterday_low": 0}
            
        prev_date = rs_date.rows[0][0]
        
        stats_query = """
            SELECT MAX(high), MIN(low), 
                   (SELECT close FROM market_data WHERE symbol = ? AND date(timestamp) = ? ORDER BY timestamp DESC LIMIT 1)
            FROM market_data 
            WHERE symbol = ? AND date(timestamp) = ?
        """
        rs = conn.execute(stats_query, [ticker, prev_date, ticker, prev_date])
        
        if rs.rows:
            r = rs.rows[0]
            return {
                "yesterday_high": r[0] if r[0] else 0,
                "yesterday_low": r[1] if r[1] else 0,
                "yesterday_close": r[2] if r[2] else 0,
                "date": prev_date
            }
        return {"yesterday_close": 0, "yesterday_high": 0, "yesterday_low": 0}
    except Exception:
        return {"yesterday_close": 0, "yesterday_high": 0, "yesterday_low": 0}
    finally:
        if conn: conn.close()


# ==========================================
# CORE ALGORITHMS (Helper)
# ==========================================

def _detect_impact_levels(df, avg_price):
    """Internal helper to allow session-based slicing."""
    if df.empty: return []

    proximity_threshold = max(0.10, avg_price * 0.0015)

    df = df.copy() # Safe copy
    df['is_peak'] = df['High'][(df['High'].shift(1) <= df['High']) & (df['High'].shift(-1) < df['High'])]
    df['is_valley'] = df['Low'][(df['Low'].shift(1) >= df['Low']) & (df['Low'].shift(-1) > df['Low'])]

    potential_peaks = df[df['is_peak'].notna()]
    potential_valleys = df[df['is_valley'].notna()]
    scored_levels = []

    # --- RESISTANCE ---
    for idx, row in potential_peaks.iterrows():
        pivot_price = row['High']
        loc_idx = df.index.get_loc(idx) if not isinstance(df.index, pd.RangeIndex) else idx
        
        future_df = df.iloc[loc_idx+1:]
        if future_df.empty: continue

        recovery_mask = future_df['High'] >= pivot_price
        if recovery_mask.any():
            recovery_time = recovery_mask.idxmax()
            interval_df = future_df.loc[:recovery_time]
            lowest_point = interval_df['Low'].min()
            magnitude = pivot_price - lowest_point
            
            if 'timestamp' in df.columns:
                 t1 = df.loc[idx]['timestamp']
                 t2 = df.loc[recovery_time]['timestamp']
                 duration_mins = (t2 - t1).total_seconds() / 60
            else:
                 duration_mins = (recovery_time - idx).total_seconds() / 60
        else:
            lowest_point = future_df['Low'].min()
            magnitude = pivot_price - lowest_point
            if 'timestamp' in df.columns:
                 t_start = df.loc[idx]['timestamp']
                 t_end = df.iloc[-1]['timestamp']
                 duration_mins = (t_end - t_start).total_seconds() / 60
            else:
                 duration_mins = len(future_df)

        magnitude_pct = (magnitude / pivot_price) * 100
        score = magnitude_pct * np.log1p(duration_mins)

        if magnitude > (avg_price * 0.00015): 
            scored_levels.append({
                "type": "RESISTANCE",
                "level": pivot_price,
                "score": score,
                "magnitude": magnitude,
                "duration": duration_mins
            })

    # --- SUPPORT ---
    for idx, row in potential_valleys.iterrows():
        pivot_price = row['Low']
        loc_idx = df.index.get_loc(idx) if not isinstance(df.index, pd.RangeIndex) else idx

        future_df = df.iloc[loc_idx+1:]
        if future_df.empty: continue

        recovery_mask = future_df['Low'] <= pivot_price
        if recovery_mask.any():
            recovery_time = recovery_mask.idxmax()
            interval_df = future_df.loc[:recovery_time]
            highest_point = interval_df['High'].max()
            magnitude = highest_point - pivot_price
            
            if 'timestamp' in df.columns:
                 t1 = df.loc[idx]['timestamp']
                 t2 = df.loc[recovery_time]['timestamp']
                 duration_mins = (t2 - t1).total_seconds() / 60
            else:
                 duration_mins = (recovery_time - idx).total_seconds() / 60
        else:
            highest_point = future_df['High'].max()
            magnitude = highest_point - pivot_price
            if 'timestamp' in df.columns:
                 t_start = df.loc[idx]['timestamp']
                 t_end = df.iloc[-1]['timestamp']
                 duration_mins = (t_end - t_start).total_seconds() / 60
            else:
                 duration_mins = len(future_df)

        score = ((magnitude / pivot_price) * 100) * np.log1p(duration_mins)

        if magnitude > (avg_price * 0.00015):
            scored_levels.append({
                "type": "SUPPORT",
                "level": pivot_price,
                "score": score,
                "magnitude": magnitude,
                "duration": duration_mins
            })

    scored_levels.sort(key=lambda x: x['score'], reverse=True)

    # De-Duplicate
    final_levels = []
    for candidate in scored_levels:
        is_duplicate = False
        for existing in final_levels:
            if (candidate['type'] == existing['type']) and \
               (abs(candidate['level'] - existing['level']) < proximity_threshold):
                is_duplicate = True
                break
        if not is_duplicate:
            final_levels.append(candidate)

    # Format Output
    resistance = [x for x in final_levels if x['type'] == 'RESISTANCE'][:2]
    support = [x for x in final_levels if x['type'] == 'SUPPORT'][:2]

    summary = []
    rank = 1
    for r in resistance:
        summary.append({
            "type": "RESISTANCE",
            "rank": rank,
            "level": r['level'],
            "strength_score": round(r['score'], 2),
            "reason": f"Rejected: Dropped ${r['magnitude']:.2f}, buyers absent for {int(r['duration'])} mins."
        })
        rank += 1
    rank = 1
    for s in support:
        summary.append({
            "type": "SUPPORT",
            "rank": rank,
            "level": s['level'],
            "strength_score": round(s['score'], 2),
            "reason": f"Bounced: Rallied ${s['magnitude']:.2f}, sellers absent for {int(s['duration'])} mins."
        })
        rank += 1

    return summary


def _analyze_slice_migration(df):
    """Internal helper to generate migration log for a slice."""
    if df.empty: return []
    
    # Needs timestamp column
    if 'timestamp' in df.columns:
        blocks = df.resample('30min', on='timestamp')
    else:
        blocks = df.resample('30min')

    total_range = df['High'].max() - df['Low'].min()
    value_migration_log = []
    block_id = 1

    for time_window, block_data in blocks:
        if len(block_data) == 0: continue

        # POC Calculation
        price_counts = {}
        for _, row in block_data.iterrows():
            l = np.floor(row['Low'] * 20) / 20
            h = np.ceil(row['High'] * 20) / 20
            if h > l: ticks = np.arange(l, h + 0.05, 0.05)
            else: ticks = [l]
            for t in ticks:
                p = round(t, 2)
                price_counts[p] = price_counts.get(p, 0) + 1
        
        if not price_counts: poc = (block_data['High'].max() + block_data['Low'].min()) / 2
        else: poc = max(price_counts, key=price_counts.get)

        # Nature
        block_h = block_data['High'].max()
        block_l = block_data['Low'].min()
        block_c = block_data['Close'].iloc[-1]
        block_o = block_data['Open'].iloc[0]
        range_val = block_h - block_l
        
        range_ratio = range_val / total_range if total_range > 0 else 0
        if range_ratio < 0.15: vol_str = "Tight"
        elif range_ratio < 0.35: vol_str = "Moderate"
        else: vol_str = "Wide"

        if block_c > block_o: dir_str = "Green"
        elif block_c < block_o: dir_str = "Red"
        else: dir_str = "Flat"
        
        # Time Window String (HH:MM)
        start_str = time_window.strftime("%H:%M")
        
        log_entry = {
            "time": start_str,
            "POC": round(poc, 2),
            "nature": f"{dir_str}, {vol_str} Range",
            "range": f"{round(block_l, 2)}-{round(block_h, 2)}"
        }
        value_migration_log.append(log_entry)
        block_id += 1
        
    return value_migration_log


def _calculate_volume_profile(df, bins=50):
    if df.empty or 'Volume' not in df.columns or df['Volume'].sum() == 0:
        return None, None, None
        
    price_mid = (df['High'] + df['Low']) / 2
    price_bins = pd.cut(price_mid, bins=bins)
    
    if price_bins.empty:
        return None, None, None
        
    grouped = df.groupby(price_bins, observed=False)['Volume'].sum()
    
    if grouped.empty:
        return None, None, None
        
    poc_bin = grouped.idxmax()
    if not isinstance(poc_bin, pd.Interval):
         return None, None, None
    poc_price = round(poc_bin.mid, 2)
    
    total_volume = grouped.sum()
    if total_volume == 0:
        return poc_price, None, None
        
    target_volume = total_volume * 0.70
    sorted_by_vol = grouped.sort_values(ascending=False)
    cumulative_vol = sorted_by_vol.cumsum()
    value_area_bins = sorted_by_vol[cumulative_vol <= target_volume]
    
    if value_area_bins.empty:
        return poc_price, None, None
        
    val_price = round(value_area_bins.index.min().left, 2)
    vah_price = round(value_area_bins.index.max().right, 2)
    
    return poc_price, vah_price, val_price

def _find_key_volume_events(df, count=3):
    if df.empty or 'Volume' not in df.columns or 'dt_eastern' not in df.columns:
        return []

    hod = df['High'].max()
    lod = df['Low'].min()
    sorted_by_vol = df.sort_values(by='Volume', ascending=False)
    top_events = sorted_by_vol.head(count)
    
    events_list = []
    for _, row in top_events.iterrows():
        time_str = row['dt_eastern'].strftime('%H:%M') if hasattr(row['dt_eastern'], 'strftime') else str(row['dt_eastern'])
        price = row['Close']
        vol = row['Volume']
        
        action_parts = []
        if row['High'] >= hod: action_parts.append("Set High-of-Day")
        if row['Low'] <= lod: action_parts.append("Set Low-of-Day")
        
        if row['Close'] > row['Open']: action_parts.append("Strong Up-Bar")
        elif row['Close'] < row['Open']: action_parts.append("Strong Down-Bar")
        else: action_parts.append("Neutral Bar")
            
        brief_action = " | ".join(action_parts)
        events_list.append({
            "time": time_str,
            "price": round(price, 2),
            "volume": int(vol),
            "action": brief_action
        })
        
    return events_list


# ==========================================
# MASTER FUNCTION: 3-PART ANALYSIS
# ==========================================

def analyze_market_context(df, ref_levels, ticker="UNKNOWN", date_str=None) -> dict:
    """
    Analyzes the market in 3 distinct sessions:
    1. Pre-Market (04:00 - 09:30)
    2. RTH (Regular Trading Hours) (09:30 - 16:00)
    3. Post-Market (16:00 - 20:00)
    """
    if df is None or df.empty:
        return {"status": "No Data", "meta": {"ticker": ticker, "date": date_str}}
    
    # 1. Slice DataFrames based on Eastern Time
    # Ensure 'dt_eastern' exists (it should from get_session_bars_from_db)
    if 'dt_eastern' not in df.columns:
         # Fallback if manual DF passed without conversion
         if df['timestamp'].dt.tz is None:
             df['timestamp'] = df['timestamp'].dt.tz_localize(pytz_timezone('UTC'))
         df['dt_eastern'] = df['timestamp'].dt.tz_convert(US_EASTERN)

    # Pre-Market
    df_pre = df[df['dt_eastern'].dt.time < MARKET_OPEN_TIME].copy()
    
    # RTH
    df_rth = df[(df['dt_eastern'].dt.time >= MARKET_OPEN_TIME) & (df['dt_eastern'].dt.time < MARKET_CLOSE_TIME)].copy()
    
    # Post-Market
    df_post = df[df['dt_eastern'].dt.time >= MARKET_CLOSE_TIME].copy()
    
    # 2. Analyze Each Slice
    
    def analyze_slice(slice_df, name, ref_close=None):
        if slice_df.empty: return {"status": "No Data"}
        
        avg_p = slice_df['Close'].mean()
        rejections = _detect_impact_levels(slice_df, avg_p)
        migration = _analyze_slice_migration(slice_df)
        
        high = slice_df['High'].max()
        low = slice_df['Low'].min()
        vol = slice_df['Volume'].sum() if 'Volume' in slice_df.columns else 0
        
        session_open = slice_df['Open'].iloc[0]
        gap_pct = None
        if ref_close and ref_close > 0:
            gap_pct = round(((session_open - ref_close) / ref_close) * 100, 2)
            
        poc, vah, val = _calculate_volume_profile(slice_df)
        key_vol_events = _find_key_volume_events(slice_df)
        
        res = {
            "status": "Active",
            "session_open": round(session_open, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "volume_approx": int(vol),
            "volume_profile": {
                "POC": poc,
                "VAH": vah,
                "VAL": val
            },
            "key_volume_events": key_vol_events,
            "key_levels": rejections,
            "value_migration": migration
        }
        if gap_pct is not None:
            res["gap_pct"] = gap_pct
            
        return res

    # 3. Construct Composite Card
    ref_close = ref_levels.get("yesterday_close", 0)
    context_card = {
        "meta": {
            "ticker": ticker,
            "date": date_str if date_str else df['dt_eastern'].iloc[0].strftime("%Y-%m-%d"),
            "data_points": len(df)
        },
        "reference": ref_levels,
        "sessions": {
            "pre_market": analyze_slice(df_pre, "Pre-Market", ref_close=ref_close),
            "regular_hours": analyze_slice(df_rth, "RTH", ref_close=ref_close),
            "post_market": analyze_slice(df_post, "Post-Market", ref_close=ref_close)
        }
    }
    
    return context_card


# ==========================================
# CACHING LAYER (Context Freezing)
# ==========================================

def _numpy_json_default(obj):
    """
    Custom JSON serializer for numpy scalar types emitted by pandas aggregations.

    Pandas operations like ``.max()``, ``.min()``, ``.mean()`` return ``np.float64``
    or ``np.int64`` values.  Python's built-in ``json.dumps`` can't handle these,
    raising ``TypeError: Object of type int64 is not JSON serializable``.  This
    encoder converts them to equivalent Python primitives transparently.
    """
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _is_valid_context(data: dict) -> bool:
    """
    Returns True only if a context card represents a successful, non-empty computation.
    
    A card is INVALID (must not be cached or served from cache) when:
    1. It is None or not a dict.
    2. Its top-level 'status' key equals 'No Data' (no price bars found).
    3. Its 'meta.data_points' is 0 or absent (computation ran but produced nothing).
    
    This guard prevents stale empty results from being frozen to disk and served
    forever on repeat calls.
    """
    if not data or not isinstance(data, dict):
        return False
    if data.get("status") == "No Data":
        return False
    data_points = data.get("meta", {}).get("data_points", 0)
    if not data_points or data_points == 0:
        return False
    return True


def get_or_compute_context(client, ticker: str, date_str: str, logger: AppLogger):
    """
    Fetches DB data and computes an Impact Context Card for the given ticker/date.
    Always queries the database to ensure fresh data.
    """
    # Fetch Data
    df = get_session_bars_from_db(client, ticker, date_str, f"{date_str} 23:59:59", logger)
    ref_stats = get_previous_session_stats(client, ticker, date_str, logger)

    # Compute Context
    context_card = analyze_market_context(df, ref_stats, ticker, date_str=date_str)

    if not _is_valid_context(context_card):
        logger.log(
            f"   ⚠️ No data for {ticker} on {date_str} "
            f"(DB may not be populated)."
        )

    return context_card
