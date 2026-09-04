"""
build_corpus.py — Crear un PERFIL DE DOMINIO nuevo y descargar/indexar su corpus.

Hasta ahora MIA solo sabía de dermatitis atópica: la enfermedad y sus fármacos
estaban escritos en config.py. Este script es la "puerta de entrada" para usar
MIA con CUALQUIER patología, mecanismo o fármaco: describes el dominio en la
línea de comandos, y el script

  1) escribe el perfil  →  domains/<slug>.json
     (enfermedad, sinónimos, fármacos por clase, mecanismos, endpoints, ejemplos)
  2) descarga la evidencia  →  data/bronze/<slug>/   (PubMed + ClinicalTrials.gov)
     por cada (enfermedad + fármaco) y, además, por cada búsqueda libre extra
     (--query "IL-17 inhibitor") para traer lo que no nombra ningún fármaco.
  3) limpia, trocea, vectoriza con MedCPT e indexa  →  colección mia_<slug>_medcpt
  4) exporta el censo reproducible  →  data/corpus_manifest_<slug>.csv
  5) (opcional, --openai) construye la colección OpenAI gemela para la comparativa.

Ejemplo — psoriasis en placas con biológicos anti-IL-17/IL-23 y una molécula oral:

  ./.venv/Scripts/python.exe build_corpus.py \
      --disease "Plaque psoriasis" --synonym psoriasis \
      --class "il17_biologics=secukinumab,ixekizumab,bimekizumab" \
      --class "il23_biologics=risankizumab,guselkumab" \
      --class "oral=apremilast,deucravacitinib" \
      --class-label "il17_biologics=anti-IL-17 antibodies" \
      --mechanism "il-17=secukinumab,ixekizumab,bimekizumab" \
      --mechanism "il-23=risankizumab,guselkumab" \
      --mechanism "tyk2=deucravacitinib" \
      --endpoint "PASI 100" --endpoint "PASI 90" --endpoint "PASI 75" --endpoint "sPGA 0/1" \
      --query "IL-17 inhibitor" --max 50 --activate

Después:   ./.venv/Scripts/python.exe src/rag.py "Is secukinumab effective in plaque psoriasis?"
           (o arranca la app y elige el perfil en la barra lateral).

El perfil original (dermatitis atópica) no se toca: conserva su corpus y sus
colecciones históricas, y se puede volver a él desde la app o con
`--activate` sobre `atopic_dermatitis`.
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
        raise argparse.ArgumentTypeError(f"esperaba 'clave{sep}valor', recibí {valor!r}")
    clave, resto = valor.split(sep, 1)
    items = [x.strip().lower() for x in resto.split(",") if x.strip()]
    return clave.strip().lower(), items


def _kv_text(valor):
    """'clave=texto libre' → ('clave', 'texto libre')."""
    if "=" not in valor:
        raise argparse.ArgumentTypeError(f"esperaba 'clave=texto', recibí {valor!r}")
    clave, texto = valor.split("=", 1)
    return clave.strip().lower(), texto.strip()


def _cap(s):
    return s[:1].upper() + s[1:]


def build_profile(args):
    """Construye el dict del perfil a partir de los argumentos (sin tocar disco)."""
    slug = args.slug or config.slugify(args.disease)
    if slug == config.DEFAULT_DOMAIN and not args.force_default:
        raise SystemExit(
            f"'{slug}' es el perfil original del capstone. Elige otro --slug, o pasa "
            "--force-default si de verdad quieres sobrescribirlo.")

    clases = {}
    for clave, farmacos in (args.drug_class or []):
        clases.setdefault(clave, [])
        clases[clave] += [f for f in farmacos if f not in clases[clave]]
    if args.drug:
        clases.setdefault("drugs", [])
        clases["drugs"] += [d.lower() for d in args.drug if d.lower() not in clases["drugs"]]
    if not any(clases.values()):
        raise SystemExit("Hace falta al menos un fármaco: --drug X  o  --class clase=a,b")

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
        "description": args.description or f"Perfil creado con build_corpus.py para {disease}.",
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
    parser.add_argument("--disease", required=True, help="nombre de la patología (en inglés)")
    parser.add_argument("--slug", help="identificador corto (def.: derivado de --disease)")
    parser.add_argument("--synonym", action="append", help="sinónimo para la búsqueda (repetible)")
    parser.add_argument("--description", help="texto libre que describe el perfil")
    parser.add_argument("--class", dest="drug_class", action="append", type=_kv_list,
                        metavar="CLASE=F1,F2", help="fármacos de una clase (repetible)")
    parser.add_argument("--class-label", action="append", type=_kv_text,
                        metavar="CLASE=etiqueta", help="nombre legible de la clase (repetible)")
    parser.add_argument("--drug", action="append", help="fármaco sin clase (repetible)")
    parser.add_argument("--extra-drug", action="append",
                        help="fármaco secundario: se etiqueta si aparece, no se descarga (repetible)")
    parser.add_argument("--mechanism", action="append", type=_kv_list,
                        metavar="MEC=F1,F2", help="mecanismo → fármacos (repetible)")
    parser.add_argument("--endpoint", action="append",
                        help="endpoint de eficacia, p. ej. 'PASI 75' o 'ACR20' (repetible; el orden importa: específicos primero)")
    parser.add_argument("--safety-term", action="append",
                        help="evento adverso propio del dominio a extraer, p. ej. 'candidiasis' (repetible)")
    parser.add_argument("--query", action="append",
                        help="búsqueda libre extra (p. ej. un mecanismo) además de fármaco+enfermedad (repetible)")
    parser.add_argument("--max", type=int, default=50, help="máx. resultados por fármaco y fuente (def: 50; 0 = SIN TOPE, todo lo que haya)")
    parser.add_argument("--activate", action="store_true", help="dejar este perfil como activo (domains/active.txt)")
    parser.add_argument("--profile-only", action="store_true", help="solo escribir el perfil, sin descargar ni indexar")
    parser.add_argument("--skip-download", action="store_true", help="no descargar: indexar lo que ya haya en bronze")
    parser.add_argument("--openai", action="store_true",
                        help="construir también la colección OpenAI (comparativa; necesita OPENAI_API_KEY)")
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
    print(f" Perfil {'actualizado' if existia else 'creado'}: {destino}")
    print(f"   {perfil['disease']}  ·  {sum(len(v) for v in perfil['drug_classes'].values())} fármacos"
          f"  ·  {len(perfil['efficacy_endpoints'])} endpoints  ·  {len(perfil['extra_queries'])} búsquedas extra")
    print("=" * 64)

    # A partir de aquí TODO el código (ingestion, processing, rag…) trabaja sobre
    # este dominio: config recalcula DISEASE, DRUGS, BRONZE_DIR, CHROMA_COLLECTION…
    config.activate_domain(slug, persist=args.activate)
    print(f"Dominio activo en este proceso: {config.DOMAIN_SLUG} → colección {config.CHROMA_COLLECTION}")
    if args.activate:
        print(f"Marcado como perfil por defecto en {config.ACTIVE_DOMAIN_FILE}")

    if args.profile_only:
        print("\n--profile-only: no se descarga ni se indexa. Para hacerlo más tarde:")
        print(f"  ./.venv/Scripts/python.exe run_phase1.py --domain {slug} --max {args.max or 0}")
        return

    from src import ingestion, processing   # import tardío: cargan torch/transformers

    if not args.skip_download:
        print("\n########## PASO 1/3: DESCARGA (bronze) ##########\n")
        ingestion.run(max_results=args.max)
    else:
        print("\n--skip-download: se usa lo que ya hay en", config.BRONZE_DIR)

    print("\n########## PASO 2/3: PROCESADO + INDEXADO (silver + chroma) ##########\n")
    processing.run()

    print("\n########## PASO 3/3: CENSO DEL CORPUS ##########\n")
    try:
        import export_corpus_manifest
        export_corpus_manifest.exportar(config.CHROMA_COLLECTION)
    except Exception as e:  # noqa: BLE001 — el censo es deseable, no imprescindible
        print(f"   [aviso] no se pudo exportar el censo: {e}")

    if args.openai:
        print("\n########## EXTRA: COLECCIÓN OPENAI (comparativa) ##########\n")
        import index_openai
        index_openai.run()

    print("\n✅ Dominio listo. Prueba:")
    q = perfil["example_questions"][0][2][0]
    print(f'   ./.venv/Scripts/python.exe src/rag.py "{q}"')
    if not args.activate:
        print(f"   (o con el perfil explícito:  set MIA_DOMAIN={slug}  /  --activate)")
    print("   o arranca la app y elige el perfil en la barra lateral.")


if __name__ == "__main__":
    main()
