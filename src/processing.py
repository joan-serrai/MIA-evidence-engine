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


def _drug_labels(label, texto):
    """Etiquetas de fármaco de un documento.

    Los archivos bronze de los fármacos del perfil vienen etiquetados con el
    fármaco buscado ("ct_dupilumab.json" → 'dupilumab'). Pero los del Scout y los de
    las búsquedas EXTRA vienen con el término de búsqueda entero ("IL-17 inhibitor
    AND (Plaque psoriasis OR psoriasis)"), que acababa pintado tal cual en las
    píldoras de la interfaz. Aquí: si la etiqueta es un fármaco conocido del perfil,
    se conserva; si no, se etiquetan los fármacos del perfil que el TEXTO menciona
    y, si no menciona ninguno, el slug del dominio (p. ej. 'plaque_psoriasis').
    """
    conocidos = [d.lower() for d in config.ALL_DRUGS]
    if (label or "").lower() in conocidos:
        return [label.lower()]
    low = (texto or "").lower()
    en_texto = [d for d in conocidos if d in low]
    return en_texto or [config.DOMAIN_SLUG]


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
        # El "texto" útil de un ensayo = título + resumen + condiciones + RESULTADOS.
        # Los resultados (eventos adversos y medidas de eficacia) vienen en la MISMA
        # respuesta de la API que ya descargábamos, pero hasta ahora se tiraban: solo
        # se guardaba la ficha descriptiva. Ver _ct_results_text.
        resultados = _ct_results_text(estudio)
        texto = _normalize_text(
            f"{titulo}. {resumen}. Conditions: {', '.join(conds)}. {resultados}")

        docs.append({
            "source": "clinicaltrials",
            "doc_id": nct,
            "title": _normalize_text(titulo),
            "text": texto,
            "authors": [],  # ClinicalTrials no da autores como tal
            "drugs": _drug_labels(drug, texto),
            "disease": config.DISEASE,
            "url": f"https://clinicaltrials.gov/study/{nct}",
            # Los registros de ClinicalTrials.gov son públicos por diseño (el
            # protocolo/resumen es abierto), así que nunca hay "muro de pago".
            "doi": "",
            "access": "open",
        })
    return docs


# --------------------------------------------------------------------------
# Resultados de ClinicalTrials.gov  (eventos adversos + medidas de eficacia)
# --------------------------------------------------------------------------
# POR QUÉ ESTO EXISTE. Hasta ahora, de cada ensayo solo guardábamos la ficha
# descriptiva (título + resumen + condiciones): la "portada". Pero la MISMA
# respuesta de la API que ya descargábamos trae un `resultsSection` con los
# resultados YA TABULADOS — y lo estábamos tirando. Medido sobre data/bronze:
# 81 de 224 ensayos (36%) lo traían, con 5.845 filas de eventos adversos.
#
# Es la mejor materia prima del proyecto, mejor incluso que un abstract:
#   - viene con numerador y denominador reales (5/55), no con prosa,
#   - viene POR BRAZO, así que hay comparación contra placebo,
#   - separa eventos graves de leves y los clasifica por sistema orgánico,
#   - lo publica el promotor en un registro oficial: es dato primario.
#
# FORMATO DE SALIDA: prosa, no una tabla. Dos motivos. (1) El LLM lee prosa; una
# tabla ASCII la interpreta mal. (2) `src/outcomes.py` extrae las cifras con
# regex buscando "métrica … N%" en la misma frase, así que escribimos UNA
# afirmación por frase, con el brazo de TRATAMIENTO primero y el comparador en
# una frase aparte. Si metiéramos ambos en la misma frase, el extractor cogería
# los dos porcentajes y no sabría cuál es de qué brazo → cifras engañosas.

# Nº máximo de eventos NO graves que incluimos por ensayo (los graves van todos).
# Un ensayo llega a listar 70+ eventos; los de frecuencia ínfima solo añaden ruido
# y hacen crecer el documento. Nos quedamos con los más frecuentes, que es justo
# lo que responde a "¿cuáles son los efectos adversos más comunes?".
_CT_MAX_OTHER_EVENTS = 20


# Un brazo de CONTROL empieza por placebo/vehículo. Buscar la palabra "placebo"
# en cualquier posición NO vale: en los ensayos doble ciego (doble dummy) los
# brazos activos se llaman "Dupilumab 300 mg + Oral Placebo up to Week 16", y con
# la regla ingenua TODOS los brazos salían como control → no se extraía ni una
# cifra. Se permite un adjetivo delante ("Matching Placebo", "Oral Placebo").
_COMPARATOR_RE = re.compile(
    r"^\s*(?:matching\s+|oral\s+|topical\s+|subcutaneous\s+|double[-\s]?dummy\s+)?"
    r"(placebo|vehicle|control|sham)\b", re.IGNORECASE)


def _is_comparator_arm(titulo):
    """¿Es un brazo de control (placebo/vehículo) y no de fármaco activo?

    Orden de comprobación: si el nombre del brazo menciona un fármaco conocido
    del estudio, es ACTIVO pase lo que pase (aunque lleve "+ Placebo" detrás).
    """
    t = (titulo or "").lower()
    if any(d.lower() in t for d in config.ALL_DRUGS):
        return False
    return bool(_COMPARATOR_RE.match(t))


