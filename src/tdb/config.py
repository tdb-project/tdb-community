import os

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


def get_log_level() -> str:
    level = os.environ.get("TDB_LOG_LEVEL", "INFO").upper()
    return level if level in _VALID_LOG_LEVELS else "INFO"


# Values TDB's own files and docs have shown as examples, so anyone can guess them.
_PLACEHOLDER_API_KEYS = frozenset(
    {
        "dev-insecure-key-change-me",
        "CHANGE-ME-IN-PRODUCTION",
        "your-secret-key",
        "your-secret-key-here",
    }
)


class InsecureApiKeyError(RuntimeError):
    pass


def _configured_api_keys() -> list[str]:
    raw = os.environ.get("TDB_API_KEYS", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def get_api_keys() -> list[str]:
    """Never a placeholder, even if the startup check did not run."""
    return [k for k in _configured_api_keys() if k not in _PLACEHOLDER_API_KEYS]


def check_api_keys(other_login: bool = False) -> None:
    """
    Stop startup on a key anyone could guess, or on no key when nothing else lets
    anyone in. A default used to be built in, so forgetting the variable gave a server
    with a public credential on an open port, and a warning does not stop that.
    """
    generate = (
        'Generate one with: python3 -c "import secrets; print(secrets.token_hex(32))"'
    )
    configured = _configured_api_keys()
    if any(k in _PLACEHOLDER_API_KEYS for k in configured):
        raise InsecureApiKeyError(
            f"TDB_API_KEYS holds an example value that anyone can guess. {generate}"
        )
    if not configured and not other_login:
        raise InsecureApiKeyError(f"TDB_API_KEYS is not set. {generate}")


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
