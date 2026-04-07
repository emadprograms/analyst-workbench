import os
import sys
import discord
from discord.ext import commands
import aiohttp
import asyncio
import json
import io
from datetime import datetime, timedelta, timezone

# Add bot directory to sys.path so plain imports (config, ui_components) always resolve,
# then add project root so cross-package imports (modules.*) work when the full repo is available.
BOT_DIR = os.path.abspath(os.path.dirname(__file__))
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

PROJECT_ROOT = os.path.abspath(os.path.join(BOT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

# Core Imports (plain imports — discord_bot/ is the Railway root, not a package)
from config import (
    DISCORD_TOKEN, GITHUB_TOKEN, GITHUB_REPO, WORKFLOW_FILENAME, ACTIONS_URL,
    STOCK_TICKERS, ETF_TICKERS, ALL_TICKERS
)
from ui_components import (
    DateSelectionView, NewsModal, BuildTypeSelectionView, TickerSelectionView,
    ViewTypeSelectionView, EditNotesTickerSelectionView, EditNotesModal, EditNotesTriggerView, TargetSelectionView
)
from modules.data.db_utils import (
    get_all_tickers_from_db, get_company_card_and_notes, update_ticker_notes, 
    get_daily_inputs, get_archived_economy_card, get_archived_company_card, get_ticker_stats,
    upsert_daily_inputs, get_archived_temp_company_card, get_temp_card_tickers_for_date
)
from modules.data.inspect_db import inspect as db_inspect_func
import re

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Major Action System Online | Logged in as: {bot.user.name}")
    
    # --- Startup Credential Check ---
    from modules.core.config import TURSO_DB_URL, TURSO_AUTH_TOKEN
    from modules.ai.ai_services import KEY_MANAGER
    
    if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
        print("❌ CRITICAL: Turso DB credentials not found. DB features will fail.")
    else:
        print("✅ Turso DB credentials verified.")
        
    if not KEY_MANAGER:
        print("❌ CRITICAL: KeyManager failed to initialize. AI features will fail.")
    else:
        print("✅ KeyManager initialized and ready.")

# --- Logic Helpers ---

async def get_stock_tickers() -> list[str]:
    """Fetches active tickers from DB and filters out ETFs."""
    loop = asyncio.get_event_loop()
    db_tickers = await loop.run_in_executor(None, get_all_tickers_from_db)
    stock_list = [t for t in db_tickers if t not in ETF_TICKERS]
    return stock_list or STOCK_TICKERS

def get_target_date(date_input: str = None) -> str | None:
    today = datetime.now(timezone.utc)
    if not date_input: return None
    if date_input == "0": return today.strftime("%Y-%m-%d")
    if date_input.startswith("-") and date_input[1:].isdigit():
        try:
            days_back = int(date_input[1:])
            target = today - timedelta(days=days_back)
            return target.strftime("%Y-%m-%d")
        except: pass
    return date_input

async def fetch_url_content(url: str) -> str | None:
    """Fetches content from a URL, with special handling for Pastebin."""
    # Convert Pastebin links to raw if needed
    if "pastebin.com" in url and "/raw/" not in url:
        # Example: https://pastebin.com/abcd -> https://pastebin.com/raw/abcd
        url = url.replace("pastebin.com/", "pastebin.com/raw/")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    return await resp.text()
                print(f"[fetch_url_content] HTTP {resp.status} for {url}")
    except Exception as e:
        print(f"Error fetching URL {url}: {e}")
    return None

async def save_news(date_str, content):
    """Saves news content directly to the database."""
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, upsert_daily_inputs, target_date, content)

async def _fetch_latest_run_url(session: aiohttp.ClientSession, headers: dict) -> str | None:
    """
    Waits ~5 s then polls GitHub once for the most recently triggered workflow run.

    GitHub takes a few seconds to register the new run, so we wait before querying.
    Only a single attempt is made to keep added latency minimal.  Callers **must**
    fall back to the general ``ACTIONS_URL`` when this returns ``None``.
    """
    if not GITHUB_REPO or not WORKFLOW_FILENAME:
        return None
    runs_url = (
        f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/"
        f"{WORKFLOW_FILENAME}/runs?per_page=1&event=workflow_dispatch"
    )
    await asyncio.sleep(5)
    try:
        async with session.get(
            runs_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                runs = data.get("workflow_runs", [])
                if runs:
                    return runs[0].get("html_url")
    except Exception:
        pass  # Non-fatal — callers have a fallback URL
    return None


async def dispatch_github_action(inputs: dict) -> tuple[bool, str, str | None]:
    """
    Dispatches a GitHub Actions workflow and attempts to confirm the run started.

    Returns a 3-tuple:
        (True,  "Dispatched", run_url_or_None)
            – GitHub accepted the request (HTTP 204).
            – ``run_url`` is the direct link to the specific Actions run if it could
              be confirmed within ~8 s, otherwise ``None``.  Callers should fall
              back to the general ``ACTIONS_URL`` when it is ``None``.

        (False, rich_error_message, None)
            – GitHub rejected the request or a network error occurred.
            – ``rich_error_message`` includes *both* the HTTP status code *and* a
              snippet of the response body so the user sees a meaningful error
              description instead of just a bare status number.
    """
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False, "Missing GITHUB_PAT or GITHUB_REPO configuration.", None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILENAME}/dispatches"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    data = {"ref": "main", "inputs": inputs}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as resp:
            if resp.status != 204:
                body = await resp.text()
                body_snippet = body[:300] if body else "(empty response body)"
                return False, f"GitHub Error {resp.status}: {body_snippet}", None
        # Dispatch confirmed (HTTP 204).  Now attempt one delayed poll to retrieve
        # the direct run URL so the user can monitor the specific run.
        async with aiohttp.ClientSession() as poll_session:
            run_url = await _fetch_latest_run_url(poll_session, headers)
        return True, "Dispatched", run_url

# --- Command Callbacks ---

async def fetch_economy_card(date_str):
    target_date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
    loop = asyncio.get_event_loop()
    card_json, _ = await loop.run_in_executor(None, get_archived_economy_card, target_date_obj)
    return card_json

async def fetch_company_card(date_str, ticker):
    target_date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
    loop = asyncio.get_event_loop()
    card_json, _ = await loop.run_in_executor(None, get_archived_company_card, target_date_obj, ticker)
    return card_json

async def fetch_notes(ticker):
    loop = asyncio.get_event_loop()
    _, current_notes, _ = await loop.run_in_executor(None, get_company_card_and_notes, ticker)
    return current_notes

async def save_notes(ticker, notes):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, update_ticker_notes, ticker, notes)

