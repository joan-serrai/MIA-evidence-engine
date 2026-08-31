# MIA — Medical Intelligence Agent
## Memoria del Proyecto Capstone · *Desarrollo IA 10X*

**Autor:** Joan Serra Llorens
**Estado:** borrador vivo · última actualización 31-ago-2026

> **Cómo usar este documento.** Las memorias `.docx` de la carpeta padre
> (`Memoria_inicial_MIA_v2.docx`, 30-jun-2026) son la **entrega preliminar** y solo cubren
> las secciones 1-3. Este fichero es el borrador de la **memoria final**: mantiene lo que
> sigue siendo válido de aquella y añade todo lo construido y medido después (migración a
> MedCPT, evaluación de embeddings en dos capas, MIA 1.0). Se redacta en Markdown para
> poder versionarlo con el código; la exportación a `.docx` se hace al final.

---

## 1. Título y concepto central

**MIA (Medical Intelligence Agent)** es un módulo de inteligencia científica **agéntico,
auditable y 100% local**, concebido como la **evolución soberana de la plataforma
Centivence**. Permite a equipos de Market Access, Medical Affairs y Marketing de la
industria farmacéutica interrogar la literatura biomédica en lenguaje natural y obtener
respuestas con **trazabilidad total**: cada afirmación queda ligada a su fragmento de texto
y a su referencia de origen (PMID de PubMed, número NCT de ClinicalTrials.gov), y todo el
proceso se ejecuta **sin que ni un solo dato salga de la organización**.

Cuando la evidencia local es insuficiente, un **agente "Scout"** consulta en tiempo real las
fuentes oficiales, importa los resultados y auto-abastece la base documental, eliminando el
"muro de información" que hoy obliga al analista a abandonar la herramienta.

El sistema se construye y valida sobre un caso real: la **dermatitis atópica** y sus
principales competidores — biológicos anti-interleucina (dupilumab, tralokinumab,
lebrikizumab, nemolizumab) e inhibidores JAK orales (upadacitinib, baricitinib,
abrocitinib).

> **En una frase:** un "bibliotecario médico" privado y especializado que trabaja a puerta
> cerrada, cita siempre la página exacta de cada dato y, si no la tiene, sale a buscarla al
> momento.

### El punto de partida: Centivence

Centivence es una plataforma de inteligencia para la industria farmacéutica. En su forma
actual se apoya en modelos de lenguaje comerciales alojados en la nube, lo que le da
capacidad y rapidez pero le impone un techo estructural precisamente en el sector más
regulado: **la soberanía del dato**.

MIA es la pieza que Centivence todavía no tiene: un **"modo privado" verificable** que
conserva las capacidades de la plataforma (preguntar en lenguaje natural, sintetizar
literatura, citar fuentes) garantizando que la propiedad intelectual estratégica del cliente
nunca abandona su infraestructura. **MIA no reemplaza a Centivence: la completa por su
flanco más crítico.**

---

## 2. Objetivos

### El problema que resuelve

Tres barreras críticas en un sector altamente regulado:

1. **Privacidad y cumplimiento.** Enviar propiedad intelectual estratégica, datos de ensayos
   o consultas de posicionamiento a APIs externas es inasumible bajo GDPR o HIPAA.
2. **Falta de especialización clínica.** Un modelo generalista corre mayor riesgo de
   alucinación semántica y de un tono poco profesional ante la jerga biomédica (escalas EASI
   o IGA, p-valores, farmacocinética).
3. **El "muro de información".** Si un dato no está cargado, la herramienta se queda muda.

### Objetivo general

Diseñar, construir y validar MIA, un módulo de inteligencia científica agéntico y 100% local
que resuelve esas tres barreras, demostrado sobre un caso real.

### Objetivos específicos y grado de cumplimiento

