# Changelog

All notable changes to TDB Community Edition are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

## [0.9.2] — 2026-10-10

> Patch: the row cap is applied by DuckDB again when a query ends in a comment.

### Fixed

- **A trailing `-- comment` no longer hides the row cap from the engine.** TDB
  appends `LIMIT <n>` to a query that has none, and appended it on the same
  line, so `SELECT * FROM data -- note` became `… -- note LIMIT 1001` and DuckDB
  read the cap as part of the comment. Responses were always correct (the
  connector reads at most `limit + 1` rows, and `truncated` was accurate), but
  the engine computed the whole result first. The cap now goes on its own line.
  Parity invariant P14.

## [0.9.1] — 2026-10-09

> Patch: the published image no longer contains pip.

### Security

- **pip is removed from the image.** The `python:3.12-slim` base image ships
  pip 25.0.1, which image scanners report for six CVEs, including
  CVE-2026-13346 (GHSA-qwm4-qh6w-59xr). TDB never runs pip — uv installs the
  dependencies at build time — so it was not exposed in use. Rather than
  upgrade a tool nothing uses, the image now drops pip and `ensurepip`'s
  bundled pip wheel. Verified with Trivy: 0 Python-package findings.

## [0.9.0] — 2026-10-09

> Minor because SQL that returned 400 now returns 200.

### Fixed

- **A write keyword used as an alias is accepted.** `SELECT … AS update` was
  refused as a write. A blocked word directly after `AS` is now read as an
  alias; a writing CTE (`WITH x AS (DELETE …)`) is still refused.

## [0.8.0] — 2026-10-09

> Minor because SQL that returned 400 now returns 200 — the same reasoning as
> 0.6.0.

### Fixed

- **The `replace()` string function is accepted.** `REPLACE` was on the
  write-keyword list as a whole word, for MySQL's `REPLACE [INTO] t …`
  statement, so any query using the string function —
  `SELECT replace(note, 'a', 'b') FROM data` — was refused with
  `Blocked keyword: replace`. `replace` followed by `(` is now read as the
  function; the statement is still refused. No supported engine has a
  `REPLACE` statement in which `(` follows the keyword.

## [0.7.2] — 2026-10-09

> Patch carrying a **security fix**. Upgrade.

### Security

- **The read-only check could be shown one statement while the engine ran
  several.** The SQL validator decided where string literals and comments end
  the ANSI way only. DuckDB, like PostgreSQL, also reads dollar-quoted strings,
  `E'…'` strings with backslash escapes and nested block comments, so carefully
  quoted SQL could carry a second statement past the one-statement rule
  introduced in 0.7.0 — and DuckDB executed it. The engine lock from 0.7.0 still
  confined file access to the data directory, but inside that directory a
  second statement could write. Any caller with query access was affected.
  Fixed in two independent places:
  - the validator now reads the SQL the way every supported engine does and
    must accept it under each reading;
  - the CSV connector asks DuckDB's own parser and runs the query only if it is
    exactly one `SELECT`. A refusal at this layer is a 400, audited as
    `sql_validation_failed`, on REST and MCP.

  SQL that some engine would read differently is now refused for all of them —
  for example a string ending in a backslash followed by a quote. Ordinary
  read-only SQL, including dollar-quoted strings and Postgres JSON operators,
  is unaffected.

## [0.7.1] — 2026-09-28

> Patch: a query ending in `;` works.

### Fixed

- **A trailing `;` no longer breaks a query.** The CSV connector appended its row cap to
  the end of the statement, after the `;` — `SELECT 1;` became
  `SELECT 1; LIMIT 6`, a syntax error returned as a 500. The validator had
  always accepted the statement, so it failed only at execution, on every
  release. One trailing `;` (and any whitespace or comment after it) is now
  removed before the cap is added; a `;` inside a string or comment is left
  alone.

## [0.7.0] — 2026-09-28

> Minor, and it carries a **security fix**: SQL could read any file the server
> process could. Upgrade. A query containing a second statement now returns 400
> where it used to run — that behaviour change is why this is not a patch.

### Security

