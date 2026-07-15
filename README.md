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

## 🔬 Resultados: ¿por qué un embedding biomédico? (MedCPT vs OpenAI)

La pregunta central del proyecto: para buscar evidencia médica, ¿merece la pena un
embedding **especializado y local** (**MedCPT**, de NCBI) frente al **generalista de
Centivence** (**text-embedding-3-small** de OpenAI, vía API)?

**Experimento controlado:** mismos ~8.900 fragmentos de PubMed, mismo troceado, mismas
preguntas. Lo *único* que cambia es el modelo de embedding, así que cualquier diferencia
es atribuible a él. Dos niveles de dificultad:

- **Básico** — la pregunta **nombra** el fármaco (control de sanidad; casi cualquier
  modelo acierta).
- **Difícil** — la pregunta usa **mecanismo o sinónimo** ("anticuerpo anti-IL-13",
  "eczema") **sin nombrar** el fármaco. Aquí es donde un modelo que *entiende* biomedicina
  debería destacar.

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

## 📁 Estructura del proyecto

| Ruta | Qué es | Módulo del curso |
|------|--------|------------------|
| `config.py` | Parámetros centrales (enfermedad, fármacos, rutas, modelos) | — |
| `check_setup.py` | Comprobación rápida de que tu entorno funciona | — |
| `src/ingestion.py` | Descargar evidencia de PubMed y ClinicalTrials.gov | 2-3 (Datos) |
| `src/processing.py` | Limpiar, trocear (chunks) y vectorizar | 2-3 + 8 |
| `src/rag.py` | Búsqueda en ChromaDB + RAG estricto con citas | 8 (RAG) |
| `src/scout.py` | Agente de fallback que rellena vacíos de evidencia | 9 (Agentes) |
| `src/evaluation.py` | Comparar modelo biomédico local vs LLM generalista | 12 (Ciclo de vida) |
| `app/streamlit_app.py` | Interfaz de chat con citas | 11 (Infra) |
| `data/bronze` | Datos crudos (JSON/XML/PDF originales) | — |
| `data/silver` | Texto limpio y troceado | — |
| `data/chroma` | Base de datos vectorial (ChromaDB) | — |

> Todas las fases (1–5) están **implementadas y funcionando** de punta a punta.

---

## ⚙️ Puesta en marcha (Windows)

1. **Instala Python 3.11+** desde https://www.python.org/downloads/
   (marca la casilla *"Add Python to PATH"* durante la instalación).
2. **Abre esta carpeta en VS Code**: `Archivo → Abrir carpeta…` y elige la carpeta `MIA`.
3. **Crea un entorno virtual** (aísla las librerías del proyecto). En la terminal de VS Code:
   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   ```
4. **Instala las dependencias**:
   ```powershell
   pip install -r requirements.txt
   ```
5. **Comprueba que todo arranca**:
   ```powershell
   python check_setup.py
   ```
6. **(Más adelante) Instala Ollama** para el modelo local: https://ollama.com/download

### Extensiones de VS Code recomendadas
- *Python* (Microsoft)
- *Jupyter* (para notebooks de pruebas)

---

## 🗺️ Hoja de ruta (borrador, ~1 mes)

- **Fase 1 — Datos**: `ingestion.py` (bajar ensayos de dermatitis atópica) → `processing.py` (trocear + vectorizar).
- **Fase 2 — RAG con citas**: `rag.py` (buscar + responder citando la fuente) sobre el modelo local.
- **Fase 3 — Agente Scout**: `scout.py` (detectar vacíos → consultar APIs → importar).
- **Fase 4 — Validación**: `evaluation.py` (biomédico local vs generalista).
- **Fase 5 — Interfaz**: `app/streamlit_app.py` (chat demostrable).

*Estado actual: las cinco fases están implementadas y probadas. La evaluación de embeddings
(MedCPT vs OpenAI, ver arriba) valida la elección del modelo biomédico local.*
