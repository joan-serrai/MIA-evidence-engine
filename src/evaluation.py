"""
src/evaluation.py — FASE 4: Validación.  [Módulo 12: Ciclo de vida / evaluación]

La parte "científica" del proyecto: demostrar con datos si el modelo biomédico
local iguala (o no) a un LLM generalista, dándole a AMBOS el mismo contexto
recuperado (comparación justa: solo cambia el modelo, no la información).

Plan:
  1. Un "golden set" de preguntas sobre dermatitis atópica.
  2. Generar la respuesta de cada modelo con el MISMO contexto RAG.
  3. Puntuar con una rúbrica (1-5) usando un LLM-JUEZ NEUTRAL (un 3er modelo,
     para que nadie se juzgue a sí mismo).
  4. Resumir resultados en una tabla.

Modelos (config.py):
  - LLM_MODEL       = OpenBioLLM (biomédico local)
  - LLM_GENERALIST  = llama3:8b   (misma base = comparación justa)
  - LLM_JUDGE       = qwen2.5:7b  (juez neutral)
"""

import sys
import json

import ollama
import pandas as pd
from dotenv import load_dotenv

try:
    from .. import config
    from . import rag
except (ImportError, ValueError):
    sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    import config
    from src import rag

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# Dimensiones de la rúbrica. TODAS "más alto = mejor" (1-5) para poder promediar.
RUBRIC = ["fidelidad_cita", "correccion_clinica", "manejo_jerga",
          "alucinacion", "tono"]

_RUBRIC_DEFS = {
    "fidelidad_cita":    "cita correctamente sus fuentes como [Doc N] y esas citas apoyan lo dicho",
    "correccion_clinica": "la información biomédica es correcta y precisa",
    "manejo_jerga":      "usa y explica bien la terminología médica",
    "alucinacion":       "NO inventa; todo se apoya en el contexto (5 = nada inventado, 1 = inventa mucho)",
    "tono":              "tono profesional, claro y apropiado para un asistente clínico",
}

# Preguntas en INGLÉS (los modelos son fiables en inglés; ver nota en rag.py).
GOLDEN_QUESTIONS = [
    "What is the efficacy of dupilumab in adults with moderate-to-severe atopic dermatitis?",
    "What are the most common adverse events associated with dupilumab?",
    "How effective is upadacitinib for atopic dermatitis compared with placebo?",
    "What safety concerns are associated with JAK inhibitors in atopic dermatitis?",
    "What is the mechanism of action of tralokinumab in atopic dermatitis?",
    "How does abrocitinib affect itch (pruritus) in patients with atopic dermatitis?",
    "What is known about the efficacy of lebrikizumab in atopic dermatitis?",
    "What is the role of nemolizumab in treating atopic dermatitis and pruritus?",
    "What dosing regimens have been studied for baricitinib in atopic dermatitis?",
    "Is dupilumab effective in children and adolescents with atopic dermatitis?",
    "What are the long-term safety findings for upadacitinib in atopic dermatitis?",
    "How do biologics compare with JAK inhibitors for atopic dermatitis?",
    "What ocular adverse events are associated with dupilumab?",
    "What is the efficacy of tralokinumab monotherapy versus placebo?",
    "What patient-reported outcomes improve with treatment in atopic dermatitis trials?",
]


# --------------------------------------------------------------------------
# Generación: ambos modelos, MISMO contexto
# --------------------------------------------------------------------------

def _generate(model, context, question):
    """Pide una respuesta a 'model' con el MISMO system prompt y contexto que el RAG."""
    user_prompt = f"CONTEXT:\n{context}\n\nQUESTION: {question}"
    r = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": rag.SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        options={"temperature": 0.0, "num_ctx": 8192},
    )
    return r["message"]["content"].strip()


# --------------------------------------------------------------------------
# Juez neutral
# --------------------------------------------------------------------------

def _parse_scores(contenido):
    """Extrae las puntuaciones del JSON del juez. Una clave inválida o fuera de
    rango 1-5 se marca como None (NO 0): así distinguimos 'no puntuó' de 'cero',
    y al promediar con pandas esos None (NaN) se ignoran en vez de hundir la media.
    Devuelve (dict de puntuaciones, ¿están todas bien?).
    """
    try:
        datos = json.loads(contenido)
    except (json.JSONDecodeError, TypeError):
        ini, fin = contenido.find("{"), contenido.rfind("}")
        try:
            datos = json.loads(contenido[ini:fin + 1]) if ini >= 0 else {}
        except json.JSONDecodeError:
            datos = {}

    puntuaciones, completo = {}, True
    for k in RUBRIC:
        try:
            v = int(datos.get(k))
        except (TypeError, ValueError):
            v = None
        if v is None or not (1 <= v <= 5):
            v, completo = None, False
        puntuaciones[k] = v
    return puntuaciones, completo


