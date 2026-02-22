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

@bot.event
async def on_ready():
    print(f"✅ Major Action logged in as {bot.user.name}")

def get_target_date(date_input: str = None) -> str:
    """
    Parses date input. Supports:
    - None -> Today (UTC)
    - "-1", "-2", etc. -> Days relative to today
    - "YYYY-MM-DD" -> Specific date
    """
    today = datetime.utcnow()
    if not date_input:
        return today.strftime("%Y-%m-%d")
    
    # Handle relative dates (-1, -2, etc)
    if date_input.startswith("-") and date_input[1:].isdigit():
        days_back = int(date_input[1:])
        target = today - timedelta(days=days_back)
        return target.strftime("%Y-%m-%d")
    
    if date_input.isdigit() and not date_input.startswith("-"):
        # Support positive integers too just in case (e.g. "1" for 1 day back)
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
async def inputnews(ctx, date_str: str = None, *, news_text: str = None):
    """Dispatch market news input to GitHub Actions."""
    # 1. Resolve relative date or default
    resolved_date = get_target_date(date_str)
    
    # 2. Logic to handle !inputnews "text" vs !inputnews -1 "text"
    if date_str == resolved_date and not news_text:
        # User didn't provide news text, only a date
        await ctx.send("❌ Error: You must provide news text. Example: `!inputnews -1 The market rallied.`")
        return
    
    if date_str != resolved_date:
        # Relative date was used, news_text must be present
        if not news_text:
            await ctx.send("❌ Error: You must provide news text after the relative date indicator.")
            return
        target_date = resolved_date
    else:
        # No date_str or it's a fixed date
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            target_date = date_str
        except (ValueError, TypeError):
            # date_str is actually news text
            news_text = f"{date_str} {news_text}" if news_text else date_str
            target_date = get_target_date(None)

    msg = await ctx.send(f"🛰️ Dispatching news entry for **{target_date}** to GitHub Actions...")
    
    inputs = {
        "target_date": target_date,
        "action": "input-news",
        "text": news_text
    }
    
    success, error = await dispatch_github_action(inputs)
    if success:
        await msg.edit(content=f"✅ **Dispatch Successful!**\n> News entry is being processed on GitHub. (ETA: **~2-3 minutes**) ⏱️")
    else:
        await msg.edit(content=f"❌ **Dispatch Failed:** {error}")

@bot.command()
async def updateeconomy(ctx, date_str: str = None, model_name: str = "gemini-3-flash-free"):
    """Dispatch Economy Update to GitHub Actions."""
    target_date = get_target_date(date_str)
    
    # Handle if user passed model name as first arg
    if date_str and "-" in date_str and len(date_str) > 10 and not date_str.startswith("-"):
        model_name = date_str
        target_date = get_target_date(None)

    try:
        datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        await ctx.send("❌ Invalid date format. Use YYYY-MM-DD, -1, -2, or leave blank for today.")
        return

    msg = await ctx.send(f"🧠 **Dispatching Economy Update** ({target_date}) to GitHub Actions...")
    
    inputs = {
        "target_date": target_date,
        "model": model_name
    }
    
    success, error = await dispatch_github_action(inputs)
    if success:
        await msg.edit(content=f"✅ **Dispatch Successful!**\n> **Analyst Workbench** is initializing... The dashboard report will arrive here in **~5-7 minutes**. 📡⏱️")
    else:
        await msg.edit(content=f"❌ **Dispatch Failed:** {error}")

@bot.command()
async def inspect(ctx):
    """Dispatch inspect command to GitHub Actions."""
    msg = await ctx.send("🔍 Dispatching database inspection...")
    inputs = {
        "action": "inspect"
    }
    success, error = await dispatch_github_action(inputs)
    if not success:
        await msg.edit(content=f"❌ **Dispatch Failed:** {error}")
    else:
        await msg.edit(content="✅ **Inspect Dispatched.** Report will arrive in **~2-3 minutes**. ⏱️")

@bot.command()
async def checknews(ctx, date_str: str = None):
    """Dispatch market news check to GitHub Actions."""
    target_date = get_target_date(date_str)

    try:
        datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        await ctx.send("❌ Invalid date format. Use YYYY-MM-DD, -1, -2, or leave blank for today.")
        return

    msg = await ctx.send(f"🔍 **Checking news** for **{target_date}** via GitHub Actions...")
    
    inputs = {
        "target_date": target_date,
        "action": "check-news"
    }
    
    success, error = await dispatch_github_action(inputs)
    if success:
        await msg.edit(content=f"✅ **Check Dispatched!**\n> The news report for {target_date} will arrive in **~2-3 minutes**. 📡⏱️")
    else:
        await msg.edit(content=f"❌ **Dispatch Failed:** {error}")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ Error: DISCORD_BOT_TOKEN not found.")
    else:
        bot.run(DISCORD_TOKEN)
