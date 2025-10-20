#!/bin/bash
# Stop Script for ECA Testing Webapp (Linux/Mac)

echo "================================================"
echo "  ECA Testing Webapp - Stopping Services"
echo "================================================"
echo ""

# Stop Python processes (Backend and Camera Service)
echo "Stopping Python services (Backend, Camera)..."
PYTHON_PIDS=$(pgrep -f "python.*run_backend.py\|python.*camera_service.py" 2>/dev/null)
if [ -n "$PYTHON_PIDS" ]; then
    echo "Stopping Python processes: $PYTHON_PIDS"
    kill $PYTHON_PIDS 2>/dev/null
    echo "✓ Python services stopped"
else
    echo "✓ No Python processes found"
fi

# Stop Node.js processes (Frontend)
echo ""
echo "Stopping Node.js services (Frontend)..."
NODE_PIDS=$(pgrep -f "npm run dev\|next dev" 2>/dev/null)
if [ -n "$NODE_PIDS" ]; then
    echo "Stopping Node processes: $NODE_PIDS"
    kill $NODE_PIDS 2>/dev/null
    echo "✓ Node.js services stopped"
else
    echo "✓ No Node.js processes found"
fi

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
