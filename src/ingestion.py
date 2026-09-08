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
            print(f"   [warning] HTTP {resp.status_code}; retry {intento}/{max_retries} "
                  f"in {espera:.0f}s")
            time.sleep(espera)
            continue
        # Error no recuperable: que explote con un mensaje claro.
        resp.raise_for_status()
    # Si agotamos los reintentos:
    raise RuntimeError(f"Request failed after {max_retries} retries: {method_kwargs}")


def _get_json(url, params):
    """GET que devuelve JSON (para ClinicalTrials.gov)."""
    resp = _request_with_retry({"url": url, "params": params})
    return resp.json()


def _get_text(url, params):
    """GET que devuelve texto crudo (para el XML de PubMed)."""
    resp = _request_with_retry({"url": url, "params": params})
    resp.encoding = "utf-8"
    return resp.text


# PubMed acepta como mucho unos cientos de PMID por petición GET (el límite real es
# la longitud de la URL). Para "sin tope" (miles de abstracts) descargamos por
# LOTES y fusionamos los <PubmedArticle> en un único <PubmedArticleSet>, que es
# lo que processing.py espera leer de cada archivo bronze.
_EFETCH_BATCH = 200
_PMID_RETMAX_NOCAP = 10000   # tope de esearch sin usar el historial de NCBI


def _efetch_pubmed_xml(idlist, api_key=None):
    """Descarga los abstracts de `idlist` en lotes y devuelve UN XML válido."""
    cuerpos = []
    for i in range(0, len(idlist), _EFETCH_BATCH):
        lote = idlist[i:i + _EFETCH_BATCH]
        params = {"db": "pubmed", "id": ",".join(lote), "rettype": "abstract", "retmode": "xml"}
        if api_key:
            params["api_key"] = api_key
        xml_text = _get_text(f"{config.PUBMED_EUTILS}/efetch.fcgi", params)
        time.sleep(0.34)
        ini, fin = xml_text.find("<PubmedArticleSet>"), xml_text.rfind("</PubmedArticleSet>")
        cuerpos.append(xml_text[ini + len("<PubmedArticleSet>"):fin] if ini >= 0 and fin > ini else "")
        if len(idlist) > _EFETCH_BATCH:
            print(f"      PubMed: {min(i + _EFETCH_BATCH, len(idlist))}/{len(idlist)} abstracts")
    return "<PubmedArticleSet>" + "".join(cuerpos) + "</PubmedArticleSet>"


def pubmed_disease_clause(disease_query):
    """Acota la ENFERMEDAD en PubMed a su descriptor MeSH o al título.

    Por qué: buscar la enfermedad como texto libre trae homónimos. Medido el
    4-sep-2026 con 'abemaciclib AND Retinoblastoma': PubMed devolvía ensayos de
    cáncer de mama "Rb-positivo" (la PROTEÍNA del retinoblastoma, no la enfermedad),
    y la respuesta acabó hablando de mama. Con `[MeSH Terms]` PubMed usa su
    indexación por tema (que distingue 'Retinoblastoma' de 'Retinoblastoma Protein');
    `[Title]` recupera lo muy reciente que aún no tiene MeSH asignado.
    Acepta 'X' o '(X OR Y)' (config.DISEASE_QUERY) y devuelve
    '(X[MeSH Terms] OR X[Title] OR Y[MeSH Terms] OR Y[Title])'.
    """
    terminos = [t.strip() for t in re.split(r"\s+OR\s+", disease_query.strip("() "))
                if t.strip()]
    partes = []
    for t in terminos:
        partes += [f'"{t}"[MeSH Terms]', f'"{t}"[Title]']
    return "(" + " OR ".join(partes) + ")"


def _esearch_ids(term, retmax, api_key=None):
    """esearch → lista de PMID (respetando el rate-limit)."""
    params = {"db": "pubmed", "term": term, "retmax": retmax, "retmode": "json"}
    if api_key:
        params["api_key"] = api_key
    data = _get_json(f"{config.PUBMED_EUTILS}/esearch.fcgi", params)
    time.sleep(0.34)
    return data.get("esearchresult", {}).get("idlist", [])


