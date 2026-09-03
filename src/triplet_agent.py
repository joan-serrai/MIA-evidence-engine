"""
src/triplet_agent.py — FASE de EVALUACIÓN: agente catalogador de TRIPLETES.
[Módulo 9: Agentes Autónomos]

¿Para qué? En la Comparativa MIA vs Centivence queremos decir, POR PREGUNTA,
qué modelo de embedding "entendió mejor" la consulta. Para las preguntas de
ejemplo ya sabemos el fármaco correcto (lo etiquetamos a mano). Pero para una
pregunta LIBRE no hay verdad conocida → no se puede calcular un triplete.

Este agente resuelve ese hueco: lee la pregunta y la DESCOMPONE en
  - anchor   : el concepto/mecanismo biomédico que se pregunta,
  - positive : el fármaco de dermatitis atópica que SÍ la responde,
  - negative : un fármaco de OTRA clase que NO la responde (distractor).
Con ese triplete, la Comparativa puede medir si cada embedding acerca el
positivo al ancla más que el negativo (igual que el benchmark offline, pero
en vivo sobre la pregunta del usuario).

GUARDARRAÍL ("libre pero verificado", elegido por su valor ante inversores y
profesionales del sector): el agente propone con libertad, pero antes de emitir
veredicto VERIFICAMOS que el fármaco 'positive' existe de verdad en el corpus.
Si no se puede verificar, el sistema SE ABSTIENE (no inventa un veredicto). Eso
es grounding + abstención bajo incertidumbre: la señal de madurez que el sector
regulado valora, y coherente con la validación determinista de citas de MIA.

Robustez (patrón de scout.py): nunca dependemos ciegamente del LLM. Si qwen
falla o degenera, caemos a un catalogado DETERMINISTA por mecanismo. Un agente
robusto siempre tiene plan B.

El LLM catalogador es qwen2.5:7b (config.LLM_JUDGE): local, buen seguidor de
instrucciones y de salida JSON. NO es un modelo de embedding ni un backend en
comparación → no hay conflicto de interés al usarlo como árbitro.
"""

import sys
import re
import json

try:
    from .. import config
except (ImportError, ValueError):
    sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    import config

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# Modelo que hace de agente. Se deja como constante del módulo (no en config)
# para poder cambiarlo sin tocar la config de producto; hoy = el juez neutral.
AGENT_MODEL = config.LLM_JUDGE   # "qwen2.5:7b"

# GENERALIZACIÓN (3-sep-2026): fármacos por clase, mecanismos y colección se leen
# de `config` EN CADA LLAMADA (no se copian al importar), para que el perfil de
# dominio activo pueda cambiar en caliente desde la interfaz. En el perfil de
# dermatitis atópica las clases son "biologics" (anti-IL) y "jak_inhibitors";
# en otro dominio serán las que defina su domains/<slug>.json.

# Palabra con sufijo típico de fármaco (-mab anticuerpo, -nib/-ib inhibidor).
_DRUG_RE = re.compile(r"\b[a-z]{4,}(?:mab|nib|ib)\b")


def _all_drugs():
    return [d.lower() for d in config.ALL_DRUGS]


# Mapa mecanismo→fármaco(s) para el FALLBACK determinista y para dar contexto.
# El orden importa: se prueban las claves más específicas primero (es el orden
# del perfil). ⚠ En el perfil de AD, para IL-13 el negativo NUNCA es dupilumab
# (también bloquea IL-13 vía IL-4Rα): la regla por clase lo evita sola.
def _mechanism_map():
    return [(k.lower(), [d.lower() for d in v]) for k, v in config.MECHANISMS.items()]


# --------------------------------------------------------------------------
# Utilidades de fármacos (clase, canonicalización, elección de negativo)
# --------------------------------------------------------------------------
def _drug_class(drug):
    """Clave de la clase terapéutica del fármaco según el perfil (o None)."""
    d = (drug or "").lower()
    for clase, farmacos in config.DRUG_CLASSES.items():
        if d in [x.lower() for x in farmacos]:
            return clase
    return None


def _canonical_drug(text):
    """Devuelve el nombre de fármaco CONOCIDO que aparece en `text` (o None).
    Primero busca un fármaco de config; si no, una palabra con sufijo -mab/-nib/-ib."""
    if not text:
        return None
    low = text.lower()
    for d in _all_drugs():               # fármaco conocido mencionado
        if d in low:
            return d
    m = _DRUG_RE.findall(low)            # patrón de fármaco (p. ej. 'ruxolitinib')
    return m[0] if m else None


