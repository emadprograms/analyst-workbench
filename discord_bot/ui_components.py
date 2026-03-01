import discord
import asyncio
import json
import io
from datetime import datetime, timedelta, timezone

# --- Reusable UI Components ---

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
    def __init__(self, target_date, save_callback):
        super().__init__()
        self.target_date = target_date
        self.save_callback = save_callback

    news_text = discord.ui.TextInput(
        label=f'Market News Content',
        style=discord.TextStyle.long,
        placeholder='Paste news headlines here...',
        required=True,
        min_length=10,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        msg = await interaction.original_response()
        await msg.edit(content=f"💾 **Saving news** for **{self.target_date}** to database... 🛰️")
        
        success = await self.save_callback(self.target_date, self.news_text.value)
        if success:
            await msg.edit(content=f"✅ **Market news successfully saved** for **{self.target_date}**! 🚀")
        else:
            await msg.edit(content=f"❌ **Failed to save news** for **{self.target_date}** to database.")

# --- Build Cards UI ---

class BuildTypeSelectionView(discord.ui.View):
    def __init__(self, target_date, dispatch_callback, actions_url, stock_tickers, ticker_view_class):
        super().__init__(timeout=180)
        self.target_date = target_date
        self.dispatch_callback = dispatch_callback
        self.actions_url = actions_url
        self.stock_tickers = stock_tickers
        self.ticker_view_class = ticker_view_class

    @discord.ui.button(label="🌎 Economy Card", style=discord.ButtonStyle.primary, emoji="📈")
    async def economy_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=f"🧠 **Building Economy Card** ({self.target_date})... 🛰️", view=None)
        msg = await interaction.original_response()
        inputs = {"target_date": self.target_date, "action": "update-economy"}
        success, message, run_url = await self.dispatch_callback(inputs)
        monitor_link = run_url or self.actions_url
        if success:
            await msg.edit(content=f"🧠 **Building Economy Card** ({self.target_date})...\n✅ **Dispatched!** (ETA: ~5-7 mins)\n🔗 [Monitor Progress](<{monitor_link}>) 📡⏱️")
        else:
            await msg.edit(content=f"🧠 **Building Economy Card** ({self.target_date})... ❌ **Failed:** {message}")

    @discord.ui.button(label="🏢 Company Cards", style=discord.ButtonStyle.success, emoji="📊")
    async def company_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = self.ticker_view_class(
            target_date=self.target_date, 
            stock_tickers=self.stock_tickers,
            dispatch_callback=self.dispatch_callback,
            actions_url=self.actions_url
        )
        await interaction.response.edit_message(content=f"🏢 **Select Companies** for **{self.target_date}**:\n(Select multiple from the menus below)", view=view)

class TickerSelectionView(discord.ui.View):
    def __init__(self, target_date, stock_tickers, dispatch_callback, actions_url):
        super().__init__(timeout=300)
        self.target_date = target_date
        self.stock_tickers = stock_tickers
        self.dispatch_callback = dispatch_callback
        self.actions_url = actions_url
        self.selected_tickers = set()
        self.dropdown_states = {}

        sorted_stocks = sorted(stock_tickers)
        for i in range(0, len(sorted_stocks), 25):
            chunk = sorted_stocks[i:i+25]
            placeholder = f"🏢 Select Stocks ({i+1}-{i+len(chunk)})..." if len(sorted_stocks) > 25 else "🏢 Select Stocks..."
            self.add_item(TickerDropdown(chunk, placeholder, self))

    @discord.ui.button(label="✅ Build Cards", style=discord.ButtonStyle.success, row=4)
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
        success, message, run_url = await self.dispatch_callback(inputs)
        monitor_link = run_url or self.actions_url
        if success:
            await msg.edit(content=f"🚀 **Cards Dispatched!** ({len(self.selected_tickers)} tickers)\n✅ **Target Date:** {self.target_date}\n🔗 [Monitor Progress](<{monitor_link}>) 📡⏱️")
        else:
            await msg.edit(content=f"❌ **Build Failed:** {message}")

    @discord.ui.button(label="🌟 Select All", style=discord.ButtonStyle.secondary, row=4)
    async def select_all_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_tickers = set(self.stock_tickers)
        for child in self.children:
            if isinstance(child, discord.ui.Select):
                child.disabled = True
        await interaction.response.edit_message(content=f"🌟 **All {len(self.stock_tickers)} Stocks Selected!**\nReady to dispatch for **{self.target_date}**.", view=self)

    @discord.ui.button(label="🔄 Reset", style=discord.ButtonStyle.danger, row=4)
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_tickers = set()
        self.dropdown_states = {}
        for child in self.children:
            if isinstance(child, discord.ui.Select):
                child.disabled = False
        await interaction.response.edit_message(content=f"🏢 **Select Companies** for **{self.target_date}**:\n(Selection Reset)", view=self)

