"""
run_phase1.py — Orquestador de la FASE 1 (Datos), de principio a fin.

Ejecuta:  python run_phase1.py
Opcional:  python run_phase1.py --max 30   (resultados por fármaco/fuente)

Hace, en orden:
  1) Ingesta   → descarga ensayos (ClinicalTrials) y abstracts (PubMed) a data/bronze/
  2) Procesado → limpia, trocea y vectoriza
  3) Indexado  → guarda los vectores en ChromaDB (data/chroma/)

Al terminar, MIA ya tiene una base de conocimiento local lista para la Fase 2 (RAG).
"""

import sys
import argparse

import config
from src import ingestion, processing

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    parser = argparse.ArgumentParser(description="MIA Phase 1: ingestion + processing + indexing")
    parser.add_argument("--max", type=int, default=50,
                        help="max. results per drug and source (default: 50; 0 = NO LIMIT, everything available)")
    parser.add_argument("--domain", default=None,
                        help="domain profile (domains/<slug>.json); defaults to the active one")
    args = parser.parse_args()
    if args.max == 0:
        args.max = None   # sin tope
    if args.domain:
        config.activate_domain(args.domain)
    print(f"Active domain: {config.DOMAIN_SLUG} — {config.DISEASE} "
          f"({len(config.DRUGS)} drugs) → collection {config.CHROMA_COLLECTION}")

    print("\n########## PHASE 1 · STEP 1/2: INGESTION ##########\n")
    ingestion.run(max_results=args.max)

    print("\n########## PHASE 1 · STEP 2/2: PROCESSING + INDEXING ##########\n")
    processing.run()

    print("\n✅ Phase 1 complete. The vector database is ready for Phase 2 (RAG).")


if __name__ == "__main__":
    main()