# --- Commands ---

@bot.command()
async def buildcards(ctx, date_indicator: str = None):
    """Interactive command to build Economy or Company cards."""
    target_date = get_target_date(date_indicator)
    stock_list = await get_stock_tickers()
    
    async def build_callback(interaction, selected_date):
        view = BuildTypeSelectionView(selected_date, dispatch_github_action, ACTIONS_URL, stock_list, TickerSelectionView)
        await interaction.response.edit_message(content=f"🏗️ **Building Cards for {selected_date}**\nWhich kind of card would you like to build?", view=view)
    if not target_date:
        await ctx.send("🗓️ **Select Date for Card Generation:**", view=DateSelectionView(build_callback))
    else:
        try:
            datetime.strptime(target_date, "%Y-%m-%d")
            view = BuildTypeSelectionView(target_date, dispatch_github_action, ACTIONS_URL, stock_list, TickerSelectionView)
            await ctx.send(f"🏗️ **Building Cards for {target_date}**\nWhich kind of card would you like to build?", view=view)
        except ValueError: await ctx.send(f"❌ Error: `{target_date}` is invalid.")

@bot.command()
async def viewcards(ctx, date_indicator: str = None):
    """Interactive command to view Economy or Company cards."""
    target_date = get_target_date(date_indicator)
    stock_list = await get_stock_tickers()

    async def view_callback(interaction, selected_date):
        view = ViewTypeSelectionView(selected_date, fetch_economy_card, fetch_company_card, stock_list)
        await interaction.response.edit_message(content=f"🔎 **Viewing Cards for {selected_date}**\nWhich kind of card would you like to view?", view=view)
    if not target_date:
        await ctx.send("🗓️ **Select Date for Card Viewing:**", view=DateSelectionView(view_callback))
    else:
        try:
            datetime.strptime(target_date, "%Y-%m-%d")
            view = ViewTypeSelectionView(target_date, fetch_economy_card, fetch_company_card, stock_list)
            await ctx.send(f"🔎 **Viewing Cards for {target_date}**\nWhich kind of card would you like to view?", view=view)
        except ValueError: await ctx.send(f"❌ Error: `{target_date}` is invalid.")


@bot.command()
async def listcards(ctx):
    """Lists all tracked tickers and their last update status."""
    await ctx.send("🔍 **Fetching ticker status from database...**")
    loop = asyncio.get_event_loop()
    stats = await loop.run_in_executor(None, get_ticker_stats)
    if not stats:
        await ctx.send("⚠️ No tickers found in database.")
        return
    lines = ["📊 **Tracked Companies Status:**", "```", f"{'Ticker':<8} | {'Last Card Date':<15}", "-" * 30]
    for s in stats: lines.append(f"{s['ticker']:<8} | {s['last_card_date'] or 'No Cards Yet':<15}")
    lines.append("```")
    await ctx.send("\n".join(lines))

@bot.command()
async def editnotes(ctx, ticker: str = None):
    """Opens a dialog to edit historical notes for a company."""
    if ticker:
        ticker = ticker.upper()
        current_notes = await fetch_notes(ticker)
        modal = EditNotesModal(ticker, current_notes or "", save_notes)
        await ctx.send(f"📝 Click the button below to edit notes for **{ticker}**:", view=EditNotesTriggerView(modal))
    else:
        stock_list = await get_stock_tickers()
        await ctx.send("🏢 **Select a company to edit historical notes:**", view=EditNotesTickerSelectionView(stock_list, fetch_notes, save_notes))

