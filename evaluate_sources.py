"""
evaluate_sources.py — ¿QUÉ FUENTE aporta la evidencia, y con qué umbral?

Hermano de `evaluate_embeddings.py`, pero mide un EJE DISTINTO:

  - evaluate_embeddings.py → ¿qué EMBEDDING recupera mejor? (MedCPT vs OpenAI).
                             Es la tesis del capstone. NO se toca desde aquí.
  - ESTE archivo           → ¿qué FUENTE (PubMed vs ClinicalTrials.gov) aporta la
                             evidencia de cada pregunta, y está bien calibrado el
                             umbral para las dos?

────────────────────────────────────────────────────────────────────────────
POR QUÉ EXISTE
────────────────────────────────────────────────────────────────────────────
Al indexar los resultados de ClinicalTrials (eventos adversos por brazo) se
observó que esos documentos casi nunca salían en las respuestas. Se probaron dos
explicaciones y las dos fallaron al medirlas:

  1. "Es que hay muchos más papers que ensayos"  → NO: la tasa base de CT.gov es
     el 11,5% de los chunks, y la recuperación se va al 0% en preguntas genéricas
     y al 50% en preguntas de cifras por brazo. No es proporción, es registro.
  2. "Hay que reservarle cupo / reformular la pregunta" → tampoco: los cupos no
     traían nada porque los documentos NO SUPERABAN EL UMBRAL.

La hipótesis que queda —y que este script mide en serio— es que el umbral de
evidencia (`config.SIMILARITY_THRESHOLD` = 66.0) se calibró midiendo SOLO contra
PubMed, y los registros de ensayos viven en una escala algo más baja. Si es
cierto, el arreglo no es forzar nada: es un umbral POR FUENTE, medido igual que
se midió el 66.0 original (relevantes vs ajenas, y buscar el hueco limpio).

────────────────────────────────────────────────────────────────────────────
QUÉ MIDE
────────────────────────────────────────────────────────────────────────────
Para cada backend y cada FUENTE por separado:

  A) CALIBRACIÓN DEL UMBRAL
     - similitud del mejor documento en preguntas RELEVANTES (deberían pasar),
     - ídem en preguntas AJENAS de control (no deberían pasar nunca),
     - el HUECO entre ambas y el umbral que caería en su centro.
     Es la misma metodología del umbral 66.0, pero por fuente y con más muestra.

  B) QUIÉN APORTA LA EVIDENCIA
     - cuántos de los top-k son de cada fuente,
     - cuántas preguntas pierden a CT.gov SOLO por el umbral (el dato clave:
       si es alto, el umbral está mal; si es cero, la hipótesis se cae).

  C) ¿LLEGA EL TIPO DE DATO QUE PIDE LA PREGUNTA?
     En las preguntas de SEGURIDAD no basta con recuperar el fármaco correcto:
     hace falta que entre lo recuperado haya cifras de eventos adversos. Se
     comprueba de forma determinista sobre el texto recuperado.

Uso:
    ./.venv/Scripts/python.exe evaluate_sources.py
Salidas en data/:
    evaluation_sources.csv     (detalle por pregunta × backend × fuente)
    evaluation_thresholds.csv  (calibración del umbral por backend × fuente)
"""

import sys
import csv
import statistics
from pathlib import Path

import chromadb

