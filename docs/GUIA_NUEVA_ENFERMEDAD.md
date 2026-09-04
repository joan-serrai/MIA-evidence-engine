# Guía: usar MIA con TU enfermedad, tus fármacos y tus mecanismos

> Para quien se descarga MIA de GitHub y quiere interrogar la evidencia de **otra
> patología** distinta de la dermatitis atópica. No hace falta tocar código.

---

## 0. Lo primero: cómo entra la información en MIA

Conviene aclararlo antes de nada, porque cambia el modo de trabajar:

**MIA no lee PDFs ni archivos sueltos que dejes en una carpeta.** Su "biblioteca" es una
base de datos vectorial (`data/chroma/`) que se construye a partir de dos fuentes públicas
y gratuitas:

| Fuente | Qué se coge | Qué NO se coge |
|--------|-------------|----------------|
| **PubMed** | El *abstract* (resumen) de cada artículo, con título, autores, PMID y DOI | El texto completo del paper |
| **ClinicalTrials.gov** | La ficha del ensayo (título, resumen, condiciones) **y sus resultados publicados** (eventos adversos y eficacia por brazo) | Nada más: la ficha es pública por diseño |

Hay **dos vías** para llenar esa biblioteca, y las dos acaban en el mismo sitio:

- **Vía A · automática (recomendada).** Le dices a MIA la enfermedad y los fármacos, y
  `build_corpus.py` descarga, limpia, trocea, vectoriza e indexa todo él solo. No hay que
  exportar nada a mano.
- **Vía B · manual.** Exportas tú mismo los resultados de una búsqueda de PubMed en formato
  *"Abstract (text)"* y se los das a `ingest_desktop_set.py`. Útil cuando quieres controlar
  exactamente qué artículos entran (una búsqueda muy afinada, un filtro por fecha, etc.).

Lo que **no se admite hoy**: PDFs, Word, texto completo de artículos, ni exports manuales de
ClinicalTrials.gov (los ensayos solo entran por la vía automática).

### Dónde acaba cada cosa

| Qué | Dónde | ¿Se sube a git? |
|-----|-------|-----------------|
| El **perfil** de tu enfermedad (nombre, fármacos, mecanismos, endpoints) | `domains/<slug>.json` | Sí: es pequeño y reproducible |
| Lo descargado en crudo (JSON de CT.gov, XML de PubMed) | `data/bronze/<slug>/` | No (regenerable) |
| Los fragmentos limpios | `data/silver/` | No |
| La base vectorial (una colección por enfermedad: `mia_<slug>_medcpt`) | `data/chroma/` | No (pesa cientos de MB) |
| El **censo** de lo indexado (un documento por fila, con su URL) | `data/corpus_manifest_<slug>.csv` | Sí: es la prueba de qué hay dentro |

`<slug>` es el nombre corto que MIA deriva de la enfermedad: *"Plaque psoriasis"* →
`plaque_psoriasis`.

---

## 1. Antes de empezar (una sola vez)

Sigue la sección *"Puesta en marcha"* del `README.md`: Python 3.12, el entorno virtual con
las dependencias, y **Ollama** con el modelo biomédico descargado. Para descargar evidencia
hace falta **conexión a internet**; para preguntar, no.

Todos los comandos de esta guía se ejecutan desde la carpeta del proyecto con el Python del
entorno virtual:

```powershell
.\.venv\Scripts\python.exe <script> ...
```

---

## 2. Paso 1 · Decide qué vas a cargar

Necesitas cuatro cosas. Todas **en inglés**, porque las fuentes y el modelo lo son.

### 2.1 La enfermedad (y sus sinónimos)

El nombre tal como aparece en PubMed / ClinicalTrials.gov: `"Plaque psoriasis"`,
`"Crohn disease"`, `"Rheumatoid arthritis"`, `"Type 2 diabetes mellitus"`. Si la
literatura usa varios nombres, añádelos como sinónimos: MIA buscará `(nombre OR sinónimo)`.

### 2.2 Los fármacos (los 5 más importantes, o los que quieras)

Usa siempre el **nombre genérico** (DCI / INN), nunca la marca: `secukinumab`, no
`Cosentyx`. Es lo que indexan las fuentes y lo que MIA usa para etiquetar cada documento.

Si no conoces el área, **MIA te lo dice con datos**. `suggest_drugs.py` consulta el registro
oficial de ensayos y cuenta en cuántos aparece cada fármaco para esa enfermedad:

