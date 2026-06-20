# The Data-Bridge (TDB) — Community Edition

> Turn any CSV file into a clean, governed, queryable API — with an audit log on every request.

[![License: AGPL v3](https://img.shields.io/badge/license-AGPLv3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-green.svg)](https://python.org)
[![CI](https://github.com/tdb-project/tdb-community/actions/workflows/ci.yml/badge.svg)](https://github.com/tdb-project/tdb-community/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/docker-ghcr.io-blue.svg)](https://github.com/tdb-project/tdb-community/pkgs/container/tdb-community)

---

## What Is TDB?

TDB is a self-hosted middleware that sits in front of your data and exposes it through a
standard REST and MCP API. You register a data source once; TDB gives it a clean SQL
interface, auto-detects the schema, enforces read-only access, and logs every query to
an audit file. AI tools like Claude and Cursor connect to the MCP endpoint directly —
no custom integration required.

**Community edition:** one CSV file, one API key, unlimited queries, full audit log.
[Enterprise edition](https://docs.tdb.jiracorp.co.in/pricing/) adds PostgreSQL, multi-source, OAuth 2.1, RBAC, and SOC 2-ready audit export.


---

## Quickstart — Docker (Windows, macOS, Linux)

The image is published to GHCR — no `git clone`, no build step, no Python required. You only
need [Docker Desktop](https://www.docker.com/products/docker-desktop/). Put your CSV in a
`data/` folder next to where you run the commands below.

> **Windows: `curl.exe` for the GET, `Invoke-RestMethod` for JSON POSTs.** In PowerShell,
> `curl` is an alias for `Invoke-WebRequest`, so type `curl.exe` explicitly for the Step 1
> health check to get the real curl. For the POST requests that send a JSON body (Steps 2–3),
> PowerShell mangles quoted JSON when handing it to `curl.exe` (the spaces in your SQL get
> split into separate arguments), so the Windows snippets use the native `Invoke-RestMethod`
> cmdlet instead — it's the reliable approach across PowerShell 5.1 and 7.x.

### Step 1 — Run it (detached)

**macOS / Linux:**
```bash
export TDB_API_KEYS=$(python3 -c "import secrets; print(secrets.token_hex(32))")

docker run -d --rm --name tdb -p 8000:8000 \
  -e TDB_API_KEYS \
  -v "$PWD/data:/data:ro" \
  ghcr.io/tdb-project/tdb-community:latest
```

**Windows (PowerShell):**
```powershell
$env:TDB_API_KEYS = (python3 -c "import secrets; print(secrets.token_hex(32))")

docker run -d --rm --name tdb -p 8000:8000 `
  -e TDB_API_KEYS `
  -v "${PWD}\data:/data:ro" `
  ghcr.io/tdb-project/tdb-community:latest
```

`-d` runs TDB **detached** so this same terminal stays free for the next steps — and the
`TDB_API_KEYS` you just generated stays available to them. Naming the container `tdb` lets
the later commands refer to it by name.

> **Image tags.** `:latest` always points to the newest **stable release** (not the tip of
> `main`). For production, pin an immutable release tag like `:0.4.2` (or a `@sha256:` digest);
> `:0.4` floats to the newest patch within a minor. Want unreleased changes? Pull `:edge`,
> which tracks the latest `main` build.
Verify it's up:

```bash
curl http://localhost:8000/health
# → {"status": "ok"}
```

Then open [http://localhost:8000/docs](http://localhost:8000/docs) for the Swagger UI, and
follow logs any time with `docker logs -f tdb`.

> **Want data to survive a restart?** The `--rm` run above is ephemeral. For a persistent
> setup, grab the [`docker-compose.yml`](docker-compose.yml) from this repo (it mounts named
> volumes for the registry and audit log), set `TDB_API_KEYS` in a `.env` file (copy
> `.env.example`), and run `docker compose up -d` instead of the `docker run` above — Steps
> 2–5 are identical. See [Configuration](#configuration) for the variables it reads.

### Step 2 — Register your CSV

**macOS / Linux:**
```bash
curl -X POST http://localhost:8000/v1/sources \
  -H "Authorization: Bearer $TDB_API_KEYS" \
  -H "Content-Type: application/json" \
  -d '{"name":"mydata","source_type":"csv","connection":{"file_path":"/data/your_file.csv"}}'
```

**Windows (PowerShell):**
```powershell
$body = @{
  name        = "mydata"
  source_type = "csv"
  connection  = @{ file_path = "/data/your_file.csv" }
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/sources `
  -Headers @{ Authorization = "Bearer $env:TDB_API_KEYS" } `
  -ContentType "application/json" -Body $body
```

Fill in the two placeholders:

- **`name`** (`mydata` above) — any label you choose for this source.
- **`file_path`** (`/data/your_file.csv` above) — the path to *your* CSV **inside the
  container**. Replace `your_file.csv` with your file's name; it must live in the `data/`
  folder you mounted in Step 1, so `/data/<your-file>.csv`.

Column names and types are auto-detected from the CSV header row, so you don't declare a
schema. The response includes the new source's `id` — copy it for Step 3.

> **The table is always named `data`.** Whatever your file or source is called, TDB exposes
> its rows as a single table named `data`. That fixed name — not your filename — is what you
> put in the `FROM` clause in Step 3.

### Step 3 — Query it (REST)

**macOS / Linux:**
```bash
curl -X POST http://localhost:8000/v1/query \
  -H "Authorization: Bearer $TDB_API_KEYS" \
  -H "Content-Type: application/json" \
  -d '{"source_id":"<id-from-step-2>","sql":"SELECT * FROM data","limit":10}'
```

**Windows (PowerShell):**
```powershell
$body = @{
  source_id = "<id-from-step-2>"
  sql       = "SELECT * FROM data"
  limit     = 10
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/query `
  -Headers @{ Authorization = "Bearer $env:TDB_API_KEYS" } `
  -ContentType "application/json" -Body $body
```

`SELECT * FROM data` works on any CSV — swap it for any read-only query you like. The
**column names are exactly your CSV's header row** (run `SELECT * FROM data` once to see
them). The optional `limit` field caps the response: it defaults to **100** and is hard-capped
at **1,000**, so a bare `SELECT *` returns at most those rows regardless of your SQL.

**Read-only is enforced.** A statement must start with `SELECT`, and any of these
keywords — `INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`, `ALTER`, `TRUNCATE`,
`REPLACE`, `MERGE` (case-insensitive) — is rejected with a `400`. Aggregations,
`WHERE`, `JOIN` (the single table is `data`), `GROUP BY`, and `ORDER BY` are all fine.

### Step 4 — Connect an AI tool (MCP)

TDB exposes a standard [MCP](https://modelcontextprotocol.io) endpoint at `/v1/mcp`. The
Community Edition ships one tool, `query_source`. See
[Connecting an MCP Client](#connecting-an-mcp-client) below for Claude Desktop, Cursor, and
VS Code configuration.

### Step 5 — Check the audit log

Every query — REST or from an AI tool — is appended to a local NDJSON file inside the container:

**macOS / Linux:**
```bash
docker exec tdb cat /app/tdb_audit.jsonl | tail -3
```

**Windows (PowerShell):**
```powershell
docker exec tdb cat /app/tdb_audit.jsonl | Select-Object -Last 3
```

Each line is a self-contained JSON object — one query, one line:

```json
{"event":"query","source_id":"3f9c2a7e-…","sql":"SELECT * FROM data LIMIT 10","rows_returned":10,"key_hint":"a1b2c3…","ts":"2026-06-20T12:34:56.789012+00:00"}
```

| Field | Meaning |
|---|---|
| `event` | Always `"query"` in this edition. |
| `source_id` | The registered source the query ran against. |
| `sql` | The exact SQL submitted (REST or via the MCP tool). |
| `rows_returned` | Row count in the response (after the 1,000-row cap). |
| `key_hint` | First 6 characters of the API key used, then `…`. **The raw key is never written** — this is only enough to tell two keys apart. |
| `ts` | UTC timestamp, ISO-8601 with a `+00:00` offset so SIEM tools and log parsers place it correctly. |

Because it's newline-delimited JSON, you can stream it straight into `jq`, Loki, or any
log pipeline — e.g. `docker exec tdb cat /app/tdb_audit.jsonl | jq 'select(.rows_returned > 100)'`.

### Stopping TDB

When you're done, stop the container from the same terminal:

```bash
docker stop tdb
```

Because it was started with `--rm`, stopping also **removes** it — the ephemeral registry and
audit log are discarded. For a setup that survives restarts, use the Compose option noted in
Step 1.

---

## Run from source (optional — contributors / no Docker)

**Most users should use the Docker quickstart above.** This path is for contributors,
or for running without Docker. Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
# 1. Clone and install
git clone https://github.com/tdb-project/tdb-community
cd tdb-community
pip install uv
uv sync

# 2. Start the server
# macOS / Linux:
TDB_API_KEYS=my-dev-key uv run tdb serve

# Windows (PowerShell):
# $env:TDB_API_KEYS="my-dev-key"; uv run tdb serve

# → Starting TDB on http://127.0.0.1:8000

# 3. Register a CSV
uv run tdb register /path/to/your/file.csv --name mydata
# → Source registered.  ID: <uuid>  Name: mydata

# 4. Query it
uv run tdb query "SELECT * FROM data LIMIT 10"

# 5. JSON or CSV output (column names are your CSV's header row)
uv run tdb query "SELECT * FROM data" --output json
uv run tdb query "SELECT * FROM data" --output csv
```

---

## Connecting an MCP Client

TDB exposes a standard [MCP](https://modelcontextprotocol.io) endpoint at `/v1/mcp` using the
**Streamable HTTP** transport. Any MCP-compatible client can connect — no local proxy or extra
process required.

**Verify the endpoint is reachable:**

**macOS / Linux:**
```bash
curl -s -X POST http://localhost:8000/v1/mcp \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}'
```

**Windows (PowerShell):**
```powershell
$body = @{
  jsonrpc = "2.0"
  id      = 1
  method  = "initialize"
  params  = @{ protocolVersion = "2024-11-05" }
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/mcp `
  -Headers @{ Authorization = "Bearer YOUR_API_KEY" } `
  -ContentType "application/json" -Body $body
```

A successful response returns a JSON-RPC result with `serverInfo` and `capabilities`.

---

### VS Code (Copilot / MCP extension)

Open or create `%APPDATA%\Code\User\mcp.json` (Windows) or `~/.config/Code/User/mcp.json` (Mac/Linux):

```json
{
  "servers": {
    "tdb": {
      "type": "http",
      "url": "http://localhost:8000/v1/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  }
}
```

Save the file, then open the Command Palette (`Ctrl+Shift+P`) and run **MCP: List Servers** to confirm `tdb` shows as connected.

> **Note:** Do not use `type: "stdio"` with a `curl` command — curl is a one-shot tool, not a
> persistent process, and will produce an `ENOENT` or immediate disconnect error.

---

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (Mac) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "tdb": {
      "type": "http",
      "url": "http://localhost:8000/v1/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  }
}
```

Restart Claude Desktop. The `query_source` tool will appear in the tools list.

---

### Cursor

Open **Cursor Settings → MCP** and add a new server, or edit `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "tdb": {
      "type": "http",
      "url": "http://localhost:8000/v1/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  }
}
```

---

### Available MCP Tool

The single MCP tool exposed is `query_source`. It accepts:
- `sql` (required) — a SQL `SELECT` statement; use `data` as the table name.
- `source_name` (optional) — the registered source to query. Defaults to the only registered source (Community Edition has just one).

Returns rows as JSON inside a JSON-RPC `tools/call` result envelope.

#### Calling the tool directly

You rarely call this by hand — your AI assistant does it for you — but it's useful for
testing the endpoint. Invoke `query_source` with a `tools/call` request:

**macOS / Linux:**
```bash
curl -s -X POST http://localhost:8000/v1/mcp \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"query_source","arguments":{"sql":"SELECT * FROM data LIMIT 5"}}}'
```

**Windows (PowerShell):**
```powershell
$body = @{
  jsonrpc = "2.0"
  id      = 2
  method  = "tools/call"
  params  = @{ name = "query_source"; arguments = @{ sql = "SELECT * FROM data LIMIT 5" } }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/mcp `
  -Headers @{ Authorization = "Bearer YOUR_API_KEY" } `
  -ContentType "application/json" -Body $body
```

The result envelope wraps the rows as JSON text — the inner payload carries
`source`, `columns`, `rows`, `rows_returned`, and `truncated` (true when the
1,000-row cap dropped rows):

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      { "type": "text", "text": "{\"source\": \"mydata\", \"columns\": [...], \"rows\": [...], \"rows_returned\": 5, \"truncated\": false}" }
    ]
  }
}
```

#### Querying from an AI assistant

Once the server is configured in your client (see above), you don't write SQL — you ask
in plain language and the assistant calls `query_source` for you, writing the SQL against
the `data` table. After connecting in **VS Code (Copilot)**, **Claude Desktop**, or
**Cursor**, try prompts like:

- *"Using the tdb `query_source` tool, how many rows are in my data?"*
- *"Show me the first 5 rows."*
- *"What columns does the dataset have, and what does a typical row look like?"*
- *"List the top 10 customers by revenue, highest first."*
- *"How many records have country = 'IN'?"*

TDB enforces read-only access and caps every response at 1,000 rows, so the assistant
can explore freely without being able to modify your data. If a result was capped, the
`truncated` flag tells the model to narrow the query (add a `WHERE` or aggregate instead
of `SELECT *`).

> **Tip (VS Code):** if the assistant doesn't reach for the tool, name it explicitly —
> "use the **tdb** server's `query_source` tool" — or confirm it's connected via
> **MCP: List Servers** in the Command Palette.

---

## What's Included (v0.4.2)

| Feature | Details |
|---|---|
| CSV data source | One registered source at a time |
| Auto schema detection | Column names and types inferred from CSV |
| SQL query endpoint | `SELECT` only — `DELETE`, `UPDATE`, `DROP` are blocked |
| Row limit | Max 1,000 rows per response |
| API key auth | `Authorization: Bearer <key>` — single static key |
| MCP server | One tool: `query_source` — compatible with Claude, Cursor, Continue |
| Audit log | Every query logged to NDJSON file (`TDB_LOG_FILE`) |
| CLI | `tdb serve`, `tdb register`, `tdb query` |
| Docker Compose | One-command install on Windows, macOS, and Linux |
| OpenAPI / Swagger | Auto-generated at `/docs` |

---

## Community vs Enterprise

| Feature | Community (this repo) | Enterprise |
|---|---|---|
| CSV connector | One source at a time | Unlimited sources |
| PostgreSQL / SQL Server / Snowflake | — | All connectors |
| API key auth | Single static key | Key rotation + management UI |
| OAuth 2.1 / PKCE (for Claude, Cursor) | — | Included |
| SSO / SAML / SCIM | — | Included |
| MCP tools | `query_source` only | Schema, preview, filter, aggregate |
| Audit log | Local NDJSON file | SIEM export (Splunk, Datadog, S3) |
| Immutable signed audit records (SOC 2) | — | Included |
| RBAC / column-level / row-level access | — | Included |
| PII detection and masking | — | Included |
| Admin Web UI | — | Included |
| Prometheus metrics | — | Included |

See the [full edition comparison](https://docs.tdb.jiracorp.co.in/pricing/) — email <hello@tdb.jiracorp.co.in> for pricing and a free 30-day evaluation.

---

## REST API reference

All endpoints are served under `/v1` and require `Authorization: Bearer <key>` unless
noted. The live, auto-generated request/response schemas are at
[`/docs`](http://localhost:8000/docs) (Swagger UI) — this table is the at-a-glance map.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/sources` | ✅ | Register a CSV source (`201`; `409` if one is already registered) |
| `GET` | `/v1/sources` | ✅ | List registered sources |
| `GET` | `/v1/sources/{id}` | ✅ | Get one source's full detail |
| `DELETE` | `/v1/sources/{id}` | ✅ | Remove a source (`204`) |
| `GET` | `/v1/sources/{id}/schema` | ✅ | Column names and inferred types — no rows |
| `POST` | `/v1/query` | ✅ | Run a read-only `SELECT` (max 1,000 rows) |
| `POST` | `/v1/mcp` | ✅ * | MCP JSON-RPC endpoint (`initialize`, `tools/list`, `tools/call`) |
| `GET` | `/health` | — | Liveness probe → `{"status": "ok"}` |
| `GET` | `/` | — | Service banner and version |

\* On `/v1/mcp`, the `initialize` handshake is unauthenticated so clients can discover the
server; `tools/list` and `tools/call` require the Bearer key.

---

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|---|---|---|
| `TDB_API_KEYS` | — | Required. Comma-separated list of valid API keys |
| `TDB_REGISTRY_DB` | `data/tdb_registry.db` | SQLite registry path |
| `TDB_LOG_FILE` | `tdb_audit.jsonl` | Audit log output path. Default writes to the server's working directory; `.env.example` overrides this to `logs/tdb_audit.jsonl` for Docker. |
| `TDB_LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `TDB_PORT` | `8000` | Server port. Used by both Docker Compose and the local `tdb serve` command. |
| `TDB_URL` | `http://localhost:8000` | Server URL used by the CLI (`tdb register`, `tdb query`). Set this if the server runs on a non-default port or remote host. |
| `TDB_DATA_DIR` | `./data` | Docker Compose only. Host directory mounted read-only into the container at `/data`; register CSVs using `file_path: /data/your_file.csv`. |
| `TDB_ALLOWED_DATA_DIR` | _(unset)_ | Security. When set, registered CSV `file_path` values must resolve to a path inside this directory (symlinks and `..` are resolved first); anything outside is rejected with `403`. Unset means no restriction. The Docker image defaults this to `/data`, so the bundled deployment is confined out of the box. For a bare `tdb serve`, set it to the folder holding your CSVs. |

Copy `.env.example` to `.env` and edit before first run.

---

## Development

Contributing? See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow,
branch strategy, and PR checklist. The common commands:

```bash
# Install dev dependencies
uv sync --extra dev

# Run tests
uv run pytest

# Lint
uv run ruff check src/ tests/

# Format check
uv run ruff format --check src/ tests/
```

---

## Troubleshooting

When a request fails, the HTTP status (or JSON-RPC error code) tells you what went wrong:

| Status | What it means | Likely cause & fix |
|---|---|---|
| `401` Unauthorized | Bad or missing API key | The `Authorization: Bearer <key>` header doesn't match a value in `TDB_API_KEYS`. Over MCP this surfaces as JSON-RPC error `-32001`. Check the key and that the env var is set on the server. |
| `403` Forbidden | CSV path outside the allowed directory | `TDB_ALLOWED_DATA_DIR` is set and the `file_path` resolves (symlinks / `..` expanded) outside it. Move the CSV inside that directory, or adjust the variable. In Docker it defaults to `/data`, so register paths as `/data/<file>.csv`. |
| `404` Not Found | Unknown `source_id` | The id doesn't match a registered source. Re-list with `GET /v1/sources` and copy the current `id` (it changes when you re-register). |
| `409` Conflict | A source is already registered | Community Edition allows **one source at a time**. `DELETE /v1/sources/{id}` the existing one first, or upgrade to Enterprise for unlimited sources. |
| `400` Bad Request | Rejected SQL or bad registration | Either the SQL isn't a plain `SELECT` / contains a blocked keyword, or the CSV `file_path` is missing/unreadable at registration time. |
| `503` Service Unavailable | Source file is gone | The CSV was moved or deleted **after** registration. Restore the file at its registered path, or delete and re-register the source. |

**MCP-specific gotchas:**

- **`type: "stdio"` with a `curl` command won't work.** TDB speaks Streamable HTTP — use
  `"type": "http"` with the `/v1/mcp` URL (see [Connecting an MCP Client](#connecting-an-mcp-client)). A stdio + curl config produces an immediate `ENOENT`/disconnect.
- **Handshake works but tools don't.** `initialize` is intentionally unauthenticated, so a
  reachable server can still reject `tools/list` / `tools/call` with `-32001` if the Bearer
  key is wrong or missing. Fix the `Authorization` header in your client config.

Still stuck? Turn up detail with `TDB_LOG_LEVEL=DEBUG`, follow logs via `docker logs -f tdb`,
and check the [audit log](#step-5--check-the-audit-log) to see exactly what SQL reached the server.

---

## License

[AGPLv3](LICENSE) — free to use, modify, and self-host.
If you distribute a modified version as a network service, you must release your changes under the same license.

Enterprise features and a commercial license (no AGPL obligations) are described on the [editions page](https://docs.tdb.jiracorp.co.in/pricing/).
