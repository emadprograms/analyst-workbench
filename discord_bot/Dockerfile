# Discord Bot — Railway Deployment
# Build context must be the REPO ROOT (not discord_bot/).
# Railway settings:
#   Root Directory  →  /  (repo root)
#   Dockerfile Path →  discord_bot/Dockerfile

FROM python:3.13-slim

WORKDIR /app

# Copy only what the bot needs
COPY modules/ ./modules/
COPY discord_bot/ ./discord_bot/

# Install bot dependencies
RUN pip install --no-cache-dir -r discord_bot/requirements.txt

# Set working directory so plain imports (config, ui_components) resolve
WORKDIR /app/discord_bot

CMD ["python", "bot.py"]