```powershell
.\.venv\Scripts\python.exe suggest_drugs.py --disease "Plaque psoriasis" --top 12
```

Salida real (4-sep-2026):

```
 #  fármaco / intervención           ensayos fase 3/4  fase máx.
------------------------------------------------------------------
 1  secukinumab                           48       39  4
 2  apremilast                            40       28  4
 3  etanercept                            39       34  4
 4  adalimumab                            34       26  4
 5  ustekinumab                           28       24  4
 6  risankizumab                          23       18  4
 ...
Comando sugerido (revísalo: agrupa por clase y quita lo que no sea un fármaco):
  .\.venv\Scripts\python.exe build_corpus.py --disease "Plaque psoriasis" \
      --class "principales=secukinumab,apremilast,etanercept,..." --max 50 --activate
```

Con `--phase3` cuenta solo ensayos de fase 3/4 (los que definen la práctica clínica).
**Revisa la lista con criterio**: a veces sale un código de molécula (`FP187`) o un fármaco
antiguo que ya no interesa. Tú decides los 5.

### 2.3 Los mecanismos de acción

Dos formas de meter el mecanismo, que se complementan:

- `--mechanism "il-17=secukinumab,ixekizumab"` — dice a MIA **qué fármacos pertenecen a qué
  mecanismo**. Lo usa la página de comparación MedCPT vs OpenAI (las preguntas "difíciles",
  que nombran el mecanismo y no el fármaco) y el catalogador de tripletes.
- `--query "IL-17 inhibitor"` — añade una **búsqueda libre extra** en la descarga. Trae los
  papers que hablan del mecanismo sin nombrar ningún fármaco concreto (revisiones de la
  vía IL-17, por ejemplo), que la búsqueda por fármaco no encontraría.

### 2.4 Los endpoints de eficacia (para el gráfico de cifras)

MIA extrae cifras del texto con reglas deterministas y las pinta en un gráfico, sin que el
LLM invente números. Para eso tiene que saber **cómo se llama la tasa de respuesta** en tu
enfermedad. Se escribe como *nombre + número*:

| Enfermedad | Endpoints típicos |
|-----------|-------------------|
| Psoriasis | `PASI 75`, `PASI 90`, `PASI 100`, `sPGA 0/1` |
| Artritis reumatoide | `ACR20`, `ACR50`, `ACR70` |
| Dermatitis atópica | `EASI 75`, `EASI 90`, `IGA 0/1` |
| Colitis ulcerosa | `Mayo 0/1` (remisión clínica) |

¿Cómo saber cuál es el tuyo? Abre en ClinicalTrials.gov un ensayo de fase 3 de la enfermedad
y mira su *Primary Outcome*: el nombre que ahí aparece es el que hay que darle. Si tu
endpoint no es una tasa de respuesta (una media de HbA1c, por ejemplo), MIA responde igual y
cita igual; solo el gráfico de barras quedará vacío.

---

## 3. Paso 2 · Crear el perfil y descargar la evidencia (vía A)

> **Sin terminal.** Desde el 4-sep-2026 todo este paso se puede hacer desde la propia app,
> en la pestaña **Build corpus**: escribes la enfermedad, pulsas *Suggest drugs* para que
> te proponga los fármacos más estudiados, marcas los que quieras, añades mecanismos y
> endpoints, eliges la cobertura (*Quick* 30 · *Standard* 100, la recomendada · *Exhaustive*
> 500 · *No cap*, todo lo que haya, con aviso de que son horas y varios GB) y pulsas
> **Build corpus**. Verás el progreso en pantalla y, al acabar, el perfil ya está activo
> en la pestaña MIA. Por debajo se ejecuta exactamente el comando de abajo.

Un solo comando hace todo: escribe el perfil, descarga, indexa y exporta el censo.

```powershell
.\.venv\Scripts\python.exe build_corpus.py `
    --disease "Plaque psoriasis" --synonym psoriasis `
    --class "il17_biologics=secukinumab,ixekizumab,bimekizumab" `
    --class "il23_biologics=risankizumab,guselkumab" `
    --class "oral=apremilast,deucravacitinib" `
    --class-label "il17_biologics=anti-IL-17 antibodies" `
    --mechanism "il-17=secukinumab,ixekizumab,bimekizumab" `
    --mechanism "il-23=risankizumab,guselkumab" `
    --mechanism "tyk2=deucravacitinib" `
    --endpoint "PASI 100" --endpoint "PASI 90" --endpoint "PASI 75" --endpoint "sPGA 0/1" `
    --query "IL-17 inhibitor" `
    --max 50 --activate
