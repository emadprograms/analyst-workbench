from infisical_sdk import InfisicalSDKClient
import os
import logging
import asyncio
import inspect

class InfisicalManager:
    def __init__(self, logger=None):
        self.client = None
        self.is_connected = False
        self.logger = logger or logging.getLogger(__name__)

        if self._is_disabled():
            self.logger.info("🧪 Infisical disabled for this runtime.")
            return
        
        # Load from Env or Secrets file
        client_token = os.getenv("INFISICAL_TOKEN")
        client_id = os.getenv("INFISICAL_CLIENT_ID")
        client_secret = os.getenv("INFISICAL_CLIENT_SECRET")
        self.project_id = os.getenv("INFISICAL_PROJECT_ID")
        
        # --- Authentication with SDK ---
        try:
            if client_token:
                # Service Token Auth (Self-contained)
                self.client = InfisicalSDKClient(host="https://app.infisical.com")
                self.client.auth.login(token=client_token)
                self.is_connected = True
                self.logger.warning("✅ Infisical Connected (Service Token)")
            elif client_id and client_secret:
                # Universal Auth (Machine Identity)
                self.client = InfisicalSDKClient(host="https://app.infisical.com")
                self.client.auth.universal_auth.login(
                    client_id=client_id,
                    client_secret=client_secret
                )
                self.is_connected = True
                self.logger.warning("✅ Infisical Connected (Universal Auth)")
            else:
                self.logger.warning("⚠️ Infisical credentials not found. Running in offline/legacy mode.")
                self.is_connected = False
        except Exception as e:
            self.logger.error(f"❌ Infisical SDK Auth Failed: {e}")
            self.is_connected = False

    def _is_disabled(self):
        disable_flag = os.getenv("DISABLE_INFISICAL", "").strip().lower()
        if disable_flag in {"1", "true", "yes", "on"}:
            return True

        if os.getenv("PYTEST_CURRENT_TEST") is not None:
            return True

        return False

    def _run_maybe_async(self, callable_obj):
        result = callable_obj()
        if inspect.isawaitable(result):
            try:
                asyncio.run(result)
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(result)
                finally:
                    loop.close()

    def close(self):
        """
        Closes the Infisical client connection.
        """
        if self.client:
            try:
                if hasattr(self.client, "close") and callable(self.client.close):
                    self._run_maybe_async(self.client.close)
                elif hasattr(self.client, "aclose") and callable(self.client.aclose):
                    self._run_maybe_async(self.client.aclose)
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
            self.logger.info(f"DEBUG: Failed to get secret '{secret_name}': {e}")
            return None
