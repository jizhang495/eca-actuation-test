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

# Start backend
cd eca-actuation-test
uv run run_backend.py > ../backend.log 2>&1 &
BACKEND_PID=$!
echo "✓ Backend started (PID: $BACKEND_PID) - Logs: backend.log"
cd ..

# Wait for backend to start
sleep 3

# Start camera service (optional)
cd camera
uv run camera_service.py > ../camera.log 2>&1 &
CAMERA_PID=$!
echo "✓ Camera service started (PID: $CAMERA_PID) - Logs: camera.log"
cd ..

# Wait for camera service to start
sleep 2

# Start frontend
cd frontend
npm run dev > ../frontend.log 2>&1 &
FRONTEND_PID=$!
echo "✓ Frontend started (PID: $FRONTEND_PID) - Logs: frontend.log"
cd ..

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
echo "  To stop all services, run:"
echo "    kill $BACKEND_PID $CAMERA_PID $FRONTEND_PID"
echo ""
echo "================================================"
echo ""
echo "Opening browser..."
sleep 2

# Open browser
if command -v xdg-open &> /dev/null; then
    xdg-open http://localhost:3000
elif command -v open &> /dev/null; then
    open http://localhost:3000
fi

echo ""
echo "Press Ctrl+C to stop all services and exit."
echo ""

# Wait for Ctrl+C
trap "echo ''; echo 'Stopping services...'; kill $BACKEND_PID $CAMERA_PID $FRONTEND_PID 2>/dev/null; exit" INT
wait

