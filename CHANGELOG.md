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

## 2026-09-04 · Guía para cualquier enfermedad, `suggest_drugs.py` y barrido de seguridad

Objetivo de la sesión: que alguien que se descargue MIA de GitHub pueda cargar **su**
enfermedad, sus fármacos y sus mecanismos sin leer código, y comprobar que el repositorio
puede publicarse sin exponer nada.

### Añadido — `docs/GUIA_NUEVA_ENFERMEDAD.md`
Guía de usuario, en español, con las dos vías de carga: **automática** (`build_corpus.py`
descarga de PubMed y ClinicalTrials.gov) y **manual** (export "Abstract (text)" de PubMed
→ `ingest_desktop_set.py`). **Motivo:** el modelo mental natural —"dejo mis PDFs en la
carpeta de la base de datos y pregunto"— no es cómo funciona MIA, y había que decirlo
explícitamente: MIA no lee PDFs; su biblioteca son abstracts y fichas de ensayos que se
descargan de las fuentes oficiales. La guía dice también dónde queda cada cosa
(`domains/`, `data/bronze/<slug>`, `data/chroma`, censo CSV) y qué se sube a git.

### Añadido — `suggest_drugs.py`: ¿qué fármacos se estudian para X?
Consulta ClinicalTrials.gov, cuenta en cuántos ensayos aparece cada intervención
farmacológica para la enfermedad y lista las más estudiadas con su fase máxima. Sin LLM,
sin descargar nada. **Motivo:** el paso más difícil para un usuario ajeno al área no es el
comando, es saber *qué 5 fármacos pedir*. Probado: psoriasis (1.028 ensayos → secukinumab,
apremilast, etanercept, adalimumab, ustekinumab…) y Crohn (2.000 ensayos → adalimumab,
infliximab, vedolizumab, ustekinumab, certolizumab pegol…). Las listas coinciden con la
práctica clínica. La normalización de nombres tiene pruebas puras (`tests/test_pure.py`).

### Cambiado — `ingest_desktop_set.py` entiende de perfiles
Nuevo `--domain <slug>` (mismo patrón que `run_phase1.py`) y la etiqueta de fallback ya no
es `atopic_dermatitis` a fuego sino el slug del dominio activo. **Probado** con un export
real de 8 registros (bimekizumab AND psoriasis) contra el perfil de psoriasis: 5 con
abstract, 19 chunks, 584 → 584 porque los 5 PMID ya estaban (entraron por la búsqueda
extra "IL-17 inhibitor") — es decir, la idempotencia por PMID funciona también por esta vía.

### Medido — `build_corpus.py` con una tercera enfermedad, de punta a punta
`--disease "Crohn disease" --class anti_tnf=infliximab,adalimumab --class il23=risankizumab
--max 5`: 15 ensayos + 15 abstracts → 26 documentos, 114 chunks, censo exportado, y una
pregunta respondida con 5 fuentes por encima del umbral (3 de CT.gov, 2 de PubMed). El
perfil de prueba se borró después: la guía usa psoriasis como ejemplo. **Observación
incómoda:** la respuesta salió sin ninguna `[Doc N]` en el texto — el reparto determinista
de citas no encontró ningún emparejamiento claro con 5 fuentes que hablan todas de
risankizumab y con un corpus tan pequeño (sin términos distintivos). Con corpus pequeños
el lector ve las fuentes pero no la cita frase a frase. Anotado en `TASKS.md`.

### Medido — ejemplo real fuera de dermatología: retinoblastoma
A petición del autor, el flujo completo con una enfermedad oncológica pediátrica.
`suggest_drugs.py` leyó 140 ensayos; se descartaron con criterio los fármacos de soporte
(filgrastim, mesna, G-CSF) y quedaron carboplatino, etopósido, vincristina, melfalán y
topotecán. `build_corpus.py --max 30` con dos búsquedas extra (intraarterial e
intravítrea): **195 documentos, 598 chunks**. La pregunta sobre melfalán intraarterial se
respondió con 5 fuentes a 72-74 de similitud (umbral 66) y la cifra clave (55% de
conservación del ojo) se verificó literal en el abstract citado. El perfil se conserva en
`domains/retinoblastoma.json` como segundo ejemplo de la guía. **Lección:** el endpoint no
siempre es "nombre + número"; aquí es *globe salvage*, y el sistema lo acepta sin número.