| # | Objetivo | Estado |
|---|----------|--------|
| 1 | Pipeline de datos local (PubMed + ClinicalTrials → ChromaDB) | ✅ 3.160 docs / 8.920 chunks |
| 2 | Inferencia 100% local (OpenBioLLM vía Ollama) | ✅ |
| 3 | RAG estricto con citas trazables | ✅ + validación determinista |
| 4 | Agente Scout de fallback | ✅ con búsqueda externa real |
| 5 | Validación empírica biomédico vs generalista | ✅ rúbrica + juez neutral |
| 6 | *(deseable)* Interfaz demostrable en Streamlit | ✅ + informe exportable |
| 7 | *(no previsto)* Validación del **embedding** en dos capas | ✅ aportación propia |

El objetivo 7 no estaba en la propuesta inicial. Surgió al detectar que la decisión más
determinante del sistema no era el LLM sino el **modelo de embedding**, y se convirtió en la
aportación más original del trabajo (§5 y §6).

---

## 3. Alcance y viabilidad

El proyecto es viable en el plazo del Capstone porque **no reconstruye Centivence**, sino que
añade y valida la pieza diferencial de la que hoy carece. Tres decisiones lo mantienen
acotado: datos de fuentes públicas y gratuitas; modelo biomédico ya entrenado (sin
reentrenamiento ni fine-tuning); y una única patología.

**Fuera de alcance a propósito:** multi-enfermedad *en caliente*. El índice es específico de
dermatitis atópica; permitir cambiarlo en runtime sin re-ingesta sería deshonesto. Queda como
plantilla configurable documentada (`config.py` es la única fuente de verdad: cambiar de
patología = cambiar ese archivo y re-indexar).

También quedan fuera los componentes de infraestructura pesada (Airflow, Docker, arquitectura
de datos por capas completa), reservados como evolución futura.

---

## 4. Arquitectura y decisiones técnicas

### 4.1 Flujo general

```
Pregunta → Streamlit → RAG estricto (rag.py) → ChromaDB (MedCPT, producto escalar)
                            │
             ¿similitud < 66.0 o falta el fármaco citado?
                            ├── NO → OpenBioLLM local → citas deterministas → respuesta
                            └── SÍ → Scout: PubMed/CT.gov → importa → indexa → reintenta
```

Los datos siguen un patrón **medallón**: `bronze` (crudo de las APIs) → `silver` (limpio y
troceado) → `chroma` (base vectorial, una colección por backend de embeddings).

### 4.2 Las tres decisiones de diseño que definen el proyecto

**(a) Las citas no se le piden al LLM: se calculan.**
OpenBioLLM 8B redacta bien a temperatura 0 pero coloca mal las `[Doc N]` (las omite o las
amontona al final). En lugar de aceptar ese error, MIA lo asume y lo corrige:
`src/citations.py` reparte cada cita a la frase que realmente respalda (por solapamiento de
términos) y **elimina toda cita fuera de rango**. Garantía resultante: *cada cita apunta a
una fuente real, siempre*. Este diseño es también la respuesta a la métrica más baja de la
evaluación (§6.3).

**(b) Los números no los escribe el LLM.**
Las cifras del gráfico de resultados (EASI-75/90/100, IGA 0/1) se extraen del texto
recuperado con expresiones regulares deterministas (`src/outcomes.py`), filtrando intervalos
de confianza y heterogeneidad de meta-análisis. Cada barra del gráfico es rastreable a su
`[Doc N]`. Principio: **si un número puede inventarse, no lo genera el modelo**.

**(c) Abstención bajo incertidumbre.**
La misma filosofía se extiende al agente catalogador: `src/triplet_agent.py` propone con
libertad pero **verifica contra el corpus** antes de emitir veredicto, y si no puede
verificar, **se abstiene**. `src/verdict.py` entonces no declara ganador. Es grounding +
abstención: la señal de madurez que un sector regulado valora.

### 4.3 El hallazgo técnico: MedCPT y la norma del vector

La migración del embedding generalista (`bge-small`) al biomédico **MedCPT** produjo el
episodio más instructivo del proyecto.

MedCPT es un modelo **asimétrico de dos torres** (un encoder para preguntas, otro para
artículos, 768 dimensiones), entrenado por NCBI con 255 millones de pares consulta-artículo
de PubMed.

Al indexarlo **con distancia coseno** (lo estándar), la puerta anti-alucinación dejó de
funcionar. Medido sobre 337 documentos:

