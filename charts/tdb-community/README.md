# tdb-community Helm chart

Runs the TDB community edition on Kubernetes: one CSV source, REST and MCP, read-only
SQL, and an audit log on a persistent volume.

```bash
git clone https://github.com/tdb-project/tdb-community.git
kubectl create secret generic tdb-key --from-literal=api-key="$(openssl rand -hex 32)"
helm install tdb ./tdb-community/charts/tdb-community \
  --set apiKey.existingSecret=tdb-key \
  --set data.existingClaim=<a PVC holding your CSV files>
```

Then register a file from the data volume with `file_path: /data/<file>.csv`. The
release notes print the exact commands.

## What it deploys

- One pod, by design: the source registry (SQLite) and the audit log are files on one
  ReadWriteOnce volume, and two writers would corrupt both. Updates use `Recreate`.
- A ClusterIP Service on port 8000, and an optional Ingress.
- The pod runs as uid 1000 with a read-only root filesystem, no privilege escalation and
  no capabilities. CSV files are mounted read-only at `/data`, and SQL cannot read files
  outside it.
- `helm test` checks `/health` from inside the cluster.

## Values

| Value | Default | Meaning |
|---|---|---|
| `apiKey.existingSecret` / `apiKey.existingSecretKey` | `""` / `api-key` | Secret holding the API key. Recommended |
| `apiKey.value` | `""` | Or a key the chart stores in a Secret. One of the two is required |
| `persistence.enabled` / `size` / `storageClass` / `existingClaim` | `true` / `1Gi` / `""` / `""` | Volume for the registry and audit log |
| `data.existingClaim` | `""` | PVC with your CSV files, mounted read-only at `/data` |
| `logLevel` | `INFO` | Application log level |
| `extraEnv` | `[]` | Other variables, e.g. `TDB_LEAN_MODE=on` |
| `service.type` / `service.port` | `ClusterIP` / `8000` | |
| `ingress.*` | disabled | `className`, `annotations`, `host`, `tls` |
| `resources`, `nodeSelector`, `tolerations`, `affinity`, `podAnnotations` | | As usual |

Serve it over HTTPS (Ingress with TLS, or a proxy) before anything outside the cluster
uses it. Full guide: https://docs.tdb.jiracorp.co.in/deployment/kubernetes/
