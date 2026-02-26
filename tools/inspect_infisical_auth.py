
from infisical_sdk import InfisicalSDKClient
client = InfisicalSDKClient(host="https://app.infisical.com")
print(f"Universal Auth: {dir(client.auth.universal_auth)}")
try:
    print(f"Token Auth: {dir(client.auth.token_auth)}")
except AttributeError:
    print("Token Auth not found.")
