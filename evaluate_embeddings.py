"""
evaluate_embeddings.py — Compara la CALIDAD DE RECUPERACIÓN: MedCPT vs OpenAI.

Objetivo del capstone: ¿qué embedding recupera MEJOR evidencia para preguntas
biomédicas? Comparamos MedCPT (biomédico, producto MIA) con text-embedding-3-small
(generalista de OpenAI = "línea Centivence"), pasando LAS MISMAS preguntas por las
DOS colecciones (que contienen LOS MISMOS chunks; solo cambia el vector).

Medimos la recuperación con métricas del "Grupo A" (no dependen del LLM):
  - precision@k : de los k documentos recuperados, ¿qué fracción es del fármaco correcto?
  - hit@1       : ¿el PRIMER documento es del fármaco correcto? (0/1)
  - MRR         : 1/posición del primer documento correcto (premia rankear bien lo relevante)

RELEVANCIA (señal objetiva y automática): un documento recuperado es "correcto" para
una pregunta si su metadata `drugs` contiene alguno de los fármacos objetivo de esa
pregunta. Es un PROXY (no un juicio humano por PMID), pero es objetivo, reproducible
y suficiente para comparar dos sistemas sobre las mismas preguntas. Limitación a
declarar en el TFM: mide "recuperó el fármaco correcto", no "recuperó EL mejor paper".

NO usa el LLM ni la API para nada más que embeber las preguntas (coste ínfimo).

Uso (requiere haber ejecutado antes index_openai.py):
    ./.venv/Scripts/python.exe evaluate_embeddings.py
Resultados: se imprime la tabla y se guardan en data/evaluation_embeddings.csv
"""

import sys
import csv
from pathlib import Path

import chromadb

sys.path.append(str(Path(__file__).resolve().parent))
import config
from src import embeddings

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ==========================================================================
# CONJUNTO DE PRUEBA (gold set).  ← Joan (experto): revisa/edita libremente.
# ==========================================================================
# DOS NIVELES a propósito:
#   BÁSICO   → la pregunta NOMBRA el fármaco. Recuperación fácil (efecto techo:
#              casi cualquier embedding acierta). Sirve de control/sanidad.
#   DIFÍCIL  → sinónimos ("eczema", "neurodermatitis") y MECANISMOS ("anti-IL-13",
#              "inhibidor JAK1") SIN nombrar el fármaco. Exige entender semántica
#              biomédica → AQUÍ es donde un embedding biomédico (MedCPT) debería
#              batir a uno generalista (OpenAI). Es el nivel que sostiene la tesis.
# 'drugs' = fármaco(s) que DEBERÍAN recuperarse (minúscula, como en la metadata).
#
# ⚠ Joan: valida SOBRE TODO los mapeos mecanismo→fármaco del nivel DIFÍCIL
# (dupilumab=IL-4Rα; tralokinumab/lebrikizumab=IL-13; nemolizumab=IL-31;
# upadacitinib/abrocitinib=JAK1 selectivo; baricitinib=JAK1/2). Corrige si procede.
GOLD_BASIC = [
    {"q": "How effective is dupilumab for moderate-to-severe atopic dermatitis?", "drugs": ["dupilumab"]},
    {"q": "What EASI-75 response rates have been reported for lebrikizumab?", "drugs": ["lebrikizumab"]},
    {"q": "Does tralokinumab reduce itch in patients with atopic dermatitis?", "drugs": ["tralokinumab"]},
    {"q": "What is the efficacy of nemolizumab for pruritus in atopic dermatitis?", "drugs": ["nemolizumab"]},
    {"q": "How well does upadacitinib control moderate-to-severe atopic dermatitis?", "drugs": ["upadacitinib"]},
    {"q": "Is abrocitinib effective for atopic dermatitis in adults?", "drugs": ["abrocitinib"]},
    {"q": "What is the clinical efficacy of baricitinib in atopic dermatitis?", "drugs": ["baricitinib"]},
    {"q": "What are the most common adverse events of upadacitinib in atopic dermatitis?", "drugs": ["upadacitinib"]},
    {"q": "What is known about conjunctivitis as a side effect of dupilumab?", "drugs": ["dupilumab"]},
    {"q": "How does dupilumab compare with tralokinumab in efficacy and safety?", "drugs": ["dupilumab", "tralokinumab"]},
    {"q": "Is lebrikizumab effective in difficult-to-treat areas such as the head and neck?", "drugs": ["lebrikizumab"]},
    {"q": "What is the evidence for dupilumab in adolescents with atopic dermatitis?", "drugs": ["dupilumab"]},
    {"q": "What is the safety profile of JAK inhibitors in atopic dermatitis?", "drugs": ["upadacitinib", "baricitinib", "abrocitinib"]},
]

