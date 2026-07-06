#!/bin/bash
set -ex

touch .env

if [ -z "$VERSION" ]; then
    make build
    VERSION="$(cat .version)"
fi


WHEEL_NAME="$(find dist/ -type f -name "*$VERSION*.whl" | head -n 1)"


DOCKER_VERSION="${VERSION//+/-}"
export WHEEL_NAME
export DOCKER_VERSION

export ADD_LATEST
export USE_CACHE

# Use buildx bake to build the Docker images
docker buildx bake --provenance=false "$@"
