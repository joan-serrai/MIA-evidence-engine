"""
src/scout.py — FASE 3: Agente Scout (fallback).  [Módulo 9: Agentes]

Rompe el "muro de información": si MIA no tiene evidencia local suficiente
para responder, este agente:
  1. Detecta el vacío (umbral de similitud bajo).
  2. Extrae las entidades de la pregunta (fármaco/tema) usando el LLM.
  3. Consulta en caliente las APIs oficiales (ClinicalTrials.gov / PubMed).
  4. Importa e indexa esos resultados en la base local (Chroma).
  5. Reintenta la respuesta, ahora SÍ con evidencia.

Reutiliza src/ingestion.py (búsqueda libre) y src/processing.py (indexado
incremental). NO importa src/rag.py a nivel de módulo para evitar un import
circular: lo importa "en caliente" dentro de answer_with_scout().
"""

import sys
import re

import ollama
from dotenv import load_dotenv

try:
    from .. import config
    from . import ingestion, processing
except (ImportError, ValueError):
    sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    import config
    from src import ingestion, processing

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# --------------------------------------------------------------------------
# 1) ¿Hace falta salir a buscar fuera?
# --------------------------------------------------------------------------

# Fármacos = palabras con sufijos típicos de anticuerpos (-mab) o de inhibidores
# de quinasa (-nib / -ib). Sirve para detectar si la pregunta nombra un fármaco
# concreto (p. ej. "delgocitinib", "ruxolitinib").
_DRUG_RE = re.compile(r"\b[a-z]{4,}(?:mab|nib|ib)\b")


def _drugs_in_question(question):
    return set(_DRUG_RE.findall(question.lower()))


def needs_fallback(question, retrieved):
    """Decide si hace falta salir a buscar fuera.

    Usamos DOS señales complementarias (el umbral fijo solo es frágil):
      A) Similitud: si no recuperamos nada o lo mejor queda bajo el umbral.
      B) Entidad ausente: si la pregunta nombra un fármaco concreto que NO
         aparece en ningún fragmento recuperado. Esto cubre el caso típico de
         "la enfermedad sí está en el corpus, pero ESE fármaco no" — donde la
         similitud global engaña (puntúa alto por el tema compartido).

    Opción futura (más agéntica): un "LLM-juez" que lea el contexto y decida si
    basta para responder.
    """
    if not retrieved:
        return True
    mejor = retrieved[0].get("similarity", 0.0)
    if mejor < config.SIMILARITY_THRESHOLD:
        return True

    nombrados = _drugs_in_question(question)
    if nombrados:
        texto_recuperado = " ".join(f["text"].lower() for f in retrieved)
        if not any(d in texto_recuperado for d in nombrados):
            return True  # nombra un fármaco que no está en lo recuperado
    return False


# --------------------------------------------------------------------------
# 2) Extracción de entidades (la parte "agéntica")
# --------------------------------------------------------------------------

def _keyword_fallback(question):
    """Si el LLM falla, extrae el fármaco sin LLM. En orden de preferencia:
      1) una palabra con sufijo de fármaco (-mab/-nib/-ib), p. ej. 'delgocitinib';
      2) un fármaco conocido de config.DRUGS;
      3) la pregunta tal cual (las APIs aceptan texto libre).
    """
    candidatos = _DRUG_RE.findall(question.lower())
    if candidatos:
        return candidatos[0]
    q = question.lower()
    for drug in config.DRUGS:
        if drug.lower() in q:
            return drug
    return question.strip().rstrip("?.")


def _extract_search_term(question):
    """Usa el LLM para extraer el fármaco/tratamiento de la pregunta.

    El modelo es inglés-céntrico (ver nota en rag.py), así que el prompt va en
    inglés. Validamos la salida y, ante cualquier rareza, caemos al fallback por
    palabras clave: un agente robusto nunca depende ciegamente del LLM.
    """
    load_dotenv()  # respeta OLLAMA_HOST del .env
    prompt = (
        "Extract the main drug, treatment, or biomedical topic from this "
        "question. Reply with ONLY the term (a few words at most), no "
        "explanation.\n\n"
        f"Question: {question}"
    )
    try:
        r = ollama.chat(
            model=config.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_ctx": 4096},
        )
        term = r["message"]["content"].strip().splitlines()[0]
        term = term.strip().strip('"').strip("'").rstrip(".")
        # Validación: ni vacío ni una "parrafada" (señal de que degeneró).
        if term and len(term.split()) <= 6:
            return term
    except Exception as e:
        print(f"   [aviso] extracción LLM falló: {e}")
    return _keyword_fallback(question)


