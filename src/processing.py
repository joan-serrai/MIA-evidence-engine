"""
src/processing.py — FASE 1: Limpiar, trocear, vectorizar e indexar.  [Módulos 2-3 + 8]

Convierte los datos CRUDOS de data/bronze/ en fragmentos ("chunks") con sus
metadatos (título, autores, PMID/NCT) y los deja INDEXADOS en ChromaDB para
que la Fase 2 (rag.py) pueda buscar sobre ellos.

Tubería (pipeline):
    bronze  →  clean_and_chunk()  →  silver/chunks.json
                                  →  embed_chunks()      →  vectores
                                  →  index_in_chroma()   →  data/chroma/

Clave del proyecto: cada chunk DEBE conservar su "herencia" (de qué documento
viene, título, autores, id), porque sin eso MIA no podría citar la fuente exacta.
"""

import sys
import re
import json
import unicodedata
import xml.etree.ElementTree as ET

import chromadb

try:
    from .. import config
    from . import embeddings
except (ImportError, ValueError):
    sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    import config
    from src import embeddings

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# --------------------------------------------------------------------------
# 1) Lectura de bronze → "documentos uniformes"
# --------------------------------------------------------------------------
# ClinicalTrials y PubMed tienen formatos muy distintos. Para no arrastrar esa
# diferencia por todo el código, los convertimos a un MISMO diccionario:
#   {source, doc_id, title, text, authors[list], drugs[list], disease, url}

def _normalize_text(texto):
    """Normaliza unicode y colapsa espacios/saltos de línea repetidos."""
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKC", texto)
    texto = re.sub(r"\s+", " ", texto)  # múltiples espacios/tabs/saltos → 1 espacio
    return texto.strip()