def _ct_event_rows(ae_module):
    """Aplana el módulo de eventos adversos a filas manejables.

    Devuelve [{term, organ, serious, arm, pct, n, at_risk, comparator}], donde
    cada fila es el brazo de TRATAMIENTO con la tasa más alta para ese evento, y
    'comparator' (si existe) los datos del brazo de control para el mismo evento.
    """
    grupos = {g.get("id"): (g.get("title") or "") for g in ae_module.get("eventGroups", [])}
    filas = []
    for clave, serio in (("seriousEvents", True), ("otherEvents", False)):
        for ev in ae_module.get(clave, []) or []:
            term = (ev.get("term") or "").strip()
            if not term:
                continue
            tratamiento, control = [], []
            for s in ev.get("stats", []) or []:
                n, ar = s.get("numAffected"), s.get("numAtRisk")
                if n is None or not ar:
                    continue
                dato = {"arm": grupos.get(s.get("groupId"), ""),
                        "n": n, "at_risk": ar, "pct": round(n / ar * 100, 1)}
                (control if _is_comparator_arm(dato["arm"]) else tratamiento).append(dato)
            if not tratamiento:
                continue
            mejor = max(tratamiento, key=lambda d: d["pct"])
            filas.append({
                "term": term,
                "organ": (ev.get("organSystem") or "").strip(),
                "serious": serio,
                **mejor,
                "comparator": max(control, key=lambda d: d["pct"]) if control else None,
            })
    return filas


def _ct_adverse_events_text(ae_module):
    """Prosa con los eventos adversos: todos los graves + los N más frecuentes."""
    filas = _ct_event_rows(ae_module)
    if not filas:
        return ""
    graves = sorted([f for f in filas if f["serious"]], key=lambda f: -f["pct"])
    otros = sorted([f for f in filas if not f["serious"]],
                   key=lambda f: -f["pct"])[:_CT_MAX_OTHER_EVENTS]

    def _frases(bloque, etiqueta):
        if not bloque:
            return ""
        out = [f"{etiqueta}:"]
        for f in bloque:
            # Frase 1: el brazo de tratamiento (la que el extractor de cifras leerá).
            org = f" ({f['organ']})" if f["organ"] else ""
            out.append(f"{f['term']}{org} was reported in {f['pct']}% "
                       f"({f['n']}/{f['at_risk']}) of participants receiving "
                       f"{f['arm']}.")
            # Frase 2: el comparador, SIN repetir el nombre del evento, para que la
            # cifra del control no se atribuya al evento como si fuera del fármaco.
            c = f["comparator"]
            if c:
                out.append(f"The corresponding rate on {c['arm']} was {c['pct']}% "
                           f"({c['n']}/{c['at_risk']}).")
        return " ".join(out)

    partes = [_frases(graves, "Serious adverse events"),
              _frases(otros, "Most frequent other adverse events")]
    return " ".join(p for p in partes if p)


# Los títulos de las medidas de resultado de CT.gov son largos y prosaicos:
#   "Percentage of Participants Achieving Eczema Area and Severity Index (EASI)
#    Response >=75 Percent (%) Improvement From Baseline at Week 12"
# `outcomes.py` busca la forma corta ("EASI 75"), que ahí NO aparece, así que sin
# esto no se extraía ni una cifra de eficacia. Traducimos el título al nombre
# canónico del endpoint y lo escribimos pegado al valor.
# GENERALIZACIÓN (3-sep-2026): las reglas ya no son una lista fija de EASI/IGA/
# SCORAD. Salen del PERFIL DE DOMINIO activo (`config.EFFICACY_ENDPOINTS`, cada
# uno con su `ct_title`: los patrones que deben aparecer TODOS en el título de la
# medida). Ver `config.endpoint_spec` para cómo se derivan de una etiqueta.
def _endpoint_rules():
    """[(nombre_corto, [patrones…])] del dominio activo, en el orden del perfil."""
    return [(e["label"], e["ct_title"]) for e in config.EFFICACY_ENDPOINTS]


def _canonical_endpoint(titulo):
    """Nombre corto del endpoint ('EASI 75', 'IGA 0/1') o None si no se reconoce."""
    t = (titulo or "").lower()
    for nombre, patrones in _endpoint_rules():
        if all(re.search(p, t) for p in patrones):
            return nombre
    return None


