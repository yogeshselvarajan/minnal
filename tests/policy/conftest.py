"""Test setup for the Cedar policy suite (design §10, testing.md).

Sockets are already blocked repo-wide by ``tests/conftest.py``. The Hypothesis
profiles are registered in ``tests/tools/conftest.py``, which is a *sibling* of
this directory, not a parent, so pytest never loads it for ``tests/policy``.
We therefore register the same profiles here so the ``[SAFETY]`` property tests
P25 and P26 run at full strength (``default``/``ci`` ≥ 200 examples, R16.3).

The ``local`` backend defaults let the P25 test import ``propose_switching`` and
``dispatch_crew`` logic (which import ``_shared.settings``) without a real
deployment. ``cedarpy`` itself runs in-process and opens no socket.
"""

from __future__ import annotations

import os

from hypothesis import settings

# Registering an already-registered profile name is harmless; guard anyway so a
# combined run (tests/tools + tests/policy) never double-registers noisily.
if "default" not in settings._profiles:  # type: ignore[attr-defined]
    settings.register_profile("default", max_examples=200)
if "ci" not in settings._profiles:  # type: ignore[attr-defined]
    settings.register_profile("ci", max_examples=200, derandomize=True, deadline=None)
if "quick" not in settings._profiles:  # type: ignore[attr-defined]
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