```

(En PowerShell la barra invertida de continuación es el acento grave `` ` ``; en bash, `\`.)

Qué significa cada opción:

| Opción | Para qué |
|--------|----------|
| `--disease`, `--synonym` | Enfermedad y sinónimos (repetible) |
| `--class "clase=f1,f2"` | Fármacos agrupados por clase terapéutica (repetible). Si no quieres clases: `--drug f1 --drug f2` |
| `--class-label` | Nombre legible de la clase para la interfaz |
| `--extra-drug` | Fármaco que se **etiqueta** si aparece, pero no se descarga a propósito |
| `--mechanism`, `--query` | Ver 2.3 |
| `--endpoint` | Ver 2.4. **El orden importa**: pon primero los más específicos (`PASI 100` antes que `PASI 10`) |
| `--safety-term` | Evento adverso propio del dominio que quieras extraer como cifra (p. ej. `candidiasis`) |
| `--max 50` | Máximo de resultados **por fármaco y por fuente**. Con 7 fármacos y 2 fuentes son hasta 700 documentos |
| `--activate` | Deja este perfil como el activo por defecto (en la app y en los scripts) |
| `--profile-only` | Solo escribe el `.json`, sin descargar. Para revisarlo o editarlo a mano antes |
| `--skip-download` | Reindexa lo que ya haya en `data/bronze/<slug>/` sin volver a descargar |
| `--openai` | Construye también la colección gemela de OpenAI para la página de comparación (necesita `OPENAI_API_KEY` en `.env`; no hace falta para usar MIA) |

**Cuánto tarda.** Medido el 4-sep-2026 con psoriasis (3 fármacos, `--max 50`, más una
búsqueda extra): 335 documentos y 2.099 fragmentos en unos minutos en CPU. Los ensayos
de ClinicalTrials.gov con resultados publicados son largos (hasta 15 fragmentos cada
uno), por eso el número de fragmentos crece más que el de documentos. La descarga va a ~1 segundo por fármaco y
fuente (PubMed limita a 3 peticiones/segundo); lo lento es vectorizar, a grandes rasgos un
minuto por cada 1.000 fragmentos sin GPU.

**Y si algo falla a medias:** el indexado es idempotente (misma clave = mismo registro), así
que se puede relanzar el comando sin duplicar nada. `--skip-download` evita volver a bajar.

### 3.1 Ejemplo completo y real: retinoblastoma (4-sep-2026)

Un caso fuera de dermatología, hecho de principio a fin tal como lo haría un usuario.

**1. ¿Qué fármacos?** `suggest_drugs.py --disease "Retinoblastoma" --top 15` leyó 140
ensayos y devolvió, por orden: carboplatin (22), etoposide (18), filgrastim (14),
melphalan (13), cyclophosphamide (8), thiotepa (8), vincristine (7+6), topotecan (7)…

**2. Criterio.** Aquí es donde el usuario aporta lo que el contador no sabe: *filgrastim*,
*mesna* y *G-CSF* aparecen en muchos ensayos pero son **tratamiento de soporte** (protegen
la médula o la vejiga durante la quimio), no fármacos contra el tumor. Se quitan. Y
*vincristine* y *vincristine sulfate* son el mismo fármaco. Quedan cinco: carboplatino,
etopósido y vincristina (la quimiorreducción sistémica clásica) y melfalán y topotecán (la
quimioterapia intraarterial e intravítrea, que va directa al ojo).

**3. Endpoint.** En retinoblastoma no hay una "tasa de respuesta con número" tipo PASI 75:
lo que se mide es la **conservación del ojo** (*globe salvage* / *eye salvage*). Se pasa tal
cual; MIA lo buscará en el texto aunque no lleve número.

**4. El comando:**

```powershell
.\.venv\Scripts\python.exe build_corpus.py `
    --disease "Retinoblastoma" `
    --class "systemic_chemo=carboplatin,etoposide,vincristine" `
    --class "local_chemo=melphalan,topotecan" `
    --class-label "systemic_chemo=systemic chemoreduction" `
    --class-label "local_chemo=intra-arterial / intravitreal chemotherapy" `
    --mechanism "platinum=carboplatin" --mechanism "topoisomerase=etoposide,topotecan" `
    --mechanism "alkylating=melphalan" --mechanism "vinca=vincristine" `
    --endpoint "globe salvage" --endpoint "eye salvage" `
    --query "intra-arterial chemotherapy" --query "intravitreal chemotherapy" `
    --max 30
