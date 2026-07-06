# Custom Temporal server image that wraps the stock entrypoint with a
# shim that materialises mTLS cert/key/chain content from env vars into
# files on disk before Temporal reads its TLS config.
#
# The base image's docker-entrypoint.sh expects TEMPORAL_TLS_* env vars
# that contain *file paths* — but ECS Secrets can only inject secret
# *content* as env vars (not files). Our shim bridges that gap.
#
# Pinned to match cluster.TEMPORAL_SERVER_TAG. Bumping requires the
# Temporal upgrade runbook (Q4).
FROM temporalio/server:1.27.4

# `tini` would be nice but the base image already has a proper init.
# We just layer in the shim. Set the mode at COPY time (`--chmod`, which
# runs as root) instead of a separate `RUN chmod`: temporalio/server:1.27.4
# runs as a non-root user, so a `RUN chmod` on the root-owned copied file
# fails with "Operation not permitted" (exit 1) and breaks the image build.
COPY --chmod=0755 entrypoint.sh /usr/local/bin/cert-ra-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/cert-ra-entrypoint.sh"]
# Inherit CMD from the base image so we keep its server start behaviour.
