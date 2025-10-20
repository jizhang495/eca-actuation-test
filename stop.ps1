# Stop Script for ECA Testing Webapp (Windows PowerShell)

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  ECA Testing Webapp - Stopping Services" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# Stop Python processes (Backend and Camera Service)
Write-Host "Stopping Python services (Backend, Camera)..." -ForegroundColor Yellow
$pythonProcesses = Get-Process -Name "python" -ErrorAction SilentlyContinue
if ($pythonProcesses) {
    $pythonProcesses | ForEach-Object {
        Write-Host "Stopping Python process (PID: $($_.Id))" -ForegroundColor Yellow
        Stop-Process -Id $_.Id -Force
    }
    Write-Host "Success: Python services stopped" -ForegroundColor Green
} else {
    Write-Host "Success: No Python processes found" -ForegroundColor Green
}

# Stop Node.js processes (Frontend)
Write-Host ""
Write-Host "Stopping Node.js services (Frontend)..." -ForegroundColor Yellow
$nodeProcesses = Get-Process -Name "node" -ErrorAction SilentlyContinue
if ($nodeProcesses) {
    $nodeProcesses | ForEach-Object {
        Write-Host "Stopping Node process (PID: $($_.Id))" -ForegroundColor Yellow
        Stop-Process -Id $_.Id -Force
    }
    Write-Host "Success: Node.js services stopped" -ForegroundColor Green
} else {
    Write-Host "Success: No Node.js processes found" -ForegroundColor Green
}

# Wait a moment for processes to fully terminate
Start-Sleep -Seconds 2

# Verify all ports are free
Write-Host ""
Write-Host "Verifying ports are free..." -ForegroundColor Yellow
$ports = @(3000, 8000, 8001)
$allFree = $true

foreach ($port in $ports) {
    $connections = netstat -ano | Select-String ":$port.*LISTENING"
    if ($connections) {
        Write-Host "Warning: Port $port is still in use:" -ForegroundColor Yellow
        $connections | ForEach-Object { Write-Host "   $_" }
        $allFree = $false
    } else {
        Write-Host "Success: Port $port is free" -ForegroundColor Green
    }
}

Write-Host ""
if ($allFree) {
    Write-Host "================================================" -ForegroundColor Green
    Write-Host "  All services stopped successfully!" -ForegroundColor Green
    Write-Host "================================================" -ForegroundColor Green
} else {
    Write-Host "================================================" -ForegroundColor Yellow
    Write-Host "  Services stopped, but some ports may still be in use" -ForegroundColor Yellow
    Write-Host "  If needed, manually kill remaining processes:" -ForegroundColor Yellow
    Write-Host "  taskkill /F /IM python.exe" -ForegroundColor White
    Write-Host "  taskkill /F /IM node.exe" -ForegroundColor White
    Write-Host "================================================" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "To start services again, run: .\start.ps1" -ForegroundColor Cyan
Write-Host ""