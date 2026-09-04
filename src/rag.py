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
    "Where several documents cover the same ground, make their relation explicit: "
    "which ones converge, and where they diverge and by how much. A reader must "
    "be able to see which paper said what, never a blur of 'studies show'. "
    "When you report a result, give the specific figure — the named endpoint "
    "(e.g. {endpoints}), its value, the timepoint and the "
    "population — whenever the CONTEXT provides it, instead of vague words like "
    "'effective'. Compare two drugs directly only if the CONTEXT contains a "
    "head-to-head study; if the figures come from separate studies, say the "
    "comparison is indirect and should be read with caution. Keep a neutral, "
    "professional, non-promotional tone. "
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


# --------------------------------------------------------------------------
# Detección de INTENCIÓN de la pregunta  (personalización dinámica)
# --------------------------------------------------------------------------
# Adaptamos el ÉNFASIS de la respuesta a lo que la pregunta pide (eficacia,
# seguridad, comparativa, mecanismo). Lo hacemos con un router LIGERO por
# palabras clave, NO con el LLM: es instantáneo, gratis, auditable (ves por qué
# clasificó) y evita una llamada extra a un modelo local que además clasifica
# mal. Es la mitad "dinámica" del perfil de usuario; el "rol" fijo (MSL, Market
# Access…) queda para más adelante, y se añadiría igual: como otro modificador.
#
# PRINCIPIO CLAVE: el modificador solo REORDENA el foco. NO relaja ninguna regla
# de seguridad del núcleo (_ANSWER_SYSTEM): citar siempre, no salir del CONTEXT,
# no alucinar. Personalizamos la presentación, nunca la integridad.
_INTENT_PATTERNS = [
    ("safety", re.compile(
        r"\b(safe(?:ty)?|adverse|side[-\s]?effect|tolerab|toxic|risk|warning|"
        r"discontinu|boxed[-\s]?warning|black[-\s]?box)\w*", re.IGNORECASE)),
    ("comparative", re.compile(
        r"\b(compare|comparison|versus|vs\.?|better\s+than|superior|"
        r"head[-\s]?to[-\s]?head|difference\s+between|relative\s+to)\b", re.IGNORECASE)),
    ("mechanism", re.compile(
        r"\b(mechanism|mode\s+of\s+action|moa|pathway|receptor|"
        r"how\s+does\s+\w+\s+work)\b", re.IGNORECASE)),
]

# Modificador que se AÑADE al system prompt según la intención detectada.
_INTENT_MODIFIERS = {
    "efficacy": (
        "FOCUS: Lead with the efficacy outcome — the named endpoint (e.g. {endpoints}), "
        "its value, timepoint and population as given in the CONTEXT."),
    "safety": (
        "FOCUS: Lead with adverse events — their type, frequency, severity and "
        "discontinuation rate — and surface any serious or class-level safety signal "
        "present in the CONTEXT."),
    "comparative": (
        "FOCUS: Emphasize the comparison asked for. State a difference only if the "
        "CONTEXT supports it; if it spans separate studies, call it an indirect "
        "comparison and advise caution."),
    "mechanism": (
        "FOCUS: Explain the mechanism of action and the drug target clearly, defining "
        "the biomedical terms you use."),
}


def detect_intent(question):
    """Clasifica la pregunta en una intención para ENFOCAR la respuesta.

    Devuelve 'safety' | 'comparative' | 'mechanism' | 'efficacy' (por defecto).
    Precedencia por orden de _INTENT_PATTERNS: seguridad y comparativa (señales
    de alto valor) se comprueban antes que mecanismo; si nada casa, asumimos que
    la pregunta es de eficacia (el caso más común en el corpus de MIA).
    """
    q = question or ""
    for etiqueta, patron in _INTENT_PATTERNS:
        if patron.search(q):
            return etiqueta
    return "efficacy"


