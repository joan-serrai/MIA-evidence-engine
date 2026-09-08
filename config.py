"""
config.py — Parámetros centrales del proyecto MIA.

Todo lo que pueda cambiar (enfermedad, fármacos, modelos, rutas, umbrales)
vive AQUÍ, en un solo sitio. Así el resto del código no tiene valores
"a fuego" y podemos reusar MIA para otra patología cambiando solo la configuración.

NOVEDAD (3-sep-2026) — PERFILES DE DOMINIO. Hasta ahora la enfermedad y los
fármacos estaban escritos en este archivo, y "cambiar de patología" significaba
editar código. Ahora cada patología es un PERFIL en `domains/<slug>.json`
(enfermedad, sinónimos, fármacos por clase, mecanismos, endpoints de eficacia,
preguntas de ejemplo). Este archivo carga el perfil ACTIVO y expone sus valores
con los MISMOS nombres de siempre (DISEASE, DRUGS, ALL_DRUGS, …), así que el
resto del código no se entera del cambio.

¿Cuál es el perfil activo? Por orden de prioridad:
  1. la variable de entorno MIA_DOMAIN (también vale ponerla en .env),
  2. el archivo `domains/active.txt` (lo escribe `build_corpus.py --activate`
     o el selector de la interfaz),
  3. el perfil por defecto: `atopic_dermatitis` (el caso original del capstone).

Para crear un perfil nuevo y descargar su corpus: `python build_corpus.py --help`.
"""

import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

# --------------------------------------------------------------------------
# 1) Rutas base (patrón "medallón" simplificado)
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
CHROMA_DIR = DATA_DIR / "chroma"   # base vectorial (ChromaDB); UNA carpeta para todos los dominios
DOMAINS_DIR = BASE_DIR / "domains" # perfiles de dominio (uno por patología)
DEFAULT_DOMAIN = "atopic_dermatitis"
# Slug "virtual" del perfil VACÍO: una instalación nueva no trae ningún
# domains/<slug>.json (8-sep-2026: MIA es genérica hasta que el usuario construye
# su primer corpus). Con él, DISEASE == "" y el resto de listas quedan vacías; la
# app lo detecta y manda a la pestaña "Build corpus" en vez de suponer una patología.
NO_DOMAIN = "none"
ACTIVE_DOMAIN_FILE = DOMAINS_DIR / "active.txt"

# El .env se carga por ruta explícita (no por cwd) para que funcione desde
# cualquier directorio. Aquí viven OLLAMA_HOST, NCBI_API_KEY, OPENAI_API_KEY y,
# opcionalmente, MIA_DOMAIN.
load_dotenv(BASE_DIR / ".env")

# --------------------------------------------------------------------------
# 2) Modelos
# --------------------------------------------------------------------------
# Backend de embeddings (convierte texto en vectores). Se puede cambiar aquí:
#   - "medcpt"                → MedCPT (biomédico, 2 torres, 768 dim). Producto MIA.
#   - "openai"                → text-embedding-3-small (generalista, API, 1536 dim).
#                               = "línea Centivence"; SOLO para la evaluación comparativa
#                               (rompe el "100% local" → no usar como producto).
#   - "sentence-transformers" → bge-small (generalista local, 384 dim). Ligero.
# El código de embeddings vive en src/embeddings.py y elige según este valor.
EMBEDDING_BACKEND = "medcpt"

# Modelo bge (se usa si EMBEDDING_BACKEND == "sentence-transformers"). 384 dim, local.
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

# MedCPT (NCBI): modelo ASIMÉTRICO entrenado con 255M pares consulta–artículo de
# PubMed. Dos encoders distintos: uno para PREGUNTAS y otro para ARTÍCULOS (768 dim).
MEDCPT_QUERY_MODEL = "ncbi/MedCPT-Query-Encoder"      # embebe preguntas
MEDCPT_ARTICLE_MODEL = "ncbi/MedCPT-Article-Encoder"  # embebe documentos/chunks
MEDCPT_QUERY_MAXLEN = 64     # tope de tokens de una consulta (según la model card)
MEDCPT_ARTICLE_MAXLEN = 512  # tope de tokens de un artículo/chunk

