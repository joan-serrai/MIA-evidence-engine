# MIA — Tareas y posibles acciones

Documento vivo. Anoto aquí lo pendiente, lo que estamos haciendo y las ideas
futuras. Se va actualizando a medida que surgen cosas.

Leyenda de estado: ⏳ pendiente · 🔨 en curso · ✅ hecho · 💡 idea a valorar

> **Última revisión: 8-sep-2026.** Tras añadir la instalación guiada (`setup.bat`),
> a la espera de la prueba del autor como usuario nuevo y de publicar en GitHub.

---

## 🔨 En curso

*(nada en curso ahora mismo — ver Pendientes)*

## 📜 Historial

El registro de lo YA hecho, con el motivo de cada cambio, vive en
[`CHANGELOG.md`](CHANGELOG.md). Este archivo mira al futuro (qué falta);
el CHANGELOG mira al pasado (qué se hizo y por qué).

## ⏳ Pendientes

- **Probar `setup.bat` como usuario nuevo (el autor).** Decidido el 8-sep-2026: antes
  de publicar, el autor repite la instalación desde cero (clon sin `.venv`, corpus
  vacío, construir un corpus desde la app). Lo que se atasque ahí es lo que hay que
  arreglar primero. Después: capturas de pantalla de cada paso para el README.
- **Instalación en Linux/Mac.** `setup.ps1` es solo Windows a propósito (no hay dónde
  probar otro sistema). Cuando alguien lo pida: `setup.sh` con los mismos ocho pasos,
  o al menos la lista manual del README verificada en ese sistema.
- **El modelo usa las citas como sujeto de la frase.** Visto el 4-sep-2026 en
  retinoblastoma: escribió *"Doc 1 and Doc 2 both report…"* y, al quitar las etiquetas
  para recolocarlas, quedó *"Specifically, and both report…"*. `citations.py` debería
  detectar `[Doc N]` seguido de "and/both/reports" y sustituirlo por "one study" /
  "two studies" en vez de borrarlo.
- **Homónimos en PubMed vía `[Title]`.** La cláusula MeSH/título quita casi todo el
  ruido, pero un paper titulado "Retinoblastoma-Positive Breast Cancer" sigue
  entrando (4-sep-2026). Opciones: excluir con `NOT "<disease> protein"[MeSH]` cuando
  el perfil lo declare, o un filtro posterior que exija la enfermedad en las
  condiciones/MeSH del registro.
- **`suggest_drugs` desde la app no filtra soporte.** Filgrastim/mesna salen arriba;
  una lista de exclusión de fármacos de soporte (G-CSF, antieméticos, uroprotectores)
  ahorraría el paso de criterio al usuario.
- **Rama pública `main` sin Centivence.** Decidido el 4-sep-2026: `master` conserva
  todo; `main` se crea sin la página de comparación, sin backend OpenAI ni
  evaluaciones, con README de producto. Pendiente de crear al publicar.
- **Citas frase a frase con corpus pequeños.** Medido el 4-sep-2026 con un perfil de
  prueba (Crohn, 26 documentos): la respuesta salió sin ninguna `[Doc N]` porque las 5
  fuentes hablaban todas del mismo fármaco y el reparto IDF no halló términos
  distintivos. Opciones: bajar `_MIN_SCORE` cuando hay pocas fuentes, o citar la
  fuente de mayor solapamiento aunque no gane por margen, marcándola como "probable".
- **`suggest_drugs.py` no distingue fármacos de comparadores antiguos.** Metotrexato o
  azatioprina salen arriba por volumen histórico de ensayos. Bastaría un `--since 2015`
  (fecha de inicio del ensayo) para sesgar hacia lo actual.
- **Que la respuesta CONTRASTE papers, no que resuma uno.** Objetivo: leer *"un
  ensayo pediátrico reporta A, mientras que un meta-análisis en adultos encuentra
  B; ambos coinciden en C"*. El prompt ya pide atribución por diseño y población
  (2-sep-2026) y eso SÍ entró, pero el modelo **se ancla a un solo documento** en
  3 de 3 pruebas. Vía de prompt agotada: subir la presión con una regla de
  recuento hizo que 2 de 3 ejecuciones se rindieran. ~~Sesgo de posición~~
  **descartado** el 2-sep-2026: invirtiendo el orden del contexto (Doc 5 primero)
  el modelo se sigue anclando al Doc 5 → es el CONTENIDO, no la posición.
  Candidatos que quedan, por orden de coste:
  1. **Redacción por tramos**: una llamada por documento y una de síntesis, en
     vez de pedirle todo de una. Más lento, pero un 8B sigue mejor una
     instrucción simple repetida que una compleja única.
  2. **Diversificar la recuperación** (tipo MMR): hoy los 5 papers recuperados son
     casi el mismo paper cinco veces — 4 de 5 son revisiones/meta-análisis con el
     mismo mensaje. Sin diversidad en la entrada no puede haber contraste en la
     salida, por bueno que sea el redactor.
  3. **Modelo más grande** para la redacción, manteniendo MedCPT en recuperación.
- **Permitir una frase de convergencia con DOS citas.** Hoy `citations.py` asigna
  como mucho un `[Doc N]` por frase, así que *"estos dos artículos demuestran X"*
  no es representable: habría que decidir si una frase puede llevar dos citas sin
  volver al amontonamiento que ese módulo existe para evitar.
- **Reponer algo en el hueco del "% de afinidad" (opcional).** Se retiró el
  2-sep-2026 por engañoso. La tarjeta de fuente quedó sin nada a la derecha. Si
  se quiere llenar, que sea con un dato que el lector pueda usar sin
  malinterpretarlo: tipo de estudio, tamaño muestral o población — nunca un
  número de "confianza".
