#!/bin/bash
# Stop Script for ECA Testing Webapp (Linux/Mac)

echo "================================================"
echo "  ECA Testing Webapp - Stopping Services"
echo "================================================"
echo ""

get_port_pids() {
    local pids=""

    if command -v fuser &> /dev/null; then
        pids=$(fuser -n tcp "$1" 2>/dev/null | tr ' ' '\n' | sed '/^$/d')
    fi

    if [ -z "$pids" ] && command -v lsof &> /dev/null; then
        pids=$(lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null)
    fi

    echo "$pids"
}

stop_pids() {
    local label="$1"
    shift
    local pids
    pids=$(printf "%s\n" "$@" | sed '/^$/d' | sort -u)

    if [ -z "$pids" ]; then
        echo "✓ No $label processes found"
        return
    fi

    echo "Stopping $label processes: $(echo "$pids" | tr '\n' ' ')"
    kill $pids 2>/dev/null || true

    for _ in 1 2 3 4 5; do
        local remaining=""
        for pid in $pids; do
            if kill -0 "$pid" 2>/dev/null; then
                remaining="$remaining $pid"
            fi
        done
        [ -z "$remaining" ] && break
        sleep 1
    done

    local stubborn=""
    for pid in $pids; do
        if kill -0 "$pid" 2>/dev/null; then
            stubborn="$stubborn $pid"
        fi
    done

    if [ -n "$stubborn" ]; then
        echo "Force stopping $label processes:$stubborn"
        kill -9 $stubborn 2>/dev/null || true
    fi
}

collect_pattern_pids() {
    local pattern="$1"
    pgrep -f "$pattern" 2>/dev/null | grep -v "^$$$" || true
}

# Stop known service process trees and any listeners on app ports. This catches
# uv/npm wrapper processes as well as the Python/Next child processes.
echo "Stopping app services..."
SERVICE_PIDS=$( {
    collect_pattern_pids "uv run run_backend.py"
    collect_pattern_pids "python.*run_backend.py"
    collect_pattern_pids "uv run camera_service.py"
    collect_pattern_pids "python.*camera_service.py"
    collect_pattern_pids "npm run dev"
    collect_pattern_pids "next dev"
    collect_pattern_pids "next-server"
    get_port_pids 3000
    get_port_pids 8000
    get_port_pids 8001
} | sed '/^$/d' | sort -u )

stop_pids "app service" $SERVICE_PIDS

# Wait a moment for processes to fully terminate
sleep 2

# Verify all ports are free
echo ""
echo "Verifying ports are free..."
PORTS=(3000 8000 8001)
ALL_FREE=true

for port in "${PORTS[@]}"; do
    if lsof -i :$port >/dev/null 2>&1; then
        echo "⚠️  Port $port is still in use:"
        lsof -i :$port
        ALL_FREE=false
    else
        echo "✓ Port $port is free"
    fi
done

echo ""
if [ "$ALL_FREE" = true ]; then
    echo "================================================"
    echo "  All services stopped successfully!"
    echo "================================================"
else
    echo "================================================"
    echo "  Services stopped, but some ports may still be in use"
    echo "  If needed, manually kill remaining processes:"
    echo "  pkill -f 'python.*run_backend.py'"
    echo "  pkill -f 'python.*camera_service.py'"
    echo "  pkill -f 'npm run dev'"
    echo "================================================"
fi

echo ""
echo "To start services again, run: ./start.sh"
echo ""
