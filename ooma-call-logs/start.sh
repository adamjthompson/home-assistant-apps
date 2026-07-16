#!/bin/bash
set -e

echo "Starting FlareSolverr in the background..."
python3 -u /app/flaresolverr.py &
FLARESOLVERR_PID=$!

echo "Waiting for FlareSolverr to report ready (GET /health)..."
until curl -sf http://localhost:8191/health > /dev/null 2>&1; do
    if ! kill -0 "$FLARESOLVERR_PID" 2>/dev/null; then
        echo "FlareSolverr exited before becoming ready -- see its output above." >&2
        exit 1
    fi
    sleep 1
done
echo "FlareSolverr is ready."

# A single long-running Python process (not a bash retry loop) -- it reads
# /data/options.json once at startup and loops internally on its own
# run_interval_minutes. `exec` replaces this script so the process stays a
# direct child of dumb-init (PID 1), same as FlareSolverr's own process.
exec python3 -u /app/ooma-call-logs.py