### Seguridad — barrido previo a GitHub (resultado: limpio)
- `.env` nunca ha estado en el historial; los 214 blobs del repo no contienen patrones de
  clave (OpenAI `sk-`, AWS, GitHub, HuggingFace, claves privadas, `password=`…); el prefijo
  de la clave real del `.env` local no aparece en ningún commit ni archivo rastreado.
- Sin rutas con nombre de usuario, ni correos, en archivos rastreados (solo `C:\dev\MIA`).
- **Corregido:** Streamlit enviaba telemetría de uso a sus servidores por defecto
  (`Collecting usage statistics` en el arranque). En un producto que promete "ningún dato
  sale del ordenador" eso se apaga: `browser.gatherUsageStats = false`.
- **Documentado:** `HF_HUB_OFFLINE=1` (opcional, en `.env.example`) para que la carga de
  MedCPT no consulte HuggingFace tras la primera descarga.
- Pendiente de decisión del autor, no de código: el correo del autor en los commits será
  público en GitHub (GitHub ofrece un correo `noreply` si se prefiere).

## 2026-09-03 · Auditoría externa: la demo fallaba, XSS, y MIA para cualquier enfermedad

Bloque nacido de una **auditoría completa** (seguridad, calidad de software, pruebas
reales de cada fase y visión de producto). Se pidió probar cada cosa tres veces antes
de darla por buena. Lo que salió, en orden de gravedad.

### Corregido — la pregunta de demo fallaba 4 de 6 veces (regresión del 2-sep)
El guardián de "modo examen" ampliado el 2-sep-2026 (`_EXAM_RE`) **descartaba** toda
respuesta que abriera con *"The Answer is:"*. Medido con instrumentación de cada intento
(`ollama.chat` espiado) sobre `"What is the efficacy of dupilumab in atopic dermatitis?"`:

```
temp 0.0 → "The Answer is: Dupilumab is effective in treating AD, improving signs…"  ← RECHAZADA
temp 0.2 → "The provided CONTEXT does not contain a specific question or task."     ← aceptada
temp 0.35→ "The Answer is a meta-analysis of adults."                                 ← RECHAZADA
temp 0.5 → "The provided CONTEXT is insufficient for answering the QUESTION…"        ← aceptada
```

Es decir: la **única salida buena** (temperatura 0) se tiraba por su primera palabra, y
los reintentos a más temperatura hacían que el modelo **se rindiera** — y esa rendición
no la cazaba ningún filtro, así que se devolvía al usuario como respuesta (o, si todo
degeneraba, el mensaje de "no pude producir una respuesta fiable"). A/B contra el prompt
committeado (HEAD): HEAD 3/3 respuestas reales (con el prefijo feo visible); working
tree 2/6. **Arreglo:** el prefijo de examen se **recorta** (`_strip_exam_prefix`) en vez
de rechazarse; solo sigue siendo degenerado el examen "puro" (`_EXAM_BARE_RE`: tras el
prefijo solo hay yes/no/una letra). Y la rendición total se detecta (`_GIVEUP_RE`, solo
si es TODA la respuesta, ≤ 2 frases — una abstención parcial legítima sigue pasando).
**Verificado 3/3** después del cambio; 12 casos positivos y negativos en `tests/test_pure.py`.
Lección para el CLAUDE.md: un guardián nuevo se prueba con casos que NO debe marcar.

### Corregido — XSS en la interfaz
`_highlight_citations` en `app/streamlit_app.py` insertaba la respuesta del LLM en HTML
con `unsafe_allow_html=True` **sin escaparla**. La respuesta se redacta a partir de
abstracts descargados de internet: un `<script>` colado en uno (o inventado por el modelo)
se habría ejecutado en el navegador. `src/report.py` ya escapaba; la app no. Ahora
`html.escape` antes de pintar las insignias. Riesgo real bajo (app local, un usuario),
pero es exactamente el tipo de fallo que un revisor de seguridad señala primero.