@bot.command()
async def checknews(ctx, date_str: str = None):
    """Verifies market news ingestion for a specific date directly in the bot."""
    target_date_str = get_target_date(date_str)
    async def check_callback(interaction, selected_date_str):
        await interaction.response.edit_message(content=f"🔍 **Checking news** for **{selected_date_str}**... 🛰️", view=None)
        target_date_obj = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
        loop = asyncio.get_event_loop()
        market_news, _ = await loop.run_in_executor(None, get_daily_inputs, target_date_obj)
        if market_news:
            char_count = len(market_news)
            preview = market_news[:1000] + "..." if char_count > 1000 else market_news
            await interaction.followup.send(f"✅ **News Found for {selected_date_str} ({char_count:,} chars):**\n```\n{preview}\n```")
        else: await interaction.followup.send(f"❌ **NO NEWS FOUND** for **{selected_date_str}**.")
    if not target_date_str:
        await ctx.send("🔍 **Select Date to Check News:**", view=DateSelectionView(check_callback))
    else:
        try:
            target_date_obj = datetime.strptime(target_date_str, "%Y-%m-%d").date()
            msg = await ctx.send(f"🔍 **Checking news** for **{target_date_str}**... 🛰️")
            loop = asyncio.get_event_loop()
            market_news, _ = await loop.run_in_executor(None, get_daily_inputs, target_date_obj)
            if market_news:
                char_count = len(market_news)
                preview = market_news[:1000] + "..." if char_count > 1000 else market_news
                await msg.edit(content=f"✅ **News Found for {target_date_str} ({char_count:,} chars):**\n```\n{preview}\n```")
            else: await msg.edit(content=f"❌ **NO NEWS FOUND** for **{target_date_str}**.")
        except ValueError: await ctx.send(f"❌ Error: `{target_date_str}` is invalid.")

@bot.command()
async def inspect(ctx, date_str: str = None):
    """Performs a deep database inspection directly in the bot."""
    target_date_str = get_target_date(date_str)
    async def inspect_callback(interaction, selected_date_str):
        await interaction.response.edit_message(content=f"🔍 **Inspecting Database** for **{selected_date_str}**... 🛰️", view=None)
        class CapturingLogger:
            def __init__(self): self.lines = []
            def log(self, msg): self.lines.append(msg)
        cap_logger = CapturingLogger()
        target_date_obj = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, db_inspect_func, target_date_obj, cap_logger)
        await interaction.followup.send(f"```\n" + "\n".join(cap_logger.lines) + "\n```")
    if not target_date_str:
        await ctx.send("🔍 **Select Date to Inspect Database:**", view=DateSelectionView(inspect_callback))
    else:
        try:
            target_date_obj = datetime.strptime(target_date_str, "%Y-%m-%d").date()
            msg = await ctx.send(f"🔍 **Inspecting Database** for **{target_date_str}**... 🛰️")
            class CapturingLogger:
                def __init__(self): self.lines = []
                def log(self, msg): self.lines.append(msg)
            cap_logger = CapturingLogger()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, db_inspect_func, target_date_obj, cap_logger)
            await msg.edit(content=f"✅ **Inspection Complete for {target_date_str}:**\n```\n" + "\n".join(cap_logger.lines) + "\n```")
        except ValueError: await ctx.send(f"❌ Error: `{target_date_str}` is invalid.")

