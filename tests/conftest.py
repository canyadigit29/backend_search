"""Pytest configuration and path setup.

Ensures tests can import `app.api.*` modules even when pytest is invoked
from the multi-root workspace rather than the backend_search folder.

We prepend the absolute path to the `app` package to sys.path so imports like
`from app.api.v2.chat import router` resolve consistently.

If dev dependencies (pytest-asyncio, etc.) are not installed, tests that rely
on them should still skip gracefully. (Current tests only use TestClient and
do not require asyncio fixtures.)
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
APP_DIR = os.path.join(ROOT_DIR, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Debug note: uncomment to verify path during CI/local
# print("[conftest] sys.path head:", sys.path[:5])
