# MIA — Tareas y posibles acciones

Documento vivo. Anoto aquí lo pendiente, lo que estamos haciendo y las ideas
futuras. Se va actualizando a medida que surgen cosas.

Leyenda de estado: ⏳ pendiente · 🔨 en curso · ✅ hecho · 💡 idea a valorar

> **Última revisión: 31-ago-2026.** Puesta al día tras el cambio de ordenador
> (el proyecto se movió de OneDrive a `C:\dev\MIA` y se reconstruyó el entorno).

---

## 🔨 En curso

- **Veredicto por pregunta en la Comparativa** (código escrito, pendiente de rodaje).
  - [✅] `src/triplet_agent.py` — agente catalogador (ancla/positivo/negativo) con
        verificación contra el corpus y **abstención** si no puede verificar.
        Fallback determinista por mecanismo si el LLM falla.
  - [✅] `src/verdict.py` — veredicto en vivo por pregunta (hit@1, on-target,
        AUC por pregunta, triplete en vivo) y desempate lexicográfico.
  - [✅] `evaluate_embeddings_semantics.py` — benchmark agregado de "Capa 1"
        (comprensión semántica por tripletes + AUC + mapas 2D). Ya ejecutado.
  - [✅] UI: banner de veredicto + expander con el benchmark agregado.
  - [✅] `src/compare.py`: `answer_from_backend` reutiliza el pipeline completo
        de `rag` (`_build_context` / `_generate_answer` / aviso de acceso).
  - [ ] **Rodar la página de Comparativa** con preguntas de ejemplo y libres, y
        comprobar que el agente se abstiene cuando toca.

## ⏳ Pendientes

- **Memoria final del TFM.** Las `.docx` de la carpeta padre son la entrega
  *preliminar* (30-jun-2026, solo secciones 1-3) y van muy por detrás: no
  mencionan MedCPT, ni la evaluación de embeddings, ni MIA 1.0. Hay que
  reescribirlas con todo lo medido después. Borrador vivo en
  `docs/memoria_tfm.md`.
- **Explicar la fidelidad de cita (3.4/5).** Es la métrica más baja del
  biomédico en la Fase 4. Es defendible —por eso las citas las coloca
  `src/citations.py` de forma determinista, no el LLM— pero hay que decirlo
  explícitamente en la memoria antes de que lo pregunten en la defensa.
- **Sin tests ni lint.** La "prueba" de cada módulo es su bloque
  `if __name__ == "__main__"`. Con tres módulos nuevos, es el punto más frágil
  del proyecto. Mínimo viable: un `pytest` que importe cada módulo y ejecute
  las funciones puras (`auc_from_flags`, `_pick_negative`, `citations`,
  `outcomes`) sin tocar Ollama ni Chroma.

## 💡 Ideas a valorar

- **Comparativa de tamaño de chunk (nº óptimo).** Probar CHUNK_SIZE = 500 / 800 / 1000
  y medir con qué valor la fuente correcta sale más arriba en el top-5. Elegir con datos.
- **MedCPT con formato [título, abstract].** Ahora embebemos el texto del chunk tal cual;
  el Article-Encoder de MedCPT rinde algo mejor con el par (título, abstract).
- **Q&A en español** vía traducción de la pregunta antes del RAG (el corpus/LLM son inglés).
- **Umbral de evidencia por LLM-juez** en vez de fijo. El umbral 66.0 está medido
  y funciona, pero es frágil por diseño (ver `CLAUDE.md` §6).
- **Validación humana y a ciegas** de una muestra de la evaluación de recuperación.
  Hoy la relevancia se mide de forma automática ("¿el documento es del fármaco
  correcto?"): objetivo y reproducible, pero no es juicio clínico.

## ✅ Hecho

### Migración a MedCPT (cerrada)
- Capa de embeddings intercambiable (`src/embeddings.py`) + backend en `config.py`
  con colección separada por backend.
- **HALLAZGO clave:** con COSENO no había umbral válido. Medido sobre 337 docs,
  "capital de Francia" puntuaba MÁS ALTO (0,68) que las preguntas relevantes
  (0,63-0,66) → la puerta anti-alucinación no funcionaba.
  **CAUSA:** MedCPT codifica la confianza en la NORMA del vector; normalizar la tira.
- **ARREGLO:** MedCPT a producto escalar (`config.CHROMA_SPACE="ip"`, sin normalizar).
- Reindexado inner-product: **8.920 chunks, 768 dim**, colección `mia_evidence_medcpt`.
- Umbral fijado en **66.0** sobre la base completa (relevantes 70-73, ajenas 56-64).
- Verificado end-to-end y **UI reescalada** a "% de confianza" legible
  (55→0 %, 80→100 %), en `streamlit_app.py` y `compare.py`.

### Evaluación (la tesis del capstone)
- **Capa 2 — recuperación** (`evaluate_embeddings.py`): MedCPT vs OpenAI con
  golden set Básico/Difícil → precision@5, hit@1, MRR. MedCPT gana en ranking
  (hit@1 0.875 vs 0.625 en difíciles).
- **Capa 1 — comprensión semántica** (`evaluate_embeddings_semantics.py`):
  tripletes + AUC sobre MedCPT / OpenAI / bge. MedCPT gana en
  Mecanismo→fármaco (0.833 vs 0.667).
- **Fase 4 — redacción** (`src/evaluation.py`): biomédico 4.16 vs generalista
  4.15 con juez neutral.

### MIA 1.0 "de demo a producto"
- `src/report.py` — informe de evidencia HTML autónomo y exportable.
- `src/status.py` — panel de estado (Ollama, modelos, corpus) y avisos accionables.
- `run.ps1` / `run.bat` — lanzador de un clic.

### Mantenimiento
- Script `ver_db.py` para inspeccionar ChromaDB (censo, ejemplos, búsqueda real).
- `export_corpus_manifest.py` — censo reproducible del corpus indexado
  (ver "Reproducibilidad del corpus" en `CLAUDE.md`).
