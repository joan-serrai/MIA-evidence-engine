"""
src/ingestion.py — FASE 1: Descargar evidencia científica.  [Módulos 2-3: Datos]

Objetivo de este archivo: traer publicaciones y ensayos clínicos sobre la
enfermedad y los fármacos de config.py, y guardarlos CRUDOS en data/bronze/.

Dos fuentes, ambas públicas y gratuitas:
  - ClinicalTrials.gov (API v2, JSON)  → ensayos clínicos
  - PubMed (NCBI E-utilities, XML)      → abstracts de artículos

Filosofía "bronze": aquí NO limpiamos ni transformamos nada. Guardamos la
respuesta tal cual llega de la API. Así, si mañana cambiamos cómo troceamos
el texto, no hace falta volver a descargar (que es lo lento y lo que depende
de internet). La limpieza vive en processing.py.
"""

import sys
import re
import json
import time
import os

import requests
from dotenv import load_dotenv
from tqdm import tqdm

# Importamos la configuración central. Este archivo puede ejecutarse de dos
# formas: como módulo (`from src import ingestion`) o directamente
# (`python src/ingestion.py`). El try/except cubre ambos casos.
try:
    from .. import config  # cuando se importa como paquete
except (ImportError, ValueError):
    sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    import config

# En Windows la consola usa por defecto cp1252 y se rompe con algunos
# caracteres. Forzamos UTF-8, igual que en check_setup.py.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# --------------------------------------------------------------------------
# Helpers compartidos
# --------------------------------------------------------------------------

# Una única "sesión" de requests reutiliza la conexión TCP entre llamadas:
# es más rápido que abrir una conexión nueva por cada petición.
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "MIA-Capstone/1.0 (research; local)"})


def _load_api_key():
    """Lee NCBI_API_KEY del archivo .env si existe. Funciona sin ella.

    Con clave, PubMed permite ~10 peticiones/seg; sin clave, ~3/seg. No es
    obligatoria para este proyecto.
    """
    load_dotenv()  # carga las variables del .env al entorno (si hay .env)
    return os.getenv("NCBI_API_KEY") or None


def _request_with_retry(method_kwargs, *, max_retries=4, base_wait=1.0):
    """Hace una petición GET reintentando ante errores transitorios.

    Reintenta si la API responde 429 (demasiadas peticiones) o 5xx (error del
    servidor), esperando cada vez un poco más (backoff exponencial). Para los
    demás errores (p. ej. 404) no tiene sentido reintentar: lanzamos el error.
    """
    for intento in range(1, max_retries + 1):
        resp = _SESSION.get(**method_kwargs, timeout=30)
        if resp.status_code == 200:
            return resp
        if resp.status_code == 429 or resp.status_code >= 500:
            espera = base_wait * (2 ** (intento - 1))  # 1s, 2s, 4s, 8s...
            print(f"   [aviso] HTTP {resp.status_code}; reintento {intento}/{max_retries} "
                  f"en {espera:.0f}s")
            time.sleep(espera)
            continue
        # Error no recuperable: que explote con un mensaje claro.
        resp.raise_for_status()
    # Si agotamos los reintentos:
    raise RuntimeError(f"La petición falló tras {max_retries} reintentos: {method_kwargs}")


def _get_json(url, params):
    """GET que devuelve JSON (para ClinicalTrials.gov)."""
    resp = _request_with_retry({"url": url, "params": params})
    return resp.json()


def _get_text(url, params):
    """GET que devuelve texto crudo (para el XML de PubMed)."""
    resp = _request_with_retry({"url": url, "params": params})
    resp.encoding = "utf-8"
    return resp.text


def _ensure_bronze_dir():
    """Crea data/bronze/ si no existe (no falla si ya está)."""
    config.BRONZE_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Fuente 1: ClinicalTrials.gov  (API v2, JSON)
# --------------------------------------------------------------------------

