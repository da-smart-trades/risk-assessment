#!/bin/bash
# temporal CLI wrapper for the cert-ra maintenance container.
#
# The maintenance container's MaintenanceContainer construct injects
# the maint mTLS triplet as env vars:
#
#   TEMPORAL_TLS_CLIENT_CERT_CONTENT  (PEM client cert)
#   TEMPORAL_TLS_CLIENT_KEY_CONTENT   (PEM private key)
#   TEMPORAL_TLS_CA_CERT_CONTENT      (PEM CA chain)
#   TEMPORAL_TLS_SERVER_NAME          (SNI / cert-CN; usually
#                                      `temporal-frontend.cert-ra.local`)
#   TEMPORAL_ADDRESS                  (NLB DNS name : 7233)
#
# The upstream `temporal` binary expects --tls-cert-path /
# --tls-key-path / --tls-ca-path / --tls-server-name with file paths.
# This wrapper materialises the PEM content to files on first
# invocation, then exec's the real binary with the right flags.
#
# Installed at /usr/local/bin/temporal in the maint Docker image; the
# real binary is renamed to /usr/local/bin/temporal-real.
#
# Idempotent: writing the same content twice is fine; the perms guard
# keeps the dir at 0700 and the files at 0400.

set -euo pipefail

# `/tmp` is universally writable; `/run` is root-owned tmpfs which
# breaks any non-root runtime. Keeping both wrappers (this one + the
# temporal_server entrypoint) on the same path avoids surprises during
# operator debugging.
TLS_DIR="/tmp/temporal-tls"
CERT_FILE="${TLS_DIR}/client.crt"
KEY_FILE="${TLS_DIR}/client.key"
CA_FILE="${TLS_DIR}/ca.crt"

# If any of the env vars are missing we exec the real binary
# unchanged — useful for `temporal env list` and other commands that
# don't actually connect.
if [ -n "${TEMPORAL_TLS_CLIENT_CERT_CONTENT:-}" ] \
   && [ -n "${TEMPORAL_TLS_CLIENT_KEY_CONTENT:-}" ] \
   && [ -n "${TEMPORAL_TLS_CA_CERT_CONTENT:-}" ]; then
    mkdir -p "${TLS_DIR}"
    chmod 0700 "${TLS_DIR}"

    # printf "%s" preserves trailing newlines from the PEM. Avoid
    # echo which adds its own newline.
    printf '%s' "${TEMPORAL_TLS_CLIENT_CERT_CONTENT}" > "${CERT_FILE}"
    printf '%s' "${TEMPORAL_TLS_CLIENT_KEY_CONTENT}" > "${KEY_FILE}"
    printf '%s' "${TEMPORAL_TLS_CA_CERT_CONTENT}" > "${CA_FILE}"
    chmod 0400 "${CERT_FILE}" "${KEY_FILE}" "${CA_FILE}"

    TLS_ARGS=(
        --tls-cert-path "${CERT_FILE}"
        --tls-key-path "${KEY_FILE}"
        --tls-ca-path "${CA_FILE}"
    )
    if [ -n "${TEMPORAL_TLS_SERVER_NAME:-}" ]; then
        TLS_ARGS+=(--tls-server-name "${TEMPORAL_TLS_SERVER_NAME}")
    fi

    # The `temporal` CLI accepts --address either at the top level or
    # after the subcommand. Threading it through unconditionally when
    # TEMPORAL_ADDRESS is set keeps operator invocations
    # (`temporal workflow list`) one-liners.
    if [ -n "${TEMPORAL_ADDRESS:-}" ]; then
        TLS_ARGS+=(--address "${TEMPORAL_ADDRESS}")
    fi

    exec /usr/local/bin/temporal-real "${TLS_ARGS[@]}" "$@"
fi

# No mTLS env vars — pass through unchanged.
exec /usr/local/bin/temporal-real "$@"
