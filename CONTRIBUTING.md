# Contributing to TDB Community Edition

Thank you for your interest in contributing. This document covers everything you need to get started.

## Before You Start

TDB Community Edition is the open-source (AGPLv3) tier of The Data-Bridge. It is intentionally minimal — enough to fully evaluate the product, not enough to replace the enterprise tier. Before contributing a feature, check the table below.

### Feature boundary

Features in this repo:

| In scope (Community) | Out of scope (Enterprise only) |
|---|---|
| CSV data source | Any connector other than CSV |
| Single static API key auth | JWT, OAuth 2.1, PKCE, SSO, SAML |
| SELECT-only REST query endpoint | Named views, query templates |
| 1,000-row response cap | Pagination beyond 1,000 rows |
| Auto schema detection on CSV | — |
| Single MCP tool: `query_source` | Multiple MCP tools |
| Local NDJSON audit log | SIEM export, signed/immutable audit |
| YAML config + env vars | Admin UI, metrics endpoints |
| Docker Compose deployment | — |
| CLI (`tdb register`, `tdb query`, `tdb serve`) | — |

If your contribution touches anything in the "Out of scope" column, it belongs in `tdb-enterprise` instead.

---

## Development Setup

**Requirements:** Python 3.12+, [uv](https://docs.astral.sh/uv/)

```bash
# Clone and enter the repo
git clone https://github.com/your-org/tdb-community.git
cd tdb-community

# Install all dependencies including dev tools
uv sync --extra dev

# Verify everything works
uv run pytest
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

---

## Branch Strategy

```
main        ← stable only, tagged releases, never commit directly
develop     ← integration branch, all features merge here first
feature/*   ← one branch per feature or fix
release/*   ← release prep only, merges to main when ready
```

Always branch from `develop`:

```bash
git checkout develop
git pull
git checkout -b feature/my-feature
```

Open a PR from `feature/*` → `develop`, not directly to `main`.

---

## Coding Rules

1. **No enterprise features** — see the feature boundary table above
2. **Every new endpoint or MCP tool needs a test** — add it to `tests/`
3. **Every API endpoint must write an audit log entry** — call `log_query()` from `tdb.audit.logger`
4. **Auth header is `Authorization: Bearer <key>`** — no other auth mechanism
5. **All SQL goes through DuckDB** — never raw file reads in endpoints
6. **1,000 row cap** — enforce at the query layer, not just in documentation
7. **MCP exposes exactly one tool: `query_source`** — no additions here
8. **No comments explaining what code does** — only add a comment when the *why* is non-obvious (a hidden constraint, a workaround for a specific bug)
9. **No hardcoded values** — config is environment variables only, read through `src/tdb/config.py`

---

## Running Tests

```bash
# Full suite
uv run pytest

# With coverage
uv run pytest --cov=src/tdb --cov-report=term-missing

# Single file
uv run pytest tests/test_day4.py -v
```

Tests use `tests/conftest.py` which sets `TDB_API_KEYS`, `TDB_REGISTRY_DB`, and `TDB_LOG_FILE` via temp files before any `tdb.*` import. Do not set these env vars manually when running tests.

---

## Linting and Formatting

```bash
# Check (CI does this)
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# Fix automatically
uv run ruff check --fix src/ tests/
uv run ruff format src/ tests/
```

Line length: 88. Python target: 3.12. Rules: E, F, I, N, UP.

---

## PR Checklist

Before opening a pull request:

- [ ] Tests added or updated for every changed endpoint or function
- [ ] `uv run pytest` passes locally
- [ ] `uv run ruff check src/ tests/` passes with no errors
- [ ] `uv run ruff format --check src/ tests/` passes
- [ ] No enterprise features introduced (see feature boundary table)
- [ ] Audit log entry written for any new query-path code
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] PR targets `develop`, not `main`

---

## Releasing & image tags

The published image (`ghcr.io/tdb-project/tdb-community`) is built by
`.github/workflows/publish.yml`, and the tag you get depends on what triggered the build:

| Tag | When it's published | Use it for |
|---|---|---|
| `latest` | A `v*` release tag (newest **stable** release; pre-releases like `v1.0.0-rc1` are skipped) | A quick try of the current release |
| `X.Y.Z` (e.g. `0.4.2`) | A `v*` release tag — **immutable**, never overwritten | **Production** — pin this (or a `@sha256:` digest) |
| `X.Y` (e.g. `0.4`) | A `v*` release tag — floats to the newest patch | Auto-getting patch fixes within a minor |
| `edge` | Every push to `main` | Trying unreleased changes |

`latest` follows **releases, not `main`** — a routine merge to `main` ships only `:edge` and
never moves `:latest`. **Cutting a release** (maintainers): bump the version, move
`CHANGELOG.md` `[Unreleased]` → the new version, then push an annotated `vX.Y.Z` tag on `main`
— the workflow publishes `latest`/`X.Y.Z`/`X.Y` automatically.

---

## Security Issues

Do not open public issues for vulnerabilities. See [SECURITY.md](SECURITY.md) for the responsible disclosure process.