# OpenAI (se usa si EMBEDDING_BACKEND == "openai"). Modelo SIMÉTRICO (mismo modelo
# para pregunta y documento), 1536 dim, vectores ya normalizados → distancia coseno.
# La clave se lee de OPENAI_API_KEY (.env). Coste ínfimo (~céntimos indexar el corpus).
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"

# LLM biomédico local servido por Ollama.
# Tag exacto del modelo descargado en Ollama (OpenBioLLM 8B cuantizado Q4_K_M).
LLM_MODEL = "koesn/llama3-openbiollm-8b:q4_K_M"
# Modelo generalista para la comparativa (Fase 4). Es la BASE sobre la que se
# afinó OpenBioLLM → mismo tamaño/arquitectura = comparación justa (aísla el
# efecto del fine-tuning biomédico).
LLM_GENERALIST = "llama3:8b"
# Juez NEUTRAL para puntuar la rúbrica (Fase 4). Un 3er modelo distinto evita
# que un modelo se juzgue a sí mismo (conflicto de interés).
LLM_JUDGE = "qwen2.5:7b"

# --------------------------------------------------------------------------
# 3) Parámetros de troceado (chunking) y recuperación (RAG)
# --------------------------------------------------------------------------
CHUNK_SIZE = 800        # caracteres por fragmento
CHUNK_OVERLAP = 120     # solape entre fragmentos (para no cortar ideas a la mitad)
TOP_K = 5               # nº de DOCUMENTOS únicos que citamos por pregunta
RELATED_K = 5           # nº de documentos extra de "lectura relacionada" (no citados)

# Presupuesto de caracteres para el CONTEXTO que enviamos al LLM.
# Ollama corre con num_ctx=8192 TOKENS. Como una fuente ahora une VARIOS chunks
# del mismo artículo, el contexto puede crecer; recortamos para no desbordar la
# ventana (regla ~4 chars/token en inglés → ~32k chars de tope duro; dejamos
# margen para system + pregunta + respuesta y nos quedamos en ~24k).
MAX_CONTEXT_CHARS = 24000

# Umbral de "evidencia suficiente": si la mejor coincidencia queda por debajo, se
# activa el Scout. OJO: la ESCALA depende del backend/métrica:
#   - bge (coseno):  ~0.70   (similitud 0..1)
#   - MedCPT (dot):  ~63      (producto escalar, ~50..75)
# MedCPT medido sobre la BASE COMPLETA (8.920 chunks, top-1 de cada pregunta):
#   relevantes (6 fármacos) 70.1-73.3  ·  ajenas (Francia, malaria, pan...) 55.8-63.7
# Hueco limpio → umbral 66.0 (centro del hueco). Umbral fijo = frágil → futura: LLM-juez.
# ⚠ Este umbral se calibró sobre el corpus de DERMATITIS ATÓPICA. Un dominio nuevo
#   hereda el valor (es más una propiedad del modelo MedCPT que del corpus), pero
#   conviene comprobarlo con `evaluate_sources.py` una vez indexado.
if EMBEDDING_BACKEND == "medcpt":
    SIMILARITY_THRESHOLD = 66.0     # producto escalar MedCPT (~55..75)
elif EMBEDDING_BACKEND == "openai":
    # Coseno de text-embedding-3-small (0..1). SIN CALIBRAR todavía: para la
    # evaluación comparativa (ranking/precisión) no hace falta; si algún día se
    # usara como producto, medir el hueco relevante/ajeno como con MedCPT.
    SIMILARITY_THRESHOLD = 0.35
else:
    SIMILARITY_THRESHOLD = 0.70     # coseno bge-small (0..1)

# Métrica de distancia de ChromaDB. Cada backend usa la SUYA nativa: MedCPT →
# producto escalar ("ip"); bge → coseno. Debe casar con embeddings.py (normalizar
# o no). Cambiar de métrica exige RECREAR la colección (reindexar).
CHROMA_SPACE = "ip" if EMBEDDING_BACKEND == "medcpt" else "cosine"

# --------------------------------------------------------------------------
# 4) Identidad de la app (pestaña "About") y contacto para comentarios
# --------------------------------------------------------------------------
APP_VERSION = "1.1.0-beta"   # fase experimental: se dice en la interfaz
# URL del repositorio público. Los comentarios de los usuarios llegan por sus
# "Issues". Fijada el 8-sep-2026 (usuario joan-serrai, repo MIA-evidence-engine).
REPO_URL = "https://github.com/joan-serrai/MIA-evidence-engine"

