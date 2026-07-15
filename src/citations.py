"""
src/citations.py — Reparte las citas [Doc N] POR FRASE (post-proceso determinista).

PROBLEMA: OpenBioLLM 8B, aunque se le pida citar tras cada frase, a menudo redacta
bien pero AMONTONA todas las citas al final ("… psoriasis. [Doc 1] [Doc 2] [Doc 3]").
Eso rompe la trazabilidad frase↔fuente que MIA promete.

SOLUCIÓN: tras generar, reasignamos cada cita a la FRASE que mejor soporta, por
solapamiento de TÉRMINOS DISTINTIVOS (nombre de fármaco, métrica EASI/IGA, palabras
clave como "psoriasis", "meta-analysis", "head and neck"…) entre la frase y el
documento. Es determinista (no interviene el LLM) y CONSERVADOR: solo coloca la cita
cuando el emparejamiento es claro; si una frase no casa con nada, se queda sin cita
(mejor sin cita que con una equivocada). Las citas que no casan con ninguna frase se
OMITEN del texto (amontonarlas al final recrearía justo el problema que evitamos);
esas fuentes siguen visibles en el panel "Fuentes citadas".

CLAVE: colocamos las citas por SOLAPAMIENTO con TODAS las fuentes recuperadas, cite
el modelo o no (OpenBioLLM 8B cita de forma poco fiable: a veces ninguna). Así la
trazabilidad frase↔fuente no depende de que el modelo se acuerde de citar; la garantiza
un emparejamiento determinista con la evidencia realmente recuperada. Si NINGÚN
emparejamiento es claro, deja la respuesta original intacta.
"""

import re

_DOC_TAG_RE = re.compile(r"\s*\[doc\s*(\d+)\]", re.IGNORECASE)
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Palabras vacías / demasiado genéricas para discriminar entre documentos.
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "with", "on", "at",
    "by", "is", "are", "was", "were", "be", "been", "being", "as", "that", "this",
    "these", "those", "it", "its", "from", "has", "have", "had", "not", "but",
    "which", "their", "there", "they", "can", "may", "also", "such", "some",
    "more", "most", "than", "into", "over", "based", "provided", "however",
    "furthermore", "additionally", "moreover", "including", "particularly",
    "study", "studies", "evidence", "data", "results", "patients", "patient",
    "treatment", "treated", "clinical", "effective", "effectiveness", "efficacy",
    "safety", "shown", "showed", "demonstrated", "supports", "supported",
    "conclusion", "findings", "compared", "improvement", "improvements",
    "moderate", "severe", "atopic", "dermatitis", "disease",
}
_MIN_OVERLAP = 2   # nº mínimo de términos distintivos compartidos para asignar


def _salient(text):
    """Conjunto de tokens significativos (>=3 letras, sin stopwords)."""
    toks = re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", (text or "").lower())
    return {t for t in toks if t not in _STOP}


def _source_terms(src):
    """Términos distintivos de una fuente (título + snippet + fármacos + métricas)."""
    partes = [src.get("title") or "", src.get("snippet") or "", src.get("drugs") or ""]
    for oc in (src.get("outcomes") or []):
        partes.append(str(oc.get("metric") or ""))
    return _salient(" ".join(partes))


def redistribute_citations(answer, sources):
    """Devuelve la respuesta con una cita [Doc N] al final de cada frase que una
    fuente recuperada respalda con claridad. Coloca las citas aunque el modelo no
    pusiera ninguna. Si no hay respuesta o fuentes, devuelve el texto igual.
    """
    if not answer or not sources:
        return answer

    # Términos distintivos por documento — de TODAS las fuentes recuperadas (no solo
    # las que el modelo citó), para poder añadir citas aunque el modelo no cite.
    terms = {s["n"]: _source_terms(s) for s in sources if s.get("n")}
    if not terms:
        return answer

    # Prosa limpia (quitamos las citas que hubiera puesto el modelo; las recolocamos
    # nosotros por solapamiento) partida en frases.
    limpio = _DOC_TAG_RE.sub("", answer).strip()
    frases = [f for f in _SENT_SPLIT_RE.split(limpio) if f.strip()]
    if not frases:
        return answer

    usados = set()
    salida = []
    for frase in frases:
        f_terms = _salient(frase)
        # Puntuamos cada doc citado por solapamiento de términos con la frase.
        puntuados = sorted(
            ((len(f_terms & terms[n]), n) for n in terms),
            key=lambda x: x[0], reverse=True,
        )
        mejor, mejor_n = puntuados[0]
        segundo = puntuados[1][0] if len(puntuados) > 1 else 0
        # Asignamos solo si el mejor es suficientemente claro (supera el umbral y
        # bate al segundo) → evita citas dudosas.
        if mejor >= _MIN_OVERLAP and mejor > segundo:
            salida.append(f"{frase.rstrip()} [Doc {mejor_n}]")
            usados.add(mejor_n)
        else:
            salida.append(frase.rstrip())

    if not usados:
        # Ninguna frase casó con claridad → no forzamos ni dejamos la respuesta sin
        # citas: devolvemos la original del modelo (con sus citas donde las puso).
        return answer

    # Éxito: ≥1 frase citada. NO reponemos las citas sobrantes al final (recrearían
    # el amontonamiento); esas fuentes siguen en el panel "Fuentes citadas".
    return " ".join(salida)


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    # Caso real DURO: el modelo NO citó NADA (frecuente a temp 0.0). Aun así debemos
    # colocar las citas por solapamiento con las fuentes recuperadas.
    demo_answer = (
        "Lebrikizumab is effective for moderate-to-severe atopic dermatitis. "
        "The efficacy was particularly notable in difficult-to-treat areas such as "
        "the head and neck. Systematic reviews and meta-analyses confirmed its "
        "efficacy versus placebo in randomized controlled trials. Some patients "
        "developed lebrikizumab-induced psoriasis during treatment."
    )
    demo_sources = [
        {"n": 1, "title": "Real-World Effectiveness of Lebrikizumab ... Head and Neck Involvement",
         "snippet": "real-world European cohort head and neck", "drugs": "lebrikizumab", "outcomes": []},
        {"n": 2, "title": "Efficacy and Safety of Lebrikizumab: systematic review and meta-analysis",
         "snippet": "systematic review meta-analysis randomized controlled trials placebo", "drugs": "lebrikizumab", "outcomes": []},
        {"n": 3, "title": "Lebrikizumab in the elderly: a case series",
         "snippet": "elderly case series", "drugs": "lebrikizumab", "outcomes": []},
        {"n": 4, "title": "Lebrikizumab-induced psoriasis in a patient",
         "snippet": "psoriasis induced", "drugs": "lebrikizumab", "outcomes": []},
        {"n": 5, "title": "Safety of Lebrikizumab in Adults and Adolescents",
         "snippet": "safety adverse events conjunctivitis", "drugs": "lebrikizumab", "outcomes": []},
    ]
    print(redistribute_citations(demo_answer, demo_sources))
