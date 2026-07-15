"""
config.py — Parámetros centrales del proyecto MIA.

Todo lo que pueda cambiar (enfermedad, fármacos, modelos, rutas, umbrales)
vive AQUÍ, en un solo sitio. Así el resto del código no tiene valores
"a fuego" y podemos reusar MIA para otra patología cambiando solo este archivo.
"""

from pathlib import Path

# --------------------------------------------------------------------------
# 1) Caso de uso: enfermedad y fármacos competidores
# --------------------------------------------------------------------------
DISEASE = "Atopic dermatitis"  # dermatitis atópica

# Fármacos competidores, separados por clase terapéutica.
BIOLOGICS = ["dupilumab", "tralokinumab", "lebrikizumab", "nemolizumab"]  # anticuerpos (anti-IL)
JAK_INHIBITORS = ["upadacitinib", "baricitinib", "abrocitinib"]           # inhibidores JAK (orales)
DRUGS = BIOLOGICS + JAK_INHIBITORS

# Fármacos adicionales (sobre todo tópicos) que aparecen en la literatura pero
# no son los competidores principales. Útiles para ETIQUETAR la evidencia si se
# mencionan. ALL_DRUGS = lista completa para detección de fármacos en el corpus.
EXTRA_DRUGS = ["ruxolitinib", "crisaborole", "delgocitinib"]
ALL_DRUGS = DRUGS + EXTRA_DRUGS

# --------------------------------------------------------------------------
# 2) Rutas de datos (patrón "medallón" simplificado)
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
BRONZE_DIR = DATA_DIR / "bronze"   # datos crudos tal cual llegan de las APIs/PDFs
SILVER_DIR = DATA_DIR / "silver"   # texto limpio y troceado en "chunks"
CHROMA_DIR = DATA_DIR / "chroma"   # base de datos vectorial (ChromaDB)

# --------------------------------------------------------------------------
# 3) Modelos
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
# 4) Parámetros de troceado (chunking) y recuperación (RAG)
# --------------------------------------------------------------------------
CHUNK_SIZE = 800        # caracteres por fragmento
CHUNK_OVERLAP = 120     # solape entre fragmentos (para no cortar ideas a la mitad)
TOP_K = 5               # nº de DOCUMENTOS únicos que citamos por pregunta

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
if EMBEDDING_BACKEND == "medcpt":
    SIMILARITY_THRESHOLD = 66.0     # producto escalar MedCPT (~55..75)
elif EMBEDDING_BACKEND == "openai":
    # Coseno de text-embedding-3-small (0..1). SIN CALIBRAR todavía: para la
    # evaluación comparativa (ranking/precisión) no hace falta; si algún día se
    # usara como producto, medir el hueco relevante/ajeno como con MedCPT.
    SIMILARITY_THRESHOLD = 0.35
else:
    SIMILARITY_THRESHOLD = 0.70     # coseno bge-small (0..1)

# La colección de ChromaDB depende del backend: los vectores de bge (384 dim) y
# MedCPT (768 dim) NO son compatibles en la misma colección. Usar una colección
# por backend permite mantener AMBOS índices a la vez y volver atrás cambiando
# solo EMBEDDING_BACKEND (sin destruir nada). bge conserva el nombre histórico.
if EMBEDDING_BACKEND == "sentence-transformers":
    CHROMA_COLLECTION = "mia_evidence"
else:
    CHROMA_COLLECTION = f"mia_evidence_{EMBEDDING_BACKEND}"

# Métrica de distancia de ChromaDB. Cada backend usa la SUYA nativa: MedCPT →
# producto escalar ("ip"); bge → coseno. Debe casar con embeddings.py (normalizar
# o no). Cambiar de métrica exige RECREAR la colección (reindexar).
CHROMA_SPACE = "ip" if EMBEDDING_BACKEND == "medcpt" else "cosine"

# --------------------------------------------------------------------------
# 5) APIs públicas (gratuitas, sin enviar datos privados)
# --------------------------------------------------------------------------
CLINICALTRIALS_API = "https://clinicaltrials.gov/api/v2/studies"
PUBMED_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