def _retmax(max_results):
    """retmax para esearch: el tope pedido, o el máximo práctico si es 'sin tope' (None)."""
    return _PMID_RETMAX_NOCAP if max_results is None else max_results


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
    # Pedimos como mucho 'max_results' (None = SIN TOPE: todo lo que haya, paginando
    # de 1.000 en 1.000, que es el máximo de la API). pageSize no supera el tope
    # para no descargar de más en la última página.
    page_size = 1000 if max_results is None else min(max_results, 1000)

    while max_results is None or len(estudios) < max_results:
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
    if max_results is not None:
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
    # Enfermedad acotada por MeSH/título (evita homónimos); si con eso no hay
    # nada (nombre sin descriptor MeSH), se repite como texto libre.
    idlist = _esearch_ids(f"{drug} AND {pubmed_disease_clause(disease)}",
                          _retmax(max_results), api_key)
    if not idlist:
        idlist = _esearch_ids(f"{drug} AND {disease}", _retmax(max_results), api_key)

    if not idlist:
        # Aun sin resultados guardamos un XML vacío válido, para que processing
        # no tenga que distinguir "no descargado" de "descargado y vacío".
        destino = config.BRONZE_DIR / f"pubmed_{drug}.xml"
        destino.write_text("<PubmedArticleSet></PubmedArticleSet>", encoding="utf-8")
        return []

    # --- Paso 2: efetch (dame el contenido de esos PMIDs), por lotes ---
    xml_text = _efetch_pubmed_xml(idlist, api_key)

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


def search_clinical_trials(term, max_results=20, cond=None):
    """Busca ensayos en ClinicalTrials.gov por TEXTO LIBRE (query.term).

    `cond` (opcional): acota además por CONDICIÓN (query.cond), el campo
    estructurado de enfermedad del registro. Lo usa el Scout para que un fármaco
    buscado por texto libre no traiga ensayos de otra patología.

    Guarda en data/bronze/ct_scout_<slug>.json y devuelve la ruta del archivo
    (o None si no hubo resultados).
    """
    _ensure_bronze_dir()
    estudios = []
    page_token = None
    page_size = 1000 if max_results is None else min(max_results, 1000)

    while max_results is None or len(estudios) < max_results:
        params = {"query.term": term, "pageSize": page_size, "format": "json"}
        if cond:
            params["query.cond"] = cond
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

    if max_results is not None:
        estudios = estudios[:max_results]
    if not estudios:
        return None

    payload = {"source": "clinicaltrials", "drug": term, "count": len(estudios),
               "studies": estudios}
    destino = config.BRONZE_DIR / f"ct_scout_{_slugify(term)}.json"
    destino.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino


def search_pubmed(term, max_results=20, fallback_term=None):
    """Busca abstracts en PubMed por TEXTO LIBRE.

    `fallback_term` (opcional): segunda búsqueda si la primera no devuelve nada
    (p. ej. la versión sin acotar por MeSH).

    Guarda en data/bronze/pubmed_scout_<slug>.xml y devuelve la ruta del archivo
    (o None si no hubo resultados).
    """
    _ensure_bronze_dir()
    api_key = _load_api_key()

    idlist = _esearch_ids(term, _retmax(max_results), api_key)
    if not idlist and fallback_term:
        idlist = _esearch_ids(fallback_term, _retmax(max_results), api_key)
    if not idlist:
        return None

    xml_text = _efetch_pubmed_xml(idlist, api_key)

    destino = config.BRONZE_DIR / f"pubmed_scout_{_slugify(term)}.xml"
    destino.write_text(xml_text, encoding="utf-8")
    return destino


# --------------------------------------------------------------------------
# Ejecución directa: descarga TODO (todos los fármacos, ambas fuentes)
# --------------------------------------------------------------------------

def run(max_results=50):
    """Descarga ambas fuentes para todos los fármacos de config.DRUGS del perfil
    (`max_results=None` = sin tope: todo lo que devuelvan las APIs)
    de dominio activo, y después las búsquedas libres extra del perfil
    (config.EXTRA_QUERIES: p. ej. un mecanismo — "IL-17 inhibitor" — para traer
    evidencia que no nombra ningún fármaco concreto)."""
    print("=" * 60)
    print(" MIA · Phase 1 — Data ingestion (bronze)")
    print("=" * 60)
    total_ct, total_pm = 0, 0
    for drug in tqdm(config.DRUGS, desc="Drugs"):
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
            p_ct = search_clinical_trials(extra, max_results, cond=config.DISEASE_QUERY)
            p_pm = search_pubmed(f"{extra} AND {pubmed_disease_clause(config.DISEASE_QUERY)}",
                                 max_results, fallback_term=term)
            print(f"   [extra] '{extra}': CT {'yes' if p_ct else 'no'} · PubMed {'yes' if p_pm else 'no'}")
        except Exception as e:
            print(f"   [error] extra search '{extra}': {e}")
    print("-" * 60)
    print(f"Clinical trials downloaded: {total_ct}")
    print(f"PubMed abstracts downloaded: {total_pm}")
    print(f"Raw files in: {config.BRONZE_DIR}")


if __name__ == "__main__":
    run()
