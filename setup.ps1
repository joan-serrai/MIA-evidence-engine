# setup.ps1 - Instalacion guiada de MIA (Windows / PowerShell). PRIMERA VEZ.
#
# Que hace: prepara todo lo que MIA necesita para arrancar, paso a paso y
# preguntando antes de cada descarga grande. Se puede ejecutar tantas veces
# como haga falta: lo que ya esta hecho lo salta con un [OK]. Nunca borra ni
# sobrescribe nada que exista.
#
# Uso:  doble clic en setup.bat   (o: powershell -ExecutionPolicy Bypass -File setup.ps1)
#       setup.ps1 -Yes            -> no pregunta (para pruebas / automatizar)
#
# Lo que NO instala (programas de terceros; los instala el usuario):
#   - Python 3.12  -> https://www.python.org/downloads/
#   - Ollama       -> https://ollama.com/download
# Si falta alguno, el script lo dice, da el enlace y se detiene. Al volver a
# ejecutarlo, continua donde se quedo.
#
# Los 8 pasos:  1 Python  2 entorno virtual  3 dependencias  4 archivo .env
#               5 Ollama  6 modelo biomedico  7 MedCPT  8 corpus (vacio a proposito)

param(
    [switch]$Yes   # responder "si" a todo (sin Read-Host)
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$venvPy = Join-Path $root ".venv\Scripts\python.exe"

# La consola de Windows usa cp1252 por defecto; Python imprime acentos y emojis.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = "utf-8"

# ----------------------------------------------------------------------------
# Utilidades de pantalla. Un solo estilo para que el usuario sepa leerlas:
#   [OK] hecho   [..] pendiente   [!] aviso   [X] error que detiene el script
# ----------------------------------------------------------------------------
function Ok($m)   { Write-Host "[OK] $m" -ForegroundColor Green }
function Todo($m) { Write-Host "[..] $m" -ForegroundColor Yellow }
function Info($m) { Write-Host "     $m" -ForegroundColor Gray }
function Warn($m) { Write-Host "[!]  $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[X]  $m" -ForegroundColor Red }
function Step($n, $t) {
    Write-Host ""
    Write-Host "--- Step $n of 8: $t" -ForegroundColor Cyan
}

function Ask-Continue($what) {
    # Devuelve $true si el usuario acepta (Enter o S), $false si escribe N.
    if ($Yes) { Info "$what  -> yes (-Yes mode)"; return $true }
    $r = Read-Host "     $what  [Enter = yes / N = no]"
    return ($r -eq "" -or $r -match '^[sSyY]')
}

function Pause-Exit($code) {
    if (-not $Yes) { Read-Host "`nPress Enter to exit" | Out-Null }
    exit $code
}

function Stop-Setup($why, $how) {
    # Se usa cuando falta un programa de terceros: explicamos y paramos.
    Write-Host ""
    Fail $why
    Info $how
    Info "Once you have it, run setup.bat again: the steps already done are skipped automatically."
    Pause-Exit 1
}

function Run-Py($code) {
    # Ejecuta un trozo de Python con el venv y devuelve su salida (sin errores).
    # Es la forma de leer config.py (UNICA fuente de verdad) desde PowerShell.
    $out = & $venvPy -c $code
    if ($LASTEXITCODE -ne 0) { throw "Python failed to run: $code" }
    return ($out | Out-String).Trim()
}

# ----------------------------------------------------------------------------
# Cabecera: que va a pasar y cuanto ocupa, ANTES de tocar nada.
# ----------------------------------------------------------------------------
Write-Host ""
Write-Host "==================================================" -ForegroundColor DarkCyan
Write-Host "  MIA - Guided setup (first time)"                  -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor DarkCyan
Write-Host ""
Info "About to set up MIA in this folder:"
Info "  $root"
Write-Host ""
Info "What gets downloaded and where it goes (approx.):"
Info "  - Python libraries     1.6 GB   -> this project's .venv folder"
Info "  - OpenBioLLM model     4.9 GB   -> Ollama's models folder"
Info "  - MedCPT encoders      0.8 GB   -> your user's HuggingFace cache"
Info "  - Corpus (PubMed)      0 MB     -> you choose it in the app, at the end"
Write-Host ""
Info "You will be asked before each large download. You can close the window"
Info "at any time and run setup.bat again later."
Write-Host ""
# Windows limita las rutas a 260 caracteres y torch instala archivos con rutas
# muy profundas dentro de .venv. Medido el 8-sep-2026: con el proyecto en una
# carpeta de ~150 caracteres, pip fallo con "WinError 206: nombre demasiado
# largo". Avisamos antes de perder 10 minutos descargando.
if ($root.Length -gt 90) {
    Warn "The project path is $($root.Length) characters long. Windows limits paths to 260 and"
    Warn "installing the libraries may fail ('filename too long')."
    Info "Recommended: move the folder to a short path, for example C:\dev\MIA, and start again."
    if (-not (Ask-Continue "Continue here anyway?")) { Pause-Exit 0 }
}
if (-not (Ask-Continue "Shall we start?")) { Pause-Exit 0 }

# ----------------------------------------------------------------------------
# Paso 1: Python. Buscamos primero el lanzador "py" (lo trae el instalador de
# python.org); si no, un "python" en el PATH que NO sea el alias falso de la
# Microsoft Store (ese abre la tienda en vez de ejecutar nada).
# ----------------------------------------------------------------------------
Step 1 "Python 3.11 or higher"

function Find-Python {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        # "py -0p" lista las versiones instaladas con su ruta. Preferimos 3.12
        # (la version en que se probo MIA); si no, la mas alta >= 3.11.
        $lines = & py -0p 2>&1 | Where-Object { $_ -match '^\s*-V:3\.(\d+)' }
        $best = $null; $bestMinor = 0
        foreach ($l in $lines) {
            if ($l -match '-V:3\.(\d+)[^\s]*\s+\*?\s*(.+python\.exe)') {
                $minor = [int]$Matches[1]; $exe = $Matches[2].Trim()
                if ($minor -eq 12) { return $exe }
                if ($minor -ge 11 -and $minor -gt $bestMinor) { $best = $exe; $bestMinor = $minor }
            }
        }
        if ($best) { return $best }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch "WindowsApps") {
        $ver = & $cmd.Source -c "import sys; print(sys.version_info >= (3, 11))"
        if ("$ver".Trim() -eq "True") { return $cmd.Source }
    }
    return $null
}