### Corregido — la app escuchaba en todas las interfaces de red
Streamlit arranca en `0.0.0.0` y anuncia una "External URL" con la IP pública. MIA no
tiene autenticación. `.streamlit/config.toml` fija ahora `server.address = "localhost"`.

### Añadido — perfiles de dominio: MIA para cualquier enfermedad
Hasta hoy la dermatitis atópica estaba escrita en `config.py` **y** repartida por el
código: endpoints EASI/IGA en `outcomes.py` y `processing.py`, mecanismos IL-4/IL-13/JAK
y clases biológico↔JAK en `triplet_agent.py`, nombres de colección en siete archivos,
ejemplos y bienvenida de la app, "EASI-75, IGA 0/1" en los prompts. Ahora:

- `domains/<slug>.json` describe una patología (enfermedad, sinónimos, fármacos por
  clase, mecanismos, endpoints, términos de seguridad, búsquedas extra, ejemplos).
  `config.py` carga el perfil activo (`MIA_DOMAIN` > `domains/active.txt` > default) y
  expone los nombres de siempre; `config.activate_domain()` cambia en caliente.
- `config.endpoint_spec("PASI 75")` deriva solo el regex del abstract y los patrones del
  título de CT.gov, así que un dominio nuevo no toca código. Verificado que para el
  perfil original produce **exactamente** las mismas cifras que la lista fija anterior.
- Cada perfil tiene su colección (`mia_<slug>_<backend>`), su `data/bronze/<slug>/`,
  su silver y su censo. El perfil original conserva `mia_evidence_*` y `data/bronze`:
  el índice de 9.734 chunks y las evaluaciones del TFM siguen valiendo tal cual.
- `build_corpus.py`: un comando crea el perfil, descarga (fármaco+enfermedad y búsquedas
  libres por mecanismo), indexa con MedCPT, exporta el censo y, con `--openai`, la
  colección gemela. Selector de perfil en la barra lateral de la app.

**Medido** con un dominio real, psoriasis en placas (secukinumab, ixekizumab,
apremilast; `--max 15`; una búsqueda extra "IL-17 inhibitor"): **3 min 19 s** de punta
a punta → 100 documentos (47 PubMed + 53 CT.gov), **584 chunks**. RAG sobre ese corpus,
3 preguntas distintas: **3/3 respuestas válidas** con citas, y `outcomes` extrajo cifras
**PASI 75 / PASI 100** sin haber escrito ni una línea nueva de regex. Control cruzado: la
pregunta de dupilumab en el corpus de psoriasis puntúa 60,85 → **bloqueada** por el umbral
y dispara el Scout, como debe.

### Añadido — pruebas automáticas y preparación para GitHub
- `tests/test_pure.py`: **26 pruebas** en ~1 s sin Ollama ni Chroma (guardianes, citas,
  cifras, endpoints de CT.gov, perfiles de dominio con cambio en caliente).
- `.github/workflows/tests.yml`: las ejecuta en cada push (instala solo lo mínimo, no torch).
- `.gitattributes` (LF en el repo; fin de los avisos CRLF), `.gitignore` ampliado
  (`domains/active.txt`, `data/_report_demo.html`), `MIA_DOMAIN` en `.env.example`,
  `pytest` como dependencia de desarrollo.

### Medido — lo que la auditoría encontró y NO se ha cambiado (queda en TASKS.md)
- **Puerta de evidencia**: preguntas ajenas quedan fuera (Francia 63,1; hipertensión 59,1;
  psoriasis 62,8; artritis 61,4 — todas < 66) pero el margen con otras patologías es de
  ~3 puntos. "rocatinlimab" (fármaco ausente) **pasa** con 69,8 porque una revisión lo
  menciona: la señal B del Scout (nombre en lo recuperado) se satisface con una mención.
- **Pregunta en español**: MedCPT la recupera (70,6, top-1 un consenso mexicano) y el LLM
  responde bien… **en inglés**. La limitación es de redacción, no de recuperación.
