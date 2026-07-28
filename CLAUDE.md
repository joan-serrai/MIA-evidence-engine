# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Idioma / público.** Este es un proyecto **educativo** de alguien que está aprendiendo a
> programar. Explica desde cero y **en español**. El código lleva a propósito **comentarios
> didácticos en español** y se construye fase a fase — mantén ese estilo al editar.

---

## 1. Qué es MIA

Proyecto **Capstone** del curso *Desarrollo IA 10X*: un motor **RAG biomédico 100% local y
soberano** sobre la **dermatitis atópica** y sus fármacos competidores. Responde preguntas
citando **la fuente exacta** de cada dato y, si no tiene evidencia local suficiente, un
**agente Scout** sale a buscarla a PubMed / ClinicalTrials.gov, la importa y reintenta.

La **tesis científica** del proyecto: para recuperar evidencia médica, un embedding
**biomédico y local** (**MedCPT**, de NCBI) rankea mejor la evidencia correcta que el
generalista de la "competencia" (**text-embedding-3-small** de OpenAI, vía API) — y encima
**sin enviar datos fuera del ordenador**. La Fase 4 lo mide con datos (ver `README.md`).

Dos "marcas" recurrentes en el código y la UI:
- **MIA** = el producto: MedCPT, biomédico, 100% local.
- **Centivence** = la línea generalista de contraste: OpenAI, vía API. **Solo** existe para
  la evaluación comparativa; **rompe el "100% local"**, así que **nunca** es el backend de producto.

---

## 2. Entorno y comandos

- **Windows · Python 3.12** en `.venv`. **Ejecuta SIEMPRE con** `./.venv/Scripts/python.exe`
  (no `python` a secas — el sistema puede tener otro Python en el PATH).
- **No es un repositorio git.** No hay framework de **tests** ni de **lint** configurado. La
  "prueba" de cada módulo es su bloque `if __name__ == "__main__"` (ver §5).
