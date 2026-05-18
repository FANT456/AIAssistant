$ErrorActionPreference = 'Stop'

$ProjectRoot = 'D:\Share2\AIAssistant'
$PythonScript = Join-Path $ProjectRoot 'code\main.py'
$PythonExe = 'D:\Software\Miniconda3\envs\myenv\python.exe'
$OllamaStartScript = 'D:\Software\ollama-intel-2.3.0b20250923-win\start-ollama.bat'
$OllamaHealthUrl = 'http://localhost:11434/api/tags'

if (-not (Test-Path $PythonScript)) {
    throw "Python script not found: $PythonScript"
}

if (-not (Test-Path $PythonExe)) {
    throw "Python executable not found: $PythonExe"
}

if (-not (Test-Path $OllamaStartScript)) {
    throw "Ollama start script not found: $OllamaStartScript"
}

Write-Host '==> 1. Starting Ollama...' -ForegroundColor Cyan
Start-Process -FilePath $OllamaStartScript

Write-Host '==> 2. Waiting for Ollama...' -ForegroundColor Cyan
$maxWaitSeconds = 60
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$ollamaReady = $false

do {
    Start-Sleep -Seconds 2
    try {
        $response = Invoke-WebRequest -Uri $OllamaHealthUrl -UseBasicParsing -TimeoutSec 3
        if ($response.StatusCode -eq 200) {
            $ollamaReady = $true
            break
        }
    }
    catch {
    }
} while ($stopwatch.Elapsed.TotalSeconds -lt $maxWaitSeconds)

if (-not $ollamaReady) {
    throw "Ollama did not become ready within $maxWaitSeconds seconds."
}

Write-Host '==> 3. Starting Python app...' -ForegroundColor Green
Set-Location $ProjectRoot
& $PythonExe $PythonScript
