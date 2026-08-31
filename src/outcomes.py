"""
src/outcomes.py — Extracción de RESULTADOS NUMÉRICOS de la evidencia recuperada.

Objetivo (mejora "insight"): en vez de intentar sacar FIGURAS de los PDFs (inviable
con el stack local: solo-texto, sin PDFs, sin GPU), MIA genera sus PROPIOS gráficos
a partir de las cifras que YA vienen en el texto del abstract (EASI 75/90/100, IGA
0/1, etc.).

Principio ANTI-ALUCINACIÓN: los números NO se los pedimos al LLM. Se extraen del
texto recuperado con expresiones regulares deterministas y se etiquetan con el
[Doc N] del que salen → cada barra del gráfico es rastreable a su fuente exacta.
Si no encontramos cifras reconocibles, no mostramos nada (no inventamos ejes).

Devuelve "puntos de dato": {metric, value, week, doc_n, kind}. Un mismo endpoint medido
en varias semanas produce varios puntos (uno por semana), no un promedio inventado.
"""

import re

# Métricas de eficacia reconocidas en la literatura de dermatitis atópica.
# ORDEN IMPORTANTE: las variantes más específicas primero (EASI 100 antes que
# EASI 10/EASI 1...) para que el patrón general no se las coma.
#
# Cada entrada es (patrón, etiqueta, TIPO). El TIPO ("efficacy" | "safety") NO es
# decorativo: una tasa de respuesta EASI-75 (cuanto más alta, mejor) y una tasa de
# nasofaringitis (cuanto más alta, peor) NO pueden ir en la misma barra de un
# gráfico sin falsear la lectura. El gráfico de la UI muestra solo 'efficacy'; la
# tabla del informe las agrupa por separado.
_METRIC_PATTERNS = [
    # --- Eficacia: endpoints de respuesta de dermatitis atópica ---
    (re.compile(r"\bEASI[-\s]?100\b", re.I), "EASI 100", "efficacy"),
    (re.compile(r"\bEASI[-\s]?90\b", re.I), "EASI 90", "efficacy"),
    (re.compile(r"\bEASI[-\s]?75\b", re.I), "EASI 75", "efficacy"),
    (re.compile(r"\bEASI[-\s]?50\b", re.I), "EASI 50", "efficacy"),
    (re.compile(r"\bvIGA[-\s]?AD\s*0\s*/\s*1\b", re.I), "vIGA-AD 0/1", "efficacy"),
    (re.compile(r"\bIGA\s*0\s*/\s*1\b", re.I), "IGA 0/1", "efficacy"),
    (re.compile(r"\bSCORAD[-\s]?75\b", re.I), "SCORAD 75", "efficacy"),
    (re.compile(r"\bSCORAD[-\s]?50\b", re.I), "SCORAD 50", "efficacy"),

    # --- Seguridad: eventos adversos ---
    # Antes no había NINGUNO, así que cualquier pregunta de seguridad daba la
    # tabla "Key figures" vacía aunque el abstract trajera las cifras.
    # ORDEN: las variantes largas primero ("serious adverse events" antes que
    # "adverse events"), o el patrón corto se las come.
    (re.compile(r"\bserious\s+(?:treatment[-\s]?emergent\s+)?adverse\s+events?\b", re.I),
     "Serious adverse events", "safety"),
    (re.compile(r"\btreatment[-\s]?emergent\s+adverse\s+events?\b", re.I),
     "Treatment-emergent AEs", "safety"),
    (re.compile(r"\b(?:treatment\s+)?discontinuation(?:\s+rate)?\b", re.I),
     "Discontinuation", "safety"),
    # El genérico lleva lookbehind para NO volver a capturar lo que ya cazaron los
    # patrones específicos de arriba: sin esto, "serious adverse events ... 2.8%"
    # producía DOS puntos con el mismo 2.8% ("Serious adverse events" y "Adverse
    # events"), y la tabla mostraba la misma cifra dos veces con etiquetas distintas.
    (re.compile(r"(?<!serious )(?<!emergent )\badverse\s+events?\b", re.I),
     "Adverse events", "safety"),
    # Eventos adversos concretos frecuentes con biológicos y JAK en dermatitis
    # atópica. En los abstracts suelen venir como "nasopharyngitis (12.5%)", que
    # encaja con la ventana-después que ya usa el extractor.
    (re.compile(r"\bnasopharyngitis\b", re.I), "Nasopharyngitis", "safety"),
    (re.compile(r"\bupper\s+respiratory\s+tract\s+infections?\b", re.I),
     "Upper respiratory tract infection", "safety"),
    (re.compile(r"\bherpes\s+zoster\b", re.I), "Herpes zoster", "safety"),
    (re.compile(r"\bherpes\s+simplex\b", re.I), "Herpes simplex", "safety"),
    (re.compile(r"\bconjunctivitis\b", re.I), "Conjunctivitis", "safety"),
    (re.compile(r"\bacne\b", re.I), "Acne", "safety"),
    (re.compile(r"\bfolliculitis\b", re.I), "Folliculitis", "safety"),
    (re.compile(r"\bheadaches?\b", re.I), "Headache", "safety"),
    (re.compile(r"\bnausea\b", re.I), "Nausea", "safety"),
    (re.compile(r"\b(?:blood\s+)?creatine\s+(?:phospho)?kinase\b", re.I),
     "Creatine kinase increased", "safety"),
    (re.compile(r"\binjection[-\s]site\s+reactions?\b", re.I),
     "Injection-site reaction", "safety"),
]

