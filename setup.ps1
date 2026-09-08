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
    Write-Host "--- Paso $n de 8: $t" -ForegroundColor Cyan
}

function Ask-Continue($what) {
    # Devuelve $true si el usuario acepta (Enter o S), $false si escribe N.
    if ($Yes) { Info "$what  -> si (modo -Yes)"; return $true }
    $r = Read-Host "     $what  [Enter = si / N = no]"
    return ($r -eq "" -or $r -match '^[sSyY]')
}

function Pause-Exit($code) {
    if (-not $Yes) { Read-Host "`nPulsa Enter para salir" | Out-Null }
    exit $code
}

function Stop-Setup($why, $how) {
    # Se usa cuando falta un programa de terceros: explicamos y paramos.
    Write-Host ""
    Fail $why
    Info $how
    Info "Cuando lo tengas, vuelve a ejecutar setup.bat: los pasos ya hechos se saltan solos."
    Pause-Exit 1
}

function Run-Py($code) {
    # Ejecuta un trozo de Python con el venv y devuelve su salida (sin errores).
    # Es la forma de leer config.py (UNICA fuente de verdad) desde PowerShell.
    $out = & $venvPy -c $code
    if ($LASTEXITCODE -ne 0) { throw "Python fallo al ejecutar: $code" }
    return ($out | Out-String).Trim()
}

# ----------------------------------------------------------------------------
# Cabecera: que va a pasar y cuanto ocupa, ANTES de tocar nada.
# ----------------------------------------------------------------------------
Write-Host ""
Write-Host "==================================================" -ForegroundColor DarkCyan
Write-Host "  MIA - Instalacion guiada (primera vez)"           -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor DarkCyan
Write-Host ""
Info "Voy a preparar MIA en esta carpeta:"
Info "  $root"
Write-Host ""
Info "Que se descarga y donde queda (aprox.):"
Info "  - Librerias de Python  1,6 GB   -> carpeta .venv de este proyecto"
Info "  - Modelo OpenBioLLM    4,9 GB   -> carpeta de modelos de Ollama"
Info "  - Encoders MedCPT      0,8 GB   -> cache de HuggingFace de tu usuario"
Info "  - Corpus (PubMed)      0 MB     -> lo eliges tu en la app, al final"
Write-Host ""
Info "Antes de cada descarga grande te pregunto. Puedes cerrar la ventana"
Info "en cualquier momento y volver a ejecutar setup.bat mas tarde."
Write-Host ""
# Windows limita las rutas a 260 caracteres y torch instala archivos con rutas
# muy profundas dentro de .venv. Medido el 8-sep-2026: con el proyecto en una
# carpeta de ~150 caracteres, pip fallo con "WinError 206: nombre demasiado
# largo". Avisamos antes de perder 10 minutos descargando.
if ($root.Length -gt 90) {
    Warn "La ruta del proyecto tiene $($root.Length) caracteres. Windows limita las rutas a 260 y"
    Warn "la instalacion de las librerias puede fallar ('nombre demasiado largo')."
    Info "Recomendado: mueve la carpeta a una ruta corta, por ejemplo C:\dev\MIA, y vuelve a empezar."
    if (-not (Ask-Continue "Seguir aqui de todas formas?")) { Pause-Exit 0 }
}
if (-not (Ask-Continue "Empezamos?")) { Pause-Exit 0 }

