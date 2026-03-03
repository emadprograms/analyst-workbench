import os
import sys
import discord
from discord.ext import commands
import aiohttp
import asyncio
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
    upsert_daily_inputs
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
            from modules.ai.ai_services import filter_daily_news_for_macro, filter_daily_news_for_company, summarize_news_with_gemini
            from modules.core.logger import AppLogger
            logger = AppLogger()
            
            if selected_target == "MACRO":
                filtered_news = filter_daily_news_for_macro(market_news)
            else:
                filtered_news = filter_daily_news_for_company(market_news, selected_target, "")
                
            if "No specific company or sector news found" in filtered_news or "No macro news found" in filtered_news or not filtered_news.strip():
                await interaction.followup.send(f"⚠️ **No {selected_target} news found** in the database for **{selected_date}**.")
                return
                
            # 3. Summarize with Gemini
            summary = await loop.run_in_executor(None, summarize_news_with_gemini, filtered_news, selected_target, logger)
            
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
                
            from modules.ai.ai_services import filter_daily_news_for_macro, filter_daily_news_for_company, summarize_news_with_gemini
            from modules.core.logger import AppLogger
            logger = AppLogger()
            
            if target == "MACRO":
                filtered_news = filter_daily_news_for_macro(market_news)
            else:
                filtered_news = filter_daily_news_for_company(market_news, target, "")
                
            if "No specific company or sector news found" in filtered_news or "No macro news found" in filtered_news or not filtered_news.strip():
                await msg.edit(content=f"⚠️ **No {target} news found** in the database for **{date_str}**.")
                return
                
            summary = await loop.run_in_executor(None, summarize_news_with_gemini, filtered_news, target, logger)
            
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

if __name__ == "__main__":
    if not DISCORD_TOKEN: print("❌ Error: DISCORD_BOT_TOKEN not found.")
    else: bot.run(DISCORD_TOKEN)
