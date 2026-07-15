# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es MIA

Proyecto **Capstone educativo** (curso "Desarrollo IA 10X"): un motor RAG biomédico **100% local y
soberano** sobre **dermatitis atópica** y sus fármacos competidores. Responde preguntas citando la
fuente exacta de cada dato y, si no tiene evidencia local, un agente sale a buscarla a PubMed/
ClinicalTrials.gov. Es un proyecto de aprendizaje: el código lleva **comentarios didácticos en español**
y se construye fase a fase.

## Entorno y comandos

- Windows, Python 3.12 en `.venv`. **Ejecutar siempre con** `./.venv/Scripts/python.exe` (no `python`).
- No es un repositorio git. No hay framework de tests ni de lint configurado.
- Requiere **Ollama** corriendo en `localhost:11434` con estos modelos descargados (`ollama pull`):
  - `koesn/llama3-openbiollm-8b:q4_K_M` — LLM biomédico (`config.LLM_MODEL`)
  - `llama3:8b` — generalista para la comparativa (`config.LLM_GENERALIST`)
  - `qwen2.5:7b` — juez neutral de la evaluación (`config.LLM_JUDGE`)
- El modelo de embeddings (`BAAI/bge-small-en-v1.5`) se descarga solo vía `sentence-transformers`.

```bash
./.venv/Scripts/python.exe check_setup.py                 # comprobar entorno
./.venv/Scripts/python.exe run_phase1.py [--max N]        # Fase 1: ingesta + procesado + indexado
./.venv/Scripts/python.exe src/rag.py "<pregunta EN INGLÉS>"      # Fase 2: RAG con citas
./.venv/Scripts/python.exe src/scout.py "<pregunta EN INGLÉS>"    # Fase 3: RAG + Scout fallback
./.venv/Scripts/python.exe src/evaluation.py [--all]      # Fase 4: comparativa (3 preguntas, o 15 con --all)
```

Cada módulo de `src/` tiene un bloque `if __name__ == "__main__"` para probarlo de forma aislada.

## Arquitectura (el "big picture")

**Flujo de datos tipo medallón** (todo bajo `data/`, regenerable, está en `.gitignore`):
`bronze` (crudo de las APIs) → `silver` (texto limpio y troceado, `chunks.json`) → `chroma`
(base vectorial ChromaDB, colección `mia_evidence`).

**`config.py` es la única fuente de verdad**: enfermedad, fármacos, rutas, modelos, umbrales y endpoints.
El resto del código no debe tener valores "a fuego"; cambiar de patología = cambiar solo este archivo.

**Tubería por fases** (cada `src/*.py` = una fase, reutilizando los anteriores):
- `ingestion.py` (Fase 1): descarga a `bronze`. `fetch_*` por (enfermedad+fármaco) precargan el corpus;
  `search_*` por texto libre las usa el Scout. Helpers compartidos: sesión `requests`, backoff en 429/5xx,
  `_load_api_key` (lee `NCBI_API_KEY` del `.env`, opcional), `sleep(0.34)` por rate limit de PubMed.
- `processing.py` (Fase 1): `clean_and_chunk` normaliza CT(JSON) + PubMed(XML) a un documento uniforme,
  deduplica por `doc_id`, trocea con metadatos; `embed_chunks` (bge-small); `index_in_chroma` (upsert).
  `index_new_bronze` indexa solo archivos nuevos (lo usa el Scout).
- `rag.py` (Fase 2): `retrieve` (embebe pregunta + query coseno) → `answer` (contexto numerado `[Doc N]`
  → OpenBioLLM). **RAG estricto**: si la mejor similitud < `SIMILARITY_THRESHOLD`, NO llama al LLM y
  devuelve "no hay evidencia" (gancho del Scout).
- `scout.py` (Fase 3): `needs_fallback` → `run_scout` (extrae entidad, busca, importa, indexa) →
  reintenta. `answer_with_scout` orquesta rag→scout→reintento; **importa `rag` en caliente** dentro de la
  función para evitar import circular (rag NO importa scout).
- `evaluation.py` (Fase 4): mismo contexto RAG a OpenBioLLM y al generalista (comparación justa), juez
  neutral puntúa la rúbrica en JSON; resultados a `data/evaluation_*.csv`.
- `app/streamlit_app.py` (Fase 5): interfaz de chat (pendiente).

## Gotchas críticos (no obvios, aprendidos a base de fallos)

- **Los LLM solo son fiables EN INGLÉS.** OpenBioLLM degenera con prompts o preguntas en español
  (repite el prompt, responde "the answer is A" en modo examen). Todos los prompts y el `SYSTEM_PROMPT`
  van en inglés; hay que **preguntar en inglés**. El corpus también es inglés.
- **Ollama trunca el contexto a 2048 tokens por defecto.** Pasar siempre `options={"num_ctx": 8192,
  "temperature": 0.0}` en las llamadas `ollama.chat`.
- **El umbral de similitud fijo es frágil**: cualquier pregunta de la enfermedad puntúa ~0.86 aunque
  falte el fármaco concreto. Por eso `needs_fallback` añade una segunda señal: si la pregunta nombra un
  fármaco (sufijos `-mab`/`-nib`/`-ib`) que no aparece en lo recuperado, dispara el Scout.
- **Metadatos de ChromaDB deben ser ESCALARES** (str/int/float/bool, nunca `None` ni listas): por eso
  `authors` y `drugs` se serializan como string con `"; "`.
- **Idempotencia**: ids deterministas (`<doc_id>::chunk<i>`) + `upsert` → re-ejecutar no duplica.
- **Importes**: cada `src/*.py` usa un `try: from .. import config / from . import ...` con `except`
  que añade la raíz a `sys.path`, para funcionar tanto importado como paquete como ejecutado directo.
- **Windows**: forzar `sys.stdout.reconfigure(encoding="utf-8")` y leer/escribir archivos con
  `encoding="utf-8"` (la consola usa cp1252 por defecto).
