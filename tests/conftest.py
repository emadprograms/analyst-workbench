import os
import pytest

def pytest_configure(config):
    """
    Runs before test collection.
    We disable Infisical credentials here to prevent the InfisicalManager
    from attempting to connect to the API during tests.
    This prevents network hangs and ensures tests run in 'offline' mode.
    """
    print("configured pytest: Disabling Infisical for test session.")
    
    # List of keys to unset
    keys = [
        "INFISICAL_TOKEN",
        "INFISICAL_CLIENT_ID",
        "INFISICAL_CLIENT_SECRET",
        "INFISICAL_PROJECT_ID"
    ]
    
    for key in keys:
        if key in os.environ:
            del os.environ[key]
