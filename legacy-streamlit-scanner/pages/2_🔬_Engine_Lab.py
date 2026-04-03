import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import json
from datetime import datetime, timedelta
import pytz

st.set_page_config(page_title="Engine Lab", layout="wide", page_icon="🔬")
st.title("🔬 Engine Lab: Isolation Testing")

# Try to import yfinance for real data
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    st.warning("⚠️ 'yfinance' library not found. Using synthetic data only.")

# ==========================================
# 1. Data Acquisition (Real & Synthetic)
# ==========================================

def fetch_real_data(ticker="QQQ"):
    """
    Fetches 1-minute pre-market data.
    """
    if not YFINANCE_AVAILABLE:
        return None, None

    with st.spinner(f"📡 Fetching real-time data for {ticker}..."):
        try:
            # A. Fetch Intraday Data (1m)
            df = yf.download(ticker, period="5d", interval="1m", prepost=True, progress=False)

            if df.empty:
                st.error("❌ No data returned from API.")
                return None, None

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC").tz_convert("US/Eastern")
            else:
                df.index = df.index.tz_convert("US/Eastern")

            # Select Previous Day (Index -2) to avoid incomplete current sessions
            unique_dates = sorted(list(set(df.index.date)))

            if len(unique_dates) > 1:
                target_date = unique_dates[-2]
                st.info(f"🔙 Selecting analysis for: {target_date} (Skipping incomplete/current session)")
            else:
                target_date = unique_dates[-1]
                st.warning(f"⚠️ Only one day of data found. Using: {target_date}")

            day_data = df[df.index.date == target_date].copy()
            pre_market = day_data.between_time("04:00", "09:30")

            if pre_market.empty:
                st.warning(f"⚠️ Data found for {target_date}, but no pre-market ticks (04:00-09:30).")
                return None, None

            st.success(f"✅ Loaded {len(pre_market)} pre-market candles for {target_date}")

            # B. Fetch Daily Data for Reference Levels
            daily = yf.download(ticker, period="1mo", interval="1d", progress=False)
            if isinstance(daily.columns, pd.MultiIndex):
                daily.columns = daily.columns.get_level_values(0)

            past_days = daily[daily.index.date < target_date]

            if not past_days.empty:
                last_close = past_days.iloc[-1]
                ref_levels = {
                    "yesterday_date": past_days.index[-1].strftime("%Y-%m-%d"),
                    "yesterday_close": round(float(last_close['Close']), 2),
                    "yesterday_high": round(float(last_close['High']), 2),
                    "yesterday_low": round(float(last_close['Low']), 2)
                }
            else:
                ref_levels = {
                    "yesterday_date": "N/A",
                    "yesterday_close": round(pre_market.iloc[0]['Open'], 2),
                    "yesterday_high": 0.0,
                    "yesterday_low": 0.0
                }

            return pre_market, ref_levels

        except Exception as e:
            st.error(f"❌ Error fetching data: {e}")
            return None, None

def generate_synthetic_data(ticker="QQQ", start_price=None):
    st.info("🎲 Generating SYNTHETIC dynamic data...")
    if start_price is None:
        start_price = np.random.uniform(300, 500)

    date_str = datetime.now().strftime("%Y-%m-%d")
    full_start = pd.to_datetime(f"{date_str} 04:00").tz_localize("US/Eastern")
    full_end = pd.to_datetime(f"{date_str} 09:30").tz_localize("US/Eastern")
    timestamps = pd.date_range(start=full_start, end=full_end, freq="1min")

    data = []
    current_price = start_price
    regime = np.random.choice(['uptrend', 'downtrend', 'chop'], p=[0.33, 0.33, 0.34])
    st.write(f"**Simulation Regime**: {regime.upper()}")

    for ts in timestamps:
        bias = 0
        if regime == 'uptrend': bias = 0.02
        if regime == 'downtrend': bias = -0.02

        volatility = 0.15
        change = np.random.normal(bias, volatility)
        close = current_price + change

        high = max(current_price, close) + abs(np.random.normal(0, 0.05))
        low = min(current_price, close) - abs(np.random.normal(0, 0.05))

        data.append({
            "Timestamp": ts,
            "Open": round(current_price, 2),
            "High": round(high, 2),
            "Low": round(low, 2),
            "Close": round(close, 2),
            "Volume": int(np.random.uniform(100, 5000))
        })
        current_price = close

    df = pd.DataFrame(data)
    df.set_index("Timestamp", inplace=True)

    ref_levels = {
        "yesterday_date": "SYNTHETIC_PREV_DAY",
        "yesterday_close": round(start_price - np.random.uniform(-3, 3), 2),
        "yesterday_high": round(start_price + np.random.uniform(2, 5), 2),
        "yesterday_low": round(start_price - np.random.uniform(2, 5), 2)
    }

    return df, ref_levels

