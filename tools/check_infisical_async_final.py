
import asyncio
import inspect
from infisical_sdk import InfisicalSDKClient

client = InfisicalSDKClient(host="https://app.infisical.com")
print(f"Token login is coroutine: {inspect.iscoroutinefunction(client.auth.token_auth.login)}")
print(f"Universal login is coroutine: {inspect.iscoroutinefunction(client.auth.universal_auth.login)}")
print(f"Get secret is coroutine: {inspect.iscoroutinefunction(client.secrets.get_secret_by_name)}")
