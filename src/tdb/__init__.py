"""The Data-Bridge (TDB) — community edition."""

# Single source of truth for the package version. Keep in sync with the
# [project] version in pyproject.toml at release time. Imported by main.py
# (FastAPI app version + root banner) and the MCP router (serverInfo) so the
# version is never hardcoded in more than one place.
__version__ = "0.4.2"