- **SQL can no longer read files outside the data directory.** DuckDB resolves
  file paths written *inside* a query — `read_csv('/etc/passwd')`,
  `read_text(...)` — not only the CSV TDB registers, and `TDB_ALLOWED_DATA_DIR`
  only ever checked the registered path. So any SELECT on a registered source
  could read any file the server process could read, with or without
  confinement set. The CSV engine now refuses file access outside the data
  directory (`TDB_ALLOWED_DATA_DIR`, or the registered CSV's own directory when
  that is unset), no longer auto-loads extensions, and locks its configuration
  so a query cannot undo either. A refused read returns **403** and is written
  to the audit log as `reason: "sql_file_access"`, on both REST and MCP.
  Affects every release up to and including 0.6.1.

### Changed

- **A query may contain only one statement.** `SELECT 1; SELECT 2` used to be
  accepted, run both, and return only the last result set; it now returns
  **400** (`Only one statement per query is allowed`). Only the first
  statement's opening keyword was ever checked, so a second statement was the
  one route to SQL the keyword list does not name. A single trailing `;` is
  still accepted, as is a `;` inside a string or comment.

## [0.6.1] — 2026-09-28

> Patch: a dependency refresh and nothing else. No behaviour, API or
> configuration change. TDB was not exposed to the advisories below; this clears
> a critical-rated CVE from scans of the published image.

### Security

- **`anyio` raised to 4.14.2** for GHSA-82r6-8w77-94w6 (critical — TLS
  certificate spoofing via IDNA 2003 hostname encoding in `TLSStream`),
  GHSA-3w57-8xmc-8v26 and GHSA-5p39-cfhj-2xmp (both in `run_process` /
  `open_process` / process-pool workers). **TDB is not exposed to any of them**:
  the only installed user of `TLSStream` is httpcore's async backend, reached
  only through `httpx.AsyncClient`, which TDB never constructs — every outbound
  call (the CLI client) is synchronous — and no installed package calls
  AnyIO's subprocess APIs. Supply-chain hygiene; `anyio` stays transitive via
  httpx and starlette.

## [0.6.0] — 2026-09-05

> Minor: `validate_sql()` now accepts **CTEs** (`WITH … SELECT`) and statements
> that open with a comment. SQL that used to return 400 now returns 200 —
> nothing that worked stops working, and no configuration changes.

### Changed

- **CTEs (`WITH … SELECT`) are accepted.** `validate_sql()` required a statement
  to *start* with `SELECT`, so every common table expression was refused with
  400 — standard, read-only SQL. The guard was doing no write-protection work
  for `WITH`: the blocked-keyword scan runs first and ignores the opening token,
  so `WITH w AS (INSERT … RETURNING *) SELECT * FROM w` was already refused, and
  still is (`Blocked keyword: INSERT`). Recursive CTEs included.
- **A leading comment no longer causes a rejection.** `/* note */ SELECT 1` and
  a leading `--` line comment were both refused. The opening token is now read
  off the masked SQL, which already blanks comments for the keyword scan.
- The rejection message for a bad opening token is now
  `Only SELECT and WITH statements are allowed`.

Unchanged and still open: semicolon-separated statements are accepted and only
the last result set is returned.

## [0.5.0] — 2026-08-30

> Minor: `tdb query` gains `--source`, and stops capping `--limit` before
> sending. **Two behaviour changes** — a `--limit` above 1,000 is now an error
> rather than a quietly shortened result.

### Added

- **`tdb query --source` (`-s`)** names the source to query, by registered name
  or UUID. The server resolves either form, so the CLI sends what you typed and
  does no lookup of its own — and when you name a source it skips the
  `GET /v1/sources` call entirely, which also means the command works with a key
  that cannot list sources. Omitting it is unchanged while one source is
  registered, which in this edition is always.

### Changed

- **`tdb query --limit` is no longer capped by the CLI before sending.** It used
  to send `min(limit, 1000)`, so `--limit 5000` quietly returned 1,000 rows that
  looked like the complete answer. The ceiling is the server's — it has to be,
  since REST and MCP callers never run the CLI — and a request above it is now
  refused rather than silently shortened. **If you passed a `--limit` above
  1,000 you will now see an error where you previously got 1,000 rows.**