# ----------------------------------------------------------------------------
# Paso 1: Python. Buscamos primero el lanzador "py" (lo trae el instalador de
# python.org); si no, un "python" en el PATH que NO sea el alias falso de la
# Microsoft Store (ese abre la tienda en vez de ejecutar nada).
# ----------------------------------------------------------------------------
Step 1 "Python 3.11 o superior"

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
    Ok "Ya hay un entorno virtual; no hace falta buscar Python."
} else {
    $pyExe = Find-Python
    if (-not $pyExe) {
        $store = Get-Command python -ErrorAction SilentlyContinue
        if ($store -and $store.Source -match "WindowsApps") {
            Warn "El 'python' de tu equipo es el alias de la Microsoft Store, no un Python real."
        }
        Stop-Setup "No encuentro Python 3.11 o superior." `
                   "Instalalo desde https://www.python.org/downloads/ (marca 'Add python.exe to PATH')."
    }
    $ver = & $pyExe -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
    Ok "Python $ver en $pyExe"
}

# ----------------------------------------------------------------------------
# Paso 2: entorno virtual. Una copia privada de Python dentro del proyecto,
# con exactamente las librerias que MIA necesita. No se sube a git: hay que
# crearla en cada equipo. Pesa 1,6 GB una vez instaladas las dependencias.
# ----------------------------------------------------------------------------
Step 2 "Entorno virtual (.venv)"
if (Test-Path $venvPy) {
    Ok "Entorno virtual encontrado."
} else {
    Todo "No existe. Lo creo con:  python -m venv .venv  (unos segundos)"
    & $pyExe -m venv (Join-Path $root ".venv")
    if (-not (Test-Path $venvPy)) { Stop-Setup "No se pudo crear el entorno virtual." "Revisa el error de arriba." }
    Ok "Entorno virtual creado."
}

# ----------------------------------------------------------------------------
# Paso 3: dependencias. requirements.lock.txt fija TODAS las versiones (135
# paquetes) para reproducir el entorno con el que se probo MIA. Es el paso
# mas largo por torch (el motor de los embeddings), que pesa mas de 1 GB.
# ----------------------------------------------------------------------------
Step 3 "Dependencias de Python"
$missingCheck = "import importlib.util as u; mods=['streamlit','chromadb','transformers','torch','ollama','sentence_transformers','dotenv','requests']; print(','.join(m for m in mods if u.find_spec(m) is None))"
$missing = Run-Py $missingCheck
if ($missing -eq "") {
    Ok "Dependencias instaladas."
} else {
    Todo "Faltan paquetes: $missing"
    Info "Voy a instalar requirements.lock.txt: unos 1,6 GB, entre 5 y 15 minutos segun tu conexion."
    if (Ask-Continue "Instalar ahora?") {
        & $venvPy -m pip install --upgrade pip --quiet
        & $venvPy -m pip install -r (Join-Path $root "requirements.lock.txt")
        if ($LASTEXITCODE -ne 0) { Stop-Setup "pip termino con error." "Lee el mensaje de arriba; suele ser red o falta de espacio en disco." }
        $missing = Run-Py $missingCheck
        if ($missing -ne "") { Stop-Setup "Tras instalar siguen faltando: $missing" "Vuelve a ejecutar setup.bat; si persiste, abre una Issue en GitHub." }
        Ok "Dependencias instaladas."
    } else {
        Stop-Setup "Sin las dependencias MIA no puede arrancar." "Vuelve a ejecutar setup.bat cuando quieras instalarlas."
    }
}
Info "Comprobacion del entorno (check_setup.py):"
& $venvPy (Join-Path $root "check_setup.py")

# ----------------------------------------------------------------------------
# Paso 4: archivo .env. Guarda claves de acceso a servicios externos (por eso
# no se sube a git). Para usar MIA NO hace falta rellenar nada: se crea vacio
# a partir de la plantilla y solo importa si quieres una clave de PubMed.
# ----------------------------------------------------------------------------
Step 4 "Archivo .env (opcional, se crea vacio)"
$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    Ok ".env ya existe; no lo toco."
} else {
    Copy-Item (Join-Path $root ".env.example") $envFile
    Ok ".env creado a partir de .env.example (no hace falta editarlo para empezar)."
}

# ----------------------------------------------------------------------------
# Paso 5: Ollama. Es el programa que ejecuta el modelo de lenguaje en local.
# Tres situaciones: responde (bien) / instalado pero parado (lo arrancamos) /
# no instalado (enlace y paramos: lo instala el usuario).
# ----------------------------------------------------------------------------
Step 5 "Ollama (servidor local del modelo)"
$ollamaHost = Run-Py "from src import status; print(status.ollama_host())"

function Test-Ollama {
    try {
        $r = Invoke-RestMethod -Uri "$ollamaHost/api/tags" -TimeoutSec 3
        return @($r.models | ForEach-Object { $_.name })
    } catch { return $null }
}

$tags = Test-Ollama
if ($null -ne $tags) {
    Ok "Ollama responde en $ollamaHost"
} else {
    $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($ollamaCmd) {
        Todo "Ollama esta instalado pero no responde. Lo arranco en segundo plano..."
        Start-Process -FilePath $ollamaCmd.Source -ArgumentList "serve" -WindowStyle Hidden
        for ($i = 0; $i -lt 10 -and $null -eq $tags; $i++) { Start-Sleep -Seconds 2; $tags = Test-Ollama }
        if ($null -eq $tags) { Stop-Setup "Ollama no arranca." "Abrelo a mano (icono de Ollama o 'ollama serve' en otra terminal) y vuelve a ejecutar setup.bat." }
        Ok "Ollama en marcha en $ollamaHost"
    } else {
        Stop-Setup "Ollama no esta instalado." `
                   "Descargalo de https://ollama.com/download e instalalo (siguiente, siguiente). Se queda en la bandeja del sistema."
    }
}