# Un porcentaje: "82.6%", "65 %", "43.9%". Capturamos solo el número.
_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%")
# Un % que NO es una tasa de respuesta sino ESTADÍSTICA: intervalo de confianza
# ("95% CI", "95% credible interval") o heterogeneidad ("I2 = 77.5%"). Si no los
# filtráramos, un metaanálisis (que reporta risk ratios con IC, no % de respuesta)
# produciría cifras FALSAS en el gráfico (violando el principio anti-alucinación).
_CI_AFTER_RE = re.compile(r"\s?%?\s*(CI|CrI|confidence interval|credible interval)", re.I)
_HETERO_BEFORE_RE = re.compile(r"I2\s*[=:]\s*$", re.I)
# Semanas: "week 52", "weeks 16, 24, and 52". Capturamos el fragmento con los números.
_WEEK_RE = re.compile(r"weeks?\s*([\d,\s]+(?:and\s*\d+)?)", re.I)
# Fin de frase (punto seguido de espacio). Un "82.6%" NO lo dispara porque tras el
# punto va un dígito, no un espacio → así separamos frases sin romper decimales.
_SENT_END_RE = re.compile(r"\.\s")


def _parse_weeks(ventana):
    """Devuelve la lista de semanas (como strings) mencionadas en la ventana."""
    nums = []
    for grupo in _WEEK_RE.findall(ventana):
        nums.extend(re.findall(r"\d+", grupo))
    return nums


def extract_outcomes(text, doc_n, max_points=8):
    """Extrae puntos de dato {metric, value, week, doc_n, kind} del texto de UN documento.

    Para cada mención de una métrica conocida, mira la VENTANA de texto justo
    después (hasta la siguiente métrica o ~110 caracteres) y empareja los
    porcentajes con las semanas cuando el número de ambos coincide (emparejado
    posicional). Si no cuadran, deja la semana en None (mejor sin etiqueta que
    con una etiqueta equivocada).
    """
    if not text:
        return []

    puntos = []
    vistos = set()
    for pat, label, kind in _METRIC_PATTERNS:
        for m in pat.finditer(text):
            ventana = text[m.end(): m.end() + 110]

            # Cortamos la ventana en lo que llegue ANTES: (a) la siguiente métrica
            # o (b) el fin de la frase. Así un % de la frase siguiente (p. ej. la
            # tasa de efectos adversos) no se atribuye por error a esta métrica.
            corte = len(ventana)
            for pat2, _, _k in _METRIC_PATTERNS:
                mm = pat2.search(ventana)
                if mm and 0 < mm.start() < corte:
                    corte = mm.start()
            fin_frase = _SENT_END_RE.search(ventana)
            if fin_frase and fin_frase.start() < corte:
                corte = fin_frase.start()
            ventana = ventana[:corte]

            # Recogemos los % REALES (tasas), descartando los que son intervalos de
            # confianza ("95% CI") o heterogeneidad ("I2 = 77.5%").
            pcts = []
            for pm in _PCT_RE.finditer(ventana):
                despues = ventana[pm.end(): pm.end() + 24]
                antes = ventana[max(0, pm.start() - 8): pm.start()]
                if _CI_AFTER_RE.match(despues):
                    continue
                if _HETERO_BEFORE_RE.search(antes):
                    continue
                pcts.append(pm.group(1))
            if not pcts:
                continue
            semanas = _parse_weeks(ventana)
            emparejar = len(semanas) == len(pcts)

            for idx, val_str in enumerate(pcts):
                val = float(val_str)
                if not 0 < val <= 100:
                    continue  # descarta valores imposibles (ruido)
                semana = semanas[idx] if emparejar else None
                clave = (label, round(val, 1), semana)
                if clave in vistos:
                    continue
                vistos.add(clave)
                puntos.append({
                    "metric": label,
                    "value": round(val, 1),
                    "week": semana,
                    "doc_n": doc_n,
                    "kind": kind,   # "efficacy" | "safety" — no mezclar en un gráfico
                })

    # Cap defensivo: si un abstract está lleno de cifras, nos quedamos con los
    # "titulares". Ordenamos EFICACIA primero y, dentro de cada tipo, por valor
    # descendente. Sin el desempate por tipo, un abstract con muchas tasas de
    # eventos adversos altas podría expulsar del corte a los EASI, que suelen ser
    # el dato principal del paper.
    puntos.sort(key=lambda p: (p["kind"] != "efficacy", -p["value"]))
    return puntos[:max_points]


# --------------------------------------------------------------------------
# Prueba rápida aislada
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # El ejemplo mezcla a propósito EFICACIA y SEGURIDAD para comprobar que se
    # extraen las dos y que salen etiquetadas con su 'kind' (no deben acabar en
    # el mismo gráfico).
    ejemplo = (
        "EASI 75 was achieved by 65.0%, 68.9%, and 82.6% of patients at weeks "
        "16, 24, and 52, respectively. EASI 90 increased from 43.9% at week 16 "
        "to 69.6% at week 52, while EASI 100 was observed in 56.5% at week 52. "
        "IGA 0/1 was achieved by 82.6% of patients at week 52. "
        "Adverse events were reported in 62.3% of patients. "
        "The most frequent were nasopharyngitis (12.5%), acne (9.1%) and "
        "headache (6.4%). Serious adverse events occurred in 2.8%. "
        "Discontinuation due to toxicity was 4.2%. "
        "The pooled risk ratio was 1.42 (95% CI 1.10-1.83) with I2 = 77.5%."
    )
    for p in extract_outcomes(ejemplo, doc_n=1, max_points=20):
        semana = f" (sem {p['week']})" if p["week"] else ""
        print(f"  [Doc {p['doc_n']}] [{p['kind']:8}] {p['metric']}{semana}: {p['value']}%")
