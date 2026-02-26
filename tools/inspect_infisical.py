
import asyncio
from infisical_sdk import InfisicalSDKClient

client = InfisicalSDKClient(host="https://app.infisical.com")
print(f"Client: {dir(client)}")
print(f"Auth: {dir(client.auth)}")
print(f"Secrets: {dir(client.secrets)}")
