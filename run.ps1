# run.ps1 - Lanzador de un clic para MIA (Windows / PowerShell).
# Verifica el entorno, avisa si Ollama no esta en marcha y arranca la app
# (Streamlit abre el navegador solo). Uso:  boton derecho > Ejecutar con PowerShell
# o doble clic en run.bat.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root ".venv\Scripts\python.exe"

Write-Host ""
Write-Host "==================================================" -ForegroundColor DarkCyan
Write-Host "  MIA - Medical Intelligence Agent (100% local)"   -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor DarkCyan

# 1) Entorno virtual
if (-not (Test-Path $py)) {
    Write-Host "[X] Virtual environment (.venv) not found." -ForegroundColor Red
    Write-Host "    Looks like the first run on this machine. Run the guided setup:" -ForegroundColor Yellow
    Write-Host "      double-click setup.bat" -ForegroundColor Yellow
    Write-Host "    (creates the environment, installs the dependencies and downloads the models, asking first)" -ForegroundColor DarkYellow
    Read-Host "`nPress Enter to exit"
    exit 1
}
Write-Host "[OK] Virtual environment found." -ForegroundColor Green

# 2) Ollama en marcha (necesario para responder). Si no, avisamos pero seguimos:
#    la propia app muestra el aviso con la solucion.
try {
    $null = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 3
    Write-Host "[OK] Ollama is responding on localhost:11434." -ForegroundColor Green
} catch {
    Write-Host "[!] Ollama is not responding. Start it in ANOTHER terminal with:" -ForegroundColor Yellow
    Write-Host "      ollama serve" -ForegroundColor Yellow
    Write-Host "    (The app will start anyway and tell you what is missing.)" -ForegroundColor DarkYellow
}

# 3) Arrancar la app en modo headless (asi Streamlit NO pide el email de primer
#    arranque, que bloquearia el lanzador) y abrir el navegador nosotros cuando
#    el servidor responda. La ventana se queda viva hasta que se cierra el server.
$appPath = Join-Path $root "app\streamlit_app.py"
Write-Host ""
Write-Host "Starting MIA at http://localhost:8501 ..." -ForegroundColor Cyan

# -ArgumentList como UNA cadena y con la ruta ENTRE COMILLAS: la ruta del proyecto
# tiene espacios ("PROYECTO CAPSTONE AI"), y un array sin comillas la partiria.
$argline = "-m streamlit run `"$appPath`" --server.headless true --server.port 8501"
$proc = Start-Process -FilePath $py -ArgumentList $argline -PassThru -NoNewWindow

# Esperar a que el servidor este vivo (hasta ~40s) y abrir el navegador.
$listo = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 2
    try {
        $null = Invoke-WebRequest -Uri "http://localhost:8501/_stcore/health" -UseBasicParsing -TimeoutSec 2
        $listo = $true; break
    } catch { }
}
if ($listo) {
    Write-Host "[OK] MIA is ready. Opening the browser..." -ForegroundColor Green
    Start-Process "http://localhost:8501"
} else {
    Write-Host "[!] The server is taking longer than usual; open http://localhost:8501 manually." -ForegroundColor Yellow
}
Write-Host "(Close this window or press Ctrl+C to stop MIA.)" -ForegroundColor DarkGray
Wait-Process -Id $proc.Id