- **A server error with a structured body now prints as a sentence.** The CLI
  interpolated `detail` directly, which is a string for most errors but a list
  of validation entries for a 422 — the response an over-ceiling `--limit`
  produces. That printed as a raw Python list.

## [0.4.8] — 2026-08-30

> Patch: two `tdb query` display fixes. A NULL now renders as a blank cell
> instead of the word `None`, and asking for an output format that does not
> exist fails instead of quietly printing a table.

### Fixed

- **`tdb query` no longer prints `None` for a NULL.** The table renderer used
  `str(row.get(col, ""))`, whose `""` default never applied — the column key is
  always present — so a NULL became the string `None`, indistinguishable from a
  column whose value genuinely is that text. An empty cell in your CSV is the
  ordinary way a NULL arrives, so a blank cell and the literal text `None`
  rendered identically. `--output json` and `--output csv` were already correct,
  making the default table format the only affected one. NULL now renders blank,
  matching `--output csv` and psql's default; use `--output json` when `null` and
  the empty string have to be told apart — it is the only format that can.
- **An unrecognised `--output` is now an error rather than a table.** `-o jsn`
  exited 0 with table output, so a script asking for the wrong format received
  plausible-looking data instead of a failure. It now exits 1 and names the
  valid formats.

### Changed

- `src/tdb/cli` has tests for the first time — 0% to 84% coverage. Both bugs
  above were found by running the commands, not by reading them.


## [0.4.7] — 2026-08-11

> Patch. The visible change: `truncated` can now be `true` on queries with no
> `LIMIT` of their own — meaning the flag does its documented job for the first
> time. If your tooling treats `truncated:false` as "complete result", it can
> now actually rely on that.

### Fixed

- **`truncated:false` no longer claims completeness for a cut result.** With no
  `LIMIT` in your SQL, TDB injected `LIMIT <limit>`, the source returned exactly
  `limit` rows, and the flag could never become `true` — a 1,500-row CSV queried
  with `limit: 1000` returned 1,000 rows and `truncated: false`, while the docs
  say the flag means "you received the whole result". A client trusting that —
  including an AI agent deciding whether it has the full answer — silently lost
  rows. The injection now passes `limit + 1` so the sentinel row that sets the
  flag can come back. Asserted as parity invariant **P7**.
- **The audit trail's `source_id` is now the source's UUID.** Queries made by
  source name logged the name, so grepping the trail by UUID missed them.
  Post-resolution entries record the UUID; a `source_not_found` denial still
  records the unresolvable ref, since the bad ref is the information.
- **An unopenable registry now fails startup with an error that explains
  itself** — naming the database path, the container uid, and the bind-mount fix
  (`mkdir -p data` before `docker run`), instead of SQLite's bare "unable to
  open database file".


## [0.4.6] — 2026-08-03

> Patch, performance only. No API, config or behaviour change. If you have ever
> found TDB slow under more than one or two simultaneous queries, this is why.

### Fixed

- **The CSV connector built and destroyed a whole DuckDB engine on every query.**
  A single query was fine (~228 ms on a 5.4 MB file), but **throughput fell as load
  rose** — it peaked at two concurrent queries and dropped 12× from there, to
  0.35 req/s at sixteen. The server got slower, in absolute terms, the more it was
  asked to do.

  Three causes compounded. Creating and closing the engine cost 70–130 ms per query
  regardless of file size — on a small CSV, more time than the query itself. The
  file was fully re-parsed every time. And DuckDB claims one thread per CPU core,
  so every concurrent query had its own engine doing that: sixteen queries on a
  six-core host meant 96 threads competing for 6 cores.

  Queries now share one engine, each on its own cursor. Measured on the same
  5.4 MB file: **228 ms → 131 ms** for a single query, and at sixteen concurrent
  queries **50.9 s → 317 ms** (0.35 → 28.1 req/s). On a 55.8 MB file, 24 queries
  at that concurrency went from 46.7 s to 1.75 s, with memory use unchanged.

  Nothing about your file is cached: rows you append, and columns you add or
  remove, are still picked up on the next query.

