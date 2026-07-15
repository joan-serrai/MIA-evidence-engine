"""
src/rag.py — FASE 2: RAG estricto con citas.  [Módulo 8: RAG]

El corazón de MIA. Dada una pregunta:
  1. Busca los fragmentos más parecidos en ChromaDB (recuperación / retrieve).
  2. Inyecta esos fragmentos como contexto al LLM biomédico local.
  3. Obliga al modelo (vía system prompt) a responder SOLO con ese contexto
     y a citar la fuente de cada afirmación  → trazabilidad / cero alucinación.

RAG = Retrieval-Augmented Generation: en vez de fiarnos de lo que el modelo
"recuerda" (y puede inventar), le DAMOS la evidencia recuperada y le pedimos
que responda apoyándose solo en ella. Así cada dato es rastreable a su fuente.
"""

import re
import sys

import chromadb
import ollama
from dotenv import load_dotenv

try:
    from .. import config
    from . import embeddings, outcomes, citations
except (ImportError, ValueError):
    sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    import config
    from src import embeddings, outcomes, citations

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# Idea del "confinamiento" que usamos en el system prompt.
#
# ¿Por qué EN INGLÉS? OpenBioLLM está afinado casi por completo en textos
# médicos en inglés (y en exámenes tipo test estilo USMLE). Si le damos las
# instrucciones —o la pregunta— en español, degenera (repite el prompt o
# responde "The Answer is A" como si fuera un test). En inglés sigue la
# instrucción de forma fiable. La evidencia recuperada también está en inglés,
# así que es lo coherente.
# LIMITACIÓN CONOCIDA: por ahora hay que PREGUNTAR EN INGLÉS. Dar soporte a
# preguntas en español (traduciéndolas antes) es una mejora futura.
SYSTEM_PROMPT = (
    "You are a biomedical assistant. Answer the question using ONLY the "
    "information in the CONTEXT below. After each statement, cite its source as "
    "[Doc N]. If the context does not contain the answer, say so clearly and do "
    "not invent anything."
)

# --------------------------------------------------------------------------
# Generación robusta: prompt rediseñado + guardián anti-degeneración
# --------------------------------------------------------------------------
# PROBLEMA: OpenBioLLM (exam-tuned) degeneraba con el prompt anterior, sobre
# todo con entradas en forma de AFIRMACIÓN: respondía "The answer is YES." o
# copiaba el system prompt (incluido el literal "[Doc N]"). SOLUCIONES aplicadas:
#  1) Instrucciones en el mensaje de USUARIO (no en el system, que parroteaba).
#  2) System mínimo y neutro.
#  3) Un ejemplo few-shot que ancla el formato (prosa + citas reales [Doc 1]).
#  4) Reglas explícitas: nada de "the answer is yes/no", citar con números reales.

# Instrucciones en el SYSTEM (como el prompt original de la Fase 2, que SÍ
# producía respuestas citadas para preguntas normales). Añadimos solo dos reglas
# quirúrgicas para el problema que sí fallaba:
#   - tratar las AFIRMACIONES como algo a evaluar (no como examen verdadero/falso),
#   - prohibir explícitamente el degenerado "the answer is yes/no".
# Las instrucciones en el user (con few-shot) hacían que el modelo las parroteara.
# EVOLUCIÓN del prompt: además de factual y citada, queremos una respuesta
# ANALÍTICA y ACCIONABLE (estilo "analista de evidencia", no "enciclopedia"):
#   1) conclusión clave primero,
#   2) evidencia que la sostiene (comparando fármacos/poblaciones si el contexto lo permite),
#   3) implicación práctica o siguiente paso que la evidencia justifica.
# OJO: seguimos confinados al CONTEXT (RAG estricto) — "accionable" no es licencia
# para inventar: si la evidencia no alcanza, debe decir QUÉ falta.
_ANSWER_SYSTEM = (
    "You are a biomedical evidence analyst. Using ONLY the provided CONTEXT, "
    "write an analytical, decision-oriented answer in full sentences: state the "
    "key finding first, then the supporting evidence — comparing efficacy, "
    "safety or populations across documents when the CONTEXT allows — and close "
    "with the practical implication or next step that the evidence justifies. "
    "Cite the supporting document INLINE right after each sentence, e.g. [Doc 1] "
    "— one citation per sentence, not grouped at the end. Use the REAL numbers, "
    "never the literal text 'Doc N'. Do NOT copy the CONTEXT verbatim and never "
    "output source ids such as '(pubmed:...)'. Never answer with just 'the answer "
    "is yes/no' or a single letter. If the INPUT is a statement, assess whether "
    "the CONTEXT supports it and explain why, with citations. Do not go beyond "
    "the CONTEXT: if the evidence is insufficient for a recommendation, state "
    "exactly what is missing. Write directly for a clinician; do NOT open with or "
    "refer to 'the CONTEXT' or 'the provided documents' — just give the answer."
)


