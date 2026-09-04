"""
suggest_drugs.py — ¿Qué fármacos se estudian para una enfermedad? (ayuda previa a build_corpus)

Antes de crear un perfil de dominio hay que decidir QUÉ fármacos descargar. Si
no conoces el área, este script lo responde con datos: consulta el registro
oficial ClinicalTrials.gov, cuenta en cuántos ensayos aparece cada intervención
farmacológica para esa enfermedad y te enseña las más estudiadas, con su fase
más alta. No usa el LLM ni descarga nada al corpus: solo mira y propone.

Uso:
    ./.venv/Scripts/python.exe suggest_drugs.py --disease "Plaque psoriasis"
    ./.venv/Scripts/python.exe suggest_drugs.py --disease "Crohn disease" --top 15 --phase3

Al final imprime un comando build_corpus.py listo para copiar con los N primeros.
Los nombres salen tal cual los escribe el promotor (a veces un código como
"FP187"): revisa la lista con criterio antes de usarla.
"""

import argparse
import re
import sys
from collections import defaultdict

import requests

import config

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Intervenciones que NO son un fármaco a estudiar (comparadores, cuidados estándar).
_NOT_A_DRUG = re.compile(
    r"^(placebo|vehicle|sham|control|standard( of)? care|usual care|no treatment|"
    r"best supportive care|saline|normal saline|dummy|comparator|observation|"
    r"treatment as usual|tau|soc|matching placebo)\b", re.I)
# Palabras de forma/dosis/vía que estorban para agrupar "Secukinumab 300 mg SC" con "secukinumab".
_NOISE = re.compile(
    r"\b(\d+([.,]\d+)?\s*(mg|mcg|µg|ug|g|ml|iu|%|mg/kg|mg/ml)(/\w+)?|"
    r"injection|injections|tablet|tablets|capsule|capsules|oral|subcutaneous|sc|iv|"
    r"intravenous|topical|cream|ointment|gel|solution|foam|film|dose|doses|dosing|"
    r"low|high|once|twice|daily|weekly|q\d+w|qd|bid|arm|group|regimen|"
    r"treatment|therapy|prefilled|syringe|autoinjector|pen)\b", re.I)
_PHASE_RANK = {"EARLY_PHASE1": 0.5, "PHASE1": 1, "PHASE2": 2, "PHASE3": 3, "PHASE4": 4}


def normalize(name):
    """'Secukinumab 300 mg SC (Cosentyx)' → 'secukinumab'. Devuelve '' si no queda nada útil."""
    n = (name or "").lower()
    n = re.split(r"[(\[,;/+]|\bwith\b|\bplus\b|\bvs\b", n)[0]      # primer componente
    n = _NOISE.sub(" ", n)
    n = re.sub(r"[^a-z0-9\- ]", " ", n)
    n = re.sub(r"\s+", " ", n).strip(" -")
    if not n or _NOT_A_DRUG.match(n) or len(n) < 3:
        return ""
    return " ".join(n.split()[:3])


def fetch_studies(disease, max_studies=2000):
    """Descarga (nctId, fases, intervenciones) de los ensayos de la enfermedad, paginando."""
    estudios, token = [], None
    while len(estudios) < max_studies:
        params = {"query.cond": disease, "format": "json",
                  "pageSize": min(1000, max_studies - len(estudios)),
                  "fields": "NCTId,Phase,InterventionName,InterventionType"}
        if token:
            params["pageToken"] = token
        r = requests.get(config.CLINICALTRIALS_API, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        lote = data.get("studies", [])
        if not lote:
            break
        estudios.extend(lote)
        token = data.get("nextPageToken")
        if not token:
            break
    return estudios


def count_drugs(estudios, only_phase3=False):
    """{fármaco: {"n": ensayos, "phase3": ensayos en fase 3/4, "best": fase máxima}}."""
    conteo = defaultdict(lambda: {"n": 0, "phase3": 0, "best": 0})
    for s in estudios:
        proto = s.get("protocolSection", {})
        fases = proto.get("designModule", {}).get("phases", []) or []
        rango = max((_PHASE_RANK.get(f, 0) for f in fases), default=0)
        if only_phase3 and rango < 3:
            continue
        vistos = set()
        for iv in proto.get("armsInterventionsModule", {}).get("interventions", []) or []:
            if (iv.get("type") or "").upper() not in ("DRUG", "BIOLOGICAL"):
                continue
            nombre = normalize(iv.get("name"))
            if nombre and nombre not in vistos:
                vistos.add(nombre)
                c = conteo[nombre]
                c["n"] += 1
                c["phase3"] += 1 if rango >= 3 else 0
                c["best"] = max(c["best"], rango)
    return conteo


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--disease", required=True, help="enfermedad (en inglés), p. ej. 'Plaque psoriasis'")
    p.add_argument("--top", type=int, default=10, help="cuántos fármacos listar (def: 10)")
    p.add_argument("--phase3", action="store_true", help="contar solo ensayos de fase 3/4")
    p.add_argument("--max-studies", type=int, default=2000, help="tope de ensayos a leer (def: 2000)")
    args = p.parse_args()

    print(f"Consultando ClinicalTrials.gov: condición = '{args.disease}' …")
    estudios = fetch_studies(args.disease, args.max_studies)
    print(f"Ensayos leídos: {len(estudios)}")
    conteo = count_drugs(estudios, args.phase3)
    if not conteo:
        sys.exit("No se encontraron intervenciones farmacológicas. ¿Está bien escrita la enfermedad (en inglés)?")

    top = sorted(conteo.items(), key=lambda kv: (-kv[1]["n"], -kv[1]["phase3"], kv[0]))[:args.top]
    etiqueta = {0: "-", 0.5: "early 1", 1: "1", 2: "2", 3: "3", 4: "4"}
    print()
    print(f"{'#':>2}  {'fármaco / intervención':32} {'ensayos':>7} {'fase 3/4':>8}  fase máx.")
    print("-" * 66)
    for i, (nombre, c) in enumerate(top, 1):
        print(f"{i:>2}  {nombre:32} {c['n']:>7} {c['phase3']:>8}  {etiqueta.get(c['best'], '-')}")

    # Hasta dos palabras: cubre "certolizumab pegol" y deja fuera descripciones largas.
    farmacos = [n for n, _ in top if len(n.split()) <= 2][:args.top]
    print("\nComando sugerido (revísalo: agrupa por clase y quita lo que no sea un fármaco):")
    print(f'  ./.venv/Scripts/python.exe build_corpus.py --disease "{args.disease}" ' + chr(92))
    print(f'      --class "principales={",".join(farmacos)}" --max 50 --activate')


if __name__ == "__main__":
    main()
