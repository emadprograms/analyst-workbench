import discord
import json

def format_economy_card(data_json: str, date_str: str) -> list[discord.Embed]:
    """Formats Economy Card JSON into a list of Discord Embeds."""
    try:
        data = json.loads(data_json)
    except:
        return [discord.Embed(title="❌ Error", description="Failed to parse Economy Card JSON.", color=discord.Color.red())]

    embeds = []
    
    # Part 1: Market Narrative & Bias
    embed1 = discord.Embed(
        title=f"🌎 ECONOMY CARD | {date_str} | Part 1/3",
        color=discord.Color.blue()
    )
    embed1.add_field(name="📜 Market Narrative", value=data.get("marketNarrative", "N/A"), inline=False)
    embed1.add_field(name="⚖️ Market Bias", value=data.get("marketBias", "N/A"), inline=True)
    
    action_log = data.get("keyActionLog", [])
    if action_log:
        log_text = "\n".join([f"• {item}" for item in action_log])
        if len(log_text) > 1024: log_text = log_text[:1021] + "..."
        embed1.add_field(name="📝 Key Action Log", value=log_text, inline=False)
    
    embeds.append(embed1)
    
    # Part 2: Economic Events & Sector Rotation
    embed2 = discord.Embed(
        title=f"🌎 ECONOMY CARD | {date_str} | Part 2/3",
        color=discord.Color.blue()
    )
    events = data.get("keyEconomicEvents", {})
    embed2.add_field(name="🕒 Last 24h Events", value=events.get("last_24h", "None"), inline=False)
    embed2.add_field(name="📅 Next 24h Events", value=events.get("next_24h", "None"), inline=False)
    
    rotation = data.get("sectorRotation", {})
    leading = ", ".join(rotation.get("leadingSectors", [])) or "None"
    lagging = ", ".join(rotation.get("laggingSectors", [])) or "None"
    embed2.add_field(name="📈 Leading Sectors", value=leading, inline=True)
    embed2.add_field(name="📉 Lagging Sectors", value=lagging, inline=True)
    embed2.add_field(name="🔄 Rotation Analysis", value=rotation.get("rotationAnalysis", "N/A"), inline=False)
    
    embeds.append(embed2)
    
    # Part 3: Index Analysis & Inter-Market
    embed3 = discord.Embed(
        title=f"🌎 ECONOMY CARD | {date_str} | Part 3/3",
        color=discord.Color.blue()
    )
    idx = data.get("indexAnalysis", {})
    idx_text = f"**Pattern**: {idx.get('pattern', 'N/A')}\n**SPY**: {idx.get('SPY', 'N/A')}\n**QQQ**: {idx.get('QQQ', 'N/A')}"
    embed3.add_field(name="📊 Index Analysis", value=idx_text, inline=False)
    
    inter = data.get("interMarketAnalysis", {})
    inter_text = (
        f"**Bonds**: {inter.get('bonds', 'N/A')}\n"
        f"**Commodities**: {inter.get('commodities', 'N/A')}\n"
        f"**Currencies**: {inter.get('currencies', 'N/A')}\n"
        f"**Crypto**: {inter.get('crypto', 'N/A')}"
    )
    embed3.add_field(name="🔗 Inter-Market Analysis", value=inter_text, inline=False)
    
    internals = data.get("marketInternals", {})
    embed3.add_field(name="📉 Market Internals (VIX)", value=internals.get("volatility", "N/A"), inline=False)
    
    embed3.set_footer(text="Powered by Analyst Workbench")
    embeds.append(embed3)
    
    return embeds

def format_company_card(data_json: str, ticker: str, date_str: str, historical_notes: str = "") -> list[discord.Embed]:
    """Formats Company Card JSON into a list of Discord Embeds."""
    try:
        data = json.loads(data_json)
    except:
        return [discord.Embed(title="❌ Error", description=f"Failed to parse Card JSON for {ticker}.", color=discord.Color.red())]

    embeds = []
    
    # Part 1: Basic Context & Technical Structure
    embed1 = discord.Embed(
        title=f"📊 {ticker} CARD | {date_str} | Part 1/3",
        color=discord.Color.green()
    )
    ctx = data.get("basicContext", {})
    embed1.add_field(name="🏢 Sector", value=ctx.get("sector", "N/A"), inline=True)
    embed1.add_field(name="📈 Trend", value=ctx.get("priceTrend", "N/A"), inline=True)
    embed1.add_field(name="🚀 Recent Catalyst", value=ctx.get("recentCatalyst", "N/A"), inline=False)
    
    tech = data.get("technicalStructure", {})
    tech_text = (
        f"**Major Support**: {tech.get('majorSupport', 'N/A')}\n"
        f"**Major Resistance**: {tech.get('majorResistance', 'N/A')}\n"
        f"**Pattern**: {tech.get('pattern', 'N/A')}\n"
        f"**Volume/Momentum**: {tech.get('volumeMomentum', 'N/A')}"
    )
    embed1.add_field(name="📐 Technical Structure", value=tech_text, inline=False)
    
    embeds.append(embed1)
    
    # Part 2: Behavioral Sentiment & Notes
    embed2 = discord.Embed(
        title=f"📊 {ticker} CARD | {date_str} | Part 2/3",
        color=discord.Color.green()
    )
    beh = data.get("behavioralSentiment", {})
    embed2.add_field(name="🎭 Emotional Tone", value=beh.get("emotionalTone", "N/A"), inline=True)
    embed2.add_field(name="⚖️ Buyer vs Seller", value=beh.get("buyerVsSeller", "N/A"), inline=True)
    embed2.add_field(name="📰 News Reaction", value=beh.get("newsReaction", "N/A"), inline=False)
    
    embed2.add_field(name="🎯 Confidence", value=data.get("confidence", "N/A"), inline=False)
    
    if historical_notes:
        notes_preview = historical_notes if len(historical_notes) < 1000 else historical_notes[:997] + "..."
        embed2.add_field(name="📜 Historical Notes", value=notes_preview, inline=False)
    
    embeds.append(embed2)
    
    # Part 3: Trade Plans
    embed3 = discord.Embed(
        title=f"📊 {ticker} CARD | {date_str} | Part 3/3",
        color=discord.Color.green()
    )
    
    op = data.get("openingTradePlan", {})
    op_text = (
        f"**Plan**: {op.get('planName', 'N/A')}\n"
        f"**Trigger**: {op.get('trigger', 'N/A')}\n"
        f"**Invalidation**: {op.get('invalidation', 'N/A')}"
    )
    embed3.add_field(name="🟢 Opening Trade Plan", value=op_text, inline=False)
    
    alt = data.get("alternativePlan", {})
    alt_text = (
        f"**Plan**: {alt.get('planName', 'N/A')}\n"
        f"**Trigger**: {alt.get('trigger', 'N/A')}\n"
        f"**Invalidation**: {alt.get('invalidation', 'N/A')}"
    )
    embed3.add_field(name="🟡 Alternative Plan", value=alt_text, inline=False)
    
    embed3.add_field(name="💡 Screener Briefing", value=data.get("screener_briefing", "N/A"), inline=False)
    
    embed3.set_footer(text="Powered by Analyst Workbench")
    embeds.append(embed3)
    
    return embeds
