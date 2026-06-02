# TDB Community — Manual Testing Guide

**Version:** 0.4.2
**Last updated:** 2026-06-02

This document describes how to manually verify TDB end-to-end after code changes.
It complements the automated test suite (`pytest tests/`) with scenarios that
require a live server, a real CSV file, and optional Docker.

---

## Prerequisites

```bash
# 1. Sample CSV (used in all tests below)
cat > /tmp/sales.csv << 'EOF'
id,product,amount,country
1,Widget A,100.00,IN
2,Widget B,200.50,US
3,Widget C,50.00,IN
4,Widget D,300.00,GB
5,Widget E,75.00,IN
EOF

# 2. Start the server (separate terminal)
TDB_API_KEYS=manual-test-key uv run tdb serve

# 3. Set convenience vars for curl commands
export KEY="manual-test-key"
export BASE="http://localhost:8000"
```

---

## MT-01 — Server Startup

| # | Step | Expected |
|---|---|---|
| 1 | Run `TDB_API_KEYS=manual-test-key uv run tdb serve` | Server starts on port 8000, no errors |
| 2 | `curl $BASE/health` | `{"status": "ok"}` |
| 3 | `curl $BASE/` | JSON with `"product": "The Data-Bridge"` and `"version": "0.4.2"` |
| 4 | Open `http://localhost:8000/docs` in browser | Swagger UI loads with all endpoints visible |
| 5 | Check logs — no deprecation warnings in terminal output | No `on_event is deprecated` warning |

---

## MT-02 — API Key Authentication

| # | Step | Expected |
|---|---|---|
| 1 | `curl $BASE/v1/sources` | `401 Unauthorized` |
| 2 | `curl $BASE/v1/sources -H "Authorization: Bearer wrong-key"` | `401 Unauthorized` |
| 3 | `curl $BASE/v1/sources -H "Authorization: Bearer $KEY"` | `200 OK`, empty list `[]` |
| 4 | `curl $BASE/health` (no header) | `200 OK` — health check is public |

---

## MT-03 — Source Registration

```bash
# Register the CSV source
curl -s -X POST $BASE/v1/sources \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"sales","source_type":"csv","connection":{"file_path":"/tmp/sales.csv"}}' \
  | python3 -m json.tool
```

| # | Check | Expected |
|---|---|---|
| 1 | HTTP status | `201 Created` |
| 2 | Response has `id` field | Non-empty UUID string |
| 3 | Response `name` == `"sales"` | Pass |
| 4 | `curl $BASE/v1/sources -H "Authorization: Bearer $KEY"` | List contains 1 source |

**Community limit — second registration must be rejected:**

```bash
curl -s -X POST $BASE/v1/sources \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"sales2","source_type":"csv","connection":{"file_path":"/tmp/sales.csv"}}' \
  | python3 -m json.tool
```

| # | Check | Expected |
|---|---|---|
| 5 | HTTP status | `409 Conflict` |
| 6 | Error message mentions "one registered source" | Pass |

---

## MT-04 — Registry Persistence (SQLite Survives Restart)

```bash
# Note the source ID from MT-03, then stop the server (Ctrl+C) and restart:
TDB_API_KEYS=manual-test-key uv run tdb serve

# After restart:
curl $BASE/v1/sources -H "Authorization: Bearer $KEY"
```

| # | Check | Expected |
|---|---|---|
| 1 | Source list is non-empty after restart | The `sales` source is still registered |
| 2 | Source ID is unchanged | Same UUID as before restart |

---

## MT-05 — Schema Inspection

```bash
# Replace <SOURCE_ID> with the ID from MT-03
export SID=$(curl -s $BASE/v1/sources -H "Authorization: Bearer $KEY" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")

curl -s $BASE/v1/sources/$SID/schema \
  -H "Authorization: Bearer $KEY" \
  | python3 -m json.tool
```

| # | Check | Expected |
|---|---|---|
| 1 | HTTP status | `200 OK` |
| 2 | `columns` array is non-empty | 4 columns: `id`, `product`, `amount`, `country` |
| 3 | Each column has `name` and `type` fields | Pass |
| 4 | `curl $BASE/v1/sources/does-not-exist/schema -H "Authorization: Bearer $KEY"` | `404 Not Found` |
| 5 | Schema endpoint without auth | `401 Unauthorized` |

---

## MT-06 — SQL Query

```bash
# Basic SELECT
curl -s -X POST $BASE/v1/query \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d "{\"source_id\":\"$SID\",\"sql\":\"SELECT * FROM data\",\"limit\":10}" \
  | python3 -m json.tool
```

| # | Check | Expected |
|---|---|---|
| 1 | HTTP status | `200 OK` |
| 2 | `rows_returned` == 5 | Pass |
| 3 | `columns` includes `id`, `product`, `amount`, `country` | Pass |

```bash
# Filtered query
curl -s -X POST $BASE/v1/query \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d "{\"source_id\":\"$SID\",\"sql\":\"SELECT * FROM data WHERE country = 'IN'\",\"limit\":10}" \
  | python3 -m json.tool
```

| # | Check | Expected |
|---|---|---|
| 4 | `rows_returned` == 3 | Only IN rows |
| 5 | All returned rows have `country == "IN"` | Pass |

```bash
# Write SQL must be rejected
curl -s -X POST $BASE/v1/query \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d "{\"source_id\":\"$SID\",\"sql\":\"DELETE FROM data\",\"limit\":10}" \
  | python3 -m json.tool
```

