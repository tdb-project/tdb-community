# Security Policy

## Supported Versions

Only the latest release receives security patches. No backports to older versions.

| Version | Supported |
|---------|-----------|
| 0.4.x   | Yes       |
| < 0.4   | No        |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report security issues via **GitHub's private security advisory** mechanism:

1. Go to the [Security tab](../../security/advisories) of this repository
2. Click **"New draft security advisory"**
3. Describe the vulnerability, steps to reproduce, and potential impact

You can also email us directly: **security@tdb.jiracorp.co.in**

### Response SLA
- **Acknowledgement:** within 48 hours of receiving the report
- **Assessment:** within 5 business days
- **Patch for critical/high severity:** within 14 days
- **Patch for medium/low severity:** within 60 days

We follow responsible disclosure: we ask that you do not publish details until a patch has been released and users have had reasonable time to update.

## Security Scope of Community Edition

The following are **known design constraints** of the Community Edition, not vulnerabilities. They are intentional and documented:

| Constraint | Detail |
|---|---|
| Single static API key | No rotation, no expiry. Use a long random string. Rotate manually by restarting the server with a new `TDB_API_KEYS` value. |
| Mutable audit log | The NDJSON audit file can be deleted or modified by anyone with filesystem access. For tamper-evident logs, see TDB Enterprise. |
| No PII masking | SQL queries and their results are not scanned for personally identifiable information. Avoid querying columns containing raw PII through the API. |
| Full SQL logged | The complete SQL query text is written to the audit log. Do not embed sensitive literal values (e.g. SSNs, passwords) directly in WHERE clauses. |
| No rate limiting | Community Edition has no built-in throttling. Place TDB behind a reverse proxy (nginx, Caddy) if public-facing. |
| SELECT-only enforcement | The SQL validator blocks INSERT, UPDATE, DELETE, DROP, CREATE, EXEC, and semicolons. It does not sanitise query results; ensure your CSV files do not contain executable content consumed by downstream clients. |

## Security Best Practices for Deployment

- Generate a strong random API key: `openssl rand -hex 32`
- Never expose TDB directly to the internet without a TLS-terminating reverse proxy
- Mount CSV files read-only (`/data:ro` in Docker Compose — already configured)
- Run as a non-root user (Docker image already does this)
- Keep the `TDB_API_KEYS` environment variable out of version control; use a secrets manager or `.env` file excluded from git
- Rotate the API key by updating `TDB_API_KEYS` and restarting the container

## Dependency Security

Runtime dependencies are pinned in `requirements.txt`. We run `pip-audit` in CI to detect known CVEs in pinned packages.