GOLD_HARD = [
    {"q": "Which biologics that block IL-13 are used to treat eczema?", "drugs": ["tralokinumab", "lebrikizumab"]},
    {"q": "Is there an antibody targeting the IL-4 receptor alpha for atopic eczema?", "drugs": ["dupilumab"]},
    {"q": "What therapy targeting IL-31 signaling relieves itch in atopic eczema?", "drugs": ["nemolizumab"]},
    {"q": "Which oral JAK inhibitors are used for severe eczema?", "drugs": ["upadacitinib", "baricitinib", "abrocitinib"]},
    {"q": "What JAK1-selective inhibitors treat atopic eczema?", "drugs": ["upadacitinib", "abrocitinib"]},
    {"q": "Monoclonal antibody options for neurodermatitis with intense pruritus?", "drugs": ["dupilumab", "tralokinumab", "lebrikizumab", "nemolizumab"]},
    {"q": "Which anti-interleukin therapies reduce flare severity in chronic eczema?", "drugs": ["dupilumab", "tralokinumab", "lebrikizumab", "nemolizumab"]},
    {"q": "Systemic small-molecule treatments for refractory atopic eczema itch?", "drugs": ["upadacitinib", "baricitinib", "abrocitinib"]},
]

# Niveles a evaluar (etiqueta, lista de preguntas).
TIERS = [
    ("Basico (nombra el farmaco)", GOLD_BASIC),
    ("Dificil (sinonimo/mecanismo)", GOLD_HARD),
]

# Backends a comparar: (etiqueta, EMBEDDING_BACKEND, nombre de colección Chroma).
BACKENDS = [
    ("MedCPT (biomédico, MIA)", "medcpt", config.collection_name("medcpt")),
    ("OpenAI 3-small (Centivence)", "openai", config.collection_name("openai")),
]

TOP_K = config.TOP_K   # nº de documentos únicos que evaluamos por pregunta (5)


def _es_relevante(meta, objetivo):
    """True si el doc recuperado menciona alguno de los fármacos objetivo."""
    drugs = (meta.get("drugs") or "").lower()
    return any(d.lower() in drugs for d in objetivo)


def _top_docs(collection, query_vec, k):
    """Devuelve hasta k DOCUMENTOS únicos (dedup por doc_id), en orden de ranking."""
    res = collection.query(query_embeddings=[query_vec], n_results=k * 5,
                           include=["metadatas"])
    vistos, docs = set(), []
    for meta in res["metadatas"][0]:
        did = meta.get("doc_id")
        if did in vistos:
            continue
        vistos.add(did)
        docs.append(meta)
        if len(docs) >= k:
            break
    return docs


def evaluar(col, etiqueta_backend, tier, gold):
    """precision@k, hit@1 y MRR de UN backend en UN nivel (tier). Devuelve
    (resumen, filas_detalle). Asume config.EMBEDDING_BACKEND ya fijado fuera."""
    precisiones, hits, rrs, filas = [], [], [], []
    for item in gold:
        qv = embeddings.embed_query(item["q"])
        docs = _top_docs(col, qv, TOP_K)
        rel = [_es_relevante(m, item["drugs"]) for m in docs]

        precision = sum(rel) / TOP_K
        hit1 = 1 if (rel and rel[0]) else 0
        rr = 0.0
        for i, r in enumerate(rel, start=1):
            if r:
                rr = 1.0 / i
                break
        precisiones.append(precision); hits.append(hit1); rrs.append(rr)
        filas.append({"backend": etiqueta_backend, "nivel": tier, "pregunta": item["q"],
                      "objetivo": ";".join(item["drugs"]),
                      "precision@k": round(precision, 3), "hit@1": hit1,
                      "rr": round(rr, 3), "docs_relevantes": sum(rel)})

    n = len(gold)
    resumen = {"backend": etiqueta_backend, "nivel": tier,
               "precision@k": sum(precisiones) / n, "hit@1": sum(hits) / n,
               "MRR": sum(rrs) / n}
    return resumen, filas


def run():
    print("=" * 78)
    print(f" Evaluación de recuperación · niveles: "
          f"{', '.join(t for t, _ in TIERS)} · top-{TOP_K}")
    print("=" * 78)
    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))

    resumenes, todas_filas = [], []
    for etiqueta, backend, coleccion in BACKENDS:
        print(f"\n→ {etiqueta}  (colección: {coleccion})")
        try:
            col = client.get_collection(coleccion)
        except Exception:
            print(f"   [ERROR] falta la colección '{coleccion}'. "
                  f"Ejecuta primero index_openai.py (para OpenAI). La salto.")
            continue
        config.EMBEDDING_BACKEND = backend   # embed_query usará este backend
        for tier, gold in TIERS:
            resumen, filas = evaluar(col, etiqueta, tier, gold)
            resumenes.append(resumen); todas_filas.extend(filas)
            print(f"   {tier:<32} precision@{TOP_K}={resumen['precision@k']:.3f}  "
                  f"hit@1={resumen['hit@1']:.3f}  MRR={resumen['MRR']:.3f}")

    # Tabla comparativa final (backend × nivel).
    print("\n" + "=" * 78)
    print(f"{'BACKEND':<30}{'NIVEL':<32}{'prec@k':>8}{'hit@1':>8}{'MRR':>8}")
    print("-" * 78)
    for r in resumenes:
        print(f"{r['backend']:<30}{r['nivel']:<32}"
              f"{r['precision@k']:>8.3f}{r['hit@1']:>8.3f}{r['MRR']:>8.3f}")

    # Guardamos el detalle por pregunta a CSV (para el repo/TFM).
    if todas_filas:
        salida = config.DATA_DIR / "evaluation_embeddings.csv"
        with open(salida, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(todas_filas[0].keys()))
            w.writeheader(); w.writerows(todas_filas)
        print(f"\nDetalle por pregunta guardado en: {salida}")


if __name__ == "__main__":
    run()