@bot.command()
async def inputnews(ctx, date_indicator: str = None):
    """Directly uploads news to DB from attachments, Pastebin URLs, or text box."""
    target_date = get_target_date(date_indicator)
    
    MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024  # 5 MB guard

    # --- 1. HANDLE ATTACHMENTS (.txt, .log) ---
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
        if attachment.filename.endswith(('.txt', '.log')):
            if attachment.size > MAX_ATTACHMENT_BYTES:
                await ctx.send(f"❌ File `{attachment.filename}` is too large ({attachment.size // 1024} KB). 5 MB max.")
                return
            if not target_date:
                async def file_date_cb(interaction, sel_date):
                    await interaction.response.edit_message(content=f"📁 **File Detected:** `{attachment.filename}`\nSaving for **{sel_date}**... 🛰️", view=None)
                    raw = await attachment.read()
                    text = raw.decode("utf-8", errors="replace")
                    success = await save_news(sel_date, text)
                    msg = await interaction.original_response()
                    if success: await msg.edit(content=f"✅ **Market news from file successfully saved** for **{sel_date}**! 🚀")
                    else: await msg.edit(content=f"❌ **Failed to save news** for **{sel_date}** to database.")
                await ctx.send(f"📁 **File detected:** `{attachment.filename}`\n🗓️ Select target date:", view=DateSelectionView(file_date_cb))
                return

            msg = await ctx.send(f"📁 **File Detected:** `{attachment.filename}`\nSaving for **{target_date}**... 🛰️")
            raw = await attachment.read()
            success = await save_news(target_date, raw.decode("utf-8", errors="replace"))
            if success: await msg.edit(content=f"✅ **Market news from file successfully saved** for **{target_date}**! 🚀")
            else: await msg.edit(content=f"❌ **Failed to save news** for **{target_date}** to database.")
            return

    # --- 2. HANDLE URLS (Pastebin, etc.) ---
    # Look for URLs in the message content.
    # The character class intentionally excludes whitespace and common Discord quote
    # characters so paths, query strings, and fragments are captured in full.
    url_pattern = r'https?://[^\s<>"\']+'  
    urls = re.findall(url_pattern, ctx.message.content)
    if urls:
        news_url = urls[0]
        if not target_date:
            async def url_date_cb(interaction, sel_date):
                await interaction.response.edit_message(content=f"🌐 **URL Detected:** `{news_url}`\nFetching and saving for **{sel_date}**... 🛰️", view=None)
                content = await fetch_url_content(news_url)
                if not content:
                    await interaction.followup.send(f"❌ **Failed to fetch content** from `{news_url}`.")
                    return
                success = await save_news(sel_date, content)
                msg = await interaction.original_response()
                if success: await msg.edit(content=f"✅ **Market news from URL successfully saved** for **{sel_date}**! 🚀")
                else: await msg.edit(content=f"❌ **Failed to save news** for **{sel_date}** to database.")
            await ctx.send(f"🌐 **URL detected:** `{news_url}`\n🗓️ Select target date:", view=DateSelectionView(url_date_cb))
            return
        
        msg = await ctx.send(f"🌐 **URL Detected:** `{news_url}`\nFetching and saving for **{target_date}**... 🛰️")
        content = await fetch_url_content(news_url)
        if not content:
            await msg.edit(content=f"❌ **Failed to fetch content** from `{news_url}`.")
            return
        success = await save_news(target_date, content)
        if success: await msg.edit(content=f"✅ **Market news from URL successfully saved** for **{target_date}**! 🚀")
        else: await msg.edit(content=f"❌ **Failed to save news** for **{target_date}** to database.")
        return

    # --- 3. HANDLE MODAL (Manual Text Entry) ---
    async def news_callback(interaction, sel_date):
        await interaction.response.send_modal(NewsModal(sel_date, save_news))
        try: await interaction.message.edit(content=f"🗓️ **News Entry Selected:** {sel_date}\n(Modal opened)", view=None)
        except: pass

    if not target_date:
        await ctx.send("🗓️ **Select Date for News Entry:**", view=DateSelectionView(news_callback))
    else:
        try:
            datetime.strptime(target_date, "%Y-%m-%d")
            class Trigger(discord.ui.View):
                def __init__(self, d, cb): super().__init__(); self.d = d; self.cb = cb
                @discord.ui.button(label=f"📝 Open Box for {target_date}", style=discord.ButtonStyle.primary)
                async def go(self, interaction, button):
                    await interaction.response.send_modal(NewsModal(self.d, self.cb))
                    try: await interaction.message.edit(content=f"✅ **Target Date:** {self.d}\n(Modal opened)", view=None)
                    except: pass
            await ctx.send(f"✅ Target Date: **{target_date}**", view=Trigger(target_date, save_news))
        except ValueError: await ctx.send(f"❌ Error: `{target_date}` is invalid.")