def _judge(question, context, answer, retries=1):
    """El juez neutral puntúa la respuesta (1-5 por dimensión).

    Reintenta si el JSON viene incompleto/mal formado. Devuelve
    (puntuaciones, contenido_crudo_del_juez) para poder auditar después.
    """
    rubrica = "\n".join(f"- {k}: {v}" for k, v in _RUBRIC_DEFS.items())
    prompt = (
        "You are a strict, neutral evaluator of biomedical answers. "
        "Score the ANSWER from 1 (poor) to 5 (excellent) on each dimension. "
        "Judge ONLY against the provided CONTEXT.\n\n"
        f"DIMENSIONS:\n{rubrica}\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"QUESTION: {question}\n\n"
        f"ANSWER:\n{answer}\n\n"
        "Reply ONLY with a JSON object whose keys are EXACTLY these five: "
        f"{', '.join(RUBRIC)}. Every value MUST be an integer from 1 to 5 "
        "(never 0, never null)."
    )
    contenido = ""
    for _ in range(retries + 1):
        r = ollama.chat(
            model=config.LLM_JUDGE,
            messages=[{"role": "user", "content": prompt}],
            format="json",  # fuerza salida JSON parseable
            options={"temperature": 0.0, "num_ctx": 8192},
        )
        contenido = r["message"]["content"]
        puntuaciones, completo = _parse_scores(contenido)
        if completo:
            break  # todas las dimensiones válidas → listo
    return puntuaciones, contenido


# --------------------------------------------------------------------------
# Comparativa completa
# --------------------------------------------------------------------------

def run_comparison(golden_questions=None, max_questions=None, verbose=True):
    """Ejecuta la comparativa biomédico-local vs generalista y devuelve resultados.

    Devuelve un dict: {"detalle": DataFrame por pregunta/modelo, "resumen": DataFrame
    de medias por modelo}. También guarda ambos en data/.
    """
    load_dotenv()  # respeta OLLAMA_HOST
    preguntas = golden_questions or GOLDEN_QUESTIONS
    if max_questions:
        preguntas = preguntas[:max_questions]

    modelos = {"biomedico": config.LLM_MODEL, "generalista": config.LLM_GENERALIST}
    filas = []
    crudos = []  # respuestas y veredictos literales, para auditar

    for i, q in enumerate(preguntas, start=1):
        if verbose:
            print(f"[{i}/{len(preguntas)}] {q}")
        # Recuperamos el contexto UNA vez → ambos modelos reciben lo mismo.
        fragmentos = rag.retrieve(q)
        contexto, _ = rag._build_context(fragmentos)

        for etiqueta, modelo in modelos.items():
            respuesta = _generate(modelo, contexto, q)
            notas, juez_crudo = _judge(q, contexto, respuesta)
            # Media ignorando dimensiones sin puntuar (None).
            validas = [v for v in notas.values() if v is not None]
            media = round(sum(validas) / len(validas), 2) if validas else None
            filas.append({"pregunta": q, "modelo": etiqueta, **notas, "media": media})
            crudos.append({"pregunta": q, "modelo": etiqueta, "modelo_id": modelo,
                           "respuesta": respuesta, "juez_crudo": juez_crudo, "notas": notas})
            if verbose:
                print(f"    {etiqueta:12s} media={media}  {notas}")

    detalle = pd.DataFrame(filas)
    # mean() de pandas ignora NaN automáticamente (los None de dimensiones sin puntuar).
    resumen = detalle.groupby("modelo")[RUBRIC + ["media"]].mean().round(2)

    # Guardamos resultados: CSV (tablas) + JSON (respuestas y veredictos crudos).
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    detalle.to_csv(config.DATA_DIR / "evaluation_detail.csv", index=False, encoding="utf-8")
    resumen.to_csv(config.DATA_DIR / "evaluation_summary.csv", encoding="utf-8")
    (config.DATA_DIR / "evaluation_raw.json").write_text(
        json.dumps(crudos, ensure_ascii=False, indent=2), encoding="utf-8")

    if verbose:
        print("\n" + "=" * 60)
        print(" RESUMEN — medias por modelo (1-5, más alto = mejor)")
        print("=" * 60)
        print(resumen.to_string())
        print(f"\nGuardado en: {config.DATA_DIR}\\evaluation_detail.csv y evaluation_summary.csv")

    return {"detalle": detalle, "resumen": resumen}


if __name__ == "__main__":
    # Por defecto, comparativa rápida con 3 preguntas. Usa '--all' para las 15.
    n = None if "--all" in sys.argv else 3
    run_comparison(max_questions=n)