sys.path.append(str(Path(__file__).resolve().parent))
import config
from src import embeddings, outcomes
# Reutilizamos el gold set de la evaluación de la tesis: una sola fuente de
# verdad para las preguntas, y así los dos scripts hablan de lo mismo.
from evaluate_embeddings import GOLD_BASIC, GOLD_HARD, _es_relevante

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ==========================================================================
# PREGUNTAS DE SEGURIDAD  ← Joan (experto): revisa/edita libremente.
# ==========================================================================
# El gold set original es casi todo de EFICACIA. Estas preguntas son de
# SEGURIDAD, que es justo donde ClinicalTrials debería brillar: sus tablas de
# eventos adversos por brazo no existen en ningún abstract de PubMed.
# 'drugs' = fármaco(s) que deberían recuperarse. 'needs_ae' = además debería
# llegar alguna cifra de evento adverso, no solo el fármaco correcto.
GOLD_SAFETY = [
    {"q": "What adverse events were reported with dupilumab versus placebo?", "drugs": ["dupilumab"], "needs_ae": True},
    {"q": "How frequent is conjunctivitis in patients treated with dupilumab?", "drugs": ["dupilumab"], "needs_ae": True},
    {"q": "What is the rate of injection-site reactions with tralokinumab?", "drugs": ["tralokinumab"], "needs_ae": True},
    {"q": "What serious adverse events occurred with upadacitinib?", "drugs": ["upadacitinib"], "needs_ae": True},
    {"q": "How many patients discontinued abrocitinib due to adverse events?", "drugs": ["abrocitinib"], "needs_ae": True},
    {"q": "What is the incidence of herpes zoster with JAK inhibitors in atopic dermatitis?", "drugs": ["upadacitinib", "baricitinib", "abrocitinib"], "needs_ae": True},
    {"q": "Is acne a common side effect of upadacitinib?", "drugs": ["upadacitinib"], "needs_ae": True},
    {"q": "What nasopharyngitis rates were observed with lebrikizumab?", "drugs": ["lebrikizumab"], "needs_ae": True},
    {"q": "What safety signals have been reported for nemolizumab?", "drugs": ["nemolizumab"], "needs_ae": False},
    {"q": "How does the safety of baricitinib compare with placebo in eczema?", "drugs": ["baricitinib"], "needs_ae": True},
    {"q": "Were there any deaths or malignancies reported with JAK inhibitors for eczema?", "drugs": ["upadacitinib", "baricitinib", "abrocitinib"], "needs_ae": False},
    {"q": "What laboratory abnormalities were seen with upadacitinib treatment?", "drugs": ["upadacitinib"], "needs_ae": False},
]

# ==========================================================================
# PREGUNTAS AJENAS (control negativo)
# ==========================================================================
# Son el otro lado del umbral: NINGUNA debería superar el corte en ninguna
# fuente. Sin ellas no se puede calibrar nada — un umbral solo tiene sentido si
# sabes qué queda a cada lado. Se mezclan tres tipos a propósito:
#   - totalmente ajenas (Francia, pan),
#   - médicas pero de OTRA patología (malaria, diabetes): las difíciles,
#   - plausibles pero fuera del corpus (fármacos de otra especialidad).
CONTROL_OFFTOPIC = [
    "What is the capital of France?",
    "How do I bake sourdough bread?",
    "Best programming language for web development",
    "History of the Roman Empire",
    "How do I change a car tyre?",
    "Treatment of malaria in children",
    "What is the first-line therapy for type 2 diabetes?",
    "Management of acute myocardial infarction",
    "Antibiotic choice for community-acquired pneumonia",
    "Screening guidelines for colorectal cancer",
    "Efficacy of metformin on glycated haemoglobin",
    "Adalimumab for rheumatoid arthritis remission rates",
    "Statin therapy and cardiovascular risk reduction",
    "Chemotherapy regimens for advanced lung cancer",
    "Vaccination schedule for infants",
]

SOURCES = [("PubMed", "pubmed"), ("ClinicalTrials", "clinicaltrials")]

BACKENDS = [
    ("MedCPT (biomédico, MIA)", "medcpt", config.collection_name("medcpt")),
    ("OpenAI 3-small (Centivence)", "openai", config.collection_name("openai")),
]

TOP_K = config.TOP_K


def _umbral_actual(backend):
    """Umbral vigente para ese backend (la escala depende de la métrica)."""
    if backend == "medcpt":
        return 66.0
    if backend == "openai":
        return 0.35
    return 0.70


def _top_docs(col, qv, k, source=None):
    """Hasta k DOCUMENTOS únicos, opcionalmente filtrando por fuente.

    Devuelve [(metadata, similitud, texto)]. El texto hace falta para comprobar
    si llegó el TIPO de dato que la pregunta necesita (cifras de AE).
    """
    kwargs = {"query_embeddings": [qv], "n_results": k * 6,
              "include": ["metadatas", "distances", "documents"]}
    if source:
        kwargs["where"] = {"source": source}
    res = col.query(**kwargs)
    vistos, docs = set(), []
    for meta, dist, doc in zip(res["metadatas"][0], res["distances"][0],
                               res["documents"][0]):
        did = meta.get("doc_id")
        if did in vistos:
            continue
        vistos.add(did)
        docs.append((meta, 1.0 - dist, doc))
        if len(docs) >= k:
            break
    return docs