# --------------------------------------------------------------------------
# 5) APIs públicas (gratuitas, sin enviar datos privados)
# --------------------------------------------------------------------------
CLINICALTRIALS_API = "https://clinicaltrials.gov/api/v2/studies"
PUBMED_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


# ==========================================================================
# 6) PERFILES DE DOMINIO (enfermedad + fármacos + endpoints)
# ==========================================================================

def slugify(texto):
    """'Plaque psoriasis' → 'plaque_psoriasis' (nombre seguro de archivo/colección)."""
    slug = re.sub(r"[^a-z0-9]+", "_", (texto or "").lower()).strip("_")
    return slug[:40] or "domain"


def list_domains():
    """Slugs de los perfiles disponibles en domains/ (ordenados, el default primero)."""
    slugs = sorted(p.stem for p in DOMAINS_DIR.glob("*.json"))
    if DEFAULT_DOMAIN in slugs:
        slugs.remove(DEFAULT_DOMAIN)
        slugs.insert(0, DEFAULT_DOMAIN)
    return slugs


def domain_path(slug):
    return DOMAINS_DIR / f"{slug}.json"


def endpoint_spec(entry):
    """Convierte un endpoint del perfil en {label, regex, ct_title}.

    Acepta una ETIQUETA ("EASI 75", "IGA 0/1", "PASI 90", "ACR20") y deriva:
      - `regex`   : cómo aparece en un abstract → lo usa src/outcomes.py para
                    extraer la cifra que viene justo detrás ("EASI-75 … 65%").
      - `ct_title`: lista de patrones que deben aparecer TODOS (en minúsculas) en
                    el título verboso de una medida de ClinicalTrials.gov
                    ("…Eczema Area and Severity Index (EASI) Response >=75 Percent…")
                    → lo usa src/processing.py para traducirlo al nombre corto.
    O un DICT {"label", "regex"?, "ct_title"?} para afinar a mano un caso raro.
    """
    if isinstance(entry, dict):
        label = str(entry["label"]).strip()
        regex, ct_title = entry.get("regex"), entry.get("ct_title")
    else:
        label, regex, ct_title = str(entry).strip(), None, None

    m = re.match(r"^(?P<name>[A-Za-z][A-Za-z\-]*(?:\s+[A-Za-z][A-Za-z\-]*)*?)"
                 r"\s*[-\s]?\s*(?P<val>\d+(?:\s*/\s*\d+)?)?\s*$", label)
    name = (m.group("name") if m else label).strip()
    val = (m.group("val") or "").replace(" ", "") if m else ""
    name_re = re.escape(name).replace(r"\-", r"[-\s]?").replace(r"\ ", r"\s+")

    if regex is None:
        if "/" in val:
            a, b = val.split("/", 1)
            regex = rf"\b{name_re}\s*{a}\s*/\s*{b}\b"
        elif val:
            regex = rf"\b{name_re}[-\s]?{val}\b"
        else:
            regex = rf"\b{name_re}\b"
    if ct_title is None:
        ct_title = [rf"\b{name_re.lower()}\b"]
        if "/" in val:
            a, b = val.split("/", 1)
            # "0/1" se escribe de mil formas en CT.gov: "Clear (0) or Almost Clear (1)",
            # "0 or 1", "0/1". Con que aparezca el nombre y cualquiera de esas, es el mismo.
            ct_title.append(rf"(clear\s*\({a}\)|almost\s*clear|\b{a}\s*(?:or|/)\s*{b}\b)")
        elif val:
            ct_title.append(rf"(?:>=?\s*)?\b{val}\s*(?:percent|%)")
    return {"label": label, "regex": regex, "ct_title": list(ct_title)}


