"""
evaluate_embeddings_semantics.py — CAPA 1: ¿ENTIENDEN los modelos el campo
semántico de los términos biomédicos?

Hermano de `evaluate_embeddings.py`, pero mide algo DISTINTO y más profundo:

  - evaluate_embeddings.py  → CAPA 2: ¿recupera el paper correcto de MI corpus?
                              (depende de qué papers están indexados en Chroma)
  - ESTE archivo            → CAPA 1: ¿el modelo SABE que "anti-IL-13" está cerca
                              de "tralokinumab"? Pura GEOMETRÍA del espacio de
                              embeddings. NO toca Chroma, NO toca el corpus, NO
                              toca el LLM. Solo embebe términos sueltos.

────────────────────────────────────────────────────────────────────────────
LA IDEA (ancla / positivo / negativo)
────────────────────────────────────────────────────────────────────────────
Un "triplete" es (ancla, positivo, negativo):
  - ancla    = el término desde el que medimos.
  - positivo = un término que, por biología REAL, DEBERÍA estar cerca.
  - negativo = un término (distractor) que DEBERÍA estar lejos.
El modelo "acierta" el triplete si  sim(ancla, positivo) > sim(ancla, negativo).
Solo miramos el ORDEN, no la magnitud → así SÍ podemos comparar modelos con
escalas distintas (coseno 0..1 de OpenAI/bge vs producto escalar ~50..75 de MedCPT).

────────────────────────────────────────────────────────────────────────────
LOS NIVELES (de fácil a difícil)
────────────────────────────────────────────────────────────────────────────
  Sinonimos          → ¿sabe que dos palabras nombran lo mismo? (control)
  Mecanismo->farmaco → ¿une un mecanismo con su fármaco? (LA TESIS: aquí el
                       biomédico debería batir al generalista)
  Clase              → ¿agrupa los fármacos por familia terapéutica?

────────────────────────────────────────────────────────────────────────────
LAS MÉTRICAS
────────────────────────────────────────────────────────────────────────────
  triplet_acc : % de tripletes con el positivo más cerca que el negativo.
                Binario (¿ganó?). 0.5 = azar.
  AUC         : probabilidad de que una pareja RELACIONADA (ancla-positivo)
                puntúe más alto que una AJENA (ancla-negativo). Mide el MARGEN
                y la robustez. 1.0 = separa perfecto; 0.5 = no distingue nada.

────────────────────────────────────────────────────────────────────────────
NOTA METODOLÓGICA (declararla en el TFM)
────────────────────────────────────────────────────────────────────────────
MedCPT es ASIMÉTRICO (dos torres). Para ser JUSTOS con él usamos su régimen de
entrenamiento (consulta·artículo):
  - el ANCLA (mecanismo/concepto = "consulta")  -> MedCPT-Query-Encoder
  - los CANDIDATOS (fármaco/término = "documento") -> MedCPT-Article-Encoder
Para bge y OpenAI (simétricos) hay un solo encoder, así que no aplica.
El mapa 2D embebe TODO el vocabulario con la torre de artículo (candidatos),
así que es ILUSTRATIVO; las métricas (arriba) son lo riguroso.

Uso (en TU máquina, con tu .venv y Ollama/torch instalados; OpenAI usa tu .env):
    ./.venv/Scripts/python.exe evaluate_embeddings_semantics.py
Salidas en data/:
    semantics_triplets.csv   (detalle por triplete y modelo)
    semantics_summary.csv    (tabla modelo x nivel: triplet_acc, AUC)
    semantics_accuracy.png   (barras: triplet_acc por nivel y modelo)
    semantics_map_<backend>.png  (mapa 2D de los términos, coloreado por familia)
"""