def _pick_negative(positive):
    """Elige un distractor de una CLASE DISTINTA a la del positivo (en AD:
    biológico↔JAK). Así el negativo nunca es de la misma familia (y para IL-13
    nunca es dupilumab, porque el positivo IL-13 es biológico → el negativo sale
    de los JAK). Si el perfil solo tiene una clase, cae a otro fármaco cualquiera."""
    clase = _drug_class(positive)
    pos = (positive or "").lower()
    for otra, farmacos in config.DRUG_CLASSES.items():
        if otra == clase:
            continue
        for d in farmacos:
            if d.lower() != pos:
                return d.lower()
    for d in _all_drugs():               # perfil de una sola clase
        if d != pos:
            return d
    return None


# --------------------------------------------------------------------------
# Verificación contra el corpus (el guardarraíl). chromadb se importa en
# caliente para no acoplar el import del módulo (y poder testear sin Chroma).
# --------------------------------------------------------------------------
_CORPUS_DRUGS = {}   # cache por dominio: slug → conjunto de fármacos presentes en el corpus


def corpus_drugs():
    """Conjunto (en minúscula) de fármacos que aparecen en el corpus, leyendo la
    metadata 'drugs' de Chroma UNA vez por dominio (cacheado). Si Chroma no está
    disponible, cae a los fármacos conocidos de config (para no bloquear en desarrollo).

    Colección para verificar: los 'drugs' de metadata son IGUALES en las dos
    colecciones (solo cambia el vector), así que usamos la de MedCPT (producto)."""
    slug = config.DOMAIN_SLUG
    if slug in _CORPUS_DRUGS:
        return _CORPUS_DRUGS[slug]
    presentes = set()
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        col = client.get_collection(config.collection_name("medcpt"))
        got = col.get(include=["metadatas"])
        for meta in got.get("metadatas", []) or []:
            for d in (meta.get("drugs") or "").lower().replace(",", ";").split(";"):
                d = d.strip()
                if d:
                    presentes.add(d)
    except Exception as e:
        print(f"   [triplet_agent] aviso: no pude leer el corpus ({e}); "
              f"uso los fármacos de config como verificación de respaldo.")
        presentes = set(_all_drugs())
    _CORPUS_DRUGS[slug] = presentes
    return presentes


def drug_in_corpus(drug):
    """True si hay evidencia de ese fármaco en el corpus (subcadena tolerante)."""
    if not drug:
        return False
    d = drug.lower()
    return any(d in c or c in d for c in corpus_drugs())


# --------------------------------------------------------------------------
# Catalogado con el LLM (qwen). ollama en caliente (testeable sin ollama).
# --------------------------------------------------------------------------
def _llm_catalog(question):
    """Pide a qwen un triplete {anchor, positive, negative} en JSON. Devuelve
    el dict crudo o None si falla/degenera (lo valida quien llama)."""
    disease = config.DISEASE.lower()
    hint = "; ".join(
        ", ".join(farmacos) + f" ({config.CLASS_LABELS.get(clase, clase.replace('_', ' '))})"
        for clase, farmacos in config.DRUG_CLASSES.items() if farmacos)
    prompt = (
        f"You are a biomedical annotator for {disease} drug research.\n"
        "Given the user question, identify a semantic triplet:\n"
        '- "anchor": a short phrase (<=12 words) with the core mechanism/concept asked.\n'
        f'- "positive": the ONE {disease} drug (generic name) that best answers it.\n'
        f'- "negative": ONE {disease} drug from a DIFFERENT class that does NOT answer it.\n'
        f"Known {disease} drugs: {hint}.\n"
        'Reply with ONLY a JSON object like '
        '{"anchor":"...","positive":"...","negative":"..."} and nothing else.\n\n'
        f"Question: {question}"
    )
    try:
        import ollama
        r = ollama.chat(
            model=AGENT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_ctx": 4096},
        )
        raw = r["message"]["content"]
    except Exception as e:
        print(f"   [triplet_agent] aviso: catalogado LLM falló ({e}); uso fallback.")
        return None

    # Extraemos el primer objeto {...} aunque el modelo añada texto alrededor.
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    if not all(k in obj for k in ("anchor", "positive", "negative")):
        return None
    return obj