| # | Check | Expected |
|---|---|---|
| 6 | HTTP status | `400 Bad Request` |

```bash
# Row limit cap
curl -s -X POST $BASE/v1/query \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d "{\"source_id\":\"$SID\",\"sql\":\"SELECT * FROM data\",\"limit\":1001}" \
  | python3 -m json.tool
```

| # | Check | Expected |
|---|---|---|
| 7 | HTTP status | `422 Unprocessable Entity` (Pydantic rejects limit > 1000) |

---

## MT-07 — MCP Endpoint (JSON-RPC 2.0)

```bash
# initialize — must work WITHOUT auth
curl -s -X POST $BASE/v1/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  | python3 -m json.tool
```

| # | Check | Expected |
|---|---|---|
| 1 | HTTP status | `200 OK` |
| 2 | `result.serverInfo.name` == `"tdb-community"` | Pass |
| 3 | `result.protocolVersion` == `"2024-11-05"` | Pass |

```bash
# tools/list — requires auth
curl -s -X POST $BASE/v1/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $KEY" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | python3 -m json.tool
```

| # | Check | Expected |
|---|---|---|
| 4 | `result.tools` has exactly 1 item | `query_source` |
| 5 | `tools/list` without auth key | `error.code == -32001` (Unauthorized) |

```bash
# tools/call — query through MCP
curl -s -X POST $BASE/v1/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $KEY" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"query_source","arguments":{"sql":"SELECT * FROM data WHERE country = '\''IN'\'' LIMIT 5"}}}' \
  | python3 -m json.tool
```

| # | Check | Expected |
|---|---|---|
| 6 | Response has `result.content[0].text` | JSON string |
| 7 | Parsed text contains `rows_returned` and `columns` | Pass |
| 8 | `isError` is absent or `false` | Pass |

```bash
# tools/call with blocked SQL
curl -s -X POST $BASE/v1/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $KEY" \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"query_source","arguments":{"sql":"DROP TABLE data"}}}' \
  | python3 -m json.tool
```

| # | Check | Expected |
|---|---|---|
| 9 | `result.isError` == `true` | Pass |
| 10 | Error text mentions "SQL validation" | Pass |

---

## MT-08 — CLI

```bash
# In a separate terminal with the server running:
export TDB_API_KEYS=manual-test-key
export TDB_URL=http://localhost:8000

# Delete the existing source first (Community Edition: one source at a time)
SID=$(curl -s $TDB_URL/v1/sources -H "Authorization: Bearer $TDB_API_KEYS" | python3 -c "import sys, json; rows=json.load(sys.stdin); print(rows[0]['id'] if rows else '')")
[ -n "$SID" ] && curl -s -X DELETE $TDB_URL/v1/sources/$SID -H "Authorization: Bearer $TDB_API_KEYS"

uv run tdb register /tmp/sales.csv --name sales-cli
```

| # | Check | Expected |
|---|---|---|
| 1 | Command prints source ID | Non-empty UUID |
| 2 | `uv run tdb query "SELECT * FROM data LIMIT 3"` | Table with 3 rows printed |
| 3 | `uv run tdb query "SELECT * FROM data" --output json` | JSON output |
| 4 | `uv run tdb query "SELECT * FROM data" --output csv` | CSV output |
| 5 | `uv run tdb register /nonexistent.csv --name bad` | Error: file not found |

---

## MT-09 — Audit Log

```bash
# Default location is ./tdb_audit.jsonl (in the server's working directory).
# If you set TDB_LOG_FILE, tail that path instead.
tail -f "${TDB_LOG_FILE:-tdb_audit.jsonl}"
# Then run any query in another terminal
```

| # | Check | Expected |
|---|---|---|
| 1 | A NDJSON line appears after each query | Line is valid JSON |
| 2 | Log line contains `source_id`, timestamp, SQL | Pass |

---

## MT-10 — Docker (requires Docker daemon)

```bash
docker compose build
docker compose up -d
curl http://localhost:8000/health

# Register a source (CSV must be in ./data/ which is mounted read-only)
cp /tmp/sales.csv ./data/sales.csv
curl -X POST http://localhost:8000/v1/sources \
  -H "Authorization: Bearer dev-insecure-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"name":"sales","source_type":"csv","connection":{"file_path":"/data/sales.csv"}}'

# Restart and verify persistence
docker compose restart
curl http://localhost:8000/v1/sources -H "Authorization: Bearer dev-insecure-key-change-me"
```

| # | Check | Expected |
|---|---|---|
| 1 | `docker compose build` succeeds | No error |
| 2 | `/health` returns 200 after `up -d` | Pass |
| 3 | Source survives `docker compose restart` | Same source ID in list |

> **Status:** Not yet verified — Docker daemon not available in WSL2 without extra setup.

---

## Regression Checklist (run after any code change)

```bash
uv run python -m pytest tests/ -v        # must show 0 failures
uv run ruff check src/ tests/            # must show 0 errors
```

All manual test cases in MT-01 through MT-07 should be re-run when changes touch:
- `src/tdb/main.py` → re-run MT-01, MT-02
- `src/tdb/registry/` → re-run MT-03, MT-04
- `src/tdb/connectors/csv.py` → re-run MT-05, MT-06
- `src/tdb/routers/mcp.py` → re-run MT-07
- `src/tdb/cli/` → re-run MT-08
- `src/tdb/audit/` → re-run MT-09
