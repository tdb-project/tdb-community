import os

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


def get_log_level() -> str:
    level = os.environ.get("TDB_LOG_LEVEL", "INFO").upper()
    return level if level in _VALID_LOG_LEVELS else "INFO"


def get_api_keys() -> list[str]:
    return [
        k.strip()
        for k in os.environ.get("TDB_API_KEYS", "dev-insecure-key-change-me").split(",")
        if k.strip()
    ]


def get_log_file() -> str:
    return os.environ.get("TDB_LOG_FILE", "tdb_audit.jsonl")


def get_registry_db_path() -> str:
    return os.environ.get("TDB_REGISTRY_DB", "data/tdb_registry.db")
