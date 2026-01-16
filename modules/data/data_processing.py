import pandas as pd
# import yfinance as yf <-- REMOVED
import datetime as dt
import numpy as np
import re
from modules.data.db_utils import get_db_connection # <-- NEW IMPORT

# --- DATA FETCHING (FROM TURSO DB) ---

def fetch_intraday_data(tickers_list, day, interval="5m"):
    """
    Fetches intraday data for a list of tickers from the Turso 'market_data' table.
    Returns a single, long-format DataFrame with standard Capital columns.
    """
    start_date_str = day.strftime('%Y-%m-%d')
    # End date is start date + 1 day for SQL filtering
    end_date_str = (day + dt.timedelta(days=1)).strftime('%Y-%m-%d')
    
    print(f"[DEBUG] fetch_intraday_data: Fetching DB data for {tickers_list} on {start_date_str}")
    
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            print("[ERROR] Database connection failed.")
            return pd.DataFrame()

        # Build placeholders for IN clause
        placeholders = ', '.join(['?'] * len(tickers_list))
        
        # Prepare query args: tickers first, then date range
        query_args = list(tickers_list)
        query_args.append(f"{start_date_str} 00:00:00")
        query_args.append(f"{end_date_str} 00:00:00")
        
        # Determine column names based on new schema map
        # DB: timestamp, symbol, open, high, low, close, volume
        sql = f"""
            SELECT symbol, timestamp, open, high, low, close, volume 
            FROM market_data 
            WHERE symbol IN ({placeholders}) 
            AND timestamp >= ? 
            AND timestamp < ?
            ORDER BY timestamp ASC
        """
        
        rs = conn.execute(sql, query_args)
        rows = rs.rows
        
        if not rows:
            print(f"[DEBUG] No data found in DB for {tickers_list} on {start_date_str}")
            return pd.DataFrame()

        # Convert to DataFrame
        # explicitly map DB columns to what the processor expects
        data = [
            {
                'Ticker': row[0], 
                'Datetime': row[1], 
                'Open': row[2], 
                'High': row[3], 
                'Low': row[4], 
                'Close': row[5], 
                'Volume': row[6]
            } 
            for row in rows
        ]
        
        df = pd.DataFrame(data)
        
        # Ensure Datetime is actually a datetime object
        df['Datetime'] = pd.to_datetime(df['Datetime'])

        # --- FIX: Timezone Conversion (UTC -> Eastern) ---
        # Database stores UTC. We must convert to Eastern for analysis tools (Opening Range, RTH, etc.)
        try:
            # 1. Localize to UTC (assuming DB is naive UTC)
            if df['Datetime'].dt.tz is None:
                df['Datetime'] = df['Datetime'].dt.tz_localize('UTC')
            else:
                df['Datetime'] = df['Datetime'].dt.tz_convert('UTC')
            
            # 2. Convert to US/Eastern
            df['Datetime'] = df['Datetime'].dt.tz_convert('US/Eastern')
            
            # 3. Remove timezone info but keep local time (so 09:30 is 09:30)
            df['Datetime'] = df['Datetime'].dt.tz_localize(None)
            
            print(f"[DEBUG] Converted timestamps to US/Eastern. New range: {df['Datetime'].min()} - {df['Datetime'].max()}")
        except Exception as e:
            print(f"[ERROR] Timezone conversion failed: {e}")
        try:
            min_ts = df['Datetime'].min()
            max_ts = df['Datetime'].max()
            print(f"[VERIFY] Fetched {len(df)} rows for {tickers_list}.")
            print(f"[VERIFY] Time Range: {min_ts} to {max_ts}")
        except: pass
        
        # ensure numeric types
        cols = ['Open', 'High', 'Low', 'Close', 'Volume']
        for c in cols:
            df[c] = pd.to_numeric(df[c])

        # --- DEBUG: CHECK PAXGUSDT BEFORE FILTER ---
        pax_debug = df[df['Ticker'] == 'PAXGUSDT']
        if not pax_debug.empty:
            print(f"[DEBUG] PAXGUSDT Raw Rows: {len(pax_debug)}")
            print(f"[DEBUG] PAXGUSDT Vol Stats: Min={pax_debug['Volume'].min()}, Max={pax_debug['Volume'].max()}")
            # Check timestamps
            print(f"[DEBUG] PAXGUSDT Time Range: {pax_debug['Datetime'].min()} - {pax_debug['Datetime'].max()}")

        # Filter out bad ticks (Volume=0), but allow indices/crypto/fx (often 0 vol)
        # ^VIX: Vol always 0. PAXG/BTC/EUR: Crypto/FX often 0 vol in feeds.
        allowed_zero_vol = ['^VIX', 'PAXGUSDT', 'BTCUSDT', 'EURUSDT', 'CL=F']
        df = df[(df['Volume'] > 0) | (df['Ticker'].isin(allowed_zero_vol))]
        
        # --- DEBUG: CHECK PAXGUSDT AFTER FILTER ---
        pax_debug_after = df[df['Ticker'] == 'PAXGUSDT']
        if not pax_debug_after.empty:
             print(f"[DEBUG] PAXGUSDT Rows After Vol Filter: {len(pax_debug_after)}")
        else:
             if not pax_debug.empty:
                 print(f"[DEBUG] ❌ PAXGUSDT removed by Volume > 0 filter!")

        print(f"[DEBUG] Returning {len(df)} rows from DB.")
        return df

    except Exception as e:
        print(f"[ERROR] DB Fetch Failed: {e}")
        return pd.DataFrame()
    finally:
        if conn:
            conn.close()