| Consulta | Coseno máximo |
|----------|:-------------:|
| Preguntas relevantes (dupilumab, lebrikizumab) | 0.63 – 0.66 |
| **Pregunta ajena ("capital de Francia")** | **0.68** ← más alta |

Una pregunta completamente ajena al dominio puntuaba **más alto** que las relevantes: el
umbral de evidencia era inservible.

**Causa:** MedCPT codifica su confianza en la **norma (magnitud)** del vector, no solo en su
dirección. Normalizar —que es lo que hace la distancia coseno— **tira esa información**.

**Solución:** usar la métrica nativa del modelo, el **producto escalar**
(`config.CHROMA_SPACE = "ip"`, sin normalizar en `embeddings.py`). Con ella la separación es
limpia:

| Consulta | Producto escalar |
|----------|:----------------:|
| Preguntas relevantes (6 fármacos) | 70.1 – 73.3 |
| Preguntas ajenas (Francia, malaria, pan) | 55.8 – 63.7 |

Hueco limpio → **umbral fijado en 66.0**, medido sobre la base completa (8.920 chunks). La UI
recalibra ese producto escalar a un "% de confianza" legible (55→0 %, 80→100 %).

> **Lección transferible:** un modelo de embedding no es una caja negra intercambiable. Su
> métrica de distancia forma parte de su diseño, y adoptarla mal puede desactivar
> silenciosamente una garantía de seguridad del sistema.

---

## 5. Metodología de evaluación

El trabajo evalúa **tres cosas distintas**, deliberadamente separadas.

### 5.1 Capa 1 — ¿el modelo *entiende* biomedicina?

Geometría pura del espacio de embeddings. **No toca el corpus, ni ChromaDB, ni el LLM**: solo
embebe términos sueltos. Se mide con **tripletes** (ancla, positivo, negativo); el modelo
acierta si `sim(ancla, positivo) > sim(ancla, negativo)`.

Como solo cuenta el **orden**, la métrica es comparable entre modelos con escalas distintas
(coseno 0-1 de OpenAI/bge frente a producto escalar ~50-75 de MedCPT). Tres niveles:
Sinónimos (control), **Mecanismo→fármaco** (la tesis) y Clase terapéutica.

Métricas: `triplet_acc` (% de tripletes acertados; 0.5 = azar) y `AUC` (margen de la
separación; 1.0 = perfecta).

*Nota metodológica:* MedCPT es asimétrico, así que se usa en su régimen de entrenamiento —
el ancla (concepto/mecanismo = "consulta") por el Query-Encoder, los candidatos (fármacos =
"documento") por el Article-Encoder. Para bge y OpenAI, simétricos, no aplica.

Script: `evaluate_embeddings_semantics.py`.

### 5.2 Capa 2 — ¿recupera el paper correcto?

Experimento controlado: **mismos** 8.920 fragmentos, **mismo** troceado, **mismas**
preguntas. Lo único que cambia es el modelo de embedding (se re-embeben los mismos chunks con
OpenAI en una colección paralela, `index_openai.py`), así que cualquier diferencia es
atribuible a él.

Dos niveles: **Básico** (la pregunta nombra el fármaco; 13 preguntas, control de sanidad) y
**Difícil** (la pregunta usa mecanismo o sinónimo *sin* nombrarlo; 8 preguntas, donde se
juega la tesis). Métricas: `precision@5`, `hit@1`, `MRR`.

Script: `evaluate_embeddings.py`.

### 5.3 Capa 3 — ¿redacta mejor el LLM biomédico?

Se da el **mismo contexto recuperado** a OpenBioLLM y a `llama3:8b` — que es la base sobre la
que se afinó OpenBioLLM, así que la comparación **aísla el efecto del fine-tuning
biomédico**. Un **juez neutral** (`qwen2.5:7b`, un tercer modelo que no compite) puntúa una
rúbrica de 1 a 5 sobre 15 preguntas: fidelidad de cita, corrección clínica, manejo de jerga,
alucinación y tono.

Script: `src/evaluation.py`.

---

