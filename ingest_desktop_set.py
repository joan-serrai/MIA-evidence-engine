"""
ingest_desktop_set.py — Amplía la base vectorial con un export de PubMed.

Toma un archivo en formato "Abstract (text)" de PubMed (el que se descarga con
Save → Format: Abstract) y lo indexa en la MISMA colección ChromaDB del proyecto
(`mia_evidence`), reutilizando la tubería de la Fase 1.

NO reentrena el modelo: solo amplía el corpus de RAG (la "biblioteca" que el LLM
consulta). OpenBioLLM no cambia; solo tendrá más evidencia que citar.

Uso:
    ./.venv/Scripts/python.exe ingest_desktop_set.py --file "<ruta al .txt>"

Diseño:
    parsear .txt → filtrar ruido → documento uniforme → [tubería Fase 1]
                                                         clean_and_chunk
                                                         embed_chunks
                                                         index_in_chroma
"""

import re
import sys
import argparse
import unicodedata
from pathlib import Path

import chromadb

# La tubería y la configuración son la ÚNICA fuente de verdad: no duplicamos
# lógica de troceado/embeddings/indexado ni listas de fármacos/rutas.
import config
from src import processing

# Windows: la consola usa cp1252 por defecto y se rompe con caracteres raros.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ==========================================================================
# 1) PARSER del formato "Abstract (text)" de PubMed
# ==========================================================================
# Cada registro de PubMed en este formato luce así:
#
#   12. J Eur Acad Dermatol Venereol. 2026 Jun. doi: 10.1111/...     ← cita (+año)
#
#   Título del artículo (puede ocupar varias líneas)
#
#   Apellido N(1), Otro M(2).                                        ← autores
#
#   Author information:                                              ← (opcional)
#   (1) ...
#
#   Texto del abstract (uno o varios párrafos).
#
#   DOI: 10.1111/...
#   PMID: 42363717                                                   ← cierra el registro
#
# Separamos por el ANCLA `PMID:` (cada registro termina justo ahí): es lo más
# robusto. El patrón inicial "N." solo lo usamos para localizar la línea de cita.

_PMID_RE = re.compile(r"^PMID:\s*(\d+)", re.MULTILINE)
_CITATION_RE = re.compile(r"^\d+\.\s")          # "12. " al inicio de la cita
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
# Línea de CITA completa: empieza por "N. ", y contiene un AÑO (19xx/20xx). Sirve
# para localizarla por PATRÓN (no por posición), porque algunos registros arrastran
# texto ajeno al principio (ver nota en parse_record).
_CITATION_LINE_RE = re.compile(r"^\d+\.\s+\S.*\b(?:19|20)\d{2}\b")
_AFFIL_MARKER_RE = re.compile(r"\(\d+\)")        # marcadores "(1)", "(2)" de autores
# Etiquetas que NO son ni título ni abstract (las saltamos al extraer el texto).
_SKIP_LABELS = ("author information:", "doi:", "pmid:", "pmcid:",
                "copyright", "©", "conflict of interest", "comment in",
                "comment on", "erratum", "update of", "update in",
                "plain language summary:", "[no authors listed]")


def _split_records(texto):
    """Divide el archivo completo en bloques, uno por registro (anclado en PMID:)."""
    registros, actual = [], []
    for linea in texto.splitlines():
        actual.append(linea)
        if linea.startswith("PMID:"):
            registros.append("\n".join(actual))
            actual = []
    return registros


def _paragraphs(bloque):
    """Trocea un bloque de registro en párrafos (separados por líneas en blanco),
    uniendo las líneas envueltas de cada párrafo en una sola."""
    parrs, buff = [], []
    for linea in bloque.splitlines():
        if linea.strip():
            buff.append(linea.strip())
        elif buff:
            parrs.append(" ".join(buff))
            buff = []
    if buff:
        parrs.append(" ".join(buff))
    return parrs


def _parse_authors(parrafo):
    """De 'Apellido N(1), Otro M(2).' → ['Apellido N', 'Otro M']."""
    if not parrafo or "[no authors listed]" in parrafo.lower():
        return []
    limpio = _AFFIL_MARKER_RE.sub("", parrafo).rstrip(".")
    return [a.strip() for a in limpio.split(",") if a.strip()]