# --- ANALYSIS FUNCTIONS (UPGRADED from 'processor.py') ---

def calculate_vwap(df):
    """Calculates the Volume Weighted Average Price (VWAP) series."""
    if df['Volume'].sum() == 0:
        return pd.Series([np.nan] * len(df), index=df.index)
    tp = (df['High'] + df['Low'] + df['Close']) / 3
    tpv = tp * df['Volume']
    vwap_series = tpv.cumsum() / df['Volume'].cumsum()
    return vwap_series

def calculate_volume_profile(df, bins=50):
    """
    Calculates Volume Profile: POC, VAH, and VAL.
    (Upgraded to the more robust 'processor.py' version)
    """
    if df.empty or df['Volume'].sum() == 0:
        return np.nan, np.nan, np.nan
        
    price_mid = (df['High'] + df['Low']) / 2
    price_bins = pd.cut(price_mid, bins=bins)
    
    if price_bins.empty:
        return np.nan, np.nan, np.nan
        
    grouped = df.groupby(price_bins, observed=False)['Volume'].sum()
    
    if grouped.empty:
        return np.nan, np.nan, np.nan
        
    poc_bin = grouped.idxmax()
    if not isinstance(poc_bin, pd.Interval):
         return np.nan, np.nan, np.nan
    poc_price = poc_bin.mid
    
    total_volume = grouped.sum()
    if total_volume == 0:
        return poc_price, np.nan, np.nan
        
    target_volume = total_volume * 0.70
    sorted_by_vol = grouped.sort_values(ascending=False)
    cumulative_vol = sorted_by_vol.cumsum()
    value_area_bins = sorted_by_vol[cumulative_vol <= target_volume]
    
    if value_area_bins.empty:
        return poc_price, np.nan, np.nan
        
    val_price = value_area_bins.index.min().left
    vah_price = value_area_bins.index.max().right
    
    return poc_price, vah_price, val_price

