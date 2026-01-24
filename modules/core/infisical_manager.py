from infisical_client import InfisicalClient, ClientSettings, GetSecretOptions, AuthenticationOptions, UniversalAuthMethod
import os
import toml
import logging

class InfisicalManager:
    def __init__(self, logger=None):
        self.client = None
        self.is_connected = False
        self.logger = logger or logging.getLogger(__name__)
        
        # Load from Env or Secrets file
        client_id = os.getenv("INFISICAL_CLIENT_ID")
        client_secret = os.getenv("INFISICAL_CLIENT_SECRET")
        self.project_id = os.getenv("INFISICAL_PROJECT_ID")
        
        # Fallback to local secrets.toml if env vars are missing
        if not client_id or not client_secret or not self.project_id:
            try:
                secrets_path = ".streamlit/secrets.toml"
                if os.path.exists(secrets_path):
                    data = toml.load(secrets_path)
                    sec = data.get("infisical", {})
                    if not client_id: client_id = sec.get("client_id")
                    if not client_secret: client_secret = sec.get("client_secret")
                    if not self.project_id: self.project_id = sec.get("project_id")
            except Exception as e:
                self.logger.warning(f"Failed to read local secrets for Infisical fallback: {e}")

        if client_id and client_secret:
            try:
                auth_method = UniversalAuthMethod(client_id=client_id, client_secret=client_secret)
                options = AuthenticationOptions(universal_auth=auth_method)
                self.client = InfisicalClient(ClientSettings(auth=options))
                self.is_connected = True
                self.logger.info("✅ Infisical Connected")
            except Exception as e:
                self.logger.error(f"❌ Infisical Auth Failed: {e}")
                self.is_connected = False
        else:
            self.logger.warning("⚠️ Infisical credentials not found (Env or secrets.toml). Running in offline/legacy mode.")

    def get_secret(self, secret_name):
        """
        Fetches a secret from Infisical. Returns None if not connected or not found.
        """
        if not self.is_connected: 
            return None
        
        try:
            # NOTE: Use snake_case for options as per SDK v2
            secret = self.client.getSecret(options=GetSecretOptions(
                secret_name=secret_name,
                project_id=self.project_id,
                environment="dev", # Defaulting to dev for now, could be configurable
                path="/"
            ))
            # NOTE: Use snake_case for attribute access
            return secret.secret_value 
        except Exception as e:
            # Silence specific "not found" errors to avoid log spam if falling back
            pass
            return None