class TickerDropdown(discord.ui.Select):
    def __init__(self, tickers, placeholder, parent_view):
        options = [discord.SelectOption(label=t, value=t) for t in tickers]
        m_val = min(len(tickers), 25)
        super().__init__(placeholder=placeholder, min_values=0, max_values=m_val, options=options)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.dropdown_states[self.placeholder] = set(self.values)
        all_selected = set()
        for state in self.parent_view.dropdown_states.values():
            all_selected.update(state)
        self.parent_view.selected_tickers = all_selected
        count = len(self.parent_view.selected_tickers)
        await interaction.response.edit_message(content=f"🏢 **{count} Tickers Selected** for **{self.parent_view.target_date}**.\nAdd more or click dispatch below.", view=self.parent_view)

# --- View Cards UI ---

class ViewTypeSelectionView(discord.ui.View):
    def __init__(self, target_date, eco_fetch_callback, company_fetch_callback, stock_tickers):
        super().__init__(timeout=180)
        self.target_date = target_date
        self.eco_fetch_callback = eco_fetch_callback
        self.company_fetch_callback = company_fetch_callback
        self.stock_tickers = stock_tickers

    @discord.ui.button(label="🌎 Economy Card", style=discord.ButtonStyle.primary, emoji="📈")
    async def economy_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=f"🔎 **Retrieving Economy Card** ({self.target_date})... 🛰️", view=None)
        
        card_json = await self.eco_fetch_callback(self.target_date)
        
        if card_json:
            try:
                formatted = json.dumps(json.loads(card_json), indent=2)
                file_data = io.BytesIO(formatted.encode("utf-8"))
                file = discord.File(file_data, filename=f"Economy_Card_{self.target_date}.json")
                await interaction.followup.send(f"✅ **Economy Card for {self.target_date}**:", file=file)
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to process card: {e}")
        else:
            await interaction.followup.send(f"❌ No Economy Card found for **{self.target_date}**.")

    @discord.ui.button(label="🏢 Company Cards", style=discord.ButtonStyle.success, emoji="📊")
    async def company_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ViewTickerSelectionView(
            target_date=self.target_date, 
            stock_tickers=self.stock_tickers,
            fetch_callback=self.company_fetch_callback
        )
        await interaction.response.edit_message(content=f"🏢 **Select Companies to View** for **{self.target_date}**:\n(Select multiple from the menus below)", view=view)

