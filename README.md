# MIA · Medical Intelligence Agent

> Un motor de inteligencia científica **soberano, auditable y 100% local** para Centivence.
> Proyecto Capstone — *Desarrollo IA 10X*.

MIA permite interrogar literatura biomédica en lenguaje natural y obtener respuestas
**con la fuente exacta de cada dato**, ejecutándose **sin enviar información fuera del
ordenador**. Cuando no encuentra evidencia en local, un agente sale a buscarla a fuentes
oficiales (PubMed y ClinicalTrials.gov). Caso de uso: **dermatitis atópica** y sus
fármacos competidores.

---

## 🧠 Cómo funciona (en una frase)

Un "bibliotecario médico" privado: trabaja a puerta cerrada, **cita siempre la página
exacta** de cada dato y, si no tiene la información, **sale a buscarla al momento**.

## 🏗️ Arquitectura (visión general)

```
                 ┌─────────────────────────────────────────────┐
   Pregunta  →   │  Interfaz (Streamlit)  ──  app/streamlit_app │
                 └───────────────┬─────────────────────────────┘
                                 ▼
                 ┌─────────────────────────────────────────────┐
                 │  RAG estricto + citas      ──  src/rag.py     │
                 │  (busca en ChromaDB e inyecta contexto al LLM)│
                 └───────┬───────────────────────────┬──────────┘
                         ▼                            ▼
            ¿hay evidencia local?            LLM biomédico LOCAL
              │ sí          │ no             (OpenBioLLM vía Ollama)
              ▼             ▼
        responde      Agente Scout  ──  src/scout.py
        con citas     (PubMed / ClinicalTrials.gov → importa e indexa)
```

**El corpus actual:** 3.160 documentos (2.971 de PubMed + 189 de ClinicalTrials.gov)
troceados en **8.920 fragmentos** vectorizados con MedCPT (768 dimensiones).

---

## 🔬 Resultados: ¿por qué un embedding biomédico? (MedCPT vs OpenAI)

La pregunta central del proyecto: para buscar evidencia médica, ¿merece la pena un
embedding **especializado y local** (**MedCPT**, de NCBI) frente al **generalista de
Centivence** (**text-embedding-3-small** de OpenAI, vía API)?

Se responde en **dos capas**, porque son preguntas distintas.

### Capa 1 — ¿ENTIENDE el modelo el campo semántico biomédico?

Geometría pura del espacio de embeddings: **no toca el corpus, ni ChromaDB, ni el LLM**.
Se mide con **tripletes** (ancla, positivo, negativo): el modelo acierta si acerca más el
positivo que el negativo. Solo cuenta el *orden*, así que es comparable entre modelos con
escalas distintas.

| Modelo | Sinónimos | **Mecanismo→fármaco** | Clase | GLOBAL (AUC) |
|--------|:---------:|:---------------------:|:-----:|:------------:|
| **MedCPT** (biomédico, local — MIA) | 1.000 | **0.833** | 1.000 | **0.909** (0.876) |
| OpenAI 3-small (generalista — Centivence) | 1.000 | 0.667 | 1.000 | 0.818 (0.810) |
| bge-small (generalista local) | 1.000 | 0.667 | 0.500 | 0.727 (0.810) |

> El nivel **Mecanismo→fármaco** (*"¿sabe que anti-IL-13 va con tralokinumab?"*) es el que
> importa: es donde un modelo entrenado en biomedicina debería destacar, y destaca.
> Detalle en [`data/semantics_triplets.csv`](data/semantics_triplets.csv).

### Capa 2 — ¿RECUPERA el paper correcto del corpus?

**Experimento controlado:** mismos ~8.900 fragmentos, mismo troceado, mismas preguntas. Lo
*único* que cambia es el modelo de embedding, así que cualquier diferencia es atribuible a
él. Dos niveles de dificultad:

- **Básico** — la pregunta **nombra** el fármaco (control de sanidad).
- **Difícil** — la pregunta usa **mecanismo o sinónimo** ("anticuerpo anti-IL-13",
  "eczema") **sin nombrar** el fármaco.

| Modelo | Nivel | precision@5 | hit@1 | MRR |
|--------|-------|:-----------:|:-----:|:---:|
| **MedCPT** (biomédico, local — MIA) | Básico | **1.000** | **1.000** | **1.000** |
| **MedCPT** (biomédico, local — MIA) | Difícil | 0.700 | **0.875** | **0.938** |
| OpenAI 3-small (generalista — Centivence) | Básico | 0.969 | **1.000** | **1.000** |
| OpenAI 3-small (generalista — Centivence) | Difícil | **0.775** | 0.625 | 0.792 |

