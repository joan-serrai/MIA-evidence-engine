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
import math

_DOC_TAG_RE = re.compile(r"\s*\[doc\s*(\d+)\]", re.IGNORECASE)
# Cita AGRUPADA que el modelo pone al final de una frase: "[Doc 1, Doc 2, Doc 3]",
# "[Doc 1-3]", "[Doc 2, 4]". El patrón de arriba NO la reconoce (exige que el ']'
# vaya justo tras el número), así que estas citas SOBREVIVÍAN al post-proceso:
# medido en una respuesta real, una frase acabó con "[Doc 1, Doc 2, Doc 3, Doc 4,
# Doc 5]" — exactamente el amontonamiento que este módulo existe para evitar, y
# además una afirmación atribuida a CINCO papers a la vez, que no es trazable.
# Se limpian antes de repartir, y el reparto pone la cita que de verdad toca.
_DOC_GROUP_RE = re.compile(r"\s*\[doc\s*\d+(?:\s*[,;&-]\s*(?:doc\s*)?\d+)+\s*\]",
                           re.IGNORECASE)
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
# ---------------------------------------------------------------------------
# PESADO POR RAREZA (IDF). Antes contábamos términos compartidos "a pelo" y el
# mejor tenía que superar ESTRICTAMENTE al segundo. Eso fallaba justo en el caso
# más común de MIA: si preguntas por upadacitinib, los 5 papers recuperados
# hablan de upadacitinib, así que ese término aparece en todos, no distingue
# nada, y los empates bloqueaban TODAS las citas. Resultado real medido: una
# respuesta sobre efectos adversos se quedó sin una sola cita.
#
# Arreglo: cada término vale según lo RARO que sea entre los documentos
# candidatos. "upadacitinib" en 5 de 5 papers ≈ no aporta señal; "nasopharyngitis"
# en 2 de 5 sí la aporta; un término único de un paper es señal fuerte.
#
#     idf(t) = log( (N + 1) / (df(t) + 0.5) )
#
# El +1/+0.5 son suavizados: evitan dividir por cero y que un término presente en
# TODOS valga exactamente 0 (con N=5: df=5 → 0.09, df=2 → 0.88, df=1 → 1.39).
_MIN_SCORE = 0.80   # puntuación mínima para asignar cita (≈ un término medianamente raro)
_MARGIN = 1.20      # el mejor debe superar al segundo por este factor (evita citas dudosas)


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
    # nosotros por solapamiento) partida en frases. Las AGRUPADAS primero: si no,
    # el patrón simple no las toca y se quedan en el texto final.
    limpio = _DOC_GROUP_RE.sub("", answer)
    limpio = _DOC_TAG_RE.sub("", limpio).strip()
    frases = [f for f in _SENT_SPLIT_RE.split(limpio) if f.strip()]
    if not frases:
        return answer

    # Peso IDF de cada término: cuántos documentos lo contienen (df) → lo raro que
    # es. Se calcula sobre ESTE conjunto de fuentes, así que se adapta solo: en una
    # tanda donde todos los papers hablan del mismo fármaco, ese fármaco pesa casi
    # nada y mandan los términos que de verdad diferencian (un evento adverso
    # concreto, una población, un endpoint).
    n_docs = len(terms)
    df = {}
    for t_set in terms.values():
        for t in t_set:
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log((n_docs + 1) / (d + 0.5)) for t, d in df.items()}

    usados = set()
    salida = []
    for frase in frases:
        f_terms = _salient(frase)
        # Puntuamos cada documento por la SUMA de pesos de los términos que
        # comparte con la frase (no por cuántos comparte).
        puntuados = sorted(
            ((sum(idf.get(t, 0.0) for t in (f_terms & terms[n])), n) for n in terms),
            key=lambda x: x[0], reverse=True,
        )
        mejor, mejor_n = puntuados[0]
        segundo = puntuados[1][0] if len(puntuados) > 1 else 0.0
        # Asignamos solo si el mejor pasa el umbral Y le saca margen al segundo.
        # Seguimos siendo conservadores: sin un ganador claro, la frase se queda
        # SIN cita (mejor sin cita que con una equivocada).
        if mejor >= _MIN_SCORE and mejor >= segundo * _MARGIN:
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


# --------------------------------------------------------------------------
# Validación determinista de citas  (garantía "cita siempre válida")
# --------------------------------------------------------------------------
# El reparto de arriba coloca citas [Doc N] correctas, pero hay un camino en el
# que la respuesta ORIGINAL del modelo se devuelve intacta (cuando ninguna frase
# casa con claridad): ahí el 8B puede haber inventado un [Doc 7] cuando solo hay
# 5 fuentes. Estas dos funciones son la última red de seguridad, aplicadas SIEMPRE
# sobre el texto final: garantizan que ninguna cita apunte a una fuente inexistente.

def cited_docs(text, n_sources):
    """Devuelve el conjunto de números [Doc N] citados en 'text' que son VÁLIDOS
    (1 <= N <= n_sources). Sirve para saber qué fuentes se citaron de verdad
    (p. ej. para avisar si alguna es de acceso restringido)."""
    if not text or n_sources <= 0:
        return set()
    return {n for n in (int(m) for m in _DOC_TAG_RE.findall(text))
            if 1 <= n <= n_sources}


def strip_invalid_citations(text, n_sources):
    """Elimina del texto cualquier cita [Doc N] fuera de rango (N<1 o N>n_sources).

    Es determinista y conservador: solo toca las citas inválidas (las que apuntan
    a una fuente que NO existe), dejando intactas las válidas y el resto del texto.
    Con esto, la promesa de MIA —'cada cita es rastreable a una fuente real'— se
    cumple SIEMPRE, pase lo que pase con la generación del modelo.
    """
    if not text:
        return text

    def _normalizar_grupo(m):
        """Convierte "[Doc 1, Doc 7, Doc 2]" en "[Doc 1] [Doc 2]" (descartando las
        fuera de rango). Hay un camino —cuando `redistribute_citations` no encuentra
        ningún emparejamiento claro y devuelve el texto tal cual del modelo— por el
        que una cita agrupada llega hasta aquí. La dejamos en citas individuales
        para que cada una apunte a UNA fuente real y comprobable."""
        nums = [int(x) for x in re.findall(r"\d+", m.group(0))]
        validos = sorted({n for n in nums if 1 <= n <= n_sources})
        return (" " + " ".join(f"[Doc {n}]" for n in validos)) if validos else ""

    def _sustituir(m):
        try:
            n = int(m.group(1))
        except (TypeError, ValueError):
            return ""  # cita ilegible → fuera
        return m.group(0) if 1 <= n <= n_sources else ""

    limpio = _DOC_GROUP_RE.sub(_normalizar_grupo, text)
    limpio = _DOC_TAG_RE.sub(_sustituir, limpio)
    # El borrado puede dejar dobles espacios o un espacio antes de un punto.
    limpio = re.sub(r"[ \t]{2,}", " ", limpio)
    limpio = re.sub(r"\s+([.,;:])", r"\1", limpio)
    return limpio.strip()


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