# ----------------------------------------------------------------------------
# Paso 6: el modelo biomedico. El nombre se lee de config.py (unica fuente de
# verdad) para no tenerlo escrito dos veces. Solo este modelo es necesario para
# el producto; los de evaluacion no se descargan aqui.
# ----------------------------------------------------------------------------
Step 6 "Modelo biomedico (OpenBioLLM 8B)"
$model = Run-Py "import config; print(config.LLM_MODEL)"
if ($tags -contains $model) {
    Ok "Modelo $model ya descargado."
} else {
    Todo "Falta el modelo $model"
    Info "Ocupa unos 4,9 GB; entre 10 y 30 minutos segun tu conexion. Se guarda en la carpeta de Ollama."
    if (Ask-Continue "Descargar ahora con 'ollama pull'?") {
        & ollama pull $model
        if ($LASTEXITCODE -ne 0) { Stop-Setup "'ollama pull' termino con error." "Comprueba la conexion y vuelve a ejecutar setup.bat." }
        $tags = Test-Ollama
        if ($tags -contains $model) { Ok "Modelo descargado." } else { Stop-Setup "Ollama no lista el modelo tras descargarlo." "Ejecuta 'ollama list' para ver que ha pasado." }
    } else {
        Warn "Sin el modelo MIA no puede responder. Podras descargarlo luego con:  ollama pull $model"
    }
}

# ----------------------------------------------------------------------------
# Paso 7: MedCPT, el modelo de embeddings biomedico (dos encoders de NCBI).
# Se descarga de HuggingFace la primera vez que se usa; si no lo hacemos
# aqui, la primera pregunta en la app tardaria minutos sin explicacion.
# ----------------------------------------------------------------------------
Step 7 "Embeddings biomedicos (MedCPT)"
$backend = Run-Py "import config; print(config.EMBEDDING_BACKEND)"
if ($backend -ne "medcpt") {
    Ok "Backend de embeddings '$backend': se descargara solo al primer uso."
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
        Ok "MedCPT ya esta en la cache de HuggingFace ($($cachedLines[1]))."
    } else {
        Todo "MedCPT no esta descargado."
        Info "Son dos modelos de NCBI, unos 840 MB en total, desde huggingface.co. Quedan en $($cachedLines[1])"
        if (Ask-Continue "Descargar ahora?") {
            $dim = & $venvPy -c "from src import embeddings; v = embeddings.embed_query('atopic dermatitis'); print(len(v))"
            if ($LASTEXITCODE -ne 0) { Stop-Setup "La descarga de MedCPT fallo." "Comprueba la conexion y vuelve a ejecutar setup.bat." }
            Ok "MedCPT descargado y probado (vector de $("$dim".Trim().Split("`n")[-1]) dimensiones)."
        } else {
            Warn "Se descargara sola en la primera pregunta (esa respuesta tardara unos minutos mas)."
        }
    }
}

# ----------------------------------------------------------------------------
# Paso 8: el corpus. NO se descarga nada aqui a proposito: el usuario elige
# la enfermedad en la app (pestana "Build corpus"), que descarga de PubMed y
# ClinicalTrials.gov y lo indexa. Aqui solo informamos de como esta.
# ----------------------------------------------------------------------------
Step 8 "Corpus de evidencia (lo eliges en la app)"
$corpus = Run-Py "from src import status; import config; s = status.corpus_status(); print(s['chunks']); print(config.DISEASE)"
$corpusLines = $corpus -split "`r?`n"
$chunks = [int]$corpusLines[0]
if ($chunks -gt 0) {
    Ok "Perfil activo '$($corpusLines[1])' con $chunks fragmentos indexados."
} else {
    Todo "La base vectorial esta vacia (es lo normal en una instalacion nueva)."
    Info "Al abrir MIA, ve a la pestana 'Build corpus', escribe la enfermedad, pulsa"
    Info "'Suggest drugs', elige los farmacos y construye el corpus (10-60 min)."
    $profiles = Get-ChildItem (Join-Path $root "domains") -Filter "*.json" | ForEach-Object { $_.BaseName }
    if ($profiles) { Info "Perfiles de ejemplo que vienen en el repo: $($profiles -join ', ')" }
}

# ----------------------------------------------------------------------------
# Resumen final.
# ----------------------------------------------------------------------------
Write-Host ""
Write-Host "==================================================" -ForegroundColor DarkCyan
Write-Host "  Resumen"                                          -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor DarkCyan
$tags = Test-Ollama
$modelOk = ($null -ne $tags) -and ($tags -contains $model)
Ok   "Python + entorno virtual + dependencias"
Ok   "Archivo .env"
if ($null -ne $tags) { Ok "Ollama en marcha" } else { Todo "Ollama parado" }
if ($modelOk)        { Ok "Modelo biomedico $model" } else { Todo "Modelo biomedico (ollama pull $model)" }
if ($chunks -gt 0)   { Ok "Corpus: $chunks fragmentos" } else { Todo "Corpus: vacio -> pestana Build corpus en la app" }
Write-Host ""
Info "Para arrancar MIA a partir de ahora: doble clic en run.bat"
Write-Host ""
# En modo -Yes (pruebas / automatizacion) NO arrancamos la app: run.ps1 se queda
# esperando con el servidor abierto y bloquearia el proceso que nos llamo.
if ($Yes) { exit 0 }
if (Ask-Continue "Arrancar MIA ahora?") {
    & (Join-Path $root "run.ps1")
} else {
    Pause-Exit 0
}
