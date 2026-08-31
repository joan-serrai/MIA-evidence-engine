# Registro de cambios — MIA

Historia del proyecto en cristiano: **qué cambió, cuándo y POR QUÉ**. Lo más nuevo, arriba.

## Para qué sirve este archivo

Ya había dos formas de saber qué se había hecho, y las dos se quedaban cortas:

- **`git log`** — es la verdad exacta (qué línea cambió en qué commit), pero es técnico y
  hay que saber leerlo. No sirve para explicarle el proyecto a nadie.
- **`TASKS.md`** — es una lista de *tareas*: qué está pendiente, en curso o hecho. Responde
  "¿qué falta?", no "¿cómo hemos llegado hasta aquí?".

Este archivo cubre el hueco: el **relato cronológico** del proyecto. Es de donde se saca
directamente la sección de "desarrollo del trabajo" de la memoria del TFM, y lo que se
consulta cuando dentro de tres meses te preguntes por qué algo está hecho de una forma rara
(casi siempre hay un motivo, y casi siempre se aprendió a base de fallar).

**Regla:** cada vez que se cierre un bloque de trabajo, se añade una entrada aquí **con el
motivo**, no solo con el qué. El "por qué" es lo único que no se puede reconstruir mirando
el código.

Formato de cada entrada: fecha · título · `commit`, y dentro, agrupado por tipo:
**Añadido**, **Cambiado**, **Corregido**, **Medido**.

---

## 2026-08-31 · Respuestas más desarrolladas, citas fiables e interfaz en inglés
`6e9c34f`

Todo esto sale de revisar **un informe real que salió mal**: a la pregunta *"What are the
most common adverse events of upadacitinib?"* MIA contestó con una sola frase, sin ninguna
cita, con la tabla de cifras vacía y mezclando español e inglés.

### Corregido
- **Trazabilidad falsa (lo más grave).** `report.py` y `streamlit_app.py` asumían que la
  primera fuente estaba citada cuando la respuesta no traía ninguna cita. Comprobado contra
  el corpus: la respuesta hablaba de nasofaringitis y el informe se la atribuyó a un
  meta-análisis (PMID 38282878) que **no menciona esa palabra en todo el abstract**. Es
  exactamente lo contrario de lo que promete MIA. Ahora, sin citas no hay fuentes citadas y
  se dice de forma explícita.
- **Citas agrupadas** (`[Doc 1, Doc 2, Doc 3]`) sobrevivían al post-proceso porque el regex
  de limpieza exigía `]` justo después del número. Se limpian antes de repartir, y la red de
  seguridad final las normaliza a citas individuales válidas.
- **El reparto de citas no colocaba ninguna** cuando los 5 papers recuperados hablaban del
  mismo fármaco: se contaban términos compartidos "a pelo" y se exigía que el mejor superase
  estrictamente al segundo, así que el empate lo bloqueaba todo. Ahora cada término pesa por
  lo **raro** que es entre los candidatos (IDF suavizado): "upadacitinib" en 5 de 5 no aporta
  señal, "nasopharyngitis" en 2 de 5 sí.
- **Respuestas de una sola frase.** La causa no era el prompt: `_build_context` metía solo
  los *chunks recuperados*, así que el modelo veía **un tercio del abstract**. Ahora va el
  documento completo (11,7k de 24k caracteres de presupuesto).
- **La directiva de formato se ignoraba** dentro de un system prompt ya muy largo. Se mueve
  al final del mensaje de usuario y se hace sensible a la intención: sin eso, a una pregunta
  de seguridad respondía sobre eficacia en adolescentes.
- **Preguntas de seguridad con la tabla siempre vacía**: `outcomes.py` no tenía **ni un**
  patrón de eventos adversos, solo de eficacia (EASI/IGA/SCORAD).

### Añadido
- Patrones de seguridad en `outcomes.py` (AEs, serious AEs, discontinuación y los concretos
  frecuentes), con etiqueta `kind` para **no mezclar eficacia y seguridad** en la misma tabla
  ni en el mismo gráfico: un EASI-75 alto es bueno y una tasa de nasofaringitis alta es mala;
  en la misma escala se leerían igual.
