"""Test setup for the grid-tools suite (design §19.3).

Sockets are already blocked repo-wide by ``tests/conftest.py``; here we add the
Hypothesis profiles, fake AWS credentials so a stray boto3 client construction
never reaches a real credential chain, and the local-backend defaults every
tool needs to import ``Settings`` without a real deployment.

- ``default`` and ``ci`` both run at least 200 examples (R16.3); the Kiro IDE
  uses ``default`` so IDE runs are full strength.
- ``quick`` (50) is opt-in for local iteration only; CI never selects it.
- ``ci`` is derandomised for reproducible generation without a committed
  database (R16.4).
"""

from __future__ import annotations

import os

from hypothesis import settings

settings.register_profile("default", max_examples=200)
settings.register_profile("ci", max_examples=200, derandomize=True, deadline=None)
settings.register_profile("quick", max_examples=50)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))

# Fake AWS credentials: construction never touches a real credential chain.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

# Local-backend defaults so `Settings()` validates without a real deployment.
os.environ.setdefault("MINNAL_BACKEND", "local")
os.environ.setdefault("MINNAL_EMERGENCY_NUMBER", "100")
os.environ.setdefault("MINNAL_LOCAL_STORE_DIR", ".local/grid-tools-tests")
