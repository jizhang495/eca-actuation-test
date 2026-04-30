#!/bin/bash
# Quick Start Script for ECA Testing Webapp (Linux/Mac)

echo "================================================"
echo "  ECA Testing Webapp - Quick Start"
echo "================================================"
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "❌ uv is not installed. Please install uv (https://github.com/astral-sh/uv)."
    exit 1
fi

# Check if Node.js is installed
if ! command -v node &> /dev/null; then
    echo "❌ Node.js is not installed. Please install Node.js 18 or higher."
    exit 1
fi

echo "✓ uv found: $(uv --version)"
echo "✓ Node.js found: $(node --version)"
echo ""

# Install Python dependencies
echo "📦 Installing Python dependencies..."
cd eca-actuation-test
echo "Using uv package manager..."
uv sync
cd ..

# Install Frontend dependencies
echo ""
echo "📦 Installing frontend dependencies..."
cd frontend
if [ ! -d "node_modules" ]; then
    npm install
else
    echo "Frontend dependencies already installed."
fi
cd ..

echo ""
echo "================================================"
echo "  Setup Complete! Starting services..."
echo "================================================"
echo ""
echo "Starting services in background..."
echo ""

STARTED_PIDS=()

get_port_pids() {
    local pids=""

    if command -v fuser &> /dev/null; then
        pids=$(fuser -n tcp "$1" 2>/dev/null | tr ' ' '\n' | sed '/^$/d' | tr '\n' ' ' | sed 's/[[:space:]]*$//')
    fi

    if [ -z "$pids" ]; then
        pids=$(lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null | tr '\n' ' ' | sed 's/[[:space:]]*$//')
    fi

    echo "$pids"
}

start_service() {
    local name="$1"
    local port="$2"
    local workdir="$3"
    local command="$4"
    local log_file="$5"
    local pid_var="$6"

    local existing_pids
    existing_pids=$(get_port_pids "$port")
    if [ -n "$existing_pids" ]; then
        printf -v "$pid_var" '%s' "$existing_pids"
        echo "✓ $name already running on port $port (PID: $existing_pids)"
        return
    fi

    (
        cd "$workdir"
        eval "$command" > "../$log_file" 2>&1
    ) &
    local pid=$!
    STARTED_PIDS+=("$pid")
    printf -v "$pid_var" '%s' "$pid"
    echo "✓ $name started (PID: $pid) - Logs: $log_file"
}

# Start camera service first so backend can detect it during startup
start_service "Camera service" 8001 "camera" "uv run camera_service.py" "camera.log" CAMERA_PID

# Wait for camera service to start
sleep 2

# Start backend
start_service "Backend" 8000 "eca-actuation-test" "uv run run_backend.py" "backend.log" BACKEND_PID

# Wait for backend to start
sleep 3

# Start frontend
start_service "Frontend" 3000 "frontend" "npm run dev" "frontend.log" FRONTEND_PID

echo ""
echo "================================================"
echo "  Services Running!"
echo "================================================"
echo ""
echo "  Frontend:  http://localhost:3000"
echo "  Backend:   http://localhost:8000"
echo "  API Docs:  http://localhost:8000/docs"
echo "  Camera:    http://localhost:8001"
echo ""
echo "  Process IDs:"
echo "    Backend: $BACKEND_PID"
echo "    Camera:  $CAMERA_PID"
echo "    Frontend: $FRONTEND_PID"
echo ""
if [ ${#STARTED_PIDS[@]} -gt 0 ]; then
    echo "  To stop services started by this script, run:"
    echo "    kill ${STARTED_PIDS[*]}"
else
    echo "  No new services were started; all ports were already in use."
fi
echo ""
echo "================================================"
echo ""
if [ "${OPEN_BROWSER:-1}" != "0" ]; then
    echo "Opening browser..."
    sleep 2

    # Open browser as a best-effort helper. Some browsers print noisy messages
    # when reusing an existing session, so keep service output clean.
    if command -v xdg-open &> /dev/null; then
        xdg-open http://localhost:3000 >/dev/null 2>&1 &
    elif command -v open &> /dev/null; then
        open http://localhost:3000 >/dev/null 2>&1 &
    else
        echo "No browser opener found. Open http://localhost:3000 manually."
    fi
else
    echo "Skipping browser open because OPEN_BROWSER=0."
fi

echo ""
echo "Press Ctrl+C to stop all services and exit."
echo ""

# Wait for Ctrl+C
trap "echo ''; echo 'Stopping services started by this script...'; if [ ${#STARTED_PIDS[@]} -gt 0 ]; then kill ${STARTED_PIDS[*]} 2>/dev/null; fi; exit" INT
wait
