
import os
import sys

# Mock Infisical SDK
class MockSecrets:
    def get_secret_by_name(self, **kwargs):
        print(f"DEBUG: get_secret_by_name called with {kwargs}")
        return type('obj', (object,), {'secretValue': 'mocked_secret'})

class MockAuth:
    def login(self, **kwargs):
        print(f"DEBUG: login called with {kwargs}")
    
    @property
    def universal_auth(self):
        return self

class MockClient:
    def __init__(self, **kwargs):
        print(f"DEBUG: Client initialized with {kwargs}")
        self.auth = MockAuth()
        self.secrets = MockSecrets()

# Inject Mock
sys.modules['infisical_sdk'] = type('module', (object,), {'InfisicalSDKClient': MockClient})

# Now import our Manager
# We need to set env vars so it tries to connect
os.environ["INFISICAL_TOKEN"] = "test_token"
os.environ["INFISICAL_PROJECT_ID"] = "test_project"

from modules.core.infisical_manager import InfisicalManager

def test_manager():
    mgr = InfisicalManager()
    secret = mgr.get_secret("test_secret")
    print(f"RESULT: {secret}")
    if secret == 'mocked_secret':
        print("SUCCESS: Secret retrieved synchronously.")
    else:
        print(f"FAILURE: Secret is {type(secret)}")

if __name__ == "__main__":
    test_manager()