- Requiere **[Ollama](https://ollama.com)** corriendo en `localhost:11434` con estos modelos
  descargados (`ollama pull <tag>`):
  - `koesn/llama3-openbiollm-8b:q4_K_M` — LLM biomédico (`config.LLM_MODEL`)
  - `llama3:8b` — generalista para la comparativa de la Fase 4 (`config.LLM_GENERALIST`)
  - `qwen2.5:7b` — juez neutral de la evaluación (`config.LLM_JUDGE`)
- Los **embeddings** se descargan solos la primera vez (HuggingFace / `sentence-transformers`):
  MedCPT (`ncbi/MedCPT-*`, ~torch+transformers) y bge (`BAAI/bge-small-en-v1.5`). OpenAI usa API
  (clave `OPENAI_API_KEY` en `.env`).

### Comandos habituales

```bash
# Comprobar entorno (Python, carpetas, paquetes). No descarga nada.
./.venv/Scripts/python.exe check_setup.py

# FASE 1 — ingesta + procesado + indexado (deja la base vectorial lista)
./.venv/Scripts/python.exe run_phase1.py [--max 50]

# FASE 2 — RAG con citas (PREGUNTA EN INGLÉS)
./.venv/Scripts/python.exe src/rag.py "What is the efficacy of dupilumab in atopic dermatitis?"

# FASE 3 — RAG + Scout (fallback a PubMed/ClinicalTrials si falta evidencia)
./.venv/Scripts/python.exe src/scout.py "What is the efficacy of delgocitinib in atopic dermatitis?"

# FASE 4 — comparativa de LLM (biomédico vs generalista, juez neutral): 3 preguntas, o 15 con --all
./.venv/Scripts/python.exe src/evaluation.py [--all]

# FASE 5 — interfaz de chat (Streamlit)
./.venv/Scripts/python.exe -m streamlit run app/streamlit_app.py
```

> La app tiene una config de arranque en `.claude/launch.json` (nombre `mia`, puerto **8524**).
> Con la herramienta de preview de Claude Code arranca con `preview_start {name:"mia"}`.

### Scripts de la EVALUACIÓN DE EMBEDDINGS (la tesis del capstone)

```bash
# 1) Crear la colección OpenAI re-embediendo los MISMOS chunks que MedCPT (necesita OPENAI_API_KEY)
./.venv/Scripts/python.exe index_openai.py

# 2) Medir recuperación MedCPT vs OpenAI (precision@k, hit@1, MRR) → data/evaluation_embeddings.csv
./.venv/Scripts/python.exe evaluate_embeddings.py
```

### Utilidades / mantenimiento

```bash
./.venv/Scripts/python.exe ver_db.py                                   # censo de ChromaDB + 3 ejemplos
./.venv/Scripts/python.exe ver_db.py --buscar "dupilumab efficacy"     # búsqueda real (ver el RAG recuperar)
./.venv/Scripts/python.exe ingest_desktop_set.py --file "<ruta.txt>"   # ampliar corpus con un export PubMed "Abstract (text)"
./.venv/Scripts/python.exe fix_source_titles.py --dry | --apply        # reparar títulos sucios ya indexados
./.venv/Scripts/python.exe retitle_desktop_set.py --file "<ruta.txt>"  # re-titular el desktop set sin re-embeber
```

---

## 3. Arquitectura — el "big picture"

### 3.1 `config.py` es la ÚNICA fuente de verdad
Enfermedad, fármacos, rutas, modelos, umbrales y endpoints viven **solo** ahí. El resto del
código **no debe tener valores "a fuego"**: cambiar de patología = cambiar solo `config.py`.
Muchos parámetros son **condicionales al backend de embeddings activo** (colección, métrica de
distancia, umbral) — ver §4.

### 3.2 Flujo de datos tipo "medallón"
Todo bajo `data/` (regenerable, en `.gitignore`):

```
bronze  →  silver  →  chroma
 crudo     limpio +    base vectorial ChromaDB
 (API)     troceado    (una colección POR backend de embeddings)
           chunks.json
```

### 3.3 Tubería por fases (cada `src/*.py` reutiliza los anteriores)

| Módulo | Fase | Rol |
|--------|------|-----|
| `src/ingestion.py`   | 1 | Descarga a `bronze`. `fetch_*` por (enfermedad+fármaco) precargan el corpus; `search_*` por texto libre las usa el Scout. Sesión `requests` con backoff en 429/5xx, `sleep(0.34)` por rate-limit de PubMed, `NCBI_API_KEY` opcional. |
| `src/embeddings.py`  | 1 | **Capa de embeddings intercambiable** (bge ↔ MedCPT ↔ OpenAI). Interfaz única `embed_documents()` / `embed_query()`. Ver §4. |
| `src/processing.py`  | 1 | `clean_and_chunk` normaliza CT(JSON)+PubMed(XML) a documento uniforme, dedup por `doc_id`, trocea **respetando frases**; `embed_chunks`; `index_in_chroma` (upsert por lotes). `index_new_bronze` indexa solo lo nuevo (lo usa el Scout). |
| `src/rag.py`         | 2 | **El corazón.** `retrieve` (embebe pregunta + query a Chroma) → `_build_context` (agrupa chunks por documento en `[Doc N]`, presupuesto de chars, extrae outcomes) → `_generate_answer` (OpenBioLLM, prompt "analista", guardián anti-degeneración + reintentos). **RAG estricto**: si la mejor similitud < umbral, NO llama al LLM. |
| `src/citations.py`   | 2 | Post-proceso **determinista** de citas. Reparte cada `[Doc N]` a la frase que respalda (por solapamiento de términos); valida y **elimina citas fuera de rango** → *"cada cita apunta a una fuente real, siempre"*. |
| `src/outcomes.py`    | 2 | Extrae **cifras verbatim** (EASI 75/90/100, IGA 0/1…) del texto con regex deterministas, etiquetadas con su `[Doc N]` → alimentan el gráfico de la UI **sin que el LLM invente números**. |
| `src/scout.py`       | 3 | Agente de fallback. `needs_fallback` (2 señales, ver §6) → `run_scout` (extrae entidad con LLM + fallback por keywords, busca, importa, indexa) → reintenta. `answer_with_scout` orquesta. **Importa `rag` en caliente** (evita import circular; `rag` NO importa `scout`). |
| `src/evaluation.py`  | 4 | Mismo contexto RAG a OpenBioLLM y al generalista (comparación justa) → **juez neutral** puntúa una rúbrica 1-5 en JSON → CSV/JSON en `data/`. |
| `src/compare.py`     | — | Recuperación comparativa MedCPT vs OpenAI (solo retrieval, **sin LLM**) para la página de comparación de la UI. |
| `app/streamlit_app.py` | 5 | Chat con citas resaltadas, tarjetas de fuente, KPIs, gráfico de outcomes y panel del Scout. CSS propio inline (sin llamadas a red). |
| `app/pages/1_Comparativa_MIA_vs_Centivence.py` | 5 | Página lado a lado: qué recupera MedCPT (MIA) vs OpenAI (Centivence) sobre la misma pregunta. |

### 3.4 Scripts raíz (fuera de `src/`, orquestan o mantienen)
- `run_phase1.py` — orquesta ingesta→procesado→indexado.
- `check_setup.py` — diagnóstico de entorno (no instala nada).
- `ver_db.py` — inspecciona ChromaDB (censo, ejemplos, búsqueda real).
- `index_openai.py` — crea la colección OpenAI re-embediendo los chunks de la de MedCPT (mismos textos).
- `evaluate_embeddings.py` — mide precision@k / hit@1 / MRR de MedCPT vs OpenAI (golden set con niveles Básico/Difícil).
- `ingest_desktop_set.py` — amplía el corpus con un export "Abstract (text)" de PubMed, reutilizando la tubería.
- `fix_source_titles.py` / `retitle_desktop_set.py` — reparación puntual de títulos sucios ya indexados (mantenimiento).

---

## 4. La capa de embeddings (lo más sutil del proyecto)

`src/embeddings.py` expone `embed_documents()` (indexar) y `embed_query()` (recuperar). El
backend activo lo elige **`config.EMBEDDING_BACKEND`**. Hay **tres**, y **no son intercambiables sin más**:

| Backend | Modelo | Tipo | Dim | Métrica nativa | ¿Normaliza? |
|---------|--------|------|-----|----------------|-------------|
| `medcpt` (**producto MIA**) | `ncbi/MedCPT-{Query,Article}-Encoder` | **Asimétrico / 2 torres** (encoder distinto para pregunta y para artículo) | 768 | **producto escalar (`ip`)** | **NO** |
| `sentence-transformers` | `BAAI/bge-small-en-v1.5` | Simétrico | 384 | coseno | Sí |
| `openai` (**solo evaluación**) | `text-embedding-3-small` | Simétrico, vía API | 1536 | coseno | Sí (la API los da normalizados) |

**Consecuencias de diseño que hay que respetar SIEMPRE:**
- **MedCPT NO se normaliza.** Codifica su "confianza" en la **magnitud** del vector; normalizarlo
  rompería la puerta de evidencia (una pregunta ajena podría colarse arriba). Por eso su
  colección usa espacio `ip` (producto escalar), no coseno.
- **Una colección de ChromaDB POR backend** (vectores de 384/768/1536 dim no conviven). Los
  nombres se derivan en `config.py`: `mia_evidence` (histórico, bge), `mia_evidence_medcpt`,
  `mia_evidence_openai`. Así se mantienen los tres índices a la vez y se cambia de backend sin
  destruir nada.
- **El umbral de evidencia depende de la ESCALA del backend** (`config.SIMILARITY_THRESHOLD`):
  MedCPT `66.0` (dot, ~55–75), bge `0.70` (coseno 0–1), OpenAI `0.35` (coseno, sin calibrar fino).
  La UI recalibra el dot de MedCPT a un "% de confianza" legible (55→0%, 80→100%; el umbral 66 ≈ 44%).
- **Cambiar de métrica o de modelo exige RE-INDEXAR** la colección de ese backend.

---

## 5. Cómo probar cada pieza aislada

No hay pytest. Cada módulo trae un `if __name__ == "__main__"` que lo ejecuta solo (con un caso
por defecto y, muchos, aceptando la pregunta por `sys.argv`). Ejemplos:

```bash
./.venv/Scripts/python.exe src/embeddings.py                       # dim + similitud pregunta↔doc
./.venv/Scripts/python.exe src/outcomes.py                         # extracción de cifras de un abstract de ejemplo
./.venv/Scripts/python.exe src/citations.py                        # reparto de citas sobre un caso "duro"
./.venv/Scripts/python.exe src/compare.py "antibody targeting IL-4 receptor alpha for eczema"
```

Para depurar el pipeline completo, `src/rag.py` y `src/scout.py` imprimen respuesta + fuentes.

---

## 6. Gotchas críticos (no obvios, aprendidos a base de fallos)

- **Los LLM solo son fiables EN INGLÉS.** OpenBioLLM degenera con prompts o preguntas en español
  (repite el prompt, responde "the answer is A" en modo examen). Todos los prompts y el corpus
  están en inglés → **hay que preguntar en inglés**. Q&A en español (traduciendo antes) es mejora futura.
- **Ollama trunca el contexto a 2048 tokens por defecto.** Pasa SIEMPRE
  `options={"num_ctx": 8192, "temperature": 0.0}` en las llamadas `ollama.chat`.
- **OpenBioLLM 8B cita mal.** No confíes en que ponga las `[Doc N]`: a temperatura 0 redacta bien
  pero suele omitirlas o amontonarlas. Las citas las coloca **después**, de forma determinista,
  `src/citations.py`. `_looks_degenerate` (en `rag.py`) descarta salidas basura (eco del contexto,
  modo examen, homóglifos cirílicos, parroteo del prompt) y **reintenta con temperatura al alza**.
- **El umbral de similitud fijo es frágil**: cualquier pregunta de la enfermedad puntúa alto aunque
  falte el fármaco concreto. Por eso `scout.needs_fallback` usa **dos señales**: (A) similitud bajo
  umbral, y (B) la pregunta nombra un fármaco (sufijos `-mab`/`-nib`/`-ib`) que **no** aparece en lo
  recuperado. Cualquiera de las dos dispara el Scout.
- **Números = NUNCA del LLM.** Las cifras del gráfico se extraen con regex del texto recuperado
  (`outcomes.py`), filtrando intervalos de confianza y heterogeneidad de meta-análisis para no
  inventar tasas. Principio anti-alucinación: cada barra es rastreable a su `[Doc N]`.
- **Metadatos de ChromaDB deben ser ESCALARES** (str/int/float/bool; nunca `None` ni listas): por
  eso `authors` y `drugs` se serializan como string con `"; "`.
- **Idempotencia**: ids deterministas (`<doc_id>::chunk<i>`) + `upsert` → re-ejecutar no duplica.
- **Importes duales**: cada `src/*.py` usa `try: from .. import config / from . import ...` con un
  `except` que añade la raíz a `sys.path` → funciona tanto importado como paquete como ejecutado directo.
- **Windows / codificación**: fuerza `sys.stdout.reconfigure(encoding="utf-8")` al inicio y lee/escribe
  archivos con `encoding="utf-8"` (la consola usa cp1252 por defecto y rompe emojis/acentos). Mantén
  este patrón en cualquier script nuevo.
- **Acceso "paper de pago"**: PubMed da el abstract gratis, pero el texto completo puede ser de pago.
  Señal fiable y gratuita: si hay id de **PMC** → `access="open"`; si solo DOI → `access="abstract_only"`
  y MIA lo avisa. **No se hace scraping del PDF**: solo se señala que existe.

---

## 7. Estado y hoja de ruta

Las **cinco fases están implementadas y probadas** de punta a punta, y la migración a **MedCPT**
está completa (inner-product, umbral 66.0, 8.920 chunks / 768 dim). La evaluación de embeddings
(MedCPT vs OpenAI) valida la elección del modelo biomédico local.

- **`TASKS.md`** es el documento vivo de tareas (pendientes / en curso / ideas). Consúltalo y
  actualízalo cuando cierres o abras trabajo.
- **`README.md`** trae la explicación divulgativa, el diagrama y la **tabla de resultados** de la
  comparativa de embeddings (precision@5, hit@1, MRR por nivel Básico/Difícil).