@bot.command()
async def getnews(ctx, arg1: str = None, arg2: str = None):
    """Fetches and summarizes news for Macro or a Company."""
    date_str = None
    target = None

    def is_date_or_offset(val):
        if not val: return False
        if val == "0" or (val.startswith("-") and val[1:].isdigit()):
            return True
        try:
            datetime.strptime(val, "%Y-%m-%d")
            return True
        except:
            return False

    if arg1 and arg2:
        if is_date_or_offset(arg1):
            date_str = get_target_date(arg1)
            target = arg2.upper()
        else:
            date_str = get_target_date(arg2)
            target = arg1.upper()
    elif arg1:
        if is_date_or_offset(arg1):
            date_str = get_target_date(arg1)
        else:
            date_str = get_target_date("0") # Default to today
            target = arg1.upper()
            
    async def finish_callback(interaction, selected_date, selected_target):
        await interaction.response.edit_message(content=f"📰 **Fetching and summarizing {selected_target} news** for **{selected_date}**... 🛰️\n*(This may take a few seconds)*", view=None)
        
        try:
            target_date_obj = datetime.strptime(selected_date, "%Y-%m-%d").date()
            
            # 1. Fetch news from DB
            loop = asyncio.get_event_loop()
            market_news, _ = await loop.run_in_executor(None, get_daily_inputs, target_date_obj)
            
            if not market_news:
                await interaction.followup.send(f"❌ **NO NEWS FOUND** for **{selected_date}**.")
                return
                
            # 2. Filter news
            from modules.ai.ai_services import filter_daily_news_for_macro, filter_daily_news_for_company, summarize_news_with_gemini, filter_daily_news_for_custom_sector
            from modules.core.logger import AppLogger
            logger = AppLogger()
            
            is_custom_sector = False
            if selected_target == "MACRO":
                filtered_news = filter_daily_news_for_macro(market_news)
            elif selected_target.startswith("SECTOR:"):
                # Handle custom sector selection
                sector_name = selected_target.split(":", 1)[1]
                filtered_news = filter_daily_news_for_custom_sector(market_news, sector_name)
                # Overwrite selected_target to just the sector name for cleaner UI
                selected_target = sector_name
                is_custom_sector = True
            else:
                filtered_news = filter_daily_news_for_company(market_news, selected_target, "")
                
            if "No specific company or sector news found" in filtered_news or "No macro news found" in filtered_news or "No specific sector news found" in filtered_news or not filtered_news.strip():
                await interaction.followup.send(f"⚠️ **No {selected_target} news found** in the database for **{selected_date}**.")
                return
                
            # 3. Summarize with Gemini
            summary = await loop.run_in_executor(None, summarize_news_with_gemini, filtered_news, selected_target, logger, is_custom_sector)
            
            # 4. Send response
            embeds = []
            chunks = [summary[i:i+4000] for i in range(0, len(summary), 4000)]
            for i, chunk in enumerate(chunks):
                title = f"📰 {selected_target} News Summary | {selected_date}"
                if len(chunks) > 1:
                    title += f" (Part {i+1}/{len(chunks)})"
                embed = discord.Embed(title=title, description=chunk, color=discord.Color.blue())
                if i == len(chunks) - 1:
                    embed.set_footer(text="Powered by Gemini 3 Flash")
                embeds.append(embed)
            
            await interaction.followup.send(embeds=embeds)
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            print(f"Error in finish_callback: {error_trace}")
            await interaction.followup.send(f"❌ **An internal error occurred:** {e}")

    if not date_str and not target:
        async def date_cb(interaction, selected_date):
            await interaction.response.edit_message(content=f"🗓️ Date Selected: **{selected_date}**\nNow, select target:", view=TargetSelectionView(selected_date, finish_callback))
        await ctx.send("🗓️ **Select Date for News Summary:**", view=DateSelectionView(date_cb))
    elif date_str and not target:
        await ctx.send(f"🗓️ Date Selected: **{date_str}**\nNow, select target:", view=TargetSelectionView(date_str, finish_callback))
    elif not date_str and target:
        async def date_cb_with_target(interaction, selected_date):
            await finish_callback(interaction, selected_date, target)
        await ctx.send(f"🎯 Target: **{target}**\n🗓️ **Select Date:**", view=DateSelectionView(date_cb_with_target))
    else:
        # We have both, directly execute
        msg = await ctx.send(f"📰 **Fetching and summarizing {target} news** for **{date_str}**... 🛰️\n*(This may take a few seconds)*")
        
        try:
            target_date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            loop = asyncio.get_event_loop()
            market_news, _ = await loop.run_in_executor(None, get_daily_inputs, target_date_obj)
            
            if not market_news:
                await msg.edit(content=f"❌ **NO NEWS FOUND** for **{date_str}**.")
                return
                
            # 2. Filter news
            from modules.ai.ai_services import filter_daily_news_for_macro, filter_daily_news_for_company, summarize_news_with_gemini, filter_daily_news_for_custom_sector
            from modules.core.logger import AppLogger
            logger = AppLogger()
            
            is_custom_sector = False
            if target == "MACRO":
                filtered_news = filter_daily_news_for_macro(market_news)
            elif target.startswith("SECTOR:"):
                # Handle custom sector selection
                sector_name = target.split(":", 1)[1]
                filtered_news = filter_daily_news_for_custom_sector(market_news, sector_name)
                # Overwrite selected_target to just the sector name for cleaner UI
                target = sector_name
                is_custom_sector = True
            else:
                filtered_news = filter_daily_news_for_company(market_news, target, "")
                
            if "No specific company or sector news found" in filtered_news or "No macro news found" in filtered_news or "No specific sector news found" in filtered_news or not filtered_news.strip():
                await msg.edit(content=f"⚠️ **No {target} news found** in the database for **{date_str}**.")
                return
                
            summary = await loop.run_in_executor(None, summarize_news_with_gemini, filtered_news, target, logger, is_custom_sector)
            
            embeds = []
            chunks = [summary[i:i+4000] for i in range(0, len(summary), 4000)]
            for i, chunk in enumerate(chunks):
                title = f"📰 {target} News Summary | {date_str}"
                if len(chunks) > 1:
                    title += f" (Part {i+1}/{len(chunks)})"
                embed = discord.Embed(title=title, description=chunk, color=discord.Color.blue())
                if i == len(chunks) - 1:
                    embed.set_footer(text="Powered by Gemini 3 Flash")
                embeds.append(embed)
            
            await msg.edit(content=None, embeds=embeds)
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            print(f"Error in getnews: {error_trace}")
            await msg.edit(content=f"❌ **An internal error occurred:** {e}")

