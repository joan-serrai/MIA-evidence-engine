"""suggest_drugs.py — Which drugs are studied for a disease? (a helper before build_corpus)

Before creating a domain profile you have to decide WHICH drugs to download. If
you do not know the field, this script answers with data: it queries the official
ClinicalTrials.gov registry, counts in how many trials each drug intervention
appears for that disease and shows the most studied ones, with their highest
phase. It does not use the LLM and downloads nothing into the corpus: it only
looks and suggests.

Usage:
    ./.venv/Scripts/python.exe suggest_drugs.py --disease "Plaque psoriasis"
    ./.venv/Scripts/python.exe suggest_drugs.py --disease "Crohn disease" --top 15 --phase3

At the end it prints a ready-to-copy build_corpus.py command with the top N.
Names come exactly as the sponsor wrote them (sometimes a code such as "FP187"):
review the list with judgement before using it.
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
    p.add_argument("--disease", required=True, help="disease (in English), e.g. 'Plaque psoriasis'")
    p.add_argument("--top", type=int, default=10, help="how many drugs to list (default: 10)")
    p.add_argument("--phase3", action="store_true", help="count only phase 3/4 trials")
    p.add_argument("--max-studies", type=int, default=2000, help="max. number of trials to read (default: 2000)")
    args = p.parse_args()

    print(f"Querying ClinicalTrials.gov: condition = '{args.disease}' …")
    estudios = fetch_studies(args.disease, args.max_studies)
    print(f"Trials read: {len(estudios)}")
    conteo = count_drugs(estudios, args.phase3)
    if not conteo:
        sys.exit("No drug interventions found. Is the disease spelled correctly (in English)?")

    top = sorted(conteo.items(), key=lambda kv: (-kv[1]["n"], -kv[1]["phase3"], kv[0]))[:args.top]
    etiqueta = {0: "-", 0.5: "early 1", 1: "1", 2: "2", 3: "3", 4: "4"}
    print()
    print(f"{'#':>2}  {'drug / intervention':32} {'trials':>7} {'phase 3/4':>9}  max phase")
    print("-" * 66)
    for i, (nombre, c) in enumerate(top, 1):
        print(f"{i:>2}  {nombre:32} {c['n']:>7} {c['phase3']:>9}  {etiqueta.get(c['best'], '-')}")

    # Hasta dos palabras: cubre "certolizumab pegol" y deja fuera descripciones largas.
    farmacos = [n for n, _ in top if len(n.split()) <= 2][:args.top]
    print("\nSuggested command (review it: group by class and drop anything that is not a drug):")
    print(f'  ./.venv/Scripts/python.exe build_corpus.py --disease "{args.disease}" ' + chr(92))
    print(f'      --class "main={",".join(farmacos)}" --max 50 --activate')


if __name__ == "__main__":
    main()
