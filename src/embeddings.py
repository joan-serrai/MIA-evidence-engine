"""
src/embeddings.py — Capa de embeddings INTERCAMBIABLE (bge  ↔  MedCPT).

¿Por qué existe este archivo? Porque los dos modelos funcionan distinto:

  - bge-small es SIMÉTRICO: un MISMO modelo embebe la pregunta y los documentos.
  - MedCPT es ASIMÉTRICO / de DOS TORRES: un encoder para la PREGUNTA
    (MedCPT-Query-Encoder) y OTRO distinto para el ARTÍCULO (MedCPT-Article-Encoder).

Para que processing.py (indexar) y rag.py (recuperar) NO tengan que saber cuál de
los dos está activo, aquí exponemos una interfaz única:

    embed_documents(textos) -> list[vector]   # al INDEXAR (Fase 1)
    embed_query(texto)      -> vector          # al RECUPERAR (Fase 2)

El backend activo se elige en config.EMBEDDING_BACKEND. Cambiar de modelo (o
comparar los dos) es cambiar UNA línea de config, sin tocar el resto del código.

Nota sobre la distancia (IMPORTANTE): cada backend usa SU métrica nativa.
  - bge  → COSENO: vectores normalizados (sentence-transformers ya lo hace).
  - MedCPT → PRODUCTO ESCALAR (inner product): vectores SIN normalizar. MedCPT
    codifica su "confianza" en la MAGNITUD del vector; normalizar la destruiría y
    rompería la puerta de evidencia (una pregunta ajena podría colarse a lo alto).
    La colección de Chroma se crea con el espacio que toque (config.CHROMA_SPACE).
"""

import sys
from pathlib import Path

try:
    from .. import config
except (ImportError, ValueError):
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    import config


# Los modelos son caros de cargar; se cargan una vez y se cachean (perezoso).
_ST_MODEL = None    # sentence-transformers (bge)
_MEDCPT = None      # dict con los tokenizers + modelos de las dos torres de MedCPT
_OPENAI = None      # cliente de la API de OpenAI (solo evaluación comparativa)


# --------------------------------------------------------------------------
# Backend 1: sentence-transformers (bge-small)  — SIMÉTRICO
# --------------------------------------------------------------------------

def _st():
    global _ST_MODEL
    if _ST_MODEL is None:
        from sentence_transformers import SentenceTransformer
        print(f"Cargando embeddings (sentence-transformers): {config.EMBEDDING_MODEL} ...")
        _ST_MODEL = SentenceTransformer(config.EMBEDDING_MODEL)
    return _ST_MODEL


# --------------------------------------------------------------------------
# Backend 2: MedCPT (NCBI)  — ASIMÉTRICO / dos torres
# --------------------------------------------------------------------------

def _medcpt():
    """Carga (una vez) los dos encoders de MedCPT y su tokenizer."""
    global _MEDCPT
    if _MEDCPT is None:
        import torch
        from transformers import AutoTokenizer, AutoModel
        print("Cargando embeddings (MedCPT): query + article encoders ...")
        q_tok = AutoTokenizer.from_pretrained(config.MEDCPT_QUERY_MODEL)
        q_mod = AutoModel.from_pretrained(config.MEDCPT_QUERY_MODEL).eval()
        a_tok = AutoTokenizer.from_pretrained(config.MEDCPT_ARTICLE_MODEL)
        a_mod = AutoModel.from_pretrained(config.MEDCPT_ARTICLE_MODEL).eval()
        _MEDCPT = {"torch": torch, "q_tok": q_tok, "q_mod": q_mod,
                   "a_tok": a_tok, "a_mod": a_mod}
    return _MEDCPT


