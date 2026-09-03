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
      /* ====================================================================
         PALETA "AURORA LSHC"
         --------------------------------------------------------------------
         Fondo nocturno + acentos de aurora boreal, con los colores que se usan
         en Life Sciences & Health Care: verde-azulado clínico, cian, verde
         salud y un violeta de apoyo. NO son colores elegidos por bonitos: el
         verde-azulado y el cian son los tonos de confianza del sector sanitario,
         y el violeta se reserva para lo secundario (la línea Centivence).

         Regla de contraste: el aurora vive SOLO en el fondo y en los bordes.
         Todo lo que hay que LEER va sobre una superficie sólida y oscura. En una
         herramienta clínica, la legibilidad manda sobre el efecto.
         ==================================================================== */
      :root {
        /* Fondos: azul casi negro, como cielo nocturno */
        --mia-bg:      #070d14;
        --mia-bg-soft: #0e1723;   /* superficie elevada (tarjetas, paneles) */
        --mia-bg-2:    #132030;   /* superficie más elevada (hover, chips) */

        /* Acentos de la aurora (LSHC) */
        --mia-teal:    #2fe0a8;   /* verde-azulado brillante: acento principal */
        --mia-teal-d:  #16b98a;   /* verde-azulado profundo */
        --mia-cyan:    #38bdf8;   /* cian: segundo velo de la aurora */
        --mia-green:   #7ee787;   /* verde salud: confirmaciones */
        --mia-violet:  #8b7cf6;   /* violeta: Centivence / secundario */
        --mia-amber:   #fbbf24;   /* ámbar: avisos y seguridad */

        /* Texto */
        --mia-ink:     #e8f1f5;   /* texto principal, blanco frío */
        --mia-slate:   #93a7b8;   /* texto secundario */
        --mia-line:    rgba(148,180,200,.16);  /* hairline translúcida */

        --mia-success:   #2fe0a8;
        --mia-success-2: #7ee787;
        --mia-mint:      rgba(47,224,168,.12); /* fondo de insignia */
        --mia-track:     rgba(148,180,200,.13);
        --mia-accent-br: #38bdf8;

        --mia-font: 'Inter','Segoe UI',-apple-system,BlinkMacSystemFont,Roboto,Helvetica,Arial,sans-serif;
        --mia-mono: 'JetBrains Mono','Cascadia Code',Consolas,'SFMono-Regular',ui-monospace,monospace;
      }

      html, body, [class*="css"], .stMarkdown, .block-container { font-family: var(--mia-font); }
      .block-container { padding-top: 2.2rem; max-width: 860px; }
      .stApp { background: var(--mia-bg); }

      /* ====================================================================
         EL FONDO AURORA
         --------------------------------------------------------------------
         Tres "velos" de luz: cada uno es un radial-gradient muy difuminado que
         se desvanece a transparente. Se superponen y se mueven MUY despacio
         (30-45 s) con desfases distintos, así que nunca repiten la misma forma
         — que es justo como se comporta una aurora real.

         `filter: blur(70px)` es lo que convierte tres manchas de color en luz
         difusa. `pointer-events:none` evita que la capa intercepte clics, y
         `position:fixed` la mantiene quieta al hacer scroll.
         ==================================================================== */
      .stApp::before {
        content: "";
        position: fixed; inset: -25% -12% auto -12%;
        height: 95vh; z-index: 0; pointer-events: none;
        /* Elipses ANCHAS y BAJAS (28% de alto), no círculos: una aurora real cae
           en cortinas horizontales, no en manchas redondas. El ligero giro las
           inclina como el arco auroral sobre el horizonte. */
        background:
          radial-gradient(58% 26% at 14% 20%, rgba(47,224,168,.58) 0%, transparent 70%),
          radial-gradient(52% 24% at 48% 10%, rgba(56,189,248,.46) 0%, transparent 68%),
          radial-gradient(50% 28% at 84% 26%, rgba(139,124,246,.44) 0%, transparent 68%),
          radial-gradient(64% 20% at 40% 38%, rgba(126,231,135,.26) 0%, transparent 72%);
        filter: blur(78px) saturate(145%);
        transform: rotate(-4deg);
        animation: auroraDrift 42s ease-in-out infinite alternate;
      }
      /* Segundo velo, más bajo y verde, para dar profundidad al degradado. */
      .stApp::after {
        content: "";
        position: fixed; inset: auto -18% -32% -18%;
        height: 62vh; z-index: 0; pointer-events: none;
        background:
          radial-gradient(60% 30% at 26% 82%, rgba(126,231,135,.32) 0%, transparent 72%),
          radial-gradient(56% 26% at 74% 92%, rgba(22,185,138,.34) 0%, transparent 70%),
          radial-gradient(48% 24% at 92% 70%, rgba(56,189,248,.22) 0%, transparent 70%);
        filter: blur(92px) saturate(135%);
        transform: rotate(3deg);
        animation: auroraDrift2 55s ease-in-out infinite alternate;
      }
      /* OJO: cada keyframe repite rotate(). `transform` es UNA sola propiedad,
         así que si la animación solo pone translate/scale, machaca la rotación
         declarada en la regla base y la cortina se endereza a mitad del ciclo. */
      @keyframes auroraDrift {
        0%   { transform: rotate(-4deg) translate3d(0,0,0)     scale(1);    opacity:.90; }
        50%  { transform: rotate(-2deg) translate3d(4%,2%,0)   scale(1.12); opacity:1;   }
        100% { transform: rotate(-6deg) translate3d(-3%,-2%,0) scale(1.05); opacity:.82; }
      }
      @keyframes auroraDrift2 {
        0%   { transform: rotate(3deg) translate3d(0,0,0)    scale(1.06); opacity:.72; }
        100% { transform: rotate(5deg) translate3d(5%,-3%,0) scale(1);    opacity:1;   }
      }
      /* Accesibilidad: quien pida menos movimiento en su sistema, ve la aurora
         quieta. El efecto se mantiene; solo se detiene la animación. */
      @media (prefers-reduced-motion: reduce) {
        .stApp::before, .stApp::after { animation: none; }
      }
      /* El contenido va POR ENCIMA de los velos. */
      .block-container, section[data-testid="stSidebar"] { position: relative; z-index: 1; }

      /* ====================================================================
         CABECERA DE MARCA
         ==================================================================== */
      .mia-hero {
        position: relative; overflow: hidden;
        background:
          linear-gradient(135deg, rgba(47,224,168,.16) 0%, rgba(56,189,248,.10) 45%,
                                  rgba(139,124,246,.14) 100%),
          var(--mia-bg-soft);
        border: 1px solid var(--mia-line);
        border-radius: 22px; padding: 28px 32px; color: var(--mia-ink);
        box-shadow: 0 18px 50px -22px rgba(47,224,168,.35),
                    inset 0 1px 0 rgba(255,255,255,.06);
        margin-bottom: 8px;
      }
      /* Filamento de luz superior: el "borde" brillante de la aurora. */
      .mia-hero::before {
        content: ""; position: absolute; inset: 0 0 auto 0; height: 2px;
        background: linear-gradient(90deg, transparent, var(--mia-teal) 22%,
                                    var(--mia-cyan) 52%, var(--mia-violet) 78%, transparent);
        opacity: .85;
      }
      .mia-hero h1 {
        font-size: 1.95rem; font-weight: 800; margin: 0;
        letter-spacing: -.025em;
        background: linear-gradient(100deg, #ffffff 0%, var(--mia-teal) 55%, var(--mia-cyan) 100%);
        -webkit-background-clip: text; background-clip: text;
        -webkit-text-fill-color: transparent; color: var(--mia-teal);
      }
      .mia-hero .tag { font-size: .95rem; color: var(--mia-slate); margin-top: 6px; }
      .mia-hero .tag b { color: var(--mia-ink); }
      .mia-badges { margin-top: 16px; display: flex; flex-wrap: wrap; gap: 8px; }
      .mia-badges span {
        background: rgba(47,224,168,.10);
        border: 1px solid rgba(47,224,168,.28);
        color: #bff3e0;
        padding: 5px 12px; border-radius: 999px;
        font-size: .78rem; font-weight: 600;
      }

      /* --- Tira "cómo funciona" --- */
      .mia-how { display: flex; gap: 10px; flex-wrap: wrap; margin: 12px 0 6px; }
      .mia-how .step {
        flex: 1 1 210px;
        background: var(--mia-bg-soft);
        border: 1px solid var(--mia-line);
        border-radius: 14px; padding: 13px 15px;
      }
      .mia-how .s-n {
        font-family: var(--mia-mono); font-size: .64rem; font-weight: 700;
        letter-spacing: .1em; color: var(--mia-teal); margin-bottom: 5px;
      }
      .mia-how .s-t { font-size: .88rem; font-weight: 700; color: var(--mia-ink); }
      .mia-how .s-d { font-size: .8rem; color: var(--mia-slate); margin-top: 3px; line-height: 1.45; }

      /* --- Insignia de cita [Doc N] --- */
      .cite {
        display: inline-block;
        background: var(--mia-mint); color: var(--mia-teal);
        border: 1px solid rgba(47,224,168,.35);
        font-size: .74rem; font-weight: 700;
        padding: 1px 7px; border-radius: 6px;
        margin: 0 1px; white-space: nowrap; vertical-align: baseline;
      }

      /* --- Tarjeta de fuente --- */
      .src-card {
        border: 1px solid var(--mia-line);
        border-left: 3px solid var(--mia-teal);
        border-radius: 14px;
        padding: 15px 17px; margin-bottom: 12px;
        background: var(--mia-bg-soft);
      }
      .src-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
      .src-doc {
        background: linear-gradient(120deg, var(--mia-teal-d), var(--mia-teal));
        color: #04231a; font-size: .72rem; font-weight: 800;
        padding: 2px 9px; border-radius: 6px;
      }
      .src-type {
        font-size: .72rem; font-weight: 700; letter-spacing: .04em;
        color: var(--mia-slate); text-transform: uppercase;
      }
      /* NOTA: aquí vivían .src-sim (badge "% match") y .src-bar (barra de
         afinidad). Se retiraron el 1-sep-2026 — ver _render_sources. */
      .src-title {
        font-size: .98rem; font-weight: 600; color: var(--mia-ink);
        margin: 10px 0 6px; line-height: 1.38;
      }
      .src-meta { font-size: .8rem; color: var(--mia-slate); margin-bottom: 8px; }
      .src-snippet {
        font-size: .86rem; color: #c3d4e0; line-height: 1.55;
        border-left: 2px solid rgba(47,224,168,.35);
        padding: 6px 0 6px 12px; margin: 4px 0 10px;
      }
      /* Comillas tipográficas LITERALES, no el escape CSS "\\201C". Motivo: este
         CSS vive dentro de una cadena de Python, y ahí "\201" es un escape OCTAL
         → Python lo convertía en el carácter de control 0x81 y dejaba la "C"
         suelta, así que los fragmentos empezaban por "▮C" en vez de por “. */
      .src-snippet::before { content: "“"; }
      .src-snippet::after  { content: "”"; }
      .src-drugs { display: flex; gap: 6px; flex-wrap: wrap; margin: 0 0 10px; }
      .src-drugs span {
        font-family: var(--mia-mono); font-size: .7rem; font-weight: 500;
        background: rgba(47,224,168,.10); color: var(--mia-teal);
        border: 1px solid rgba(47,224,168,.25); padding: 2px 8px; border-radius: 999px;
      }
      .src-reason {
        font-size: .83rem; color: var(--mia-slate); background: rgba(148,180,200,.06);
        border: 1px solid var(--mia-line); border-radius: 10px;
        padding: 8px 10px; margin: 4px 0 8px;
      }
      .src-link {
        display: inline-block; font-size: .82rem; font-weight: 600;
        color: var(--mia-teal); text-decoration: none;
      }
      .src-link:hover { text-decoration: underline; }

      /* --- Data Cards (KPIs) --- */
      .mia-kpis { display: flex; gap: 10px; flex-wrap: wrap; margin: 10px 0 4px; }
      .mia-kpi {
        flex: 1 1 120px;
        border: 1px solid var(--mia-line); border-radius: 14px;
        padding: 13px 15px; background: var(--mia-bg-soft);
      }
      .mia-kpi .k-num {
        font-family: var(--mia-mono); font-size: 1.5rem; font-weight: 600;
        color: var(--mia-ink); line-height: 1.1; letter-spacing: -.01em;
      }
      .mia-kpi .k-lbl {
        font-family: var(--mia-mono); font-size: .64rem; font-weight: 600;
        letter-spacing: .09em; text-transform: uppercase;
        color: var(--mia-slate); margin-top: 5px;
      }
      .mia-kpi .k-dot {
        display: inline-block; width: 7px; height: 7px; border-radius: 999px;
        margin-right: 6px; vertical-align: middle; box-shadow: 0 0 10px currentColor;
      }
      .mia-kpi.k-scout { border-left: 3px solid var(--mia-cyan); }

      /* --- Gráfico de cifras --- */
      .mia-chart {
        border: 1px solid var(--mia-line); border-radius: 14px;
        padding: 16px 18px; margin: 12px 0; background: var(--mia-bg-soft);
      }
      .mia-chart .oc-head {
        font-size: .82rem; font-weight: 700; color: var(--mia-ink);
        display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
        margin-bottom: 10px;
      }
      .mia-chart .oc-note {
        font-family: var(--mia-mono); font-size: .66rem; font-weight: 500;
        color: var(--mia-slate); text-transform: uppercase; letter-spacing: .05em;
      }
      .oc-row { display: flex; align-items: center; gap: 10px; margin: 7px 0; }
      .oc-lbl { flex: 0 0 190px; font-size: .82rem; color: var(--mia-ink); }
      .oc-sub { display: block; font-size: .68rem; color: var(--mia-slate); }
      .oc-track { flex: 1; height: 9px; border-radius: 999px;
                  background: var(--mia-track); overflow: hidden; }
      .oc-track > i {
        display: block; height: 100%; border-radius: 999px;
        background: linear-gradient(90deg, var(--mia-teal-d), var(--mia-teal));
        box-shadow: 0 0 14px rgba(47,224,168,.55);
      }
      .oc-val { font-family: var(--mia-mono); font-size: .8rem; font-weight: 700;
                color: var(--mia-teal); min-width: 52px; text-align: right; }
      .oc-doc {
        font-family: var(--mia-mono); font-size: .68rem; font-weight: 700;
        color: var(--mia-teal); background: var(--mia-mint);
        border: 1px solid rgba(47,224,168,.3); padding: 2px 7px; border-radius: 6px;
      }
      .oc-group {
        display: flex; align-items: center; gap: 8px;
        margin: 14px 0 6px; padding-top: 11px; border-top: 1px dashed var(--mia-line);
      }
      .oc-group:first-of-type { border-top: none; padding-top: 0; margin-top: 2px; }
      .oc-group-title {
        font-size: .78rem; font-weight: 600; color: var(--mia-ink);
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }
      /* Sub-cabecera por TIPO de cifra. Separar eficacia de seguridad no es
         cosmético: un EASI-75 alto es bueno y una tasa de evento adverso alta es
         mala; sin la etiqueta, dos barras iguales se leen igual. */
      .oc-kind {
        font-family: var(--mia-mono); font-size: .63rem; font-weight: 700;
        letter-spacing: .09em; text-transform: uppercase; margin: 10px 0 2px;
      }
      .oc-kind-efficacy { color: var(--mia-teal); }
      .oc-kind-safety   { color: var(--mia-amber); }
      .oc-kind-safety ~ .oc-row .oc-track > i {
        background: linear-gradient(90deg, #b4790d, var(--mia-amber));
        box-shadow: 0 0 14px rgba(251,191,36,.45);
      }
      .oc-kind-safety ~ .oc-row .oc-val { color: var(--mia-amber); }

      /* --- Paneles de estado (Scout / sin evidencia) --- */
      .mia-panel {
        border-radius: 14px; padding: 15px 17px; margin: 10px 0;
        font-size: .9rem; line-height: 1.55;
        background: var(--mia-bg-soft); border: 1px solid var(--mia-line);
        color: var(--mia-ink);
      }
      .mia-panel .p-head {
        font-weight: 700; font-size: .8rem; letter-spacing: .04em;
        text-transform: uppercase; margin-bottom: 6px;
        display: flex; align-items: center; gap: 7px;
      }
      .mia-panel .p-dot {
        width: 8px; height: 8px; border-radius: 999px; display: inline-block;
      }
      .mia-panel-scout { border-left: 3px solid var(--mia-cyan); }
      .mia-panel-scout .p-head { color: var(--mia-cyan); }
      .mia-panel-scout .p-dot  { background: var(--mia-cyan); box-shadow: 0 0 10px var(--mia-cyan); }
      .mia-panel-empty { border-left: 3px solid var(--mia-amber); }
      .mia-panel-empty .p-head { color: var(--mia-amber); }
      .mia-panel-empty .p-dot  { background: var(--mia-amber); box-shadow: 0 0 10px var(--mia-amber); }

      /* --- Estado vacío / bienvenida --- */
      .mia-welcome {
        border: 1px solid var(--mia-line); border-radius: 16px;
        padding: 20px 22px; margin: 14px 0 8px;
        background: var(--mia-bg-soft);
      }
      .mia-welcome h3 { margin: 0 0 6px; font-size: 1.05rem; color: var(--mia-ink); }
      .mia-welcome p  { margin: 0; font-size: .9rem; color: var(--mia-slate); line-height: 1.6; }

      /* ====================================================================
         WIDGETS NATIVOS DE STREAMLIT
         config.toml tiñe los colores base, pero los bordes y superficies hay
         que ajustarlos aquí para que no desentonen con el fondo aurora.
         ==================================================================== */
      section[data-testid="stSidebar"] {
        background: rgba(10,17,26,.86);
        border-right: 1px solid var(--mia-line);
        backdrop-filter: blur(12px);
      }
      div[data-testid="stExpander"] {
        border: 1px solid var(--mia-line) !important;
        border-radius: 14px !important;
        background: rgba(14,23,35,.7) !important;
        backdrop-filter: blur(8px);
      }
      div[data-testid="stExpander"] summary:hover { color: var(--mia-teal) !important; }
      /* El cuadro de chat crece solo según el texto, pero en el PRIMER render
         Streamlit calcula mal su altura (medido: 182 px con una sola línea, que
         debería ocupar 28) y se queda enorme hasta que haces clic. No lo provoca
         este CSS —comprobado desactivándolo: el valor no cambia—, es un fallo de
         medición del propio Streamlit. El tope lo acota: sigue creciendo para
         preguntas de varias líneas, pero nunca se dispara. */
      div[data-testid="stChatInput"] textarea {
        color: var(--mia-ink) !important;
        max-height: 92px !important;
      }
      .stButton > button {
        border: 1px solid var(--mia-line);
        background: var(--mia-bg-soft);
        color: var(--mia-ink);
        border-radius: 10px; font-size: .84rem;
        transition: border-color .15s ease, box-shadow .15s ease;
      }
      .stButton > button:hover {
        border-color: rgba(47,224,168,.55);
        box-shadow: 0 0 18px -4px rgba(47,224,168,.5);
        color: var(--mia-teal);
      }
      .stDownloadButton > button {
        border: 1px solid rgba(47,224,168,.4);
        background: rgba(47,224,168,.10);
        color: var(--mia-teal); border-radius: 10px; font-weight: 600;
      }
      .stDownloadButton > button:hover {
        background: rgba(47,224,168,.18);
        box-shadow: 0 0 22px -6px rgba(47,224,168,.6);
      }
      div[data-testid="stChatMessage"] {
        background: rgba(14,23,35,.55);
        border: 1px solid var(--mia-line);
        border-radius: 16px; padding: 14px 16px;
        backdrop-filter: blur(6px);
      }
      hr, div[data-testid="stDivider"] { border-color: var(--mia-line) !important; }

      /* --- Iconos SVG inline --- */
      .mia-ic {
        width: 1em; height: 1em;
        display: inline-block; vertical-align: -.14em;
        stroke: currentColor; fill: none;
      }
      .mia-hero h1 .mia-ic { vertical-align: -.10em; margin-right: 8px;
                             stroke: var(--mia-teal); }
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
      <div class="tag">Biomedical evidence, <b>100% local and sovereign</b> — your data
        never leaves this computer · {html.escape(config.DISEASE)}</div>
      <div class="mia-badges">
        <span>{_icon("lock")}100% local · no cloud</span>
        <span>{_icon("cite")}Every figure traceable to its abstract</span>
        <span>{_icon("shield")}No evidence, no answer</span>
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
      <div class="step"><div class="s-n">01 · RETRIEVE</div>
        <div class="s-t">Local biomedical search</div>
        <div class="s-d">MedCPT (NCBI embeddings) finds the evidence in your own
          corpus, without calling any external API.</div></div>
      <div class="step"><div class="s-n">02 · CITE</div>
        <div class="s-t">Every claim to its source</div>
        <div class="s-d">The local model answers and the [Doc N] citations are placed
          deterministically: every figure is traceable to its abstract.</div></div>
      <div class="step"><div class="s-n">03 · EXTEND</div>
        <div class="s-t">Scout agent when evidence is missing</div>
        <div class="s-d">If the local corpus is not enough, it queries PubMed and
          ClinicalTrials, imports the evidence and retries — it never invents.</div></div>
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
        "**MIA is not ready yet.** Something needs starting before you can ask:\n\n"
        + "\n".join(f"- {h}" for h in _hints),
        icon=":material/build:",
    )


# ==========================================================================
# 2) Barra lateral: información y ajustes
# ==========================================================================
with st.sidebar:
    st.header(":material/settings: Settings")
    usar_scout = st.toggle(
        "Scout agent",
        value=True,
        help="If there is not enough local evidence, it searches PubMed and "
             "ClinicalTrials, imports the results and retries.",
    )
    st.divider()
    st.subheader(":material/smart_toy: Models (local)")
    st.caption(f":material/neurology: Biomedical: `{config.LLM_MODEL}`")
    # Mostramos el embedding REALMENTE activo (según EMBEDDING_BACKEND), no una
    # constante fija: si estamos en MedCPT, decir 'bge' sería engañoso.
    if getattr(config, "EMBEDDING_BACKEND", "") == "medcpt":
        _emb_label = "MedCPT (NCBI) · biomedical, 2 towers"
    else:
        _emb_label = config.EMBEDDING_MODEL
    st.caption(f":material/tag: Embeddings: `{_emb_label}`")
    st.divider()
    # --- Perfil de dominio (patología) ---------------------------------------
    # MIA ya no está atada a la dermatitis atópica: cada patología es un perfil en
    # domains/<slug>.json con su propio corpus (colección de ChromaDB). Cambiar
    # aquí re-activa el perfil EN CALIENTE: config recalcula sus valores y se
    # vacían las cachés que dependían del dominio (colección abierta en rag,
    # colecciones de la comparativa, estado del sistema). Se persiste en
    # domains/active.txt para que la próxima ejecución arranque en el mismo.
    st.subheader(":material/coronavirus: Disease profile")
    _domains = config.list_domains()
    _labels = {}
    for _slug in _domains:
        try:
            _labels[_slug] = config.load_domain(_slug)["disease"]
        except Exception:  # noqa: BLE001 — un perfil roto no debe tumbar la app
            _labels[_slug] = _slug
    _sel = st.selectbox(
        "Active profile", _domains,
        index=_domains.index(config.DOMAIN_SLUG) if config.DOMAIN_SLUG in _domains else 0,
        format_func=lambda s: f"{_labels.get(s, s)}  ({s})",
        help="Each profile has its own disease, drugs, endpoints and indexed corpus. "
             "Create a new one with:  python build_corpus.py --disease \"…\" --drug …",
    )
    if _sel != config.DOMAIN_SLUG:
        config.activate_domain(_sel, persist=True)
        rag._COLLECTION = None                 # la colección abierta era del otro dominio
        try:
            from src import compare
            compare._COLLECTIONS.clear()
        except Exception:  # noqa: BLE001
            pass
        _cached_status.clear()
        st.session_state.messages = []         # la conversación era sobre otra patología
        st.rerun()
    st.caption(f"{len(config.DRUGS)} drugs · corpus `{config.CHROMA_COLLECTION}`")
    st.caption(f":material/tune: Evidence threshold: {config.SIMILARITY_THRESHOLD}")

    # --- Estado del sistema: semáforos reales (Ollama / modelo / corpus) ---
    st.divider()
    st.subheader(":material/monitor_heart: System status")
    _s = _cached_status()
    _ollama_ok = _s["ollama"]["up"]
    _modelo_ok = all(_s["models"].values())
    _corpus = _s["corpus"]
    st.markdown(
        f"- {'🟢' if _ollama_ok else '🔴'} **Ollama** "
        f"{'running' if _ollama_ok else 'not responding'}\n"
        f"- {'🟢' if _modelo_ok else '🔴'} **Biomedical model** "
        f"{'downloaded' if _modelo_ok else 'not found'}\n"
        f"- {'🟢' if _corpus['ok'] else '🔴'} **Corpus** "
        f"{_corpus['chunks']:,} chunks"
    )
    if not _s["ready"]:
        st.caption("⚠️ See the notice above to get it running.")

    st.info("Questions must be asked **in English** (the biomedical model is only "
            "reliable in English).", icon=":material/translate:")

    if st.session_state.get("messages"):
        st.divider()
        if st.button("Clear conversation", icon=":material/delete:",
                     width="stretch"):
            st.session_state.messages = []
            st.rerun()


# ==========================================================================
# 3) Utilidades de presentación
# ==========================================================================
def _highlight_citations(texto: str) -> str:
    """Convierte las citas '[Doc N]' del modelo en insignias visuales.

    SEGURIDAD (3-sep-2026): el texto se ESCAPA antes de insertar el HTML de las
    insignias. La respuesta la redacta el LLM a partir de abstracts descargados de
    internet; si un documento (o el propio modelo) colara una etiqueta HTML o un
    <script>, `unsafe_allow_html=True` la ejecutaría en el navegador (XSS).
    `src/report.py` ya escapaba; la app no.
    """
    seguro = html.escape(texto or "")
    return re.sub(r"\[Doc\s*(\d+)\]", r'<span class="cite">Doc \1</span>', seguro)


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


# RETIRADAS el 1-sep-2026: `_confidence_pct` (similitud cruda → % legible, con la
# banda medida 55→0%, 80→100%) y `_conf_color` (verde/salvia/ámbar según ese %).
# Ya no se pinta ningún % de afinidad en la página de respuesta, así que ambas
# quedaban muertas. La calibración NO se ha perdido: sigue viva en
# `src/compare.py::_confidence_pct`, que es donde tiene sentido enseñarla — la
# página de comparación MedCPT vs OpenAI trata precisamente de la CALIDAD DE
# RECUPERACIÓN, y ahí el número es el objeto de estudio, no un adorno.


def _render_sources(sources):
    """Pinta la lista de fuentes citadas como tarjetas dentro de un desplegable."""
    if not sources:
        return
    # EXPANDIDO por defecto: son los papers en los que se apoya la respuesta, así
    # que forman parte de la respuesta, no del aparato de diagnóstico. Lo que se
    # pliega ahora es el ruido de recuperación (ver _render_retrieval_details).
    with st.expander(f"Cited sources ({len(sources)})",
                     icon=":material/menu_book:", expanded=True):
        for f in sources:
            # Aquí NO se muestra ningún "% de afinidad". La similitud sigue
            # existiendo por dentro (ordena el ranking y guarda la puerta de
            # evidencia), pero enseñarla engañaba: invitaba a leerla como "este
            # paper responde mejor a la pregunta", cuando solo dice "este texto
            # cae cerca de la pregunta en el espacio del embedding". Medido el
            # 1-sep-2026 con "efficacy of dupilumab": los 5 papers iban de 70.16
            # a 69.23 — un empate técnico presentado como podio 60% → 57%.

            # 'n_fragments' = cuántos chunks de ESTE artículo coincidieron y se
            # unieron bajo un solo [Doc N] (citas deduplicadas por PMID/NCT).
            n_frag = f.get("n_fragments", 1)
            frag_txt = f"{n_frag} chunk" + ("s" if n_frag != 1 else "")

            title = html.escape(f.get("title") or "(untitled)")
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
                  </div>
                  <div class="src-title">{title}</div>
                  {snippet_html}
                  {drugs_html}
                  <div class="src-meta">{id_lbl} · {frag_txt}</div>
                  <a class="src-link" href="{url}" target="_blank">Open source ↗</a>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _render_other_sources(sources):
    """Fuentes recuperadas por afinidad que la respuesta NO citó, cada una con su
    motivo (transparencia): el usuario ve por qué aparecieron sin confundirlas con
    las fuentes en las que MIA se apoyó.

    Ya NO abre su propio desplegable: se pinta DENTRO de "Retrieval details", el
    único sitio donde se agrupa lo que no es la respuesta.
    """
    if not sources:
        return
    st.caption("MIA read these papers too — they came back for the same question — "
               "but no sentence in the answer could be attributed to them with "
               "confidence, so they are not cited. Listed with the reason, for "
               "transparency.")
    for f in sources:
        title = html.escape(f.get("title") or "(untitled)")
        id_lbl = html.escape(_id_label(f.get("source"), f.get("doc_id")))
        src_lbl = html.escape(_source_type_label(f.get("source")))
        url = html.escape(f.get("url") or "#")
        st.markdown(
            f"""
            <div class="src-card">
              <div class="src-head">
                <span class="src-doc">Doc {f['n']}</span>
                <span class="src-type">{src_lbl}</span>
              </div>
              <div class="src-title">{title}</div>
              <div class="src-reason">{_relevance_reason(f)}</div>
              <div class="src-meta">{id_lbl}</div>
              <a class="src-link" href="{url}" target="_blank">Open source ↗</a>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _split_cited(data):
    """Separa las fuentes en (citadas, solo-recuperadas) según qué [Doc N] aparecen
    DE VERDAD en el texto de la respuesta (citations.cited_docs). Así el gráfico y
    los KPIs hablan solo de los papers en los que la respuesta se apoya, y el resto
    (recuperados por afinidad pero no citados) se muestran aparte, con su motivo.

    OJO — aquí había un bug grave, el mismo que en src/report.py: si la respuesta
    no traía ninguna cita, se ASUMÍA que la mejor fuente estaba citada. Medido en
    un caso real: la respuesta hablaba de nasofaringitis y la UI se la atribuyó a
    un meta-análisis que no menciona esa palabra en todo el abstract. Eso es
    exactamente lo contrario de lo que promete MIA. Ahora: sin citas, no hay
    fuentes citadas, y la UI lo dice.
    """
    sources = data.get("sources") or []
    texto = data.get("answer") or data.get("content") or ""
    cited_n = citations.cited_docs(texto, len(sources))
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
        partes.append("covers <b>" + html.escape(", ".join(drugs)) + "</b>")
    mets = sorted({str(o.get("metric", "")) for o in (f.get("outcomes") or [])
                   if o.get("metric")})
    if mets:
        partes.append("reports <b>" + html.escape(", ".join(mets[:3])) + "</b>")
    detalle = ("; ".join(partes) + ". " if partes else "")
    return (f"{detalle}Retrieved for the same topic, but no sentence in the answer "
            "could be traced to it with confidence.")


def _render_data_cards(result, cited):
    """Fila de KPIs (Data Cards) con datos REALES de esta respuesta.

    Regla de honestidad: solo métricas que MIA calcula de verdad, y solo las que
    el lector puede interpretar sin equivocarse. Los KPIs hablan de las fuentes
    realmente CITADAS (`cited`), no de todas las recuperadas. Si no hubo
    evidencia, no mostramos nada: no hay KPIs que presumir.

    RETIRADO el 1-sep-2026: el KPI "Retrieval conf." (mejor similitud calibrada a
    %). Aunque el tooltip avisaba de que no era una probabilidad de que la
    respuesta fuese cierta, un número grande junto a "confianza" se lee como nota
    de fiabilidad. Es el mismo motivo por el que desapareció el badge de las
    tarjetas de fuente (ver _render_sources).
    """
    if not result.get("has_evidence") or not cited:
        return
    sources = cited

    # 1) Nº de fuentes citadas.
    n_fuentes = len(sources)

    # 2) Desglose PubMed vs ClinicalTrials (agregado real por 'source').
    n_pm = sum(1 for s in sources if "pubmed" in (s.get("source") or "").lower())
    n_ct = n_fuentes - n_pm

    cards = [
        f'<div class="mia-kpi"><div class="k-num">{n_fuentes}</div>'
        f'<div class="k-lbl">Cited sources</div></div>',
        f'<div class="mia-kpi"><div class="k-num">{n_pm} / {n_ct}</div>'
        f'<div class="k-lbl">PubMed / CT</div></div>',
    ]

    # 4) Card del Scout: solo si actuó y trajo evidencia nueva.
    if result.get("used_scout"):
        new_chunks = int((result.get("scout") or {}).get("new_chunks", 0) or 0)
        if new_chunks > 0:
            cards.append(
                f'<div class="mia-kpi k-scout"><div class="k-num">{new_chunks}</div>'
                f'<div class="k-lbl">New chunks</div></div>'
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
    # (fuente, sus cifras) — solo papers con cifras.
    grupos = [(f, list(f.get("outcomes") or [])) for f in (sources or [])]
    grupos = [(f, ocs) for f, ocs in grupos if ocs]
    if not grupos:
        return

    # ESCALA POR TIPO, no global. Una tasa EASI-75 del 82% y una de
    # nasofaringitis del 12% no comparten significado: normalizarlas contra el
    # mismo máximo haría que el evento adverso pareciera insignificante (o al
    # revés). Cada bloque se escala contra el máximo DE SU TIPO.
    maximos = {}
    for _, ocs in grupos:
        for oc in ocs:
            k = oc.get("kind", "efficacy")
            maximos[k] = max(maximos.get(k, 0), float(oc.get("value", 0) or 0))

    bloques = []
    for f, ocs in grupos:
        titulo = html.escape((f.get("title") or "(untitled)")[:80])
        cabecera = (
            f'<div class="oc-group">'
            f'<span class="oc-doc">Doc {int(f.get("n", 0))}</span>'
            f'<span class="oc-group-title">{titulo}</span></div>'
        )
        cuerpo = ""
        for kind, etiqueta_kind in (("efficacy", "Efficacy"), ("safety", "Safety")):
            del_tipo = [o for o in ocs if o.get("kind", "efficacy") == kind]
            if not del_tipo:
                continue
            del_tipo.sort(key=lambda p: p.get("value", 0), reverse=True)
            tope = maximos.get(kind, 100) or 100
            filas = []
            for p in del_tipo[:8]:  # tope por paper y tipo para no saturar
                val = float(p.get("value", 0) or 0)
                wk = p.get("week")
                etiqueta = html.escape(str(p.get("metric", "")))
                sub = f"week {html.escape(str(wk))}" if wk else ""
                ancho = int(round(val / tope * 100))
                filas.append(
                    f'<div class="oc-row">'
                    f'<div class="oc-lbl">{etiqueta}<span class="oc-sub">{sub}</span></div>'
                    f'<div class="oc-track"><i style="width:{ancho}%"></i></div>'
                    f'<span class="oc-val">{val:g}%</span>'
                    f'</div>'
                )
            cuerpo += (f'<div class="oc-kind oc-kind-{kind}">{etiqueta_kind}</div>'
                       + "".join(filas))
        bloques.append(cabecera + cuerpo)

    st.markdown(
        f'<div class="mia-chart">'
        f'<div class="oc-head">{_icon("cite")} Key figures from the evidence'
        f'<span class="oc-note">quoted verbatim from the abstract · '
        f'not generated by the model</span></div>'
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
# Preguntas de ejemplo: vienen del PERFIL DE DOMINIO activo (domains/<slug>.json,
# clave "example_questions"), así que cambian con la patología. Formato de cada
# entrada: [título, icono, [preguntas…]].
EJEMPLOS_POR_INTENCION = [
    (str(t), str(i), [str(q) for q in qs])
    for t, i, qs in (tuple(e) for e in config.EXAMPLE_QUESTIONS if len(e) == 3)
]

if not st.session_state.messages:
    st.markdown(
        f"""
        <div class="mia-welcome">
          <h3>Welcome</h3>
          <p>Ask about the evidence on <b>{html.escape(config.DISEASE.lower())}</b> and
             its drugs. Everything runs <b>locally</b>: every answer cites the exact
             sources backing it, and when the evidence is not enough MIA says so
             instead of inventing. Try an example:</p>
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
          <div class="p-head"><span class="p-dot"></span>{_icon("search")} Scout agent activated</div>
          There was not enough local evidence. I searched for «<b>{term}</b>» and
          imported <b>{new_chunks}</b> new chunks from PubMed/ClinicalTrials.
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
        hints = [f"Unexpected model error: `{type(exc).__name__}: {exc}`. "
                 "Retry; if it persists, check that Ollama has enough memory."]
    st.error(
        "**I could not generate the answer.**\n\n" + "\n".join(f"- {h}" for h in hints),
        icon=":material/error:",
    )


def _render_no_evidence_panel(texto: str):
    """Panel ámbar de 'sin evidencia local' (estado has_evidence=False)."""
    st.markdown(
        f"""
        <div class="mia-panel mia-panel-empty">
          <div class="p-head"><span class="p-dot"></span>No local evidence</div>
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
        st.caption(f":material/subdirectory_arrow_right: I read your question as: "
                   f"*{condensed}*")

    if has_evidence is False:
        # Estado sin evidencia: el texto ya es el mensaje explicativo → panel.
        _render_no_evidence_panel(texto)
        if data.get("used_scout"):
            _render_scout_panel(data.get("scout"))
        return

    # Separamos las fuentes CITADAS por la respuesta de las solo-recuperadas.
    citadas, otras = _split_cited(data)

    # ORDEN DE LECTURA (rediseñado). Antes se mezclaban dos intenciones —
    # responder a la pregunta y justificar cómo de seguro estaba el sistema— al
    # mismo nivel visual, y resultaba confuso. Ahora:
    #   ARRIBA  → lo que responde: respuesta citada, fuentes citadas, cifras clave.
    #   PLEGADO → el aparato de recuperación: confianza, papers no usados.
    # El aviso del Scout se queda arriba: no es diagnóstico, es algo que MIA HIZO
    # y que cambia de dónde sale la respuesta.
    _render_answer(texto)

    if data.get("used_scout"):
        _render_scout_panel(data.get("scout"))

    if citadas:
        _render_sources(citadas)        # papers en los que se apoya (desplegado)
        _render_outcomes_chart(citadas) # cifras verbatim de esos papers
    else:
        # Sin citas válidas no fingimos ninguna (ver _split_cited): lo decimos.
        st.warning(
            "No sentence in this answer could be confidently attributed to a single "
            "retrieved paper, so MIA is not claiming any cited source. The retrieved "
            "evidence is under **Retrieval details** below.",
            icon=":material/link_off:",
        )

    _render_retrieval_details(data, citadas, otras)
    _render_export_button(data, texto, citadas)


def _render_retrieval_details(data, citadas, otras):
    """UN solo desplegable con todo lo que NO es la respuesta: los KPIs de
    confianza y los papers recuperados que la respuesta no usó.

    Está cerrado por defecto a propósito: quien quiera auditar lo abre, y quien
    solo quiera la respuesta no lo ve. Es la separación que pedía el usuario
    entre "contéstame" y "demuéstrame lo seguro que estás".
    """
    if not (citadas or otras):
        return
    n = len(otras)
    etiqueta = ("Retrieval details" if not n
                else f"Retrieval details · {n} more paper{'s' if n != 1 else ''} retrieved")
    with st.expander(etiqueta, icon=":material/manage_search:", expanded=False):
        _render_data_cards(data, citadas)
        if otras:
            st.markdown("")
            _render_other_sources(otras)


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
        "Download report (HTML → PDF)",
        data=html_report,
        file_name=f"MIA_evidence_report_{datetime.now():%Y%m%d_%H%M}.html",
        mime="text/html",
        key=key,
        icon=":material/download:",
        help="A branded document with the question, the cited answer and the sources. "
             "Open it and use Ctrl+P → Save as PDF to share it.",
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
            with st.spinner("Retrieving evidence and reasoning with the local model…"):
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