- Reintento por respuesta corta que **conserva la mejor obtenida** en vez de descartarla.
- Limpieza determinista de muletillas tipo *"based on the provided CONTEXT"*.
- Aviso explícito de qué paper citado es **narrativo** y por qué no tiene cifras (muchos
  meta-análisis redactan los resultados en prosa, sin un solo número).

### Cambiado
- **Interfaz e informe monolingües en inglés.** Los comentarios del código siguen en español,
  que es el idioma de trabajo del proyecto.
- **Orden de lectura**: respuesta citada, fuentes citadas y cifras clave arriba; la confianza
  de recuperación y los papers recuperados pero no usados, plegados en un único
  *"Retrieval details"*. Antes se mezclaban al mismo nivel dos intenciones —contestar y
  justificar cómo de seguro estaba el sistema— y resultaba confuso.
- Escalada de temperatura bajada de 0.7 a 0.5: los reintentos ahora se disparan mucho más a
  menudo (también por longitud), y premiar la deriva sería mal negocio en un sistema
  anti-alucinación.

### Medido
- La pregunta original pasa de **1 frase y 0 citas** a **3-4 frases y 2 fuentes citadas**, y
  esas dos fuentes **sí** mencionan los eventos adversos que se afirman.
- Límite conocido: OpenBioLLM 8B se resiste a pasar de 3-5 frases aunque se le pidan 5-9.

---

## 2026-08-31 · Versiones de dependencias fijadas
`525038f`

### Cambiado
- `requirements.txt` pasa de `>=` a `==` en las dependencias directas, manteniendo los
  comentarios didácticos. **Motivo:** al montar el proyecto en el ordenador nuevo, `>=`
  trajo saltos de versión MAYOR (transformers 4→5, pandas 2→3, numpy 1→2, torch 2→2.13).
  Funcionó, pero por suerte, no por diseño — y el TFM necesita ser reinstalable.
- Se añaden `torch`, `transformers` y `numpy` como dependencias **explícitas**: el código las
  importa directamente (`src/embeddings.py` para MedCPT, `evaluate_embeddings_semantics.py`)
  pero venían colándose como transitivas de `sentence-transformers`, así que su versión la
  decidía otro paquete.

### Añadido
- `requirements.lock.txt` — `pip freeze` completo (122 librerías) para reproducir el entorno
  exacto con el que se obtuvieron los resultados del TFM.

### Medido
- `pip install --dry-run` de los dos ficheros no propone ningún cambio; `pip check` sin
  conflictos.

---

## 2026-08-31 · Traslado de ordenador y reconstrucción del entorno
*(sin commit: es infraestructura, no código)*

### Cambiado
- El proyecto se mueve de OneDrive a **`C:\dev\MIA`**. **Motivo:** OneDrive mantenía
  `data/chroma` (312 MB) y `.venv` (1,5 GB) como *placeholders* en la nube — ocupaban 24 KB
  reales en disco. Una base de datos vectorial sobre una carpeta que se sincroniza sola es
  lenta y propensa a corrupción por escrituras concurrentes.
- Entorno reconstruido: Python 3.12.10, Ollama 0.33.2 y los tres modelos (~15 GB).

### Medido
- Copia verificada: 226/226 ficheros, 351,2 MB, 0 placeholders, `git fsck` limpio.
- El corpus sobrevivió al traslado: **no** hubo que reindexar (se ahorran ~45 min de CPU).

---

## 2026-08-31 · Cierre del rodaje de la Comparativa
`91ad821`

### Medido
- Página de comparación verificada en el navegador con preguntas de ejemplo y libres,
  incluida la **abstención** del agente cuando no puede verificar el objetivo contra el
  corpus.

---

## 2026-08-31 · Documentación al día y arranque de la memoria
`21deb6d`

### Añadido
- `docs/memoria_tfm.md` — borrador vivo de la memoria final.

### Cambiado
- `CLAUDE.md`, `README.md` y `TASKS.md` sincronizados con el estado real (llevaban desfasados
  desde la capa MIA 1.0: no mencionaban `report.py`, `status.py`, `verdict.py` ni
  `triplet_agent.py`).

---

## 2026-08-31 · Censo reproducible del corpus
`3056ddf`

### Añadido
- `export_corpus_manifest.py` → `data/corpus_manifest.csv` y `data/corpus_pmids.txt`.

