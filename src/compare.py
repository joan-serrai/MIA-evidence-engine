"""
src/compare.py — Recuperación COMPARATIVA MedCPT vs OpenAI para la interfaz.

La tesis del capstone es la CALIDAD DE RECUPERACIÓN del embedding: dada una
pregunta, ¿qué evidencia trae cada modelo y en qué orden? Este módulo pide a DOS
backends (dos colecciones de ChromaDB con LOS MISMOS textos, solo cambia el vector)
los documentos que recupera cada uno, para enseñarlos lado a lado en la página de
comparación.

Es la versión "solo recuperación" del pipeline de `rag.py`: NO llama al LLM. Es
rápida (unos segundos) y suficiente, porque el modelo redactor es el MISMO para
ambos → no es el factor que se compara. Reutiliza las utilidades de `rag.py`
(agrupar por documento, recortar el fragmento) para no duplicar lógica.

IMPORTANTE sobre las escalas: la "similitud" NO significa lo mismo en los dos
modelos — MedCPT usa PRODUCTO ESCALAR (~55-75) y OpenAI COSENO (0-1). Por eso los
% de confianza de cada columna NO son directamente comparables entre sí. Lo que SÍ
se compara de forma justa es el RANKING (qué pone cada uno arriba) y cuántos de los
documentos recuperados son del fármaco correcto.
"""

import sys
from pathlib import Path

import chromadb

try:
    from .. import config
    from . import embeddings, rag
except (ImportError, ValueError):
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    import config
    from src import embeddings, rag

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# Colecciones abiertas (una por nombre; abrir ChromaDB es costoso → se cachea).
_COLLECTIONS = {}


def _collection(name):
    if name not in _COLLECTIONS:
        client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        _COLLECTIONS[name] = client.get_collection(name)
    return _COLLECTIONS[name]


def _confidence_pct(sim, backend):
    """Calibra la similitud cruda a un % 0-100 legible, SEGÚN el backend.

    MedCPT (producto escalar ~55-75): banda medida 55→0%, 80→100%.
    OpenAI (coseno 0-1): ×100. Es una CONFIANZA DE RECUPERACIÓN relativa a cada
    modelo, no una probabilidad ni algo comparable entre modelos (ver cabecera).
    """
    if backend == "medcpt":
        pct = (sim - 55.0) / (80.0 - 55.0) * 100.0
    else:
        pct = sim * 100.0
    return int(round(max(0.0, min(100.0, pct))))


def retrieve_ranked(question, backend, collection_name, target_drugs=None, top_k=None):
    """Recupera los `top_k` DOCUMENTOS que un backend trae para `question`.

    Devuelve un dict:
      {
        "docs": [ {rank, title, doc_id, source, url, drugs, similarity,
                   confidence, snippet, on_target}, ... ],
        "n_on_target": nº de docs del fármaco correcto (o None si no hay objetivo),
        "hit1": ¿el nº1 es del fármaco correcto? (True/False/None),
        "total": nº de documentos devueltos,
      }

    `target_drugs` (opcional): lista de fármacos que DEBERÍAN recuperarse para esta
    pregunta (p. ej. ['dupilumab']). Si se da, marca cada doc como on_target y
    calcula el resumen (n_on_target, hit1). Con preguntas libres se deja a None.
    """
    if top_k is None:
        top_k = config.TOP_K

    # Fijamos el backend para que embed_query use la torre/modelo correcto.
    config.EMBEDDING_BACKEND = backend
    query_vec = embeddings.embed_query(question)

    col = _collection(collection_name)
    res = col.query(
        query_embeddings=[query_vec],
        n_results=top_k * 4,   # sobre-recuperamos para dedup por documento
        include=["documents", "metadatas", "distances"],
    )

    # Reutilizamos el formato de rag para poder agrupar por documento único.
    fragmentos = []
    for texto, meta, dist in zip(res["documents"][0], res["metadatas"][0],
                                 res["distances"][0]):
        # similitud = 1 - distancia: da PRODUCTO ESCALAR con MedCPT (espacio "ip")
        # y COSENO con OpenAI (espacio "cosine"). Misma fórmula, escala distinta.
        fragmentos.append({"text": texto, "metadata": meta,
                           "similarity": 1.0 - dist})

    documentos = rag._group_by_document(fragmentos, top_k)

    targets = [d.lower() for d in (target_drugs or [])]
    docs = []
    for i, doc in enumerate(documentos, start=1):
        meta = doc["metadata"]
        drugs_raw = meta.get("drugs") or ""
        drug_list = [d.strip() for d in drugs_raw.split(";") if d.strip()]

        on_target = None
        if targets:
            low = drugs_raw.lower()
            on_target = any(t in low for t in targets)

        sim = doc["similarity"]
        docs.append({
            "rank": i,
            "title": meta.get("title") or "(sin título)",
            "doc_id": meta.get("doc_id"),
            "source": meta.get("source"),
            "url": meta.get("url"),
            "drugs": drug_list,
            "similarity": round(sim, 3),
            "confidence": _confidence_pct(sim, backend),
            "snippet": rag._excerpt(doc["chunks"][0][1]),
            "on_target": on_target,
        })

    n_on = sum(1 for d in docs if d["on_target"]) if targets else None
    hit1 = docs[0]["on_target"] if (targets and docs) else None
    return {"docs": docs, "n_on_target": n_on, "hit1": hit1, "total": len(docs)}


# --------------------------------------------------------------------------
# Prueba rápida aislada
# --------------------------------------------------------------------------
if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "antibody targeting the IL-4 receptor alpha for atopic eczema"
    print(f"Pregunta: {q}\n")
    for etiqueta, backend, coleccion in [
        ("MedCPT (MIA)", "medcpt", "mia_evidence_medcpt"),
        ("OpenAI (Centivence)", "openai", "mia_evidence_openai"),
    ]:
        r = retrieve_ranked(q, backend, coleccion, target_drugs=["dupilumab"])
        print(f"--- {etiqueta} · hit@1={r['hit1']} · on-target={r['n_on_target']}/{r['total']}")
        for d in r["docs"]:
            marca = "✓" if d["on_target"] else ("·" if d["on_target"] is False else " ")
            print(f"  {marca} #{d['rank']} [{d['confidence']}%] {d['drugs']}  {d['title'][:70]}")
        print()