def parse_record(bloque):
    """Convierte un bloque de texto en un dict con los campos crudos del registro.

    Devuelve None si no tiene PMID (registro malformado).
    """
    m = _PMID_RE.search(bloque)
    if not m:
        return None
    pmid = m.group(1)

    parrs = _paragraphs(bloque)
    if not parrs:
        return None

    # Localizamos la CITA por PATRÓN, no por posición. Motivo (bug de títulos):
    # en este export el bloque "Conflict of interest statement" va DESPUÉS del
    # "PMID:", así que _split_records (que corta en PMID:) lo arrastra al inicio
    # del registro SIGUIENTE. Si tomáramos parrs[0] como cita, la cita real caería
    # en 'title' y el título real se iría al abstract. Anclar en "N. …AÑO…" lo evita
    # e ignora cualquier párrafo ajeno que preceda a la cita.
    cita_idx = next((i for i, p in enumerate(parrs) if _CITATION_LINE_RE.match(p)), 0)
    cita = parrs[cita_idx]
    year_m = _YEAR_RE.search(cita)
    year = year_m.group(0) if year_m else ""

    cuerpo = parrs[cita_idx + 1:]  # todo lo que sigue a la cita
    titulo = cuerpo[0] if cuerpo else ""

    # Autores: el párrafo tras el título si parece una lista de autores.
    autores = []
    abstract_parrs = []
    if len(cuerpo) >= 2:
        posible_autores = cuerpo[1]
        es_autores = ("[No authors listed]" in posible_autores
                      or _AFFIL_MARKER_RE.search(posible_autores)
                      or (posible_autores.endswith(".") and "," in posible_autores
                          and len(posible_autores) < 400))
        resto = cuerpo[2:] if es_autores else cuerpo[1:]
        if es_autores:
            autores = _parse_authors(posible_autores)
        # Abstract = párrafos de prosa, descartando etiquetas conocidas.
        for p in resto:
            if p.lower().startswith(_SKIP_LABELS):
                continue
            abstract_parrs.append(p)
    abstract = " ".join(abstract_parrs)

    return {"pmid": pmid, "cita": cita, "year": year,
            "title": titulo, "authors": autores, "abstract": abstract}


# ==========================================================================
# 2) FILTRO de ruido (erratas, correcciones, comentarios, retractaciones…)
# ==========================================================================
# El formato "Abstract (text)" no trae las etiquetas [Publication Type] del
# MEDLINE, así que detectamos por patrones del título y la cita. Heurístico
# pero efectivo. OJO: "Comment IN" significa que a este artículo lo comentan
# (sigue siendo válido); "Comment ON" significa que este ES un comentario.

def classify_noise(rec):
    """Devuelve el tipo de ruido ('erratum'/'correction'/'comment'/'retraction'/
    'editorial') si el registro debe descartarse, o None si es válido."""
    titulo = rec["title"].lower().strip()
    bloque = (rec["cita"] + " " + rec["title"]).lower()

    if titulo.startswith("erratum") or "erratum for" in bloque or "erratum in" in titulo:
        return "erratum"
    if titulo.startswith(("correction to", "correction:", "author correction")):
        return "correction"
    if titulo.startswith(("comment on", "comments on", "re:", "reply", "response to")):
        return "comment"
    if titulo.startswith(("retraction", "retracted")) or "retraction of" in bloque:
        return "retraction"
    if titulo.startswith("editorial"):
        return "editorial"
    return None


# ==========================================================================
# 3) Detección de fármacos y conversión a DOCUMENTO UNIFORME
# ==========================================================================

def detect_drugs(rec):
    """Fármacos de config.ALL_DRUGS mencionados en el registro. Si ninguno,
    devuelve ['atopic_dermatitis']."""
    texto = f"{rec['title']} {rec['abstract']}".lower()
    encontrados = [d for d in config.ALL_DRUGS if d.lower() in texto]
    return encontrados or ["atopic_dermatitis"]


def to_uniform_document(rec):
    """Convierte el registro al MISMO esquema que usa processing.py, para poder
    pasarlo por clean_and_chunk sin tocar la tubería."""
    texto = unicodedata.normalize("NFKC", f"{rec['title']}. {rec['abstract']}")
    return {
        "source": "pubmed",
        "doc_id": rec["pmid"],
        "title": rec["title"],
        "text": texto,
        "authors": rec["authors"],
        "drugs": detect_drugs(rec),
        "disease": config.DISEASE,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{rec['pmid']}/",
    }