def _build_user_prompt(contexto, question):
    """Solo datos en el USER (contexto + consulta). Las instrucciones van en el
    system para que el modelo no las copie como respuesta."""
    return f"CONTEXT:\n{contexto}\n\nINPUT: {question}"


# Cita real: acepta [Doc 1], [Doc1], rangos [Doc 1-3] y AGRUPADAS [Doc 1, Doc 2, 3].
# (Antes exigía "[Doc 1]" con ] tras el número y rechazaba citas agrupadas válidas.)
_CITATION_RE = re.compile(r"\[doc[^\]]*\d", re.IGNORECASE)
_EXAM_RE = re.compile(r"^(the answer is|answer:)\s*(yes|no|true|false|[a-e])\b")
# El modelo a veces REGURGITA el bloque CONTEXT en vez de responder (copia
# "[Doc N] (pubmed:12345) Título…"). Esos ids de fuente SOLO existen en el
# contexto, nunca en una respuesta redactada → si aparecen, está degenerando.
_CONTEXT_ECHO_RE = re.compile(r"\((?:pubmed|clinicaltrials|ct)\s*:", re.IGNORECASE)
# OpenBioLLM a veces transcribe parte de una palabra a CIRÍLICO ("lebrikiz умаб"):
# se ve casi igual pero está corrupto (rompe copiar/buscar). El corpus y la respuesta
# son en inglés → cualquier letra cirílica es basura. Si aparece, reintentamos.
_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
# Fragmentos de las instrucciones (system) que NO deben aparecer en la respuesta
# (señal de que el modelo está parroteando el prompt en vez de responder).
_INSTRUCTION_MARKERS = (
    "using only the provided context", "after each claim", "the literal text",
    "never answer with", "if the input is a statement", "cite the supporting",
    # frases distintivas del prompt "analista" (si aparecen, está parroteando):
    "decision-oriented answer", "state the key finding first",
    "do not go beyond the context",
)


def _looks_degenerate(text):
    """True si la salida es basura: vacía, copia del prompt/contexto o modo examen.

    OJO: ya NO exigimos que el modelo cite. Las citas [Doc N] las coloca DESPUÉS,
    de forma determinista, `citations.redistribute_citations` (el 8B cita de forma
    poco fiable). Así una respuesta bien redactada pero sin citar es válida — le
    añadimos las citas nosotros — y evitamos fallbacks innecesarios.
    """
    if not text or len(text.strip()) < 15:
        return True
    t = text.strip()
    low = t.lower()
    if "[doc n]" in low:                    # copió el placeholder literal
        return True
    if _EXAM_RE.match(low):                  # "the answer is yes/no/A…"
        return True
    if _CONTEXT_ECHO_RE.search(t):           # regurgitó el bloque CONTEXT
        return True
    if _CYRILLIC_RE.search(t):               # coló homóglifos cirílicos (texto corrupto)
        return True
    if any(m in low for m in _INSTRUCTION_MARKERS):  # parroteó instrucciones
        return True
    return False