# --------------------------------------------------------------------------
# 3) Salir a buscar e importar
# --------------------------------------------------------------------------

def run_scout(question, max_results=20):
    """Busca evidencia externa para la pregunta, la importa y la indexa.

    Devuelve un resumen: {term, ct_file, pubmed_file, new_chunks}.
    """
    term = _extract_search_term(question)
    # Acotamos siempre a la enfermedad del proyecto para no traer ruido.
    search = f"{term} AND {config.DISEASE_QUERY}"
    print(f"   [scout] término de búsqueda: '{search}'")

    ct_path = ingestion.search_clinical_trials(search, max_results)
    pm_path = ingestion.search_pubmed(search, max_results)

    nuevos = processing.index_new_bronze([ct_path, pm_path], term)
    print(f"   [scout] chunks nuevos indexados: {nuevos}")

    return {
        "term": term,
        "ct_file": str(ct_path) if ct_path else None,
        "pubmed_file": str(pm_path) if pm_path else None,
        "new_chunks": nuevos,
    }


# --------------------------------------------------------------------------
# 4) Orquestador: responde, y si falta evidencia, activa el Scout y reintenta
# --------------------------------------------------------------------------

def answer_with_scout(question, max_results=20, history=None):
    """Pipeline completo con fallback:

        rag.answer  →  ¿hay evidencia?  →  sí: devolver
                                        →  no: run_scout → rag.answer otra vez

    Devuelve el dict de rag.answer + 'used_scout' y, si lo usó, 'scout'.

    `history` (opcional): turnos previos. Se CONDENSA aquí UNA sola vez (para que
    el disparador del Scout y la búsqueda usen ya la pregunta autónoma) y se pasa
    la pregunta resuelta a rag.answer SIN historial (para no re-condensar).
    """
    # Import "en caliente" para evitar el import circular (rag NO importa scout).
    # Patrón dual como en la cabecera: relativo si se usa como paquete, absoluto
    # si se ejecuta el módulo directamente (`python src/scout.py`).
    try:
        from . import rag
    except (ImportError, ValueError):
        from src import rag

    # Conversación dinámica: resolvemos el follow-up ANTES de decidir el fallback
    # y de buscar fuera → el Scout busca el fármaco correcto, no un pronombre.
    pregunta = rag.condense_question(question, history) if history else question
    condensed = pregunta if pregunta != question else None

    fragmentos = rag.retrieve(pregunta)
    if not needs_fallback(pregunta, fragmentos):
        res = rag.answer(pregunta)          # ya autónoma → sin history
        res["used_scout"] = False
        res["condensed_question"] = condensed
        return res

    print("→ Evidencia local insuficiente; activando el agente Scout...")
    resumen = run_scout(pregunta, max_results)

    res = rag.answer(pregunta)              # reintento con la base ya ampliada
    res["used_scout"] = True
    res["scout"] = resumen
    res["condensed_question"] = condensed
    return res


# --------------------------------------------------------------------------
# Ejecución directa: prueba del Scout end-to-end
# --------------------------------------------------------------------------

if __name__ == "__main__":
    pregunta = " ".join(sys.argv[1:]) or "What is the efficacy of delgocitinib in atopic dermatitis?"
    print("=" * 60)
    print(f"Pregunta: {pregunta}")
    print("=" * 60)
    resultado = answer_with_scout(pregunta)
    print("\n--- RESPUESTA ---\n")
    print(resultado["answer"])
    print(f"\n¿Usó Scout?: {resultado['used_scout']}")
    print("\n--- FUENTES ---")
    for f in resultado["sources"]:
        print(f"  [Doc {f['n']}] {f['source']}:{f['doc_id']}  (sim {f['similarity']}, "
              f"{f['n_fragments']} frag)  {f['url']}")