$pyExe = $null
if (Test-Path $venvPy) {
    Ok "A virtual environment already exists; no need to look for Python."
} else {
    $pyExe = Find-Python
    if (-not $pyExe) {
        $store = Get-Command python -ErrorAction SilentlyContinue
        if ($store -and $store.Source -match "WindowsApps") {
            Warn "The 'python' on this machine is the Microsoft Store alias, not a real Python."
        }
        Stop-Setup "Python 3.11 or higher not found." `
                   "Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')."
    }
    $ver = & $pyExe -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
    Ok "Python $ver at $pyExe"
}

# ----------------------------------------------------------------------------
# Paso 2: entorno virtual. Una copia privada de Python dentro del proyecto,
# con exactamente las librerias que MIA necesita. No se sube a git: hay que
# crearla en cada equipo. Pesa 1,6 GB una vez instaladas las dependencias.
# ----------------------------------------------------------------------------
Step 2 "Virtual environment (.venv)"
if (Test-Path $venvPy) {
    Ok "Virtual environment found."
} else {
    Todo "Not found. Creating it with:  python -m venv .venv  (a few seconds)"
    & $pyExe -m venv (Join-Path $root ".venv")
    if (-not (Test-Path $venvPy)) { Stop-Setup "Could not create the virtual environment." "Check the error above." }
    Ok "Virtual environment created."
}

# ----------------------------------------------------------------------------
# Paso 3: dependencias. requirements.lock.txt fija TODAS las versiones (135
# paquetes) para reproducir el entorno con el que se probo MIA. Es el paso
# mas largo por torch (el motor de los embeddings), que pesa mas de 1 GB.
# ----------------------------------------------------------------------------
Step 3 "Python dependencies"
$missingCheck = "import importlib.util as u; mods=['streamlit','chromadb','transformers','torch','ollama','sentence_transformers','dotenv','requests']; print(','.join(m for m in mods if u.find_spec(m) is None))"
$missing = Run-Py $missingCheck
if ($missing -eq "") {
    Ok "Dependencies installed."
} else {
    Todo "Missing packages: $missing"
    Info "About to install requirements.lock.txt: around 1.6 GB, 5 to 15 minutes depending on your connection."
    if (Ask-Continue "Install now?") {
        & $venvPy -m pip install --upgrade pip --quiet
        & $venvPy -m pip install -r (Join-Path $root "requirements.lock.txt")
        if ($LASTEXITCODE -ne 0) { Stop-Setup "pip finished with an error." "Read the message above; it is usually the network or not enough disk space." }
        $missing = Run-Py $missingCheck
        if ($missing -ne "") { Stop-Setup "Still missing after installing: $missing" "Run setup.bat again; if it persists, open an Issue on GitHub." }
        Ok "Dependencies installed."
    } else {
        Stop-Setup "MIA cannot start without the dependencies." "Run setup.bat again whenever you want to install them."
    }
}
Info "Environment check (check_setup.py):"
& $venvPy (Join-Path $root "check_setup.py")

# ----------------------------------------------------------------------------
# Paso 4: archivo .env. Guarda claves de acceso a servicios externos (por eso
# no se sube a git). Para usar MIA NO hace falta rellenar nada: se crea vacio
# a partir de la plantilla y solo importa si quieres una clave de PubMed.
# ----------------------------------------------------------------------------
Step 4 ".env file (optional, created empty)"
$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    Ok ".env already exists; leaving it untouched."
} else {
    Copy-Item (Join-Path $root ".env.example") $envFile
    Ok ".env created from .env.example (no need to edit it to get started)."
}

# ----------------------------------------------------------------------------
# Paso 5: Ollama. Es el programa que ejecuta el modelo de lenguaje en local.
# Tres situaciones: responde (bien) / instalado pero parado (lo arrancamos) /
# no instalado (enlace y paramos: lo instala el usuario).
# ----------------------------------------------------------------------------
Step 5 "Ollama (local model server)"
$ollamaHost = Run-Py "from src import status; print(status.ollama_host())"

function Test-Ollama {
    try {
        $r = Invoke-RestMethod -Uri "$ollamaHost/api/tags" -TimeoutSec 3
        return @($r.models | ForEach-Object { $_.name })
    } catch { return $null }
}

$tags = Test-Ollama
if ($null -ne $tags) {
    Ok "Ollama is responding at $ollamaHost"
} else {
    $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($ollamaCmd) {
        Todo "Ollama is installed but not responding. Starting it in the background..."
        Start-Process -FilePath $ollamaCmd.Source -ArgumentList "serve" -WindowStyle Hidden
        for ($i = 0; $i -lt 10 -and $null -eq $tags; $i++) { Start-Sleep -Seconds 2; $tags = Test-Ollama }
        if ($null -eq $tags) { Stop-Setup "Ollama does not start." "Open it manually (Ollama icon or 'ollama serve' in another terminal) and run setup.bat again." }
        Ok "Ollama running at $ollamaHost"
    } else {
        Stop-Setup "Ollama is not installed." `
                   "Download it from https://ollama.com/download and install it (next, next). It stays in the system tray."
    }
}