def calculate_opening_range(df, minutes=30, session_open_time_str="09:30"):
    """
    Calculates Opening Range High/Low and a narrative for the RTH 
    session, ignoring pre-market data.
    (This is the robust version from 'processor.py' that fixes the 'nan' bug)
    """
    if df.empty:
        return np.nan, np.nan, "No data."

    try:
        rth_open_time = pd.to_datetime(session_open_time_str).time()
    except Exception:
        rth_open_time = dt.time(9, 30) # Fallback

    # Filter for RTH data only
    rth_df = df[df['Datetime'].dt.time >= rth_open_time].copy()

    if rth_df.empty:
        return np.nan, np.nan, "No RTH (9:30am onward) data found."

    start_time = rth_df['Datetime'].min()
    end_time = start_time + pd.Timedelta(minutes=minutes)
    
    opening_range_df = rth_df[rth_df['Datetime'] < end_time]
    
    if opening_range_df.empty:
        return np.nan, np.nan, "No data found in opening range window (9:30-10:00)."
        
    orl = opening_range_df['Low'].min()
    orh = opening_range_df['High'].max()
    
    rest_of_day_df = rth_df[rth_df['Datetime'] >= end_time]
    
    if rest_of_day_df.empty:
        return orh, orl, "Market closed after opening range."

    # Check for breaks
    broke_low = rest_of_day_df['Low'].min() < orl
    broke_high = rest_of_day_df['High'].max() > orh
    
    time_broke_low_series = rest_of_day_df[rest_of_day_df['Low'] < orl]['Datetime']
    time_broke_high_series = rest_of_day_df[rest_of_day_df['High'] > orh]['Datetime']

    time_broke_low = time_broke_low_series.min() if not time_broke_low_series.empty else pd.NaT
    time_broke_high = time_broke_high_series.min() if not time_broke_high_series.empty else pd.NaT

    # Build the narrative
    narrative = ""
    if not broke_low and not broke_high:
        narrative = "Price remained entirely inside the Opening Range (Balance Day)."
    elif broke_high and not broke_low:
        narrative = f"Price held the ORL as support and broke out above ORH at {time_broke_high.strftime('%H:%M')}, trending higher."
    elif not broke_high and broke_low:
        narrative = f"Price held the ORH as resistance and broke down below ORL at {time_broke_low.strftime('%H:%M')}, trending lower."
    elif broke_high and broke_low:
        if pd.isna(time_broke_low) or pd.isna(time_broke_high):
             narrative = "Price broke both ORH and ORL, but timing data is incomplete."
        elif time_broke_low < time_broke_high:
            narrative = f"Price broke below ORL at {time_broke_low.strftime('%H:%M')}, then reversed and broke above ORH at {time_broke_high.strftime('%H:%M')}."
        else:
            narrative = f"Price broke above ORH at {time_broke_high.strftime('%H:%M')}, then reversed and broke below ORL at {time_broke_low.strftime('%H:%M')}."
            
    return orh, orl, narrative

def find_key_volume_events(df, count=3):
    """
    Finds the top N volume candles and describes their context.
    (Upgraded from std-dev method to top-N method from 'processor.py')
    """
    if df.empty:
        return ["No data to find events."]
        
    rth_df = df[df['Datetime'].dt.time >= dt.time(9, 30)].copy()
    if rth_df.empty:
        return ["No RTH data to find events."]

    hod = rth_df['High'].max()
    lod = rth_df['Low'].min()
    sorted_by_vol = rth_df.sort_values(by='Volume', ascending=False)
    top_events = sorted_by_vol.head(count)
    
    events_list = []
    for _, row in top_events.iterrows():
        time = row['Datetime'].strftime('%H:%M')
        price = row['Close']
        vol = row['Volume']
        
        action_parts = []
        if row['High'] >= hod: action_parts.append("Set High-of-Day")
        if row['Low'] <= lod: action_parts.append("Set Low-of-Day")
        
        if row['Close'] > row['Open']: action_parts.append("Strong Up-Bar")
        elif row['Close'] < row['Open']: action_parts.append("Strong Down-Bar")
        else: action_parts.append("Neutral Bar")
            
        brief_action = " | ".join(action_parts)
        formatted_string = f"{time} @ ${price:.2f} (Vol: {vol:,.0f}) - [{brief_action}]"
        events_list.append(formatted_string)
        
    return events_list

