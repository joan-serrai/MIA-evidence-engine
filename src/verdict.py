"""
src/verdict.py — VEREDICTO POR PREGUNTA: ¿qué embedding entendió mejor ESTA
consulta?  [Módulos 5/6 embeddings · 8 recuperación · 9 agente · 12 evaluación]

Es la versión EN VIVO del benchmark de tripletes: en vez de un set etiquetado
offline, medimos, para la pregunta que el usuario acaba de escribir, si cada
modelo de embedding acerca lo relevante y lo pone arriba. Dos señales:

  A) RECUPERACIÓN (necesita saber el fármaco objetivo):
     - hit@1        : ¿el 1er documento es del fármaco correcto?
     - on-target    : ¿cuántos del top-k son del fármaco correcto?
     - AUC-pregunta : ¿con qué limpieza rankeó los correctos por encima del resto?
  B) GEOMETRÍA (triplete en vivo): ¿acerca el ancla al positivo más que al negativo?

¿De dónde sale el "fármaco objetivo"? De dos sitios:
  - preguntas de ejemplo → lo sabemos (etiquetado a mano).
  - preguntas libres     → lo cataloga el AGENTE (src/triplet_agent), que además
                           VERIFICA contra el corpus y SE ABSTIENE si no puede.
Si no hay objetivo verificado, este módulo NO declara ganador (abstención): se
muestran las dos columnas lado a lado, sin veredicto. Grounding > espectáculo.

Diseño: solo importa `config` a nivel de módulo (barato). Las piezas caras
(compare/embeddings/triplet_agent) se importan EN CALIENTE dentro de las
funciones, vía envoltorios de módulo (retrieve/catalog/embed_*) que además se
pueden inyectar en los tests sin cargar Ollama/Chroma.
"""

import sys
from pathlib import Path

try:
    from .. import config
except (ImportError, ValueError):
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    import config

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# Los dos motores que enfrenta la página (MIA vs Centivence). El de bge queda
# fuera: la página es a dos columnas. 'engine' es la etiqueta técnica legible.
BACKENDS = [
    {"label": "MIA", "engine": "MedCPT", "backend": "medcpt",
     "collection": config.collection_name("medcpt")},     # según el dominio activo
    {"label": "Centivence", "engine": "OpenAI 3-small", "backend": "openai",
     "collection": config.collection_name("openai")},
]


# --------------------------------------------------------------------------
# Envoltorios "en caliente" (inyectables en tests). No importan nada pesado
# hasta que se llaman de verdad.
# --------------------------------------------------------------------------
def _lazy(nombre):
    """Importa un módulo hermano de `src/` EN CALIENTE, funcione como funcione.

    Es la versión "dentro de una función" del patrón de importes duales que usa
    todo el proyecto (ver CLAUDE.md §6): `from . import X` solo vale si el módulo
    se cargó como parte del paquete `src`. Si `verdict.py` se ejecuta DIRECTO
    (`python src/verdict.py`), no hay paquete padre y el import relativo falla,
    así que caemos al import absoluto (la raíz ya está en `sys.path` por la
    cabecera del módulo).
    """
    try:
        from importlib import import_module
        return import_module(f".{nombre}", __package__ or "src")
    except (ImportError, ValueError, TypeError):
        from importlib import import_module
        return import_module(nombre)


def retrieve(question, backend, collection, target_drugs):
    compare = _lazy("compare")
    return compare.retrieve_ranked(question, backend, collection,
                                   target_drugs=target_drugs)


def catalog(question):
    return _lazy("triplet_agent").catalog_question(question)


def pick_negative(positive):
    """Negativo de clase opuesta (biológico↔JAK) para poder formar el triplete en
    vivo también en las preguntas de ejemplo, donde solo conocemos el positivo."""
    return _lazy("triplet_agent")._pick_negative(positive)


def embed_anchor(term, backend):
    return _lazy("embeddings").embed_query(term, backend=backend)


def embed_candidate(term, backend):
    embeddings = _lazy("embeddings")
    if backend == "medcpt":                      # asimétrico: candidatos = torre artículo
        return embeddings.embed_documents([term], backend=backend)[0]
    return embeddings.embed_query(term, backend=backend)


# --------------------------------------------------------------------------
# Métricas puras (sin dependencias pesadas → fáciles de testear)
# --------------------------------------------------------------------------
def _sim(a, b):
    return float(sum(x * y for x, y in zip(a, b)))


def auc_from_flags(sims, flags):
    """AUC = P(un doc on-target puntúe > uno off-target), por conteo de pares
    (Mann-Whitney). Empates 0.5. None si falta una de las dos clases."""
    rel = [s for s, f in zip(sims, flags) if f]
    aje = [s for s, f in zip(sims, flags) if not f]
    if not rel or not aje:
        return None
    mayor = iguales = 0
    for r in rel:
        for a in aje:
            if r > a:
                mayor += 1
            elif r == a:
                iguales += 1
    return (mayor + 0.5 * iguales) / (len(rel) * len(aje))


def live_triplet_ok(anchor, positive, negative, backend):
    """¿El backend acerca el ancla al positivo más que al negativo? True/False,
    o None si faltan términos para formar el triplete."""
    if not (anchor and positive and negative):
        return None
    va = embed_anchor(anchor, backend)
    vp = embed_candidate(positive, backend)
    vn = embed_candidate(negative, backend)
    return _sim(va, vp) > _sim(va, vn)


