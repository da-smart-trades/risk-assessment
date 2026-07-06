#!/bin/sh
# cert-ra entrypoint shim for the Temporal server.
#
# Reads MTLS_* env vars containing PEM content (injected as ECS Secrets
# from the temporal-frontend Secrets Manager entry, fields cert/key/chain).
# Writes them to files in /tmp/temporal-tls/, sets the TEMPORAL_TLS_*
# path env vars that the upstream config_template.yaml consumes, then
# unsets the content env vars so they don't leak via the shell env or
# /proc/self/environ.
#
# When the MTLS_* env vars are absent (initial bootstrap deploy, mTLS
# off), the shim is a no-op and just execs the upstream entrypoint.
#
# Idempotent: writing the same content twice is fine; permissions are
# enforced with chmod.

set -eu

# `/tmp` is the universal world-writable directory on Linux containers.
# `/run` is owned by root with 0755 and the temporalio/server image runs
# as the non-root `temporal` user, so a previous attempt with `/run`
# crash-looped with `mkdir: can't create directory '/run/temporal-tls':
# Permission denied` the first time mTLS enforcement was flipped on.
# `chmod 0700` on the dir below still keeps cert material confined to
# the running user.
TLS_DIR="/tmp/temporal-tls"
SERVER_CERT="${TLS_DIR}/server.crt"
SERVER_KEY="${TLS_DIR}/server.key"
SERVER_CA="${TLS_DIR}/ca.crt"

if [ -n "${MTLS_CERT_CONTENT:-}" ] \
   && [ -n "${MTLS_KEY_CONTENT:-}" ] \
   && [ -n "${MTLS_CHAIN_CONTENT:-}" ]; then
    mkdir -p "${TLS_DIR}"
    # 0700 on the dir, 0400 on the key.
    chmod 0700 "${TLS_DIR}"

    # printf "%s" — preserves trailing newlines from the PEM. Avoid
    # echo which adds its own newline and may interpret backslashes
    # depending on shell.
    printf '%s' "${MTLS_CERT_CONTENT}" > "${SERVER_CERT}"
    printf '%s' "${MTLS_KEY_CONTENT}" > "${SERVER_KEY}"
    printf '%s' "${MTLS_CHAIN_CONTENT}" > "${SERVER_CA}"
    chmod 0400 "${SERVER_CERT}" "${SERVER_KEY}" "${SERVER_CA}"

    # Wire the path env vars that /etc/temporal/config/config_template.yaml
    # consumes. We use the same cert/key for internode + frontend server
    # listeners (per the design's "shared internode + frontend TLS" model)
    # and the same CA bundle to accept client connections at the frontend
    # (clientCaFiles == server CA bundle, since all client certs are issued
    # by the same subordinate).
    export TEMPORAL_TLS_SERVER_CERT="${SERVER_CERT}"
    export TEMPORAL_TLS_SERVER_KEY="${SERVER_KEY}"
    export TEMPORAL_TLS_SERVER_CA_CERT="${SERVER_CA}"
    export TEMPORAL_TLS_FRONTEND_CERT="${SERVER_CERT}"
    export TEMPORAL_TLS_FRONTEND_KEY="${SERVER_KEY}"
    export TEMPORAL_TLS_CLIENT1_CA_CERT="${SERVER_CA}"
    export TEMPORAL_TLS_FRONTEND_SERVER_NAME="${TEMPORAL_TLS_FRONTEND_SERVER_NAME:-temporal-frontend.cert-ra.local}"
    export TEMPORAL_TLS_INTERNODE_SERVER_NAME="${TEMPORAL_TLS_INTERNODE_SERVER_NAME:-temporal-frontend.cert-ra.local}"

    # Unset the content env vars so they don't show up in `env`,
    # /proc/self/environ, or anywhere the Temporal server might log.
    unset MTLS_CERT_CONTENT MTLS_KEY_CONTENT MTLS_CHAIN_CONTENT
fi

# Temporal cluster membership (ringpop): every server role advertises its
# own RPC address into the cluster_membership table, and peer roles dial
# that address to form the ring. On Fargate awsvpc each task gets a fresh,
# dynamic ENI IP, so a static value can't be baked into the task def. We
# use the literal 0.0.0.0 (or unset) as a sentinel meaning "resolve my real
# IP at runtime"; an explicit address is left untouched. Without this each
# role advertises 0.0.0.0, peers can't dial it, ringpop never forms a quorum
# ("Not enough hosts to serve the request" / "failed to start ringpop:
# join duration exceeded max 30s"), and every role crash-loops. bindOnIP
# stays 0.0.0.0 (listen on all interfaces) — only the advertised address
# must be the routable task IP.
if [ -z "${TEMPORAL_BROADCAST_ADDRESS:-}" ] \
   || [ "${TEMPORAL_BROADCAST_ADDRESS}" = "0.0.0.0" ]; then
    resolved_ip="$(hostname -i 2>/dev/null | awk '{print $1}')"
    if [ -n "${resolved_ip:-}" ]; then
        export TEMPORAL_BROADCAST_ADDRESS="${resolved_ip}"
        echo "cert-ra entrypoint: resolved TEMPORAL_BROADCAST_ADDRESS=${resolved_ip}" >&2
    fi
fi

# Hand off to the stock Temporal entrypoint (confirmed path on
# temporalio/server:1.27.x). CMD is inherited from the base image
# (defaults to start-temporal.sh).
exec /etc/temporal/entrypoint.sh "$@"