# ==========================================
# 2. The Analysis Engine (Logic)
# ==========================================

def detect_impact_levels(df):
    """
    Identifies Levels based on IMPACT (Depth & Duration).
    1. Find every pivot.
    2. Calculate Score = (How far price went away) * log(How long it stayed away).
    3. Rank by Score.
    4. De-duplicate (remove nearby weaker signals).
    """
    avg_price = df['Close'].mean()

    # Define "Nearby" for de-duplication (e.g. 0.15% of price)
    proximity_threshold = max(0.10, avg_price * 0.0015)

    # 1. Find ALL Pivots (Local Extremes)
    # We use a small window (3) because we want to catch the exact moment of rejection
    df['is_peak'] = df['High'][(df['High'].shift(1) <= df['High']) & (df['High'].shift(-1) < df['High'])]
    df['is_valley'] = df['Low'][(df['Low'].shift(1) >= df['Low']) & (df['Low'].shift(-1) > df['Low'])]

    potential_peaks = df[df['is_peak'].notna()]
    potential_valleys = df[df['is_valley'].notna()]

    scored_levels = []

    # 2. Score Every Pivot Individually

    # --- RESISTANCE SCORING ---
    for idx, row in potential_peaks.iterrows():
        pivot_price = row['High']
        pivot_time = idx

        # Look forward
        loc_idx = df.index.get_loc(pivot_time)
        future_df = df.iloc[loc_idx+1:]

        if future_df.empty: continue

        # Did price ever return to this level?
        # Return condition: Price crosses ABOVE the pivot again
        recovery_mask = future_df['High'] >= pivot_price

        if recovery_mask.any():
            recovery_time = recovery_mask.idxmax()
            interval_df = future_df.loc[:recovery_time]
            # Max Adverse Excursion (How low did it go before coming back?)
            lowest_point = interval_df['Low'].min()
            magnitude = pivot_price - lowest_point
            duration_mins = (recovery_time - pivot_time).total_seconds() / 60
        else:
            # Price NEVER returned (The "Killer Wick" Scenario)
            # Magnitude is the drop to the lowest point in the rest of the session
            lowest_point = future_df['Low'].min()
            magnitude = pivot_price - lowest_point
            duration_mins = len(future_df) # Duration is the rest of the session

        # SCORE CALCULATION
        # We weight Magnitude heavily. Duration acts as a multiplier.
        # Log(Duration) prevents a 2-hour drift from overpowering a sharp crash.
        score = magnitude * np.log1p(duration_mins)

        if magnitude > (avg_price * 0.001): # Filter noise (must move at least 0.1%)
            scored_levels.append({
                "type": "RESISTANCE",
                "level": pivot_price,
                "score": score,
                "magnitude": magnitude,
                "duration": duration_mins,
                "time": pivot_time
            })

    # --- SUPPORT SCORING ---
    for idx, row in potential_valleys.iterrows():
        pivot_price = row  ['Low']
        pivot_time = idx

        loc_idx = df.index.get_loc(pivot_time)
        future_df = df.iloc[loc_idx+1:]
        if future_df.empty: continue

        recovery_mask = future_df['Low'] <= pivot_price

        if recovery_mask.any():
            recovery_time = recovery_mask.idxmax()
            interval_df = future_df.loc[:recovery_time]
            highest_point = interval_df['High'].max()
            magnitude = highest_point - pivot_price
            duration_mins = (recovery_time - pivot_time).total_seconds() / 60
        else:
            highest_point = future_df['High'].max()
            magnitude = highest_point - pivot_price
            duration_mins = len(future_df)

        score = magnitude * np.log1p(duration_mins)

        if magnitude > (avg_price * 0.001):
            scored_levels.append({
                "type": "SUPPORT",
                "level": pivot_price,
                "score": score,
                "magnitude": magnitude,
                "duration": duration_mins,
                "time": pivot_time
            })

    # 3. Sort by Score (Impact)
    scored_levels.sort(key=lambda x: x['score'], reverse=True)

    # 4. De-Duplicate (Keep strongest signal in a zone)
    final_levels = []

    for candidate in scored_levels:
        is_duplicate = False
        for existing in final_levels:
            # If close to an existing (higher ranked) level of same type
            if (candidate['type'] == existing['type']) and \
               (abs(candidate['level'] - existing['level']) < proximity_threshold):
                is_duplicate = True
                break

        if not is_duplicate:
            final_levels.append(candidate)

    # Return Top Results (separated)
    resistance = [x for x in final_levels if x['type'] == 'RESISTANCE'][:2]
    support = [x for x in final_levels if x['type'] == 'SUPPORT'][:2]

    # Format for JSON
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