def get_vwap_interaction(df, vwap_series):
    """
    Analyzes how price interacted with VWAP.
    (Upgraded from 'processor.py')
    """
    if df.empty or vwap_series.isnull().all():
        return "N/A"
        
    rth_df = df[df['Datetime'].dt.time >= dt.time(9, 30)].copy()
    if rth_df.empty:
        return "N/A"
        
    # Ensure index alignment before slicing vwap_series
    vwap_series_rth = vwap_series.loc[rth_df.index]
    if vwap_series_rth.empty:
        return "N/A"

    crosses = ((rth_df['Close'] > vwap_series_rth) & (rth_df['Close'].shift(1) < vwap_series_rth)) | \
              ((rth_df['Close'] < vwap_series_rth) & (rth_df['Close'].shift(1) > vwap_series_rth))
    num_crosses = crosses.sum()
    
    if num_crosses > 4:
        return "Crossed multiple times"
    elif (rth_df['Low'] > vwap_series_rth).all():
        return "Support"
    elif (rth_df['High'] < vwap_series_rth).all():
        return "Resistance"
    else:
        return "Mixed (acted as both support and resistance)"

# --- TEXT GENERATION (UPGRADED) ---

def generate_analysis_text(tickers_to_process, analysis_date):
    """
    Performs all analysis and returns a single formatted string
    in the new, desired "Data Extraction Summary" format.
    """
    print(f"[DEBUG] generate_analysis_text: Processing tickers: {tickers_to_process} (type: {type(tickers_to_process)}) for date {analysis_date}")
    if not tickers_to_process or not isinstance(tickers_to_process, (list, tuple)) or len(tickers_to_process) == 0:
        print(f"[DEBUG] generate_analysis_text: No tickers supplied! Value: {tickers_to_process}")
        return f"[ERROR] No tickers supplied to analysis function."
    all_data_df = fetch_intraday_data(tickers_to_process, analysis_date, interval="5m")

    if all_data_df.empty:
        print(f"[DEBUG] generate_analysis_text: No data found for any tickers on {analysis_date}. Tickers supplied: {tickers_to_process}")
        return f"[ERROR] No data found for any tickers on {analysis_date}. Tickers supplied: {tickers_to_process}. It may be a weekend, holiday, or a data-fetching issue."

    full_analysis_text = []
    errors = []

    for ticker in tickers_to_process:
        print(f"[DEBUG] generate_analysis_text: Processing ticker: '{ticker}' (type: {type(ticker)})")
        if not ticker or not isinstance(ticker, str):
            print(f"[DEBUG] generate_analysis_text: Skipping invalid ticker: {ticker}")
            continue
        df_ticker = all_data_df[all_data_df['Ticker'] == ticker.upper()].copy()
        print(f"[DEBUG] generate_analysis_text: Data rows for {ticker}: {len(df_ticker)}")
        if df_ticker.empty:
            print(f"[DEBUG] generate_analysis_text: No data for ticker '{ticker}' on {analysis_date}")
            # --- FIX: Output explicit error block instead of silent skip ---
            full_analysis_text.append(f"Data Extraction Summary: {ticker} | {analysis_date}\n==================================================\n[ERROR] No data found (Volume=0 or Empty Fetch).")
            continue

        df_ticker.reset_index(drop=True, inplace=True)

        try:
            # Filter for RTH data to get correct O/C/H/L
            rth_df = df_ticker[df_ticker['Datetime'].dt.time >= dt.time(9, 30)]
            if rth_df.empty:
                print(f"[DEBUG] generate_analysis_text: No RTH data for ticker {ticker}")
                full_analysis_text.append(f"Data Extraction Summary: {ticker} | {analysis_date}\n==================================================\n[ERROR] Row data exists ({len(df_ticker)}), but NO RTH (9:30+) data found.")
                continue

            open_price = rth_df['Open'].iloc[0]
            close_price = rth_df['Close'].iloc[-1]
            hod_price = rth_df['High'].max()
            hod_time_str = rth_df.loc[rth_df['High'].idxmax(), 'Datetime'].strftime('%H:%M')
            lod_price = rth_df['Low'].min()
            lod_time_str = rth_df.loc[rth_df['Low'].idxmin(), 'Datetime'].strftime('%H:%M')

            vwap_series = calculate_vwap(df_ticker)
            session_vwap_final = vwap_series.iloc[-1] # Full session VWAP

            # Use RTH data for profile and events
            poc, vah, val = calculate_volume_profile(rth_df)
            orh, orl, or_narrative = calculate_opening_range(df_ticker) # This function handles RTH filtering internally
            key_volume_events = find_key_volume_events(df_ticker) # This also handles RTH

            close_vs_vwap = "Above" if close_price > session_vwap_final else "Below"
            vwap_interaction = get_vwap_interaction(df_ticker, vwap_series)

            # Build the new, high-quality string
            ticker_summary = f"""
Data Extraction Summary: {ticker} | {analysis_date}
==================================================

[VERIFICATION]
Source: Turso Database (market_data table)
Rows Fetched: {len(df_ticker)}
Time Range: {df_ticker['Datetime'].min().strftime('%H:%M:%S')} - {df_ticker['Datetime'].max().strftime('%H:%M:%S')}

1. Session Extremes & Timing:
   - Open: ${open_price:.2f}
   - Close: ${close_price:.2f}
   - High of Day (HOD): ${hod_price:.2f} (Set at {hod_time_str})
   - Low of Day (LOD): ${lod_price:.2f} (Set at {lod_time_str})

2. Volume Profile (Value References):
   - Point of Control (POC): ${poc:.2f} (Highest volume traded)
   - Value Area High (VAH): ${vah:.2f}
   - Value Area Low (VAL): ${val:.2f}

3. Key Intraday Volume Events:
"""
            for event in key_volume_events:
                ticker_summary += f"   - {event}\n"

            ticker_summary += f"""
4. VWAP Relationship:
   - Session VWAP: ${session_vwap_final:.2f}
   - Close vs. VWAP: {close_vs_vwap}
   - Key Interactions: VWAP primarily acted as {vwap_interaction}.

5. Opening Range Analysis (First 30 Mins):
   - Opening Range: ${orl:.2f} - ${orh:.2f}
   - Outcome Narrative: {or_narrative}
"""
            full_analysis_text.append(ticker_summary.strip())

        except Exception as e:
            errors.append(f"An error occurred during analysis for {ticker}: {e}")
            # Also print to console for debugging
            print(f"Error processing {ticker}: {e}")

    final_text = "\n\n".join(full_analysis_text)
    if errors:
        final_text += "\n\n--- ERRORS ---\n" + "\n".join(errors)
    return final_text