import sys
import csv
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parent))
import config
from src import embeddings

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ==========================================================================
# CONJUNTO DE PRUEBA (tripletes).  ← Joan (experto biotech): revisa/edita.
# ==========================================================================
# 'level' agrupa por TIPO de relación. 'anchor'/'positive'/'negative' en el
# texto tal como aparecería en la literatura (la forma exacta afecta al embedding).
#
# ⚠ DECISIÓN DE DISEÑO: dupilumab NO se usa como negativo de "anti-IL-13",
# porque dupilumab TAMBIÉN bloquea la señalización de IL-13 (vía IL-4Rα, el
# receptor compartido). Sería un negativo tramposo (está relacionado de verdad)
# y penalizaría injustamente al modelo que lo entiende bien.
TRIPLETS = [
    # ---- NIVEL 1: Sinónimos (control) --------------------------------------
    {"level": "Sinonimos", "anchor": "atopic dermatitis", "positive": "eczema",          "negative": "malaria"},
    {"level": "Sinonimos", "anchor": "atopic dermatitis", "positive": "neurodermatitis", "negative": "hypertension"},
    {"level": "Sinonimos", "anchor": "itch",              "positive": "pruritus",        "negative": "bone fracture"},

    # ---- NIVEL 2: Mecanismo -> fármaco (LA TESIS) --------------------------
    {"level": "Mecanismo->farmaco", "anchor": "anti-IL-13 monoclonal antibody",        "positive": "tralokinumab", "negative": "upadacitinib"},
    {"level": "Mecanismo->farmaco", "anchor": "anti-IL-13 monoclonal antibody",        "positive": "lebrikizumab", "negative": "baricitinib"},
    {"level": "Mecanismo->farmaco", "anchor": "IL-4 receptor alpha antagonist",        "positive": "dupilumab",    "negative": "nemolizumab"},
    {"level": "Mecanismo->farmaco", "anchor": "IL-31 receptor signaling inhibitor",    "positive": "nemolizumab",  "negative": "abrocitinib"},
    {"level": "Mecanismo->farmaco", "anchor": "JAK1-selective inhibitor",              "positive": "upadacitinib", "negative": "tralokinumab"},
    {"level": "Mecanismo->farmaco", "anchor": "JAK1 and JAK2 inhibitor",               "positive": "baricitinib",  "negative": "dupilumab"},

    # ---- NIVEL 3: Clase terapéutica ----------------------------------------
    {"level": "Clase", "anchor": "dupilumab",    "positive": "tralokinumab", "negative": "upadacitinib"},  # 2 biológicos vs 1 JAK
    {"level": "Clase", "anchor": "upadacitinib", "positive": "abrocitinib",  "negative": "dupilumab"},     # 2 JAK vs 1 biológico
]

# Vocabulario para el MAPA 2D (término -> familia, solo para colorear).
VOCAB = [
    # Biológicos anti-interleucina (anticuerpos monoclonales)
    ("dupilumab",    "Biologico anti-IL"),
    ("tralokinumab", "Biologico anti-IL"),
    ("lebrikizumab", "Biologico anti-IL"),
    ("nemolizumab",  "Biologico anti-IL"),
    # Inhibidores JAK (moléculas pequeñas orales)
    ("upadacitinib", "Inhibidor JAK"),
    ("baricitinib",  "Inhibidor JAK"),
    ("abrocitinib",  "Inhibidor JAK"),
    # Mecanismos de acción
    ("anti-IL-13 monoclonal antibody",     "Mecanismo"),
    ("IL-4 receptor alpha antagonist",     "Mecanismo"),
    ("IL-31 receptor signaling inhibitor", "Mecanismo"),
    ("JAK1-selective inhibitor",           "Mecanismo"),
    # Enfermedad y sinónimos
    ("atopic dermatitis", "Enfermedad/sintoma"),
    ("eczema",            "Enfermedad/sintoma"),
    ("neurodermatitis",   "Enfermedad/sintoma"),
    ("pruritus",          "Enfermedad/sintoma"),
    # Distractores (deberían quedar aparte)
    ("malaria",      "Distractor"),
    ("hypertension", "Distractor"),
    ("bone fracture","Distractor"),
]

# Backends a comparar: (etiqueta legible, valor de EMBEDDING_BACKEND).
BACKENDS = [
    ("MedCPT (biomedico)",            "medcpt"),
    ("OpenAI 3-small (generalista)",  "openai"),
    ("bge-small (generalista local)", "sentence-transformers"),
]


# --------------------------------------------------------------------------
# Acceso a embeddings — respeta la asimetría de MedCPT (ver nota metodológica).
# Se aíslan en dos funciones para poder testearlas con un embedder falso.
# --------------------------------------------------------------------------
def embed_anchor(term, backend):
    """El ancla actúa como 'consulta' (MedCPT: torre de preguntas)."""
    return np.asarray(embeddings.embed_query(term, backend=backend), dtype=float)


def embed_candidate(term, backend):
    """El candidato actúa como 'documento' (MedCPT: torre de artículo)."""
    if backend == "medcpt":
        return np.asarray(embeddings.embed_documents([term], backend=backend)[0], dtype=float)
    # Simétricos (bge, openai): un solo encoder.
    return np.asarray(embeddings.embed_query(term, backend=backend), dtype=float)