def analyze_market_context(df, ref_levels, ticker="QQQ"):
    if df is None or df.empty: return {}, []

    session_high = df['High'].max()
    session_low = df['Low'].min()
    current_price = df.iloc[-1]['Close']
    total_range = session_high - session_low

    # Block Analysis
    blocks = df.resample('30min')
    value_migration_log = []
    block_id = 1

    # Helper to track POCs for Time-Based Support detection
    all_block_pocs = []

    for time_window, block_data in blocks:
        if len(block_data) == 0: continue

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

        all_block_pocs.append(poc) # Collect POC for clustering later

        total_minutes = len(block_data)
        poc_hits = price_counts.get(poc, 0)
        time_at_poc_pct = round((poc_hits / total_minutes) * 100, 1) if total_minutes > 0 else 0

        block_h = block_data['High'].max()
        block_l = block_data['Low'].min()
        block_c = block_data['Close'].iloc[-1]
        block_o = block_data['Open'].iloc[0]
        range_val = block_h - block_l

        if total_range > 0: range_ratio = range_val / total_range
        else: range_ratio = 0

        if range_ratio < 0.15: vol_str = "Tight Compression"
        elif range_ratio < 0.35: vol_str = "Moderate Range"
        else: vol_str = "Wide Expansion"

        if range_val == 0: loc_str = "unchanged"
        else:
            pct_loc = (block_c - block_l) / range_val
            if pct_loc > 0.8: loc_str = "near highs"
            elif pct_loc < 0.2: loc_str = "near lows"
            else: loc_str = "mid-range"

        if block_c > block_o: dir_str = "Green"
        elif block_c < block_o: dir_str = "Red"
        else: dir_str = "Flat"

        nature_desc = f"{dir_str} candle, {vol_str} (${range_val:.2f}), closed {loc_str}"

        log_entry = {
            "block_id": block_id,
            "time_window": time_window.strftime("%H:%M") + " - " + (time_window + timedelta(minutes=30)).strftime("%H:%M"),
            "observations": {
                "block_high": round(block_h, 2),
                "block_low": round(block_l, 2),
                "most_traded_price_level": round(poc, 2),
                "time_at_poc_percent": f"{min(time_at_poc_pct, 100)}%",
                "price_action_nature": nature_desc
            }
        }
        value_migration_log.append(log_entry)
        block_id += 1

    # 3. IMPACT-BASED REJECTION SYSTEM (Rank 1 Priority)
    ranked_rejections = detect_impact_levels(df.copy())

    # 4. TIME-BASED ACCEPTANCE (Stacked POCs - Rank 2 Priority)
    # Logic: Find clusters of POCs. If >= 3 POCs align, it's a "Time Level".
    all_block_pocs.sort()
    time_based_levels = []
    if all_block_pocs:
        # Tight tolerance for "Acceptance" (e.g. 0.10%)
        tolerance = max(0.05, df['Close'].mean() * 0.001)

        current_cluster = [all_block_pocs[0]]
        for i in range(1, len(all_block_pocs)):
            if all_block_pocs[i] - np.mean(current_cluster) <= tolerance:
                current_cluster.append(all_block_pocs[i])
            else:
                if len(current_cluster) >= 3: # Minimum 3 blocks (1.5 hours)
                    time_based_levels.append({
                        "level": round(np.mean(current_cluster), 2),
                        "count": len(current_cluster),
                        "note": "Significant Time-Based Acceptance (Stacked POCs)"
                    })
                current_cluster = [all_block_pocs[i]]
        # Check last
        if len(current_cluster) >= 3:
            time_based_levels.append({
                "level": round(np.mean(current_cluster), 2),
                "count": len(current_cluster),
                "note": "Significant Time-Based Acceptance (Stacked POCs)"
            })

    # Sort Time Levels by Count
    time_based_levels.sort(key=lambda x: x['count'], reverse=True)

    y_close = ref_levels.get("yesterday_close", 0)
    gap_text = "N/A"
    if y_close > 0:
        if current_price > y_close: gap_text = "GAP_UP"
        elif current_price < y_close: gap_text = "GAP_DOWN"
        else: gap_text = "FLAT"

    context_card = {
        "meta": {
            "ticker": ticker,
            "timestamp": df.index[-1].strftime("%H:%M:%S"),
            "pre_market_session_open": df.index[0].strftime("%H:%M:%S")
        },
        "reference_levels": {
            "yesterday_close": ref_levels.get("yesterday_close", 0),
            "yesterday_high": ref_levels.get("yesterday_high", 0),
            "yesterday_low": ref_levels.get("yesterday_low", 0),
            "current_price": round(current_price, 2)
        },
        "session_extremes": {
            "pre_market_high": session_high,
            "pre_market_low": session_low,
            "total_range_dollars": round(total_range, 2)
        },
        "value_migration_log": value_migration_log,
        "key_level_rejections": ranked_rejections,
        "time_based_acceptance": time_based_levels # NEW Field
    }

    return context_card, blocks

