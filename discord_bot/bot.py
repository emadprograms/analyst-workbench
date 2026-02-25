import os
import sys
import discord
from discord.ext import commands
import aiohttp
import asyncio
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# --- 1. Setup & Config ---
load_dotenv()

# Add project root to sys.path for module imports
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from modules.data.db_utils import get_all_tickers_from_db, get_company_card_and_notes, update_ticker_notes, get_daily_inputs
from modules.data.inspect_db import inspect as db_inspect_func

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_PAT")
GITHUB_REPO = os.getenv("GITHUB_REPO", "emadprograms/analyst-workbench") 
WORKFLOW_FILENAME = "manual_run.yml"
ACTIONS_URL = f"<https://github.com/{GITHUB_REPO}/actions>"

# --- 1.5 Ticker Configuration ---
STOCK_TICKERS = [
    "AAPL", "AMZN", "APP", "ABT", "PEP", "TSLA", "NVDA", "AMD",
    "SNOW", "NET", "PLTR", "MU", "ORCL", "TSM",
    "ADBE", "AVGO", "BABA", "GOOGL", "LRCX", "META", "MSFT", 
    "NDAQ", "PANW", "QCOM", "SHOP"
]
ETF_TICKERS = [
    "SPY", "QQQ", "IWM", "DIA", "TLT", "XLK", "XLF", "XLP", "XLE",
    "SMH", "XLI", "XLV", "UUP", "PAXGUSDT", "BTCUSDT",
    "XLC", "XLU", "EURUSDT", "CL=F", "^VIX"
]
ALL_TICKERS = sorted(STOCK_TICKERS + ETF_TICKERS)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Major Action System Online | Logged in as: {bot.user.name}")

# --- 2. Reusable UI Components ---