def _generate_answer(contexto, question, retries=3):
    """Genera la respuesta con reintentos y temperatura ESCALONADA.

    1er intento determinista (temp 0.0) → salida FIEL y REPRODUCIBLE. Ya no nos
    preocupa que a temp 0.0 el modelo no cite (medido: redacta bien pero omite las
    [Doc N]); las citas las pone luego el post-proceso determinista. Si degenera
    (eco/examen/parroteo), reintenta con temperatura al alza para escapar del bucle.
    Devuelve el texto o None si todo falla.
    """
    temps = [0.0, 0.3, 0.5, 0.7]
    user_prompt = _build_user_prompt(contexto, question)
    for intento in range(retries + 1):
        temp = temps[intento] if intento < len(temps) else temps[-1]
        resp = ollama.chat(
            model=config.LLM_MODEL,
            messages=[
                {"role": "system", "content": _ANSWER_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": temp, "num_ctx": 8192},
        )
        salida = (resp["message"]["content"] or "").strip()
        # El modelo a veces antepone una etiqueta ("OUTPUT:", "ANSWER:"); la quitamos.
        salida = re.sub(r"^\s*(output|answer)\s*:\s*", "", salida, flags=re.IGNORECASE).strip()
        if not _looks_degenerate(salida):
            return salida
    return None  # todas las tentativas degeneraron


# --------------------------------------------------------------------------
# Conexiones perezosas (se crean una vez y se reutilizan)
# --------------------------------------------------------------------------
# Abrir ChromaDB es costoso; lo hacemos una sola vez y lo cacheamos.
# (El modelo de embeddings se cachea dentro de src/embeddings.py.)
_COLLECTION = None


def _get_collection():
    """Abre la colección de ChromaDB creada en la Fase 1."""
    global _COLLECTION
    if _COLLECTION is None:
        client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        _COLLECTION = client.get_collection(config.CHROMA_COLLECTION)
    return _COLLECTION


def _full_doc_text(doc_id):
    """Texto COMPLETO de un documento (todos sus chunks ordenados por chunk_index),
    leído de ChromaDB.

    Lo usa la extracción de cifras (outcomes): el chunk con los números (p. ej. los
    EASI) puede NO estar entre los recuperados, pero pertenece al mismo artículo que
    citamos como [Doc N], así que la cifra sigue siendo trazable a esa fuente.
    """
    if not doc_id:
        return ""
    try:
        res = _get_collection().get(
            where={"doc_id": doc_id}, include=["documents", "metadatas"])
    except Exception:
        return ""
    pares = [(m.get("chunk_index", 0), d)
             for d, m in zip(res.get("documents") or [], res.get("metadatas") or [])]
    pares.sort(key=lambda c: c[0])
    return " ".join(t for _, t in pares)


# --------------------------------------------------------------------------
# 1) Recuperación (retrieve)
# --------------------------------------------------------------------------

def retrieve(question, top_k=None):
    """Devuelve los fragmentos (chunks) más relevantes desde ChromaDB, ordenados
    de más a menos parecido a la pregunta.

    Pasos:
      1. Convertir la pregunta en un vector con el MISMO modelo de la Fase 1.
      2. Pedir a Chroma MÁS candidatos que documentos queremos (distancia coseno).

    IMPORTANTE: aquí NO deduplicamos por documento. Devolvemos los chunks CRUDOS
    tal cual los ranquea Chroma (varios pueden ser del mismo artículo). La
    deduplicación y el "colapso" a UNA fuente por documento se hacen luego en
    `_build_context`, que así puede: contar cuántos fragmentos coincidieron y
    UNIR su texto como contexto de ese [Doc N]. Mover la dedup al ensamblado de
    citas mantiene el ranking intacto y no pierde los chunks hermanos.

    Devuelve una lista de dicts ordenados de más a menos relevante:
      {text, metadata, similarity}
    """
    if top_k is None:
        top_k = config.TOP_K

    coleccion = _get_collection()

    # La pregunta se embebe con el MISMO backend que los documentos (src/embeddings.py).
    # Con MedCPT usa la TORRE de preguntas; con bge, el mismo modelo. Va en una lista
    # porque Chroma espera una lista de vectores de consulta.
    query_vec = [embeddings.embed_query(question)]

    # Sobre-recuperamos (x4) para tener margen al agrupar por documento después:
    # varios chunks del top pueden ser del mismo artículo, así que pedimos de
    # sobra para que sigan quedando ~top_k DOCUMENTOS distintos tras deduplicar.
    res = coleccion.query(
        query_embeddings=query_vec,
        n_results=top_k * 4,
        include=["documents", "metadatas", "distances"],
    )

    fragmentos = []
    # Chroma devuelve listas anidadas (una por query) y ORDENADAS por similitud.
    for texto, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        fragmentos.append({
            "text": texto,
            "metadata": meta,
            # Con espacio coseno: distancia = 1 - similitud  →  similitud = 1 - distancia.
            "similarity": 1.0 - dist,
        })
    return fragmentos


# --------------------------------------------------------------------------
# 2) Construcción del contexto citado
# --------------------------------------------------------------------------

def _group_by_document(fragmentos, top_k=None):
    """Colapsa los chunks recuperados en UN registro por documento (PMID/NCT).

    Este es el corazón de la deduplicación de citas: antes, tres chunks del mismo
    artículo (p. ej. PMID 41392588::chunk0/1/2) aparecían como [Doc 1], [Doc 2] y
    [Doc 3] — tres "fuentes" que en realidad eran UNA. Aquí los agrupamos por
    `doc_id` para que cada artículo único sea UNA sola fuente.

    `fragmentos` viene de `retrieve`, ya ORDENADO por similitud, así que el primer
    chunk que vemos de cada documento es su mejor coincidencia. Recorremos en ese
    orden y, por cada documento, conservamos:
      - la MEJOR similitud (la del primer chunk visto),
      - CUÁNTOS de sus chunks coincidieron (n_fragments),
      - la lista de sus chunks (texto + chunk_index) para unirlos como contexto.

    Devolvemos hasta `top_k` DOCUMENTOS distintos, en orden de relevancia.
    """
    if top_k is None:
        top_k = config.TOP_K

    por_doc = {}   # doc_id → registro agrupado
    orden = []     # doc_ids en orden de primera aparición (= de mejor similitud)
    for frag in fragmentos:
        meta = frag["metadata"]
        doc_id = meta.get("doc_id")
        if doc_id not in por_doc:
            orden.append(doc_id)
            por_doc[doc_id] = {
                "metadata": meta,                    # metadatos del mejor chunk
                "similarity": frag["similarity"],    # la mejor (primer visto)
                "chunks": [],                        # (chunk_index, texto) de este doc
            }
        por_doc[doc_id]["chunks"].append(
            (meta.get("chunk_index", 0), frag["text"])
        )

    return [por_doc[d] for d in orden[:top_k]]


# Parte del corpus (set ingerido en formato TEXTO de PubMed) arrastra al inicio
# del texto la LÍNEA DE CITA cruda ("638. Dermatitis. 2025...doi:...Online ahead
# of print.."). El TÍTULO ya se limpió en la metadata; aquí limpiamos también el
# FRAGMENTO que se enseña en la tarjeta, para que la "ilustración" tenga sentido.
_CIT_HEAD_RE = re.compile(r"^\s*\d+\.\s")
_CIT_MARK_RE = re.compile(r"(doi:\s)|(\bEpub\b)|(ahead of print)|(eCollection)", re.IGNORECASE)
_CIT_BOUNDARY_RE = re.compile(r"\.\.\s+")   # frontera "cita.. contenido" que dejó processing


def _strip_citation_line(text):
    """Si el texto EMPIEZA por una línea de cita cruda, la quita y devuelve solo
    el contenido. Es SOLO para presentación (snippet); no toca lo que ve el LLM."""
    if not text:
        return text
    cabeza = text[:400]
    if _CIT_HEAD_RE.match(cabeza) and _CIT_MARK_RE.search(cabeza):
        m = _CIT_BOUNDARY_RE.search(text)
        if m:
            resto = text[m.end():].lstrip()
            if len(resto) >= 15:          # evita dejar el snippet vacío
                return resto
    return text


def _excerpt(text, max_chars=300):
    """Recorta un fragmento legible para la TARJETA de fuente en la UI.

    No afecta al contexto que ve el LLM: es solo presentación. Cumple la
    trazabilidad visual de MIA — el usuario ve el TEXTO REAL que respalda la
    cita, así la "ilustración" siempre corresponde con la fuente.
    """
    t = " ".join((_strip_citation_line(text) or "").split())   # limpia cita + normaliza
    if len(t) <= max_chars:
        return t
    return t[:max_chars].rsplit(" ", 1)[0].rstrip() + "…"   # corta en palabra


def _build_context(fragmentos):
    """Convierte los fragmentos recuperados en un bloque de texto numerado [Doc N],
    con UNA fuente por documento único (citas deduplicadas).

    Devuelve (texto_contexto, lista_de_fuentes). La numeración [Doc N] del contexto
    que ve el modelo y la de las fuentes que ve el usuario se generan en el MISMO
    bucle → siempre coinciden (ninguna cita queda "colgada" sin su fuente).
    """
    documentos = _group_by_document(fragmentos)

    lineas = []
    fuentes = []
    presupuesto = config.MAX_CONTEXT_CHARS  # tope de chars para todo el contexto
    for i, doc in enumerate(documentos, start=1):
        meta = doc["metadata"]
        # Unimos los chunks del documento en orden de lectura (por chunk_index)
        # para dar al modelo el contexto completo de ese artículo bajo un solo [Doc N].
        trozos = [t for _, t in sorted(doc["chunks"], key=lambda c: c[0])]
        texto_doc = " ".join(trozos)

        # Cifras numéricas (EASI, IGA...) extraídas VERBATIM del artículo COMPLETO
        # (todos sus chunks, no solo los recuperados), etiquetadas con este [Doc i]
        # → alimentan el gráfico de la UI. Determinista: no lo genera el LLM.
        outcomes_doc = outcomes.extract_outcomes(_full_doc_text(meta.get("doc_id")), i)

        # Respetar la ventana del LLM (num_ctx): si nos pasamos del presupuesto,
        # recortamos el texto de esta fuente (mejor una cita algo corta que
        # desbordar el contexto y que Ollama lo trunque a ciegas).
        if len(texto_doc) > presupuesto:
            texto_doc = texto_doc[:max(0, presupuesto)].rstrip() + " […]"
        presupuesto -= len(texto_doc)

        n_frag = len(doc["chunks"])
        # Contexto para el LLM: SOLO la etiqueta [Doc i] + el texto. NO metemos el
        # título ni el id de fuente (pubmed:...): el modelo tiende a recitarlos
        # literalmente en la respuesta (títulos larguísimos, a veces en MAYÚSCULAS),
        # y no aportan nada para razonar porque el contenido ya está en el abstract.
        # El título/id se conservan aparte, en 'fuentes', para el panel de la UI.
        lineas.append(f"[Doc {i}]\n{texto_doc}")
        fuentes.append({
            "n": i,
            "source": meta.get("source"),
            "doc_id": meta.get("doc_id"),
            "title": meta.get("title"),
            "url": meta.get("url"),
            "drugs": meta.get("drugs"),
            "similarity": round(doc["similarity"], 3),
            "n_fragments": n_frag,  # cuántos chunks de este artículo coincidieron
            # Texto real del MEJOR chunk de este doc (chunks[0] = mejor similitud,
            # NO 'trozos', que está reordenado por chunk_index para el LLM). Solo UI.
            "snippet": _excerpt(doc["chunks"][0][1]),
            "outcomes": outcomes_doc,  # cifras verbatim para el gráfico (puede ir vacío)
        })

        if presupuesto <= 0:
            break  # sin espacio para más fuentes; no dejamos [Doc N] a medias

    return "\n\n".join(lineas), fuentes


# --------------------------------------------------------------------------
# 3) Pipeline completo (answer)
# --------------------------------------------------------------------------

def answer(question, top_k=None):
    """Pipeline RAG completo: recuperar → construir prompt → preguntar al LLM local.

    Devuelve un dict:
      {
        "answer":  texto de la respuesta del modelo,
        "sources": lista de fuentes citadas (para mostrarlas en la UI),
        "has_evidence": bool — si la mejor coincidencia supera el umbral,
      }

    Nota: el OLLAMA_HOST del .env se aplica automáticamente al cargar dotenv.
    """
    load_dotenv()  # respeta OLLAMA_HOST si está definido en .env

    fragmentos = retrieve(question, top_k)

    # ¿Tenemos evidencia local suficientemente buena?
    mejor = fragmentos[0]["similarity"] if fragmentos else 0.0
    has_evidence = mejor >= config.SIMILARITY_THRESHOLD

    contexto, fuentes = _build_context(fragmentos)

    # RAG ESTRICTO: si la evidencia local es débil, NO dejamos que el modelo
    # responda de memoria (evita alucinaciones tipo "the answer is Paris").
    # Aquí es donde en la FASE 3 entrará el agente Scout a buscar fuera.
    if not has_evidence:
        return {
            "answer": ("No encuentro evidencia local suficiente para responder "
                       "a esta pregunta. (En la Fase 3, el agente Scout saldrá a "
                       "buscarla en PubMed/ClinicalTrials.)"),
            "sources": fuentes,
            "has_evidence": False,
        }

    # Generación robusta: prompt con instrucciones+few-shot en el user, guardián
    # anti-degeneración y reintentos (ver _generate_answer / _looks_degenerate).
    salida = _generate_answer(contexto, question)
    if salida is None:
        # Tras los reintentos seguía degenerando: mensaje claro, NO basura.
        salida = ("No he podido generar una respuesta fiable a partir de la "
                  "evidencia recuperada. Prueba a reformular (mejor como "
                  "pregunta que como afirmación) e inténtalo de nuevo.")
    else:
        # El modelo 8B suele AMONTONAR las citas al final; las repartimos a la
        # frase que cada una respalda (post-proceso determinista y conservador).
        salida = citations.redistribute_citations(salida, fuentes)

    return {
        "answer": salida,
        "sources": fuentes,
        "has_evidence": has_evidence,
    }


# --------------------------------------------------------------------------
# Ejecución directa: prueba rápida del RAG
# --------------------------------------------------------------------------

if __name__ == "__main__":
    pregunta = " ".join(sys.argv[1:]) or "What is the efficacy of dupilumab in atopic dermatitis?"
    print("=" * 60)
    print(f"Pregunta: {pregunta}")
    print("=" * 60)
    resultado = answer(pregunta)
    print("\n--- RESPUESTA ---\n")
    print(resultado["answer"])
    print(f"\n--- FUENTES (evidencia local: {'sí' if resultado['has_evidence'] else 'débil'}) ---")
    for f in resultado["sources"]:
        print(f"  [Doc {f['n']}] {f['source']}:{f['doc_id']}  (sim {f['similarity']}, "
              f"{f['n_fragments']} frag)  {f['url']}")