```

**5. Resultado:** 118 ensayos y 150 abstracts descargados → **195 documentos únicos, 598
fragmentos** indexados en `mia_retinoblastoma_medcpt`, censo en
`data/corpus_manifest_retinoblastoma.csv`. Unos minutos en CPU.

**6. Una pregunta:** *"What is the efficacy of intra-arterial melphalan for retinoblastoma
eye salvage?"* MIA respondió, entre otras cosas: *"long-term globe salvage was achieved in
55% of retinoblastoma cases treated with intra-arterial melphalan [Doc 5] … outcomes were
poorer with Group D/E tumors, vitreous seeds, prior intravenous chemotherapy failure, or
requiring more than three intra-arterial melphalan cycles"*. Comprobado a mano: el 55% y
esa lista de factores están, literalmente, en la conclusión del abstract citado como Doc 5
(PMID 40563605, una serie real de 20 ojos con 60 meses de seguimiento). Las cinco fuentes
recuperadas quedaron muy por encima del umbral de evidencia (72-74 frente a 66), así que el
umbral calibrado en dermatitis también separó bien aquí.

Lo que no salió perfecto, para que nadie se lleve a engaño: una frase sobre riesgos de
complicaciones se atribuyó a un ensayo de CT.gov que solo dice que se evaluará la toxicidad.
La cita apunta a una fuente real y relacionada, pero no a la que mejor la respalda. Es la
limitación conocida del reparto determinista de citas cuando varias fuentes hablan de lo
mismo (ver `TASKS.md`).

El perfil resultante está en el repositorio como segundo ejemplo:
`domains/retinoblastoma.json`.

**Una trampa que salió en esta prueba y ya está corregida.** *Retinoblastoma* es también el
nombre de una **proteína** (Rb) que aparece en miles de papers de cáncer de mama, pulmón o
sarcoma. Buscando la enfermedad como texto libre, el corpus se llenaba de esos papers y el
Scout llegó a responder sobre cáncer de mama. Ahora MIA acota la enfermedad en PubMed a su
descriptor **MeSH** o al título (con vuelta al texto libre si no encuentra nada), y en
ClinicalTrials.gov al campo *condición*: de 6 documentos ajenos a 1. Si tu enfermedad
comparte nombre con un gen o una proteína, revisa igualmente el censo CSV.

---

## 4. Paso 3 · Comprobar que está y preguntar

```powershell
# Censo: cuántos fragmentos hay y tres ejemplos
.\.venv\Scripts\python.exe ver_db.py

# Una pregunta desde consola (EN INGLÉS)
.\.venv\Scripts\python.exe src\rag.py "Is secukinumab effective in plaque psoriasis?"