# ==========================================================================
# 4) ORQUESTACIÓN: leer → parsear → filtrar → indexar (reusando Fase 1)
# ==========================================================================

def _open_collection():
    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    return client.get_or_create_collection(
        name=config.CHROMA_COLLECTION, metadata={"hnsw:space": config.CHROMA_SPACE})


def run(file_path):
    ruta = Path(file_path)
    if not ruta.exists():
        sys.exit(f"[error] No existe el archivo: {ruta}")

    print("=" * 64)
    print(" Ampliación del corpus RAG · export de PubMed → ChromaDB")
    print("=" * 64)
    print(f"Archivo: {ruta}")

    # --- Leer y parsear ---
    texto = ruta.read_text(encoding="utf-8", errors="replace")
    bloques = _split_records(texto)
    registros = [r for r in (parse_record(b) for b in bloques) if r]
    print(f"\nRegistros leídos: {len(registros)}")

    # --- Filtrar ruido (contando por tipo) ---
    descartes = {"erratum": 0, "correction": 0, "comment": 0,
                 "retraction": 0, "editorial": 0}
    validos = []
    for rec in registros:
        tipo = classify_noise(rec)
        if tipo:
            descartes[tipo] += 1
        else:
            validos.append(rec)
    print("\nDescartados por ruido:")
    for tipo, n in descartes.items():
        print(f"  - {tipo:12s}: {n}")
    print(f"  TOTAL descartado: {sum(descartes.values())}")

    # --- Descartar registros SIN abstract (editoriales/cartas/noticias): como
    #     evidencia "solo título" aportan poco y suelen ser ruido. Los contamos. ---
    con_texto = [r for r in validos if r["abstract"].strip()]
    sin_abstract = len(validos) - len(con_texto)
    print(f"\nDescartados por no tener abstract (solo título): {sin_abstract}")

    # --- Documento uniforme + dedup defensivo por PMID (fusionando drugs) ---
    por_pmid = {}
    for rec in con_texto:
        doc = to_uniform_document(rec)
        if doc["doc_id"] in por_pmid:
            for d in doc["drugs"]:
                if d not in por_pmid[doc["doc_id"]]["drugs"]:
                    por_pmid[doc["doc_id"]]["drugs"].append(d)
        else:
            por_pmid[doc["doc_id"]] = doc
    docs = list(por_pmid.values())

    pmids = [r["pmid"] for r in con_texto]
    print(f"\nPMIDs con abstract: {len(pmids)} | únicos: {len(set(pmids))} "
          f"| duplicados en archivo: {len(pmids) - len(set(pmids))}")

    # --- Indexar REUTILIZANDO la tubería de la Fase 1 ---
    col = _open_collection()
    antes = col.count()
    print(f"\nChromaDB '{config.CHROMA_COLLECTION}' ANTES: {antes} chunks")

    chunks = processing.clean_and_chunk(docs, save_silver=False)  # no pisa el silver de Fase 1
    print(f"Chunks generados (tras trocear): {len(chunks)}")
    if not chunks:
        print("No hay nada que indexar."); return
    embeddings = processing.embed_chunks(chunks)
    processing.index_in_chroma(chunks, embeddings)               # ids '<pmid>::chunk<i>' + upsert

    despues = col.count()

    # --- Resumen final ---
    print("\n" + "=" * 64)
    print(" RESUMEN")
    print("=" * 64)
    print(f"  Registros leídos:        {len(registros)}")
    print(f"  Descartados (ruido):     {sum(descartes.values())}  {descartes}")
    print(f"  Descartados (sin abstract): {sin_abstract}")
    print(f"  Documentos indexados:    {len(docs)}")
    print(f"  Chunks generados:        {len(chunks)}")
    print(f"  ChromaDB antes → después: {antes} → {despues}  (+{despues - antes} chunks nuevos)")
    print("  (los PMID que ya existían de la Fase 1 se ACTUALIZAN vía upsert, no se duplican)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Indexa un export 'Abstract (text)' de PubMed en la colección ChromaDB del proyecto.")
    parser.add_argument("--file", required=True,
                        help="Ruta al archivo .txt exportado de PubMed.")
    args = parser.parse_args()
    run(args.file)
