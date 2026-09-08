"""
app/corpus_tab.py — Pestaña "Build corpus": crear el corpus de una enfermedad desde la app.

Hasta ahora, cargar una patología nueva exigía abrir una terminal y escribir el
comando de `build_corpus.py` con sus opciones. Esta pestaña hace lo mismo con un
formulario: el usuario escribe la enfermedad, pulsa un botón que le propone los
fármacos más estudiados (consultando ClinicalTrials.gov, como `suggest_drugs.py`),
elige mecanismos, endpoints y cuánta literatura quiere, y MIA construye el corpus
mostrando el progreso en pantalla.

DISEÑO: no se llama a la tubería dentro del proceso de Streamlit. Se lanza
`build_corpus.py` como SUBPROCESO y se enseña su salida línea a línea. Motivos:
  1) la descarga + vectorización dura minutos y así la interfaz sigue viva y
     muestra progreso real (no un spinner mudo);
  2) el subproceso carga MedCPT en SU memoria y la libera al terminar, en vez de
     duplicar los modelos dentro de la app;
  3) es EXACTAMENTE el mismo comando que un usuario avanzado lanzaría a mano, así
     que hay una sola tubería que mantener y probar.

MEMORIA: cada perfil creado queda en `domains/<slug>.json` y su corpus en
`data/chroma/`, en el ordenador del usuario. Al volver a abrir la app siguen ahí.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
import config

# Niveles de cobertura. El valor es el `--max` de build_corpus.py (por fármaco y
# por fuente). 0 = sin tope (todo lo que devuelvan las APIs).
COVERAGE = [
    ("Quick · 30 per drug and source (a few minutes)", 30),
    ("Standard · 100 per drug and source — recommended", 100),
    ("Exhaustive · 500 per drug and source (20-30 min on CPU)", 500),
    ("No cap · everything the sources have (see warning)", 0),
]
_DEFAULT_COVERAGE = 1   # "Standard"

# Líneas de la salida del subproceso que solo son ruido de progreso interno.
_NOISE_RE = re.compile(r"Loading weights|HF_TOKEN|it/s\]|^\s*$")


@st.cache_data(ttl=600, show_spinner=False)
def _suggest(disease, top):
    """Fármacos más estudiados para `disease` según ClinicalTrials.gov (cacheado 10 min)."""
    import suggest_drugs
    estudios = suggest_drugs.fetch_studies(disease, max_studies=2000)
    conteo = suggest_drugs.count_drugs(estudios)
    top_list = sorted(conteo.items(), key=lambda kv: (-kv[1]["n"], -kv[1]["phase3"], kv[0]))[:top]
    return len(estudios), [
        {"drug": n, "trials": c["n"], "phase3": c["phase3"], "best": c["best"]}
        for n, c in top_list if len(n.split()) <= 2
    ]


@st.cache_data(ttl=60, show_spinner=False)
def _profiles_table():
    """Perfiles existentes con el tamaño de su corpus (para la sección 'memoria')."""
    filas = []
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        existentes = {c.name for c in client.list_collections()}
    except Exception:  # noqa: BLE001 — sin Chroma, enseñamos los perfiles igualmente
        client, existentes = None, set()
    for slug in config.list_domains():
        try:
            d = config.load_domain(slug)
        except Exception:  # noqa: BLE001
            continue
        coll = config.collection_name("medcpt", slug)
        n = 0
        if client is not None and coll in existentes:
            try:
                n = client.get_collection(coll).count()
            except Exception:  # noqa: BLE001
                n = 0
        n_drugs = sum(len(v) for v in d.get("drug_classes", {}).values())
        filas.append({"Disease": d["disease"], "Profile": slug, "Drugs": n_drugs,
                      "Indexed chunks": n, "Endpoints": ", ".join(
                          (e["label"] if isinstance(e, dict) else str(e))
                          for e in d.get("efficacy_endpoints", [])[:4])})
    return filas


def _md_table(rows, columns):
    """Tabla en Markdown. NO usamos st.dataframe/st.table: necesitan pyarrow, y en
    equipos con Control de Aplicaciones de Windows su DLL queda bloqueada (medido
    el 4-sep-2026: 'DLL load failed while importing lib'). Markdown no depende de nada."""
    if not rows:
        return ""
    nl = chr(10)
    cab = "| " + " | ".join(columns) + " |" + nl + "|" + "|".join("---" for _ in columns) + "|" + nl
    cuerpo = nl.join("| " + " | ".join(str(r.get(c, "")) for c in columns) + " |" for r in rows)
    return cab + cuerpo


def _parse_list(texto):
    """'a, b\nc' → ['a', 'b', 'c'] en minúsculas, sin repetir ni vacíos."""
    items = []
    for x in re.split(r"[,\n;]+", texto or ""):
        x = x.strip().lower()
        if x and x not in items:
            items.append(x)
    return items


def _build_args(form):
    """Traduce el formulario a la línea de comandos de build_corpus.py."""
    args = [sys.executable, "build_corpus.py", "--disease", form["disease"]]
    for s in _parse_list(form["synonyms"]):
        args += ["--synonym", s]
    if form["drugs"]:
        args += ["--class", "main=" + ",".join(form["drugs"])]
        args += ["--class-label", "main=main drugs"]
    for linea in (form["mechanisms"] or "").splitlines():
        if "=" in linea:
            k, v = linea.split("=", 1)
            farmacos = _parse_list(v)
            if k.strip() and farmacos:
                args += ["--mechanism", f"{k.strip().lower()}={','.join(farmacos)}"]
    for e in re.split(r"[,\n;]+", form["endpoints"] or ""):
        if e.strip():
            args += ["--endpoint", e.strip()]
    for q in re.split(r"[,\n;]+", form["queries"] or ""):
        if q.strip():
            args += ["--query", q.strip()]
    args += ["--max", str(form["max"])]
    if form["activate"]:
        args.append("--activate")
    return args


def _run_build(args, log_box, status_box):
    """Lanza build_corpus.py y vuelca su salida en pantalla. Devuelve el código de salida."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        args, cwd=str(config.BASE_DIR), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    lineas = []
    for raw in proc.stdout:
        linea = raw.rstrip("\r\n")
        if _NOISE_RE.search(linea):
            continue
        lineas.append(linea)
        log_box.code("\n".join(lineas[-30:]), language="text")
        if linea.startswith("##########"):
            status_box.update(label=linea.strip("# ").strip())
    proc.wait()
    return proc.returncode, lineas


