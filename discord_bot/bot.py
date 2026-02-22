import os
import discord
from discord.ext import commands
import aiohttp
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv

# 1. Setup & Config
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_PAT")
GITHUB_REPO = os.getenv("GITHUB_REPO", "emadprograms/analyst-workbench") 
WORKFLOW_FILENAME = "manual_run.yml"
ACTIONS_URL = f"<https://github.com/{GITHUB_REPO}/actions>"

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
        today = datetime.utcnow()
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

# --- 3. Internal Logic Helpers ---

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
async def inputnews(ctx, date_indicator: str = None):
    """Opens a date picker, then a text box OR handles an attached .txt file."""
    print(f"[DEBUG] Command !inputnews called by {ctx.author}")
    
    # Check for attachments first
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
        if attachment.filename.endswith(('.txt', '.log')):
            target_date = get_target_date(date_indicator)
            await ctx.send(f"🛰️ **File Detected:** `{attachment.filename}`\nDispatching content for **{target_date}** to GitHub... 🚀")
            
            inputs = {
                "target_date": target_date,
                "action": "input-news",
                "news_url": attachment.url # Pass the Discord URL
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

    if not date_indicator:
        view = DateSelectionView(action_callback=news_callback)
        await ctx.send("🗓️ **Select Date for News Entry:**", view=view)
    else:
        target_date = get_target_date(date_indicator)
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
async def updateeconomy(ctx, date_str: str = None, model_name: str = "gemini-3-flash-free"):
    """Dispatch Economy Update to GitHub Actions."""
    print(f"[DEBUG] Command !updateeconomy called by {ctx.author}")
    target_date = get_target_date(date_str)
    
    async def economy_callback(interaction, selected_date):
        await interaction.response.edit_message(content=f"🧠 **Updating Economy** ({selected_date})... 🛰️", view=None)
        msg = await interaction.original_response()
        inputs = {"target_date": selected_date, "model": model_name}
        success, error = await dispatch_github_action(inputs)
        if success:
            await msg.edit(content=f"🧠 **Updating Economy** ({selected_date})...\n✅ **Dispatched!** (ETA: ~5-7 mins)\n🔗 [Monitor Progress]({ACTIONS_URL}) 📡⏱️")
        else:
            await msg.edit(content=f"🧠 **Updating Economy** ({selected_date})... ❌ **Failed:** {error}")

    if not date_str:
        view = DateSelectionView(action_callback=economy_callback)
        await ctx.send(f"🌎 **Select Date for Economy Update** (using `{model_name}`):", view=view)
    else:
        if date_str and "-" in date_str and len(date_str) > 10 and not date_str.startswith("-"):
            model_name = date_str
            target_date = get_target_date(None)

        try:
            datetime.strptime(target_date, "%Y-%m-%d")
            msg = await ctx.send(f"🧠 **Updating Economy** ({target_date})... 🛰️")
            inputs = {"target_date": target_date, "model": model_name}
            success, error = await dispatch_github_action(inputs)
            if success:
                await msg.edit(content=f"🧠 **Updating Economy** ({target_date})...\n✅ **Dispatched!** (ETA: ~5-7 mins)\n🔗 [Monitor Progress]({ACTIONS_URL}) 📡⏱️")
            else:
                await msg.edit(content=f"🧠 **Updating Economy** ({target_date})... ❌ **Failed:** {error}")
        except ValueError:
            await ctx.send(f"❌ Error: `{target_date}` is invalid.")

@bot.command()
async def inspect(ctx):
    """Dispatch inspect command to GitHub Actions."""
    print(f"[DEBUG] Command !inspect called by {ctx.author}")
    msg = await ctx.send("🔍 **Inspecting Database**... 🛰️")
    inputs = {"action": "inspect"}
    success, error = await dispatch_github_action(inputs)
    if success:
        await msg.edit(content="🔍 **Inspecting Database**...\n✅ **Dispatched!** (ETA: ~2-3 mins)\n🔗 [Monitor Progress]({ACTIONS_URL}) ⏱️")
    else:
        await msg.edit(content=f"🔍 **Inspecting Database**... ❌ **Failed:** {error}")

@bot.command()
async def checknews(ctx, date_str: str = None):
    """Dispatch market news check to GitHub Actions."""
    print(f"[DEBUG] Command !checknews called by {ctx.author}")
    async def check_callback(interaction, selected_date):
        await interaction.response.edit_message(content=f"🔍 **Checking news** for **{selected_date}**... 🛰️", view=None)
        msg = await interaction.original_response()
        inputs = {"target_date": selected_date, "action": "check-news"}
        success, error = await dispatch_github_action(inputs)
        if success:
            await msg.edit(content=f"🔍 **Checking news** for **{selected_date}**...\n✅ **Dispatched!** (ETA: ~2-3 mins)\n🔗 [Monitor Progress]({ACTIONS_URL}) 📡⏱️")
        else:
            await msg.edit(content=f"🔍 **Checking news** for **{selected_date}**... ❌ **Failed:** {error}")

    if not date_str:
        view = DateSelectionView(action_callback=check_callback)
        await ctx.send("🔍 **Select Date to Check News:**", view=view)
    else:
        target_date = get_target_date(date_str)
        try:
            datetime.strptime(target_date, "%Y-%m-%d")
            msg = await ctx.send(f"🔍 **Checking news** for **{target_date}**... 🛰️")
            inputs = {"target_date": target_date, "action": "check-news"}
            success, error = await dispatch_github_action(inputs)
            if success:
                await msg.edit(content=f"🔍 **Checking news** for **{target_date}**...\n✅ **Dispatched!** (ETA: ~2-3 mins)\n🔗 [Monitor Progress]({ACTIONS_URL}) 📡⏱️")
            else:
                await msg.edit(content=f"🔍 **Checking news** for **{target_date}**... ❌ **Failed:** {error}")
        except ValueError:
            await ctx.send(f"❌ Error: `{target_date}` is invalid.")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ Error: DISCORD_BOT_TOKEN not found.")
    else:
        bot.run(DISCORD_TOKEN)
