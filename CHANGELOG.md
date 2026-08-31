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

## 2026-09-01 · Instrumento de evaluación por fuente (y dos hallazgos incómodos)

### Corregido — las dos colecciones habían divergido
Al indexar los resultados de CT.gov solo se reindexó la colección de MedCPT:
**9.734 chunks frente a 8.920 en la de OpenAI**. Eso invalidaba el experimento de
la tesis, donde lo ÚNICO que debe cambiar entre las dos es el vector. Re-ejecutado
`index_openai.py`: ambas colecciones vuelven a tener 9.734 chunks y 1.117 de CT.gov.

### Añadido
- `evaluate_sources.py` — mide un eje que hasta ahora nadie medía: **qué fuente
  aporta la evidencia** y **si el umbral está bien calibrado para cada una**. No
  toca `evaluate_embeddings.py`: ese mide la tesis (MedCPT vs OpenAI) y sus
  números están publicados; mezclar ejes lo enturbiaría.
- 12 preguntas de **seguridad** (el gold set original era casi todo eficacia) y 15
  de **control negativo**, deliberadamente más duras que antes: no solo "capital
  de Francia", también otras patologías (malaria, diabetes) y otros fármacos
  (adalimumab en artritis). Un umbral solo tiene sentido si sabes qué queda a
  cada lado, y lo que queda al lado no son preguntas absurdas.
- Comprobación de si llega **el tipo de dato** que la pregunta pide: en seguridad
  no basta con recuperar el fármaco correcto, tiene que llegar alguna cifra de
  evento adverso. Se verifica con el extractor determinista de `outcomes.py`.

### Medido — lo que sale bien
- **ClinicalTrials aporta cifras de eventos adversos en 10 de 12 preguntas de
  seguridad; PubMed, en 4 de 12.** Dos veces y media mejor. La Capa 1 queda
  justificada con datos, no con intuición.
- CT.gov ocupa el 8-12% del top-5 según el nivel, cerca de su tasa base (11,5%):
  globalmente no está silenciada, el problema es de preguntas concretas.

### Medido — los dos hallazgos incómodos
1. **El umbral 66,0 es más frágil de lo que parecía.** Con un control negativo
   duro, el hueco de PubMed cae a **+1,79** (con los controles fáciles originales
   daba +6,6). Es decir: el 66,0 separa bien de "capital de Francia", pero apenas
   de "tratamiento de la diabetes tipo 2". La hipótesis de ayer —bajarlo a ~63—
   **no es segura**: el percentil 95 de las ajenas está en 63,35.
   Sí queda margen para un umbral **por fuente**: CT.gov separa MEJOR (+3,00 de
   hueco) que PubMed (+1,79), así que puede permitirse un corte más bajo.
   Hoy pierde 4 de 33 preguntas en las que tenía el fármaco correcto (sim 63,1-63,8).
2. **Con OpenAI no existe umbral válido.** El hueco es NEGATIVO en las dos
   fuentes (−0,02 en PubMed, −0,07 en CT): las preguntas ajenas puntúan MÁS ALTO
   que las relevantes. Es exactamente el mismo fallo que tuvo MedCPT con coseno
   (ver más abajo, migración a MedCPT).

   **Esto es un resultado de tesis, no una nota técnica.** Hasta ahora MedCPT
   ganaba a OpenAI en ranking (hit@1, MRR). Este es un eje distinto y más
   decisivo: **MedCPT puede sostener una puerta de evidencia y OpenAI no**. Para
   un sistema cuyo lema es "sin evidencia, no responde", eso no es un matiz.

Salidas: `data/evaluation_sources.csv` (33 preguntas × 2 backends) y
`data/evaluation_thresholds.csv` (calibración por backend × fuente).

---

## 2026-09-01 · Resultados de ClinicalTrials.gov e interfaz "Aurora LSHC"

### Añadido — Capa 1: los resultados de los ensayos
Hasta ahora, de cada ensayo solo se guardaba la ficha descriptiva (título +
resumen + condiciones). El **hallazgo**: la MISMA respuesta de la API que ya se
descargaba trae un `resultsSection` con los resultados tabulados, y se estaba
tirando en `processing.py`. No hizo falta descargar nada: el dato llevaba meses
en `data/bronze` — **81 de 224 ensayos (36%) con 5.845 filas de eventos adversos**.