def sim(a, b):
    """Producto escalar. Para vectores normalizados (bge/openai) ES el coseno;
    para MedCPT (sin normalizar) es su métrica nativa. Comparamos SIEMPRE dentro
    del mismo modelo, así que la escala no importa."""
    return float(np.dot(a, b))


# --------------------------------------------------------------------------
# Métricas
# --------------------------------------------------------------------------
def _auc(related, unrelated):
    """AUC = P(una similitud relacionada > una ajena), por conteo de pares
    (equivale al estadístico de Mann-Whitney U). Sin dependencias externas.
    Empates cuentan 0.5. Devuelve None si falta alguna clase."""
    related, unrelated = list(related), list(unrelated)
    if not related or not unrelated:
        return None
    mayor = iguales = 0
    for r in related:
        for u in unrelated:
            if r > u:
                mayor += 1
            elif r == u:
                iguales += 1
    return (mayor + 0.5 * iguales) / (len(related) * len(unrelated))


def evaluar_backend(etiqueta, backend):
    """Embebe los tripletes con UN backend y calcula, por nivel y global,
    triplet_acc y AUC. Devuelve (filas_resumen, filas_detalle)."""
    # cache de vectores para no re-embeber el mismo término
    cache_anchor, cache_cand = {}, {}

    def va(t):
        if t not in cache_anchor:
            cache_anchor[t] = embed_anchor(t, backend)
        return cache_anchor[t]

    def vc(t):
        if t not in cache_cand:
            cache_cand[t] = embed_candidate(t, backend)
        return cache_cand[t]

    detalle = []
    for tri in TRIPLETS:
        s_pos = sim(va(tri["anchor"]), vc(tri["positive"]))
        s_neg = sim(va(tri["anchor"]), vc(tri["negative"]))
        detalle.append({
            "backend": etiqueta, "nivel": tri["level"], "ancla": tri["anchor"],
            "positivo": tri["positive"], "negativo": tri["negative"],
            "sim_pos": round(s_pos, 4), "sim_neg": round(s_neg, 4),
            "acierto": int(s_pos > s_neg),
        })

    # Resumen por nivel + global
    niveles = list(dict.fromkeys(t["level"] for t in TRIPLETS)) + ["GLOBAL"]
    resumen = []
    for niv in niveles:
        filas = detalle if niv == "GLOBAL" else [d for d in detalle if d["nivel"] == niv]
        acc = sum(d["acierto"] for d in filas) / len(filas)
        auc = _auc([d["sim_pos"] for d in filas], [d["sim_neg"] for d in filas])
        resumen.append({"backend": etiqueta, "nivel": niv, "n": len(filas),
                        "triplet_acc": round(acc, 3),
                        "AUC": None if auc is None else round(auc, 3)})
    return resumen, detalle


# --------------------------------------------------------------------------
# Mapa 2D de los términos (UMAP si está disponible; si no, PCA)
# --------------------------------------------------------------------------
def _reducir_2d(matriz):
    """De N x D a N x 2. Prefiere UMAP; cae a PCA si no está instalado."""
    try:
        import umap  # umap-learn
        red = umap.UMAP(n_neighbors=min(8, len(matriz) - 1), min_dist=0.3,
                        metric="cosine", random_state=42)
        return red.fit_transform(matriz), "UMAP"
    except Exception:
        from sklearn.decomposition import PCA
        return PCA(n_components=2, random_state=42).fit_transform(matriz), "PCA"


