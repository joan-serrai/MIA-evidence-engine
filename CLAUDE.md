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
**sin enviar datos fuera del ordenador**. Se mide en DOS capas (ver §7 y `README.md`).

Dos "marcas" recurrentes en el código y la UI:
- **MIA** = el producto: MedCPT, biomédico, 100% local.
- **Centivence** = la línea generalista de contraste: OpenAI, vía API. **Solo** existe para
  la evaluación comparativa; **rompe el "100% local"**, así que **nunca** es el backend de producto.

---

## 2. Entorno y comandos

- **Ubicación del proyecto: `C:\dev\MIA`.** Se movió aquí (31-ago-2026) desde OneDrive: allí
  `data/chroma` y `.venv` quedaban como *placeholders* en la nube y una base vectorial sobre
  una carpeta sincronizada es lenta y propensa a corrupción. **No devuelvas el proyecto a OneDrive.**
- **Windows · Python 3.12** en `.venv`. **Ejecuta SIEMPRE con** `./.venv/Scripts/python.exe`
  (no `python` a secas — el alias de Microsoft Store secuestra ese nombre).
- **Es un repositorio git** (rama `master`, sin remoto). No hay framework de **tests** ni de
  **lint** configurado. La "prueba" de cada módulo es su bloque `if __name__ == "__main__"` (ver §5).
- **Dependencias FIJADAS con `==`** (desde el 31-ago-2026). `requirements.txt` lleva las
  directas con comentarios; `requirements.lock.txt` es el `pip freeze` completo (135 líneas)
  para reproducir el entorno exacto. Antes usaban `>=` y al montar el proyecto en un equipo
  nuevo llegaron saltos de versión MAYOR (transformers 4→5, pandas 2→3, numpy 1→2). Funcionó,
  pero por suerte. **Al subir una dependencia: probar y volver a fijar la versión a mano.**
