import libsql_client
from datetime import date
from modules.core.config import TURSO_DB_URL, TURSO_AUTH_TOKEN, TURSO_PRICE_DB_URL, TURSO_PRICE_AUTH_TOKEN
from modules.ai.ai_services import TRACKER

def inspect(target_date: date, logger=None):
    """
    Performs a deep inspection of the database for a specific date.
    """
    def log_msg(msg):
        if logger:
            logger.log(msg)
        else:
            print(msg)

    try:
        if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
            log_msg("❌ CRITICAL: Turso DB URL or Auth Token not found in config/Infisical.")
            return

        db_url = TURSO_DB_URL
        auth_token = TURSO_AUTH_TOKEN

        # Force HTTPS
        https_url = db_url.replace("libsql://", "https://")
        
        client = libsql_client.create_client_sync(url=https_url, auth_token=auth_token)
        log_msg("✅ Connected to Database.")

        date_str = target_date.isoformat()
        log_msg(f"\n--- 🔎 DATABASE INSPECTION: {date_str} ---")
        
        # 1. Check News (aw_daily_news)
        try:
            rs = client.execute("SELECT COUNT(*) FROM aw_daily_news WHERE target_date = ?", [date_str])
            count = rs.rows[0][0]
            status = "✅ PRESENT" if count > 0 else "❌ MISSING"
            log_msg(f"Market News: {status}")
            TRACKER.set_result("market_news", status)
        except Exception as e:
            log_msg(f"Error checking news: {e}")

        # 2. Check Economy Card (aw_economy_cards)
        try:
            rs = client.execute("SELECT COUNT(*) FROM aw_economy_cards WHERE date = ?", [date_str])
            count = rs.rows[0][0]
            status = "✅ PRESENT" if count > 0 else "❌ MISSING"
            log_msg(f"Economy Card: {status}")
            TRACKER.set_result("economy_card", status)
        except Exception as e:
            log_msg(f"Error checking economy card: {e}")

        # 3. Check Updated Tickers (aw_company_cards)
        try:
            rs = client.execute("SELECT ticker FROM aw_company_cards WHERE date = ? ORDER BY ticker ASC", [date_str])
            tickers = [row[0] for row in rs.rows]
            if tickers:
                log_msg(f"Updated Tickers ({len(tickers)}): {', '.join(tickers)}")
                TRACKER.set_result("updated_tickers", f"{len(tickers)} found")
                # Also add to details for execution log
                TRACKER.metrics.details.append(f"📦 Tickers: {', '.join(tickers)}")
            else:
                log_msg("Updated Tickers: ❌ NONE FOUND")
                TRACKER.set_result("updated_tickers", "❌ None")
        except Exception as e:
            log_msg(f"Error checking updated tickers: {e}")

        # 4. Check Market Data Rows (External Price DB)
        if not TURSO_PRICE_DB_URL:
            log_msg("⚠️ TURSO_PRICE_DB_URL not found. Skipping price DB check.")
        else:
            try:
                price_url = TURSO_PRICE_DB_URL.replace("libsql://", "https://")
                price_client = libsql_client.create_client_sync(url=price_url, auth_token=TURSO_PRICE_AUTH_TOKEN)
                
                # Check row count for that date using date() function on timestamp
                rs = price_client.execute("SELECT COUNT(*) FROM market_data WHERE date(timestamp) = ?", [date_str])
                row_count = rs.rows[0][0]
                log_msg(f"Market Data Rows (Price DB): {row_count:,}")
                TRACKER.set_result("market_data_rows", f"{row_count:,}")
                price_client.close()
            except Exception as e:
                log_msg(f"❌ Price DB Check Failed: {e}")

        client.close()
        log_msg("\nInspection Complete.")

    except Exception as e:
        log_msg(f"❌ Inspection Failed: {e}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, required=True, help="Target date (YYYY-MM-DD)")
    args = parser.parse_args()
    
    try:
        target_date = date.fromisoformat(args.date)
        inspect(target_date)
    except Exception as e:
        print(f"❌ Error: {e}")