Es mejor materia prima que un abstract: trae numerador y denominador reales
(5/55), viene **por brazo** (o sea, con su placebo al lado), separa eventos
graves de leves y lo publica el promotor en un registro oficial.

- `_ct_results_text()` y auxiliares en `processing.py`: convierten el módulo de
  eventos adversos y las medidas de resultado a **prosa**, no a tabla. Dos
  motivos: el LLM lee prosa, y `outcomes.py` extrae las cifras por frase. Se
  escribe **una afirmación por frase**, con el brazo de tratamiento primero y el
  comparador aparte — si fueran a la misma frase, el extractor cogería los dos
  porcentajes sin saber cuál es de qué brazo.
- `_canonical_endpoint()`: traduce los títulos verbosos de CT.gov
  ("...(EASI) Response >=75 Percent...") al nombre corto ("EASI 75") que
  `outcomes.py` sabe reconocer. Sin esto no se extraía ni una cifra de eficacia.

### Corregido
- **Detección del brazo de control.** Buscar "placebo" en cualquier posición del
  nombre del brazo fallaba: en los ensayos doble ciego los brazos activos se
  llaman *"Dupilumab 300 mg + Oral Placebo"*, así que TODOS salían como control y
  no se extraía ni un dato. Ahora se comprueba primero si el brazo menciona un
  fármaco conocido, y solo después el patrón de placebo (que además debe ir al
  principio del nombre).
- **Comillas rotas en los fragmentos.** El CSS decía `content: "\201C"`, pero ese
  CSS vive dentro de una cadena de Python y ahí `\201` es un **escape octal**:
  Python lo convertía en el carácter de control `0x81` y dejaba la "C" suelta, así
  que cada fragmento empezaba por "▮C". Bug latente que venía del código original.
- Tope de altura en el cuadro de chat. En el primer render Streamlit calcula mal
  su altura (medido: 182 px para una línea que ocupa 28) y se queda enorme hasta
  que haces clic. Comprobado que **no lo causa este CSS** —desactivándolo el valor
  no cambia—; el tope lo acota sin impedir que crezca con varias líneas.

### Cambiado — Interfaz "Aurora LSHC"
- Tema oscuro con degradados difusos que evocan una aurora boreal, en los colores
  del sector Life Sciences & Health Care: verde-azulado clínico, cian, verde salud
  y violeta de apoyo. En la Comparativa el violeta cobra sentido semántico:
  MIA = verde-azulado, Centivence = violeta.
- Técnica: dos capas `position:fixed` con radiales **anchos y bajos** (elipses al
  26% de alto, no círculos: una aurora cae en cortinas horizontales), `blur(78px)`
  y una deriva muy lenta con desfases distintos, así que nunca repiten forma.
- Regla de contraste que se respeta en todo el diseño: **la aurora vive solo en el
  fondo y en los bordes**; todo lo que hay que leer va sobre superficie sólida. En
  una herramienta clínica la legibilidad manda sobre el efecto.
- Se respeta `prefers-reduced-motion`: la aurora se queda quieta si el sistema lo
  pide, sin perder el degradado.

### Medido
- Corpus: 8.920 → **9.734 chunks**.
- Ensayos con cifras extraíbles: 0 → **76 de 189 (40%)**, con **575 puntos de dato**
  (129 de eficacia, 446 de seguridad).
- Verificado en navegador: respuesta de 6 frases con **cada afirmación citada**,
  fuentes desplegadas, sin errores de consola.
- Limitación observada y declarada: los documentos de CT.gov **rara vez ganan** a
  las revisiones de PubMed en el ranking (hay 2.971 papers frente a 189 ensayos, y
  un abstract se parece más a una pregunta en lenguaje natural que la prosa de un
  registro). El dato está dentro y sale cuando la pregunta es de corte "ensayo",
  pero no domina. Mejorarlo pide recuperación híbrida, no más datos.

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