- **Decidir el umbral por fuente (opción A).** Ya está el instrumento
  (`evaluate_sources.py`) y la medición hecha. Resultado: CT.gov separa mejor
  (+3,00 de hueco) que PubMed (+1,79), así que un umbral propio ~62-63 para
  CT.gov es defendible y recuperaría 4 de 33 preguntas que hoy se pierden. PERO
  el margen de PubMed con controles duros es fino: **no bajar su 66,0 sin más
  medición**. Cambio pendiente de decisión, no de código.
- **El umbral 66,0 se calibró con controles fáciles.** Frente a preguntas médicas
  de otras patologías el hueco es de solo +1,79. Merece una recalibración seria
  con el control negativo nuevo, y decirlo en la memoria.
- **Los ensayos de CT.gov no ganan en el ranking.** Sus resultados ya están
  indexados (Capa 1 hecha), pero MedCPT casi siempre prefiere las revisiones de
  PubMed: hay 2.971 papers frente a 189 ensayos, y un abstract se parece más a
  una pregunta en lenguaje natural que la prosa de un registro. El dato sale
  cuando la pregunta es de corte "ensayo", pero no domina. Arreglarlo pide
  **recuperación híbrida** (p. ej. dar un empujón a los documentos con cifras
  estructuradas cuando la pregunta es de seguridad), no más datos.
- **El aviso de "paper de pago" no salta nunca.** Los 3.160 documentos indexados
  tienen `access = None` (se indexaron antes de que existiera el campo) y
  `rag.py` hace `meta.get("access") or "open"`, así que todo se marca como
  abierto. Arreglarlo exige reprocesar el corpus (~11 min con la CPU actual).

- **Memoria final del TFM.** Las `.docx` de la carpeta padre son la entrega
  *preliminar* (30-jun-2026, solo secciones 1-3) y van muy por detrás: no
  mencionan MedCPT, ni la evaluación de embeddings, ni MIA 1.0. Hay que
  reescribirlas con todo lo medido después. Borrador vivo en
  `docs/memoria_tfm.md`.
- **Explicar la fidelidad de cita (3.4/5).** Es la métrica más baja del
  biomédico en la Fase 4. Es defendible —por eso las citas las coloca
  `src/citations.py` de forma determinista, no el LLM— pero hay que decirlo
  explícitamente en la memoria antes de que lo pregunten en la defensa.
- **Subir el repositorio a GitHub.** No hay remoto. Pasos: crear el repo vacío en
  github.com (sin README), `git remote add origin …`, `git push -u origin master`.
  Antes: commitear el bloque del 3-sep-2026 y elegir licencia (recomendada MIT).
  El workflow de Actions (`.github/workflows/tests.yml`) arrancará solo.
- **Reprocesar el corpus para rellenar `access`.** 8.617 chunks de PubMed tienen
  `access = None` → el aviso de "paper de pago" nunca salta (ver abajo). Tras
  reprocesar, volver a ejecutar `index_openai.py` para mantener la paridad.
- **El Scout rompe la paridad MedCPT/OpenAI.** Cuando importa evidencia nueva solo
  la indexa en la colección de producto (MedCPT). La colección OpenAI se queda
  atrás y la comparativa deja de ser un experimento controlado. Opciones: que el
  Scout embeba también con OpenAI si hay clave (coste ínfimo), o que la página de
  comparación avise cuando los recuentos difieren.
- **Cobertura de citas baja en respuestas genéricas.** Medido 3-sep-2026 con la
  pregunta de demo: 0-1 citas por respuesta en 3 ejecuciones (la respuesta es
  correcta pero genérica — "improves signs and symptoms" — y el reparto por
  solapamiento, conservador a propósito, no encuentra términos distintivos). Una
  respuesta sin citas muestra el aviso ámbar en la app. Vías: pedir cifras
  concretas con más fuerza (ya se hace), o relajar `_MIN_SCORE` solo cuando la
  frase menciona el fármaco y hay una única fuente candidata.
- **El LLM no detecta "please provide the question" como fallo**… ya sí (3-sep-2026,
  `_GIVEUP_RE`), pero el patrón es una lista de frases: conviene medir con más
  preguntas qué otras formas de rendición existen.
- **Calibrar el umbral por dominio.** Los perfiles nuevos heredan el 66.0 de
  dermatitis atópica. `evaluate_sources.py` debería aceptar `--domain` y un
  golden set mínimo por perfil (p. ej. generado de `example_questions`).
- **Lint.** Sigue sin haber. `ruff` con la config por defecto sería suficiente y
  cabe en el mismo workflow de Actions.

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

### Auditoría y generalización (3-sep-2026)
- **Regresión del guardián de examen** corregida (`_strip_exam_prefix` + `_GIVEUP_RE`):
  la pregunta de demo pasó de fallar 4/6 a 3/3 correctas.
- **XSS** en la app: la respuesta del LLM se escapa antes de pintarla como HTML.
- **Perfiles de dominio** (`domains/*.json`, `build_corpus.py`, selector en la app):
  MIA sirve para cualquier patología; el perfil original conserva sus colecciones.
- **pytest** (`tests/test_pure.py`, 26 pruebas) + **GitHub Actions** + `.gitattributes`.
- Auditoría de dependencias (`pip-audit`): 4 CVE en chromadb 1.5.9, todas del modo
  servidor/RBAC; MIA usa `PersistentClient` embebido → no expuesto. Sin fix publicado.

### Mantenimiento
- Script `ver_db.py` para inspeccionar ChromaDB (censo, ejemplos, búsqueda real).
- `export_corpus_manifest.py` — censo reproducible del corpus indexado
  (ver "Reproducibilidad del corpus" en `CLAUDE.md`).
