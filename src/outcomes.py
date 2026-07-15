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

Devuelve "puntos de dato": {metric, value, week, doc_n}. Un mismo endpoint medido
en varias semanas produce varios puntos (uno por semana), no un promedio inventado.
"""

import re

# Métricas de eficacia reconocidas en la literatura de dermatitis atópica.
# ORDEN IMPORTANTE: las variantes más específicas primero (EASI 100 antes que
# EASI 10/EASI 1...) para que el patrón general no se las coma.
_METRIC_PATTERNS = [
    (re.compile(r"\bEASI[-\s]?100\b", re.I), "EASI 100"),
    (re.compile(r"\bEASI[-\s]?90\b", re.I), "EASI 90"),
    (re.compile(r"\bEASI[-\s]?75\b", re.I), "EASI 75"),
    (re.compile(r"\bEASI[-\s]?50\b", re.I), "EASI 50"),
    (re.compile(r"\bvIGA[-\s]?AD\s*0\s*/\s*1\b", re.I), "vIGA-AD 0/1"),
    (re.compile(r"\bIGA\s*0\s*/\s*1\b", re.I), "IGA 0/1"),
    (re.compile(r"\bSCORAD[-\s]?75\b", re.I), "SCORAD 75"),
    (re.compile(r"\bSCORAD[-\s]?50\b", re.I), "SCORAD 50"),
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
    """Extrae puntos de dato {metric, value, week, doc_n} del texto de UN documento.

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
    for pat, label in _METRIC_PATTERNS:
        for m in pat.finditer(text):
            ventana = text[m.end(): m.end() + 110]

            # Cortamos la ventana en lo que llegue ANTES: (a) la siguiente métrica
            # o (b) el fin de la frase. Así un % de la frase siguiente (p. ej. la
            # tasa de efectos adversos) no se atribuye por error a esta métrica.
            corte = len(ventana)
            for pat2, _ in _METRIC_PATTERNS:
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
                })

    # Cap defensivo: si un abstract está lleno de cifras, nos quedamos con los
    # valores más altos (los "titulares") para no saturar el gráfico.
    puntos.sort(key=lambda p: p["value"], reverse=True)
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

    ejemplo = (
        "EASI 75 was achieved by 65.0%, 68.9%, and 82.6% of patients at weeks "
        "16, 24, and 52, respectively. EASI 90 increased from 43.9% at week 16 "
        "to 69.6% at week 52, while EASI 100 was observed in 56.5% at week 52. "
        "IGA 0/1 was achieved by 82.6% of patients at week 52. Overall, 5.7% of "
        "patients reported at least one AE."
    )
    for p in extract_outcomes(ejemplo, doc_n=1):
        semana = f" (sem {p['week']})" if p["week"] else ""
        print(f"  [Doc {p['doc_n']}] {p['metric']}{semana}: {p['value']}%")
