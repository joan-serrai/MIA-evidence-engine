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
    parser = argparse.ArgumentParser(description="Fase 1 de MIA: ingesta + procesado + indexado")
    parser.add_argument("--max", type=int, default=50,
                        help="máx. resultados por fármaco y fuente (def: 50)")
    parser.add_argument("--domain", default=None,
                        help="perfil de dominio (domains/<slug>.json); por defecto el activo")
    args = parser.parse_args()
    if args.domain:
        config.activate_domain(args.domain)
    print(f"Dominio activo: {config.DOMAIN_SLUG} — {config.DISEASE} "
          f"({len(config.DRUGS)} fármacos) → colección {config.CHROMA_COLLECTION}")

    print("\n########## FASE 1 · PASO 1/2: INGESTA ##########\n")
    ingestion.run(max_results=args.max)

    print("\n########## FASE 1 · PASO 2/2: PROCESADO + INDEXADO ##########\n")
    processing.run()

    print("\n✅ Fase 1 completada. La base vectorial está lista para la Fase 2 (RAG).")


if __name__ == "__main__":
    main()
