
import asyncio
import inspect
import os
try:
    from infisical_sdk import InfisicalSDKClient
    print("✅ infisical_sdk imported.")
    
    # Check if methods are async
    client = InfisicalSDKClient(host="https://app.infisical.com")
    print(f"Client: {client}")
    print(f"Auth login is coroutine: {inspect.iscoroutinefunction(client.auth.login)}")
    print(f"Get secret is coroutine: {inspect.iscoroutinefunction(client.secrets.get_secret_by_name)}")
    
except ImportError:
    print("❌ infisical_sdk not found.")
except Exception as e:
    print(f"💥 Error: {e}")
