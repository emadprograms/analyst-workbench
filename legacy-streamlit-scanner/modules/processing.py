import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time as dt_time
from pytz import timezone as pytz_timezone
from modules.utils import AppLogger

US_EASTERN = pytz_timezone('US/Eastern')
MARKET_OPEN_TIME = dt_time(9, 30)

# --- DB FETCHING UTILITIES ---

def get_latest_price_details(client, ticker: str, cutoff_str: str, logger: AppLogger) -> tuple[float | None, str | None]:
    query = "SELECT close, timestamp FROM market_data WHERE symbol = ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1"
    try:
        rs = client.execute(query, [ticker, cutoff_str])
        if rs.rows:
            return rs.rows[0][0], rs.rows[0][1]
        return None, None
    except Exception as e:
        logger.log(f"DB Read Error {ticker}: {e}")
        return None, None

def get_session_bars_from_db(client, epic: str, benchmark_date: str, cutoff_str: str, logger: AppLogger) -> pd.DataFrame | None:
    try:
        # We need High/Low/Close for Impact logic. Volume is optional but good to have.
        query = """
            SELECT timestamp, open, high, low, close, volume, session
            FROM market_data
            WHERE symbol = ? AND date(timestamp) = ? AND timestamp <= ?
            ORDER BY timestamp ASC
        """
        rs = client.execute(query, [epic, benchmark_date, cutoff_str])
        if not rs.rows:
            return None
        df = pd.DataFrame(
            rs.rows,
            columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'session_db'],
        )

        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(str).str.replace('Z', '').str.replace(' ', 'T'))
        if df['timestamp'].dt.tz is None:
            df['timestamp'] = df['timestamp'].dt.tz_localize('UTC')

        df['dt_eastern'] = df['timestamp'].dt.tz_convert(US_EASTERN)
        
        # Filter for Pre-Market (04:00 - 09:30 ET)
        time_eastern = df['dt_eastern'].dt.time
        df = df[time_eastern < MARKET_OPEN_TIME].copy()

        for col in ['open', 'high', 'low', 'close']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        df.dropna(subset=['close'], inplace=True)
        
        # Normalize columns for the Engine
        df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'}, inplace=True)
        return df.reset_index(drop=True)
    except Exception as e:
        logger.log(f"Data Error ({epic}): {e}")
        return None

def get_previous_session_stats(client, ticker: str, current_date_str: str, logger: AppLogger) -> dict:
    """
    Fetches Yesterday's High, Low, and Close for context.
    """
    try:
        # Find the latest date BEFORE the current analysis date
        date_query = "SELECT DISTINCT date(timestamp) as d FROM market_data WHERE symbol = ? AND date(timestamp) < ? ORDER BY d DESC LIMIT 1"
        rs_date = client.execute(date_query, [ticker, current_date_str])
        
        if not rs_date.rows:
            return {"yesterday_close": 0, "yesterday_high": 0, "yesterday_low": 0}
            
        prev_date = rs_date.rows[0][0]
        
        # Get Stats for that date
        stats_query = """
            SELECT MAX(high), MIN(low), 
                   (SELECT close FROM market_data WHERE symbol = ? AND date(timestamp) = ? ORDER BY timestamp DESC LIMIT 1)
            FROM market_data 
            WHERE symbol = ? AND date(timestamp) = ?
        """
        rs = client.execute(stats_query, [ticker, prev_date, ticker, prev_date])
        
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

# ==========================================
# THE IMPACT CONTEXT ENGINE
# ==========================================