@bot.command()
async def buildtempcards(ctx, *, args_str: str = None):
    """Build temp company cards for non-tracked tickers.
    Usage: !buildtempcards SOFI, RIVN [date]
    Date can be: 0 (today), -1 (yesterday), -2, YYYY-MM-DD, or omitted (defaults to today).
    """
    if not args_str:
        await ctx.send("❌ **Usage:** `!buildtempcards SOFI, RIVN [date]`\n"
                       "Example: `!buildtempcards SOFI, RIVN 0` (today)\n"
                       "Example: `!buildtempcards SOFI, RIVN -1` (yesterday)")
        return

    # Parse: split into tokens, identify tickers vs date indicator
    # Tickers are comma-separated (possibly with spaces), date is the last token if it looks like a date
    parts = [p.strip() for p in args_str.split(",")]
    
    # The last part might contain the date indicator after the last ticker
    # e.g. "RIVN 0" or "RIVN -1" or "RIVN 2026-04-03"
    last_part_tokens = parts[-1].strip().split()
    date_indicator = None
    
    if len(last_part_tokens) > 1:
        potential_date = last_part_tokens[-1]
        # Check if last token is a date indicator
        if potential_date == "0" or (potential_date.startswith("-") and potential_date[1:].isdigit()):
            date_indicator = potential_date
            parts[-1] = " ".join(last_part_tokens[:-1])
        elif len(potential_date) == 10 and potential_date.count("-") == 2:
            try:
                datetime.strptime(potential_date, "%Y-%m-%d")
                date_indicator = potential_date
                parts[-1] = " ".join(last_part_tokens[:-1])
            except ValueError:
                pass
    
    # Extract tickers
    tickers = [p.strip().upper() for p in parts if p.strip()]
    if not tickers:
        await ctx.send("❌ **No tickers provided.** Usage: `!buildtempcards SOFI, RIVN [date]`")
        return
    
    # Resolve date
    target_date = get_target_date(date_indicator or "0")  # Default to today
    
    tickers_str = ",".join(tickers)
    await ctx.send(
        f"🚀 **Building TEMP Cards** for **{len(tickers)}** ticker(s): `{tickers_str}`\n"
        f"📅 **Date:** {target_date}\n"
        f"📡 Dispatching GitHub Action..."
    )
    msg = await ctx.channel.fetch_message(ctx.channel.last_message_id)
    
    inputs = {
        "target_date": target_date,
        "action": "update-temp-company",
        "tickers": tickers_str
    }
    success, message, run_url = await dispatch_github_action(inputs)
    monitor_link = run_url or ACTIONS_URL
    if success:
        await msg.edit(
            content=f"🚀 **TEMP Cards Dispatched!** ({len(tickers)} tickers: `{tickers_str}`)\n"
                    f"📅 **Date:** {target_date}\n"
                    f"✅ **Dispatched!** (ETA: ~3-5 mins)\n"
                    f"🔗 [Monitor Progress](<{monitor_link}>) 📡⏱️\n"
                    f"💡 Use `!viewtempcards {target_date}` to view results when done."
        )
    else:
        await msg.edit(
            content=f"❌ **TEMP Card Build Failed:** {message}"
        )