def load_domain(slug):
    """Lee y valida domains/<slug>.json. Devuelve el dict con valores por defecto."""
    if slug in (None, NO_DOMAIN):
        # Perfil vacío (ver NO_DOMAIN): mismas claves que un perfil real, todo en
        # blanco, para que el código que lee config.DRUGS, config.EFFICACY_ENDPOINTS…
        # siga funcionando sin comprobar nada especial.
        return {"slug": NO_DOMAIN, "disease": "", "synonyms": [], "drug_classes": {},
                "class_labels": {}, "extra_drugs": [], "mechanisms": {},
                "efficacy_endpoints": [], "safety_terms": [], "extra_queries": [],
                "example_questions": [], "mechanism_questions": [],
                "legacy_collections": False, "endpoint_examples": "the named endpoint"}
    path = domain_path(slug)
    if not path.exists():
        raise FileNotFoundError(
            f"Domain profile '{slug}' not found ({path}). "
            f"Available: {', '.join(list_domains()) or '(none)'}. "
            "Create one with:  python build_corpus.py --disease \"...\" --drug ...")
    d = json.loads(path.read_text(encoding="utf-8"))
    if not d.get("disease"):
        raise ValueError(f"Profile {path} has no 'disease' field.")
    d.setdefault("slug", slug)
    d.setdefault("synonyms", [])
    d.setdefault("drug_classes", {})
    d.setdefault("class_labels", {})
    d.setdefault("extra_drugs", [])
    d.setdefault("mechanisms", {})
    d.setdefault("efficacy_endpoints", [])
    d.setdefault("safety_terms", [])
    d.setdefault("extra_queries", [])
    d.setdefault("example_questions", [])
    d.setdefault("mechanism_questions", [])
    d.setdefault("legacy_collections", False)
    if not d.get("endpoint_examples"):
        d["endpoint_examples"] = (
            ", ".join(endpoint_spec(e)["label"] for e in d["efficacy_endpoints"][:3])
            or "the named endpoint")
    return d


def collection_name(backend=None, slug=None):
    """Nombre de la colección de ChromaDB para (dominio, backend).

    Los vectores de bge (384), MedCPT (768) y OpenAI (1536) NO conviven en una
    colección, así que hay una POR backend; y cada dominio tiene las suyas, así
    que varias patologías conviven en la misma carpeta data/chroma sin pisarse.

    El dominio original conserva sus nombres históricos (`mia_evidence`,
    `mia_evidence_medcpt`, `mia_evidence_openai`): así el índice de 9.734 chunks y
    todas las evaluaciones del TFM siguen siendo válidos sin reindexar nada.
    """
    backend = backend or EMBEDDING_BACKEND
    dom = DOMAIN if slug is None else load_domain(slug)
    if dom.get("legacy_collections"):
        return "mia_evidence" if backend == "sentence-transformers" else f"mia_evidence_{backend}"
    return f"mia_{dom['slug']}_{backend}"


def resolve_active_domain():
    """Slug del perfil activo: MIA_DOMAIN > domains/active.txt > DEFAULT_DOMAIN.

    Si ni siquiera existe el perfil por defecto (instalación nueva, sin ningún
    domains/<slug>.json), devuelve NO_DOMAIN: MIA arranca "en blanco".
    """
    slug = (os.getenv("MIA_DOMAIN") or "").strip()
    if not slug and ACTIVE_DOMAIN_FILE.exists():
        slug = ACTIVE_DOMAIN_FILE.read_text(encoding="utf-8").strip()
    if not slug or not domain_path(slug).exists():
        slug = DEFAULT_DOMAIN
    if not domain_path(slug).exists():
        return NO_DOMAIN
    return slug