def _parse_clinical_trials(path, drug):
    """Convierte un ct_<drug>.json en documentos uniformes."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    # La etiqueta de fármaco real vive en el propio JSON (útil para los archivos
    # del Scout, cuyo nombre no es un fármaco de config); si no, usamos la pasada.
    drug = payload.get("drug") or drug
    docs = []
    for estudio in payload.get("studies", []):
        proto = estudio.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        desc = proto.get("descriptionModule", {})
        conds = proto.get("conditionsModule", {}).get("conditions", [])

        nct = ident.get("nctId")
        if not nct:
            continue
        titulo = ident.get("officialTitle") or ident.get("briefTitle") or ""
        resumen = desc.get("briefSummary") or ""
        # El "texto" útil de un ensayo = título + resumen + condiciones.
        texto = _normalize_text(f"{titulo}. {resumen}. Conditions: {', '.join(conds)}")

        docs.append({
            "source": "clinicaltrials",
            "doc_id": nct,
            "title": _normalize_text(titulo),
            "text": texto,
            "authors": [],  # ClinicalTrials no da autores como tal
            "drugs": [drug],
            "disease": config.DISEASE,
            "url": f"https://clinicaltrials.gov/study/{nct}",
        })
    return docs


def _parse_pubmed(path, drug):
    """Convierte un pubmed_<drug>.xml en documentos uniformes."""
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except ET.ParseError:
        return []

    docs = []
    for article in root.findall(".//PubmedArticle"):
        pmid_el = article.find(".//MedlineCitation/PMID")
        pmid = pmid_el.text if pmid_el is not None else None
        if not pmid:
            continue

        titulo_el = article.find(".//Article/ArticleTitle")
        titulo = "".join(titulo_el.itertext()) if titulo_el is not None else ""

        # ¡OJO! Un abstract puede tener VARIOS <AbstractText> con un Label
        # (BACKGROUND, METHODS, RESULTS...). Hay que concatenarlos TODOS.
        partes = []
        for at in article.findall(".//Article/Abstract/AbstractText"):
            label = at.get("Label")
            contenido = "".join(at.itertext())
            partes.append(f"{label}: {contenido}" if label else contenido)
        abstract = " ".join(partes)

        # Autores: "Apellido Nombre" o nombre colectivo de un grupo.
        autores = []
        for autor in article.findall(".//Article/AuthorList/Author"):
            colectivo = autor.find("CollectiveName")
            if colectivo is not None and colectivo.text:
                autores.append(colectivo.text.strip())
                continue
            apellido = autor.find("LastName")
            nombre = autor.find("ForeName")
            if apellido is not None and apellido.text:
                nombre_completo = apellido.text
                if nombre is not None and nombre.text:
                    nombre_completo += f" {nombre.text}"
                autores.append(nombre_completo.strip())

        texto = _normalize_text(f"{titulo}. {abstract}")
        # Si no hay abstract, el título solo suele ser demasiado pobre; lo
        # conservamos igualmente (mejor poco que nada para la demo).
        docs.append({
            "source": "pubmed",
            "doc_id": pmid,
            "title": _normalize_text(titulo),
            "text": texto,
            "authors": autores,
            "drugs": [drug],
            "disease": config.DISEASE,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        })
    return docs


def _load_uniform_documents():
    """Lee TODO data/bronze/ y devuelve documentos uniformes deduplicados.

    Dedup por doc_id: un mismo ensayo/artículo puede aparecer al buscar dos
    fármacos distintos. En vez de duplicarlo (o sobrescribirlo), fusionamos la
    lista de 'drugs' para no perder esa información.
    """
    por_id = {}

    for path in sorted(config.BRONZE_DIR.glob("ct_*.json")):
        drug = path.stem.replace("ct_", "")
        for doc in _parse_clinical_trials(path, drug):
            _merge_doc(por_id, doc)

    for path in sorted(config.BRONZE_DIR.glob("pubmed_*.xml")):
        drug = path.stem.replace("pubmed_", "")
        for doc in _parse_pubmed(path, drug):
            _merge_doc(por_id, doc)

    return list(por_id.values())


def _merge_doc(por_id, doc):
    """Inserta o fusiona un documento en el diccionario por doc_id."""
    existente = por_id.get(doc["doc_id"])
    if existente is None:
        por_id[doc["doc_id"]] = doc
    else:
        # Fusionar fármacos sin repetir, conservando el orden.
        for d in doc["drugs"]:
            if d not in existente["drugs"]:
                existente["drugs"].append(d)


# --------------------------------------------------------------------------
# 2) Troceado (chunking)
# --------------------------------------------------------------------------

# Corta el texto en frases por el final de oración (. ! ?) seguido de espacio.
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _chunk_text(texto, size, overlap):
    """Trocea respetando FRASES: empaqueta oraciones enteras hasta ~'size'
    caracteres, con un solape de una frase entre chunks.

    Antes troceábamos por caracteres y se cortaba a media palabra/frase
    (p. ej. "...r for lebrikizumab..."), lo que confundía al LLM. Ahora cada
    chunk empieza y acaba en frases completas → más legible y citable.
    """
    texto = texto.strip()
    if len(texto) <= size:
        return [texto] if texto else []

    frases = [f for f in _SENT_SPLIT.split(texto) if f]
    chunks, actual, longitud = [], [], 0
    for f in frases:
        # Si añadir esta frase desborda el tamaño y ya hay contenido, cerramos chunk.
        if actual and longitud + len(f) + 1 > size:
            chunks.append(" ".join(actual))
            # Solape por frase: arrancamos el siguiente con la última frase del
            # anterior (si overlap>0 y no es desproporcionadamente larga).
            if overlap > 0 and len(actual[-1]) <= overlap * 2:
                actual, longitud = [actual[-1]], len(actual[-1]) + 1
            else:
                actual, longitud = [], 0
        actual.append(f)
        longitud += len(f) + 1
    if actual:
        chunks.append(" ".join(actual))

    # Último recurso: una frase patológicamente larga (> 1.5×size) se trocea por
    # caracteres para no perderla ni crear un chunk gigante.
    salida = []
    for c in chunks:
        if len(c) <= int(size * 1.5):
            salida.append(c)
        else:
            paso = max(1, size - overlap)
            salida.extend(c[i:i + size] for i in range(0, len(c), paso))
    return salida


def clean_and_chunk(raw_documents=None, save_silver=True):
    """Limpia y trocea los documentos crudos en fragmentos con metadatos.

    Si no se pasan documentos, los lee de data/bronze/ (lo habitual).
    'save_silver' guarda el resultado en silver/chunks.json (lo desactiva el
    Scout, que solo procesa un puñado de documentos nuevos y no debe pisar el
    snapshot completo del corpus).
    Cada chunk lleva metadatos ESCALARES (str/int) porque ChromaDB no admite
    listas ni None: por eso 'authors' y 'drugs' se serializan como string.

    Devuelve una lista de dicts: {"id", "text", "metadata"}.
    """
    if raw_documents is None:
        raw_documents = _load_uniform_documents()

    chunks = []
    for doc in raw_documents:
        texto = doc.get("text", "")
        if not texto or len(texto) < 30:
            continue  # documento sin contenido útil → lo saltamos

        for i, trozo in enumerate(_chunk_text(texto, config.CHUNK_SIZE, config.CHUNK_OVERLAP)):
            chunks.append({
                "id": f"{doc['doc_id']}::chunk{i}",
                "text": trozo,
                "metadata": {
                    "source": doc["source"],
                    "doc_id": doc["doc_id"],
                    "title": doc["title"][:300],  # recorte defensivo
                    "authors": "; ".join(doc["authors"]),  # lista → string
                    "drugs": "; ".join(doc["drugs"]),      # lista → string
                    "disease": doc["disease"],
                    "url": doc["url"],
                    "chunk_index": i,
                },
            })

    # Persistimos en "silver" para poder inspeccionar el resultado a ojo.
    if save_silver:
        config.SILVER_DIR.mkdir(parents=True, exist_ok=True)
        salida = config.SILVER_DIR / "chunks.json"
        salida.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    return chunks


# --------------------------------------------------------------------------
# 3) Embeddings (texto → vectores)
# --------------------------------------------------------------------------

def embed_chunks(chunks):
    """Convierte el texto de cada chunk en un vector.

    Delega en src/embeddings.py, que embebe con el backend activo (bge o MedCPT)
    y devuelve vectores normalizados (longitud 1), como espera la distancia coseno
    que configuramos en ChromaDB. Cambiar de modelo = cambiar config.EMBEDDING_BACKEND.
    """
    if not chunks:
        return []
    textos = [c["text"] for c in chunks]
    return embeddings.embed_documents(textos)


# --------------------------------------------------------------------------
# 4) Indexado en ChromaDB
# --------------------------------------------------------------------------

def index_in_chroma(chunks, embeddings):
    """Guarda los chunks + sus vectores en la base vectorial ChromaDB.

    Usamos 'upsert' (no 'add') con ids deterministas: si re-ejecutamos la Fase 1,
    los chunks existentes se ACTUALIZAN en lugar de duplicarse (idempotencia).
    """
    if not chunks:
        print("No hay chunks que indexar.")
        return None

    config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    collection = client.get_or_create_collection(
        name=config.CHROMA_COLLECTION,
        metadata={"hnsw:space": config.CHROMA_SPACE},  # coseno (bge) o dot/ip (MedCPT)
    )

    # ChromaDB limita cuántos registros admite por llamada (~5.461). Para corpus
    # grandes (p. ej. ampliar con miles de abstracts) troceamos el upsert en lotes.
    try:
        lote_max = client.get_max_batch_size()
    except Exception:
        lote_max = 5000  # valor seguro por defecto
    lote_max = max(1, min(lote_max, 5000))

    ids = [c["id"] for c in chunks]
    docs = [c["text"] for c in chunks]
    metas = [c["metadata"] for c in chunks]
    for i in range(0, len(chunks), lote_max):
        collection.upsert(
            ids=ids[i:i + lote_max],
            documents=docs[i:i + lote_max],
            embeddings=embeddings[i:i + lote_max],
            metadatas=metas[i:i + lote_max],
        )
    return collection


def index_new_bronze(paths, term):
    """Procesa e indexa SOLO los archivos bronze indicados (indexado incremental).

    Lo usa el agente Scout (Fase 3): tras descargar evidencia nueva, en vez de
    re-procesar todo el corpus, troceamos/embebemos/indexamos únicamente esos
    archivos. 'term' es la etiqueta de fármaco/tema buscado (para los metadatos).

    Devuelve el nº de chunks nuevos indexados.
    """
    por_id = {}
    for p in paths:
        if p is None:
            continue
        if p.name.startswith("ct_"):
            for doc in _parse_clinical_trials(p, term):
                _merge_doc(por_id, doc)
        elif p.name.startswith("pubmed_"):
            for doc in _parse_pubmed(p, term):
                _merge_doc(por_id, doc)

    docs = list(por_id.values())
    if not docs:
        return 0
    chunks = clean_and_chunk(docs, save_silver=False)
    if not chunks:
        return 0
    embeddings = embed_chunks(chunks)
    index_in_chroma(chunks, embeddings)
    return len(chunks)


# --------------------------------------------------------------------------
# Ejecución directa: procesa bronze → silver → chroma
# --------------------------------------------------------------------------

def run():
    """Pipeline completo de procesado: limpiar → trocear → embeber → indexar."""
    print("=" * 60)
    print(" MIA · Fase 1 — Procesado e indexado (silver + chroma)")
    print("=" * 60)
    chunks = clean_and_chunk()
    print(f"Chunks generados: {len(chunks)}")
    if not chunks:
        print("⚠ No hay datos en bronze. Ejecuta primero la ingesta.")
        return
    embeddings = embed_chunks(chunks)
    collection = index_in_chroma(chunks, embeddings)
    print(f"Chunks indexados en ChromaDB: {collection.count()}")
    print(f"Colección: {config.CHROMA_COLLECTION}  ·  Ruta: {config.CHROMA_DIR}")


if __name__ == "__main__":
    run()
