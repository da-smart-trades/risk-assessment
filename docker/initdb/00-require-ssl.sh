#!/bin/bash
# Force every Postgres connection through TLS. Runs once at first
# `initdb` — the resulting `pg_hba.conf` persists in the data volume,
# so a fresh `db-data` volume is the only way to re-trigger it.
#
# The cert/key the server presents are generated on every container
# start by the wrapper in `docker/docker-compose.yml`. Clients are
# free to skip verification (e.g. `CERT_RA_DB_SSL_MODE=require`)
# since the cert is self-signed.
set -euo pipefail

PG_HBA="${PGDATA}/pg_hba.conf"

if [ -f "$PG_HBA" ]; then
    # Rewrite all `host` rules to `hostssl`. Leave `local`
    # (unix-socket) rules alone — they don't go through TCP and
    # can't speak TLS.
    sed -i 's/^host\b/hostssl/g' "$PG_HBA"
fi