def _trae_cifras_ae(docs):
    """¿Hay alguna cifra de EVENTO ADVERSO entre lo recuperado?

    Determinista, con el mismo extractor que alimenta la UI: si `outcomes` saca
    al menos un punto de tipo 'safety', la respuesta podrá dar una cifra real en
    vez de un "se han descrito efectos adversos".
    """
    for _meta, _sim, texto in docs:
        for p in outcomes.extract_outcomes(texto, doc_n=1, max_points=30):
            if p.get("kind") == "safety":
                return True
    return False


def evaluar_backend(col, etiqueta, backend):
    """Devuelve (filas_detalle, filas_umbral) para UN backend."""
    config.EMBEDDING_BACKEND = backend          # embed_query usa este backend
    umbral = _umbral_actual(backend)
    filas, sims_rel, sims_aje = [], {s: [] for _, s in SOURCES}, {s: [] for _, s in SOURCES}

    tiers = [("Basico", GOLD_BASIC), ("Dificil", GOLD_HARD), ("Seguridad", GOLD_SAFETY)]
    for tier, gold in tiers:
        for item in gold:
            qv = embeddings.embed_query(item["q"])

            # (a) Recuperación GLOBAL (como funciona MIA hoy): ¿quién gana?
            globales = _top_docs(col, qv, TOP_K)
            n_por_fuente = {s: sum(1 for m, _, _ in globales if m.get("source") == s)
                            for _, s in SOURCES}

            # (b) Recuperación POR FUENTE: cuál es lo MEJOR que tiene cada una,
            #     aunque hoy no llegue al top-5 global.
            por_fuente = {}
            for _et, s in SOURCES:
                docs = _top_docs(col, qv, TOP_K, source=s)
                mejor = docs[0][1] if docs else float("-inf")
                sims_rel[s].append(mejor)
                por_fuente[s] = {
                    "mejor_sim": mejor,
                    "pasa": mejor >= umbral,
                    "relevantes": sum(1 for m, _, _ in docs
                                      if _es_relevante(m, item["drugs"])),
                    "trae_ae": _trae_cifras_ae(docs),
                }

            ct = por_fuente["clinicaltrials"]
            filas.append({
                "backend": etiqueta, "nivel": tier, "pregunta": item["q"],
                "objetivo": ";".join(item["drugs"]),
                "top5_pubmed": n_por_fuente["pubmed"],
                "top5_ct": n_por_fuente["clinicaltrials"],
                "mejor_sim_pubmed": round(por_fuente["pubmed"]["mejor_sim"], 3),
                "mejor_sim_ct": round(ct["mejor_sim"], 3),
                "ct_pasa_umbral": int(ct["pasa"]),
                "ct_relevantes": ct["relevantes"],
                # EL DATO CLAVE: CT tenía el fármaco correcto pero el umbral lo tumbó.
                "ct_perdido_por_umbral": int(ct["relevantes"] > 0 and not ct["pasa"]),
                "pubmed_trae_cifras_ae": int(por_fuente["pubmed"]["trae_ae"]),
                "ct_trae_cifras_ae": int(ct["trae_ae"]),
                "necesita_ae": int(bool(item.get("needs_ae"))),
            })

    # Control negativo: lo que NO debe pasar el umbral, por fuente.
    for q in CONTROL_OFFTOPIC:
        qv = embeddings.embed_query(q)
        for _et, s in SOURCES:
            docs = _top_docs(col, qv, 1, source=s)
            if docs:
                sims_aje[s].append(docs[0][1])

    # Calibración: ¿dónde está el hueco entre relevantes y ajenas, por fuente?
    filas_umbral = []
    for et, s in SOURCES:
        rel, aje = sims_rel[s], sims_aje[s]
        if not rel or not aje:
            continue
        # Percentiles, no min/max: un solo caso raro no debe mover el umbral.
        p05_rel = statistics.quantiles(rel, n=20)[0]     # 5º percentil de relevantes
        p95_aje = statistics.quantiles(aje, n=20)[18]    # 95º percentil de ajenas
        hueco = p05_rel - p95_aje
        filas_umbral.append({
            "backend": etiqueta, "fuente": et,
            "n_relevantes": len(rel), "n_ajenas": len(aje),
            "rel_min": round(min(rel), 2), "rel_p05": round(p05_rel, 2),
            "rel_mediana": round(statistics.median(rel), 2),
            "aje_p95": round(p95_aje, 2), "aje_max": round(max(aje), 2),
            "hueco": round(hueco, 2),
            "umbral_actual": umbral,
            "umbral_sugerido": round((p05_rel + p95_aje) / 2, 1) if hueco > 0 else None,
            "hueco_limpio": int(hueco > 0),
        })
    return filas, filas_umbral