def detect_impact_levels(df):
    """
    Identifies Levels based on IMPACT (Depth & Duration).
    Formula: Score = Magnitude * Log(Duration)
    """
    if df.empty: return []
    
    avg_price = df['Close'].mean()
    proximity_threshold = max(0.10, avg_price * 0.0015) 

    # 1. Find Pivots
    df['is_peak'] = df['High'][(df['High'].shift(1) <= df['High']) & (df['High'].shift(-1) < df['High'])]
    df['is_valley'] = df['Low'][(df['Low'].shift(1) >= df['Low']) & (df['Low'].shift(-1) > df['Low'])]
    
    scored_levels = []

    # --- RESISTANCE SCORING ---
    for idx, row in df[df['is_peak'].notna()].iterrows():
        pivot_price = row['High']
        future_df = df.loc[idx:].iloc[1:]
        
        if future_df.empty: continue
        
        recovery_mask = future_df['High'] >= pivot_price
        if recovery_mask.any():
            recovery_time = recovery_mask.idxmax()
            magnitude = pivot_price - df.loc[idx:recovery_time]['Low'].min()
            duration_mins = (recovery_time - idx).total_seconds() / 60
        else:
            # Killer Wick (Never returned)
            magnitude = pivot_price - future_df['Low'].min()
            duration_mins = len(future_df) * 5 # Approx
            
        score = magnitude * np.log1p(duration_mins)
        
        if magnitude > (avg_price * 0.001):
            scored_levels.append({
                "type": "RESISTANCE", "level": pivot_price, "score": score,
                "magnitude": magnitude, "duration": duration_mins
            })

    # --- SUPPORT SCORING ---
    for idx, row in df[df['is_valley'].notna()].iterrows():
        pivot_price = row['Low']
        future_df = df.loc[idx:].iloc[1:]
        
        if future_df.empty: continue
        
        recovery_mask = future_df['Low'] <= pivot_price
        if recovery_mask.any():
            recovery_time = recovery_mask.idxmax()
            magnitude = df.loc[idx:recovery_time]['High'].max() - pivot_price
            duration_mins = (recovery_time - idx).total_seconds() / 60
        else:
            magnitude = future_df['High'].max() - pivot_price
            duration_mins = len(future_df) * 5
            
        score = magnitude * np.log1p(duration_mins)
        
        if magnitude > (avg_price * 0.001):
            scored_levels.append({
                "type": "SUPPORT", "level": pivot_price, "score": score,
                "magnitude": magnitude, "duration": duration_mins
            })

    # Rank & Deduplicate
    scored_levels.sort(key=lambda x: x['score'], reverse=True)
    final_levels = []
    for c in scored_levels:
        # Check if close to existing higher-ranked level
        if not any(x['type'] == c['type'] and abs(x['level'] - c['level']) < proximity_threshold for x in final_levels):
            final_levels.append(c)
            
    return final_levels[:5] # Return top 5 overall impacts

def analyze_market_context(df, ref_levels, ticker="UNKNOWN") -> dict:
    """
    The Master Function.
    Returns the JSON Observation Card (Migration Log + Impact Levels).
    """
    if df is None or df.empty:
        return {"status": "No Data", "meta": {"ticker": ticker}}

    session_high = df['High'].max()
    session_low = df['Low'].min()
    current_price = df.iloc[-1]['Close']
    total_range = session_high - session_low
    
    # 1. VALUE MIGRATION LOG (30-min Blocks)
    blocks = df.resample('30min', on='timestamp')
    migration_log = []
    block_id = 1
    all_block_pocs = [] # For Time-Based Acceptance

    for time_window, block_data in blocks:
        if len(block_data) == 0: continue
        
        # Calculate Time-Based POC (Price with most ticks/minutes)
        # Bucket size = 0.05 for granularity
        buckets = (block_data['Close'] / 0.05).round() * 0.05
        poc = buckets.mode()[0]
        all_block_pocs.append(poc)
        
        # Direction
        o = block_data.iloc[0]['Open']
        c = block_data.iloc[-1]['Close']
        color = "Green" if c > o else "Red"
        
        log_entry = {
            "block_id": block_id,
            "time": time_window.strftime("%H:%M"),
            "poc": round(poc, 2),
            "candle": color,
            "high": round(block_data['High'].max(), 2),
            "low": round(block_data['Low'].min(), 2)
        }
        migration_log.append(log_entry)
        block_id += 1

    # 2. IMPACT REJECTIONS
    impact_levels = detect_impact_levels(df.copy())
    
    # Format for JSON
    formatted_impacts = []
    for lvl in impact_levels:
        formatted_impacts.append({
            "type": lvl['type'],
            "price": round(lvl['level'], 2),
            "score": round(lvl['score'], 2),
            "note": f"Held for {int(lvl['duration'])}m after {lvl['magnitude']:.2f} move"
        })

    # 3. TIME-BASED ACCEPTANCE (Stacked POCs)
    time_levels = []
    if len(all_block_pocs) >= 3:
        all_block_pocs.sort()
        cluster = [all_block_pocs[0]]
        tolerance = current_price * 0.001 # 0.1% tolerance
        
        for i in range(1, len(all_block_pocs)):
            if abs(all_block_pocs[i] - np.mean(cluster)) <= tolerance:
                cluster.append(all_block_pocs[i])
            else:
                if len(cluster) >= 2: # 2+ blocks agreeing is significant
                    time_levels.append({"price": round(np.mean(cluster), 2), "blocks": len(cluster)})
                cluster = [all_block_pocs[i]]
        if len(cluster) >= 2:
            time_levels.append({"price": round(np.mean(cluster), 2), "blocks": len(cluster)})

    # 4. CONSTRUCT JSON CARD
    context_card = {
        "meta": {
            "ticker": ticker,
            "current_price": round(current_price, 2),
            "timestamp": df.iloc[-1]['timestamp'].strftime("%H:%M")
        },
        "session_extremes": {
            "ceiling": round(session_high, 2),
            "floor": round(session_low, 2),
            "range": round(total_range, 2)
        },
        "reference_levels": ref_levels,
        "value_migration_log": migration_log,
        "impact_rejections": formatted_impacts,
        "time_acceptance_levels": time_levels,
        "summary_text": f"Observation Card Generated. Migration Steps: {len(migration_log)}. Impact Levels: {len(formatted_impacts)}."
    }
    
    return context_card