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
[Enterprise edition](https://tdb.jiracorp.co.in) adds PostgreSQL, multi-source, OAuth 2.1, RBAC, and SOC 2-ready audit export.

---

## Quickstart — Docker (Windows, macOS, Linux)

The image is published to GHCR. You only need [Docker Desktop](https://www.docker.com/products/docker-desktop/) — no `git clone`, no build step, no Python required.

### Try it now (ephemeral — data gone on stop)

**macOS / Linux:**
```bash
docker run --rm -p 8000:8000 \
  -e TDB_API_KEYS=my-dev-key \
  -v /path/to/your/csvs:/data:ro \
  ghcr.io/tdb-project/tdb-community:latest
```

**Windows (PowerShell):**
```powershell
docker run --rm -p 8000:8000 `
  -e TDB_API_KEYS=my-dev-key `
  -v C:\path\to\your\csvs:/data:ro `
  ghcr.io/tdb-project/tdb-community:latest
```

Open [http://localhost:8000/docs](http://localhost:8000/docs). The container is deleted when stopped.

### Persistent setup — Docker Compose

**Step 1 — get the compose file** (works on all platforms):

```bash
# macOS / Linux
mkdir tdb && cd tdb
curl -fsSL https://raw.githubusercontent.com/tdb-project/tdb-community/main/docker-compose.yml \
  -o docker-compose.yml
```

```powershell
# Windows (PowerShell)
mkdir tdb; cd tdb
curl -fsSL https://raw.githubusercontent.com/tdb-project/tdb-community/main/docker-compose.yml `
  -o docker-compose.yml
```

**Step 2 — configure your API key** (pick the method for your OS):

```bash
# macOS / Linux — generate and set in the current shell
export TDB_API_KEYS=$(python3 -c "import secrets; print(secrets.token_hex(32))")
```

```powershell
# Windows (PowerShell) — generate and set in the current shell
$env:TDB_API_KEYS = (python3 -c "import secrets; print(secrets.token_hex(32))")
```

```ini
# All platforms — recommended for a permanent setup:
# Copy .env.example to .env and set your key there.
# Docker Compose loads .env automatically.
TDB_API_KEYS=your-strong-key-here
```

**Step 3 — add your CSV, start, and query:**

```bash
# Create the data directory and copy your CSV into it
# macOS/Linux:  mkdir -p data && cp /path/to/file.csv data/
# Windows:      mkdir data; copy C:\path\to\file.csv data\

# Start (pulls pre-built image — no build step)
docker compose up -d

# Verify
curl http://localhost:8000/health
# → {"status": "ok"}

# Register your CSV (replace $TDB_API_KEYS with your key if not set in the shell)
curl -X POST http://localhost:8000/v1/sources \
  -H "Authorization: Bearer $TDB_API_KEYS" \
  -H "Content-Type: application/json" \
  -d '{"name":"mydata","source_type":"csv","connection":{"file_path":"/data/your_file.csv"}}'

# Query it
curl -X POST http://localhost:8000/v1/query \
  -H "Authorization: Bearer $TDB_API_KEYS" \
  -H "Content-Type: application/json" \
  -d '{"source_id":"<id-from-register>","sql":"SELECT * FROM data LIMIT 10"}'
```

> **Note:** if `TDB_API_KEYS` is not set, the compose file falls back to a known-insecure default key and prints a startup warning. Always set your own key before exposing TDB to a network.

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

# 5. JSON or CSV output
uv run tdb query "SELECT * FROM data WHERE country = 'IN'" --output json
uv run tdb query "SELECT * FROM data" --output csv
```

---

## Connecting an MCP Client

TDB exposes a standard [MCP](https://modelcontextprotocol.io) endpoint at `/v1/mcp` using the
**Streamable HTTP** transport. Any MCP-compatible client can connect — no local proxy or extra
process required.

**Verify the endpoint is reachable:**

```bash
curl -s -X POST http://localhost:8000/v1/mcp \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}'
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

---

## What's Included (v0.4.1)

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
| Docker Compose | One-command install on Linux/Mac |
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

Enterprise pricing starts at $499/month. See [tdb.jiracorp.co.in](https://tdb.jiracorp.co.in).

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

## License

[AGPLv3](LICENSE) — free to use, modify, and self-host.
If you distribute a modified version as a network service, you must release your changes under the same license.

Enterprise features and a commercial license (no AGPL obligations) are available at [tdb.jiracorp.co.in](https://tdb.jiracorp.co.in).
