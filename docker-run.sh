#!/usr/bin/env bash
# Build and run jadebot in Docker.
# The image is built with uv (see Dockerfile). The container reads its
# config from $ENV_FILE and persists SQLite data in $LOGS_DIR.
set -euo pipefail

IMAGE="${IMAGE:-jadebot:latest}"
ENV_FILE="${ENV_FILE:-.env}"
PORT="${PORT:-8080}"
LOGS_DIR="${LOGS_DIR:-$PWD/logs}"
NAME="${NAME:-jadebot}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "error: $ENV_FILE not found. Copy .env.example to .env and fill it in." >&2
    exit 1
fi

mkdir -p "$LOGS_DIR"

docker build -t "$IMAGE" .

# Inside the container the web server always binds to 0.0.0.0:8080 so the
# port mapping works regardless of what WEB_HOST/WEB_PORT the user set.
exec docker run --rm -it \
    --name "$NAME" \
    --env-file "$ENV_FILE" \
    -e WEB_HOST=0.0.0.0 \
    -e WEB_PORT=8080 \
    -p "$PORT:8080" \
    -v "$LOGS_DIR:/app/logs" \
    "$IMAGE"