def _first_correct_rank(docs):
    """Puesto (1-indexado) del primer documento del fármaco correcto, o None."""
    for d in docs:
        if d.get("on_target") is True:
            return d.get("rank")
    return None


def _key(m):
    """Clave lexicográfica para ordenar backends: mejor = mayor. Prioridad:
    hit@1 > nº on-target > AUC-pregunta > triplete en vivo."""
    return (
        1 if m["hit1"] else 0,
        m["n_on_target"] or 0,
        m["auc"] if m["auc"] is not None else -1.0,
        1 if m["triplet_ok"] else (0 if m["triplet_ok"] is False else -1),
    )


# --------------------------------------------------------------------------
# Orquestación
# --------------------------------------------------------------------------
def _metrics_for(cfg, question, anchor, positive, negative, target_drugs):
    """Recupera con un backend y arma sus métricas por-pregunta."""
    res = retrieve(question, cfg["backend"], cfg["collection"], target_drugs)
    docs = res["docs"]
    sims = [d["similarity"] for d in docs]
    flags = [bool(d.get("on_target")) for d in docs]
    auc = auc_from_flags(sims, flags) if target_drugs else None
    triplet_ok = (live_triplet_ok(anchor, positive, negative, cfg["backend"])
                  if (positive and negative) else None)
    return {
        "label": cfg["label"], "engine": cfg["engine"], "backend": cfg["backend"],
        "hit1": res["hit1"], "n_on_target": res["n_on_target"], "total": res["total"],
        "auc": None if auc is None else round(auc, 3),
        "triplet_ok": triplet_ok,
        "first_correct_rank": _first_correct_rank(docs),
        "docs": docs,   # se devuelven para que la página no re-recupere
    }


def _verdict_text(winner, per_backend, positive):
    """Frase legible del veredicto (para pintar arriba de las columnas)."""
    if winner is None:
        return ("Empate: ambos modelos entendieron esta pregunta de forma "
                "equivalente (mismo hit@1 y cobertura del fármaco correcto).")
    gan = next(m for m in per_backend if m["label"] == winner)
    otros = [m for m in per_backend if m["label"] != winner]
    otro = otros[0] if otros else None
    farmaco = f" ({positive})" if positive else ""

    def rank_phrase(m):
        r = m["first_correct_rank"]
        return f"lo puso en el puesto {r}" if r else "no lo trajo en el top-k"

    rg = gan["first_correct_rank"]
    head = (f"puso el fármaco correcto{farmaco} en el puesto {rg}" if rg
            else f"rankeó mejor la evidencia correcta{farmaco}")
    tail = f"; {otro['engine']} {rank_phrase(otro)}" if otro else ""
    return f"{gan['engine']} entendió mejor esta pregunta: {head}{tail}."


def question_verdict(question, target_drugs=None, backends=None):
    """Da el veredicto por-pregunta comparando los backends de `BACKENDS`.

    `target_drugs`: si se conoce (preguntas de ejemplo), se pasa y NO se llama al
    agente. Si es None/vacío (pregunta libre), se cataloga con el agente, que
    puede ABSTENERSE (entonces abstained=True y winner=None).

    Devuelve un dict con: abstained, anchor/positive/negative, agent_source,
    agent_reason, per_backend (métricas + docs), winner (label o None) y
    verdict_text.
    """
    backends = backends or BACKENDS

    anchor = question
    positive = negative = None
    agent_source = "conocido"
    agent_reason = ""
    abstained = False

    if target_drugs:                     # objetivo conocido (ejemplo etiquetado)
        positive = target_drugs[0]
        negative = pick_negative(positive)   # generamos negativo para el triplete en vivo
    else:                                # pregunta libre → agente cataloga + verifica
        cat = catalog(question)
        agent_source = cat.get("source", "none")
        agent_reason = cat.get("reason", "")
        if cat.get("verified"):
            anchor = cat.get("anchor") or question
            positive = cat.get("positive")
            negative = cat.get("negative")
            target_drugs = cat.get("target_drugs") or ([positive] if positive else [])
        else:
            abstained = True             # sin objetivo verificado → sin veredicto

    per_backend = [
        _metrics_for(cfg, question, anchor, positive, negative,
                     target_drugs if not abstained else None)
        for cfg in backends
    ]

    winner = None
    if not abstained:
        keys = [(_key(m), m["label"]) for m in per_backend]
        keys.sort(reverse=True)
        # Empate si las dos mejores claves coinciden.
        if len(keys) >= 2 and keys[0][0] == keys[1][0]:
            winner = None
        else:
            winner = keys[0][1]

    return {
        "question": question, "abstained": abstained,
        "anchor": anchor, "positive": positive, "negative": negative,
        "agent_source": agent_source, "agent_reason": agent_reason,
        "per_backend": per_backend,
        "winner": winner,
        "verdict_text": (agent_reason if abstained
                         else _verdict_text(winner, per_backend, positive)),
    }


# --------------------------------------------------------------------------
if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Which antibody blocking IL-13 helps eczema?"
    v = question_verdict(q)
    print(f"Pregunta: {q}")
    print(f"¿Abstención?: {v['abstained']}  ganador: {v['winner']}")
    print(f"Triplete: ancla='{v['anchor']}' pos={v['positive']} neg={v['negative']}")
    for m in v["per_backend"]:
        print(f"  {m['engine']:<16} hit@1={m['hit1']} on-target={m['n_on_target']}/"
              f"{m['total']} AUC={m['auc']} triplete={m['triplet_ok']}")
    print(f"→ {v['verdict_text']}")