> Patch. Some SQL that was refused with `400 Blocked keyword: …` now runs — an
> ordinary filter like `WHERE status = 'update pending'`. Nothing that was allowed
> becomes refused, and nothing that was refused for a real reason becomes allowed.

### Fixed

- **A write keyword inside a string, comment or quoted identifier is no longer refused
  as a write.** The read-only check scanned the raw SQL, so
  `WHERE status = 'update pending'` came back `400 Blocked keyword: update` — and since
  0.4.3 the refusal was written to the audit log as a denial, making an ordinary filter
  look like an attempted write. String literals, `--` and `/* */` comments, and quoted
  identifiers are now excluded from the scan.

  The masking is deliberately conservative, because it guards a security check: an
  unterminated quote or comment leaves the rest of the statement visible to the scan,
  backslash is not treated as an escape (standard SQL rules, so nothing is hidden on
  PostgreSQL), MySQL's executable `/*! ... */` comments are treated as code, and T-SQL
  `[identifier]` brackets are not treated as quoting. Every existing refusal still
  refuses — `SELECT 'a'; DROP TABLE t` included.

## [0.4.4] — 2026-08-01

> Patch. **No API change** — the same rows and the same `truncated` flag come back
> from every query. What changes is how much memory the server uses to produce them.
> Nothing to do on upgrade.

### Fixed

- **The 1,000-row cap now bounds memory, not just the response.** The cap was enforced
  by reading the query's entire result out of DuckDB and slicing it — the response was
  always correct, but a query the injector could not cap (`SELECT … LIMIT 99999`, or one
  that merely contains the word, e.g. `WHERE note = 'no limit'`) turned every row it
  produced into Python objects first. On a 200,000-row CSV that is **30.9 MB for a
  ten-row answer; it is now 0.01 MB**, because the connector reads at most `limit + 1`
  rows. No API change — same rows, same `truncated` flag.

## [0.4.3] — 2026-07-28

### Added

- **Denied and blocked attempts are now audit events.** Previously only *successful* queries were written to the audit log; anything refused (bad or missing API key, non-`SELECT` SQL, unknown source, a path outside `TDB_ALLOWED_DATA_DIR`, an unreadable CSV at register time, a registry name conflict) was only an app-log warning — so the audit file could not answer "who tried what and was turned away", which is the first question a security reviewer asks. Refusals now write `{"event":"denied","action":…,"reason":…}` alongside the existing `event: "query"` lines, across REST and MCP. Covers `auth`, `query`, `register`, `mcp_auth`, and `mcp_query` actions. Existing `event: "query"` lines are unchanged, so current log consumers keep working — filter on `.event` to separate the two. The raw API key is still never written; denials record the same 6-character `key_hint`. 11 new tests.

### Security

- **Dependency refresh** (`uv lock --upgrade`) clears the last open Dependabot alert: `msgpack` 1.1.2 → 1.2.1 (GHSA-6v7p-g79w-8964, high). Also refreshes the rest of the locked tree to latest compatible (fastapi, uvicorn, pydantic, duckdb, typer, structlog, etc.). `pip-audit -r requirements.txt` reports no known vulnerabilities; full test suite passes.

### Documentation

- **README expanded** with: the audit-log NDJSON schema (field-by-field, with a `jq` example); a worked MCP `tools/call` example plus natural-language prompts for querying from VS Code Copilot / Claude Desktop / Cursor; a REST API reference table of all `/v1` endpoints; a Troubleshooting section mapping each HTTP/JSON-RPC status (`401`/`403`/`404`/`409`/`400`/`503`, MCP `-32001`) to its cause and fix; and a corrected read-only SQL note listing the full blocked-keyword set (was understated as four). Docs-only — no behaviour change.

### Fixed

- **The startup log reported a stale version.** `src/tdb/main.py` still hardcoded `"0.4.2"` in its `tdb_startup version=…` line, missed by the single-source-of-truth refactor below — so on this release the banner would have announced the previous version while every other surface reported the new one. It now uses `__version__` like the rest.

### Changed

- **Version is now defined once** in `src/tdb/__init__.py` (`__version__`) and imported by the FastAPI app version, the `/` banner, and the MCP `serverInfo`. Previously the version string was hardcoded in three places, which risks drift on release.
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