class ViewTickerSelectionView(discord.ui.View):
    def __init__(self, target_date, stock_tickers, fetch_callback):
        super().__init__(timeout=300)
        self.target_date = target_date
        self.stock_tickers = stock_tickers
        self.fetch_callback = fetch_callback
        self.selected_tickers = set()
        self.dropdown_states = {}

        sorted_stocks = sorted(stock_tickers)
        for i in range(0, len(sorted_stocks), 25):
            chunk = sorted_stocks[i:i+25]
            placeholder = f"🏢 Select Stocks ({i+1}-{i+len(chunk)})..." if len(sorted_stocks) > 25 else "🏢 Select Stocks..."
            self.add_item(TickerDropdown(chunk, placeholder, self))

    @discord.ui.button(label="✅ View Cards", style=discord.ButtonStyle.success, row=4)
    async def dispatch_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_tickers:
            await interaction.response.send_message("❌ Please select at least one ticker!", ephemeral=True)
            return
        
        count = len(self.selected_tickers)
        await interaction.response.edit_message(content=f"🚀 **Retrieving {count} Cards** for **{self.target_date}**...", view=None)
        
        files = []
        not_found = []
        
        for ticker in sorted(list(self.selected_tickers)):
            card_json = await self.fetch_callback(self.target_date, ticker)
            if card_json:
                try:
                    formatted = json.dumps(json.loads(card_json), indent=2)
                    file_data = io.BytesIO(formatted.encode("utf-8"))
                    files.append(discord.File(file_data, filename=f"{ticker}_Card_{self.target_date}.json"))
                except:
                    not_found.append(ticker)
            else:
                not_found.append(ticker)
        
        if files:
            chunks = [files[i:i + 10] for i in range(0, len(files), 10)]
            for i, chunk in enumerate(chunks):
                msg = f"✅ **Company Cards ({self.target_date})** - Part {i+1}:" if len(chunks) > 1 else f"✅ **Company Cards ({self.target_date})**:"
                await interaction.followup.send(msg, files=chunk)
        
        if not_found:
            await interaction.followup.send(f"❌ Cards not found for: `{', '.join(not_found)}`")

    @discord.ui.button(label="🌟 Select All", style=discord.ButtonStyle.secondary, row=4)
    async def select_all_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_tickers = set(self.stock_tickers)
        for child in self.children:
            if isinstance(child, discord.ui.Select):
                child.disabled = True
        await interaction.response.edit_message(content=f"🌟 **All {len(self.stock_tickers)} Stocks Selected!**\nReady to retrieve for **{self.target_date}**.", view=self)

    @discord.ui.button(label="🔄 Reset", style=discord.ButtonStyle.danger, row=4)
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_tickers = set()
        self.dropdown_states = {}
        for child in self.children:
            if isinstance(child, discord.ui.Select):
                child.disabled = False
        await interaction.response.edit_message(content=f"🏢 **Select Companies to View** for **{self.target_date}**:\n(Selection Reset)", view=self)

# --- Edit Notes UI ---

class EditNotesModal(discord.ui.Modal):
    def __init__(self, ticker, current_notes, update_callback):
        super().__init__(title=f"Edit Notes: {ticker}")
        self.ticker = ticker
        self.update_callback = update_callback
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
        success = await self.update_callback(self.ticker, self.notes_input.value)
        if success:
            await interaction.followup.send(f"✅ **{self.ticker}** notes updated successfully!", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Failed to update notes for **{self.ticker}**.", ephemeral=True)

class EditNotesTickerSelectionView(discord.ui.View):
    def __init__(self, stock_tickers, fetch_callback, update_callback):
        super().__init__(timeout=180)
        self.stock_tickers = stock_tickers
        sorted_stocks = sorted(stock_tickers)
        for i in range(0, len(sorted_stocks), 25):
            chunk = sorted_stocks[i:i+25]
            options = [discord.SelectOption(label=t, value=t) for t in chunk]
            placeholder = f"Select company ({i+1}-{i+len(chunk)})..." if len(sorted_stocks) > 25 else "Select company to edit notes..."
            self.add_item(EditNotesTickerDropdown(options, placeholder, fetch_callback, update_callback))

class EditNotesTickerDropdown(discord.ui.Select):
    def __init__(self, options, placeholder, fetch_callback, update_callback):
        super().__init__(placeholder=placeholder, options=options)
        self.fetch_callback = fetch_callback
        self.update_callback = update_callback

    async def callback(self, interaction: discord.Interaction):
        ticker = self.values[0]
        await interaction.response.edit_message(content=f"🔍 **Fetching current notes for {ticker}...**", view=None)
        current_notes = await self.fetch_callback(ticker)
        modal = EditNotesModal(ticker=ticker, current_notes=current_notes or "", update_callback=self.update_callback)
        await interaction.followup.send(f"📝 Opening editor for **{ticker}**...", ephemeral=True)
        await interaction.followup.send_modal(modal)

class EditNotesTriggerView(discord.ui.View):
    def __init__(self, modal):
        super().__init__(timeout=60)
        self.modal = modal
    
    @discord.ui.button(label="📝 Open Editor", style=discord.ButtonStyle.primary)
    async def open_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(self.modal)
