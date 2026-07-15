"""
fix_source_titles.py — Reparación puntual de títulos de fuentes.  [Mantenimiento]

PROBLEMA (histórico): parte del corpus (el "set de escritorio" que se ingirió en
formato TEXTO de PubMed) guardó como `title` la LÍNEA DE CITA en vez del título
real. Ejemplo de título contaminado:

    638. Dermatitis. 2025 Jul 23:17103568251361920. doi: 10.1177/17103568251361920. Online ahead of print.

...cuando el título real es "Thirty-Six-Week Real-World Effectiveness of
Lebrikizumab ...". El script que ingirió ese set ya no existe, así que esto NO se
arregla en código: hay que reparar el DATO ya indexado en ChromaDB.

ESTRATEGIA (dos vías, la barata primero):
  1) OFFLINE: para la mayoría, el título real SÍ está dentro del texto guardado
     (justo tras la línea de cita, antes de la lista de autores). Lo recuperamos
     sin tocar la red → fiel al espíritu "100% local".
  2) RED (fallback): unos pocos registros no traían título en el texto (el export
     saltaba directo a los autores). Para esos, pedimos el título real a PubMed
     por PMID en LOTE (reutilizando los helpers de ingestion.py).

Luego actualizamos el metadato `title` de TODOS los chunks de cada documento en
ChromaDB (idempotente: re-ejecutar no rompe nada; ya no habrá títulos sucios).

Uso:
    ./.venv/Scripts/python.exe fix_source_titles.py --dry     # solo informa
    ./.venv/Scripts/python.exe fix_source_titles.py --apply   # escribe en Chroma
"""

import sys
import re
import time
import argparse
import xml.etree.ElementTree as ET

import chromadb

try:
    from src import ingestion
except (ImportError, ValueError):
    sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent))
    from src import ingestion
import config

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# --------------------------------------------------------------------------
# 1) Detección de títulos "sucios" (línea de cita en vez de título)
# --------------------------------------------------------------------------
# Marcas INEQUÍVOCAS de una línea de cita (no aparecen en títulos reales).
# OJO: NO usamos "año+mes" como señal — sale en títulos legítimos y marcaría
# como malos títulos buenos (p. ej. de ensayos o libros).
_DIRTY_START = re.compile(r"^\s*\d+\.\s")          # prefijo "638. " del export
_DIRTY_MARK = re.compile(
    r"(doi:\s)|(\bEpub\b)|(ahead of print)|(eCollection)", re.IGNORECASE)


def _looks_bad(title):
    """True si el 'título' no parece un título real: vacío, muy corto, con
    prefijo numérico de export ('638. ...') o con marcas de cita (doi:/Epub…)."""
    if not title or len(title.strip()) < 15:
        return True
    return bool(_DIRTY_START.search(title) or _DIRTY_MARK.search(title))


def _is_citation_format(title):
    """True solo para el patrón EXACTO 'N. Revista…doi/Epub…' (la línea de cita
    completa). Es el único caso donde la extracción offline desde el texto es
    fiable; fuera de ahí NO extraemos (evita convertir un título bueno en basura)."""
    return bool(title and _DIRTY_START.search(title) and _DIRTY_MARK.search(title))


def _strip_index_prefix(title):
    """Quita el prefijo numérico de export ('675. Título') si lo que queda ya es
    un título limpio. Devuelve el título limpio o None si no aplica."""
    m = re.match(r"^\s*\d+\.\s+(.*)$", title, re.DOTALL)
    if not m:
        return None
    resto = m.group(1).strip()
    if len(resto) >= 15 and not _looks_bad(resto):
        return resto.rstrip(".").strip()
    return None


# --------------------------------------------------------------------------
# 2) Recuperación OFFLINE del título real desde el texto del documento
# --------------------------------------------------------------------------
# En el texto guardado, la línea de cita acaba en ".. " (doble punto: processing
# hizo f"{titulo}. {abstract}" y el "titulo" —la cita— ya acababa en "."). Tras
# ese marcador viene el título real y luego los autores ("Apellido X(1), ...").
_CITATION_END = re.compile(r"\.\.\s+")
_AUTHOR_HINT = re.compile(r"[A-Z][a-z]+ [A-Z]{1,3}\(\d")   # "Hagino T(1)"
_ABSTRACT_HINT = re.compile(
    r"^(background|methods?|results?|conclusions?|objectives?|introduction|"
    r"abstract|importance|purpose|aims?)\b", re.IGNORECASE)


def extract_title_offline(full_text):
    """Devuelve el título real recuperado del texto, o None si no está."""
    m = _CITATION_END.search(full_text)
    resto = full_text[m.end():].strip() if m else ""
    if not resto:
        return None
    # El título real = las primeras frases hasta toparnos con la lista de autores
    # o con el inicio del abstract (Background:, Methods:, ...).
    frases = re.split(r"(?<=\.)\s+", resto)
    partes = []
    for f in frases:
        if _AUTHOR_HINT.search(f) or _ABSTRACT_HINT.search(f):
            break
        partes.append(f)
        if sum(len(x) for x in partes) > 60:   # los títulos rara vez son más largos
            break
    titulo = " ".join(partes).strip().rstrip(".").strip()
    if _looks_bad(titulo):
        return None
    return titulo