def render(on_built=None):
    """Pinta la pestaña. `on_built(slug)` se llama al terminar con éxito (para
    vaciar cachés y activar el perfil en la app)."""
    st.markdown("### Build the corpus for a disease")
    st.caption("Tell MIA the disease, the drugs and the mechanisms you care about. It downloads "
               "the abstracts (PubMed) and the trials with their results (ClinicalTrials.gov), "
               "indexes them locally and leaves the profile ready to query. No PDFs, no manual "
               "exports. Everything stays on this computer.")

    col_form, col_help = st.columns([3, 1.3], gap="large")

    with col_help:
        st.markdown("**How to fill it in**")
        st.markdown(
            "- **Disease**: the English name PubMed uses (*Plaque psoriasis*, *Crohn disease*).\n"
            "- **Drugs**: generic names, never brands. Press *Suggest* if you do not know them.\n"
            "- **Mechanisms**: one per line, `il-17=secukinumab,ixekizumab`. Optional.\n"
            "- **Endpoints**: the response rate of that disease (`PASI 75`, `ACR20`). "
            "Look at the *Primary Outcome* of a phase 3 trial.\n"
            "- **Extra searches**: free-text topics without a drug name "
            "(*IL-17 inhibitor*)."
        )
        st.markdown("**Your profiles (saved on this computer)**")
        filas = _profiles_table()
        if filas:
            st.markdown(_md_table(filas, ["Disease", "Profile", "Drugs", "Indexed chunks", "Endpoints"]))
            st.caption("Stored in `domains/<profile>.json` and `data/chroma/`. They persist "
                       "between sessions; switch between them in the MIA tab.")
        else:
            # Instalación nueva: ningún perfil todavía (una tabla vacía confunde).
            st.caption("None yet. The first one you build will appear here and will be "
                       "remembered between sessions.")

    with col_form:
        disease = st.text_input("Disease (in English)", placeholder="Plaque psoriasis",
                                key="cb_disease")
        synonyms = st.text_input("Synonyms (comma-separated, optional)",
                                 placeholder="psoriasis", key="cb_syn")

        # --- Sugerencia de fármacos (ClinicalTrials.gov) -------------------------
        c1, c2 = st.columns([1, 2])
        with c1:
            top = st.number_input("Suggest top", min_value=5, max_value=30, value=10, step=5,
                                  key="cb_top")
        with c2:
            st.write("")
            st.write("")
            if st.button("Suggest drugs from ClinicalTrials.gov", icon=":material/science:",
                         disabled=not disease.strip(), key="cb_suggest"):
                with st.spinner("Counting trials on ClinicalTrials.gov…"):
                    try:
                        n_trials, sugeridos = _suggest(disease.strip(), int(top))
                        st.session_state["cb_suggested"] = sugeridos
                        st.session_state["cb_n_trials"] = n_trials
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Could not query ClinicalTrials.gov: {e}")
        sugeridos = st.session_state.get("cb_suggested") or []
        if sugeridos:
            st.caption(f"{st.session_state.get('cb_n_trials', 0)} trials read. Most studied "
                       "drugs (review with judgement: supportive care such as filgrastim or "
                       "mesna is not a treatment; codes like FP187 are experimental molecules):")
            st.markdown(_md_table(
                [{"drug": x["drug"], "trials": x["trials"], "phase 3/4": x["phase3"]}
                 for x in sugeridos], ["drug", "trials", "phase 3/4"]))
        opciones = [s["drug"] for s in sugeridos]
        elegidos = st.multiselect("Drugs to download (pick from the suggestions)", opciones,
                                  default=opciones[:5], key="cb_pick") if opciones else []
        otros = st.text_input("Other drugs (comma-separated)", placeholder="topotecan, melphalan",
                              key="cb_other")
        drugs = elegidos + [d for d in _parse_list(otros) if d not in elegidos]

        mechanisms = st.text_area("Mechanisms → drugs (one per line, optional)",
                                  placeholder="il-17=secukinumab,ixekizumab\npde4=apremilast",
                                  height=80, key="cb_mech")
        endpoints = st.text_input("Efficacy endpoints (comma-separated, most specific first)",
                                  placeholder="PASI 90, PASI 75, sPGA 0/1", key="cb_end")
        queries = st.text_input("Extra searches (comma-separated, optional)",
                                placeholder="IL-17 inhibitor", key="cb_q")

        cobertura = st.radio("How much literature", [c[0] for c in COVERAGE],
                             index=_DEFAULT_COVERAGE, key="cb_cov")
        max_results = dict(COVERAGE)[cobertura]
        if max_results == 0:
            st.warning(
                "**No cap** downloads *everything* PubMed and ClinicalTrials.gov return for each "
                "drug (PubMed serves up to 10,000 abstracts per search). For a common disease "
                "with 5 drugs that can be tens of thousands of documents: expect **hours** of "
                "indexing on CPU, several GB in `data/`, and a noticeably slower search. "
                "Recommended only for rare diseases or when completeness matters more than "
                "time. You can stop and re-run later: nothing is duplicated.",
                icon=":material/warning:")
        activate = st.checkbox("Make it the active profile when done", value=True, key="cb_act")

        # --- Validación ---------------------------------------------------------
        problemas = []
        if not disease.strip():
            problemas.append("the disease name")
        if not drugs:
            problemas.append("at least one drug")
        slug = config.slugify(disease)
        # El perfil original del TFM (con sus colecciones "legacy") se protege de
        # cambios desde la app SOLO si existe en este equipo; en una instalación
        # nueva no hay nada que proteger y "Atopic dermatitis" es un nombre válido.
        if slug == config.DEFAULT_DOMAIN and config.domain_path(slug).exists():
            problemas.append(f"a disease other than the built-in `{config.DEFAULT_DOMAIN}` profile")
        if slug in config.list_domains() and slug != config.DEFAULT_DOMAIN:
            st.info(f"Profile `{slug}` already exists: it will be **updated** and its corpus "
                    "extended (existing documents are not duplicated).", icon=":material/info:")

        form = {"disease": disease.strip(), "synonyms": synonyms, "drugs": drugs,
                "mechanisms": mechanisms, "endpoints": endpoints, "queries": queries,
                "max": max_results, "activate": activate}
        with st.expander("Command that will run", expanded=False):
            st.code(" ".join(
                f'"{a}"' if " " in a else a for a in _build_args(form)[1:]), language="text")

        if st.button("Build corpus", type="primary", icon=":material/construction:",
                     disabled=bool(problemas), key="cb_go"):
            args = _build_args(form)
            with st.status("Starting…", expanded=True) as status_box:
                log_box = st.empty()
                try:
                    code, lineas = _run_build(args, log_box, status_box)
                except Exception as e:  # noqa: BLE001
                    code, lineas = 1, [f"[error] {e}"]
                if code == 0:
                    status_box.update(label="Corpus ready", state="complete", expanded=False)
                else:
                    status_box.update(label="Build failed — see the log", state="error")
            if code == 0:
                resumen = [l.strip() for l in lineas
                           if "Chunks indexados" in l or "Documentos unicos" in l]
                st.session_state["flash"] = (
                    "**Corpus ready.** " + " · ".join(resumen)
                    + f" The profile `{slug}` is saved on this computer"
                    + (" and is now active: ask away." if activate
                       else ". Select it in the Disease profile box to ask."))
                _profiles_table.clear()
                if on_built:
                    on_built(slug if activate else None)
                # Rerun: la app vuelve a la pestaña MIA con el aviso y el perfil ya
                # activo, y la tabla de perfiles se repinta con el nuevo (sin esto,
                # la tabla de la derecha —pintada ANTES de construir— no lo enseñaría).
                st.rerun()
            else:
                st.error("The build did not finish. Check the log above: a misspelled drug "
                         "downloads nothing, and PubMed sometimes refuses bursts of requests "
                         "(just run it again).")
        elif problemas:
            st.caption("To build, fill in: " + ", ".join(problemas) + ".")