# --------------------------------------------------------------------------
# Conversación dinámica: condensación del follow-up  (RAG conversacional)
# --------------------------------------------------------------------------
# PROBLEMA: hasta ahora cada pregunta era independiente. Si preguntas
# "Is lebrikizumab effective?" y luego "and its safety?", MIA no sabía que
# "its" = lebrikizumab → recuperaba evidencia genérica y perdía el hilo.
#
# SOLUCIÓN (patrón "condense question" del RAG conversacional): antes de
# recuperar, reescribimos el follow-up como una pregunta AUTÓNOMA usando el
# historial ("and its safety?" → "What is the safety profile of lebrikizumab in
# atopic dermatitis?"). Con esa pregunta ya completa, el retrieval, el router de
# intención y el Scout funcionan igual que siempre — sin tocar el núcleo.
#
# DECISIONES de diseño (importan por el modelo 8B, que degenera fácil):
#   1) Router BARATO primero: solo llamamos al LLM si la pregunta PARECE un
#      follow-up dependiente (corta, con pronombre, o empieza por "and/what about").
#      Una pregunta ya autónoma no gasta una llamada ni arriesga degeneración.
#   2) La condensación NUNCA rompe el pipeline: si el LLM devuelve algo vacío,
#      larguísimo o corrupto, caemos a la pregunta original. Robustez > elegancia.
_FOLLOWUP_PRONOUN_RE = re.compile(
    r"\b(it|its|it's|they|them|their|that|this|those|these|he|she|him|her|the drug|"
    r"the same|both|either)\b", re.IGNORECASE)
_FOLLOWUP_START_RE = re.compile(
    r"^\s*(and|but|what about|how about|also|vs\.?|versus|compared|then|so|what if|"
    r"why|and what|ok|okay)\b", re.IGNORECASE)


def _looks_like_followup(question):
    """Heurística barata: ¿esta pregunta DEPENDE del contexto previo?

    True si es corta, empieza por conector de seguimiento, o usa un pronombre sin
    sujeto propio. Falso si parece autónoma (ya nombra su tema) → así no
    malgastamos una llamada al LLM ni arriesgamos que degenere en preguntas que
    ya se entienden solas.
    """
    q = (question or "").strip()
    if not q:
        return False
    n_palabras = len(q.split())
    if _FOLLOWUP_START_RE.search(q):
        return True
    if n_palabras <= 7 and _FOLLOWUP_PRONOUN_RE.search(q):
        return True
    if n_palabras <= 4:            # "safety?", "and children?" → claramente dependiente
        return True
    return False


def condense_question(question, history, max_turns=4):
    """Reescribe un follow-up como pregunta autónoma usando el historial reciente.

    `history`: lista de mensajes {role, content} (como la de Streamlit). Tomamos
    los últimos `max_turns` turnos user/assistant. Devuelve la pregunta reescrita
    o, ante cualquier duda, la ORIGINAL (nunca falla hacia un estado peor).
    """
    if not history or not _looks_like_followup(question):
        return question

    recientes = [m for m in history
                 if m.get("role") in ("user", "assistant") and m.get("content")][-max_turns:]
    if not recientes:
        return question

    # Recortamos cada turno (las respuestas del asistente son largas) para no
    # desbordar el contexto ni confundir al modelo con paja.
    convo = "\n".join(
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {str(m['content'])[:300]}"
        for m in recientes
    )
    prompt = (
        "You rewrite a follow-up question into a standalone question. Use the "
        "conversation to fill in what the follow-up leaves implicit (the drug, the "
        "disease, the topic). Keep it in English and keep the user's intent. Reply "
        "with ONLY the rewritten question on one single line, nothing else.\n\n"
        f"Conversation:\n{convo}\n\nFollow-up: {question}\n\nStandalone question:"
    )
    try:
        r = ollama.chat(
            model=config.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_ctx": 4096},
        )
        salida = (r["message"]["content"] or "").strip().splitlines()[0].strip()
        salida = salida.strip('"').strip("'").strip()
        # Validación conservadora: ni vacío, ni parrafada, ni corrupto, ni eco del
        # prompt/contexto. Si algo huele mal, nos quedamos con la pregunta original.
        if (salida
                and 3 <= len(salida.split()) <= 40
                and not _CYRILLIC_RE.search(salida)
                and "context" not in salida.lower()
                and "[doc" not in salida.lower()
                and "standalone question" not in salida.lower()):
            return salida
    except Exception as e:
        print(f"   [aviso] condensación de la pregunta falló: {e}")
    return question