> `precision@5`: fracción de los 5 documentos recuperados que son del fármaco correcto ·
> `hit@1`: ¿el **primer** documento es el correcto? · `MRR`: cómo de arriba aparece el
> primer documento correcto (premia rankear bien).
> Detalle pregunta a pregunta en [`data/evaluation_embeddings.csv`](data/evaluation_embeddings.csv).

**Lectura:** en preguntas fáciles ambos son casi perfectos. En las **difíciles** aparece el
matiz importante: OpenAI mete algún documento relevante *más* en el top-5
(precision@5 0.775 vs 0.700), pero **MedCPT gana con claridad en el ranking** — coloca el
fármaco correcto **en primera posición** mucho más a menudo (hit@1 **0.875 vs 0.625**;
MRR **0.938 vs 0.792**). En consultas por mecanismo como *"anticuerpo dirigido al receptor
IL-4α"* o *"terapia anti-IL-31 para el picor"*, MedCPT sitúa **dupilumab** y **nemolizumab**
como primer resultado, mientras que OpenAI los deja más abajo.

**Por qué esto importa para MIA:** MIA es un sistema con **citas**, en el que la primera
fuente mostrada es la que el clínico lee primero. Un modelo que pone la evidencia correcta
*arriba del todo* (mejor hit@1 y MRR) es más valioso aquí que uno que recupera un poco más
de material relevante pero peor ordenado — y lo hace **en local, sin enviar datos a una API
externa**, que es justamente la soberanía de datos que MIA aporta sobre Centivence.

