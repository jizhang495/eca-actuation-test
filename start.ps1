# Quick Start Script for ECA Testing Webapp (Windows PowerShell)

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  ECA Testing Webapp - Quick Start" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# Check if Python is installed
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Error: Python is not installed. Please install Python 3.11 or higher." -ForegroundColor Red
    exit 1
}

# Check if Node.js is installed
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "Error: Node.js is not installed. Please install Node.js 18 or higher." -ForegroundColor Red
    exit 1
}

$pythonVersion = python --version
$nodeVersion = node --version
Write-Host "Success: Python found: $pythonVersion" -ForegroundColor Green
Write-Host "Success: Node.js found: $nodeVersion" -ForegroundColor Green
Write-Host ""

# Install Python dependencies
Write-Host "Installing Python dependencies..." -ForegroundColor Yellow
Set-Location eca-actuation-test
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Host "Using uv package manager..."
    uv sync
} else {
    Write-Host "Using pip..."
    pip install -e ..
}
Set-Location ..

# Install Frontend dependencies
Write-Host ""
Write-Host "Installing frontend dependencies..." -ForegroundColor Yellow
Set-Location frontend
if (-not (Test-Path "node_modules")) {
    npm install
} else {
    Write-Host "Frontend dependencies already installed."
}
Set-Location ..

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Setup Complete! Starting services..." -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# Start backend
Write-Host "Starting backend..." -ForegroundColor Yellow
Set-Location eca-actuation-test
$backend = Start-Process python -ArgumentList "run_backend.py" -PassThru -WindowStyle Hidden
Write-Host "Success: Backend started (PID: $($backend.Id))" -ForegroundColor Green
Set-Location ..

# Wait for backend to start
Start-Sleep -Seconds 3

# Start camera service
Write-Host "Starting camera service..." -ForegroundColor Yellow
Set-Location camera
$camera = Start-Process python -ArgumentList "camera_service.py" -PassThru -WindowStyle Hidden
Write-Host "Success: Camera service started (PID: $($camera.Id))" -ForegroundColor Green
Set-Location ..

# Wait for camera service to start
Start-Sleep -Seconds 2

# Start frontend
Write-Host "Starting frontend..." -ForegroundColor Yellow
Set-Location frontend
$frontend = Start-Process npm -ArgumentList "run","dev" -PassThru -WindowStyle Hidden
Write-Host "Success: Frontend started (PID: $($frontend.Id))" -ForegroundColor Green
Set-Location ..

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Services Running!" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Frontend:  http://localhost:3000" -ForegroundColor White
Write-Host "  Backend:   http://localhost:8000" -ForegroundColor White
Write-Host "  API Docs:  http://localhost:8000/docs" -ForegroundColor White
Write-Host "  Camera:    http://localhost:8001" -ForegroundColor White
Write-Host ""
Write-Host "  Process IDs:" -ForegroundColor Yellow
Write-Host "    Backend:  $($backend.Id)"
Write-Host "    Camera:   $($camera.Id)"
Write-Host "    Frontend: $($frontend.Id)"
Write-Host ""
Write-Host "  To stop services:" -ForegroundColor Yellow
Write-Host "    Stop-Process -Id $($backend.Id),$($camera.Id),$($frontend.Id)"
Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Opening browser..." -ForegroundColor Yellow
Start-Sleep -Seconds 3

# Open browser
Start-Process "http://localhost:3000"

Write-Host ""
Write-Host "Services are running. Check the process IDs above to stop them when done." -ForegroundColor Green
Write-Host ""