# --------------------------------------------------------------------------
# Catalogado DETERMINISTA (fallback sin LLM), por mecanismo o fármaco nombrado.
# --------------------------------------------------------------------------
def _fallback_catalog(question):
    """Construye un triplete sin LLM. Si la pregunta menciona un mecanismo
    conocido o un fármaco, lo usa como positivo. Devuelve dict o None."""
    low = question.lower()

    # 1) ¿nombra un fármaco directamente? → ese es el positivo.
    nombrado = _canonical_drug(low)
    if nombrado:
        neg = _pick_negative(nombrado)
        return {"anchor": question.strip(), "positive": nombrado,
                "negative": neg, "_via": "farmaco nombrado"}

    # 2) ¿menciona un mecanismo del mapa? → primer fármaco de ese mecanismo.
    for clave, farmacos in _mechanism_map():
        if clave in low:
            pos = farmacos[0]
            return {"anchor": question.strip(), "positive": pos,
                    "negative": _pick_negative(pos), "_via": f"mecanismo '{clave}'"}
    return None


# --------------------------------------------------------------------------
# API pública: cataloga y VERIFICA (o se abstiene)
# --------------------------------------------------------------------------
def catalog_question(question):
    """Descompone la pregunta en un triplete verificado.

    Devuelve un dict SIEMPRE (nunca lanza), con:
      verified : bool  → ¿podemos dar veredicto por-pregunta con este triplete?
      anchor, positive, negative : términos del triplete (positive/negative en
                                    minúscula canónica si se pudieron resolver)
      target_drugs : [positive] si verificado (para hit@1/on-target/AUC), o []
      source : 'llm' | 'fallback' | 'none'
      reason : explicación (sobre todo cuando NO se verifica → abstención)
    """
    base = {"question": question, "verified": False, "anchor": question.strip(),
            "positive": None, "negative": None, "target_drugs": [],
            "source": "none", "reason": ""}

    # 1) intento con el LLM; 2) fallback determinista.
    cand = _llm_catalog(question)
    source = "llm"
    if not cand:
        cand = _fallback_catalog(question)
        source = "fallback"
    if not cand:
        base["reason"] = ("No pude identificar un fármaco objetivo en la pregunta; "
                          "me abstengo de dar veredicto (se muestran las dos columnas).")
        return base

    # Canonicalizar positivo y negativo a fármacos conocidos.
    positive = _canonical_drug(cand.get("positive") or "") or (cand.get("positive") or "").lower().strip()
    negative = _canonical_drug(cand.get("negative") or "")
    anchor = (cand.get("anchor") or question).strip()

    # Guardarraíl del negativo: debe existir y ser de OTRA clase que el positivo
    # (evita el negativo tramposo, p. ej. dupilumab para una pregunta de IL-13).
    if (not negative) or (_drug_class(negative) == _drug_class(positive) and _drug_class(positive) is not None):
        negative = _pick_negative(positive) or negative

    # VERIFICACIÓN contra el corpus (el guardarraíl). Si el positivo no está en la
    # evidencia, nos ABSTENEMOS: mejor no dar veredicto que darlo sin respaldo.
    if not drug_in_corpus(positive):
        base.update({"anchor": anchor, "positive": positive, "negative": negative,
                     "source": source,
                     "reason": (f"El fármaco objetivo propuesto ('{positive}') no está "
                                f"respaldado por evidencia en el corpus → me abstengo "
                                f"de emitir veredicto para esta pregunta.")})
        return base

    return {"question": question, "verified": True, "anchor": anchor,
            "positive": positive, "negative": negative,
            "target_drugs": [positive], "source": source,
            "reason": f"Objetivo '{positive}' verificado en el corpus (vía {source})."}


# --------------------------------------------------------------------------
# Prueba rápida aislada
# --------------------------------------------------------------------------
if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Which antibody blocking IL-13 helps eczema?"
    r = catalog_question(q)
    print(f"Pregunta:  {q}")
    print(f"¿Verificado?: {r['verified']}  (fuente: {r['source']})")
    print(f"  ancla:    {r['anchor']}")
    print(f"  positivo: {r['positive']}")
    print(f"  negativo: {r['negative']}")
    print(f"  objetivo: {r['target_drugs']}")
    print(f"  razón:    {r['reason']}")