def dibujar_mapa(etiqueta, backend):
    """Embebe el VOCAB, lo proyecta a 2D y guarda un PNG coloreado por familia."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    terminos = [t for t, _ in VOCAB]
    familias = [f for _, f in VOCAB]
    mat = np.vstack([embed_candidate(t, backend) for t in terminos])
    xy, metodo = _reducir_2d(mat)

    fig, ax = plt.subplots(figsize=(11, 8))
    fams = list(dict.fromkeys(familias))
    cmap = plt.get_cmap("tab10")
    for i, fam in enumerate(fams):
        idx = [j for j, f in enumerate(familias) if f == fam]
        ax.scatter(xy[idx, 0], xy[idx, 1], s=90, color=cmap(i), label=fam, alpha=0.85,
                   edgecolors="black", linewidths=0.5)
    for j, t in enumerate(terminos):
        ax.annotate(t, (xy[j, 0], xy[j, 1]), fontsize=7,
                    xytext=(4, 4), textcoords="offset points")
    ax.set_title(f"Mapa semantico ({metodo}) — {etiqueta}")
    ax.legend(loc="best", fontsize=8, framealpha=0.9)
    ax.set_xticks([]); ax.set_yticks([])
    fig.tight_layout()
    salida = config.DATA_DIR / f"semantics_map_{backend}.png"
    fig.savefig(salida, dpi=130)
    plt.close(fig)
    return salida


def dibujar_barras(resumenes):
    """Barras agrupadas de triplet_acc por nivel y backend (para el TFM)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    niveles = [n for n in dict.fromkeys(r["nivel"] for r in resumenes) if n != "GLOBAL"]
    backends = list(dict.fromkeys(r["backend"] for r in resumenes))
    x = np.arange(len(niveles))
    ancho = 0.8 / max(1, len(backends))
    fig, ax = plt.subplots(figsize=(10, 6))
    cmap = plt.get_cmap("Set2")
    for i, b in enumerate(backends):
        vals = [next((r["triplet_acc"] for r in resumenes
                      if r["backend"] == b and r["nivel"] == n), 0) for n in niveles]
        ax.bar(x + i * ancho, vals, ancho, label=b, color=cmap(i))
    ax.axhline(0.5, ls="--", color="grey", lw=1, label="azar (0.5)")
    ax.set_xticks(x + ancho * (len(backends) - 1) / 2)
    ax.set_xticklabels(niveles)
    ax.set_ylabel("triplet accuracy"); ax.set_ylim(0, 1.05)
    ax.set_title("¿Entiende cada modelo el campo semantico? (triplet accuracy por nivel)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    salida = config.DATA_DIR / "semantics_accuracy.png"
    fig.savefig(salida, dpi=130)
    plt.close(fig)
    return salida


# --------------------------------------------------------------------------
def run():
    print("=" * 78)
    print(" CAPA 1 — Comprension semantica de los embeddings (triplet acc + AUC)")
    print("=" * 78)

    todos_resumen, todos_detalle, mapas = [], [], []
    for etiqueta, backend in BACKENDS:
        print(f"\n→ {etiqueta}  (backend={backend})")
        try:
            resumen, detalle = evaluar_backend(etiqueta, backend)
        except Exception as e:
            print(f"   [SALTADO] no se pudo usar este backend: {e}")
            continue
        todos_resumen.extend(resumen); todos_detalle.extend(detalle)
        for r in resumen:
            auc = "  n/d" if r["AUC"] is None else f"{r['AUC']:.3f}"
            print(f"   {r['nivel']:<20} n={r['n']:<2} triplet_acc={r['triplet_acc']:.3f}  AUC={auc}")
        try:
            mapas.append(dibujar_mapa(etiqueta, backend))
        except Exception as e:
            print(f"   [mapa saltado] {e}")

    if not todos_resumen:
        print("\nNingun backend disponible. ¿Falta torch/modelos o la API key?")
        return

    # Tabla comparativa final (solo la fila GLOBAL, para el titular).
    print("\n" + "=" * 78)
    print(f"{'BACKEND':<32}{'NIVEL':<20}{'triplet_acc':>12}{'AUC':>8}")
    print("-" * 78)
    for r in todos_resumen:
        auc = "   n/d" if r["AUC"] is None else f"{r['AUC']:.3f}"
        print(f"{r['backend']:<32}{r['nivel']:<20}{r['triplet_acc']:>12.3f}{auc:>8}")

    # Guardar CSVs
    p_sum = config.DATA_DIR / "semantics_summary.csv"
    with open(p_sum, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(todos_resumen[0].keys()))
        w.writeheader(); w.writerows(todos_resumen)
    p_det = config.DATA_DIR / "semantics_triplets.csv"
    with open(p_det, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(todos_detalle[0].keys()))
        w.writeheader(); w.writerows(todos_detalle)

    # El gráfico de barras es OPCIONAL (necesita matplotlib): si no está, no
    # reventamos tras haber hecho todo el trabajo — degradamos igual que los mapas.
    try:
        p_bar = dibujar_barras(todos_resumen)
    except Exception as e:
        p_bar = None
        print(f"   [barras saltadas] {e}")

    print("\nGuardado:")
    print(f"  {p_sum}\n  {p_det}")
    if p_bar:
        print(f"  {p_bar}")
    for m in mapas:
        print(f"  {m}")


if __name__ == "__main__":
    run()