*Limitación (declarada para el TFM):* la relevancia se mide de forma automática ("¿el
documento es del fármaco correcto?"), no con juicio clínico paper a paper. Es objetivo y
reproducible, pero un siguiente paso de mayor rigor sería una validación **humana y a
ciegas** de una muestra.

### Y el LLM que redacta, ¿gana también?

Fase 4: mismo contexto recuperado a OpenBioLLM (biomédico) y a llama3:8b (generalista, la
base sobre la que se afinó → comparación justa), puntuados por un **juez neutral**
(qwen2.5:7b) con una rúbrica 1-5 sobre 15 preguntas.

| Modelo | Fidelidad de cita | Corrección clínica | Jerga | Alucinación | Tono | **Media** |
|--------|:---:|:---:|:---:|:---:|:---:|:---:|
| Biomédico (OpenBioLLM) | 3.40 | **4.38** | **4.40** | **4.21** | **4.67** | **4.16** |
| Generalista (llama3:8b) | **4.13** | 4.00 | 4.00 | 4.20 | 4.40 | 4.15 |

**Empate técnico en media**, con ventaja del biomédico en todo *salvo* fidelidad de cita.
Ese 3.40 es esperable y está mitigado por diseño: OpenBioLLM coloca mal las `[Doc N]`, así
que **MIA no se fía de él** — las citas las reparte y valida después, de forma determinista,
[`src/citations.py`](src/citations.py). La conclusión que importa: **la apuesta por un motor
soberano no sacrifica calidad a cambio de privacidad**.

---

## 📁 Estructura del proyecto

| Ruta | Qué es | Módulo del curso |
|------|--------|------------------|
| `config.py` | Parámetros centrales (enfermedad, fármacos, rutas, modelos) | — |
| `setup.bat` / `setup.ps1` | **Instalación guiada** de primera vez (venv, dependencias, modelos), paso a paso | 11 (Infra) |
| `run.bat` / `run.ps1` | Lanzador de un clic: comprueba el entorno y abre la app | 11 (Infra) |
| `check_setup.py` | Comprobación rápida de que tu entorno funciona | — |
| `src/ingestion.py` | Descargar evidencia de PubMed y ClinicalTrials.gov | 2-3 (Datos) |
| `src/processing.py` | Limpiar, trocear (chunks) y vectorizar | 2-3 + 8 |
| `src/embeddings.py` | Capa de embeddings intercambiable (MedCPT / bge / OpenAI) | 5-6 |
| `src/rag.py` | Búsqueda en ChromaDB + RAG estricto con citas | 8 (RAG) |
| `src/citations.py` | Reparto y validación **deterministas** de las citas | 8 |
| `src/outcomes.py` | Cifras (EASI, IGA) extraídas con regex, nunca por el LLM | 8 |
| `src/scout.py` | Agente de fallback que rellena vacíos de evidencia | 9 (Agentes) |
| `src/triplet_agent.py` | Agente catalogador de tripletes, con abstención | 9 (Agentes) |
| `src/verdict.py` | Veredicto en vivo: qué embedding entendió mejor la pregunta | 12 |
| `src/evaluation.py` | Comparar modelo biomédico local vs LLM generalista | 12 (Ciclo de vida) |
| `src/report.py` | Informe de evidencia exportable (HTML autónomo → PDF) | 11 |
| `src/status.py` | Panel de estado del sistema (Ollama, modelos, corpus) | 11 |
| `app/streamlit_app.py` | Interfaz de chat con citas | 11 (Infra) |
| `app/pages/1_Comparativa…` | MIA vs Centivence lado a lado, con veredicto | 12 |
| `data/bronze` | Datos crudos (JSON/XML originales) | — |
| `data/silver` | Texto limpio y troceado | — |
| `data/chroma` | Base de datos vectorial (ChromaDB) | — |
| `data/corpus_manifest.csv` | **Censo del corpus indexado** (reproducibilidad) | — |

> Todas las fases (1–5) están **implementadas y funcionando** de punta a punta, más la
> capa **MIA 1.0** (informe exportable, panel de estado, robustez, lanzador de un clic).

---

## ⚙️ Puesta en marcha (Windows)

> El proyecto vive en **`C:\dev\MIA`**. **No lo muevas a OneDrive**: allí la base vectorial
> queda como "archivo en la nube" y se vuelve lenta e insegura.

### Si ya está montado

Doble clic en **`run.bat`**. Comprueba el entorno, avisa si Ollama no está en marcha y abre
la app en el navegador.

### Desde cero (equipo nuevo): instalación guiada

Solo dos programas los instalas tú (son de terceros y no los instalamos a tus espaldas):

1. **Python 3.12** — https://www.python.org/downloads/ (marca *"Add python.exe to PATH"*).
2. **Ollama** — https://ollama.com/download (siguiente, siguiente; se queda en la bandeja).

Después, **doble clic en `setup.bat`**. Se abre una ventana de terminal que va paso a paso
(8 pasos) y **pregunta antes de cada descarga grande**, diciendo cuánto ocupa y dónde queda:

| Paso | Qué hace | Descarga |
|------|----------|----------|
| 1-2 | Localiza Python y crea el entorno virtual `.venv` | — |
| 3 | Instala las dependencias fijadas (`requirements.lock.txt`) | 1,6 GB |
| 4 | Crea `.env` vacío a partir de `.env.example` (no hay que rellenar nada) | — |
| 5 | Comprueba que Ollama responde (si está instalado pero parado, lo arranca) | — |
| 6 | Descarga el modelo biomédico OpenBioLLM 8B con `ollama pull` | 4,9 GB |
| 7 | Descarga los encoders de MedCPT (embeddings) desde HuggingFace | 0,8 GB |
| 8 | Informa de que el corpus está **vacío**: lo eliges tú en la app | 0 |

Si falta Python u Ollama, el script lo dice, da el enlace y se detiene; al volver a
ejecutarlo continúa donde se quedó. Se puede lanzar tantas veces como haga falta: lo que
ya está hecho lo salta con un `[OK]`.

> La primera vez Windows puede mostrar *"Windows protegió su PC"* porque el archivo no
> está firmado: pulsa *Más información → Ejecutar de todas formas*.

Al terminar, **doble clic en `run.bat`** abre MIA. Como el corpus está vacío, la app lo avisa
y te lleva a la pestaña **Build corpus**: escribe la enfermedad, pulsa *Suggest drugs*, elige
los fármacos y construye el corpus (de 10 a 60 minutos según la cobertura). A partir de ahí,
pregunta.

> **Nota sobre el corpus de la tesis.** Un usuario que elija *dermatitis atópica* reconstruye
> el corpus desde PubMed y ClinicalTrials; obtiene uno parecido pero **no idéntico** al de la
> memoria (parte del original vino de un export manual que ya no existe, ver `CLAUDE.md` §8).

<details>
<summary>Instalación manual (los mismos pasos, a mano)</summary>

```powershell
ollama pull koesn/llama3-openbiollm-8b:q4_K_M      # solo este para el producto
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt   # entorno exacto (135 paquetes)
copy .env.example .env
.\.venv\Scripts\python.exe check_setup.py
```

`requirements.txt` lleva solo las dependencias directas, también fijadas (`==`); el lock
reproduce el árbol completo verificado el 31-ago-2026. Para la comparativa y la evaluación
(rama `master`) hacen falta además `ollama pull llama3:8b`, `ollama pull qwen2.5:7b` y una
`OPENAI_API_KEY` en `.env`.
</details>

> ⚠️ Las preguntas hay que hacerlas **en inglés**: el corpus y el LLM biomédico lo son, y
> OpenBioLLM degenera si se le habla en español. Ver `CLAUDE.md` §6.

### Extensiones de VS Code recomendadas
- *Python* (Microsoft)
- *Jupyter* (para notebooks de pruebas)

---

## 🧬 Usar MIA con OTRA enfermedad (perfiles de dominio)

> **Desde la app:** pestaña **Build corpus** (enfermedad → *Suggest drugs* → cobertura → *Build*).
> Los perfiles creados quedan guardados en `domains/` y se cambian en la pestaña MIA.
>
> **Guía paso a paso para usuarios:** [`docs/GUIA_NUEVA_ENFERMEDAD.md`](docs/GUIA_NUEVA_ENFERMEDAD.md)
> — cómo elegir la enfermedad, sus 5 fármacos principales (`suggest_drugs.py` los propone
> con datos de ClinicalTrials.gov), mecanismos y endpoints; la descarga automática y la
> vía manual con un export de PubMed; y dónde queda cada cosa.

MIA se construyó y validó sobre la dermatitis atópica, pero **no está atada a ella**.
Desde el 3-sep-2026 cada patología es un **perfil de dominio** en `domains/<slug>.json`
(enfermedad, sinónimos, fármacos por clase, mecanismos, endpoints de eficacia, preguntas
de ejemplo) con **su propio corpus** (colección de ChromaDB) y sus propias descargas
(`data/bronze/<slug>/`). Varias patologías conviven en la misma instalación y se cambia
de una a otra **desde la barra lateral de la app**, sin reiniciar.

Para crear un perfil nuevo y descargar/indexar su evidencia con un solo comando:

```powershell
.\.venv\Scripts\python.exe build_corpus.py `
    --disease "Plaque psoriasis" --synonym psoriasis `
    --class "il17_biologics=secukinumab,ixekizumab,bimekizumab" `
    --class "il23_biologics=risankizumab,guselkumab" `
    --class "oral=apremilast,deucravacitinib" `
    --mechanism "il-17=secukinumab,ixekizumab,bimekizumab" `
    --mechanism "il-23=risankizumab,guselkumab" `
    --endpoint "PASI 100" --endpoint "PASI 90" --endpoint "PASI 75" --endpoint "sPGA 0/1" `
    --query "IL-17 inhibitor" --max 50 --activate
```

Qué hace, en orden: escribe `domains/plaque_psoriasis.json` → descarga de PubMed y
ClinicalTrials.gov por cada (enfermedad + fármaco) y por cada `--query` libre → limpia,
trocea e indexa con MedCPT en `mia_plaque_psoriasis_medcpt` → exporta el censo
`data/corpus_manifest_plaque_psoriasis.csv`. Con `--openai` construye además la colección
gemela de OpenAI para la página de comparación. `--profile-only` solo escribe el perfil.

Notas honestas:
- El **umbral de evidencia** (66.0) se calibró con el corpus de dermatitis atópica. Es
  sobre todo una propiedad de MedCPT, así que suele valer, pero **compruébalo** en el
  dominio nuevo con `evaluate_sources.py` antes de fiarte de la puerta anti-alucinación.
- Los **golden sets** de las evaluaciones (`evaluate_embeddings*.py`, `src/evaluation.py`)
  son de dermatitis atópica: miden la tesis del TFM, no el dominio nuevo.
- Los endpoints de eficacia se declaran con su etiqueta (`--endpoint "PASI 75"`); MIA
  deriva sola cómo buscarlos en abstracts y en los títulos de ClinicalTrials.gov. Los
  términos de seguridad genéricos (eventos adversos, discontinuación, infecciones) valen
  para cualquier fármaco; añade los propios del dominio con `--safety-term`.
- El agente Scout sigue funcionando en cualquier dominio: busca `<fármaco> AND <enfermedad
  OR sinónimos>` en las fuentes oficiales e indexa lo nuevo en la colección del perfil.

## 🧪 Pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

`tests/test_pure.py` cubre las funciones puras (guardianes de la generación, reparto y
validación de citas, extracción de cifras, endpoints de ClinicalTrials.gov, perfiles de
dominio) sin necesitar Ollama ni la base vectorial. El mismo comando corre en GitHub
Actions (`.github/workflows/tests.yml`) en cada push.

## 🗺️ Estado y hoja de ruta

- ✅ **Fase 1 — Datos**: `ingestion.py` + `processing.py` (8.920 chunks indexados).
- ✅ **Fase 2 — RAG con citas**: `rag.py` + `citations.py` + `outcomes.py`.
- ✅ **Fase 3 — Agente Scout**: `scout.py`, con búsqueda externa real ejecutada.
- ✅ **Fase 4 — Validación**: `evaluation.py` (biomédico vs generalista, juez neutral).
- ✅ **Fase 5 — Interfaz**: `streamlit_app.py` + página de comparativa.
- ✅ **MIA 1.0**: informe exportable, panel de estado, robustez, lanzador de un clic.
- ✅ **Evaluación de embeddings**: Capa 1 (semántica) y Capa 2 (recuperación).
- ⏳ **Memoria final del TFM** — borrador vivo en [`docs/memoria_tfm.md`](docs/memoria_tfm.md).

Tareas abiertas e ideas: [`TASKS.md`](TASKS.md). Guía técnica interna: [`CLAUDE.md`](CLAUDE.md).
