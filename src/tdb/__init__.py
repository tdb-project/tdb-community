"""The Data-Bridge (TDB) — community edition."""

# Single source of truth for the package version. Bump it here and nowhere
# else: pyproject.toml declares `dynamic = ["version"]` and hatchling reads
# this line at build time, and main.py (FastAPI app version + root banner) and
# the MCP router (serverInfo) import it at runtime.
__version__ = "0.4.3"
