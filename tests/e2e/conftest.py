"""Shared fixtures for the e2e suite.

``bearer_for`` is defined alongside the multitenancy API tests (it owns the
local RSA keypair and the offline ``key_source`` wiring, so tokens verify with
zero network calls). Re-exporting it here makes pytest discover it for every
test in this directory, so a test can take ``bearer_for`` as a parameter
without importing it. Importing a fixture into the module that also names it
as an argument is what ruff flags as F811.
"""
from test_multitenancy import bearer_for  # noqa: F401 (re-exported fixture)

__all__ = ["bearer_for"]