# Directiva de FORMATO que va al FINAL del mensaje de usuario, después del
# contexto y la pregunta.
#
# ¿Por qué aquí y no en el system? Medido: con la instrucción de longitud dentro
# del system (que ya es largo), OpenBioLLM 8B la ignoraba y despachaba la pregunta
# en UNA frase. Los modelos pequeños atienden mucho más a lo último que leen, así
# que la orden operativa va al final. Se mantiene CORTA y en imperativo para que
# no la parrotee (y si la parrotea, `_looks_degenerate` lo caza y reintenta).
#
# Lo de "una afirmación por frase" no es estilo: es lo que permite a
# src/citations.py colgar la cita correcta a cada afirmación. Si el modelo mete
# tres hechos de tres papers en una frase, esa frase no se puede atribuir a una
# sola fuente sin mentir.
_TASK_DIRECTIVE = (
    "TASK: Answer the QUESTION above using only the CONTEXT.\n"
    "- Answer exactly what the QUESTION asks, in its first sentence.\n"
    "- Write 5 to 9 sentences in three short paragraphs. Never answer in a "
    "single sentence.\n"
    "- Put ONE self-contained claim in each sentence, so each sentence traces to "
    "one document. Never merge findings from different studies into one sentence.\n"
    "- Say WHOSE finding it is: open the sentence with the study's design and "
    "population as the CONTEXT gives them — 'a meta-analysis of adults', 'a "
    "pediatric trial', 'a real-world review' — instead of 'multiple studies'.\n"
    "- Take your sentences from DIFFERENT documents, and when two of them cover "
    "the same endpoint say plainly whether they agree or differ: 'a pediatric "
    "trial reported A, whereas a meta-analysis in adults found B'.\n"
    "- Name the specific items the CONTEXT gives — adverse events by name, "
    "endpoints, numeric values, populations, timepoints. Never write a vague "
    "summary such as 'adverse events occurred' or 'showed improvement'.\n"
    "- Write the content directly, as a clinician would. NEVER use the phrases "
    "'the key finding', 'the supporting evidence', 'the practical implication' "
    "or 'the evidence does not settle' — do not narrate your own structure.\n"
    "- End each sentence with its source, e.g. [Doc 2]."
)

# Cierre por intención: se pega al final del todo (lo último que lee el modelo)
# para que la pregunta concreta gane a la inercia del corpus. Sin esto, medido:
# a "most common adverse events of upadacitinib?" respondía sobre EFICACIA en
# adolescentes, porque es lo que más abunda en los abstracts recuperados.
_TASK_FOCUS = {
    "safety": ("- FOCUS: list the adverse events BY NAME with their frequency and "
               "severity, plus discontinuation rates and any serious safety signal."),
    "efficacy": ("- FOCUS: lead with the efficacy outcome — named endpoint, value, "
                 "timepoint and population."),
    "comparative": ("- FOCUS: make the comparison the QUESTION asks for; if the "
                    "figures come from separate studies, say it is indirect."),
    "mechanism": ("- FOCUS: explain the mechanism of action and the drug target, "
                  "defining the biomedical terms you use."),
}


# Preguntas de SÍ/NO ("Is X effective…?", "Does X cause…?"). Medido el 4-sep-2026
# con el Scout en retinoblastoma: a "Is abemaciclib effective for retinoblastoma?"
# el 8B contestó "Abemaciclib is not effective for retinoblastoma." — UNA frase,
# sin cita, en modo veredicto de examen, y los cuatro reintentos devolvieron lo
# mismo. Con la MISMA evidencia, "What is the evidence for abemaciclib in
# retinoblastoma?" produjo tres frases citadas. Es el mismo tic USMLE que ya
# tratábamos con `_EXAM_RE`, pero disparado por la forma de la pregunta. Arreglo
# determinista: la pregunta que ve el REDACTOR se reformula como pregunta abierta;
# la recuperación, el router de intención y la interfaz siguen usando la original.
_YESNO_RE = re.compile(
    r"^\s*(is|are|was|were|does|do|did|can|could|should|has|have|will|would)\b",
    re.IGNORECASE)


def open_phrasing(question):
    """'Is X effective for Y?' → 'Summarize the evidence relevant to this question,
    with the specific findings: Is X effective for Y?' (solo para el prompt de
    redacción). Otras preguntas, intactas."""
    q = (question or "").strip()
    m = _YESNO_RE.match(q)
    if not m:
        return question
    # Se conserva la pregunta tal cual (así el modelo sigue entendiendo qué se
    # le pregunta) y se antepone un encargo abierto: resumir la evidencia con sus
    # hallazgos concretos, en vez de emitir un veredicto.
    return f"Summarize the evidence relevant to this question, with the specific findings: {q}"


