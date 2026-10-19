#!/bin/sh
# Apply database migrations (only from the web container), then start the given command.
set -e

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "Applying database migrations..."
    flask db upgrade
fi

exec "$@"
