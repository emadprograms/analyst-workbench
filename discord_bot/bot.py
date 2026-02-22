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

def get_today_utc():
    return datetime.utcnow().strftime("%Y-%m-%d")

@bot.command()
async def inputnews(ctx, date_str: str = None, *, news_text: str = None):
    """Dispatch market news input to GitHub Actions."""
    if date_str and not news_text:
        # User likely sent !inputnews "the news text" without a date
        # We shift things: news_text = date_str, date_str = today
        news_text = date_str
        date_str = get_today_utc()
    elif not date_str:
        await ctx.send("❌ Error: You must provide news text. Example: `!inputnews The market is up.`")
        return
    
    if not date_str: date_str = get_today_utc()

    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        # If it's not a date, maybe it's the start of the news text
        news_text = f"{date_str} {news_text}" if news_text else date_str
        date_str = get_today_utc()

    msg = await ctx.send(f"🛰️ Dispatching news entry for **{date_str}** to GitHub Actions...")
    
    inputs = {
        "target_date": date_str,
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
    if not date_str: date_str = get_today_utc()
    
    # Check if the user passed a model name instead of a date
    if date_str and "-" in date_str and len(date_str) > 10:
        # Likely a model name like gemini-3-flash-free
        model_name = date_str
        date_str = get_today_utc()

    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        await ctx.send("❌ Invalid date format. Use YYYY-MM-DD or leave blank for today.")
        return

    msg = await ctx.send(f"🧠 **Dispatching Economy Update** ({date_str}) to GitHub Actions...")
    
    inputs = {
        "target_date": date_str,
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
    if not date_str: date_str = get_today_utc()

    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        await ctx.send("❌ Invalid date format. Use YYYY-MM-DD or leave blank for today.")
        return

    msg = await ctx.send(f"🔍 **Checking news** for **{date_str}** via GitHub Actions...")
    
    inputs = {
        "target_date": date_str,
        "action": "check-news"
    }
    
    success, error = await dispatch_github_action(inputs)
    if success:
        await msg.edit(content=f"✅ **Check Dispatched!**\n> The news report for {date_str} will arrive in **~2-3 minutes**. 📡⏱️")
    else:
        await msg.edit(content=f"❌ **Dispatch Failed:** {error}")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ Error: DISCORD_BOT_TOKEN not found.")
    else:
        bot.run(DISCORD_TOKEN)