# La interfaz
.\.venv\Scripts\python.exe -m streamlit run app\streamlit_app.py
```

En la app, la barra lateral tiene un **selector de patología**: puedes tener varias
enfermedades cargadas y cambiar entre ellas sin reiniciar. El perfil activo también se puede
fijar con `MIA_DOMAIN=<slug>` en `.env` o en la variable de entorno del proceso.

Si preguntas por un fármaco que no cargaste, el **agente Scout** (interruptor en la barra
lateral) sale a PubMed y ClinicalTrials.gov, importa lo que encuentra acotado a tu
enfermedad, lo indexa y responde. Es la forma más cómoda de ampliar el corpus poco a poco.

---

## 5. Vía B · Exportar a mano desde PubMed

Para cuando quieres decidir tú exactamente qué artículos entran.

1. Entra en <https://pubmed.ncbi.nlm.nih.gov/> y escribe la búsqueda. Sé concreto y usa el
   nombre genérico del fármaco. Ejemplos:
   - `bimekizumab AND psoriasis`
   - `(secukinumab OR ixekizumab) AND "plaque psoriasis" AND randomized`
   - `"IL-17" AND psoriasis AND review[pt]` (revisiones del mecanismo)
   Puedes filtrar por fecha o tipo de artículo en el panel izquierdo.
2. Pulsa **Save** (encima de la lista de resultados).
3. **Selection:** *All results* (hasta 10.000 por archivo). **Format:** **Abstract (text)**.
   No sirve *Summary* (no trae el resumen) ni *PubMed*/*CSV* (otro formato).
4. **Create file**. Se descarga un `.txt` (`pubmed-bimekizum-set.txt` o parecido).
5. Indéxalo en el perfil que toque:

```powershell
.\.venv\Scripts\python.exe ingest_desktop_set.py --file "C:\ruta\pubmed-xxx.txt" --domain plaque_psoriasis
```

El script lee los registros, descarta ruido (erratas, comentarios, retractaciones,
artículos sin resumen), etiqueta cada uno con los fármacos del perfil que menciona, y lo
indexa en la colección de esa enfermedad. Los PMID que ya existían se actualizan, no se
duplican. Probado el 4-sep-2026 con un export real de 8 registros de bimekizumab.

Para que el perfil exista antes, o bien lo creaste con la vía A, o bien lo escribes sin
descargar nada:

```powershell
.\.venv\Scripts\python.exe build_corpus.py --disease "Plaque psoriasis" --drug bimekizumab --endpoint "PASI 90" --profile-only --activate
```

---

## 6. Ampliar, actualizar o afinar más adelante

- **Más documentos de los mismos fármacos:** `run_phase1.py --domain <slug> --max 100`.
- **Un fármaco nuevo:** edita `domains/<slug>.json` (añádelo a su clase) y relanza
  `run_phase1.py --domain <slug>`. Solo descarga lo que falte; lo demás se actualiza en sitio.
- **Editar el perfil a mano.** Es un JSON legible. Claves útiles:

| Clave | Qué es |
|-------|--------|
| `disease`, `synonyms` | Nombre y sinónimos para buscar |
| `drug_classes` | `{"clase": ["f1", "f2"]}`. Se descarga por cada fármaco |
| `extra_drugs` | Se etiquetan si aparecen; no se descargan |
| `mechanisms` | `{"il-17": ["secukinumab"]}` |
| `efficacy_endpoints` | `["PASI 75", "sPGA 0/1"]`, específicos primero |
| `safety_terms` | Eventos adversos propios del dominio a extraer como cifra |
| `extra_queries` | Búsquedas libres extra en la descarga |
| `example_questions` | Botones de ejemplo de la app |

- **Volver a exportar el censo** tras ampliar: `export_corpus_manifest.py` (lo hace solo
  `build_corpus.py`). Es el archivo que dice qué hay dentro del índice y conviene
  guardarlo con el proyecto.

---

## 7. Limitaciones que conviene saber

- **Las preguntas van en inglés.** El corpus y el modelo biomédico lo son; en español el
  modelo degenera.
- **El umbral de "evidencia suficiente"** (cuándo MIA se niega a responder o llama al Scout)
  se calibró sobre dermatitis atópica. Es más una propiedad del modelo de embeddings que del
  corpus, y con psoriasis se comportó igual, pero en un dominio muy distinto conviene
  comprobarlo con `evaluate_sources.py` una vez indexado.
- **El gráfico de cifras solo entiende endpoints "nombre + número"** (tasas de respuesta).
  Medias, medianas o razones de riesgo no se pintan; sí aparecen en la respuesta citada.
- **Los ensayos de CT.gov rara vez ganan a los abstracts** en el ranking: hay muchos más
  papers y se parecen más a una pregunta en lenguaje natural. Su dato está indexado y sale
  cuando la pregunta es de corte "ensayo" o de seguridad.
- **Cuanto mejor elijas los fármacos, mejor el corpus.** Un nombre mal escrito descarga cero
  documentos sin dar error (PubMed simplemente no encuentra nada). El resumen final de
  `build_corpus.py` dice cuántos documentos trajo cada uno: si un fármaco sale con 0, revisa
  el nombre.

---

## 8. Chuleta

```powershell
# ¿Qué fármacos se estudian para X?
.\.venv\Scripts\python.exe suggest_drugs.py --disease "X" --top 10 --phase3

# Crear perfil + descargar + indexar (vía A)
.\.venv\Scripts\python.exe build_corpus.py --disease "X" --class "clase=f1,f2" --mechanism "mec=f1" --endpoint "END 75" --max 50 --activate

# Solo el perfil (para editarlo o para la vía B)
.\.venv\Scripts\python.exe build_corpus.py --disease "X" --drug f1 --profile-only --activate

# Export manual de PubMed (vía B)
.\.venv\Scripts\python.exe ingest_desktop_set.py --file "pubmed-xxx.txt" --domain <slug>

# Ampliar un perfil existente
.\.venv\Scripts\python.exe run_phase1.py --domain <slug> --max 100

# Comprobar y preguntar
.\.venv\Scripts\python.exe ver_db.py
.\.venv\Scripts\python.exe src\rag.py "Is f1 effective in X?"
.\.venv\Scripts\python.exe -m streamlit run app\streamlit_app.py
```
