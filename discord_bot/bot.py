import os
import sys
import discord
from discord.ext import commands
from datetime import date
import logging
import asyncio

# Path hack for local development vs Railway root
# If Running on Railway with 'discord_bot' as root, the current dir contains everything.
# If running locally, 'discord_bot' is a subfolder, and we need the parent dir in sys.path.
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)

if os.path.exists(os.path.join(parent_dir, "modules")):
    sys.path.insert(0, parent_dir)
else:
    sys.path.insert(0, current_dir)

from modules.core.config import infisical_mgr, AVAILABLE_MODELS
from modules.core.logger import AppLogger
from modules.data.db_utils import upsert_daily_inputs, get_db_connection
from modules.ai.ai_services import update_economy_card, TRACKER
# We try to import from parent or local depending on root
try:
    from main import run_update_economy
except ImportError:
    # If on Railway and main.py isn't at root, we might need a different import
    # but run_update_economy is crucial. Assuming it will be available.
    pass

# Setup Logger
logger = AppLogger("discord_bot")

# 1. Load Discord Token
DISCORD_TOKEN = infisical_mgr.get_secret("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")

if not DISCORD_TOKEN:
    logger.error("❌ DISCORD_BOT_TOKEN not found in Infisical or environment.")
    # We don't exit here to allow for manual setup if needed, but it will fail on run()

# 2. Setup Bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logger.log(f"✅ Bot is logged in as {bot.user.name} (ID: {bot.user.id})")
    print("------")

@bot.command()
async def inputnews(ctx, date_str: str, *, news_text: str):
    """
    Manually input market news for a specific date.
    Usage: !inputnews 2026-02-13 Fed cuts rates...
    """
    try:
        target_date = date.fromisoformat(date_str)
    except ValueError:
        await ctx.send(f"❌ Invalid date format: `{date_str}`. Use YYYY-MM-DD.")
        return

    if upsert_daily_inputs(target_date, news_text):
        await ctx.send(f"✅ Market news successfully saved for **{target_date}**.")
        logger.log(f"Discord: Market news saved for {target_date}")
    else:
        await ctx.send(f"❌ Failed to save market news for {target_date}.")

@bot.command()
async def updateeconomy(ctx, date_str: str, model_name: str = "gemini-3-flash-free"):
    """
    Trigger the Economy Card update for a specific date.
    Usage: !updateeconomy 2026-02-13 [model_name]
    """
    from modules.ai.ai_services import TRACKER
    
    try:
        target_date = date.fromisoformat(date_str)
    except ValueError:
        await ctx.send(f"❌ Invalid date format: `{date_str}`. Use YYYY-MM-DD.")
        return

    if model_name not in AVAILABLE_MODELS:
        await ctx.send(f"⚠️ Unknown model: `{model_name}`. Available: {', '.join(AVAILABLE_MODELS.keys())}")
        return

    status_msg = await ctx.send(f"🧠 **Starting Economy Update** for {target_date} using `{model_name}`...")
    
    # Start tracking
    TRACKER.start()

    # We run the synchronous update in a separate thread to avoid blocking the bot
    def run_sync_update():
        run_update_economy(target_date, model_name, logger)

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, run_sync_update)
        TRACKER.finish()
        
        # Get metrics
        summary = TRACKER.get_summary()
        
        # Build Dashboard Embed
        embed = discord.Embed(
            title=f"📊 Execution Dashboard: {target_date}",
            description=f"Economy Card update completed for logical session date.",
            color=0x2ecc71 if summary["success_rate"] == "100.0%" else 0xf1c40f
        )
        
        embed.add_field(name="🕒 Duration", value=summary["duration"], inline=True)
        embed.add_field(name="🤖 API Calls", value=str(summary["total_calls"]), inline=True)
        embed.add_field(name="🪙 Token Usage", value=f"{summary['total_tokens']:,}", inline=True)
        embed.add_field(name="✅ Success Rate", value=summary["success_rate"], inline=True)
        
        if summary["details"]:
            details_text = "\n".join(summary["details"])[:1024]
            embed.add_field(name="📝 Execution Log", value=details_text, inline=False)
            
        if summary["errors"]:
            error_text = "\n".join(summary["errors"])[:1024]
            embed.add_field(name="⚠️ Failures", value=error_text, inline=False)
            
        embed.set_footer(text=f"Model: {model_name} | KeyManager Tracking Active")
        embed.timestamp = discord.utils.utcnow()

        await status_msg.edit(content="✅ **Economy Card Update Complete**")
        await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ **Error during update**: {e}")
        logger.error(f"Discord Command Error (updateeconomy): {e}")

@bot.command()
async def inspect(ctx):
    """
    Briefly inspect the database state.
    """
    conn = get_db_connection()
    if not conn:
        await ctx.send("❌ Failed to connect to database.")
        return

    try:
        # Get counts or basic info
        rs = conn.execute("SELECT count(*) FROM daily_inputs")
        news_count = rs.rows[0][0]
        
        rs_eco = conn.execute("SELECT count(*) FROM economy_cards")
        eco_count = rs_eco.rows[0][0]
        
        embed = discord.Embed(title="📊 Database Snapshot", color=0x3498db)
        embed.add_field(name="Market News Records", value=str(news_count), inline=True)
        embed.add_field(name="Economy Card Records", value=str(eco_count), inline=True)
        
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Error inspecting DB: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        print("❌ Bot cannot start: No DISCORD_BOT_TOKEN provided.")