class CustomDateModal(discord.ui.Modal, title='Enter Custom Date'):
    def __init__(self, action_callback, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.action_callback = action_callback

    date_val = discord.ui.TextInput(
        label='Date (YYYY-MM-DD)',
        placeholder='2026-02-22',
        required=True,
        min_length=10,
        max_length=10
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            datetime.strptime(self.date_val.value, "%Y-%m-%d")
            await self.action_callback(interaction, self.date_val.value)
        except ValueError:
            await interaction.response.send_message("❌ Invalid date format. Use YYYY-MM-DD.", ephemeral=True)

class DateSelectionView(discord.ui.View):
    def __init__(self, action_callback):
        super().__init__(timeout=180)
        self.action_callback = action_callback
        
        options = []
        today = datetime.now(timezone.utc)
        for i in range(14):
            target = today - timedelta(days=i)
            date_str = target.strftime("%Y-%m-%d")
            if i == 0:
                label = "Today (0)"
            elif i == 1:
                label = "Yesterday (-1)"
            else:
                day_name = target.strftime("%A")
                label = f"{day_name} (-{i})"
            
            options.append(discord.SelectOption(label=label, description=date_str, value=date_str))
        
        self.add_item(DateDropdown(options, action_callback))

    @discord.ui.button(label="⌨️ Manual Date Entry", style=discord.ButtonStyle.secondary)
    async def manual_date(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CustomDateModal(self.action_callback))

class DateDropdown(discord.ui.Select):
    def __init__(self, options, action_callback):
        super().__init__(placeholder="📅 Select a date...", min_values=1, max_values=1, options=options)
        self.action_callback = action_callback

    async def callback(self, interaction: discord.Interaction):
        await self.action_callback(interaction, self.values[0])

class NewsModal(discord.ui.Modal, title='Market News Entry'):
    def __init__(self, target_date, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_date = target_date

    news_text = discord.ui.TextInput(
        label=f'Market News Content',
        style=discord.TextStyle.long,
        placeholder='Paste news headlines here...',
        required=True,
        min_length=10,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🛰️ Dispatching news for **{self.target_date}**... 🚀", ephemeral=False)
        msg = await interaction.original_response()
        
        inputs = {"target_date": self.target_date, "action": "input-news", "text": self.news_text.value}
        success, error = await dispatch_github_action(inputs)
        
        if success:
            await msg.edit(content=f"🛰️ Dispatching news for **{self.target_date}**...\n✅ **Dispatched!** (ETA: ~2-3 mins)\n🔗 [Monitor Progress]({ACTIONS_URL}) ⏱️")
        else:
            await msg.edit(content=f"🛰️ Dispatching news for **{self.target_date}**...\n❌ **Failed:** {error}")

# --- NEW: BuildCards UI Components ---

class BuildTypeSelectionView(discord.ui.View):
    def __init__(self, target_date):
        super().__init__(timeout=180)
        self.target_date = target_date

    @discord.ui.button(label="🌎 Economy Card", style=discord.ButtonStyle.primary, emoji="📈")
    async def economy_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=f"🧠 **Building Economy Card** ({self.target_date})... 🛰️", view=None)
        msg = await interaction.original_response()
        inputs = {"target_date": self.target_date, "action": "update-economy"}
        success, error = await dispatch_github_action(inputs)
        if success:
            await msg.edit(content=f"🧠 **Building Economy Card** ({self.target_date})...\n✅ **Dispatched!** (ETA: ~5-7 mins)\n🔗 [Monitor Progress]({ACTIONS_URL}) 📡⏱️")
        else:
            await msg.edit(content=f"🧠 **Building Economy Card** ({self.target_date})... ❌ **Failed:** {error}")

    @discord.ui.button(label="🏢 Company Cards", style=discord.ButtonStyle.success, emoji="📊")
    async def company_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # We only want to select STOCKS. ETFs are for economy cards only.
        view = TickerSelectionView(target_date=self.target_date, stock_tickers=STOCK_TICKERS)
        await interaction.response.edit_message(content=f"🏢 **Select Companies** for **{self.target_date}**:\n(Select multiple from the menus below)", view=view)

class TickerSelectionView(discord.ui.View):
    def __init__(self, target_date, stock_tickers):
        super().__init__(timeout=300)
        self.target_date = target_date
        self.stock_tickers = stock_tickers
        self.selected_tickers = set()
        self.dropdown_states = {} # Track state of each dropdown

        # Split tickers for Discord's 25-item limit
        display_stocks = sorted(stock_tickers)[:25]
        self.add_item(TickerDropdown(display_stocks, "🏢 Select Stocks...", self))
        # REMOVED: ETF Dropdown (ETFs are for economy cards only)

    @discord.ui.button(label="✅ Build Cards", style=discord.ButtonStyle.success, row=2)
    async def dispatch_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_tickers:
            await interaction.response.send_message("❌ Please select at least one ticker!", ephemeral=True)
            return
        
        tickers_str = ",".join(sorted(list(self.selected_tickers)))
        await interaction.response.edit_message(content=f"🚀 **Building Cards** for {len(self.selected_tickers)} tickers...\n`{tickers_str}`", view=None)
        msg = await interaction.original_response()
        
        inputs = {
            "target_date": self.target_date,
            "action": "update-company",
            "tickers": tickers_str
        }
        success, error = await dispatch_github_action(inputs)
        if success:
            await msg.edit(content=f"🚀 **Cards Dispatched!** ({len(self.selected_tickers)} tickers)\n✅ **Target Date:** {self.target_date}\n🔗 [Monitor Progress]({ACTIONS_URL}) 📡⏱️")
        else:
            await msg.edit(content=f"❌ **Build Failed:** {error}")

    @discord.ui.button(label="🌟 Select All", style=discord.ButtonStyle.secondary, row=2)
    async def select_all_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_tickers = set(self.stock_tickers)
        tickers_str = ",".join(sorted(list(self.selected_tickers)))
        await interaction.response.edit_message(content=f"🌟 **All {len(self.stock_tickers)} Stocks Selected!**\nReady to dispatch for **{self.target_date}**.", view=self)

    @discord.ui.button(label="🔄 Reset", style=discord.ButtonStyle.danger, row=2)
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_tickers = set()
        self.dropdown_states = {}
        # We need to manually reset the Select components' current values too
        # But for simplicity, we just clear the tracking set
        await interaction.response.edit_message(content=f"🏢 **Select Companies** for **{self.target_date}**:\n(Selection Reset)", view=self)

class TickerDropdown(discord.ui.Select):
    def __init__(self, tickers, placeholder, parent_view):
        options = [discord.SelectOption(label=t, value=t) for t in tickers]
        # max_values must be at most 25 (Discord limit)
        m_val = min(len(tickers), 25)
        super().__init__(placeholder=placeholder, min_values=0, max_values=m_val, options=options)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        # Update the parent view's tracking for THIS dropdown
        # Note: We'll identify dropdowns by their placeholder or a custom ID
        self.parent_view.dropdown_states[self.placeholder] = set(self.values)
        
        # Aggregate all selected tickers
        all_selected = set()
        for state in self.parent_view.dropdown_states.values():
            all_selected.update(state)
        
        self.parent_view.selected_tickers = all_selected
        
        count = len(self.parent_view.selected_tickers)
        await interaction.response.edit_message(content=f"🏢 **{count} Tickers Selected** for **{self.parent_view.target_date}**.\nAdd more or click dispatch below.", view=self.parent_view)

class ViewTypeSelectionView(discord.ui.View):
    def __init__(self, target_date):
        super().__init__(timeout=180)
        self.target_date = target_date

    @discord.ui.button(label="🌎 Economy Card", style=discord.ButtonStyle.primary, emoji="📈")
    async def economy_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=f"🔎 **Retrieving Economy Card** ({self.target_date})... 🛰️", view=None)
        msg = await interaction.original_response()
        inputs = {"target_date": self.target_date, "action": "view-economy"}
        success, error = await dispatch_github_action(inputs)
        if success:
            await msg.edit(content=f"🔎 **Retrieving Economy Card** ({self.target_date})...\n✅ **Dispatched!** (ETA: ~1 min)\n🔗 [Monitor Progress]({ACTIONS_URL}) 📡⏱️")
        else:
            await msg.edit(content=f"🔎 **Retrieving Economy Card** ({self.target_date})... ❌ **Failed:** {error}")

    @discord.ui.button(label="🏢 Company Cards", style=discord.ButtonStyle.success, emoji="📊")
    async def company_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ViewTickerSelectionView(target_date=self.target_date, stock_tickers=STOCK_TICKERS)
        await interaction.response.edit_message(content=f"🏢 **Select Companies to View** for **{self.target_date}**:\n(Select multiple from the menus below)", view=view)

class ViewTickerSelectionView(discord.ui.View):
    def __init__(self, target_date, stock_tickers):
        super().__init__(timeout=300)
        self.target_date = target_date
        self.stock_tickers = stock_tickers
        self.selected_tickers = set()
        self.dropdown_states = {}

        display_stocks = sorted(stock_tickers)[:25]
        self.add_item(TickerDropdown(display_stocks, "🏢 Select Stocks...", self))

    @discord.ui.button(label="✅ View Cards", style=discord.ButtonStyle.success, row=2)
    async def dispatch_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_tickers:
            await interaction.response.send_message("❌ Please select at least one ticker!", ephemeral=True)
            return
        
        tickers_str = ",".join(sorted(list(self.selected_tickers)))
        await interaction.response.edit_message(content=f"🚀 **Retrieving Cards** for {len(self.selected_tickers)} tickers...\n`{tickers_str}`", view=None)
        msg = await interaction.original_response()
        
        inputs = {
            "target_date": self.target_date,
            "action": "view-company",
            "tickers": tickers_str
        }
        success, error = await dispatch_github_action(inputs)
        if success:
            await msg.edit(content=f"🚀 **Retrieval Dispatched!** ({len(self.selected_tickers)} tickers)\n✅ **Target Date:** {self.target_date}\n🔗 [Monitor Progress]({ACTIONS_URL}) 📡⏱️")
        else:
            await msg.edit(content=f"❌ **Retrieval Failed:** {error}")

    @discord.ui.button(label="🌟 Select All", style=discord.ButtonStyle.secondary, row=2)
    async def select_all_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_tickers = set(self.stock_tickers)
        tickers_str = ",".join(sorted(list(self.selected_tickers)))
        await interaction.response.edit_message(content=f"🌟 **All {len(self.stock_tickers)} Stocks Selected!**\nReady to retrieve for **{self.target_date}**.", view=self)

    @discord.ui.button(label="🔄 Reset", style=discord.ButtonStyle.danger, row=2)
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_tickers = set()
        self.dropdown_states = {}
        await interaction.response.edit_message(content=f"🏢 **Select Companies to View** for **{self.target_date}**:\n(Selection Reset)", view=self)

# --- NEW: EditNotes UI Components ---

class EditNotesModal(discord.ui.Modal):
    def __init__(self, ticker, current_notes):
        super().__init__(title=f"Edit Notes: {ticker}")
        self.ticker = ticker
        self.notes_input = discord.ui.TextInput(
            label="Historical Level Notes",
            style=discord.TextStyle.paragraph,
            placeholder="Enter major multi-year levels, structural patterns, etc...",
            default=current_notes,
            required=True,
            max_length=4000
        )
        self.add_item(self.notes_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        # Run DB update in executor
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, update_ticker_notes, self.ticker, self.notes_input.value)
        
        if success:
            await interaction.followup.send(f"✅ **{self.ticker}** notes updated successfully!", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Failed to update notes for **{self.ticker}**. Check bot logs.", ephemeral=True)

class EditNotesTickerSelectionView(discord.ui.View):
    def __init__(self, stock_tickers):
        super().__init__(timeout=180)
        self.stock_tickers = stock_tickers
        
        # Simple dropdown for single selection
        options = [discord.SelectOption(label=t, value=t) for t in sorted(stock_tickers)[:25]]
        self.add_item(EditNotesTickerDropdown(options))

class EditNotesTickerDropdown(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="Select company to edit notes...", options=options)

    async def callback(self, interaction: discord.Interaction):
        ticker = self.values[0]
        await interaction.response.edit_message(content=f"🔍 **Fetching current notes for {ticker}...**", view=None)
        
        # Fetch current notes
        loop = asyncio.get_event_loop()
        _, current_notes, _ = await loop.run_in_executor(None, get_company_card_and_notes, ticker)
        
        modal = EditNotesModal(ticker=ticker, current_notes=current_notes or "")
        await interaction.followup.send(f"📝 Opening editor for **{ticker}**...", ephemeral=True)
        await interaction.followup.send_modal(modal)

# --- 3. Internal Logic Helpers ---

def get_target_date(date_input: str = None) -> str | None:
    """
    Parses date input. Supports:
    - None -> Returns None (Forces picker in commands)
    - "0" -> Today (UTC)
    - "-1", "-2", etc. -> Days relative to today
    - "YYYY-MM-DD" -> Specific date
    """
    today = datetime.now(timezone.utc)
    if not date_input:
        return None
    
    if date_input == "0":
        return today.strftime("%Y-%m-%d")
    
    # Handle relative dates (MUST start with - and be followed by digits)
    if date_input.startswith("-") and date_input[1:].isdigit():
        try:
            days_back = int(date_input[1:])
            target = today - timedelta(days=days_back)
            return target.strftime("%Y-%m-%d")
        except: pass

    return date_input # Return as-is for validation later

async def dispatch_github_action(inputs: dict):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False, "Missing GITHUB_PAT or GITHUB_REPO configuration."

    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILENAME}/dispatches"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {"ref": "main", "inputs": inputs}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as resp:
            return (True, "Success") if resp.status == 204 else (False, f"GitHub Error {resp.status}")

# --- 4. Commands ---

@bot.command()
async def editnotes(ctx, ticker: str = None):
    """Opens a dialog to edit historical notes for a company."""
    print(f"[DEBUG] Command !editnotes called by {ctx.author}")
    
    if ticker:
        ticker = ticker.upper()
        await ctx.send(f"🔍 **Fetching current notes for {ticker}...**")
        
        loop = asyncio.get_event_loop()
        _, current_notes, _ = await loop.run_in_executor(None, get_company_card_and_notes, ticker)
        
        modal = EditNotesModal(ticker=ticker, current_notes=current_notes or "")
        await ctx.send(f"📝 Click the button below to edit notes for **{ticker}**:", view=EditNotesTriggerView(modal))
    else:
        # Fetch active tickers from DB
        loop = asyncio.get_event_loop()
        db_tickers = await loop.run_in_executor(None, get_all_tickers_from_db)
        stock_list = [t for t in db_tickers if t not in ETF_TICKERS]
        
        if not stock_list:
            stock_list = STOCK_TICKERS # Fallback
            
        view = EditNotesTickerSelectionView(stock_tickers=stock_list)
        await ctx.send("🏢 **Select a company to edit historical notes:**", view=view)

class EditNotesTriggerView(discord.ui.View):
    def __init__(self, modal):
        super().__init__(timeout=60)
        self.modal = modal
    
    @discord.ui.button(label="📝 Open Editor", style=discord.ButtonStyle.primary)
    async def open_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(self.modal)

@bot.command()
async def buildcards(ctx, date_indicator: str = None):
    """Interactive command to build Economy or Company cards."""
    print(f"[DEBUG] Command !buildcards called by {ctx.author}")
    
    target_date = get_target_date(date_indicator)

    async def build_callback(interaction, selected_date):
        view = BuildTypeSelectionView(target_date=selected_date)
        await interaction.response.edit_message(content=f"🏗️ **Building Cards for {selected_date}**\nWhich kind of card would you like to build?", view=view)

    if not target_date:
        view = DateSelectionView(action_callback=build_callback)
        await ctx.send("🗓️ **Select Date for Card Generation:**", view=view)
    else:
        try:
            datetime.strptime(target_date, "%Y-%m-%d")
            view = BuildTypeSelectionView(target_date=target_date)
            await ctx.send(f"🏗️ **Building Cards for {target_date}**\nWhich kind of card would you like to build?", view=view)
        except ValueError:
            await ctx.send(f"❌ Error: `{target_date}` is invalid.")

@bot.command()
async def viewcards(ctx, date_indicator: str = None):
    """Interactive command to view Economy or Company cards."""
    print(f"[DEBUG] Command !viewcards called by {ctx.author}")
    
    target_date = get_target_date(date_indicator)

    async def view_callback(interaction, selected_date):
        view = ViewTypeSelectionView(target_date=selected_date)
        await interaction.response.edit_message(content=f"🔎 **Viewing Cards for {selected_date}**\nWhich kind of card would you like to view?", view=view)

    if not target_date:
        view = DateSelectionView(action_callback=view_callback)
        await ctx.send("🗓️ **Select Date for Card Viewing:**", view=view)
    else:
        try:
            datetime.strptime(target_date, "%Y-%m-%d")
            view = ViewTypeSelectionView(target_date=target_date)
            await ctx.send(f"🔎 **Viewing Cards for {target_date}**\nWhich kind of card would you like to view?", view=view)
        except ValueError:
            await ctx.send(f"❌ Error: `{target_date}` is invalid.")

@bot.command()
async def inputnews(ctx, date_indicator: str = None):
    """Opens a date picker, then a text box OR handles an attached .txt file."""
    print(f"[DEBUG] Command !inputnews called by {ctx.author}")
    
    target_date = get_target_date(date_indicator)

    # Check for attachments first
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
        if attachment.filename.endswith(('.txt', '.log')):
            
            # --- VISION FIX: If no date, show the picker instead of defaulting to today ---
            if not target_date:
                async def attachment_date_callback(interaction, selected_date):
                    await interaction.response.edit_message(content=f"🛰️ **File Detected:** `{attachment.filename}`\nDispatching content for **{selected_date}**... 🚀", view=None)
                    msg = await interaction.original_response()
                    
                    inputs = {
                        "target_date": selected_date,
                        "action": "input-news",
                        "news_url": attachment.url
                    }
                    success, error = await dispatch_github_action(inputs)
                    if success:
                        await msg.edit(content=f"🛰️ **File Detected:** `{attachment.filename}`\n✅ **Dispatch Successful for {selected_date}!**\n🔗 [Monitor Progress]({ACTIONS_URL}) ⏱️")
                    else:
                        await msg.edit(content=f"❌ **File Dispatch Failed:** {error}")

                view = DateSelectionView(action_callback=attachment_date_callback)
                await ctx.send(f"📁 **File detected:** `{attachment.filename}`\n🗓️ Please select the target date for this news file:", view=view)
                return

            # If date was provided (e.g. !inputnews 0 or !inputnews 2026-02-23)
            await ctx.send(f"🛰️ **File Detected:** `{attachment.filename}`\nDispatching content for **{target_date}** to GitHub... 🚀")
            
            inputs = {
                "target_date": target_date,
                "action": "input-news",
                "news_url": attachment.url
            }
            
            success, error = await dispatch_github_action(inputs)
            if success:
                await ctx.send(f"✅ **File Dispatch Successful!** (ETA: ~2-3 mins)\n🔗 [Monitor Progress]({ACTIONS_URL}) ⏱️")
            else:
                await ctx.send(f"❌ **File Dispatch Failed:** {error}")
            return
        else:
            await ctx.send("⚠️ Please upload a `.txt` file for market news.")
            return

    async def news_callback(interaction, selected_date):
        await interaction.response.send_modal(NewsModal(target_date=selected_date))
        try:
            # Edit the original selection message to remove the picker
            await interaction.message.edit(content=f"🗓️ **News Entry Selected:** {selected_date}\n(Modal opened - check your pop-up box)", view=None)
        except: pass

    if not target_date:
        view = DateSelectionView(action_callback=news_callback)
        await ctx.send("🗓️ **Select Date for News Entry:**", view=view)
    else:
        try:
            datetime.strptime(target_date, "%Y-%m-%d")
            class TriggerView(discord.ui.View):
                def __init__(self, date):
                    super().__init__()
                    self.date = date
                @discord.ui.button(label=f"📝 Open Box for {target_date}", style=discord.ButtonStyle.primary)
                async def go(self, interaction, button):
                    await interaction.response.send_modal(NewsModal(target_date=self.date))
                    try:
                        await interaction.message.edit(content=f"✅ **Target Date:** {self.date}\n(Modal opened)", view=None)
                    except: pass
            await ctx.send(f"✅ Target Date: **{target_date}**", view=TriggerView(target_date))
        except ValueError:
            await ctx.send(f"❌ Error: `{target_date}` is invalid.")

@bot.command()
async def inspect(ctx, date_str: str = None):
    """Performs a deep database inspection for a specific date directly in the bot."""
    print(f"[DEBUG] Command !inspect called by {ctx.author}")
    
    target_date_str = get_target_date(date_str)

    async def inspect_callback(interaction, selected_date_str):
        await interaction.response.edit_message(content=f"🔍 **Inspecting Database** for **{selected_date_str}**... 🛰️", view=None)
        
        # We need a custom logger-like object to capture inspect output
        class CapturingLogger:
            def __init__(self): self.lines = []
            def log(self, msg): self.lines.append(msg)
        
        cap_logger = CapturingLogger()
        target_date_obj = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, db_inspect_func, target_date_obj, cap_logger)
        
        output = "\n".join(cap_logger.lines)
        # Use code block for formatting
        await interaction.followup.send(f"```\n{output}\n```")

    if not target_date_str:
        view = DateSelectionView(action_callback=inspect_callback)
        await ctx.send("🔍 **Select Date to Inspect Database:**", view=view)
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
            
            output = "\n".join(cap_logger.lines)
            await msg.edit(content=f"✅ **Inspection Complete for {target_date_str}:**\n```\n{output}\n```")
        except ValueError:
            await ctx.send(f"❌ Error: `{target_date_str}` is invalid.")

@bot.command()
async def checknews(ctx, date_str: str = None):
    """Verifies market news ingestion for a specific date directly in the bot."""
    print(f"[DEBUG] Command !checknews called by {ctx.author}")
    
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
        else:
            await interaction.followup.send(f"❌ **NO NEWS FOUND** for **{selected_date_str}**.")

    if not target_date_str:
        view = DateSelectionView(action_callback=check_callback)
        await ctx.send("🔍 **Select Date to Check News:**", view=view)
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
            else:
                await msg.edit(content=f"❌ **NO NEWS FOUND** for **{target_date_str}**.")
        except ValueError:
            await ctx.send(f"❌ Error: `{target_date_str}` is invalid.")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ Error: DISCORD_BOT_TOKEN not found.")
    else:
        bot.run(DISCORD_TOKEN)