def _ct_outcomes_text(om_module):
    """Prosa con las medidas de resultado (eficacia) declaradas del ensayo.

    Solo añadimos el símbolo '%' cuando la unidad ES un porcentaje: así las tasas
    de respuesta reales (EASI-75, IGA 0/1…) las recoge `outcomes.py`, y un valor
    en otra unidad (puntos de escala, ng/mL) NO se cuela como si fuera un %.
    """
    medidas = om_module.get("outcomeMeasures", []) or []
    if not medidas:
        return ""
    out = []
    for m in medidas[:6]:                       # tope: las 6 primeras (primarias antes)
        titulo = (m.get("title") or "").strip()
        if not titulo:
            continue
        unidad = (m.get("unitOfMeasure") or "").strip()
        es_pct = "percent" in unidad.lower()
        grupos = {g.get("id"): (g.get("title") or "") for g in m.get("groups", []) or []}
        medidos = []
        for cl in m.get("classes", []) or []:
            for cat in cl.get("categories", []) or []:
                for med in cat.get("measurements", []) or []:
                    v = med.get("value")
                    if v is None:
                        continue
                    medidos.append((grupos.get(med.get("groupId"), ""), v))
        if not medidos:
            out.append(f"{m.get('type','').title()} outcome measure: {titulo}.")
            continue
        trat = [x for x in medidos if not _is_comparator_arm(x[0])]
        ctrl = [x for x in medidos if _is_comparator_arm(x[0])]
        suf = "%" if es_pct else f" {unidad}" if unidad else ""
        # El título va en SU PROPIA frase y el endpoint canónico + valor en otra.
        # Así, si el extractor encuentra "EASI" dentro del título, su ventana se
        # corta al acabar esa frase (sin cifras) y no inventa un dato; el valor
        # bueno lo lee de la frase corta, donde está pegado al nombre del endpoint.
        canon = _canonical_endpoint(titulo) if es_pct else None
        out.append(f"{m.get('type','').title()} outcome measure: {titulo}.")
        etiqueta = canon or "Value"
        if trat:
            arm, val = max(trat, key=lambda x: _num(x[1]))
            out.append(f"{etiqueta}: {val}{suf} with {arm}.")
        if ctrl:
            arm, val = ctrl[0]
            out.append(f"The corresponding value on {arm} was {val}{suf}.")
    return (" ".join(out)) if out else ""


def _num(v):
    """float(v) tolerante: los valores de la API llegan como texto y a veces vacíos."""
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return float("-inf")


def _ct_results_text(estudio):
    """Texto de resultados de UN ensayo (o cadena vacía si no publicó ninguno).

    Solo ~36% de los ensayos tienen `resultsSection`: los que aún reclutan o no
    han publicado no lo traen. En ese caso devolvemos "" y el documento queda
    exactamente como antes — el cambio es puramente aditivo.
    """
    rs = (estudio or {}).get("resultsSection") or {}
    if not rs:
        return ""
    partes = [
        _ct_outcomes_text(rs.get("outcomeMeasuresModule", {}) or {}),
        _ct_adverse_events_text(rs.get("adverseEventsModule", {}) or {}),
    ]
    cuerpo = " ".join(p for p in partes if p)
    return f"Reported trial results. {cuerpo}" if cuerpo else ""


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

        # ACCESO ("paper de pago"): PubMed nos da SIEMPRE el abstract gratis, pero
        # el TEXTO COMPLETO puede estar tras un muro de pago. La señal fiable y
        # gratuita: si el artículo tiene un id de PubMed Central (PMC), su texto
        # completo es de acceso abierto; si solo tiene DOI (editorial) y no PMC,
        # lo tratamos como "solo resumen" → MIA avisará de que hay más detrás.
        # (No hacemos scraping del PDF de pago: sería ilegal; solo lo señalamos.)
        pmc = doi = ""
        for aid in article.findall(".//PubmedData/ArticleIdList/ArticleId"):
            tipo = (aid.get("IdType") or "").lower()
            if tipo == "pmc" and aid.text:
                pmc = aid.text.strip()
            elif tipo == "doi" and aid.text:
                doi = aid.text.strip()
        access = "open" if pmc else "abstract_only"

        texto = _normalize_text(f"{titulo}. {abstract}")
        # Si no hay abstract, el título solo suele ser demasiado pobre; lo
        # conservamos igualmente (mejor poco que nada para la demo).
        docs.append({
            "source": "pubmed",
            "doc_id": pmid,
            "title": _normalize_text(titulo),
            "text": texto,
            "authors": autores,
            "drugs": _drug_labels(drug, texto),
            "disease": config.DISEASE,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "doi": doi,
            "access": access,
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
                    # Acceso al texto completo: "open" (PMC / ensayo público) o
                    # "abstract_only" (solo resumen; el texto completo puede estar
                    # de pago). ChromaDB exige metadatos ESCALARES → guardamos
                    # strings, con defaults por si un doc viejo no los trae.
                    "doi": doc.get("doi", ""),
                    "access": doc.get("access", "open"),
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
        print("No chunks to index.")
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
    print(" MIA · Phase 1 — Processing and indexing (silver + chroma)")
    print("=" * 60)
    chunks = clean_and_chunk()
    print(f"Chunks generated: {len(chunks)}")
    if not chunks:
        print("⚠ No data in bronze. Run the ingestion first.")
        return
    embeddings = embed_chunks(chunks)
    collection = index_in_chroma(chunks, embeddings)
    print(f"Chunks indexed in ChromaDB: {collection.count()}")
    print(f"Collection: {config.CHROMA_COLLECTION}  ·  Path: {config.CHROMA_DIR}")


if __name__ == "__main__":
    run()