- Requiere **[Ollama](https://ollama.com)** corriendo en `localhost:11434` con estos modelos
  descargados (`ollama pull <tag>`):
  - `koesn/llama3-openbiollm-8b:q4_K_M` — LLM biomédico (`config.LLM_MODEL`)
  - `llama3:8b` — generalista para la comparativa de la Fase 4 (`config.LLM_GENERALIST`)
  - `qwen2.5:7b` — juez neutral de la evaluación y **agente catalogador** (`config.LLM_JUDGE`)
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

> Doble clic en **`run.bat`** (que llama a `run.ps1`) hace lo mismo comprobando antes el
> entorno y Ollama. La app también tiene config de arranque en `.claude/launch.json`
> (nombre `mia`, puerto **8524**) para `preview_start {name:"mia"}`.

### Scripts de la EVALUACIÓN DE EMBEDDINGS (la tesis del capstone)

```bash
# 1) Crear la colección OpenAI re-embediendo los MISMOS chunks que MedCPT (necesita OPENAI_API_KEY)
./.venv/Scripts/python.exe index_openai.py

# 2) CAPA 2 — recuperación: MedCPT vs OpenAI (precision@k, hit@1, MRR) → data/evaluation_embeddings.csv
./.venv/Scripts/python.exe evaluate_embeddings.py

# 3) CAPA 1 — comprensión semántica: tripletes + AUC sobre MedCPT/OpenAI/bge → data/semantics_*.csv y .png
./.venv/Scripts/python.exe evaluate_embeddings_semantics.py

# 4) POR FUENTE — PubMed vs ClinicalTrials + calibración del umbral → data/evaluation_sources.csv
./.venv/Scripts/python.exe evaluate_sources.py
```

> ⚠️ **Si reindexas la colección de MedCPT, vuelve a ejecutar `index_openai.py`.**
> Las dos colecciones deben tener EXACTAMENTE los mismos chunks o la comparativa
> de la tesis deja de ser un experimento controlado. Ya pasó una vez (1-sep-2026):
> 9.734 vs 8.920 chunks.

### Utilidades / mantenimiento

```bash
./.venv/Scripts/python.exe ver_db.py                                   # censo de ChromaDB + 3 ejemplos
./.venv/Scripts/python.exe ver_db.py --buscar "dupilumab efficacy"     # búsqueda real (ver el RAG recuperar)
./.venv/Scripts/python.exe export_corpus_manifest.py                   # censo reproducible del corpus (ver §8)
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
| `src/processing.py`  | 1 | `clean_and_chunk` normaliza CT(JSON)+PubMed(XML) a documento uniforme, dedup por `doc_id`, trocea **respetando frases**; `embed_chunks`; `index_in_chroma` (upsert por lotes). `index_new_bronze` indexa solo lo nuevo (lo usa el Scout). **`_ct_results_text`** convierte el `resultsSection` de ClinicalTrials (eventos adversos + medidas de eficacia, por brazo) a prosa indexable — ver §9. |
| `src/rag.py`         | 2 | **El corazón.** `retrieve` → `_build_context` (agrupa chunks por documento en `[Doc N]`, presupuesto de chars, extrae outcomes) → `_generate_answer` (OpenBioLLM, prompt "analista", guardián anti-degeneración + reintentos). Incluye **router de intención** (`detect_intent`) y **condensado de preguntas de seguimiento** (`condense_question`) para el chat multi-turno. **RAG estricto**: si la mejor similitud < umbral, NO llama al LLM. |
| `src/citations.py`   | 2 | Post-proceso **determinista** de citas. Reparte cada `[Doc N]` a la frase que respalda (por solapamiento de términos); valida y **elimina citas fuera de rango** → *"cada cita apunta a una fuente real, siempre"*. |
| `src/outcomes.py`    | 2 | Extrae **cifras verbatim** (EASI 75/90/100, IGA 0/1…) del texto con regex deterministas, etiquetadas con su `[Doc N]` → alimentan el gráfico de la UI **sin que el LLM invente números**. |
| `src/scout.py`       | 3 | Agente de fallback. `needs_fallback` (2 señales, ver §6) → `run_scout` (extrae entidad con LLM + fallback por keywords, busca, importa, indexa) → reintenta. `answer_with_scout` orquesta. **Importa `rag` en caliente** (evita import circular; `rag` NO importa `scout`). |
| `src/evaluation.py`  | 4 | Mismo contexto RAG a OpenBioLLM y al generalista (comparación justa) → **juez neutral** puntúa una rúbrica 1-5 en JSON → CSV/JSON en `data/`. |
| `src/compare.py`     | — | Recuperación comparativa MedCPT vs OpenAI (`retrieve_ranked`, sin LLM) y redacción opcional (`answer_from_backend`, que reutiliza el pipeline completo de `rag`). Alimenta la página de comparación. |
| `src/triplet_agent.py` | Eval | **Agente catalogador**: descompone una pregunta libre en (ancla, positivo, negativo), **verifica** que el fármaco existe en el corpus y **se abstiene** si no puede. Plan B determinista por mecanismo si el LLM falla. Usa `config.LLM_JUDGE`. |
| `src/verdict.py`     | Eval | **Veredicto en vivo por pregunta**: qué embedding entendió mejor ESTA consulta (hit@1, on-target, AUC de la pregunta, triplete en vivo). Sin objetivo verificado → **no declara ganador**. |
| `src/report.py`      | 1.0 | **Informe de evidencia exportable**: HTML autónomo (CSS embebido, sin red), imprimible a PDF. Separa fuentes citadas de solo recuperadas. |
| `src/status.py`      | 1.0 | `system_status()`: comprueba Ollama, presencia de los modelos y nº de chunks del corpus. **Nunca lanza**; devuelve flags + detalle. |
| `app/streamlit_app.py` | 5 | Chat con citas resaltadas, tarjetas de fuente, KPIs, gráfico de outcomes, panel del Scout, panel de estado y botón de descarga del informe. CSS propio inline (sin llamadas a red). |
| `app/pages/1_Comparativa_MIA_vs_Centivence.py` | 5 | Página lado a lado: qué recupera MedCPT (MIA) vs OpenAI (Centivence), con **banner de veredicto** por pregunta y expander del benchmark agregado. |

### 3.4 Scripts raíz (fuera de `src/`, orquestan o mantienen)
- `run_phase1.py` — orquesta ingesta→procesado→indexado.
- `check_setup.py` — diagnóstico de entorno (no instala nada).
- `run.ps1` / `run.bat` — lanzador de un clic (verifica `.venv`, avisa si Ollama no está, abre la app).
- `ver_db.py` — inspecciona ChromaDB (censo, ejemplos, búsqueda real).
- `index_openai.py` — crea la colección OpenAI re-embediendo los chunks de la de MedCPT (mismos textos).
- `evaluate_embeddings.py` — CAPA 2: precision@k / hit@1 / MRR de MedCPT vs OpenAI (golden set Básico/Difícil).
- `evaluate_embeddings_semantics.py` — CAPA 1: tripletes + AUC + mapas 2D (MedCPT / OpenAI / bge).
- `export_corpus_manifest.py` — censo reproducible del corpus indexado (ver §8).
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
./.venv/Scripts/python.exe src/triplet_agent.py                    # catalogado + verificación contra el corpus
./.venv/Scripts/python.exe src/verdict.py                          # veredicto por pregunta (los dos backends)
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
- **Abstención > espectáculo.** `triplet_agent` verifica contra el corpus antes de emitir
  veredicto y `verdict` NO declara ganador sin objetivo verificado. Es la misma filosofía que la
  validación determinista de citas: preferimos no decir nada a decir algo no fundamentado.
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

Las **cinco fases están implementadas y probadas** de punta a punta, más la capa **MIA 1.0**
(informe exportable, panel de estado, robustez, lanzador). La migración a **MedCPT** está
completa (inner-product, umbral 66.0, 8.920 chunks / 768 dim).

La tesis se valida en **dos capas**:
- **Capa 1 — comprensión semántica** (`data/semantics_summary.csv`): MedCPT 0.833 vs OpenAI
  0.667 en tripletes Mecanismo→fármaco.
- **Capa 2 — recuperación** (`data/evaluation_embeddings.csv`): en preguntas difíciles MedCPT
  hit@1 0.875 vs OpenAI 0.625; MRR 0.938 vs 0.792.

Documentos vivos:
- **`CHANGELOG.md`** — **registro cronológico de cambios con el PORQUÉ de cada uno.**
  ⚠️ **Al cerrar cualquier bloque de trabajo, añade una entrada aquí antes de commitear.**
  Formato: fecha · título · hash, y dentro **Añadido / Cambiado / Corregido / Medido**.
  Escribe el **motivo**, no solo el qué: es lo único que no se puede reconstruir leyendo el
  código. De aquí sale la sección "desarrollo del trabajo" de la memoria del TFM.
- **`TASKS.md`** — tareas (pendientes / en curso / ideas). Mira al FUTURO; el CHANGELOG mira
  al pasado. Consúltalo y actualízalo cuando cierres o abras trabajo.
- **`README.md`** — explicación divulgativa, diagrama y tablas de resultados.
- **`docs/memoria_tfm.md`** — borrador vivo de la memoria final del TFM.

**Los tres niveles de trazabilidad** (no se solapan, se complementan):

| Nivel | Dónde | Responde a |
|-------|-------|-----------|
| Exacto | `git log` / `git show` | ¿Qué línea cambió, en qué commit? |
| Narrativo | `CHANGELOG.md` | ¿Qué cambió, cuándo y **por qué**? |
| Prospectivo | `TASKS.md` | ¿Qué falta por hacer? |

---

## 8. Reproducibilidad del corpus (importante)

El corpus se construyó de dos maneras y **solo una es reproducible sola**:

1. `run_phase1.py` descarga a `data/bronze` por (enfermedad + fármaco) → **sí** se regenera.
2. `ingest_desktop_set.py` importó un export manual de PubMed ("Abstract (text)") que vivía en
   el escritorio del ordenador anterior. **Ese `.txt` se ha perdido**, y de ahí sale buena parte
   de los 8.920 chunks.

Para que la pérdida no rompa la reproducibilidad, `export_corpus_manifest.py` lee ChromaDB y
escribe el censo de TODO lo indexado:

- `data/corpus_manifest.csv` — un documento por fila (doc_id, fuente, título, URL, fármacos,
  acceso, nº de chunks y si es regenerable desde bronze).
- `data/corpus_pmids.txt` — la lista de PMID, re-descargables desde PubMed.

**Regla:** si vuelves a indexar o amplías el corpus, **re-ejecuta este script y commitea el CSV**.
Es la única prueba de qué hay dentro del índice.

---

## 9. Materia prima: qué se coge de cada fuente (y qué NO)

MIA **no lee papers completos**. Conviene tenerlo claro y declararlo en el TFM.

| Fuente | Qué se descarga | Tamaño típico |
|--------|-----------------|---------------|
| **PubMed** | Solo el **abstract** (`efetch` con `rettype=abstract`). Nunca el texto completo. | ~1.850 chars/doc |
| **ClinicalTrials.gov** | Ficha (`officialTitle` + `briefSummary` + `conditions`) **y**, desde el 1-sep-2026, el `resultsSection`. | hasta ~12.000 chars |

El id de **PMC** solo se usa como *señal de acceso* (`access="open"` vs
`"abstract_only"`), **nunca** para bajar el texto completo. No se hace scraping de PDFs.

### 9.1 El `resultsSection` de ClinicalTrials (la mejor materia prima)

La API ya lo devolvía en la misma petición que se hacía; simplemente se tiraba. Trae:
- **`adverseEventsModule`** — eventos adversos con numerador/denominador reales (5/55),
  **por brazo** (fármaco vs placebo), separando graves de leves y clasificados por
  sistema orgánico. Es dato primario publicado por el promotor.
- **`outcomeMeasuresModule`** — endpoints de eficacia con su valor por brazo.

`_ct_results_text()` lo convierte a **prosa** (no a tabla) por dos razones:
1. el LLM lee prosa; una tabla ASCII la interpreta mal;
2. `outcomes.py` extrae las cifras con regex **por frase**, así que se escribe
   **una afirmación por frase**, con el brazo de tratamiento primero y el comparador
   en una frase aparte. Si fueran a la misma frase, el extractor cogería los dos
   porcentajes sin saber cuál es de qué brazo → cifras engañosas.

**Dos trampas que costaron encontrar:**
- **El brazo de control NO se detecta buscando "placebo"** en cualquier posición: en los
  ensayos doble ciego los brazos activos se llaman *"Dupilumab 300 mg + Oral Placebo"*. Se
  comprueba primero si el nombre menciona un fármaco de `config.ALL_DRUGS`.
- **Los títulos de los endpoints son verbosos** ("...(EASI) Response >=75 Percent...") y no
  casan con el patrón corto de `outcomes.py`. `_canonical_endpoint()` los traduce a
  "EASI 75" / "IGA 0/1" y lo escribe pegado al valor.

### 9.2 Cobertura real (medida el 1-sep-2026)

- 81 de 224 ensayos en bronze (36%) traen resultados → **5.845 filas** de eventos adversos.
- Tras indexar: **76 de 189 ensayos (40%)** aportan cifras, **575 puntos de dato**
  (129 de eficacia, 446 de seguridad). Antes: **cero**.
- Texto completo de PubMed Central: solo **33%** del corpus sería descargable legalmente,
  y cada paper pesa **×33** un abstract (~61.000 chars). Ver `CHANGELOG.md`.

### 9.3 Limitación conocida

Los documentos de CT.gov **rara vez ganan** a las revisiones de PubMed en el ranking: hay
2.971 papers frente a 189 ensayos, y un abstract se parece más a una pregunta en lenguaje
natural que la prosa de un registro. El dato está indexado y sale cuando la pregunta es de
corte "ensayo", pero no domina. Mejorarlo pide **recuperación híbrida**, no más datos.
