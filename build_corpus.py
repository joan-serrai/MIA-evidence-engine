"""build_corpus.py — Create a new DOMAIN PROFILE and download/index its corpus.

This script is the entry point for using MIA with ANY disease, mechanism or
drug: you describe the domain on the command line, and the script

  1) writes the profile  →  domains/<slug>.json
     (disease, synonyms, drugs by class, mechanisms, endpoints, example questions)
  2) downloads the evidence  →  data/bronze/<slug>/   (PubMed + ClinicalTrials.gov)
     for every (disease + drug) pair and, in addition, for every free-text extra
     search (--query "IL-17 inhibitor") to bring in what names no drug at all.
  3) cleans, chunks, embeds with MedCPT and indexes  →  collection mia_<slug>_medcpt
  4) exports the reproducible census  →  data/corpus_manifest_<slug>.csv

Example — plaque psoriasis with anti-IL-17/IL-23 biologics and one oral molecule:

  ./.venv/Scripts/python.exe build_corpus.py       --disease "Plaque psoriasis" --synonym psoriasis       --class "il17_biologics=secukinumab,ixekizumab,bimekizumab"       --class "il23_biologics=risankizumab,guselkumab"       --class "oral=apremilast,deucravacitinib"       --class-label "il17_biologics=anti-IL-17 antibodies"       --mechanism "il-17=secukinumab,ixekizumab,bimekizumab"       --mechanism "il-23=risankizumab,guselkumab"       --mechanism "tyk2=deucravacitinib"       --endpoint "PASI 100" --endpoint "PASI 90" --endpoint "PASI 75" --endpoint "sPGA 0/1"       --query "IL-17 inhibitor" --max 50 --activate

Then:   ./.venv/Scripts/python.exe src/rag.py "Is secukinumab effective in plaque psoriasis?"
        (or start the app and pick the profile in the settings panel).

Existing profiles are not touched: each keeps its own corpus and collections, and
you can switch back to any of them from the app or with `--activate`.
"""

import argparse
import json
import sys

import config

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _kv_list(valor, sep="="):
    """'clave=a,b,c' → ('clave', ['a','b','c']). Tolera espacios y mayúsculas."""
    if sep not in valor:
        raise argparse.ArgumentTypeError(f"expected 'key{sep}value', got {valor!r}")
    clave, resto = valor.split(sep, 1)
    items = [x.strip().lower() for x in resto.split(",") if x.strip()]
    return clave.strip().lower(), items


def _kv_text(valor):
    """'clave=texto libre' → ('clave', 'texto libre')."""
    if "=" not in valor:
        raise argparse.ArgumentTypeError(f"expected 'key=text', got {valor!r}")
    clave, texto = valor.split("=", 1)
    return clave.strip().lower(), texto.strip()


def _cap(s):
    return s[:1].upper() + s[1:]