def _build_user_prompt(contexto, question, intent=None):
    """Contexto + consulta + directiva de formato + foco de la intención, en ese
    orden (lo más operativo, al final).

    Las reglas de SEGURIDAD (no salir del contexto, no alucinar) siguen en el
    system; aquí solo va el CÓMO presentar la respuesta.
    """
    foco = _TASK_FOCUS.get(intent or "", "")
    directiva = f"{_TASK_DIRECTIVE}\n{foco}" if foco else _TASK_DIRECTIVE
    return f"CONTEXT:\n{contexto}\n\nQUESTION: {question}\n\n{directiva}"


# Cita real: acepta [Doc 1], [Doc1], rangos [Doc 1-3] y AGRUPADAS [Doc 1, Doc 2, 3].
# (Antes exigía "[Doc 1]" con ] tras el número y rechazaba citas agrupadas válidas.)
_CITATION_RE = re.compile(r"\[doc[^\]]*\d", re.IGNORECASE)
# MODO EXAMEN. OpenBioLLM está afinado con preguntas tipo USMLE y a veces arranca
# como si estuviera corrigiendo un test. El patrón ANTERIOR exigía que tras "the
# answer is" viniera yes/no/true/false/A-E, así que se le colaba el caso REAL medido
# el 1-sep-2026 con "What is the efficacy of dupilumab...?": la respuesta empezó con
# "The Answer is: Dupilumab is effective in treating atopic dermatitis…" — el mismo
# tic de examen, pero seguido de prosa normal, así que pasaba el filtro intacto.
# Se tolera el adorno markdown inicial ("**The answer is:**") y el rodeo
# "the answer to this question is", que el modelo también usa.
#
# CORRECCIÓN (3-sep-2026). La versión anterior DESCARTABA toda respuesta que abriera
# así y reintentaba con más temperatura. Medido 6 veces con la pregunta de demo
# ("What is the efficacy of dupilumab…?"): a temperatura 0 el modelo SIEMPRE abre
# con "The Answer is: Dupilumab is effective…" seguido de prosa correcta; al subir
# la temperatura se rinde ("The provided CONTEXT does not contain a specific
# question") y esa rendición pasaba todos los guardianes → 4 de 6 ejecuciones
# devolvían basura o nada. Tirar una respuesta buena por su primera palabra es un
# mal negocio: ahora el PREFIJO se RECORTA (`_strip_exam_prefix`) y solo se
# considera degenerado el modo examen "puro" — cuando tras el prefijo solo queda
# un yes/no/true/false o una letra de test (`_EXAM_BARE_RE`).
_EXAM_PREFIX_RE = re.compile(
    r"^[\s*_#>\-]*"                                   # viñetas/negritas markdown
    r"(?:the\s+answer(?:\s+to\s+[^.:]{0,60})?\s+is\b"  # "the answer (to X) is…"
    r"|answer\b)\s*[:\-]?[\s*_]*",                     # "Answer:" / "Answer -"
    re.IGNORECASE,                                     # (\b: no tocar "Answering…")
)
_EXAM_BARE_RE = re.compile(r"^(?:yes|no|true|false|[a-e])\b[.!]?\s*$", re.IGNORECASE)
# Compatibilidad: sigue existiendo un _EXAM_RE que detecta la apertura (lo usan
# scripts de diagnóstico), pero ya NO descarta la respuesta por sí solo.
_EXAM_RE = _EXAM_PREFIX_RE

# RENDICIÓN. Cuando la temperatura sube, el 8B a veces deja de responder y pide
# que le den "la pregunta" o "más detalles" — aunque el CONTEXT tiene 10.000
# caracteres sobre el fármaco y la QUESTION está justo debajo. Es una respuesta
# vacía disfrazada de prosa, y se devolvía al usuario como si fuera la respuesta.
# OJO: no confundir con la abstención LEGÍTIMA ("the evidence does not report
# discontinuation rates"), que el prompt pide y que sí debe pasar. Lo que se caza
# aquí es la rendición TOTAL: pedir la pregunta, pedir más información, o decir
# en una o dos frases que el contexto no sirve, sin nombrar qué falta.
_GIVEUP_RE = re.compile(
    r"(?:does\s+not\s+(?:contain|include|provide)\s+(?:a\s+)?(?:specific\s+)?(?:question|task)"
    r"|please\s+provide\s+(?:more|the\s+necessary|a\s+specific|additional)"
    r"|clarify\s+your\s+expectations"
    r"|(?:context|information)\s+(?:provided\s+)?is\s+insufficient\s+(?:for|to)\s+answer)",
    re.IGNORECASE,
)


