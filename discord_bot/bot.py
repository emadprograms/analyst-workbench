import os
import discord
from discord.ext import commands
import aiohttp
import asyncio
from datetime import datetime
from dotenv import load_dotenv

# Load local environment variables
load_dotenv()

# Configuration
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_PAT")
GITHUB_REPO = os.getenv("GITHUB_REPO", "emadprograms/analyst-workbench") 
WORKFLOW_FILENAME = "manual_run.yml"

# Setup Intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Major Action logged in as {bot.user.name}")

async def dispatch_github_action(inputs: dict):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False, "Missing GITHUB_PAT or GITHUB_REPO configuration."

    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILENAME}/dispatches"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "ref": "main",
        "inputs": inputs
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as resp:
            if resp.status == 204:
                return True, "Success"
            else:
                try:
                    err_json = await resp.json()
                    err_msg = err_json.get("message", "Unknown error")
                except:
                    err_msg = await resp.text()
                return False, f"GitHub Error ({resp.status}): {err_msg}"

from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load local environment variables
load_dotenv()

# Configuration
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_PAT")
GITHUB_REPO = os.getenv("GITHUB_REPO", "emadprograms/analyst-workbench") 
WORKFLOW_FILENAME = "manual_run.yml"

# Setup Intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- MODAL FOR NEWS INPUT ---
class NewsModal(discord.ui.Modal, title='Market News Entry'):
    def __init__(self, target_date, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_date = target_date

    news_text = discord.ui.TextInput(
        label=f'Enter News for Today', # Updated dynamically in __init__ if I could, but label is static here
        style=discord.TextStyle.long,
        placeholder='Paste your news headlines or summary here...',
        required=True,
        min_length=10,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🛰️ Dispatching news entry for **{self.target_date}** to GitHub Actions...", ephemeral=True)
        
        inputs = {
            "target_date": self.target_date,
            "action": "input-news",
            "text": self.news_text.value
        }
        
        success, error = await dispatch_github_action(inputs)
        if success:
            await interaction.followup.send(content=f"✅ **Dispatch Successful!**\n> News entry for **{self.target_date}** is being processed on GitHub. (ETA: **~2-3 minutes**) ⏱️")
        else:
            await interaction.followup.send(content=f"❌ **Dispatch Failed:** {error}")

class NewsButtonView(discord.ui.View):
    def __init__(self, target_date):
        super().__init__(timeout=300) # 5 minute timeout
        self.target_date = target_date

    @discord.ui.button(label="📝 Open News Entry Box", style=discord.ButtonStyle.primary)
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = NewsModal(target_date=self.target_date)
        modal.title = f"News Entry: {self.target_date}"
        await interaction.response.send_modal(modal)

@bot.event
async def on_ready():
    print(f"✅ Major Action logged in as {bot.user.name}")

# --- REUSABLE DATE PICKER COMPONENTS ---

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
        # Validate date
        try:
            datetime.strptime(self.date_val.value, "%Y-%m-%d")
            await self.action_callback(interaction, self.date_val.value)
        except ValueError:
            await interaction.response.send_message("❌ Invalid date format. Use YYYY-MM-DD.", ephemeral=True)

class DateSelectionView(discord.ui.View):
    def __init__(self, action_callback):
        super().__init__(timeout=180)
        self.action_callback = action_callback
        
        # Create dropdown options for the last 14 days
        options = []
        today = datetime.utcnow()
        for i in range(14):
            target = today - timedelta(days=i)
            date_str = target.strftime("%Y-%m-%d")
            # Dynamic labels showing the shortcut for every date
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

# --- UPDATED MODALS AND VIEWS ---

class NewsModal(discord.ui.Modal, title='Market News Entry'):
    def __init__(self, target_date, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_date = target_date

    news_text = discord.ui.TextInput(
        label=f'Market News',
        style=discord.TextStyle.long,
        placeholder='Paste news headlines here...',
        required=True,
        min_length=10,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🛰️ Dispatching news for **{self.target_date}** to GitHub...", ephemeral=True)
        inputs = {"target_date": self.target_date, "action": "input-news", "text": self.news_text.value}
        success, error = await dispatch_github_action(inputs)
        if success:
            await interaction.followup.send(content=f"✅ **News entry processing** for **{self.target_date}**. (~2-3 mins) ⏱️")
        else:
            await interaction.followup.send(content=f"❌ **Dispatch Failed:** {error}")

# --- COMMAND LOGIC ---

def get_target_date(date_input: str = None) -> str:
    """
    Parses date input. Supports:
    - None -> Today (UTC)
    - "0" -> Today (UTC)
    - "-1", "-2", etc. -> Days relative to today
    - "YYYY-MM-DD" -> Specific date
    """
    today = datetime.utcnow()
    if not date_input or date_input == "0":
        return today.strftime("%Y-%m-%d")
    
    # Handle relative dates (-1, -2, etc)
    if date_input.startswith("-") and date_input[1:].isdigit():
        days_back = int(date_input[1:])
        target = today - timedelta(days=days_back)
        return target.strftime("%Y-%m-%d")
    
    if date_input.isdigit() and not date_input.startswith("-"):
        # Support positive integers too (e.g. "1" for 1 day back)
        days_back = int(date_input)
        target = today - timedelta(days=days_back)
        return target.strftime("%Y-%m-%d")

    return date_input # Return as-is for validation later

async def dispatch_github_action(inputs: dict):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False, "Missing GITHUB_PAT or GITHUB_REPO configuration."

    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILENAME}/dispatches"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "ref": "main",
        "inputs": inputs
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as resp:
            if resp.status == 204:
                return True, "Success"
            else:
                try:
                    err_json = await resp.json()
                    err_msg = err_json.get("message", "Unknown error")
                except:
                    err_msg = await resp.text()
                return False, f"GitHub Error ({resp.status}): {err_msg}"

@bot.command()
async def inputnews(ctx, date_indicator: str = None):
    """Opens a date picker, then a text box to input market news."""
    
    async def news_callback(interaction, selected_date):
        await interaction.response.send_modal(NewsModal(target_date=selected_date))

    if not date_indicator:
        # Show Picker
        view = DateSelectionView(action_callback=news_callback)
        await ctx.send("🗓️ **Select Date for News Entry:**", view=view)
    else:
        # User provided a date or relative indicator (-1, etc)
        target_date = get_target_date(date_indicator)
        try:
            datetime.strptime(target_date, "%Y-%m-%d")
            # Open Modal directly
            # We can't open a modal from a command without an interaction, 
            # so we send a button if they provided a manual date string.
            class TriggerView(discord.ui.View):
                def __init__(self, date):
                    super().__init__()
                    self.date = date
                @discord.ui.button(label=f"📝 Open Box for {target_date}", style=discord.ButtonStyle.primary)
                async def go(self, interaction, button):
                    await interaction.response.send_modal(NewsModal(target_date=self.date))
            
            await ctx.send(f"✅ Target Date: **{target_date}**", view=TriggerView(target_date))
        except ValueError:
            await ctx.send(f"❌ Error: `{target_date}` is invalid. Use -1, -2, or leave blank.")

@bot.command()
async def updateeconomy(ctx, date_str: str = None, model_name: str = "gemini-3-flash-free"):
    """Dispatch Economy Update to GitHub Actions."""
    
    async def economy_callback(interaction, selected_date):
        # We need to handle model_name. We'll use the default or one provided in the command.
        # This callback is simple.
        await interaction.response.send_message(f"🧠 **Dispatching Economy Update** ({selected_date}) using `{model_name}`...", ephemeral=True)
        inputs = {"target_date": selected_date, "model": model_name}
        success, error = await dispatch_github_action(inputs)
        if success:
            await interaction.followup.send(content=f"✅ **Dispatch Successful!** ETA: ~5-7 mins. 📡⏱️")
        else:
            await interaction.followup.send(content=f"❌ **Dispatch Failed:** {error}")

    if not date_str:
        view = DateSelectionView(action_callback=economy_callback)
        await ctx.send(f"🌎 **Select Date for Economy Update** (using `{model_name}`):", view=view)
    else:
        target_date = get_target_date(date_str)
        # Check if user passed model name as first arg
        if date_str and "-" in date_str and len(date_str) > 10 and not date_str.startswith("-"):
            model_name = date_str
            target_date = get_target_date(None)

        try:
            datetime.strptime(target_date, "%Y-%m-%d")
            msg = await ctx.send(f"🧠 **Dispatching Economy Update** ({target_date}) to GitHub Actions...")
            inputs = {"target_date": target_date, "model": model_name}
            success, error = await dispatch_github_action(inputs)
            if success:
                await msg.edit(content=f"✅ **Dispatch Successful!** ETA: **~5-7 minutes**. 📡⏱️")
            else:
                await msg.edit(content=f"❌ **Dispatch Failed:** {error}")
        except ValueError:
            await ctx.send(f"❌ Error: `{target_date}` is an invalid date.")

@bot.command()
async def inspect(ctx):
    """Dispatch inspect command to GitHub Actions."""
    msg = await ctx.send("🔍 Dispatching database inspection...")
    inputs = {"action": "inspect"}
    success, error = await dispatch_github_action(inputs)
    if not success:
        await msg.edit(content=f"❌ **Dispatch Failed:** {error}")
    else:
        await msg.edit(content="✅ **Inspect Dispatched.** Report will arrive in **~2-3 minutes**. ⏱️")

@bot.command()
async def checknews(ctx, date_str: str = None):
    """Dispatch market news check to GitHub Actions."""
    
    async def check_callback(interaction, selected_date):
        await interaction.response.send_message(f"🔍 **Checking news** for **{selected_date}** via GitHub...", ephemeral=True)
        inputs = {"target_date": selected_date, "action": "check-news"}
        success, error = await dispatch_github_action(inputs)
        if success:
            await interaction.followup.send(content=f"✅ **Check Dispatched!** ETA: ~2-3 mins. 📡⏱️")
        else:
            await interaction.followup.send(content=f"❌ **Dispatch Failed:** {error}")

    if not date_str:
        view = DateSelectionView(action_callback=check_callback)
        await ctx.send("🔍 **Select Date to Check News:**", view=view)
    else:
        target_date = get_target_date(date_str)
        try:
            datetime.strptime(target_date, "%Y-%m-%d")
            msg = await ctx.send(f"🔍 **Checking news** for **{target_date}** via GitHub Actions...")
            inputs = {"target_date": target_date, "action": "check-news"}
            success, error = await dispatch_github_action(inputs)
            if success:
                await msg.edit(content=f"✅ **Check Dispatched!** ETA: **~2-3 minutes**. 📡⏱️")
            else:
                await msg.edit(content=f"❌ **Dispatch Failed:** {error}")
        except ValueError:
            await ctx.send(f"❌ Error: `{target_date}` is an invalid date.")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ Error: DISCORD_BOT_TOKEN not found.")
    else:
        bot.run(DISCORD_TOKEN)
