"""
app/streamlit_app.py — FASE 5: Interfaz de chat.  [Módulo 11: Infraestructura]

Un panel de chat donde el usuario escribe su pregunta y ve:
  - la respuesta del modelo biomédico local (con citas [Doc N] resaltadas),
  - las fuentes (título + PMID/NCT + similitud + enlace) en tarjetas desplegables,
  - y, si se activó el agente Scout, un aviso de la evidencia que importó al momento.

Se ejecuta con:   streamlit run app/streamlit_app.py
(Requiere haber hecho la Fase 1 —tener datos indexados— y tener Ollama corriendo.)

El diseño (cabecera con gradiente, tarjetas, insignias) se logra con un poco de CSS
propio inyectado más abajo. Streamlit lee además el tema de `.streamlit/config.toml`.
"""

import hashlib
import html
import re
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

# La app vive en app/, así que añadimos la raíz del proyecto al path para poder
# importar config y el paquete src.
sys.path.append(str(Path(__file__).resolve().parent.parent))
import config
from src import rag, scout, citations, report, status

st.set_page_config(
    page_title="MIA · Medical Intelligence Agent",
    page_icon=":material/biotech:",
    layout="centered",
)


# ==========================================================================
# 0) Estilos propios (CSS). Se inyectan una sola vez al cargar la página.
# ==========================================================================
st.markdown(
    """
    <style>
      /* --- Paleta Centivence como variables reutilizables --- */
      :root {
        --mia-teal:    #3f6e66;   /* acento salvia (conserva el nombre por compatibilidad) */
        --mia-teal-d:  #2c524c;   /* salvia oscuro */
        --mia-ink:     #1a1a1a;   /* tinta */
        --mia-slate:   #555555;   /* gris de cuerpo */
        --mia-line:    #eeeeee;   /* línea/hairline */
        --mia-bg-soft: #fafafa;   /* fondo suave */
        --mia-accent-br: #5a9389; /* salvia brillante */
        --mia-success:   #1a7a3a; /* verde éxito */
        --mia-success-2: #22c55e; /* verde vivo (puntos) */
        --mia-mint:      #e8f5ee; /* fondo menta */
        --mia-amber:     #92600a; /* ámbar cálido (avisos) */
        --mia-track:     #f0f0f0; /* pista de barras */
        /* Tipografía: Inter si está instalada; si no, Segoe UI (Windows) — SIN red,
           coherente con el principio "100% local". Mono para etiquetas/números. */
        --mia-font: 'Inter','Segoe UI',-apple-system,BlinkMacSystemFont,Roboto,Helvetica,Arial,sans-serif;
        --mia-mono: 'JetBrains Mono','Cascadia Code',Consolas,'SFMono-Regular',ui-monospace,monospace;
      }

      /* Fuente de marca en toda la app (stack local, sin llamadas a Google Fonts). */
      html, body, [class*="css"], .stMarkdown, .block-container { font-family: var(--mia-font); }

      /* Reducimos el hueco superior por defecto de Streamlit. */
      .block-container { padding-top: 2.2rem; max-width: 820px; }

      /* --- Cabecera de marca con gradiente --- */
      .mia-hero {
        background: linear-gradient(150deg, #3f6e66 0%, #2c524c 100%);
        border-radius: 20px;
        padding: 26px 30px;
        color: #fff;
        box-shadow: 0 10px 30px -12px rgba(63,110,102,.35);
        margin-bottom: 6px;
      }
      .mia-hero h1 {
        font-size: 1.9rem; font-weight: 800; margin: 0;
        letter-spacing: -.02em; color: #fff;
      }
      .mia-hero .tag { font-size: .95rem; opacity: .92; margin-top: 4px; }
      .mia-badges { margin-top: 14px; display: flex; flex-wrap: wrap; gap: 8px; }
      .mia-badges span {
        background: rgba(255,255,255,.16);
        border: 1px solid rgba(255,255,255,.28);
        padding: 4px 11px; border-radius: 999px;
        font-size: .78rem; font-weight: 600; backdrop-filter: blur(4px);
      }

      /* --- Insignia de cita [Doc N] dentro de la respuesta --- */
      .cite {
        display: inline-block;
        background: var(--mia-mint); color: var(--mia-teal-d);
        border: 1px solid #cde8d8;
        font-size: .74rem; font-weight: 700;
        padding: 1px 7px; border-radius: 6px;
        margin: 0 1px; white-space: nowrap; vertical-align: baseline;
      }

      /* --- Tarjeta de fuente --- */
      .src-card {
        border: 1px solid var(--mia-line);
        border-left: 4px solid var(--mia-teal);
        border-radius: 12px;
        padding: 14px 16px; margin-bottom: 12px;
        background: #fff;
        box-shadow: 0 1px 2px rgba(15,23,42,.04);
      }
      .src-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
      .src-doc {
        background: var(--mia-teal); color: #fff;
        font-size: .72rem; font-weight: 700;
        padding: 2px 9px; border-radius: 6px;
      }
      .src-type {
        font-size: .72rem; font-weight: 700; letter-spacing: .03em;
        color: var(--mia-slate); text-transform: uppercase;
      }
      .src-sim { margin-left: auto; font-size: .74rem; font-weight: 700; }
      .src-title {
        font-size: .98rem; font-weight: 600; color: var(--mia-ink);
        margin: 9px 0 6px; line-height: 1.35;
      }
      .src-meta { font-size: .8rem; color: var(--mia-slate); margin-bottom: 8px; }

      /* Fragmento REAL recuperado: la "ilustración" que corresponde con la fuente. */
      .src-snippet {
        font-size: .86rem; color: #333; line-height: 1.5;
        border-left: 3px solid var(--mia-line);
        padding: 6px 0 6px 12px; margin: 4px 0 10px;
      }
      .src-snippet::before { content: "\201C"; }
      .src-snippet::after  { content: "\201D"; }

      /* Fármacos detectados en la fuente (dato real de la metadata). */
      .src-drugs { display: flex; gap: 6px; flex-wrap: wrap; margin: 0 0 10px; }
      .src-drugs span {
        font-family: var(--mia-mono); font-size: .7rem; font-weight: 500;
        background: var(--mia-mint); color: var(--mia-success);
        border: 1px solid #cde8d8; padding: 2px 8px; border-radius: 999px;
      }
      .src-bar { height: 6px; border-radius: 999px; background: var(--mia-track); overflow: hidden; margin: 8px 0 10px; }
      .src-bar > i { display: block; height: 100%; border-radius: 999px; }
      .src-link {
        display: inline-block; font-size: .82rem; font-weight: 600;
        color: var(--mia-teal-d); text-decoration: none;
      }
      .src-link:hover { text-decoration: underline; }

      /* --- Data Cards: fila de KPIs con datos REALES de la respuesta --- */
      .mia-kpis { display: flex; gap: 10px; flex-wrap: wrap; margin: 10px 0 4px; }
      .mia-kpi {
        flex: 1 1 120px;
        border: 1px solid var(--mia-line); border-radius: 12px;
        padding: 12px 14px; background: #fff;
        box-shadow: 0 1px 2px rgba(26,26,26,.04);
      }
      .mia-kpi .k-num {
        font-family: var(--mia-mono); font-size: 1.5rem; font-weight: 600;
        color: var(--mia-ink); line-height: 1.1; letter-spacing: -.01em;
      }
      .mia-kpi .k-lbl {
        font-family: var(--mia-mono); font-size: .66rem; font-weight: 600;
        letter-spacing: .08em; text-transform: uppercase;
        color: var(--mia-slate); margin-top: 4px;
      }
      .mia-kpi .k-dot {
        display: inline-block; width: 7px; height: 7px; border-radius: 999px;
        margin-right: 6px; vertical-align: middle;
      }
      .mia-kpi.k-scout { border-left: 3px solid var(--mia-accent-br); }

      /* --- Gráfico de outcomes: cifras extraídas VERBATIM de las fuentes --- */
      .mia-chart {
        border: 1px solid var(--mia-line); border-radius: 12px;
        padding: 14px 16px; margin: 12px 0; background: #fff;
        box-shadow: 0 1px 2px rgba(26,26,26,.04);
      }
      .mia-chart .oc-head {
        font-family: var(--mia-mono); font-size: .72rem; font-weight: 600;
        letter-spacing: .06em; text-transform: uppercase; color: var(--mia-slate);
        display: flex; align-items: center; gap: 7px; flex-wrap: wrap; margin-bottom: 12px;
      }
      .mia-chart .oc-note {
        text-transform: none; letter-spacing: 0; font-weight: 500;
        color: var(--mia-accent-br); font-family: var(--mia-font); font-size: .72rem;
      }
      .oc-row { display: flex; align-items: center; gap: 10px; margin: 7px 0; }
      .oc-lbl {
        flex: 0 0 104px; font-size: .8rem; font-weight: 600; color: var(--mia-ink);
        display: flex; flex-direction: column; line-height: 1.15;
      }
      .oc-lbl .oc-sub { font-size: .66rem; font-weight: 500; color: var(--mia-slate); }
      .oc-track {
        position: relative; flex: 1 1 auto; height: 22px; border-radius: 6px;
        background: var(--mia-track); overflow: hidden;
      }
      .oc-track > i {
        display: block; height: 100%; border-radius: 6px;
        background: linear-gradient(90deg, var(--mia-accent-br), var(--mia-teal));
      }
      .oc-val {
        flex: 0 0 46px; text-align: right;
        font-family: var(--mia-mono); font-size: .74rem; font-weight: 600; color: var(--mia-ink);
      }
      .oc-doc {
        flex: 0 0 auto; font-family: var(--mia-mono); font-size: .66rem; font-weight: 600;
        color: var(--mia-teal-d); background: var(--mia-mint);
        border: 1px solid #cde8d8; padding: 2px 7px; border-radius: 6px;
      }
      /* Cabecera de grupo: referencia UNA vez el paper (Doc N + título) y debajo
         van sus cifras SIN repetir la cita en cada fila. */
      .oc-group {
        display: flex; align-items: center; gap: 8px;
        margin: 14px 0 6px; padding-top: 10px; border-top: 1px dashed var(--mia-line);
      }
      .oc-group:first-of-type { border-top: none; padding-top: 0; margin-top: 2px; }
      .oc-group-title {
        font-size: .78rem; font-weight: 600; color: var(--mia-ink);
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }

      /* --- Paneles de estado (Scout / sin evidencia) --- */
      .mia-panel {
        border-radius: 12px; padding: 14px 16px; margin: 10px 0;
        font-size: .9rem; line-height: 1.5; border: 1px solid var(--mia-line);
      }
      .mia-panel .p-head {
        font-family: var(--mia-mono); font-size: .7rem; font-weight: 600;
        letter-spacing: .08em; text-transform: uppercase;
        display: flex; align-items: center; gap: 8px; margin-bottom: 6px;
      }
      .mia-panel .p-dot { width: 8px; height: 8px; border-radius: 999px; }
      .mia-panel-scout { background: var(--mia-mint); border-color: #cde8d8; color: var(--mia-ink); }
      .mia-panel-scout .p-dot { background: var(--mia-success-2); }
      .mia-panel-empty { background: #fafafa; border-color: var(--mia-line); color: var(--mia-slate); }
      .mia-panel-empty .p-dot { background: var(--mia-amber); }

      /* --- Motivo de relevancia en las fuentes recuperadas-no-citadas --- */
      .src-reason {
        font-size: .8rem; color: var(--mia-slate); line-height: 1.45;
        margin-top: 6px; padding: 7px 9px; border-radius: 8px;
        background: var(--mia-bg-soft); border: 1px solid var(--mia-line);
      }
      .src-reason b { color: var(--mia-ink); font-weight: 600; }

      /* --- Tira "cómo funciona": 3 pasos, patrón de los competidores --- */
      .mia-how {
        display: flex; gap: 10px; flex-wrap: wrap; margin: 6px 0 2px;
      }
      .mia-how .step {
        flex: 1 1 150px; border: 1px solid var(--mia-line); border-radius: 12px;
        padding: 11px 13px; background: #fff;
      }
      .mia-how .step .s-n {
        font-family: var(--mia-mono); font-size: .66rem; font-weight: 700;
        color: var(--mia-teal); letter-spacing: .06em;
      }
      .mia-how .step .s-t { font-size: .86rem; font-weight: 600; color: var(--mia-ink); margin-top: 2px; }
      .mia-how .step .s-d { font-size: .78rem; color: var(--mia-slate); margin-top: 3px; line-height: 1.4; }

      /* --- Sección de bienvenida (estado vacío) --- */
      .mia-welcome {
        border: 1px dashed var(--mia-line); border-radius: 16px;
        padding: 22px 24px; background: var(--mia-bg-soft); margin-top: 14px;
      }
      .mia-welcome h3 { margin: 0 0 4px; font-size: 1.05rem; color: var(--mia-ink); }
      .mia-welcome p  { margin: 0; color: var(--mia-slate); font-size: .9rem; }

      /* Botones de ejemplo un poco más suaves. */
      div[data-testid="stButton"] > button {
        border-radius: 10px; border: 1px solid var(--mia-line);
        text-align: left; font-size: .88rem; font-weight: 500;
        color: var(--mia-ink); background: #fff;
      }
      div[data-testid="stButton"] > button:hover {
        border-color: var(--mia-teal); color: var(--mia-teal-d);
      }

      /* --- Iconos SVG inline (estilo línea Material). Heredan color (currentColor)
             y tamaño del texto; van EMBEBIDOS, sin ninguna llamada a red. --- */
      .mia-ic {
        width: 1em; height: 1em;
        display: inline-block; vertical-align: -.14em;
        stroke: currentColor; fill: none;
      }
      .mia-hero h1 .mia-ic { vertical-align: -.10em; margin-right: 6px; }
      .mia-badges span .mia-ic { vertical-align: -.15em; margin-right: 5px; width: .95em; height: .95em; }
      .mia-panel .p-head .mia-ic { width: 1.05em; height: 1.05em; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ==========================================================================
# 0.b) Iconos SVG inline (sustituyen a los emojis; sin red, coherente con "local")
# ==========================================================================
# Cada icono es un SVG de trazo (viewBox 24x24) que hereda color y tamaño del
# texto que lo rodea. `_icon("lock")` devuelve el <svg> listo para incrustar.
_ICONS = {
    "dna": '<path d="M7 3c0 6 10 9 10 15M17 3c0 6-10 9-10 15"/><path d="M8 6h8M9 9h6M9 15h6M8 18h8"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "cite": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h5"/>',
    "shield": '<path d="M12 3l7 3v5c0 4.5-3 7.6-7 9-4-1.4-7-4.5-7-9V6z"/><path d="M9.3 12l1.9 1.9 3.5-3.9"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
}


def _icon(name):
    """Devuelve el SVG inline del icono (o cadena vacía si no existe)."""
    body = _ICONS.get(name, "")
    if not body:
        return ""
    return (f'<svg class="mia-ic" viewBox="0 0 24 24" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'{body}</svg>')


# ==========================================================================
# 1) Cabecera de marca
# ==========================================================================
st.markdown(
    f"""
    <div class="mia-hero">
      <h1>{_icon("dna")}MIA — Medical Intelligence Agent</h1>
      <div class="tag">Evidencia biomédica <b>100% local y soberana</b> — tus datos
        nunca salen de este ordenador · {html.escape(config.DISEASE)}</div>
      <div class="mia-badges">
        <span>{_icon("lock")}100% local · sin nube</span>
        <span>{_icon("cite")}Cada cifra rastreable al abstract</span>
        <span>{_icon("shield")}Sin evidencia, no responde</span>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Tira "cómo funciona": el patrón de presentación de los líderes (OpenEvidence,
# UpToDate…) es explicar el flujo y la política de citas en portada. Aquí lo
# adaptamos al diferenciador de MIA: recuperar local → citar de forma
# determinista → ampliar con el Scout si falta evidencia.
st.markdown(
    f"""
    <div class="mia-how">
      <div class="step"><div class="s-n">01 · RECUPERA</div>
        <div class="s-t">Búsqueda biomédica local</div>
        <div class="s-d">MedCPT (embeddings de NCBI) encuentra la evidencia en tu
          corpus, sin llamar a ninguna API externa.</div></div>
      <div class="step"><div class="s-n">02 · CITA</div>
        <div class="s-t">Cada afirmación, a su fuente</div>
        <div class="s-d">El modelo local responde y las citas [Doc N] se colocan de
          forma determinista: cada cifra es rastreable a su abstract.</div></div>
      <div class="step"><div class="s-n">03 · AMPLÍA</div>
        <div class="s-t">Agente Scout si falta evidencia</div>
        <div class="s-d">Si el corpus local no basta, sale a PubMed/ClinicalTrials,
          importa la evidencia y reintenta — nunca inventa.</div></div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ==========================================================================
# 1.b) Estado del sistema (producto): ¿está todo listo para responder?
# ==========================================================================
@st.cache_data(ttl=30, show_spinner=False)
def _cached_status():
    """Estado del sistema cacheado 30s (no re-chequear en cada rerun de Streamlit)."""
    return status.system_status()


# Banner accionable: si falta algo (Ollama, modelo o corpus), lo decimos con la
# solución exacta EN VEZ de dejar que la app reviente al primer intento.
_status = _cached_status()
if not _status["ready"]:
    _hints = status.fix_hints(_status)
    st.error(
        "**MIA no está listo todavía.** Falta poner en marcha algo antes de preguntar:\n\n"
        + "\n".join(f"- {h}" for h in _hints),
        icon=":material/build:",
    )


# ==========================================================================
# 2) Barra lateral: información y ajustes
# ==========================================================================
with st.sidebar:
    st.header(":material/settings: Ajustes")
    usar_scout = st.toggle(
        "Agente Scout",
        value=True,
        help="Si no hay evidencia local suficiente, sale a buscarla a "
             "PubMed/ClinicalTrials, la importa y reintenta.",
    )
    st.divider()
    st.subheader(":material/smart_toy: Modelos (locales)")
    st.caption(f":material/neurology: Biomédico: `{config.LLM_MODEL}`")
    # Mostramos el embedding REALMENTE activo (según EMBEDDING_BACKEND), no una
    # constante fija: si estamos en MedCPT, decir 'bge' sería engañoso.
    if getattr(config, "EMBEDDING_BACKEND", "") == "medcpt":
        _emb_label = "MedCPT (NCBI) · biomédico, 2 torres"
    else:
        _emb_label = config.EMBEDDING_MODEL
    st.caption(f":material/tag: Embeddings: `{_emb_label}`")
    st.divider()
    st.caption(f":material/coronavirus: Enfermedad: **{config.DISEASE}**")
    st.caption(f":material/tune: Umbral de evidencia: {config.SIMILARITY_THRESHOLD}")

    # --- Estado del sistema: semáforos reales (Ollama / modelo / corpus) ---
    st.divider()
    st.subheader(":material/monitor_heart: Estado del sistema")
    _s = _cached_status()
    _ollama_ok = _s["ollama"]["up"]
    _modelo_ok = all(_s["models"].values())
    _corpus = _s["corpus"]
    st.markdown(
        f"- {'🟢' if _ollama_ok else '🔴'} **Ollama** "
        f"{'en marcha' if _ollama_ok else 'no responde'}\n"
        f"- {'🟢' if _modelo_ok else '🔴'} **Modelo biomédico** "
        f"{'descargado' if _modelo_ok else 'no encontrado'}\n"
        f"- {'🟢' if _corpus['ok'] else '🔴'} **Corpus** "
        f"{_corpus['chunks']:,} fragmentos".replace(",", ".")
    )
    if not _s["ready"]:
        st.caption("⚠️ Revisa el aviso de arriba para ponerlo en marcha.")

    st.info("Por ahora hay que **preguntar en inglés** (el modelo biomédico "
            "es fiable solo en inglés).", icon=":material/translate:")

    if st.session_state.get("messages"):
        st.divider()
        if st.button("Borrar conversación", icon=":material/delete:",
                     width="stretch"):
            st.session_state.messages = []
            st.rerun()


# ==========================================================================
# 3) Utilidades de presentación
# ==========================================================================
def _highlight_citations(texto: str) -> str:
    """Convierte las citas '[Doc N]' del modelo en insignias visuales."""
    return re.sub(r"\[Doc\s*(\d+)\]", r'<span class="cite">Doc \1</span>', texto)


def _source_type_label(source: str) -> str:
    """Nombre bonito de la procedencia de la fuente."""
    s = (source or "").lower()
    if "pubmed" in s:
        return "PubMed"
    if "clinical" in s or s in {"ct", "ctgov"}:
        return "ClinicalTrials.gov"
    return source or "Fuente"


def _id_label(source: str, doc_id: str) -> str:
    """Etiqueta legible del identificador: 'PMID 123' o 'NCT01234567'."""
    s = (source or "").lower()
    doc_id = str(doc_id or "")
    if "pubmed" in s:
        return f"PMID {doc_id}"
    if doc_id.upper().startswith("NCT"):
        return doc_id.upper()
    return doc_id


def _confidence_pct(sim: float) -> int:
    """Convierte la 'similarity' cruda en un % de confianza 0-100 para la UI.

    CONVENCIÓN DOCUMENTADA (importante para el TFM): con MedCPT la similarity NO
    es un coseno 0-1, sino un PRODUCTO ESCALAR (~55-75; ver config: relevantes
    ~70-73, ajenos ~56-64, umbral de evidencia 66). Lo calibramos linealmente a
    0-100% con la banda medida (55 → 0%, 80 → 100%) para que sea legible. NO es
    una probabilidad ni una 'precisión/veracidad': es una CONFIANZA DE
    RECUPERACIÓN relativa. Con bge (coseno) el valor ya es 0-1 → solo ×100.
    Con esta escala, el umbral de evidencia (66) equivale a ~44%.
    """
    if getattr(config, "EMBEDDING_BACKEND", "") == "medcpt":
        pct = (sim - 55.0) / (80.0 - 55.0) * 100.0
    else:
        pct = sim * 100.0
    return int(round(max(0.0, min(100.0, pct))))


def _conf_color(pct: int) -> str:
    """Color según el % de confianza calibrado (~44% = umbral de evidencia)."""
    if pct >= 60:
        return "#1a7a3a"   # verde éxito Centivence
    if pct >= 44:
        return "#3f6e66"   # salvia (por encima del umbral de evidencia)
    return "#92600a"       # ámbar cálido (flojo)


def _render_sources(sources):
    """Pinta la lista de fuentes citadas como tarjetas dentro de un desplegable."""
    if not sources:
        return
    with st.expander(f"Fuentes citadas ({len(sources)})",
                     icon=":material/menu_book:", expanded=False):
        for f in sources:
            sim = float(f.get("similarity", 0) or 0)
            pct = _confidence_pct(sim)
            color = _conf_color(pct)

            # 'n_fragments' = cuántos chunks de ESTE artículo coincidieron y se
            # unieron bajo un solo [Doc N] (citas deduplicadas por PMID/NCT).
            n_frag = f.get("n_fragments", 1)
            frag_txt = f"{n_frag} fragmento" + ("s" if n_frag != 1 else "")

            title = html.escape(f.get("title") or "(sin título)")
            id_lbl = html.escape(_id_label(f.get("source"), f.get("doc_id")))
            src_lbl = html.escape(_source_type_label(f.get("source")))
            url = html.escape(f.get("url") or "#")

            # Fragmento real recuperado (garantía visual↔fuente). Solo si existe.
            snip = html.escape(f.get("snippet") or "")
            snippet_html = f'<div class="src-snippet">{snip}</div>' if snip else ""

            # Pills de fármacos: 'drugs' llega como string "; "-join; troceamos
            # de forma defensiva y mostramos hasta 6 (dato real de la fuente).
            drugs_raw = f.get("drugs") or ""
            drug_list = [d.strip() for d in drugs_raw.split(";") if d.strip()][:6]
            if drug_list:
                pills = "".join(f"<span>{html.escape(d)}</span>" for d in drug_list)
                drugs_html = f'<div class="src-drugs">{pills}</div>'
            else:
                drugs_html = ""

            st.markdown(
                f"""
                <div class="src-card">
                  <div class="src-head">
                    <span class="src-doc">Doc {f['n']}</span>
                    <span class="src-type">{src_lbl}</span>
                    <span class="src-sim" style="color:{color}">● {pct}% confianza</span>
                  </div>
                  <div class="src-title">{title}</div>
                  <div class="src-bar"><i style="width:{pct}%;background:{color}"></i></div>
                  {snippet_html}
                  {drugs_html}
                  <div class="src-meta">{id_lbl} · {frag_txt}</div>
                  <a class="src-link" href="{url}" target="_blank">Ver fuente ↗</a>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _render_other_sources(sources):
    """Fuentes recuperadas por afinidad que la respuesta NO citó. Se muestran en un
    desplegable aparte y CADA UNA con su motivo (transparencia): así el usuario ve
    por qué aparecieron sin confundirlas con las fuentes en las que MIA se apoyó."""
    if not sources:
        return
    with st.expander(f"También recuperadas · no citadas ({len(sources)})",
                     icon=":material/inventory_2:", expanded=False):
        st.caption("MIA recuperó estas fuentes por su afinidad con la pregunta, pero "
                   "la respuesta no se apoyó en ellas. Se listan con el motivo por "
                   "transparencia (no cuentan como fuentes citadas).")
        for f in sources:
            title = html.escape(f.get("title") or "(sin título)")
            id_lbl = html.escape(_id_label(f.get("source"), f.get("doc_id")))
            src_lbl = html.escape(_source_type_label(f.get("source")))
            url = html.escape(f.get("url") or "#")
            pct = _confidence_pct(float(f.get("similarity", 0) or 0))
            st.markdown(
                f"""
                <div class="src-card">
                  <div class="src-head">
                    <span class="src-doc">Doc {f['n']}</span>
                    <span class="src-type">{src_lbl}</span>
                    <span class="src-sim" style="color:var(--mia-slate)">● {pct}% afinidad</span>
                  </div>
                  <div class="src-title">{title}</div>
                  <div class="src-reason">{_relevance_reason(f)}</div>
                  <div class="src-meta">{id_lbl}</div>
                  <a class="src-link" href="{url}" target="_blank">Ver fuente ↗</a>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _split_cited(data):
    """Separa las fuentes en (citadas, solo-recuperadas) según qué [Doc N] aparecen
    DE VERDAD en el texto de la respuesta (citations.cited_docs). Así el gráfico y
    los KPIs hablan solo de los papers en los que la respuesta se apoya, y el resto
    (recuperados por afinidad pero no citados) se muestran aparte, con su motivo.

    Si la respuesta no trae ninguna cita válida, tratamos la mejor fuente como
    citada (para no ocultarlo todo) y el resto como recuperadas.
    """
    sources = data.get("sources") or []
    texto = data.get("answer") or data.get("content") or ""
    cited_n = citations.cited_docs(texto, len(sources))
    if not cited_n and sources:
        cited_n = {sources[0].get("n")}
    citadas = [s for s in sources if s.get("n") in cited_n]
    otras = [s for s in sources if s.get("n") not in cited_n]
    return citadas, otras


def _relevance_reason(f):
    """Frase corta de POR QUÉ se recuperó una fuente que la respuesta NO citó.
    Responde a la pregunta 'si me la das, dime por qué': qué fármaco/cifras cubre
    y el recordatorio de que la respuesta no se apoyó en ella."""
    partes = []
    drugs = [d.strip() for d in (f.get("drugs") or "").split(";") if d.strip()][:3]
    if drugs:
        partes.append("trata <b>" + html.escape(", ".join(drugs)) + "</b>")
    mets = sorted({str(o.get("metric", "")).split()[0]
                   for o in (f.get("outcomes") or []) if o.get("metric")})
    if mets:
        partes.append("aporta cifras <b>" + html.escape(", ".join(mets[:3])) + "</b>")
    detalle = ("; ".join(partes) + ". " if partes else "")
    return (f"{detalle}Se recuperó por afinidad temática con la pregunta, pero la "
            "respuesta no se apoyó en ella.")


def _render_data_cards(result, cited):
    """Fila de KPIs (Data Cards) con datos REALES de esta respuesta.

    Regla de honestidad: solo métricas que MIA calcula de verdad. La confianza
    es la SIMILITUD de recuperación (no "precisión" ni "veracidad": MIA no juzga
    la verdad de la respuesta en vivo). Los KPIs hablan de las fuentes realmente
    CITADAS (`cited`), no de todas las recuperadas. Si no hubo evidencia, no
    mostramos nada: no hay KPIs que presumir.
    """
    if not result.get("has_evidence") or not cited:
        return
    sources = cited

    # 1) Nº de fuentes citadas.
    n_fuentes = len(sources)

    # 2) Confianza = mejor similitud calibrada a % (fuentes ya ordenadas por relevancia).
    best_sim = float(sources[0].get("similarity", 0) or 0)
    pct = _confidence_pct(best_sim)
    dot = _conf_color(pct)

    # 3) Desglose PubMed vs ClinicalTrials (agregado real por 'source').
    n_pm = sum(1 for s in sources if "pubmed" in (s.get("source") or "").lower())
    n_ct = n_fuentes - n_pm

    cards = [
        f'<div class="mia-kpi"><div class="k-num">{n_fuentes}</div>'
        f'<div class="k-lbl">Fuentes citadas</div></div>',
        f'<div class="mia-kpi" title="Confianza de recuperación calibrada 0-100% '
        f'a partir de la afinidad MedCPT (umbral de evidencia ≈ 44%). No es una '
        f'probabilidad de veracidad."><div class="k-num">'
        f'<span class="k-dot" style="background:{dot}"></span>{pct}%</div>'
        f'<div class="k-lbl">Confianza recup.</div></div>',
        f'<div class="mia-kpi"><div class="k-num">{n_pm} / {n_ct}</div>'
        f'<div class="k-lbl">PubMed / CT</div></div>',
    ]

    # 4) Card del Scout: solo si actuó y trajo evidencia nueva.
    if result.get("used_scout"):
        new_chunks = int((result.get("scout") or {}).get("new_chunks", 0) or 0)
        if new_chunks > 0:
            cards.append(
                f'<div class="mia-kpi k-scout"><div class="k-num">{new_chunks}</div>'
                f'<div class="k-lbl">Chunks nuevos</div></div>'
            )

    st.markdown(f'<div class="mia-kpis">{"".join(cards)}</div>',
                unsafe_allow_html=True)


def _render_outcomes_chart(sources):
    """Gráfico de barras con las CIFRAS extraídas verbatim de las fuentes.

    Cada barra es un número real hallado en el abstract (EASI 75, IGA 0/1…). NO lo
    genera el LLM: es determinista y trazable. Las cifras se AGRUPAN por paper: la
    referencia [Doc N] + título se muestra UNA sola vez, en la cabecera del grupo,
    y las cifras de ese mismo paper van debajo SIN repetir la cita en cada dato
    (si son del mismo paper, no hace falta re-citarlo). `sources` debe venir ya
    filtrada a las fuentes realmente CITADAS por la respuesta.
    """
    # (fuente, sus cifras ordenadas de mayor a menor) — solo papers con cifras.
    grupos = [
        (f, sorted((f.get("outcomes") or []), key=lambda p: p.get("value", 0), reverse=True))
        for f in (sources or [])
    ]
    grupos = [(f, ocs) for f, ocs in grupos if ocs]
    if not grupos:
        return

    max_val = max((oc.get("value", 0) for _, ocs in grupos for oc in ocs), default=100) or 100

    bloques = []
    for f, ocs in grupos:
        titulo = html.escape((f.get("title") or "(sin título)")[:80])
        cabecera = (
            f'<div class="oc-group">'
            f'<span class="oc-doc">Doc {int(f.get("n", 0))}</span>'
            f'<span class="oc-group-title">{titulo}</span></div>'
        )
        filas = []
        for p in ocs[:8]:  # tope por paper para no saturar la vista
            val = float(p.get("value", 0) or 0)
            wk = p.get("week")
            etiqueta = html.escape(str(p.get("metric", "")))
            sub = f"semana {html.escape(str(wk))}" if wk else ""
            ancho = int(round(val / max_val * 100))
            filas.append(
                f'<div class="oc-row">'
                f'<div class="oc-lbl">{etiqueta}<span class="oc-sub">{sub}</span></div>'
                f'<div class="oc-track"><i style="width:{ancho}%"></i></div>'
                f'<span class="oc-val">{val:g}%</span>'
                f'</div>'
            )
        bloques.append(cabecera + "".join(filas))

    st.markdown(
        f'<div class="mia-chart">'
        f'<div class="oc-head">{_icon("cite")} Datos clave de la evidencia'
        f'<span class="oc-note">cifras citadas literalmente del abstract · '
        f'no generadas por el modelo</span></div>'
        f'{"".join(bloques)}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_answer(texto: str):
    """Pinta la respuesta del modelo con las citas resaltadas."""
    st.markdown(_highlight_citations(texto), unsafe_allow_html=True)


# ==========================================================================
# 4) Historial de la conversación (persiste entre interacciones)
# ==========================================================================
if "messages" not in st.session_state:
    st.session_state.messages = []

# Estado vacío: bienvenida + galería de ejemplos clicables, agrupados por INTENCIÓN
# (eficacia / seguridad / comparativa) → el usuario ve de un vistazo qué sabe hacer.
EJEMPLOS_POR_INTENCION = [
    ("Eficacia", ":material/trending_up:", [
        "Is lebrikizumab effective for atopic dermatitis?",
        "What is the efficacy of dupilumab in atopic dermatitis?",
    ]),
    ("Seguridad", ":material/health_and_safety:", [
        "What are the most common adverse events of upadacitinib?",
        "Is baricitinib safe for long-term use in atopic dermatitis?",
    ]),
    ("Comparativa", ":material/compare_arrows:", [
        "How does dupilumab compare to tralokinumab in safety?",
        "Dupilumab vs upadacitinib efficacy in atopic dermatitis?",
    ]),
]

if not st.session_state.messages:
    st.markdown(
        """
        <div class="mia-welcome">
          <h3>Bienvenido/a</h3>
          <p>Pregunta sobre la evidencia de la dermatitis atópica y sus fármacos.
             Todo se ejecuta <b>en local</b>: cada respuesta cita las fuentes exactas
             que la respaldan y, si no hay evidencia suficiente, MIA lo dice en vez de
             inventar. Prueba con un ejemplo:</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.write("")
    for titulo, icono, ejemplos in EJEMPLOS_POR_INTENCION:
        st.caption(f"{icono} **{titulo}**")
        cols = st.columns(len(ejemplos))
        for col, ej in zip(cols, ejemplos):
            if col.button(ej, width="stretch", key=f"ej_{ej[:24]}"):
                st.session_state.pending_q = ej
                st.rerun()


def _render_scout_panel(scout):
    """Panel con marca del agente Scout (sustituye al antiguo st.info)."""
    if not scout:
        return
    term = html.escape(str(scout.get("term") or ""))
    new_chunks = int(scout.get("new_chunks", 0) or 0)
    st.markdown(
        f"""
        <div class="mia-panel mia-panel-scout">
          <div class="p-head"><span class="p-dot"></span>{_icon("search")} Agente Scout activado</div>
          No había evidencia local suficiente. Busqué «<b>{term}</b>» e importé
          <b>{new_chunks}</b> fragmentos nuevos de PubMed/ClinicalTrials.
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_generation_error(exc):
    """Tarjeta accionable cuando la generación falla (Ollama caído, etc.). Re-chequea
    el estado en vivo (sin caché) para dar la solución exacta en vez de un traceback."""
    s = status.system_status()
    hints = status.fix_hints(s)
    if not hints:  # el sistema parece OK → error inesperado; damos la pista técnica
        hints = [f"Error inesperado del modelo: `{type(exc).__name__}: {exc}`. "
                 "Reintenta; si persiste, revisa que Ollama tenga memoria suficiente."]
    st.error(
        "**No he podido generar la respuesta.**\n\n" + "\n".join(f"- {h}" for h in hints),
        icon=":material/error:",
    )


def _render_no_evidence_panel(texto: str):
    """Panel ámbar de 'sin evidencia local' (estado has_evidence=False)."""
    st.markdown(
        f"""
        <div class="mia-panel mia-panel-empty">
          <div class="p-head"><span class="p-dot"></span>Sin evidencia local</div>
          {html.escape(texto)}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_assistant(data):
    """Pinta un turno del asistente. Usa las MISMAS piezas en vivo y en el
    historial → el re-render tras st.rerun() es idéntico.

    `data` acepta tanto el dict de `rag/scout` (clave 'answer') como el mensaje
    guardado en el historial (clave 'content').
    """
    texto = data.get("answer") or data.get("content") or ""
    has_evidence = data.get("has_evidence")

    # Conversación dinámica: si la pregunta era un follow-up y se resolvió a una
    # pregunta autónoma, lo decimos (transparencia: el usuario ve CÓMO se interpretó).
    condensed = data.get("condensed_question")
    if condensed:
        st.caption(f":material/subdirectory_arrow_right: Interpreté tu pregunta como: "
                   f"*{condensed}*")

    if has_evidence is False:
        # Estado sin evidencia: el texto ya es el mensaje explicativo → panel.
        _render_no_evidence_panel(texto)
        if data.get("used_scout"):
            _render_scout_panel(data.get("scout"))
        return

    # Separamos las fuentes CITADAS por la respuesta de las solo-recuperadas: el
    # gráfico, los KPIs y las "fuentes citadas" hablan solo de las primeras; las
    # segundas van en su propio bloque, cada una con su motivo.
    citadas, otras = _split_cited(data)

    _render_answer(texto)
    _render_outcomes_chart(citadas)   # cifras verbatim, agrupadas por paper citado
    _render_data_cards(data, citadas) # KPIs sobre las fuentes realmente citadas

    if data.get("used_scout"):
        _render_scout_panel(data.get("scout"))

    _render_sources(citadas)          # "Fuentes citadas" = solo las que cita el texto
    _render_other_sources(otras)      # recuperadas-no-citadas, con su motivo
    _render_export_button(data, texto, citadas)


def _render_export_button(data, texto, citadas):
    """Botón 'Descargar informe': genera un HTML autónomo (imprimible → PDF) con la
    respuesta, sus citas, los datos clave y las fuentes. Solo si hay algo que citar."""
    if not citadas:
        return
    pregunta = data.get("question") or data.get("condensed_question") or ""
    html_report = report.build_report_html(pregunta, data)
    # Clave estable entre reruns (hash del contenido) para que Streamlit no se queje.
    key = "dl_" + hashlib.md5((pregunta + texto).encode("utf-8")).hexdigest()[:10]
    st.download_button(
        "Descargar informe (HTML → PDF)",
        data=html_report,
        file_name=f"MIA_informe_{datetime.now():%Y%m%d_%H%M}.html",
        mime="text/html",
        key=key,
        icon=":material/download:",
        help="Documento con marca, con la pregunta, la respuesta citada y las fuentes. "
             "Ábrelo y usa Ctrl+P → Guardar como PDF para compartirlo.",
    )


# Re-pintamos todo el historial en cada recarga (así es Streamlit).
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant":
            _render_assistant(msg)
        else:
            st.markdown(msg["content"])


# ==========================================================================
# 5) Entrada del usuario
# ==========================================================================
# La pregunta puede venir del cuadro de chat o de un botón de ejemplo.
pending = st.session_state.pop("pending_q", None)
pregunta = st.chat_input("Ask a question about the evidence (in English)…") or pending

if pregunta:
    # Conversación dinámica: el historial son los turnos ANTERIORES a esta
    # pregunta (todo lo que ya hay en messages). Se lo pasamos al pipeline para
    # que resuelva follow-ups ("and its safety?") a una pregunta autónoma.
    historial = list(st.session_state.messages)

    # 1) Mostramos y guardamos la pregunta del usuario.
    st.session_state.messages.append({"role": "user", "content": pregunta})
    with st.chat_message("user"):
        st.markdown(pregunta)

    # 2) Generamos la respuesta. Envuelto en try/except: un fallo (p. ej. Ollama
    #    caído a mitad) muestra una tarjeta accionable en vez de reventar la app.
    with st.chat_message("assistant"):
        resultado = None
        try:
            with st.spinner("Buscando evidencia y razonando con el modelo local…"):
                if usar_scout:
                    resultado = scout.answer_with_scout(pregunta, history=historial)
                else:
                    resultado = rag.answer(pregunta, history=historial)
                    resultado["used_scout"] = False
        except Exception as exc:  # noqa: BLE001 — queremos degradar con elegancia
            _render_generation_error(exc)

        if resultado is not None:
            resultado["question"] = pregunta  # para el informe exportable
            # Mismo renderizador que el historial (respuesta + Data Cards + paneles).
            _render_assistant(resultado)

    # 3) Guardamos la respuesta en el historial (solo si se generó). Persistimos las
    #    señales que necesitan las Data Cards / paneles / informe para re-pintarse igual.
    if resultado is not None:
        st.session_state.messages.append({
            "role": "assistant",
            "content": resultado["answer"],
            "question": pregunta,
            "sources": resultado.get("sources"),
            "has_evidence": resultado.get("has_evidence"),
            "used_scout": resultado.get("used_scout"),
            "scout": resultado.get("scout"),
            "condensed_question": resultado.get("condensed_question"),
        })