- **Citas**: en respuestas genéricas ("improves signs and symptoms") el reparto conservador
  deja 0-1 citas por respuesta (3 ejecuciones). Correcto por diseño, pobre para la demo.
- **Paridad MedCPT/OpenAI**: el Scout solo indexa en la colección MedCPT → cada uso rompe
  el experimento controlado de la comparativa.
- **`access`**: 8.617 chunks de PubMed tienen `access = None`; el aviso de paper de pago
  no salta nunca hasta reprocesar.
- **Dependencias** (`pip-audit` sobre el lock): 4 CVE en chromadb 1.5.9
  (CVE-2026-45830/-45831/-45833, PYSEC-2026-311), todas del **modo servidor** (RBAC,
  multi-tenant, inyección vía función de embedding). MIA usa `PersistentClient` embebido
  sin servidor HTTP → no expuesto. Sin versión corregida publicada; vigilar.
- **Secretos**: `.env` con la clave real de OpenAI está ignorado y **no aparece en ningún
  commit** (comprobado con `git log -p -S`). `.env.example` correcto.
- **Documentación desfasada**: README/CLAUDE.md decían 8.920 chunks; el índice tiene 9.734
  en MedCPT y OpenAI (misma cifra: la paridad está bien ahora). Corregido en CLAUDE.md.

---

## 2026-09-02 · Fuera el "% de afinidad", y el modo examen ya no se cuela

Todo esto sale de **un solo test manual** con la pregunta
`"What is the efficacy of dupilumab in atopic dermatitis?"`. La respuesta citaba
Doc 3, 4 y 5, y el panel de "Retrieval details" listaba Doc 1 y Doc 2 con un
porcentaje de afinidad MAYOR. La pregunta legítima del usuario fue: *si esos dos
son los que mejor casan, ¿por qué no los cita?* Investigarlo destapó tres cosas.

### Corregido — el guardián de "modo examen" tenía un agujero
`_looks_degenerate` existe para descartar la degeneración tipo test de OpenBioLLM
(está afinado con preguntas estilo USMLE). Pero `_EXAM_RE` exigía que tras
*"the answer is"* viniera `yes|no|true|false|[a-e]`, así que **dejaba pasar el caso
real medido**: `"The Answer is: Dupilumab is effective in treating atopic
dermatitis…"` — el mismo tic de examen, seguido de prosa normal. Ahora basta con
que la respuesta ABRA así (es una apertura de examen se complete como se complete),
y se toleran el adorno markdown `**The answer is:**` y el rodeo *"the answer to
this question is"*. Detectarlo no descarta la respuesta: dispara un reintento con
la temperatura al alza. Probado con 8 casos, 5 positivos y 3 negativos que NO debe
marcar (entre ellos uno que empieza por *"Answering this question requires…"*).

### Cambiado — se retira el "% de afinidad" de la página de respuesta
Desaparecen el badge `● N% match` y la barra de las tarjetas de fuente citada, el
`● N% affinity` de las no citadas, el KPI *"Retrieval conf."* y la tabla
*"Retrieval confidence"* del informe exportable (`src/report.py`). Con ellos se van
`_confidence_pct` y `_conf_color` de la app y del informe.

**Motivo.** El número invitaba a leerse como *"este paper responde mejor a la
pregunta"*, y no es eso: solo mide **proximidad entre la pregunta y el texto en el
espacio del embedding**, calculada ANTES de que exista ninguna respuesta. En el
test quedó claro que además sugiere un podio inexistente: los 5 papers puntuaron
**70,16 · 69,56 · 69,54 · 69,37 · 69,23** — un empate técnico que la calibración
estiraba a un engañoso 60% → 57%. El tooltip ya avisaba de que no era una
probabilidad de veracidad, pero un número grande junto a la palabra "confianza" se
lee como nota de fiabilidad por mucho que la letra pequeña lo niegue.

La calibración **no se pierde**: sigue viva en `src/compare.py`, que alimenta la
página MedCPT vs OpenAI. Ahí el número es el objeto de estudio (la tesis va
justamente de calidad de recuperación), no un adorno junto a una respuesta.