## 6. Resultados

### 6.1 Capa 1 — comprensión semántica

| Modelo | Sinónimos | **Mecanismo→fármaco** | Clase | GLOBAL | AUC |
|--------|:---------:|:---------------------:|:-----:|:------:|:---:|
| **MedCPT** (biomédico, local) | 1.000 | **0.833** | 1.000 | **0.909** | **0.876** |
| OpenAI 3-small (generalista) | 1.000 | 0.667 | 1.000 | 0.818 | 0.810 |
| bge-small (generalista local) | 1.000 | 0.667 | 0.500 | 0.727 | 0.810 |

Los tres aciertan los sinónimos (control de sanidad). La diferencia aparece exactamente donde
se predijo: **Mecanismo→fármaco**, donde MedCPT saca 17 puntos a ambos generalistas. bge
además falla la agrupación por clase terapéutica.

### 6.2 Capa 2 — recuperación

| Modelo | Nivel | precision@5 | hit@1 | MRR |
|--------|-------|:-----------:|:-----:|:---:|
| **MedCPT** | Básico | **1.000** | **1.000** | **1.000** |
| **MedCPT** | Difícil | 0.700 | **0.875** | **0.938** |
| OpenAI 3-small | Básico | 0.969 | **1.000** | **1.000** |
| OpenAI 3-small | Difícil | **0.775** | 0.625 | 0.792 |

En preguntas fáciles ambos son casi perfectos. En las **difíciles** aparece el matiz
importante: OpenAI mete algún documento relevante *más* en el top-5 (0.775 vs 0.700), pero
**MedCPT gana con claridad en el ranking**: coloca el fármaco correcto en primera posición
mucho más a menudo (hit@1 **0.875 vs 0.625**, MRR **0.938 vs 0.792**).

**Por qué esa es la métrica que importa:** MIA es un sistema con citas, y la primera fuente
mostrada es la que el clínico lee primero. Rankear bien vale más que recuperar un poco más de
material desordenado.

### 6.3 Capa 3 — redacción

| Modelo | Fidelidad de cita | Corrección clínica | Jerga | Alucinación | Tono | **Media** |
|--------|:---:|:---:|:---:|:---:|:---:|:---:|
| Biomédico (OpenBioLLM) | 3.40 | **4.38** | **4.40** | **4.21** | **4.67** | **4.16** |
| Generalista (llama3:8b) | **4.13** | 4.00 | 4.00 | 4.20 | 4.40 | 4.15 |

**Empate técnico en media (4.16 vs 4.15)**, con ventaja del biomédico en todo salvo fidelidad
de cita.

**Sobre ese 3.40 — hay que decirlo antes de que lo pregunten.** Es la métrica más baja del
trabajo y es *esperable*: OpenBioLLM está afinado sobre exámenes tipo USMLE y coloca mal los
marcadores `[Doc N]`. Pero **no es un fallo del sistema**, porque MIA no se fía de él: las
citas se reparten y validan después de forma determinista (§4.2a). La métrica evalúa la salida
*cruda* del modelo, no la que ve el usuario. Un trabajo futuro honesto sería re-puntuar la
rúbrica **después** del post-proceso de citas, que es lo que realmente se entrega.

### 6.4 Lectura conjunta

Las tres capas cuentan una historia coherente: el modelo biomédico **entiende** mejor el
dominio (Capa 1), y ese entendimiento **se traduce** en poner la evidencia correcta arriba
(Capa 2), mientras que en la redacción **no se pierde calidad** frente al generalista
(Capa 3). Es decir: **la soberanía del dato no se paga con rendimiento**. Ese es el argumento
que da valor a MIA dentro de Centivence.

---

## 7. Limitaciones

Declaradas de forma explícita, porque un trabajo que no las declara no es creíble:

1. **La relevancia se mide automáticamente.** El criterio de la Capa 2 es "¿el documento es
   del fármaco correcto?", no juicio clínico paper a paper. Es objetivo y reproducible, pero
   un siguiente paso de mayor rigor sería una **validación humana y a ciegas** de una muestra.
