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
    ViewTypeSelectionView, EditNotesTickerSelectionView, EditNotesModal, EditNotesTriggerView
)
from modules.data.db_utils import (
    get_all_tickers_from_db, get_company_card_and_notes, update_ticker_notes, 
    get_daily_inputs, get_archived_economy_card, get_archived_company_card, get_ticker_stats
)
from modules.data.inspect_db import inspect as db_inspect_func

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Major Action System Online | Logged in as: {bot.user.name}")

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

async def dispatch_github_action(inputs: dict):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False, "Missing GITHUB_PAT or GITHUB_REPO configuration."
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILENAME}/dispatches"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    data = {"ref": "main", "inputs": inputs}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as resp:
            return (True, "Success") if resp.status == 204 else (False, f"GitHub Error {resp.status}")

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
    """Opens a date picker, then a text box OR handles an attached .txt file."""
    target_date = get_target_date(date_indicator)
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
        if attachment.filename.endswith(('.txt', '.log')):
            if not target_date:
                async def file_date_cb(interaction, sel_date):
                    await interaction.response.edit_message(content=f"🛰️ **File Detected:** `{attachment.filename}`\nDispatching for **{sel_date}**... 🚀", view=None)
                    success, error = await dispatch_github_action({"target_date": sel_date, "action": "input-news", "news_url": attachment.url})
                    msg = await interaction.original_response()
                    if success: await msg.edit(content=f"✅ **File Dispatched for {sel_date}!**\n🔗 [Monitor Progress]({ACTIONS_URL})")
                    else: await msg.edit(content=f"❌ **File Dispatch Failed:** {error}")
                await ctx.send(f"📁 **File detected:** `{attachment.filename}`\n🗓️ Select target date:", view=DateSelectionView(file_date_cb))
                return
            await ctx.send(f"🛰️ **File Detected:** `{attachment.filename}`\nDispatching for **{target_date}**... 🚀")
            success, error = await dispatch_github_action({"target_date": target_date, "action": "input-news", "news_url": attachment.url})
            if success: await ctx.send(f"✅ **File Dispatch Successful!**\n🔗 [Monitor Progress]({ACTIONS_URL})")
            else: await ctx.send(f"❌ **File Dispatch Failed:** {error}")
            return
    async def news_callback(interaction, sel_date):
        await interaction.response.send_modal(NewsModal(sel_date, dispatch_github_action, ACTIONS_URL))
        try: await interaction.message.edit(content=f"🗓️ **News Entry Selected:** {sel_date}\n(Modal opened)", view=None)
        except: pass
    if not target_date:
        await ctx.send("🗓️ **Select Date for News Entry:**", view=DateSelectionView(news_callback))
    else:
        try:
            datetime.strptime(target_date, "%Y-%m-%d")
            class Trigger(discord.ui.View):
                def __init__(self, d): super().__init__(); self.d = d
                @discord.ui.button(label=f"📝 Open Box for {target_date}", style=discord.ButtonStyle.primary)
                async def go(self, interaction, button):
                    await interaction.response.send_modal(NewsModal(self.d, dispatch_github_action, ACTIONS_URL))
                    try: await interaction.message.edit(content=f"✅ **Target Date:** {self.d}\n(Modal opened)", view=None)
                    except: pass
            await ctx.send(f"✅ Target Date: **{target_date}**", view=Trigger(target_date))
        except ValueError: await ctx.send(f"❌ Error: `{target_date}` is invalid.")

if __name__ == "__main__":
    if not DISCORD_TOKEN: print("❌ Error: DISCORD_BOT_TOKEN not found.")
    else: bot.run(DISCORD_TOKEN)