@bot.command()
async def viewtempcards(ctx, date_indicator: str = None):
    """View previously generated temp company cards.
    Usage: !viewtempcards [date]
    Date can be: 0 (today), -1, -2, YYYY-MM-DD, or omitted (triggers date picker).
    """
    target_date_str = get_target_date(date_indicator)

    async def fetch_and_show(interaction_or_ctx, selected_date_str, is_interaction=True):
        """Core logic to fetch and display temp cards."""
        if is_interaction:
            await interaction_or_ctx.response.edit_message(
                content=f"🔍 **Fetching TEMP cards** for **{selected_date_str}**... 🛰️", view=None
            )
        
        target_date_obj = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
        loop = asyncio.get_event_loop()
        
        # Get all temp tickers for this date
        temp_tickers = await loop.run_in_executor(
            None, get_temp_card_tickers_for_date, target_date_obj
        )
        
        if not temp_tickers:
            msg_text = f"❌ **No TEMP cards found** for **{selected_date_str}**.\n💡 Use `!buildtempcards TICKER1, TICKER2 {selected_date_str}` to create some."
            if is_interaction:
                await interaction_or_ctx.followup.send(msg_text)
            else:
                await interaction_or_ctx.edit(content=msg_text)
            return
        
        # Fetch all cards
        files = []
        for ticker in temp_tickers:
            card_json, _ = await loop.run_in_executor(
                None, get_archived_temp_company_card, target_date_obj, ticker
            )
            if card_json:
                try:
                    formatted = json.dumps(json.loads(card_json), indent=2)
                    file_data = io.BytesIO(formatted.encode("utf-8"))
                    files.append(discord.File(file_data, filename=f"TEMP_{ticker}_Card_{selected_date_str}.json"))
                except:
                    pass
        
        if files:
            header = f"✅ **TEMP Company Cards ({selected_date_str})** — {len(files)} ticker(s): `{', '.join(temp_tickers)}`"
            # Discord limit is 10 files per message
            chunks = [files[i:i + 10] for i in range(0, len(files), 10)]
            for i, chunk in enumerate(chunks):
                msg_text = f"{header} — Part {i+1}" if len(chunks) > 1 else header
                if is_interaction:
                    await interaction_or_ctx.followup.send(msg_text, files=chunk)
                else:
                    await interaction_or_ctx.edit(content=msg_text)
                    # For subsequent chunks, send new messages
                    if len(chunks) > 1 and i < len(chunks) - 1:
                        await interaction_or_ctx.channel.send(files=chunks[i+1])
        else:
            msg_text = f"⚠️ **Cards exist but could not be loaded** for **{selected_date_str}**."
            if is_interaction:
                await interaction_or_ctx.followup.send(msg_text)
            else:
                await interaction_or_ctx.edit(content=msg_text)

    if not target_date_str:
        # Show date picker
        async def view_temp_callback(interaction, selected_date):
            await fetch_and_show(interaction, selected_date, is_interaction=True)
        await ctx.send("🗓️ **Select Date to View TEMP Cards:**", view=DateSelectionView(view_temp_callback))
    else:
        try:
            datetime.strptime(target_date_str, "%Y-%m-%d")
            msg = await ctx.send(f"🔍 **Fetching TEMP cards** for **{target_date_str}**... 🛰️")
            
            target_date_obj = datetime.strptime(target_date_str, "%Y-%m-%d").date()
            loop = asyncio.get_event_loop()
            
            temp_tickers = await loop.run_in_executor(
                None, get_temp_card_tickers_for_date, target_date_obj
            )
            
            if not temp_tickers:
                await msg.edit(
                    content=f"❌ **No TEMP cards found** for **{target_date_str}**.\n"
                            f"💡 Use `!buildtempcards TICKER1, TICKER2 {target_date_str}` to create some."
                )
                return
            
            files = []
            for ticker in temp_tickers:
                card_json, _ = await loop.run_in_executor(
                    None, get_archived_temp_company_card, target_date_obj, ticker
                )
                if card_json:
                    try:
                        formatted = json.dumps(json.loads(card_json), indent=2)
                        file_data = io.BytesIO(formatted.encode("utf-8"))
                        files.append(discord.File(file_data, filename=f"TEMP_{ticker}_Card_{target_date_str}.json"))
                    except:
                        pass
            
            if files:
                header = f"✅ **TEMP Company Cards ({target_date_str})** — {len(files)} ticker(s): `{', '.join(temp_tickers)}`"
                chunks = [files[i:i + 10] for i in range(0, len(files), 10)]
                for i, chunk in enumerate(chunks):
                    chunk_msg = f"{header} — Part {i+1}" if len(chunks) > 1 else header
                    if i == 0:
                        await msg.edit(content=chunk_msg)
                        await ctx.send(files=chunk)
                    else:
                        await ctx.send(chunk_msg, files=chunk)
            else:
                await msg.edit(content=f"⚠️ **Cards exist but could not be loaded** for **{target_date_str}**.")
        except ValueError:
            await ctx.send(f"❌ Error: `{target_date_str}` is invalid.")