def _strip_exam_prefix(text):
    """Quita la apertura tipo examen ("The Answer is:", "Answer -") y devuelve el
    resto. Si tras el prefijo no queda nada, devuelve "" (que `_looks_degenerate`
    descarta por corta). Determinista y conservador: solo toca el arranque."""
    if not text:
        return text
    m = _EXAM_PREFIX_RE.match(text)
    if not m:
        return text
    resto = text[m.end():].strip()
    # Tras "The answer is" puede venir la respuesta en mayúscula inicial ("Dupilumab
    # is effective…") o en minúscula ("a meta-analysis…"): la capitalizamos.
    return (resto[:1].upper() + resto[1:]) if resto else ""
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
    # frases de la directiva de formato del mensaje de usuario (_TASK_DIRECTIVE):
    "three short paragraphs", "one self-contained claim",
    "do not answer in a single sentence", "task: answer the question",
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
    if _EXAM_BARE_RE.match(_strip_exam_prefix(t)):  # "the answer is yes/no/A…" a secas
        return True
    # Rendición total ("please provide the question…"): solo cuenta como
    # degenerada si es TODA la respuesta (≤ 2 frases). En una respuesta larga, una
    # frase de abstención parcial es legítima y se conserva.
    if _GIVEUP_RE.search(t) and _count_sentences(t) <= 2:
        return True
    if _CONTEXT_ECHO_RE.search(t):           # regurgitó el bloque CONTEXT
        return True
    if _CYRILLIC_RE.search(t):               # coló homóglifos cirílicos (texto corrupto)
        return True
    if any(m in low for m in _INSTRUCTION_MARKERS):  # parroteó instrucciones
        return True
    return False


# Mínimo de frases para considerar la respuesta "desarrollada". Pedimos 5-9 en
# la directiva; aceptamos 4 como suficiente (el 8B rara vez pasa de 6) y por
# debajo reintentamos. Es un umbral de CALIDAD, no de seguridad: si no se
# alcanza nunca, se devuelve igualmente la mejor respuesta obtenida.
_MIN_SENTENCES = 4
_SENTENCE_RE = re.compile(r"[.!?](?:\s|$)")

# Muletillas de "meta-discurso": el modelo se refiere al andamiaje del prompt
# ("based on the provided CONTEXT", "according to the documents"...). El system
# ya lo prohíbe, pero se le escapa; lo limpiamos de forma determinista para que
# la respuesta se lea como la escribiría un clínico.
_CONTEXT_TALK_RE = re.compile(
    r",?\s*(?:as\s+)?(?:based\s+on|according\s+to|from|per)\s+the\s+"
    r"(?:provided\s+|given\s+|above\s+)?"
    r"(?:context|documents?|evidence\s+provided|provided\s+evidence)\b,?",
    re.IGNORECASE,
)


def _count_sentences(text):
    """Nº aproximado de frases (para el umbral de 'respuesta desarrollada')."""
    return len([s for s in _SENTENCE_RE.split(text or "") if s.strip()])


def _strip_context_talk(text):
    """Quita las referencias al andamiaje del prompt ('based on the provided
    CONTEXT'), dejando la frase legible."""
    if not text:
        return text
    limpio = _CONTEXT_TALK_RE.sub("", text)
    limpio = re.sub(r"\s{2,}", " ", limpio)      # dobles espacios que deja el borrado
    limpio = re.sub(r"\s+([,.;:])", r"\1", limpio)  # espacio antes de puntuación
    return limpio.strip()


def _build_system_prompt(intent):
    """Núcleo invariante (_ANSWER_SYSTEM) + modificador de énfasis de la intención.

    El núcleo lleva las reglas de seguridad (citar, no alucinar, no salir del
    CONTEXT) y es IGUAL para todos; el modificador solo reordena el foco. Así la
    personalización nunca puede debilitar la integridad de la respuesta.
    """
    modificador = _INTENT_MODIFIERS.get(intent, "")
    texto = f"{_ANSWER_SYSTEM}\n\n{modificador}" if modificador else _ANSWER_SYSTEM
    # Los ejemplos de endpoint ("EASI-75, IGA 0/1…") dependen de la patología: se
    # toman del perfil de dominio activo (config.ENDPOINT_EXAMPLES), no van a fuego.
    return texto.replace("{endpoints}", config.ENDPOINT_EXAMPLES)


