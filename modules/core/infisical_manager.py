from infisical_sdk import InfisicalSDKClient
import os
import toml
import logging

class InfisicalManager:
    def __init__(self, logger=None):
        self.client = None
        self.is_connected = False
        self.logger = logger or logging.getLogger(__name__)
        
        # Load from Env or Secrets file
        client_token = os.getenv("INFISICAL_TOKEN")
        client_id = os.getenv("INFISICAL_CLIENT_ID")
        client_secret = os.getenv("INFISICAL_CLIENT_SECRET")
        self.project_id = os.getenv("INFISICAL_PROJECT_ID")
        
        # Fallback to local secrets.toml if env vars are missing
        if not client_token and (not client_id or not client_secret or not self.project_id):
            try:
                secrets_path = ".streamlit/secrets.toml"
                if os.path.exists(secrets_path):
                    data = toml.load(secrets_path)
                    sec = data.get("infisical", {})
                    if not client_token: client_token = sec.get("token")
                    if not client_id: client_id = sec.get("client_id")
                    if not client_secret: client_secret = sec.get("client_secret")
                    if not self.project_id: self.project_id = sec.get("project_id")
            except Exception as e:
                self.logger.warning(f"Failed to read local secrets for Infisical fallback: {e}")

        # --- NEW: Authenticate with SDK ---
        try:
            if client_token:
                # Service Token Auth (Self-contained)
                self.client = InfisicalSDKClient(host="https://app.infisical.com")
                self.client.auth.login(token=client_token)
                self.is_connected = True
                self.logger.info("✅ Infisical Connected (Service Token)")
            elif client_id and client_secret:
                # Universal Auth (Machine Identity)
                self.client = InfisicalSDKClient(host="https://app.infisical.com")
                self.client.auth.universal_auth.login(
                    client_id=client_id,
                    client_secret=client_secret
                )
                self.is_connected = True
                self.logger.info("✅ Infisical Connected (Universal Auth)")
            else:
                self.logger.warning("⚠️ Infisical credentials not found. Running in offline/legacy mode.")
        except Exception as e:
            self.logger.error(f"❌ Infisical SDK Auth Failed: {e}")
            self.is_connected = False

    def close(self):
        """
        Closes the Infisical client connection.
        """
        if self.client:
            try:
                # The new SDK might not have an explicit close if it doesn't use persistent sessions,
                # but we check if it has a way to shut down. 
                # If it uses aiohttp under the hood, we want to ensure it's cleaned up.
                self.client = None
                self.is_connected = False
            except Exception:
                pass

    def get_secret(self, secret_name):
        """
        Fetches a secret from Infisical. Returns None if not connected or not found.
        """
        if not self.is_connected: 
            return None
        
        try:
            secret = self.client.secrets.get_secret_by_name(
                secret_name=secret_name,
                project_id=self.project_id,
                environment_slug="dev",
                secret_path="/"
            )
            return secret.secretValue 
        except Exception as e:
            # Silence specific "not found" errors to avoid log spam if falling back
            return None