def fetch_clinical_trials(disease, drug, max_results=50):
    """Descarga ensayos de ClinicalTrials.gov para (enfermedad + fármaco).

    Usa la API v2:
      - query.cond  → condición/enfermedad
      - query.intr  → intervención/fármaco
      - pageSize    → cuántos por página (máx 1000)
      - pageToken   → cursor para pedir la siguiente página

    Guarda el JSON CRUDO en data/bronze/ct_<drug>.json y devuelve la lista de
    estudios descargados.
    """
    _ensure_bronze_dir()

    estudios = []
    page_token = None
    # Pedimos como mucho 'max_results'. pageSize no debería superar ese límite
    # para no descargar de más en la última página.
    page_size = min(max_results, 100)

    while len(estudios) < max_results:
        params = {
            "query.cond": disease,
            "query.intr": drug,
            "pageSize": page_size,
            "format": "json",
            "countTotal": "true",
        }
        if page_token:
            params["pageToken"] = page_token

        data = _get_json(config.CLINICALTRIALS_API, params)
        lote = data.get("studies", [])
        if not lote:
            break  # no hay (más) resultados
        estudios.extend(lote)

        page_token = data.get("nextPageToken")
        if not page_token:
            break  # era la última página

    # Recortamos por si la última página nos pasó del máximo.
    estudios = estudios[:max_results]

    # Envolvemos los datos crudos con un poco de "procedencia" (de dónde y para
    # qué se descargaron). Esto ayuda a depurar después.
    payload = {
        "source": "clinicaltrials",
        "disease": disease,
        "drug": drug,
        "count": len(estudios),
        "studies": estudios,
    }
    destino = config.BRONZE_DIR / f"ct_{drug}.json"
    destino.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return estudios


# --------------------------------------------------------------------------
# Fuente 2: PubMed  (NCBI E-utilities, XML)
# --------------------------------------------------------------------------

def fetch_pubmed(disease, drug, max_results=50):
    """Descarga abstracts de PubMed para (enfermedad + fármaco).

    Flujo en dos pasos (típico de E-utilities):
      1. esearch → busca y devuelve una lista de PMIDs (identificadores).
      2. efetch  → baja el contenido (abstracts) de esos PMIDs en XML.

    Guarda el XML CRUDO en data/bronze/pubmed_<drug>.xml y devuelve la lista
    de PMIDs encontrados.
    """
    _ensure_bronze_dir()
    api_key = _load_api_key()

    # --- Paso 1: esearch (¿qué artículos existen?) ---
    esearch_params = {
        "db": "pubmed",
        "term": f"{drug} AND {disease}",
        "retmax": max_results,
        "retmode": "json",
    }
    if api_key:
        esearch_params["api_key"] = api_key

    esearch_data = _get_json(f"{config.PUBMED_EUTILS}/esearch.fcgi", esearch_params)
    idlist = esearch_data.get("esearchresult", {}).get("idlist", [])

    # Respetamos el límite de peticiones de NCBI (3/seg sin clave).
    time.sleep(0.34)

    if not idlist:
        # Aun sin resultados guardamos un XML vacío válido, para que processing
        # no tenga que distinguir "no descargado" de "descargado y vacío".
        destino = config.BRONZE_DIR / f"pubmed_{drug}.xml"
        destino.write_text("<PubmedArticleSet></PubmedArticleSet>", encoding="utf-8")
        return []

    # --- Paso 2: efetch (dame el contenido de esos PMIDs) ---
    efetch_params = {
        "db": "pubmed",
        "id": ",".join(idlist),
        "rettype": "abstract",
        "retmode": "xml",
    }
    if api_key:
        efetch_params["api_key"] = api_key

    xml_text = _get_text(f"{config.PUBMED_EUTILS}/efetch.fcgi", efetch_params)
    time.sleep(0.34)

    destino = config.BRONZE_DIR / f"pubmed_{drug}.xml"
    destino.write_text(xml_text, encoding="utf-8")
    return idlist


# --------------------------------------------------------------------------
# Búsqueda por TEXTO LIBRE  (la usa el agente Scout en la Fase 3)
# --------------------------------------------------------------------------
# Las funciones de arriba buscan por (enfermedad + fármaco) estructurado, que es
# perfecto para precargar el corpus conocido. Pero el Scout recibe preguntas
# sobre cosas que NO están en config.DRUGS, así que necesita buscar por texto
# libre. Estas dos funciones hacen justo eso, reutilizando los mismos helpers.

def _slugify(texto):
    """Convierte un texto en un nombre de archivo seguro (p. ej. para bronze)."""
    slug = re.sub(r"[^a-z0-9]+", "_", texto.lower()).strip("_")
    return slug[:40] or "scout"