def run():
    print("=" * 84)
    print(" Evaluación POR FUENTE · PubMed vs ClinicalTrials.gov · calibración de umbral")
    print("=" * 84)
    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    backend_original = config.EMBEDDING_BACKEND

    todas, umbrales = [], []
    try:
        for etiqueta, backend, coleccion in BACKENDS:
            try:
                col = client.get_collection(coleccion)
            except Exception:
                print(f"\n[AVISO] falta la colección '{coleccion}'. La salto.")
                continue
            n_ct = len(col.get(where={"source": "clinicaltrials"},
                               include=[], limit=50000)["ids"])
            print(f"\n→ {etiqueta}   ({col.count():,} chunks · {n_ct:,} de CT.gov)")
            filas, f_umbral = evaluar_backend(col, etiqueta, backend)
            todas.extend(filas); umbrales.extend(f_umbral)

            perdidas = sum(f["ct_perdido_por_umbral"] for f in filas)
            seg = [f for f in filas if f["nivel"] == "Seguridad"]
            ae_pm = sum(f["pubmed_trae_cifras_ae"] for f in seg)
            ae_ct = sum(f["ct_trae_cifras_ae"] for f in seg)
            print(f"   preguntas donde CT tenía el fármaco correcto pero el "
                  f"umbral lo tumbó: {perdidas}/{len(filas)}")
            print(f"   seguridad · llegan cifras de AE:  PubMed {ae_pm}/{len(seg)}   "
                  f"CT.gov {ae_ct}/{len(seg)}")
    finally:
        config.EMBEDDING_BACKEND = backend_original   # no dejamos el módulo tocado

    if umbrales:
        print("\n" + "=" * 84)
        print("CALIBRACIÓN DEL UMBRAL (relevantes vs ajenas, por fuente)")
        print("-" * 84)
        print(f"{'BACKEND':<28}{'FUENTE':<16}{'rel p05':>9}{'aje p95':>9}"
              f"{'hueco':>8}{'actual':>9}{'sugerido':>10}")
        for r in umbrales:
            sug = r["umbral_sugerido"] if r["umbral_sugerido"] is not None else "—"
            print(f"{r['backend']:<28}{r['fuente']:<16}{r['rel_p05']:>9.2f}"
                  f"{r['aje_p95']:>9.2f}{r['hueco']:>8.2f}{r['umbral_actual']:>9}"
                  f"{str(sug):>10}")
        print("\nLectura: 'hueco' positivo = las relevantes se separan de las ajenas y")
        print("el umbral sugerido es seguro. Negativo = NO hay umbral válido para esa")
        print("fuente (fue justo lo que pasó con MedCPT y coseno; ver CHANGELOG).")

    for filas, nombre in ((todas, "evaluation_sources.csv"),
                          (umbrales, "evaluation_thresholds.csv")):
        if not filas:
            continue
        salida = config.DATA_DIR / nombre
        with open(salida, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(filas[0].keys()))
            w.writeheader(); w.writerows(filas)
        print(f"Guardado: {salida}")


if __name__ == "__main__":
    run()