2. **Los golden sets son pequeños** (11 tripletes en Capa 1; 21 preguntas en Capa 2 — 13
   básicas y solo **8 difíciles**, que son justamente las que sostienen la conclusión; 15 en
   Capa 3) y etiquetados por una sola persona. Suficiente para detectar diferencias grandes,
   insuficiente para afirmar significación estadística: con 8 preguntas difíciles, la
   diferencia de hit@1 (0.875 vs 0.625) equivale a **2 preguntas**. Ampliar ese set es la
   mejora de rigor más barata y más urgente del trabajo.
3. **Solo se pregunta en inglés.** El corpus y OpenBioLLM lo son; con prompts en español el
   modelo degenera (repite el prompt, responde en modo examen). El Q&A en español vía
   traducción previa queda como mejora futura.
4. **El umbral de evidencia es fijo (66.0).** Está medido sobre la base completa y funciona,
   pero es frágil por diseño: cualquier pregunta de la patología puntúa alto aunque falte el
   fármaco concreto. Se mitiga con la **segunda señal** del Scout (fármaco nombrado que no
   aparece en lo recuperado), no se elimina.
5. **Solo el abstract, no el texto completo.** PubMed da el resumen gratis; el texto completo
   puede ser de pago. MIA lo **señala** (`access: open` / `abstract_only`) pero no hace
   scraping del PDF.
6. **Una sola patología.** La generalización a otras áreas terapéuticas está diseñada
   (`config.py`) pero no demostrada.

---

## 8. Conclusiones

1. **La apuesta soberana no sacrifica calidad.** Media 4.16 vs 4.15 frente al generalista,
   con ventaja biomédica en corrección clínica, jerga y tono — ejecutándose íntegramente en
   local.
2. **La decisión más determinante no era el LLM, sino el embedding.** MedCPT bate a
   `text-embedding-3-small` tanto en comprensión semántica (0.833 vs 0.667) como en calidad de
   ranking (hit@1 0.875 vs 0.625), y además lo hace sin salir del ordenador.
3. **La métrica de distancia forma parte del modelo.** El episodio del coseno frente al
   producto escalar (§4.3) demuestra que una elección aparentemente rutinaria puede desactivar
   en silencio una garantía de seguridad.
4. **La fiabilidad se construye con determinismo, no pidiéndosela al modelo.** Citas
   validadas, cifras extraídas por regex y abstención bajo incertidumbre son las tres piezas
   que hacen a MIA auditable.

## 9. Líneas futuras

- Re-puntuar la rúbrica **después** del post-proceso de citas (§6.3).
- Validación humana y a ciegas de una muestra de la evaluación de recuperación.
- Q&A en español mediante traducción de la pregunta antes del RAG.
- Umbral de evidencia por LLM-juez en lugar de constante calibrada.
- MedCPT con el formato `[título, abstract]` que recomienda su model card.
- Comparativa del tamaño de chunk (500 / 800 / 1000) elegido con datos.
- Infraestructura: contenedores, orquestación y arquitectura de datos completa.

---

## Anexo A · Reproducibilidad

- **Corpus:** 3.160 documentos (2.971 PubMed + 189 ClinicalTrials) → 8.920 chunks, 768 dim.
  Censo completo en `data/corpus_manifest.csv`; PMIDs en `data/corpus_pmids.txt`.
  De ellos, 1.209 son regenerables con `run_phase1.py` y 1.951 provienen de un export manual
  de PubMed cuyo fichero original se perdió al cambiar de equipo — de ahí que el censo sea la
  prueba documental del contenido del índice.
- **Modelos:** MedCPT (`ncbi/MedCPT-{Query,Article}-Encoder`), OpenBioLLM 8B Q4_K_M,
  llama3:8b, qwen2.5:7b, text-embedding-3-small (solo evaluación).
- **Resultados brutos:** `data/semantics_*.csv`, `data/evaluation_embeddings.csv`,
  `data/evaluation_summary.csv`, `data/evaluation_detail.csv`.
- **Entorno:** Windows 11, Python 3.12, Ollama en `localhost:11434`. Detalle en `CLAUDE.md`.
