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


def get_allowed_data_dir() -> str | None:
    """
    Directory that CSV ``file_path`` values must reside within.

    When set, the CSV connector rejects any source whose file resolves to a
    path outside this directory (symlinks and ``..`` are resolved first). When
    unset (the default), no restriction is applied — paths are allowed as-is.
    The Docker image sets this to ``/data`` so the bundled deployment is
    confined out of the box.
    """
    val = os.environ.get("TDB_ALLOWED_DATA_DIR", "").strip()
    return val or None