# ----------------------------------------------------------------------------
# Paso 6: el modelo biomedico. El nombre se lee de config.py (unica fuente de
# verdad) para no tenerlo escrito dos veces. Solo este modelo es necesario para
# el producto; los de evaluacion no se descargan aqui.
# ----------------------------------------------------------------------------
Step 6 "Biomedical model (OpenBioLLM 8B)"
$model = Run-Py "import config; print(config.LLM_MODEL)"
if ($tags -contains $model) {
    Ok "Model $model already downloaded."
} else {
    Todo "Model $model is missing"
    Info "It takes about 4.9 GB; 10 to 30 minutes depending on your connection. It is stored in the Ollama folder."
    if (Ask-Continue "Download now with 'ollama pull'?") {
        & ollama pull $model
        if ($LASTEXITCODE -ne 0) { Stop-Setup "'ollama pull' finished with an error." "Check your connection and run setup.bat again." }
        $tags = Test-Ollama
        if ($tags -contains $model) { Ok "Model downloaded." } else { Stop-Setup "Ollama does not list the model after downloading it." "Run 'ollama list' to see what happened." }
    } else {
        Warn "Without the model MIA cannot answer. You can download it later with:  ollama pull $model"
    }
}

# ----------------------------------------------------------------------------
# Paso 7: MedCPT, el modelo de embeddings biomedico (dos encoders de NCBI).
# Se descarga de HuggingFace la primera vez que se usa; si no lo hacemos
# aqui, la primera pregunta en la app tardaria minutos sin explicacion.
# ----------------------------------------------------------------------------
Step 7 "Biomedical embeddings (MedCPT)"
$backend = Run-Py "import config; print(config.EMBEDDING_BACKEND)"
if ($backend -ne "medcpt") {
    Ok "Embedding backend '$backend': it will download itself on first use."
} else {
    $cached = Run-Py @"
import os, config
from huggingface_hub import constants
c = constants.HF_HUB_CACHE
ok = all(os.path.isdir(os.path.join(c, 'models--' + m.replace('/', '--'))) for m in (config.MEDCPT_QUERY_MODEL, config.MEDCPT_ARTICLE_MODEL))
print('yes' if ok else 'no'); print(c)
"@
    $cachedLines = $cached -split "`r?`n"
    if ($cachedLines[0] -eq "yes") {
        Ok "MedCPT is already in the HuggingFace cache ($($cachedLines[1]))."
    } else {
        Todo "MedCPT is not downloaded."
        Info "Two NCBI models, about 840 MB in total, from huggingface.co. They are stored in $($cachedLines[1])"
        if (Ask-Continue "Download now?") {
            $dim = & $venvPy -c "from src import embeddings; v = embeddings.embed_query('atopic dermatitis'); print(len(v))"
            if ($LASTEXITCODE -ne 0) { Stop-Setup "The MedCPT download failed." "Check your connection and run setup.bat again." }
            Ok "MedCPT downloaded and tested (vector of $("$dim".Trim().Split("`n")[-1]) dimensions)."
        } else {
            Warn "It will download itself on the first question (that answer will take a few extra minutes)."
        }
    }
}

# ----------------------------------------------------------------------------
# Paso 8: el corpus. NO se descarga nada aqui a proposito: el usuario elige
# la enfermedad en la app (pestana "Build corpus"), que descarga de PubMed y
# ClinicalTrials.gov y lo indexa. Aqui solo informamos de como esta.
# ----------------------------------------------------------------------------
Step 8 "Evidence corpus (you choose it in the app)"
$corpus = Run-Py "from src import status; import config; s = status.corpus_status(); print(s['chunks']); print(config.DISEASE)"
$corpusLines = $corpus -split "`r?`n"
$chunks = [int]$corpusLines[0]
if ($chunks -gt 0) {
    Ok "Active profile '$($corpusLines[1])' with $chunks chunks indexed."
} else {
    Todo "The vector database is empty (normal on a fresh install)."
    Info "When you open MIA, go to the 'Build corpus' tab, type the disease, click"
    Info "'Suggest drugs', pick the drugs and build the corpus (10-60 min)."
    $profiles = Get-ChildItem (Join-Path $root "domains") -Filter "*.json" | ForEach-Object { $_.BaseName }
    if ($profiles) { Info "Example profiles shipped with the repo: $($profiles -join ', ')" }
}

# ----------------------------------------------------------------------------
# Resumen final.
# ----------------------------------------------------------------------------
Write-Host ""
Write-Host "==================================================" -ForegroundColor DarkCyan
Write-Host "  Summary"                                          -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor DarkCyan
$tags = Test-Ollama
$modelOk = ($null -ne $tags) -and ($tags -contains $model)
Ok   "Python + virtual environment + dependencies"
Ok   ".env file"
if ($null -ne $tags) { Ok "Ollama running" } else { Todo "Ollama stopped" }
if ($modelOk)        { Ok "Biomedical model $model" } else { Todo "Biomedical model (ollama pull $model)" }
if ($chunks -gt 0)   { Ok "Corpus: $chunks chunks" } else { Todo "Corpus: empty -> Build corpus tab in the app" }
Write-Host ""
Info "To start MIA from now on: double-click run.bat"
Write-Host ""
# En modo -Yes (pruebas / automatizacion) NO arrancamos la app: run.ps1 se queda
# esperando con el servidor abierto y bloquearia el proceso que nos llamo.
if ($Yes) { exit 0 }
if (Ask-Continue "Start MIA now?") {
    & (Join-Path $root "run.ps1")
} else {
    Pause-Exit 0
}
