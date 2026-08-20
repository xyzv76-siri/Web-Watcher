#!/bin/sh
set -eu

# Ensure runtime directories exist (volumes may start empty)
mkdir -p /data /logs

# Pre-flight: fail fast if required env / files are obviously wrong.
# Actual validation happens inside the Python process; this is a lightweight
# guard so we don't start the interpreter with a clearly broken environment.
if [ -n "${WEB_WATCHER_DB:-}" ] && [ ! -e "${WEB_WATCHER_DB}" ]; then
    echo "[entrypoint] DB path does not exist yet: ${WEB_WATCHER_DB} (will be created)" >&2
fi

# Execute the main command
exec "$@"
