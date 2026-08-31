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
    from . import embeddings, rag, citations
except (ImportError, ValueError):
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    import config
    from src import embeddings, rag, citations

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

    # Embebemos con el backend EXPLÍCITO, sin mutar config.EMBEDDING_BACKEND.
    # CLAVE: en Streamlit todas las páginas comparten el MISMO proceso, así que
    # mutar el config global aquí "contaminaba" la página de chat (la pregunta se
    # embebía luego con OpenAI, 1536 dim, contra la colección MedCPT de 768 dim →
    # crash de Chroma). Con el parámetro explícito, esta página no deja rastro.
    query_vec = embeddings.embed_query(question, backend=backend)

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
            "title": meta.get("title") or "(untitled)",
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
# Respuesta REDACTADA por backend (para ver el efecto del embedding en el output)
# --------------------------------------------------------------------------

def answer_from_backend(question, backend, collection_name, top_k=None):
    """Genera una respuesta REDACTADA usando SOLO la evidencia que recupera `backend`.

    ¿Por qué es una comparación justa (y qué compara)? El LLM redactor es el MISMO
    (`rag._generate_answer`, OpenBioLLM local) para las dos columnas. Lo ÚNICO que
    cambia es qué evidencia le llega, porque cada embedding recupera y ordena
    distinto. Así, cualquier diferencia entre las dos respuestas es atribuible al
    EMBEDDING — que es justo la tesis del capstone. (Es la extensión natural de
    `retrieve_ranked`: aquel enseña QUÉ recupera cada uno; este, en qué se traduce
    esa recuperación cuando el mismo modelo redacta a partir de ella.)

    Para que la respuesta se vea IGUAL DE BIEN que en el chat principal, reutiliza
    EXACTAMENTE el mismo pipeline de redacción de `rag`: `_build_context` (contexto
    [Doc N] + fuentes ENRIQUECIDAS con outcomes y acceso), `_generate_answer`
    (prompt profesional + intención) y el aviso de "paper de pago". Devuelve
    {answer, sources}.

    ¿No acopla esto a la colección de producto? No: `_build_context` extrae los
    outcomes con `rag._full_doc_text(doc_id)`, que lee el TEXTO por doc_id — y el
    texto es IDÉNTICO en las dos colecciones (solo cambia el vector). No muta el
    estado global → seguro en Streamlit multipágina.
    """
    if top_k is None:
        top_k = config.TOP_K

    # 1) Recuperar con el backend EXPLÍCITO contra SU colección (sin tocar config).
    query_vec = embeddings.embed_query(question, backend=backend)
    col = _collection(collection_name)
    res = col.query(
        query_embeddings=[query_vec],
        n_results=top_k * 4,
        include=["documents", "metadatas", "distances"],
    )
    fragmentos = [
        {"text": t, "metadata": m, "similarity": 1.0 - d}
        for t, m, d in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
    ]
    if not fragmentos:
        return {"answer": "No evidence was retrieved to write an answer from.",
                "sources": []}

    # 2) MISMA construcción de contexto que el chat principal (fuentes ricas:
    #    outcomes, acceso, n_fragments, snippet).
    contexto, fuentes = rag._build_context(fragmentos)

    # 3) MISMA redacción que rag.answer: prompt profesional + intención (detectada
    #    dentro de _generate_answer), reparto y limpieza deterministas de citas, y
    #    aviso de acceso restringido para fuentes de pago citadas.
    salida = rag._generate_answer(contexto, question)
    if salida is None:
        salida = ("Could not generate a reliable answer from this evidence "
                  "(the model degenerated after several retries).")
    else:
        salida = citations.redistribute_citations(salida, fuentes)
    salida = citations.strip_invalid_citations(salida, len(fuentes))
    salida = rag._append_access_notice(salida, fuentes)
    return {"answer": salida, "sources": fuentes}


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
