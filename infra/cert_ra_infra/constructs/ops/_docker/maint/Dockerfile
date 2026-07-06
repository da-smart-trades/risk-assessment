# Custom maintenance container image for cert-ra.
#
# Per the design spec § MaintenanceContainer construct, this image
# layers operator tooling on top of a Debian-slim Python base:
#
# - `temporal` CLI (real binary renamed to `temporal-real`; wrapper
#   at `/usr/local/bin/temporal` materialises the mTLS triplet env
#   vars into files before exec'ing the real binary).
# - `psql` (PostgreSQL client matching the RDS major version, 16).
# - `jq` + `dnsutils` for ad-hoc inspection.
#
# **Explicitly excluded under H2:**
# - `curl` / `wget` / `httpie`. The maint container is for inspecting
#   *internal* state; staging external data should happen elsewhere.
#   The build stage uses curl to fetch the temporal CLI tarball; the
#   final stage does NOT carry curl forward (multi-stage Docker).
#
# **Why no cert-ra Python install:**
# Running `certora-risk-api database upgrade` from the maint container
# was considered but rejected — MigrationsStack already provides that
# entrypoint as a one-off ECS RunTask invocation with the right IAM
# scoping (`EcsRunMigrationTaskOnly` Sid). The maint container is
# operator-facing (interactive shell, inspection) — keeping schema
# mutation OUT of it is the audit + blast-radius win.

# Pinned to match the upstream Temporal server we run. The CLI versions
# its own release cadence (separate from the server); CLI 1.x is
# compatible with server 1.27.x.
ARG TEMPORAL_CLI_VERSION=1.6.0

# --- Stage 1: tools ---
# Downloads the temporal CLI binary. curl + tar live here only; the
# final image doesn't carry them forward.
FROM debian:bookworm-slim AS tools

ARG TEMPORAL_CLI_VERSION
ARG TARGETARCH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# The temporal CLI publishes per-arch tarballs at temporal.download.
# `TARGETARCH` is set by buildx (`linux/arm64` → `arm64`).
RUN curl -fsSL \
        "https://temporal.download/cli/archive/v${TEMPORAL_CLI_VERSION}?platform=linux&arch=${TARGETARCH}" \
        -o /tmp/temporal.tgz \
    && tar -xzf /tmp/temporal.tgz -C /tmp temporal \
    && mv /tmp/temporal /usr/local/bin/temporal-real \
    && chmod 0755 /usr/local/bin/temporal-real \
    && /usr/local/bin/temporal-real --version

# --- Stage 2: runtime ---
# Same Python base as the cert-ra app image so Python is available
# for ad-hoc scripts via the operator's Exec session.
FROM python:3.13-slim-bookworm AS runtime

# Runtime apt packages. No curl / wget / httpie — H2 constraint.
#   postgresql-client: `psql` for ad-hoc DB inspection (RDS major 16).
#   jq: JSON munging in Exec sessions.
#   dnsutils: `dig`, `nslookup` for internal-DNS sanity checks.
#   tini: minimal init so signals propagate cleanly to children.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        dnsutils \
        jq \
        postgresql-client \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Copy the temporal CLI binary in from the tools stage.
COPY --from=tools /usr/local/bin/temporal-real /usr/local/bin/temporal-real

# Install the wrapper as /usr/local/bin/temporal. The MaintenanceContainer
# construct injects TEMPORAL_TLS_* + TEMPORAL_ADDRESS env vars; the
# wrapper materialises them to files in /run/temporal-tls/ and exec's
# the real binary with the right flags.
COPY temporal-wrapper.sh /usr/local/bin/temporal
RUN chmod 0755 /usr/local/bin/temporal

# The MaintenanceContainer construct overrides CMD with `sleep
# infinity` (set in cert_ra_infra/constructs/ops/maintenance_container.py
# via `MAINT_COMMAND`). We keep the upstream `python:3.13-slim`
# default WORKDIR; operator Exec sessions land in /.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sleep", "infinity"]
