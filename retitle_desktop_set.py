"""
retitle_desktop_set.py — Corrige los TÍTULOS ya indexados del set de escritorio.

CONTEXTO: el parser antiguo de `ingest_desktop_set.py` guardaba como `title` la
LÍNEA DE CITA de la revista ("186. Dermatol Ther (Heidelb). 2026...") en los
artículos que llevan declaración de conflictos de interés (ver la nota en
`ingest_desktop_set.parse_record`). El parser ya está arreglado, pero los datos
YA indexados en ChromaDB conservan el título feo.

Este script REPARA los datos existentes SIN re-embeber (rápido): reparsea el .txt
con el parser corregido, y para cada PMID actualiza SOLO el campo `title` de la
metadata de todos sus chunks, en las colecciones indicadas. El vector y el texto
no se tocan (solo cambia la etiqueta que se muestra en la tarjeta de fuente).

Uso:
    ./.venv/Scripts/python.exe retitle_desktop_set.py --file "<ruta al .txt>"
"""

import sys
import argparse
from pathlib import Path

import chromadb

import config
import ingest_desktop_set as ing

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Colecciones a reparar (la activa MedCPT y la histórica bge, si existen).
_COLLECTIONS = ["mia_evidence_medcpt", "mia_evidence"]


def build_title_map(file_path):
    """pmid -> título REAL (recortado a 300 como en processing), desde el .txt."""
    texto = Path(file_path).read_text(encoding="utf-8", errors="replace")
    recs = [r for r in (ing.parse_record(b) for b in ing._split_records(texto)) if r]
    return {r["pmid"]: (r["title"] or "")[:300] for r in recs if r["title"].strip()}


def retitle_collection(client, nombre, title_map):
    """Actualiza el título de los chunks cuyo doc_id está en title_map y difiere."""
    try:
        col = client.get_collection(nombre)
    except Exception:
        print(f"  [omitida] la colección '{nombre}' no existe.")
        return 0, 0

    data = col.get(include=["metadatas"])
    ids_upd, metas_upd = [], []
    for cid, meta in zip(data["ids"], data["metadatas"]):
        pmid = str(meta.get("doc_id") or "")
        nuevo = title_map.get(pmid)
        if nuevo and meta.get("title") != nuevo:
            meta = dict(meta)          # copia; no mutamos el original
            meta["title"] = nuevo
            ids_upd.append(cid)
            metas_upd.append(meta)

    # Actualización por lotes (update no re-embebe: solo cambia metadata).
    for i in range(0, len(ids_upd), 2000):
        col.update(ids=ids_upd[i:i + 2000], metadatas=metas_upd[i:i + 2000])

    return len(ids_upd), len({m["doc_id"] for m in metas_upd})


def run(file_path):
    print("=" * 64)
    print(" Reparación de TÍTULOS del set de escritorio (solo metadata)")
    print("=" * 64)
    title_map = build_title_map(file_path)
    print(f"Títulos leídos del .txt: {len(title_map)}")

    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    for nombre in _COLLECTIONS:
        n_chunks, n_docs = retitle_collection(client, nombre, title_map)
        print(f"  {nombre:20s}: {n_chunks} chunks corregidos ({n_docs} artículos)")
    print("Hecho.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Corrige títulos ya indexados del set de escritorio.")
    parser.add_argument("--file", required=True, help="Ruta al .txt exportado de PubMed.")
    args = parser.parse_args()
    run(args.file)
