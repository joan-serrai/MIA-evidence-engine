"""export_corpus_manifest.py — REPRODUCIBLE CENSUS of the indexed corpus.

Why does this script exist?
---------------------------
A corpus can be built in more than one way (automatic download by drug, or a
manual import of a PubMed export), and only the first is reproducible on its
own. This script closes that gap: it reads the ChromaDB collection and writes
the census of EVERYTHING that is indexed (one record per document, with its
PMID/NCT, title and link). With that census anyone can fetch the same documents
again from PubMed/ClinicalTrials.gov even if an original file is lost.

Outputs (in `data/`):
    corpus_manifest.csv  — one document per row: doc_id, source, title, URL,
                           drugs, access, number of chunks and whether it is in bronze.
    corpus_pmids.txt     — the PMIDs only, one per line. Paste them as they are
                           into the PubMed search box to download them again.

Usage:
    ./.venv/Scripts/python.exe export_corpus_manifest.py
    ./.venv/Scripts/python.exe export_corpus_manifest.py --collection mia_evidence_openai
"""

import sys
import csv
import argparse
from pathlib import Path

import chromadb

sys.path.append(str(Path(__file__).resolve().parent))
import config

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _bronze_doc_ids():
    """doc_ids que SÍ se pueden regenerar con `run_phase1.py`.

    No re-parseamos los ficheros de bronze (sería caro y frágil): basta con
    buscar el identificador como texto dentro de los JSON/XML crudos, que es
    donde aparece. Si no hay carpeta bronze, devolvemos conjunto vacío.
    """
    ids = set()
    if not config.BRONZE_DIR.exists():
        return ids
    for f in config.BRONZE_DIR.iterdir():
        if f.suffix.lower() not in (".json", ".xml"):
            continue
        try:
            texto = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        ids.add(("__RAW__", texto))   # guardamos el texto para buscar después
    return ids


def _en_bronze(doc_id, blobs):
    """¿Aparece este doc_id en algún fichero crudo de bronze?"""
    return any(doc_id in texto for _, texto in blobs)


def exportar(nombre_coleccion):
    cliente = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    coleccion = cliente.get_collection(nombre_coleccion)
    total_chunks = coleccion.count()
    print(f"Collection '{nombre_coleccion}': {total_chunks} chunks indexed.")

    # Traemos SOLO los metadatos (no los vectores ni el texto): mucho más ligero.
    # ChromaDB pagina por lotes para no cargar 9.000 registros de golpe.
    docs = {}
    lote = 2000
    for desplazamiento in range(0, total_chunks, lote):
        res = coleccion.get(limit=lote, offset=desplazamiento, include=["metadatas"])
        for meta in res["metadatas"]:
            doc_id = meta.get("doc_id")
            if not doc_id:
                continue
            if doc_id not in docs:
                docs[doc_id] = {
                    "doc_id": doc_id,
                    "source": meta.get("source", ""),
                    "title": meta.get("title", ""),
                    "url": meta.get("url", ""),
                    "drugs": meta.get("drugs", ""),
                    "access": meta.get("access", ""),
                    "doi": meta.get("doi", ""),
                    "n_chunks": 0,
                }
            docs[doc_id]["n_chunks"] += 1
        print(f"  ... {min(desplazamiento + lote, total_chunks)}/{total_chunks}")

    # Marcamos qué documentos son regenerables desde bronze y cuáles no.
    blobs = _bronze_doc_ids()
    for d in docs.values():
        d["en_bronze"] = "si" if _en_bronze(d["doc_id"], blobs) else "no"

    filas = sorted(docs.values(), key=lambda d: (d["source"], d["doc_id"]))

    # Ruta por dominio (config.MANIFEST_CSV): el perfil original escribe
    # data/corpus_manifest.csv como siempre; otro dominio, corpus_manifest_<slug>.csv.
    salida_csv = config.MANIFEST_CSV
    campos = ["doc_id", "source", "title", "url", "drugs", "access", "doi",
              "n_chunks", "en_bronze"]
    with open(salida_csv, "w", encoding="utf-8", newline="") as fh:
        escritor = csv.DictWriter(fh, fieldnames=campos)
        escritor.writeheader()
        escritor.writerows(filas)

    pmids = [d["doc_id"] for d in filas if d["source"] == "pubmed"]
    salida_pmids = config.MANIFEST_PMIDS
    salida_pmids.write_text("\n".join(pmids) + "\n", encoding="utf-8")

    # Resumen por consola (lo interesante para el TFM).
    n_bronze = sum(1 for d in filas if d["en_bronze"] == "si")
    n_externos = len(filas) - n_bronze
    print()
    print("=" * 62)
    print(f"  Unique documents       : {len(filas)}")
    print(f"  Total chunks           : {total_chunks}")
    print(f"  - from PubMed          : {sum(1 for d in filas if d['source'] == 'pubmed')}")
    print(f"  - from ClinicalTrials  : {sum(1 for d in filas if d['source'] == 'clinicaltrials')}")
    print(f"  Regenerable (bronze)   : {n_bronze}")
    print(f"  Only in the index      : {n_externos}   <- came from the desktop set")
    print("=" * 62)
    print(f"Written: {salida_csv}")
    print(f"Written: {salida_pmids}  ({len(pmids)} PMIDs)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default=config.CHROMA_COLLECTION,
                        help="Name of the ChromaDB collection to inventory.")
    args = parser.parse_args()
    exportar(args.collection)
