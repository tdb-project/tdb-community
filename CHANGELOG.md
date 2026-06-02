# Changelog

All notable changes to TDB Community Edition are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Changed

- **Docker image tag policy now follows release tags, not `main`.** `latest`, `X.Y.Z`, and `X.Y` are published only when a `v*` release tag is cut (so `:latest` always points at the newest stable release and skips pre-releases). Pushes to `main` publish a separate `edge` tag instead of moving `latest`. Pull `:edge` for bleeding-edge builds; pin `:X.Y.Z` (or a digest) for production.

## [0.4.2] — 2026-06-02

### Fixed

- **Row cap is now a true ceiling, not just a default** (`src/tdb/connectors/csv.py`). A query whose SQL carried its own larger `LIMIT` (e.g. `SELECT * FROM data LIMIT 99999`) previously bypassed the documented 1,000-row response cap and returned all rows. The connector now slices results to the requested `limit` unconditionally after fetch. Added a regression test.
- **`truncated` response flag now reflects reality.** It previously always reported `false`. The connector now reports whether the cap dropped rows, and both the REST `QueryResponse` and the MCP `query_source` result surface it — so callers (and AI agents) know when a result was capped at 1,000.
- **CSV source paths are validated at registration** (`src/tdb/routers/sources.py`, `src/tdb/routers/query.py`). Registering a CSV with a missing or unreadable `file_path` previously returned `201` and only failed later at query time as an HTTP **500** that echoed the absolute server path. Registration now rejects a bad path up front with **400** and persists nothing; if a source's file disappears after registration, queries return **503** with a source-name message and no path disclosure. Added regression tests. (#7)

### Security / housekeeping

