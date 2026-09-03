"""
index_openai.py — Crea la colección de embeddings de OpenAI para la EVALUACIÓN.

Para comparar MedCPT (producto MIA) con text-embedding-3-small (la "línea Centivence")
de forma JUSTA, la colección de OpenAI debe contener EXACTAMENTE los mismos chunks que
la de MedCPT — solo cambia el vector, no el texto. Por eso NO re-troceamos desde bronze
(donde además falta el set de escritorio): leemos los chunks ya existentes de
`mia_evidence_medcpt` y los re-embebemos con OpenAI en `mia_evidence_openai`.

Requisitos: OPENAI_API_KEY en el .env. Coste: ~céntimos (unos 1.8M tokens).

Uso:
    ./.venv/Scripts/python.exe index_openai.py
"""

import sys
from pathlib import Path

import chromadb

sys.path.append(str(Path(__file__).resolve().parent))
import config
from src import embeddings

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Colecciones del DOMINIO ACTIVO (config.collection_name): para el perfil original
# son mia_evidence_medcpt / mia_evidence_openai; para otro, mia_<slug>_medcpt / _openai.
SOURCE_COLLECTION = config.collection_name("medcpt")   # de aquí copiamos los chunks (mismos textos)
TARGET_COLLECTION = config.collection_name("openai")   # aquí escribimos los vectores de OpenAI


def run():
    print("=" * 64)
    print(" Indexado con OpenAI (text-embedding-3-small) para la evaluación")
    print("=" * 64)

    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    src = client.get_collection(SOURCE_COLLECTION)
    data = src.get(include=["documents", "metadatas"])
    ids = data["ids"]
    docs = data["documents"]
    metas = data["metadatas"]
    print(f"Chunks a re-embeber (desde {SOURCE_COLLECTION}): {len(ids)}")
    if not ids:
        print("No hay chunks de origen. ¿Has indexado antes con MedCPT?"); return

    # Colección destino: coseno (text-embedding-3 devuelve vectores normalizados).
    target = client.get_or_create_collection(
        name=TARGET_COLLECTION, metadata={"hnsw:space": "cosine"})
    ya = target.count()
    print(f"Colección '{TARGET_COLLECTION}' antes: {ya} chunks")

    # Embebemos con OpenAI (forzamos el backend openai para esta llamada).
    prev_backend = config.EMBEDDING_BACKEND
    config.EMBEDDING_BACKEND = "openai"
    try:
        print("Llamando a la API de OpenAI por lotes… (puede tardar 1-3 min)")
        vectores = embeddings.embed_documents(docs)
    finally:
        config.EMBEDDING_BACKEND = prev_backend

    # Upsert por lotes (Chroma limita el tamaño por llamada).
    try:
        lote_max = min(client.get_max_batch_size(), 5000)
    except Exception:
        lote_max = 5000
    for i in range(0, len(ids), lote_max):
        target.upsert(
            ids=ids[i:i + lote_max],
            documents=docs[i:i + lote_max],
            embeddings=vectores[i:i + lote_max],
            metadatas=metas[i:i + lote_max],
        )

    print(f"Colección '{TARGET_COLLECTION}' después: {target.count()} chunks")
    print("Listo. Ya puedes ejecutar la evaluación comparativa.")


if __name__ == "__main__":
    run()
