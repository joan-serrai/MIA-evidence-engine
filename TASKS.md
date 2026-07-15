# MIA — Tareas y posibles acciones

Documento vivo. Anoto aquí lo pendiente, lo que estamos haciendo y las ideas
futuras. Se va actualizando a medida que surgen cosas.

Leyenda de estado: ⏳ pendiente · 🔨 en curso · ✅ hecho · 💡 idea a valorar

---

## 🔨 En curso

- **Cambiar el modelo de embeddings a MedCPT** (`ncbi/MedCPT-*`).
  - [✅] Diseñar capa de embeddings intercambiable (`src/embeddings.py`).
  - [✅] Configurar backend en `config.py` (bge ↔ MedCPT) + colección separada.
  - [✅] Prueba de humo: MedCPT carga, da 768 dim y discrimina (relevante 0,59 > irrelevante 0,30).
  - [ ] Reindexar TODO el corpus con MedCPT (bronze + `abstract-atopicderm-set.txt`).
        → paso lento (CPU); llena la colección `mia_evidence_medcpt`, sin destruir bge.
  - [✅] Reindexado MedCPT (coseno) hecho: 8.920 chunks, 768 dim, ~45 min en CPU.
  - [⚠️] HALLAZGO: con COSENO no hay umbral válido. Medido sobre 337 docs:
        relevantes (dupilumab/lebrikizumab) máx coseno 0,63-0,66; "capital de Francia"
        (ajena) máx coseno 0,68 → MÁS ALTA que las relevantes. La puerta de evidencia
        (anti-alucinación) NO funcionaría con coseno.
        CAUSA: MedCPT codifica confianza en la NORMA del vector; normalizar la tira.
        CON PRODUCTO ESCALAR (métrica nativa de MedCPT) SÍ separa: relevantes 67-70,
        ajenas 54-60 → umbral ~63.
  - [✅] ARREGLO aplicado: MedCPT a inner-product (embeddings.py no normaliza;
        config.CHROMA_SPACE="ip"; umbral en escala dot). Micro-test OK: dupilumab 64-66
        (>63, hay evidencia), "capital de Francia" ~55 (<63, sin evidencia). Anomalía resuelta.
  - [✅] Reindexado inner-product hecho: 8.920 chunks, 768 dim, colección `mia_evidence_medcpt`.
  - [✅] Umbral fijado en 66.0, medido sobre la base completa (relevantes 70-73, ajenas 56-64;
        hueco limpio). `config.SIMILARITY_THRESHOLD` depende del backend.
  - [✅] Verificado END-TO-END: "dupilumab efficacy" → 5 fuentes citadas (PMID+URL),
        respuesta analítica de OpenBioLLM. **Migración a MedCPT COMPLETA.**
  - [ ] UI: la tarjeta muestra la similitud como "confianza" en % (0-1 de bge). Con dot
        (~56-73) hay que reescalar para que no se vea "7000%". Ajustar en streamlit_app.

## ⏳ Pendientes

- **Re-tunear el umbral de similitud** para MedCPT usando `ver_db.py --buscar`
  sobre preguntas conocidas (ver la distribución de similitudes antes de fijarlo).
- **Verificar que la respuesta de MIA no empeora** tras el cambio (preguntas de control:
  dupilumab, lebrikizumab, delgocitinib vía Scout).

## 💡 Ideas a valorar

- **Comparativa de tamaño de chunk (nº óptimo).** Probar CHUNK_SIZE = 500 / 800 / 1000
  y medir con qué valor la fuente correcta sale más arriba en el top-5. Elegir con datos.
- **Comparativa de embeddings bge vs MedCPT.** Como cada backend tiene su colección,
  se pueden mantener las dos e ir preguntando a ambas para comparar recuperación.
- **Evaluación de recuperación (retrieval eval).** 10–15 preguntas con la fuente correcta
  conocida → métrica objetiva (¿sale en top-5?) para justificar decisiones de embedding/chunk.
- **MedCPT con formato [título, abstract].** Ahora embebemos el texto del chunk tal cual;
  el Article-Encoder de MedCPT rinde algo mejor con el par (título, abstract).
- **Q&A en español** vía traducción de la pregunta antes del RAG (el corpus/LLM son inglés).

## ✅ Hecho

- Script `ver_db.py` para inspeccionar ChromaDB (censo, ejemplos, búsqueda real).