def search_clinical_trials(term, max_results=20):
    """Busca ensayos en ClinicalTrials.gov por TEXTO LIBRE (query.term).

    Guarda en data/bronze/ct_scout_<slug>.json y devuelve la ruta del archivo
    (o None si no hubo resultados).
    """
    _ensure_bronze_dir()
    estudios = []
    page_token = None
    page_size = min(max_results, 100)

    while len(estudios) < max_results:
        params = {"query.term": term, "pageSize": page_size, "format": "json"}
        if page_token:
            params["pageToken"] = page_token
        data = _get_json(config.CLINICALTRIALS_API, params)
        lote = data.get("studies", [])
        if not lote:
            break
        estudios.extend(lote)
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    estudios = estudios[:max_results]
    if not estudios:
        return None

    payload = {"source": "clinicaltrials", "drug": term, "count": len(estudios),
               "studies": estudios}
    destino = config.BRONZE_DIR / f"ct_scout_{_slugify(term)}.json"
    destino.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino


def search_pubmed(term, max_results=20):
    """Busca abstracts en PubMed por TEXTO LIBRE.

    Guarda en data/bronze/pubmed_scout_<slug>.xml y devuelve la ruta del archivo
    (o None si no hubo resultados).
    """
    _ensure_bronze_dir()
    api_key = _load_api_key()

    esearch_params = {"db": "pubmed", "term": term, "retmax": max_results,
                      "retmode": "json"}
    if api_key:
        esearch_params["api_key"] = api_key
    esearch_data = _get_json(f"{config.PUBMED_EUTILS}/esearch.fcgi", esearch_params)
    idlist = esearch_data.get("esearchresult", {}).get("idlist", [])
    time.sleep(0.34)
    if not idlist:
        return None

    efetch_params = {"db": "pubmed", "id": ",".join(idlist), "rettype": "abstract",
                     "retmode": "xml"}
    if api_key:
        efetch_params["api_key"] = api_key
    xml_text = _get_text(f"{config.PUBMED_EUTILS}/efetch.fcgi", efetch_params)
    time.sleep(0.34)

    destino = config.BRONZE_DIR / f"pubmed_scout_{_slugify(term)}.xml"
    destino.write_text(xml_text, encoding="utf-8")
    return destino


# --------------------------------------------------------------------------
# Ejecución directa: descarga TODO (todos los fármacos, ambas fuentes)
# --------------------------------------------------------------------------

def run(max_results=50):
    """Descarga ambas fuentes para todos los fármacos de config.DRUGS del perfil
    de dominio activo, y después las búsquedas libres extra del perfil
    (config.EXTRA_QUERIES: p. ej. un mecanismo — "IL-17 inhibitor" — para traer
    evidencia que no nombra ningún fármaco concreto)."""
    print("=" * 60)
    print(" MIA · Fase 1 — Ingesta de datos (bronze)")
    print("=" * 60)
    total_ct, total_pm = 0, 0
    for drug in tqdm(config.DRUGS, desc="Fármacos"):
        try:
            ct = fetch_clinical_trials(config.DISEASE_QUERY, drug, max_results)
            total_ct += len(ct)
        except Exception as e:
            print(f"   [error] ClinicalTrials/{drug}: {e}")
        try:
            pm = fetch_pubmed(config.DISEASE_QUERY, drug, max_results)
            total_pm += len(pm)
        except Exception as e:
            print(f"   [error] PubMed/{drug}: {e}")
    for extra in config.EXTRA_QUERIES:
        term = f"{extra} AND {config.DISEASE_QUERY}"
        try:
            p_ct = search_clinical_trials(term, max_results)
            p_pm = search_pubmed(term, max_results)
            print(f"   [extra] '{extra}': CT {'sí' if p_ct else 'no'} · PubMed {'sí' if p_pm else 'no'}")
        except Exception as e:
            print(f"   [error] búsqueda extra '{extra}': {e}")
    print("-" * 60)
    print(f"Ensayos clínicos descargados: {total_ct}")
    print(f"Abstracts PubMed descargados: {total_pm}")
    print(f"Archivos crudos en: {config.BRONZE_DIR}")


if __name__ == "__main__":
    run()