def _medcpt_encode(textos, torre, max_length, batch=32):
    """Embebe una lista de textos con la TORRE indicada ('query' o 'article').

    MedCPT usa el vector del token especial [CLS] (la primera posición de la
    última capa) como representación del texto. Procesamos por lotes para no
    quedarnos sin memoria en CPU.
    """
    m = _medcpt()
    torch = m["torch"]
    tok, mod = (m["q_tok"], m["q_mod"]) if torre == "query" else (m["a_tok"], m["a_mod"])

    salida = []
    with torch.no_grad():  # inferencia: sin gradientes (más rápido, menos memoria)
        for i in range(0, len(textos), batch):
            lote = textos[i:i + batch]
            enc = tok(lote, truncation=True, padding=True,
                      max_length=max_length, return_tensors="pt")
            emb = mod(**enc).last_hidden_state[:, 0, :]  # token [CLS]
            salida.extend(emb.cpu().tolist())
    return salida


# --------------------------------------------------------------------------
# Backend 3: OpenAI (text-embedding-3-small)  — SIMÉTRICO, vía API
# --------------------------------------------------------------------------
# SOLO para la EVALUACIÓN comparativa ("línea Centivence"). Rompe el "100% local",
# así que NO es un backend de producto. La clave se lee de OPENAI_API_KEY (.env).

def _openai():
    """Crea (una vez) el cliente de OpenAI. Lee la clave de OPENAI_API_KEY (.env)."""
    global _OPENAI
    if _OPENAI is None:
        from openai import OpenAI
        from dotenv import load_dotenv
        # Cargamos el .env por RUTA EXPLÍCITA del proyecto (config.BASE_DIR), no por
        # cwd: así funciona se ejecute desde donde se ejecute el script.
        load_dotenv(dotenv_path=config.BASE_DIR / ".env")
        _OPENAI = OpenAI()  # toma la clave de la variable de entorno OPENAI_API_KEY
    return _OPENAI


def _openai_embed(textos):
    """Embebe una lista de textos con text-embedding-3-small (por lotes)."""
    client = _openai()
    salida = []
    # La API acepta muchos inputs por llamada; troceamos por prudencia/robustez.
    for i in range(0, len(textos), 256):
        lote = [t if t.strip() else " " for t in textos[i:i + 256]]  # evita inputs vacíos
        resp = client.embeddings.create(model=config.OPENAI_EMBEDDING_MODEL, input=lote)
        salida.extend(d.embedding for d in resp.data)
    return salida


# --------------------------------------------------------------------------
# Interfaz pública (lo único que usan processing.py y rag.py)
# --------------------------------------------------------------------------

def embed_documents(textos):
    """Vectoriza DOCUMENTOS (chunks) para indexarlos. Devuelve lista de vectores."""
    if not textos:
        return []
    if config.EMBEDDING_BACKEND == "medcpt":
        # SIN normalizar: MedCPT usa producto escalar (ver nota de cabecera).
        return _medcpt_encode(textos, "article", config.MEDCPT_ARTICLE_MAXLEN)
    if config.EMBEDDING_BACKEND == "openai":
        # text-embedding-3 devuelve vectores ya normalizados (coseno).
        return _openai_embed(textos)
    # sentence-transformers ya normaliza internamente (coseno).
    return _st().encode(textos, normalize_embeddings=True, show_progress_bar=True).tolist()


def embed_query(texto):
    """Vectoriza UNA pregunta para recuperar. Devuelve un solo vector."""
    if config.EMBEDDING_BACKEND == "medcpt":
        # SIN normalizar (producto escalar): usa la TORRE de preguntas.
        return _medcpt_encode([texto], "query", config.MEDCPT_QUERY_MAXLEN)[0]
    if config.EMBEDDING_BACKEND == "openai":
        return _openai_embed([texto])[0]
    return _st().encode([texto], normalize_embeddings=True).tolist()[0]


# --------------------------------------------------------------------------
# Prueba rápida aislada
# --------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print(f"Backend activo: {config.EMBEDDING_BACKEND}")
    q = embed_query("efficacy of dupilumab in atopic dermatitis")
    d = embed_documents(["Dupilumab significantly improved EASI-75 in adults with "
                         "moderate-to-severe atopic dermatitis."])[0]
    print(f"dim pregunta: {len(q)}  ·  dim documento: {len(d)}")
    coseno = sum(a * b for a, b in zip(q, d))  # ambos ya normalizados
    print(f"similitud (coseno) pregunta↔documento relevante: {coseno:.3f}")