# --- PARSER (NOW UPDATED TO READ THE NEW FORMAT) ---

def parse_raw_summary(raw_text: str) -> dict:
    """
    Parses the new, high-quality "Data Extraction Summary" format.
    """
    data = {"raw_text_summary": raw_text}
    
    def find_value(pattern, text, type_conv=float, group_num=1):
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            val_str = match.group(group_num).replace(',', '').replace('$', '').strip()
            if not val_str or val_str.lower() == 'nan': return None
            try: return type_conv(val_str)
            except:
                if type_conv == str: return val_str
                return None
        return None

    data['ticker'] = find_value(r"Data Extraction Summary:\s*([A-Z\.]+)", raw_text, str) # FIX: Changed regex to match generator
    data['date'] = find_value(r"\|\s*([\d\-]+)", raw_text, str)
    
    money_pattern = r"\$([\d\.,]+)"
    data['open'] = find_value(rf"Open:\s*{money_pattern}", raw_text)
    data['close'] = find_value(rf"Close:\s*{money_pattern}", raw_text)
    data['high'] = find_value(rf"High of Day \(HOD\):\s*{money_pattern}", raw_text)
    data['low'] = find_value(rf"Low of Day \(LOD\):\s*{money_pattern}", raw_text)
    data['poc'] = find_value(rf"Point of Control \(POC\):\s*{money_pattern}", raw_text)
    data['vah'] = find_value(rf"Value Area High \(VAH\):\s*{money_pattern}", raw_text)
    data['val'] = find_value(rf"Value Area Low \(VAL\):\s*{money_pattern}", raw_text)
    data['vwap'] = find_value(rf"Session VWAP:\s*{money_pattern}", raw_text)
    
    or_match = re.search(rf"Opening Range:\s*\$?([\d\.]+)\s*-\s*\$?([\d\.]+)", raw_text, re.IGNORECASE)
    if or_match:
        try: data['orl'] = float(or_match.group(1))
        except: data['orl'] = None
        try: data['orh'] = float(or_match.group(2))
        except: data['orh'] = None
    else:
        data['orl'] = None
        data['orh'] = None
        
    data['or_narrative'] = find_value(r"Outcome Narrative:\s*(.*)", raw_text, str)
    data['vwap_narrative'] = find_value(r"Key Interactions:\s*VWAP primarily acted as ([^\n\.]*)", raw_text, str)
    
    return data