# --------------------------------------------------------------------------
# 3) Fallback por RED: pedir títulos a PubMed por PMID (en lote)
# --------------------------------------------------------------------------

def fetch_titles_from_pubmed(pmids, batch=200):
    """Devuelve {pmid: titulo} pidiendo a PubMed efetch en lotes."""
    api_key = ingestion._load_api_key()
    titulos = {}
    for i in range(0, len(pmids), batch):
        lote = pmids[i:i + batch]
        params = {"db": "pubmed", "id": ",".join(lote),
                  "rettype": "abstract", "retmode": "xml"}
        if api_key:
            params["api_key"] = api_key
        xml_text = ingestion._get_text(
            f"{config.PUBMED_EUTILS}/efetch.fcgi", params)
        time.sleep(0.34)  # rate limit NCBI
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            continue
        for art in root.findall(".//PubmedArticle"):
            pmid_el = art.find(".//MedlineCitation/PMID")
            t_el = art.find(".//Article/ArticleTitle")
            if pmid_el is not None and t_el is not None:
                titulo = "".join(t_el.itertext()).strip().rstrip(".").strip()
                if titulo:
                    titulos[pmid_el.text] = titulo
        print(f"   PubMed: {len(titulos)}/{len(pmids)} títulos recuperados...")
    return titulos


# --------------------------------------------------------------------------
# 4) Orquestación
# --------------------------------------------------------------------------

def main(apply=False):
    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    col = client.get_collection(config.CHROMA_COLLECTION)

    data = col.get(include=["documents", "metadatas"])
    # Agrupar por doc_id: ids de sus chunks, su metadata y su texto completo.
    docs = {}
    for _id, meta, doc in zip(data["ids"], data["metadatas"], data["documents"]):
        d = meta["doc_id"]
        g = docs.setdefault(d, {"ids": [], "metas": [], "chunks": [],
                                "title": meta.get("title") or "",
                                "source": meta.get("source")})
        g["ids"].append(_id)
        g["metas"].append(meta)
        g["chunks"].append((meta.get("chunk_index", 0), doc))

    sucios = {d: g for d, g in docs.items() if _looks_bad(g["title"])}
    print(f"Documentos totales: {len(docs)}  ·  con título sucio: {len(sucios)}")

    nuevos = {}       # doc_id -> título nuevo
    pendientes = []   # PMIDs sin título recuperable en local (para PubMed)
    for d, g in sucios.items():
        orig = g["title"]
        # 1) ¿Basta con quitar el prefijo numérico ('675. Título real')? (seguro)
        t = _strip_index_prefix(orig)
        # 2) Solo si es EXACTAMENTE una línea de cita, extraemos del texto guardado.
        if not t and _is_citation_format(orig):
            full = " ".join(x for _, x in sorted(g["chunks"], key=lambda c: c[0]))
            t = extract_title_offline(full)
        # 3) Si tenemos un título limpio, lo usamos; si no y es de PubMed, lo
        #    pedimos a la fuente (autoritativo). Cualquier otro caso se deja intacto.
        if t and not _looks_bad(t):
            nuevos[d] = t
        elif g["source"] == "pubmed" and d.isdigit():
            pendientes.append(d)

    print(f"Recuperados OFFLINE: {len(nuevos)}  ·  pendientes de PubMed: {len(pendientes)}")

    if pendientes:
        print("Pidiendo títulos a PubMed (fallback)...")
        via_red = fetch_titles_from_pubmed(pendientes)
        for d, t in via_red.items():
            nuevos[d] = t
        print(f"Recuperados por RED: {len(via_red)}")

    no_resueltos = [d for d in sucios if d not in nuevos]
    print(f"TOTAL a corregir: {len(nuevos)}  ·  sin resolver: {len(no_resueltos)}")

    # Muestra de control
    print("\n--- Muestra (antes -> después) ---")
    for d in list(nuevos)[:8]:
        print(f"[{d}] {sucios[d]['title'][:55]!r}\n   -> {nuevos[d]!r}")

    if not apply:
        print("\n(DRY-RUN: no se ha escrito nada. Ejecuta con --apply para aplicar.)")
        return

    # --- Aplicar: actualizar el metadato title de TODOS los chunks de cada doc ---
    print("\nAplicando cambios en ChromaDB...")
    upd_ids, upd_metas = [], []
    for d, titulo in nuevos.items():
        g = sucios[d]
        for _id, meta in zip(g["ids"], g["metas"]):
            m = dict(meta)
            m["title"] = titulo[:300]
            upd_ids.append(_id)
            upd_metas.append(m)

    # Troceamos el update en lotes seguros.
    lote = 1000
    for i in range(0, len(upd_ids), lote):
        col.update(ids=upd_ids[i:i + lote], metadatas=upd_metas[i:i + lote])
    print(f"Actualizados {len(upd_ids)} chunks de {len(nuevos)} documentos. Hecho.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="escribe los cambios en Chroma")
    ap.add_argument("--dry", action="store_true", help="solo informa (por defecto)")
    args = ap.parse_args()
    main(apply=args.apply)