### Motivo
Parte del corpus se importó de un export manual de PubMed que vivía en el escritorio del
ordenador anterior y **se ha perdido**. El censo lee ChromaDB y deja constancia de todo lo
indexado, con los PMID re-descargables: sin él, el corpus no sería reproducible ni auditable.

---

## 2026-08-31 · Veredicto por pregunta y benchmark semántico
`f8b14e3`

### Añadido
- `src/triplet_agent.py` — agente catalogador que descompone una pregunta libre en
  (ancla, positivo, negativo), **verifica contra el corpus** y **se abstiene** si no puede.
  Plan B determinista por mecanismo si el LLM falla.
- `src/verdict.py` — veredicto en vivo: qué embedding entendió mejor *esta* consulta.
  Sin objetivo verificado, **no declara ganador**.
- `evaluate_embeddings_semantics.py` — CAPA 1 del experimento: tripletes + AUC + mapas 2D.

### Medido
- Comprensión semántica, nivel Mecanismo→fármaco: **MedCPT 0.833** vs OpenAI 0.667 vs
  bge 0.667.

---

## 2026-07-28 · MIA 1.0 — "de demo a producto"
`e4d7a7c`, `4ba50a1`, `9d0d270`

### Añadido
- `src/report.py` — informe de evidencia HTML autónomo, imprimible a PDF.
- `src/status.py` — panel de estado (Ollama, modelos, corpus) y avisos accionables con el
  comando exacto para arreglar lo que falte, en vez de un traceback.
- `run.ps1` / `run.bat` — lanzador de un clic.

### Cambiado
- Se separan las fuentes **citadas** de las solo **recuperadas**, cada una con su motivo.

### Corregido
- Import del Scout (evita el ciclo `rag` ↔ `scout` importándolo en caliente).

---

## 2026-07-15 · Citas válidas garantizadas, router de intención y acceso
`d689313`

### Añadido
- Validación determinista de citas: se elimina cualquier `[Doc N]` fuera de rango, **siempre**
  → "cada cita apunta a una fuente real".
- Router de intención (eficacia / seguridad / comparativa / mecanismo) por palabras clave, sin
  LLM: instantáneo, gratis y auditable.
- Transparencia de acceso: si un paper citado es de pago (solo DOI, sin PMC), MIA lo avisa.

---

## 2026-07-15 · Interfaz de comparación MedCPT vs OpenAI
`3fa6e46`

### Añadido
- Página a dos columnas que enseña qué recupera cada modelo sobre la misma pregunta y el
  mismo corpus — lo único que cambia es el embedding.

---

## 2026-07-15 · Primer commit: MIA de punta a punta
`ee49a6b`

### Añadido
- Las cinco fases completas: ingesta (PubMed + ClinicalTrials) → procesado y vectorizado →
  RAG estricto con citas → agente Scout → interfaz Streamlit.
- Evaluación de embeddings MedCPT vs OpenAI (CAPA 2, recuperación).

### Medido
- En preguntas difíciles (por mecanismo, sin nombrar el fármaco): MedCPT **hit@1 0.875** vs
  OpenAI 0.625; **MRR 0.938** vs 0.792.

---

## Antes del control de versiones

El proyecto nació antes del primer commit. Lo relevante de esa etapa, reconstruido desde
`TASKS.md` y los comentarios del código:

- **Migración de bge-small a MedCPT** y el hallazgo que la define: con **coseno** no existía
  umbral válido. Medido sobre 337 documentos, la pregunta ajena *"capital de Francia"*
  puntuaba **más alto** (0,68) que las preguntas relevantes (0,63-0,66) → la puerta
  anti-alucinación no funcionaba. **Causa:** MedCPT codifica la confianza en la **norma** del
  vector, y normalizar la destruye. **Arreglo:** producto escalar (`ip`), sin normalizar.
  Reindexado completo: 8.920 chunks, 768 dim. Umbral fijado en **66,0** (relevantes 70-73,
  ajenas 56-64: hueco limpio).
- **Las citas las coloca el post-proceso, no el LLM**: OpenBioLLM 8B redacta bien a
  temperatura 0 pero omite o amontona las `[Doc N]`.
- **Los números nunca los pone el modelo**: se extraen con regex del texto recuperado.