### Corregido — la UI afirmaba más de lo que puede demostrar
Decía de las fuentes no citadas: *"the answer did not rely on them"*. **Eso no está
probado.** Doc 1 y Doc 2 SÍ entraron en el contexto que leyó el LLM — de hecho
entraron primero, y el presupuesto de caracteres se gasta en orden, así que fueron
los que menos riesgo tuvieron de truncarse. Lo único cierto es que **ninguna frase
pudo atribuírseles con confianza**, que es lo que dice ahora. Mismo arreglo en la
app y en el informe.

### Medido — por qué Doc 1 y Doc 2 se quedaron sin cita
No fue el ranking: fue el reparto determinista de `citations.py`. Reproducido el
scoring frase a frase:

```
F1: "Dupilumab is effective…"   D3=2.86 | D5=2.86 | D1=0.09 | D2=0.09 | D4=0.09
    -> SIN CITA (empate PERFECTO entre Doc 3 y Doc 5, margen 1.00x < 1.20)
```

Doc 1 y Doc 2 sacaron **0,09**: sus términos distintivos son `monoclonal, antibody,
human, blocking` — vocabulario de farmacología general que la respuesta no llegó a
usar. Doc 5 aportaba `dose regimens, signs, symptoms` y sí casó. O sea: recuperación
correcta **y** reparto de citas correcto; lo que fallaba era el letrero.

### Cambiado — el prompt pide ahora atribución por estudio, no "multiple studies"
Se añade a `_TASK_DIRECTIVE` y a `_ANSWER_SYSTEM` que cada frase abra con el
**diseño y la población** del estudio ("a meta-analysis of adults", "a pediatric
trial") y que, cuando dos documentos toquen el mismo endpoint, se diga si
**coinciden o discrepan**. Objetivo: que la respuesta se lea como *"este paper dice
A, mientras que aquel dice B"* en vez de un puré de "los estudios demuestran".

**Funciona a medias, y conviene decirlo.** La atribución sí entró (ahora escribe *"A
systematic review and network meta-analysis of RCTs involving 3.679 patients…"*),
pero en 3 de 3 ejecuciones el modelo **se ancla a UN solo documento**. Y hubo que
retroceder: una primera versión añadía la regla *"usa al menos TRES documentos y
nunca más de dos frases seguidas del mismo"* y **2 de 3 ejecuciones se rindieron**
("The provided CONTEXT is insufficient…"). Lección: un 8B tiene un presupuesto de
instrucciones limitado, y una regla de RECUENTO se lo come entero. Se revirtió.

### Medido — el anclaje a un documento NO es sesgo de posición
Hipótesis razonable: el modelo se pega al `[Doc 5]` porque es el último bloque del
contexto, pegadito a la pregunta (sesgo de recencia). **Refutada.** Se invirtió el
orden de los bloques dejando cada etiqueta con su paper (Doc 5 primero, Doc 1
último) y el modelo **siguió anclándose al Doc 5** en las dos ejecuciones. Es el
CONTENIDO: el Doc 5 es un meta-análisis en red con 3.679 pacientes y todas las
pautas de dosis — el documento con más "forma de respuesta" de los cinco.
Consecuencia práctica: **reordenar el contexto no arregla nada**, y la vía barata
queda descartada. Anotado en `TASKS.md` con las alternativas que quedan.

Y un apunte que explica bastante: de los 5 papers recuperados, 4 son revisiones o
meta-análisis que dicen casi lo mismo. **Sin diversidad en la recuperación no puede
haber contraste en la redacción**, por bueno que sea el redactor. Puede que el
arreglo de verdad esté en recuperar 5 papers DISTINTOS (tipo MMR), no en pedirle
al LLM que contraste cinco versiones del mismo documento.

### Medido — la respuesta NO es determinista pese a `temperature: 0.0`
Dos ejecuciones seguidas de la misma pregunta dieron respuestas distintas, y una de
ellas arrancó en modo examen. Ollama no garantiza determinismo. **Importante para la
memoria del TFM:** no se puede afirmar que el sistema sea reproducible frase a frase;
lo reproducible es la RECUPERACIÓN (misma pregunta → mismos 5 papers, mismos scores).

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
