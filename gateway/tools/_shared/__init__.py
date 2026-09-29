"""Shared contract and logic modules bundled into every grid-tools Lambda.

Imports are always ``from _shared import ...`` because ``pythonpath`` puts
``gateway/tools`` on the path in tests and CDK copies ``_shared`` into each
Lambda asset, so the import path is identical in both places (design §3.2).
"""