def _generate_answer(contexto, question, retries=3, intent=None):
    """Genera la respuesta con reintentos y temperatura ESCALONADA.

    1er intento determinista (temp 0.0) → salida FIEL y REPRODUCIBLE. Ya no nos
    preocupa que a temp 0.0 el modelo no cite (medido: redacta bien pero omite las
    [Doc N]); las citas las pone luego el post-proceso determinista. Si degenera
    (eco/examen/parroteo), reintenta con temperatura al alza para escapar del bucle.
    Devuelve el texto o None si todo falla.

    'intent' enfoca la respuesta (ver detect_intent). Si no se pasa, se detecta
    aquí a partir de la pregunta.
    """
    intent = intent or detect_intent(question)
    system_prompt = _build_system_prompt(intent)
    # Escalada de temperatura CONTENIDA (antes llegaba a 0.7). Ahora los
    # reintentos se disparan también por respuesta corta, no solo por
    # degeneración, así que se usan mucho más a menudo: con 0.7 la respuesta
    # devuelta sería casi siempre la más creativa, y en un sistema
    # anti-alucinación eso es un mal negocio. 0.5 como techo da margen para
    # escapar de un bucle degenerado sin premiar la deriva.
    temps = [0.0, 0.2, 0.35, 0.5]
    user_prompt = _build_user_prompt(contexto, question, intent)
    mejor_corta = None   # mejor respuesta VÁLIDA pero corta, por si ninguna cumple
    for intento in range(retries + 1):
        temp = temps[intento] if intento < len(temps) else temps[-1]
        resp = ollama.chat(
            model=config.LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            # num_predict = tope de tokens que puede GENERAR. Lo fijamos explícito
            # (900 ≈ 3 párrafos holgados) para que una respuesta desarrollada nunca
            # se corte a media frase, que dejaría una afirmación sin su cita.
            options={"temperature": temp, "num_ctx": 8192, "num_predict": 900},
        )
        salida = (resp["message"]["content"] or "").strip()
        # El modelo a veces antepone una etiqueta ("OUTPUT:", "ANSWER:"); la quitamos.
        salida = re.sub(r"^\s*(output|answer)\s*:\s*", "", salida, flags=re.IGNORECASE).strip()
        salida = _strip_context_talk(salida)
        if _looks_degenerate(salida):
            continue
        # Apertura de examen ("The Answer is: …"): se recorta, no se descarta (ver
        # nota junto a _EXAM_PREFIX_RE). Se hace DESPUÉS del guardián para que el
        # modo examen puro (solo "yes/no") siga cayendo en el reintento.
        salida = _strip_exam_prefix(salida)
        # Válida, pero ¿desarrollada? Pedimos 5-9 frases y el 8B tiende a
        # despachar en 1-3. Si se queda corta NO la tiramos: la guardamos y
        # reintentamos con más temperatura, que suele soltarle la lengua. Al
        # final devolvemos la más desarrollada que hayamos conseguido — nunca
        # perdemos una respuesta correcta por no cumplir un mínimo de estilo.
        if _count_sentences(salida) >= _MIN_SENTENCES:
            return salida
        if mejor_corta is None or len(salida) > len(mejor_corta):
            mejor_corta = salida
    return mejor_corta  # None solo si TODAS degeneraron


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
    # Además de los top_k documentos que entran en el contexto, queremos unos
    # cuantos MÁS para la "lectura relacionada" (bibliografía que no se citó pero
    # es afín a la pregunta), así que pedimos margen para ambos.
    res = coleccion.query(
        query_embeddings=query_vec,
        n_results=(top_k + config.RELATED_K) * 3,
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


def _related_documents(fragmentos, top_k=None, n=None):
    """Bibliografía RELACIONADA: los documentos que vinieron justo después de los
    `top_k` que entraron en el contexto, siempre que superen el umbral de evidencia.

    Por qué existe: cuando el Scout importa evidencia nueva —o simplemente cuando el
    corpus es grande— la respuesta cita 5 papers, pero hay más que responden a la
    misma pregunta. Se enseñan como "lectura relacionada", con su enlace, SIN
    pretender que la respuesta se apoye en ellos (no se citan). Es un servicio al
    lector, no una afirmación del sistema, y por eso se separa de las fuentes.
    """
    if top_k is None:
        top_k = config.TOP_K
    if n is None:
        n = config.RELATED_K
    grupos = _group_by_document(fragmentos, top_k=top_k + n)[top_k:]
    salida = []
    for g in grupos:
        if g["similarity"] < config.SIMILARITY_THRESHOLD:
            continue          # por debajo del umbral no lo llamamos "relacionado"
        m = g["metadata"]
        salida.append({
            "source": m.get("source"), "doc_id": m.get("doc_id"),
            "title": m.get("title"), "url": m.get("url"), "drugs": m.get("drugs"),
            "access": m.get("access") or "open",
            "similarity": round(g["similarity"], 3),
            "snippet": _excerpt(g["chunks"][0][1], max_chars=200),
        })
    return salida


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

        # Texto del ARTÍCULO COMPLETO, no solo de los chunks que casaron con la
        # pregunta. Por qué: Chroma devuelve el chunk más parecido, pero un abstract
        # troceado en 3 puede tener el resultado en el chunk 0 y los efectos adversos
        # en el chunk 2. Si al modelo solo le damos el chunk recuperado, responde con
        # un tercio del paper — y de ahí salían respuestas de una sola frase.
        # Es el MISMO artículo que citamos como [Doc N], así que la trazabilidad no
        # cambia. Si el documento no se puede leer entero, caemos a los chunks
        # recuperados (comportamiento anterior).
        texto_completo = _full_doc_text(meta.get("doc_id"))
        trozos = [t for _, t in sorted(doc["chunks"], key=lambda c: c[0])]
        texto_doc = texto_completo or " ".join(trozos)

        # Cifras numéricas (EASI, IGA, eventos adversos...) extraídas VERBATIM del
        # artículo completo, etiquetadas con este [Doc i] → alimentan el gráfico y la
        # tabla de "Key figures". Determinista: no lo genera el LLM.
        outcomes_doc = outcomes.extract_outcomes(texto_doc, i)

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
            # Acceso al texto completo: "open" o "abstract_only" (paper de pago).
            # Default "open" si el índice es viejo y no trae el campo → evita
            # avisos falsos hasta que se reprocese el corpus (processing.run()).
            "access": meta.get("access") or "open",
            "doi": meta.get("doi") or "",
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

def _append_access_notice(answer_text, fuentes):
    """Añade un aviso al final si alguna fuente CITADA es de acceso restringido.

    Transparencia "paper de pago": cuando la respuesta se apoya en un artículo del
    que MIA solo tiene el RESUMEN (texto completo posiblemente de pago), lo decimos
    explícitamente. Solo avisamos de las fuentes realmente CITADAS (evita ruido);
    el resto de fuentes restringidas quedan marcadas en el panel vía su campo
    'access'. No accedemos al PDF de pago: solo señalamos que existe.
    """
    citadas = citations.cited_docs(answer_text, len(fuentes))
    restringidas = [f for f in fuentes
                    if f["n"] in citadas and f.get("access") == "abstract_only"]
    if not restringidas:
        return answer_text
    refs = ", ".join(f"[Doc {f['n']}]" for f in restringidas)
    verbo = "is" if len(restringidas) == 1 else "are"
    aviso = (
        f"\n\n⚠ Access note: {refs} {verbo} restricted access — MIA only has the "
        "ABSTRACT; the full text may be paywalled and contain additional data "
        "(figures, methods). Consult the original source for the full detail."
    )
    return answer_text + aviso


def answer(question, top_k=None, history=None):
    """Pipeline RAG completo: recuperar → construir prompt → preguntar al LLM local.

    Devuelve un dict:
      {
        "answer":  texto de la respuesta del modelo (citas válidas garantizadas),
        "sources": lista de fuentes citadas (para mostrarlas en la UI),
        "related": bibliografía relacionada que NO entró en el contexto (hasta
                   config.RELATED_K documentos por encima del umbral),
        "has_evidence": bool — si la mejor coincidencia supera el umbral,
        "intent": intención detectada de la pregunta (enfoque de la respuesta),
        "condensed_question": la pregunta autónoma que se usó si era un follow-up
                              (o None si la pregunta ya era autónoma) — para
                              mostrar en la UI cómo se interpretó.
      }

    `history` (opcional): turnos previos de la conversación. Si se pasa y la
    pregunta parece un follow-up, se CONDENSA a una pregunta autónoma antes de
    recuperar (ver condense_question). Todo el pipeline posterior (retrieval,
    intención, generación) trabaja ya sobre esa pregunta completa.

    Nota: el OLLAMA_HOST del .env se aplica automáticamente al cargar dotenv.
    """
    load_dotenv()  # respeta OLLAMA_HOST si está definido en .env

    # Conversación dinámica: si viene historial, resolvemos el follow-up a una
    # pregunta autónoma. `pregunta` es la que usa TODO el pipeline; `question` es
    # la original tal cual la escribió el usuario (solo para reportar la interpretación).
    pregunta = condense_question(question, history) if history else question
    condensed = pregunta if pregunta != question else None

    # Intención de la pregunta → enfoca la respuesta (personalización dinámica).
    intent = detect_intent(pregunta)

    fragmentos = retrieve(pregunta, top_k)

    # ¿Tenemos evidencia local suficientemente buena?
    mejor = fragmentos[0]["similarity"] if fragmentos else 0.0
    has_evidence = mejor >= config.SIMILARITY_THRESHOLD

    contexto, fuentes = _build_context(fragmentos)
    relacionados = _related_documents(fragmentos, top_k)

    # RAG ESTRICTO: si la evidencia local es débil, NO dejamos que el modelo
    # responda de memoria (evita alucinaciones tipo "the answer is Paris").
    # Aquí es donde en la FASE 3 entrará el agente Scout a buscar fuera.
    if not has_evidence:
        return {
            "answer": ("I cannot find enough local evidence to answer this "
                       "question. Enable the Scout agent to search PubMed and "
                       "ClinicalTrials.gov for it."),
            "sources": fuentes,
            "related": [],
            "has_evidence": False,
            "intent": intent,
            "condensed_question": condensed,
        }

    # Generación robusta: prompt (núcleo + modificador de intención), guardián
    # anti-degeneración y reintentos (ver _generate_answer / _looks_degenerate).
    # El redactor recibe la pregunta en forma ABIERTA (ver open_phrasing); la
    # recuperación y la intención ya se hicieron con la original.
    salida = _generate_answer(contexto, open_phrasing(pregunta), intent=intent)
    if salida is None:
        # Tras los reintentos seguía degenerando: mensaje claro, NO basura.
        salida = ("I could not produce a reliable answer from the retrieved "
                  "evidence. Try rephrasing it — as a question rather than a "
                  "statement — and ask again.")
    else:
        # El modelo 8B suele AMONTONAR las citas al final; las repartimos a la
        # frase que cada una respalda (post-proceso determinista y conservador).
        salida = citations.redistribute_citations(salida, fuentes)

    # RED DE SEGURIDAD DETERMINISTA: quitamos cualquier cita [Doc N] fuera de
    # rango (una fuente inexistente que el modelo pudiera haber inventado). Se
    # aplica SIEMPRE, pase lo que pase antes → la respuesta final nunca cita una
    # fuente que no existe. Es la garantía de "cita válida siempre".
    salida = citations.strip_invalid_citations(salida, len(fuentes))

    # Aviso de "paper de pago" para las fuentes citadas de acceso restringido.
    salida = _append_access_notice(salida, fuentes)

    return {
        "answer": salida,
        "sources": fuentes,
        "related": relacionados,   # lectura relacionada (no citada), con enlaces
        "has_evidence": has_evidence,
        "intent": intent,
        "condensed_question": condensed,
    }


# --------------------------------------------------------------------------
# Ejecución directa: prueba rápida del RAG
# --------------------------------------------------------------------------

if __name__ == "__main__":
    pregunta = " ".join(sys.argv[1:]) or "What is the efficacy of dupilumab in atopic dermatitis?"
    print("=" * 60)
    print(f"Pregunta: {pregunta}")
    # Intención detectada por el router → confirma qué ENFOQUE aplicó al prompt.
    print(f"Intención detectada: {detect_intent(pregunta)}")
    print("=" * 60)
    resultado = answer(pregunta)
    print("\n--- RESPUESTA ---\n")
    print(resultado["answer"])
    print(f"\n--- FUENTES (evidencia local: {'sí' if resultado['has_evidence'] else 'débil'}) ---")
    for f in resultado["sources"]:
        # Acceso al texto completo: "abierto" o "SOLO-RESUMEN" (posible pago).
        # Usamos texto (no emojis) para que la consola de Windows no lo rompa.
        acc = f.get("access") or "open"
        etiqueta_acc = "SOLO-RESUMEN (posible pago)" if acc == "abstract_only" else "abierto"
        print(f"  [Doc {f['n']}] {f['source']}:{f['doc_id']}  (sim {f['similarity']}, "
              f"{f['n_fragments']} frag, acceso: {etiqueta_acc})  {f['url']}")
    if resultado.get("related"):
        print(chr(10) + "--- LECTURA RELACIONADA (no citada) ---")
        for r in resultado["related"]:
            print(f"  · {r['source']}:{r['doc_id']}  (sim {r['similarity']})  {(r['title'] or '')[:90]}")
