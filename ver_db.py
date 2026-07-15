"""
ver_db.py — Asómate por dentro a la base vectorial de MIA (ChromaDB).

La base de datos NO se abre a ojo (es un chroma.sqlite3 con formato interno),
pero SÍ se puede inspeccionar con código. Este script te enseña:
  1) cuántos registros hay y cómo se reparten (por fuente),
  2) ejemplos reales de registros (texto + metadatos + tamaño del vector),
  3) opcionalmente, una búsqueda de verdad (para ver el RAG recuperando).

Uso (desde la carpeta MIA):
  ./.venv/Scripts/python.exe ver_db.py                       # resumen + 3 ejemplos
  ./.venv/Scripts/python.exe ver_db.py --ejemplos 5          # nº de ejemplos a mostrar
  ./.venv/Scripts/python.exe ver_db.py --buscar "dupilumab efficacy in atopic dermatitis"
"""

import sys
import argparse
import collections

import chromadb

# Reutilizamos config.py (única fuente de verdad: rutas, nombre de colección…).
import config

# Windows: la consola usa cp1252 por defecto y peta con algunos caracteres.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _abrir_coleccion():
    """Abre la colección 'mia_evidence' que creó la Fase 1 en data/chroma/."""
    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    return client.get_collection(config.CHROMA_COLLECTION)


def resumen(col):
    """Imprime el 'censo' de la base: total, reparto por fuente y docs únicos."""
    total = col.count()
    print("=" * 64)
    print(f" BASE VECTORIAL: {config.CHROMA_COLLECTION}   ({config.CHROMA_DIR})")
    print("=" * 64)
    print(f"Fragmentos (chunks) indexados : {total}")

    # Traemos SOLO los metadatos de todo (sin vectores → rápido y ligero).
    metas = col.get(include=["metadatas"])["metadatas"]

    por_fuente = collections.Counter(m.get("source") for m in metas)
    print("Reparto por fuente            :", dict(por_fuente))

    docs_unicos = {m.get("doc_id") for m in metas}
    print(f"Documentos únicos (PMID/NCT)  : {len(docs_unicos)}")

    # Tamaño del vector: pedimos 1 registro CON su embedding y medimos su longitud.
    muestra = col.peek(1)
    dim = len(muestra["embeddings"][0]) if muestra.get("embeddings") is not None else "?"
    print(f"Dimensiones del vector        : {dim}")
    print()


def ejemplos(col, n):
    """Muestra n registros reales: id, metadatos y un trozo del texto."""
    print("-" * 64)
    print(f" {n} EJEMPLOS DE REGISTROS")
    print("-" * 64)
    datos = col.get(limit=n, include=["documents", "metadatas"])
    for i, (doc_id, texto, meta) in enumerate(
        zip(datos["ids"], datos["documents"], datos["metadatas"]), start=1
    ):
        print(f"\n[{i}] id = {doc_id}")
        print(f"    fuente : {meta.get('source')}   doc_id: {meta.get('doc_id')}")
        print(f"    título : {(meta.get('title') or '')[:70]}")
        print(f"    fármacos: {meta.get('drugs')}")
        print(f"    url    : {meta.get('url')}")
        print(f"    texto  : {texto[:180].strip()} ...")
    print()


def buscar(consulta, top_k=5):
    """Búsqueda REAL: usa el mismo RAG de MIA para recuperar los más parecidos.

    Sirve para 'ver' lo que hace la base cuando le preguntas: convierte la
    consulta en vector y devuelve los fragmentos más cercanos por significado.
    """
    from src import rag  # import perezoso: solo si se pide búsqueda (carga el modelo)

    print("-" * 64)
    print(f" BÚSQUEDA: {consulta!r}")
    print("-" * 64)
    fragmentos = rag.retrieve(consulta, top_k=top_k)
    for i, frag in enumerate(fragmentos[:top_k], start=1):
        meta = frag["metadata"]
        print(f"\n[{i}] similitud = {frag['similarity']:.3f}   "
              f"{meta.get('source')}:{meta.get('doc_id')}")
        print(f"    {(meta.get('title') or '')[:70]}")
        print(f"    {frag['text'][:160].strip()} ...")
    print()


def main():
    parser = argparse.ArgumentParser(description="Inspecciona la base vectorial de MIA.")
    parser.add_argument("--ejemplos", type=int, default=3,
                        help="cuántos registros de ejemplo mostrar (por defecto 3)")
    parser.add_argument("--buscar", type=str, default=None,
                        help="haz una búsqueda real por significado (en inglés)")
    args = parser.parse_args()

    col = _abrir_coleccion()
    resumen(col)
    if args.ejemplos > 0:
        ejemplos(col, args.ejemplos)
    if args.buscar:
        buscar(args.buscar)


if __name__ == "__main__":
    main()