# --- THIS FUNCTION IS NOW UN-INDENTED AND VISIBLE ---
def split_stock_summaries(raw_text: str) -> dict:
    """
    Splits the combined raw text (in "Data Extraction Summary" format)
    into a dictionary of ticker: summary.
    """
    summaries = {}
    
    # Pattern to find the start of each summary block.
    # It captures the ticker name (e.g., "AAPL", "BRK.B", "BTC-USD", "CL=F", "^VIX").
    # FIX: Added -, =, ^, 0-9 to the character class
    pattern = re.compile(r"Data Extraction Summary:\s*([A-Z0-9\.\-\=\^]+)\s*\|")
    
    # Find all starting points
    matches = list(pattern.finditer(raw_text))
    
    if not matches:
        return {} # No tickers found

    for i, match in enumerate(matches):
        ticker = match.group(1).strip()
        
        # The start of the summary text for *this* block
        # is the start of the match itself (the "Data Extraction..." line)
        start_index = match.start()
        
        # The end of this summary block is the start of the *next* block
        if i + 1 < len(matches):
            end_index = matches[i+1].start()
        else:
            # If it's the last one, go to the end of the string
            end_index = len(raw_text)
            
        # Get the full summary block text
        summary_text = raw_text[start_index:end_index].strip()
        
        if ticker and summary_text:
            # Add the full text, including the header
            summaries[ticker] = summary_text
            
    return summaries

# --- AGENTIC ADDITION: DATA HELPER ---
def calculate_bias_score(bias_text: str) -> float:
    """
    Converts a Setup Bias text string into a numerical score for comparison.
    Used for Trend Analysis and Structure Break detection.
    
    Mapping:
    - Bullish: 2.0
    - Bullish Consolidation: 1.5
    - Neural (Bullish Lean): 1.0
    - Neutral: 0.0
    - Neutral (Bearish Lean): -1.0
    - Bearish Consolidation: -1.5
    - Bearish: -2.0
    """
    lower_bias = bias_text.lower()
    score = 0.0
    
    # 1. Complex/Compound States (Consolidation)
    if "bullish consolidation" in lower_bias:
        score = 1.5
    elif "bearish consolidation" in lower_bias:
        score = -1.5
    
    # 2. Leans (Neutral with Lean)
    elif "neutral" in lower_bias and "bullish lean" in lower_bias:
        score = 1.0
    elif "neutral" in lower_bias and "bearish lean" in lower_bias:
        score = -1.0

    # 3. Base States (Stronger than lean, stronger than consolidation?)
    elif "bullish" in lower_bias and "neutral" not in lower_bias:
        score = 2.0
    elif "bearish" in lower_bias and "neutral" not in lower_bias:
        score = -2.0
        
    return score