# ==========================================
# 3. Visualization
# ==========================================
def visualize_context(df, json_data, ref_levels):
    if df is None or df.empty:
        st.write("No data to visualize.")
        return

    fig = go.Figure()

    # 1. Candlesticks
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['Open'], high=df['High'],
        low=df['Low'], close=df['Close'],
        name='Price'
    ))

    # 2. Plot POCs
    for entry in json_data['value_migration_log']:
        t_window = entry['time_window']
        t_start_str = t_window.split(' - ')[0]
        poc = entry['observations']['most_traded_price_level']

        day_str = df.index[0].strftime("%Y-%m-%d")
        try:
            t_start = pd.to_datetime(f"{day_str} {t_start_str}").tz_localize("US/Eastern")
        except:
             t_start = pd.to_datetime(f"{day_str} {t_start_str}")
             if df.index.tz is not None: t_start = t_start.tz_localize(df.index.tz)

        t_end = t_start + timedelta(minutes=30)

        fig.add_shape(
            type="line",
            x0=t_start, y0=poc, x1=t_end, y1=poc,
            line=dict(color="cyan", width=2, dash="dot")
        )

    # 3. Plot Impact Zones (Red/Green)
    for rej in json_data['key_level_rejections']:
        color = "Red" if rej['type'] == "RESISTANCE" else "Green"
        level = rej['level']
        rank_str = f"#{rej['rank']}"

        width = 3 if rej['rank'] == 1 else 1

        fig.add_hline(
            y=level,
            line_color=color,
            line_width=width,
            line_dash="dash",
            annotation_text=f"{rej['type']} {rank_str} (Score: {rej['strength_score']})",
            annotation_position="top right" if rej['type'] == "RESISTANCE" else "bottom right"
        )

    # 4. Plot Time-Based Acceptance Levels (Blue) - NEW
    for time_lvl in json_data.get('time_based_acceptance', []):
        fig.add_hline(
            y=time_lvl['level'],
            line_color="Blue",
            line_width=2,
            line_dash="dot",
            annotation_text=f"Time Level (x{time_lvl['count']})",
            annotation_position="bottom left"
        )

    # 5. Plot Reference Levels
    if ref_levels:
        fig.add_hline(y=ref_levels.get("yesterday_high", 0), line_color="gray", line_dash="dash", annotation_text="Y_High")
        fig.add_hline(y=ref_levels.get("yesterday_low", 0), line_color="gray", line_dash="dash", annotation_text="Y_Low")
        fig.add_hline(y=ref_levels.get("yesterday_close", 0), line_color="orange", line_width=1, annotation_text="Y_Close")

    fig.update_layout(
        title=f"Context Engine: {json_data['meta']['ticker']}",
        xaxis_title="Pre-Market Time (ET)",
        yaxis_title="Price",
        template="plotly_dark",
        xaxis_rangeslider_visible=False,
        height=700
    )

    st.plotly_chart(fig, use_container_width=True)

# ==========================================
# Main Execution
# ==========================================
ticker_input = st.text_input("Enter Ticker", value="RDDT")
if st.button("Run Simulation"):
    
    st.write(f"--- Starting Context Engine for {ticker_input} ---")

    # 1. Try Real Data
    df, ref_levels = fetch_real_data(ticker_input)

    # 2. Fallback to Dynamic Synthetic if Real fails/empty
    if df is None:
        st.write("⚠️ Switching to Dynamic Synthetic Generator.")
        df, ref_levels = generate_synthetic_data(ticker_input)

    # 3. Analyze
    card, blocks = analyze_market_context(df, ref_levels, ticker_input)

    # 4. Output JSON
    st.subheader("RAW OBSERVATION CARD (JSON)")
    st.json(card)

    # 5. Visualize
    visualize_context(df, card, ref_levels)