def build_profile(args):
    """Construye el dict del perfil a partir de los argumentos (sin tocar disco)."""
    slug = args.slug or config.slugify(args.disease)
    if slug == config.DEFAULT_DOMAIN and not args.force_default:
        raise SystemExit(
            f"'{slug}' is the original capstone profile. Choose another --slug, or pass "
            "--force-default if you really want to overwrite it.")

    clases = {}
    for clave, farmacos in (args.drug_class or []):
        clases.setdefault(clave, [])
        clases[clave] += [f for f in farmacos if f not in clases[clave]]
    if args.drug:
        clases.setdefault("drugs", [])
        clases["drugs"] += [d.lower() for d in args.drug if d.lower() not in clases["drugs"]]
    if not any(clases.values()):
        raise SystemExit("At least one drug is required: --drug X  or  --class class=a,b")

    todos = [d for ds in clases.values() for d in ds]
    disease = args.disease.strip()
    dl = disease.lower()

    mecanismos = {}
    for clave, farmacos in (args.mechanism or []):
        mecanismos[clave] = farmacos

    # Preguntas de ejemplo para la interfaz, generadas a partir de los fármacos.
    d1 = todos[0]
    d2 = todos[1] if len(todos) > 1 else todos[0]
    ejemplos = [
        ["Efficacy", ":material/trending_up:", [
            f"Is {d1} effective for {dl}?",
            f"What is the efficacy of {d2} in {dl}?"]],
        ["Safety", ":material/health_and_safety:", [
            f"What are the most common adverse events of {d1}?",
            f"Is {d2} safe for long-term use in {dl}?"]],
    ]
    if len(todos) > 1:
        ejemplos.append(["Comparison", ":material/compare_arrows:", [
            f"How does {d1} compare to {d2} in safety?",
            f"{_cap(d1)} vs {d2} efficacy in {dl}?"]])
    # Preguntas "difíciles" por mecanismo (sin nombrar el fármaco) para la página
    # de comparación MedCPT vs OpenAI: es donde el embedding biomédico debe destacar.
    mech_qs = [{"q": f"Therapy targeting {m.upper()} for {dl}", "drugs": fs}
               for m, fs in mecanismos.items() if fs][:4]

    return {
        "slug": slug,
        "disease": disease,
        "synonyms": [s.strip() for s in (args.synonym or []) if s.strip()],
        "description": args.description or f"Profile created with build_corpus.py for {disease}.",
        "drug_classes": clases,
        "class_labels": dict(args.class_label or []),
        "extra_drugs": [d.lower() for d in (args.extra_drug or [])],
        "mechanisms": mecanismos,
        "efficacy_endpoints": [e.strip() for e in (args.endpoint or []) if e.strip()],
        "safety_terms": [s.strip() for s in (args.safety_term or []) if s.strip()],
        "endpoint_examples": ", ".join((args.endpoint or [])[:3]) or "the named endpoint",
        "extra_queries": [q.strip() for q in (args.query or []) if q.strip()],
        "example_questions": ejemplos,
        "mechanism_questions": mech_qs,
        "legacy_collections": False,
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--disease", required=True, help="name of the disease (in English)")
    parser.add_argument("--slug", help="short identifier (default: derived from --disease)")
    parser.add_argument("--synonym", action="append", help="search synonym (repeatable)")
    parser.add_argument("--description", help="free text describing the profile")
    parser.add_argument("--class", dest="drug_class", action="append", type=_kv_list,
                        metavar="CLASS=D1,D2", help="drugs of one class (repeatable)")
    parser.add_argument("--class-label", action="append", type=_kv_text,
                        metavar="CLASS=label", help="human-readable name of the class (repeatable)")
    parser.add_argument("--drug", action="append", help="drug without a class (repeatable)")
    parser.add_argument("--extra-drug", action="append",
                        help="secondary drug: tagged if it appears, not downloaded (repeatable)")
    parser.add_argument("--mechanism", action="append", type=_kv_list,
                        metavar="MECH=D1,D2", help="mechanism → drugs (repeatable)")
    parser.add_argument("--endpoint", action="append",
                        help="efficacy endpoint, e.g. 'PASI 75' or 'ACR20' (repeatable; order matters: most specific first)")
    parser.add_argument("--safety-term", action="append",
                        help="domain-specific adverse event to extract, e.g. 'candidiasis' (repeatable)")
    parser.add_argument("--query", action="append",
                        help="extra free-text search (e.g. a mechanism) besides drug+disease (repeatable)")
    parser.add_argument("--max", type=int, default=50, help="max. results per drug and source (default: 50; 0 = NO LIMIT, everything available)")
    parser.add_argument("--activate", action="store_true", help="set this profile as the active one (domains/active.txt)")
    parser.add_argument("--profile-only", action="store_true", help="only write the profile, without downloading or indexing")
    parser.add_argument("--skip-download", action="store_true", help="do not download: index whatever is already in bronze")
    parser.add_argument("--force-default", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.max == 0:
        args.max = None   # sin tope

    perfil = build_profile(args)
    slug = perfil["slug"]
    config.DOMAINS_DIR.mkdir(parents=True, exist_ok=True)
    destino = config.domain_path(slug)
    existia = destino.exists()
    destino.write_text(json.dumps(perfil, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("=" * 64)
    print(f" Profile {'updated' if existia else 'created'}: {destino}")
    print(f"   {perfil['disease']}  ·  {sum(len(v) for v in perfil['drug_classes'].values())} drugs"
          f"  ·  {len(perfil['efficacy_endpoints'])} endpoints  ·  {len(perfil['extra_queries'])} extra queries")
    print("=" * 64)

    # A partir de aquí TODO el código (ingestion, processing, rag…) trabaja sobre
    # este dominio: config recalcula DISEASE, DRUGS, BRONZE_DIR, CHROMA_COLLECTION…
    config.activate_domain(slug, persist=args.activate)
    print(f"Active domain in this process: {config.DOMAIN_SLUG} → collection {config.CHROMA_COLLECTION}")
    if args.activate:
        print(f"Set as the default profile in {config.ACTIVE_DOMAIN_FILE}")

    if args.profile_only:
        print("\n--profile-only: nothing is downloaded or indexed. To do it later:")
        print(f"  ./.venv/Scripts/python.exe run_phase1.py --domain {slug} --max {args.max or 0}")
        return

    from src import ingestion, processing   # import tardío: cargan torch/transformers

    if not args.skip_download:
        print("\n########## STEP 1/3: DOWNLOAD (bronze) ##########\n")
        ingestion.run(max_results=args.max)
    else:
        print("\n--skip-download: using what is already in", config.BRONZE_DIR)

    print("\n########## STEP 2/3: PROCESSING + INDEXING (silver + chroma) ##########\n")
    processing.run()

    print("\n########## STEP 3/3: CORPUS MANIFEST ##########\n")
    try:
        import export_corpus_manifest
        export_corpus_manifest.exportar(config.CHROMA_COLLECTION)
    except Exception as e:  # noqa: BLE001 — el censo es deseable, no imprescindible
        print(f"   [warning] could not export the manifest: {e}")

    print("\n✅ Domain ready. Try:")
    q = perfil["example_questions"][0][2][0]
    print(f'   ./.venv/Scripts/python.exe src/rag.py "{q}"')
    if not args.activate:
        print(f"   (or with the explicit profile:  set MIA_DOMAIN={slug}  /  --activate)")
    print("   or start the app and pick the profile in the sidebar.")


if __name__ == "__main__":
    main()