- **CSV `file_path` can be confined to an allowed directory** via the new `TDB_ALLOWED_DATA_DIR` env var (`src/tdb/connectors/csv.py`, `src/tdb/config.py`). Without it the CSV connector reads any path the server process can access — a client with the API key could register `file_path: /etc/passwd` and read it back. When set, paths that resolve (symlinks and `..` expanded) outside the directory are rejected with **403** at register, schema, and query time. Opt-in so existing setups are unaffected; the Docker image defaults it to `/data`, so the bundled deployment is confined out of the box. Added tests. (#6)
- Removed the maintainer's personal email from `SECURITY.md` (now `security@tdb.jiracorp.co.in`) and a local build path from the `requirements.txt` header.
- Updated `starlette` 1.0.0 → 1.2.1 (PYSEC-2026-161).
- All product/contact URLs moved to `tdb.jiracorp.co.in`; docs now at `https://docs.tdb.jiracorp.co.in`.
- README reframed: Docker is the primary install path; running from source is the optional contributor path.

---

## [0.4.1] — 2026-05-31

### Fixed

- **Insecure-key warning was silent when `TDB_API_KEYS` env var was not set at all** (`src/tdb/main.py`). The `dev_mode` check used `os.environ.get("TDB_API_KEYS", "")`, so an absent env var returned `""` and the warning never fired — even though TDB was silently using the insecure default key. Changed the default to match `get_api_keys()`: `os.environ.get("TDB_API_KEYS", "dev-insecure-key-change-me")`. Warning now fires on bare `docker run` with no `-e TDB_API_KEYS` as well as via `docker compose up`.
- **Audit log timestamps were timezone-naive** (`src/tdb/audit/logger.py`). `datetime.utcnow()` is deprecated in Python 3.12 and removed in 3.14; it also produces naive datetimes without a UTC offset. Replaced with `datetime.now(UTC)`. Timestamps now include `+00:00` (e.g. `2026-05-23T11:09:05.593784+00:00`), which SIEM tools and log parsers require to place events correctly in timelines.

### Changed

- Renamed test files to be descriptive (`test_day3.py` → `test_api_auth_query.py`, `test_day4.py` → `test_persistence_mcp.py`).
- Updated `idna` transitive dependency 3.13 → 3.17 (resolves two Dependabot moderate advisories for CVE-2024-3651 bypass).

---

## [0.4.0] — 2026-05-09

### Added
- FastAPI lifespan migration — replaces deprecated `@app.on_event("startup")`
- Shared `tests/conftest.py` — temp SQLite DB via `mkstemp`, proper env isolation per test
- Manual testing guide — `docs/testing/manual_testing.md` with ten scenarios (MT-01 through MT-10)
- Pinned `requirements.txt` and `requirements-dev.txt` generated from `uv.lock`
- Startup warning printed to stdout if the default insecure dev API key is detected
- `SECURITY.md` — responsible disclosure policy, deployment hardening guide, known community edition constraints
- `CHANGELOG.md` — this file
- `CONTRIBUTING.md` — development setup, branch strategy, PR checklist
- `CODE_OF_CONDUCT.md` — Contributor Covenant v2.1
- `get_log_level()` in `config.py` — `TDB_LOG_LEVEL` env var now correctly validated and applied at startup
- CI: `pip-audit -r requirements.txt` dependency CVE scan step (scoped to runtime deps only)
- CI: `bandit -r src/ -ll` Python SAST step

### Changed
- `README.md` fully rewritten: Docker Compose quickstart first, MCP client connection section, CLI command reference, AGPLv3 badge, community vs enterprise feature table
- CI workflow: removed stale `TDB_SECRET_KEY` and `TDB_DEBUG` env vars that were never used
- `LICENSE` — replaced Apache 2.0 with correct AGPLv3 text (was a scaffolding error)
- `pyproject.toml` — removed three enterprise-only dependencies that were never used in community code (`python-jose`, `passlib`, `slowapi`); fixed license classifier to AGPLv3
- CLI `tdb serve` default `--host` changed from `0.0.0.0` to `127.0.0.1` — bandit B104 finding; production binding should be done via Docker Compose or explicit `--host 0.0.0.0`

### Fixed
- `pyproject.toml` version bumped to `0.4.0`; ruff lint config corrected
- **Audit log was completely non-functional.** `log_query()` was defined in `tdb/audit/logger.py` but never imported or called from any router. Both REST `POST /v1/query` and MCP `tools/call query_source` now write NDJSON entries to `TDB_LOG_FILE` as documented. (The product's headline marketing feature was dead.)
- **All API endpoints crashed with TypeError after first call.** Five `_log.info(...)`/`_log.error(...)` sites (in `main.py`, `routers/sources.py` ×2, `routers/query.py` ×2, `routers/mcp.py`) passed structlog-style keyword arguments to a standard Python logger, which raises `Logger._log() got an unexpected keyword argument`. Tests didn't catch this because `TestClient` lifespan/exception handling differs from real ASGI. Converted all calls to printf-style format strings.
- **`POST /v1/query` returned 500 with `LIMIT None`.** Two `QueryRequest` Pydantic models existed: `tdb/models.py` (default `limit=100`) and `tdb/models/source.py` (default `limit=None`). Python loaded the package over the file, so the `None` default was active. Removed the orphaned `tdb/models.py` shadow file; fixed the active model to default to `100`.
- **`docker compose build` failed with `License file does not exist`.** Dockerfile copied `pyproject.toml` (which references `LICENSE` and `README.md` via hatchling) before those files were present in the build context. Fixed COPY order; removed `*.md` exclusion of README.md from `.dockerignore`.
- **`uv sync --no-editable` in Dockerfile installed dependencies into `/app/.venv` while `CMD` used `/usr/local/bin/python`** — runtime ImportError for uvicorn. Switched to `uv pip install --system -r requirements.txt`.
- `TDB_LOG_LEVEL` environment variable was documented but silently ignored — now wired to Python root logger at startup

---

## [0.3.0] — 2026-05-08

### Security
- **SQL injection fix** — CSV connector switched from embedding the file path in a SQL string to `conn.register("data", conn.read_csv(path))`. The path is now passed through DuckDB's Python API and never interpolated into SQL.
- **MCP auth gap closed** — `tools/list` and `tools/call` now require a valid `Authorization: Bearer` token. Previously `tools/list` was unauthenticated.
- Corrected internal documentation: auth header is `Authorization: Bearer <key>`, not `X-TDB-Key`

---

## [0.2.0] — 2026-05-08

### Added
- **Persistent source registry** — SQLite-backed (replaces in-memory dict); survives server restarts
- **Schema endpoint** — `GET /v1/sources/{id}/schema` returns column names and inferred DuckDB types
- **MCP server** — HTTP JSON-RPC 2.0 at `/v1/mcp`; single tool `query_source`; `initialize` unauthenticated, all other methods require Bearer token
- **CLI** — `tdb serve`, `tdb register`, `tdb query` (table / JSON / CSV output formats); uses httpx to call the REST API (works against remote servers)
- **Docker** — `Dockerfile` (non-root `tdb` user, health check), `docker-compose.yml` (named volumes for data and logs, read-only CSV mount), `.dockerignore`

### Fixed
- Enum serialisation to SQLite — always use `.value` (`str(StrEnum.MEMBER)` returns `"ClassName.MEMBER"` in Python 3.12)
- SQLite `NULL` description field — handled gracefully in registry reads
- Test env var collision — test suite now uses isolated temp databases

---

## [0.1.0] — 2026-05-06

### Added
- Project scaffold: FastAPI, DuckDB, structlog, Typer, pytest, GitHub Actions CI
- **CSV connector** — reads CSV files via DuckDB `read_csv`; auto-detects column names and types
- **Bearer API key authentication** — `Authorization: Bearer <key>` via FastAPI `HTTPBearer`; keys read from `TDB_API_KEYS` env var
- **Source registry CRUD** — `POST /v1/sources`, `GET /v1/sources`, `GET /v1/sources/{id}`, `DELETE /v1/sources/{id}`
- **Community one-source limit** — second `POST /v1/sources` returns `409 Conflict`
- **Query endpoint** — `POST /v1/query`; SELECT only; 1,000 row hard cap enforced
- **SQL validator** — blocks `INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`, `EXEC`, semicolons, and empty queries
- **Local NDJSON audit log** — every query writes a JSON line to `TDB_LOG_FILE`
- **GitHub Actions CI** — runs on push to `develop` and PRs to `main`/`develop`; format check, lint, full test suite
- **Docker Compose** — one-command `docker compose up` deployment
- `pyproject.toml`, `.env.example`, `README.md`, `LICENSE`
