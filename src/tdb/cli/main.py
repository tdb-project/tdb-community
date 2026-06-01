"""
TDB CLI — Community Edition

Commands:
    tdb serve     Start the TDB REST server
    tdb register  Register a CSV file as a data source
    tdb query     Run a SQL query against the registered source

Environment variables:
    TDB_API_KEYS   Your API key (required for register/query)
    TDB_URL        TDB server URL (default: http://localhost:8000)
    TDB_PORT       Port for 'tdb serve' (default: 8000)
"""

from __future__ import annotations

import csv as csv_module
import io
import json
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from tdb.cli.client import get_base_url, make_client

app = typer.Typer(
    name="tdb",
    help="The Data-Bridge CLI — turn CSV files into queryable REST and MCP APIs.",
    no_args_is_help=True,
)
console = Console()
err = Console(stderr=True)


# ---------------------------------------------------------------------------
# tdb serve
# ---------------------------------------------------------------------------


@app.command()
def serve(
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Bind host (use 0.0.0.0 for network; prefer Docker for production)",
    ),
    port: int = typer.Option(
        int(os.environ.get("TDB_PORT", "8000")), "--port", "-p", help="Bind port"
    ),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload (dev only)"),
) -> None:
    """Start the TDB REST server."""
    try:
        import uvicorn
    except ImportError:
        err.print("[red]uvicorn is not installed. Run: pip install uvicorn[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Starting TDB on http://{host}:{port}[/green]")
    console.print(f"  Docs: http://{host}:{port}/docs")
    console.print(f"  MCP:  http://{host}:{port}/v1/mcp")
    uvicorn.run("tdb.main:app", host=host, port=port, reload=reload)


# ---------------------------------------------------------------------------
# tdb register
# ---------------------------------------------------------------------------


@app.command()
def register(
    file_path: str = typer.Argument(..., help="Absolute path to the CSV file"),
    name: str = typer.Option(..., "--name", "-n", help="Unique source name"),
    description: str = typer.Option(
        "", "--description", "-d", help="Optional description"
    ),
    tags: str | None = typer.Option(None, "--tags", help="Comma-separated tags"),
) -> None:
    """
    Register a CSV file as a TDB data source.

    The TDB server must be running first (use 'tdb serve').

    Example:
        tdb register /data/sales.csv --name sales_q1
    """
    path = Path(file_path)
    if not path.is_file():
        err.print(f"[red]File not found: {file_path}[/red]")
        raise typer.Exit(1)

    if path.suffix.lower() != ".csv":
        err.print(
            f"[yellow]Warning: file does not have .csv extension: {file_path}[/yellow]"
        )

    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]

    payload = {
        "name": name,
        "source_type": "csv",
        "connection": {"file_path": str(path.resolve())},
        "description": description,
        "tags": tag_list,
    }

    try:
        with make_client() as client:
            response = client.post("/v1/sources", json=payload)
    except RuntimeError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    except Exception as exc:
        err.print(f"[red]Could not connect to TDB at {get_base_url()}[/red]")
        err.print(f"[red]{exc}[/red]")
        err.print(
            "[yellow]Is the server running? Use 'tdb serve' to start it.[/yellow]"
        )
        raise typer.Exit(1)

    if response.status_code == 201:
        data = response.json()
        console.print("[green]Source registered.[/green]")
        console.print(f"  ID:   [bold]{data['id']}[/bold]")
        console.print(f"  Name: {data['name']}")
        console.print(f"  File: {data['connection']['file_path']}")
    elif response.status_code == 401:
        err.print("[red]Authentication failed. Check TDB_API_KEYS.[/red]")
        raise typer.Exit(1)
    else:
        detail = response.json().get("detail", response.text)
        err.print(f"[red]Registration failed ({response.status_code}): {detail}[/red]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# tdb query
# ---------------------------------------------------------------------------


@app.command()
def query(
    sql: str = typer.Argument(
        ..., help="SQL SELECT statement. Use 'data' as the table name."
    ),
    limit: int = typer.Option(100, "--limit", "-l", help="Max rows (max 1000)"),
    output: str = typer.Option(
        "table", "--output", "-o", help="Output format: table | json | csv"
    ),
) -> None:
    """
    Run a SQL query against the registered data source.

    The TDB server must be running first (use 'tdb serve').

    Examples:
        tdb query "SELECT * FROM data LIMIT 10"
        tdb query "SELECT country, COUNT(*) FROM data GROUP BY country" --output json
    """
    try:
        with make_client() as client:
            sources_resp = client.get("/v1/sources")
    except RuntimeError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    except Exception as exc:
        err.print(f"[red]Could not connect to TDB at {get_base_url()}: {exc}[/red]")
        raise typer.Exit(1)

    if sources_resp.status_code != 200:
        err.print(f"[red]Error listing sources: {sources_resp.text}[/red]")
        raise typer.Exit(1)

    sources = sources_resp.json()
    if not sources:
        err.print("[red]No sources registered. Use 'tdb register' first.[/red]")
        raise typer.Exit(1)

    source_id = sources[0]["id"]
    capped_limit = min(limit, 1000)

    try:
        with make_client() as client:
            response = client.post(
                "/v1/query",
                json={"source_id": source_id, "sql": sql, "limit": capped_limit},
            )
    except Exception as exc:
        err.print(f"[red]Query request failed: {exc}[/red]")
        raise typer.Exit(1)

    if response.status_code != 200:
        detail = response.json().get("detail", response.text)
        err.print(f"[red]Query failed ({response.status_code}): {detail}[/red]")
        raise typer.Exit(1)

    data = response.json()
    rows: list[dict] = data["rows"]
    columns: list[str] = data["columns"]

    if output == "json":
        console.print_json(json.dumps(rows, default=str))

    elif output == "csv":
        buf = io.StringIO()
        writer = csv_module.DictWriter(buf, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        console.print(buf.getvalue(), end="")

    else:
        table = Table(show_header=True, header_style="bold cyan")
        for col in columns:
            table.add_column(col)
        for row in rows:
            table.add_row(*[str(row.get(c, "")) for c in columns])
        console.print(table)
        console.print(f"\n[dim]{data['rows_returned']} row(s) returned[/dim]")