def activate_domain(slug, persist=False):
    """Carga el perfil `slug` y (re)define los valores de dominio de este módulo.

    Se llama una vez al importar config y, en la interfaz, cuando el usuario cambia
    de patología en el selector (sin reiniciar la app). Los módulos que cachean
    algo derivado del dominio (colección de Chroma, patrones de endpoints) deben
    leer `config.DOMAIN_SLUG` para saber si tienen que refrescarse.
    `persist=True` deja el slug en domains/active.txt para la próxima ejecución.
    """
    global DOMAIN, DOMAIN_SLUG, DISEASE, DISEASE_SYNONYMS, DISEASE_QUERY
    global DRUG_CLASSES, CLASS_LABELS, BIOLOGICS, JAK_INHIBITORS, DRUGS, EXTRA_DRUGS, ALL_DRUGS
    global MECHANISMS, EFFICACY_ENDPOINTS, SAFETY_TERMS, ENDPOINT_EXAMPLES
    global EXAMPLE_QUESTIONS, MECHANISM_QUESTIONS, EXTRA_QUERIES
    global BRONZE_DIR, SILVER_DIR, CHROMA_COLLECTION, MANIFEST_CSV, MANIFEST_PMIDS

    d = load_domain(slug)
    DOMAIN = d
    DOMAIN_SLUG = d["slug"]

    # --- Caso de uso: enfermedad y fármacos competidores ---
    DISEASE = d["disease"]
    DISEASE_SYNONYMS = list(d["synonyms"])
    # Término de búsqueda en las APIs. Con sinónimos, "(X OR Y OR Z)"; sin ellos,
    # la enfermedad tal cual (comportamiento original del capstone).
    DISEASE_QUERY = (f"({' OR '.join([DISEASE] + DISEASE_SYNONYMS)})"
                     if DISEASE_SYNONYMS else DISEASE)
    DRUG_CLASSES = {k: list(v) for k, v in d["drug_classes"].items()}
    CLASS_LABELS = dict(d["class_labels"])
    # Alias históricos (el código del capstone los usa por nombre). En un dominio
    # sin esas clases quedan vacíos, y el código que los usa debe tolerarlo.
    BIOLOGICS = list(DRUG_CLASSES.get("biologics", []))
    JAK_INHIBITORS = list(DRUG_CLASSES.get("jak_inhibitors", []))
    DRUGS = [drug for drugs in DRUG_CLASSES.values() for drug in drugs]
    EXTRA_DRUGS = list(d["extra_drugs"])
    ALL_DRUGS = DRUGS + [x for x in EXTRA_DRUGS if x not in DRUGS]

    # --- Mecanismos → fármacos (para el agente catalogador / veredicto) ---
    MECHANISMS = {str(k).lower(): list(v) for k, v in d["mechanisms"].items()}

    # --- Endpoints de eficacia y términos de seguridad (extracción de cifras) ---
    EFFICACY_ENDPOINTS = [endpoint_spec(e) for e in d["efficacy_endpoints"]]
    SAFETY_TERMS = [endpoint_spec(e) for e in d["safety_terms"]]
    ENDPOINT_EXAMPLES = d["endpoint_examples"]

    # --- Interfaz: preguntas de ejemplo y búsquedas extra ---
    EXAMPLE_QUESTIONS = list(d["example_questions"])
    MECHANISM_QUESTIONS = list(d["mechanism_questions"])
    EXTRA_QUERIES = list(d["extra_queries"])

    # --- Rutas de datos por dominio ---
    # El dominio original vive en data/bronze y data/silver (donde siempre); los
    # demás en subcarpetas, para que sus descargas no se mezclen con el corpus del
    # TFM (processing lee data/bronze/ct_*.json SIN recursión → no se cruzan).
    if d.get("legacy_collections"):
        BRONZE_DIR = DATA_DIR / "bronze"
        SILVER_DIR = DATA_DIR / "silver"
        MANIFEST_CSV = DATA_DIR / "corpus_manifest.csv"
        MANIFEST_PMIDS = DATA_DIR / "corpus_pmids.txt"
    else:
        BRONZE_DIR = DATA_DIR / "bronze" / DOMAIN_SLUG
        SILVER_DIR = DATA_DIR / "silver" / DOMAIN_SLUG
        MANIFEST_CSV = DATA_DIR / f"corpus_manifest_{DOMAIN_SLUG}.csv"
        MANIFEST_PMIDS = DATA_DIR / f"corpus_pmids_{DOMAIN_SLUG}.txt"
    CHROMA_COLLECTION = collection_name(EMBEDDING_BACKEND)

    if persist:
        DOMAINS_DIR.mkdir(parents=True, exist_ok=True)
        ACTIVE_DOMAIN_FILE.write_text(DOMAIN_SLUG + "\n", encoding="utf-8")
    return d


# Activación al importar: a partir de aquí existen DISEASE, DRUGS, ALL_DRUGS,
# BRONZE_DIR, CHROMA_COLLECTION, … exactamente como antes.
activate_domain(resolve_active_domain())