@bot.command()
async def movers(ctx, date_indicator: str = None):
    """Scans today's news for the most important pre-market movers.
    Usage: !movers [date]
    Date can be: 0 (today), -1 (yesterday), YYYY-MM-DD, or omitted (defaults to today).
    """
    target_date_str = get_target_date(date_indicator or "0")  # Default to today
    
    try:
        datetime.strptime(target_date_str, "%Y-%m-%d")
    except ValueError:
        await ctx.send(f"❌ Error: `{target_date_str}` is invalid.")
        return

    msg = await ctx.send(
        f"🔍 **Scanning Pre-Market Movers** for **{target_date_str}**...\n"
        f"📰 Step 1/4: Fetching news from database... 🛰️"
    )

    try:
        # --- Step 1: Fetch news from DB ---
        target_date_obj = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        loop = asyncio.get_event_loop()
        market_news, _ = await loop.run_in_executor(None, get_daily_inputs, target_date_obj)

        if not market_news:
            await msg.edit(content=f"❌ **NO NEWS FOUND** for **{target_date_str}**.\n💡 Use `!inputnews {target_date_str}` to add news first.")
            return

        # --- Step 2: AI extracts and ranks tickers by importance ---
        await msg.edit(content=(
            f"🔍 **Scanning Pre-Market Movers** for **{target_date_str}**...\n"
            f"✅ News found ({len(market_news):,} chars)\n"
            f"🧠 Step 2/4: AI ranking tickers by importance..."
        ))

        from modules.ai.ai_services import extract_and_rank_movers, generate_movers_briefing
        from modules.core.logger import AppLogger
        logger = AppLogger()

        ranked_tickers = await loop.run_in_executor(None, extract_and_rank_movers, market_news, logger)

        if not ranked_tickers or len(ranked_tickers) < 2:
            await msg.edit(content=f"⚠️ **Not enough stock movers found** in the news for **{target_date_str}**.\nAI found: `{ranked_tickers}`")
            return

        # Cap at 15 for Yahoo Finance batch
        ranked_tickers = ranked_tickers[:15]

        # --- Step 3: Batch fetch Yahoo Finance data ---
        await msg.edit(content=(
            f"🔍 **Scanning Pre-Market Movers** for **{target_date_str}**...\n"
            f"✅ News found ({len(market_news):,} chars)\n"
            f"✅ AI identified {len(ranked_tickers)} tickers: `{', '.join(ranked_tickers)}`\n"
            f"📡 Step 3/4: Fetching market data from Yahoo Finance..."
        ))

        from modules.data.yahoo_fetcher import fetch_movers_snapshot
        market_data = await loop.run_in_executor(None, fetch_movers_snapshot, ranked_tickers, logger)

        # Build ordered dict preserving AI importance ranking, with market data merged
        ticker_data = {}
        for ticker in ranked_tickers:
            if ticker in market_data:
                ticker_data[ticker] = market_data[ticker]

        if not ticker_data:
            await msg.edit(content=f"⚠️ **Could not fetch market data** from Yahoo Finance for any of the {len(ranked_tickers)} tickers.")
            return

        # --- Step 4: AI generates catalyst summaries ---
        await msg.edit(content=(
            f"🔍 **Scanning Pre-Market Movers** for **{target_date_str}**...\n"
            f"✅ News found ({len(market_news):,} chars)\n"
            f"✅ AI identified {len(ranked_tickers)} tickers\n"
            f"✅ Yahoo Finance data for {len(ticker_data)} tickers\n"
            f"🧠 Step 4/4: AI generating catalyst summaries..."
        ))

        briefing = await loop.run_in_executor(
            None, generate_movers_briefing, market_news, ticker_data, logger
        )

        # --- Step 5: Build the Discord embed ---
        market_theme = "Market movers identified"
        catalyst_map = {}

        if briefing:
            market_theme = briefing.get("market_theme", market_theme)
            for pick in briefing.get("picks", []):
                t = pick.get("ticker", "").upper()
                catalyst_map[t] = {
                    "direction": pick.get("direction", "neutral"),
                    "catalyst": pick.get("catalyst", "No specific catalyst identified"),
                }

        # --- Step 5a: Fact-check each catalyst against source news ---
        from modules.ai.ai_services import verify_catalyst_against_news
        verification_map = {}
        for ticker_sym, ai_info in catalyst_map.items():
            verified = verify_catalyst_against_news(
                ticker_sym, ai_info["catalyst"], market_news
            )
            verification_map[ticker_sym] = verified

        # Build embed
        embed = discord.Embed(
            title=f"📊 PRE-MARKET MOVERS | {target_date_str}",
            description=f"🎯 **Market Theme:** {market_theme}",
            color=discord.Color.gold(),
        )

        # Rank medals
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣"]

        lines = []
        for i, (ticker, data) in enumerate(ticker_data.items()):
            if i >= 7:  # Cap at 7 picks
                break

            gap = data["gap_pct"]
            rvol = data["rvol"]
            price = data["last_price"]
            medal = medals[i] if i < len(medals) else f"{i+1}."

            # Direction emoji from AI (fallback to gap direction)
            ai_info = catalyst_map.get(ticker, {})
            direction = ai_info.get("direction", "bullish" if gap >= 0 else "bearish")
            dir_emoji = "🟢" if direction == "bullish" else "🔴"
            catalyst = ai_info.get("catalyst", "No specific catalyst identified")

            # Fact-check icon: ✅ verified against news, ⚠️ unverified
            verified = verification_map.get(ticker, False)
            verify_icon = "✅" if verified else "⚠️"

            # Gap formatting with sign
            gap_str = f"+{gap:.2f}%" if gap >= 0 else f"{gap:.2f}%"

            lines.append(
                f"{medal} **{ticker}**  {dir_emoji} {gap_str}  |  RVOL {rvol}x  |  ${price:.2f}\n"
                f"↳ {verify_icon} {catalyst}"
            )

        embed.add_field(
            name="━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            value="\n\n".join(lines),
            inline=False,
        )

        embed.set_footer(
            text=f"✅ = Verified in news  ⚠️ = Unverified  •  📈 Gap% & RVOL from Yahoo Finance"
        )

        await msg.edit(content=None, embed=embed)

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"Error in movers: {error_trace}")
        await msg.edit(content=f"❌ **An internal error occurred:** {e}")

if __name__ == "__main__":
    if not DISCORD_TOKEN: print("❌ Error: DISCORD_BOT_TOKEN not found.")
    else: bot.run(DISCORD_TOKEN)
