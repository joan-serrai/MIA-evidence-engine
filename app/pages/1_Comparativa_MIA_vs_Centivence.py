"""
app/pages/1_Comparativa_MIA_vs_Centivence.py — Página de EVALUACIÓN comparativa.

Enseña, lado a lado y sobre la MISMA pregunta, qué evidencia recupera cada modelo
de embeddings:
  - IZQUIERDA  · MIA        → MedCPT (biomédico, local, 2 torres).
  - DERECHA    · Centivence → text-embedding-3-small (generalista, API OpenAI).

Ambas columnas buscan en colecciones con LOS MISMOS ~8.900 fragmentos de PubMed;
lo único que cambia es el modelo de embedding. Así se ve, de forma visual, la
diferencia de RANKING que mide el experimento del capstone (README): MedCPT tiende
a colocar el fármaco correcto MÁS ARRIBA en preguntas por mecanismo/sinónimo.

NO usa el LLM: compara solo la recuperación (el redactor es común a los dos, no es
el factor que se evalúa). Es rápida (unos segundos tras cargar los modelos).
"""

import html
import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
import config
from src import compare

st.set_page_config(
    page_title="MIA · Comparativa de modelos",
    page_icon=":material/compare_arrows:",
    layout="wide",
)

# Backends a enfrentar: (etiqueta, backend, colección, sublínea).
IZQ = ("MIA", "medcpt", "mia_evidence_medcpt", "MedCPT · biomédico · 100% local")
DER = ("Centivence", "openai", "mia_evidence_openai",
       "text-embedding-3-small · generalista · API OpenAI")

# Preguntas de ejemplo DIFÍCILES (por mecanismo/sinónimo, sin nombrar el fármaco):
# es donde un embedding biomédico debería destacar. 'drugs' = objetivo correcto.
EJEMPLOS = [
    {"q": "Antibody targeting the IL-4 receptor alpha for atopic eczema",
     "drugs": ["dupilumab"]},
    {"q": "Therapy targeting IL-31 signaling to relieve itch in atopic eczema",
     "drugs": ["nemolizumab"]},
    {"q": "Biologics that block IL-13 to treat eczema",
     "drugs": ["tralokinumab", "lebrikizumab"]},
    {"q": "JAK1-selective inhibitors for severe atopic eczema",
     "drugs": ["upadacitinib", "abrocitinib"]},
]


# ==========================================================================
# Estilos (paleta Centivence; la página principal inyecta los suyos aparte).
# ==========================================================================
st.markdown(
    """
    <style>
      :root {
        --mia-teal:#3f6e66; --mia-teal-d:#2c524c; --mia-ink:#1a1a1a;
        --mia-slate:#555; --mia-line:#eee; --mia-mint:#e8f5ee;
        --mia-amber:#92600a; --mia-success:#1a7a3a;
        --cen-slate:#5b6b8c; --cen-slate-d:#3f4c6b; --cen-bg:#f4f6fb;
        --mia-font:'Inter','Segoe UI',-apple-system,BlinkMacSystemFont,Roboto,Helvetica,Arial,sans-serif;
        --mia-mono:'JetBrains Mono','Cascadia Code',Consolas,ui-monospace,monospace;
      }
      html, body, [class*="css"], .stMarkdown { font-family: var(--mia-font); }
      .block-container { padding-top: 2rem; max-width: 1180px; }

      .cmp-hero {
        background: linear-gradient(150deg,#3f6e66 0%,#2c524c 100%);
        border-radius: 18px; padding: 22px 26px; color:#fff; margin-bottom: 6px;
        box-shadow: 0 10px 30px -12px rgba(63,110,102,.35);
      }
      .cmp-hero h1 { font-size:1.5rem; font-weight:800; margin:0; letter-spacing:-.02em; color:#fff; }
      .cmp-hero p  { margin:6px 0 0; font-size:.92rem; opacity:.92; }

      /* Cabecera de cada columna (marca del modelo). */
      .col-head { border-radius:14px; padding:12px 16px; margin:2px 0 12px; color:#fff; }
      .col-head.mia { background:linear-gradient(135deg,#3f6e66,#2c524c); }
      .col-head.cen { background:linear-gradient(135deg,#5b6b8c,#3f4c6b); }
      .col-head .h-name { font-size:1.1rem; font-weight:800; letter-spacing:-.01em; }
      .col-head .h-sub  { font-size:.74rem; opacity:.9; font-family:var(--mia-mono);
                          text-transform:uppercase; letter-spacing:.05em; margin-top:2px; }
      .col-head .h-sum  { margin-top:10px; display:flex; gap:8px; flex-wrap:wrap; }
      .col-head .h-chip {
        background:rgba(255,255,255,.18); border:1px solid rgba(255,255,255,.3);
        border-radius:999px; padding:3px 11px; font-size:.76rem; font-weight:700;
      }
      .col-head .h-chip.ok  { background:rgba(34,197,94,.28); border-color:rgba(34,197,94,.5); }
      .col-head .h-chip.no  { background:rgba(146,96,10,.30);  border-color:rgba(146,96,10,.55); }

      /* Tarjeta de un documento recuperado (rank). */
      .rk { border:1px solid var(--mia-line); border-radius:12px; padding:12px 14px;
            margin-bottom:10px; background:#fff; box-shadow:0 1px 2px rgba(15,23,42,.04); }
      .rk.hit  { border-left:4px solid var(--mia-success); }
      .rk.miss { border-left:4px solid #d8d8d8; }
      .rk-top  { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
      .rk-rank { font-family:var(--mia-mono); font-weight:800; font-size:.82rem;
                 color:#fff; background:var(--mia-slate); border-radius:6px; padding:1px 8px; }
      .rk.hit  .rk-rank { background:var(--mia-teal); }
      .rk-tag  { font-size:.72rem; font-weight:700; padding:1px 8px; border-radius:999px; }
      .rk-tag.ok { color:var(--mia-success); background:var(--mia-mint); border:1px solid #cde8d8; }
      .rk-tag.no { color:var(--mia-slate);  background:#f4f4f4;  border:1px solid var(--mia-line); }
      .rk-conf { margin-left:auto; font-family:var(--mia-mono); font-size:.74rem;
                 font-weight:700; color:var(--mia-slate); }
      .rk-title { font-size:.92rem; font-weight:600; color:var(--mia-ink);
                  margin:8px 0 6px; line-height:1.32; }
      .rk-drugs { display:flex; gap:5px; flex-wrap:wrap; margin:0 0 7px; }
      .rk-drugs span { font-family:var(--mia-mono); font-size:.68rem; font-weight:500;
                       background:var(--mia-mint); color:var(--mia-success);
                       border:1px solid #cde8d8; padding:1px 7px; border-radius:999px; }
      .rk-snip { font-size:.82rem; color:#444; line-height:1.45;
                 border-left:3px solid var(--mia-line); padding:4px 0 4px 10px; margin:2px 0 8px; }
      .rk-link { font-size:.78rem; font-weight:600; color:var(--mia-teal-d); text-decoration:none; }
      .rk-link:hover { text-decoration:underline; }

      /* Bloque de respuesta redactada (cuando el toggle está activo). */
      .rk-answer { border:1px solid var(--mia-line); border-radius:12px;
                   padding:12px 15px; margin:2px 0 14px; background:#fff;
                   box-shadow:0 1px 2px rgba(15,23,42,.04); }
      .rk-answer.mia { border-left:4px solid var(--mia-teal); }
      .rk-answer.cen { border-left:4px solid var(--cen-slate); }
      .rk-answer .a-head { font-family:var(--mia-mono); font-size:.66rem; font-weight:700;
                           letter-spacing:.07em; text-transform:uppercase;
                           color:var(--mia-slate); margin-bottom:7px; }
      .rk-answer .a-body { font-size:.9rem; line-height:1.55; color:var(--mia-ink); }
      .rk-answer .a-body p { margin:0 0 8px; }
      /* Insignia de cita [Doc N] dentro de la respuesta redactada. */
      .cmp-cite { display:inline-block; background:var(--mia-mint); color:var(--mia-teal-d);
                  border:1px solid #cde8d8; font-size:.72rem; font-weight:700;
                  padding:1px 6px; border-radius:6px; margin:0 1px; white-space:nowrap; }

      .cmp-note { font-size:.8rem; color:var(--mia-slate); background:var(--mia-bg-soft,#fafafa);
                  border:1px solid var(--mia-line); border-radius:10px; padding:10px 14px; margin:6px 0 2px; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ==========================================================================
# Cabecera
# ==========================================================================
st.markdown(
    """
    <div class="cmp-hero">
      <h1>Comparativa de recuperación · MIA vs Centivence</h1>
      <p>La misma pregunta, el mismo corpus (~8.900 fragmentos de PubMed), el mismo
      troceado. <b>Lo único que cambia es el modelo de embedding.</b> Así se ve qué
      evidencia trae cada uno y, sobre todo, <b>en qué orden</b>.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ==========================================================================
# Entrada: ejemplos difíciles + pregunta libre
# ==========================================================================
st.caption("Prueba un ejemplo por **mecanismo** (sin nombrar el fármaco) — es donde "
           "un embedding biomédico debería destacar:")
cols_ej = st.columns(len(EJEMPLOS))
for col, ej in zip(cols_ej, EJEMPLOS):
    if col.button(ej["q"], use_container_width=True):
        st.session_state.cmp_q = ej["q"]
        st.session_state.cmp_drugs = ej["drugs"]

pregunta_libre = st.chat_input("…o escribe tu propia pregunta (en inglés)")
if pregunta_libre:
    st.session_state.cmp_q = pregunta_libre
    st.session_state.cmp_drugs = []   # pregunta libre: sin objetivo conocido

pregunta = st.session_state.get("cmp_q")
target_drugs = st.session_state.get("cmp_drugs", [])

# Redacción opcional: además del ranking, generar la RESPUESTA que cada modelo
# produciría con su evidencia. Va detrás de un toggle (OFF por defecto) porque
# son DOS llamadas al LLM local (OpenBioLLM 8B) y tardan bastante; el ranking
# solo es instantáneo. Así el usuario elige cuándo pagar esa espera.
redactar = st.toggle(
    "Redactar también la respuesta de cada modelo (usa el LLM local · más lento)",
    value=False,
    help="Genera la respuesta que cada modelo daría a partir de SU evidencia "
         "recuperada. El redactor es el mismo para ambos → la diferencia viene "
         "solo del embedding. Son 2 llamadas al modelo local, tarda unos segundos.",
)


def _resumen_chips(res):
    """Chips de resumen (hit@1 y on-target) para la cabecera de columna."""
    if res.get("n_on_target") is None:       # pregunta libre: sin objetivo
        return ""
    hit = res.get("hit1")
    hit_cls = "ok" if hit else "no"
    hit_txt = "1er resultado correcto" if hit else "1er resultado NO correcto"
    return (f'<div class="h-sum">'
            f'<span class="h-chip {hit_cls}">hit@1 · {hit_txt}</span>'
            f'<span class="h-chip">{res["n_on_target"]}/{res["total"]} del fármaco correcto</span>'
            f'</div>')


def _highlight_citations(texto):
    """Convierte '[Doc N]' en insignias visuales (igual que la página de chat)."""
    import re
    return re.sub(r"\[Doc\s*(\d+)\]", r'<span class="cmp-cite">Doc \1</span>',
                  html.escape(texto))


def _render_answer_block(css, ans):
    """Pinta la respuesta redactada de un backend (si se generó)."""
    if not ans:
        return
    cuerpo = _highlight_citations(ans.get("answer") or "").replace("\n\n", "</p><p>")
    st.markdown(
        f'<div class="rk-answer {css}">'
        f'<div class="a-head">Respuesta redactada · mismo LLM local</div>'
        f'<div class="a-body"><p>{cuerpo}</p></div></div>',
        unsafe_allow_html=True,
    )


def _render_columna(etiqueta, sub, css, res, ans=None):
    """Pinta la cabecera de marca + (opcional) la respuesta redactada + las
    tarjetas de documentos de un backend."""
    st.markdown(
        f'<div class="col-head {css}"><div class="h-name">{html.escape(etiqueta)}</div>'
        f'<div class="h-sub">{html.escape(sub)}</div>{_resumen_chips(res)}</div>',
        unsafe_allow_html=True,
    )
    _render_answer_block(css, ans)
    for d in res["docs"]:
        # Marca de acierto: verde si es del fármaco correcto; gris si no; neutro
        # cuando no hay objetivo (pregunta libre → on_target None).
        ot = d["on_target"]
        if ot is True:
            card_cls, tag = "hit", '<span class="rk-tag ok">✓ fármaco correcto</span>'
        elif ot is False:
            card_cls, tag = "miss", '<span class="rk-tag no">otro fármaco</span>'
        else:
            card_cls, tag = "", ""

        pills = "".join(f"<span>{html.escape(x)}</span>" for x in d["drugs"][:5])
        pills_html = f'<div class="rk-drugs">{pills}</div>' if pills else ""
        snip = html.escape(d["snippet"] or "")
        snip_html = f'<div class="rk-snip">{snip}</div>' if snip else ""
        title = html.escape(d["title"])
        url = html.escape(d["url"] or "#")
        did = html.escape(str(d["doc_id"] or ""))

        st.markdown(
            f'<div class="rk {card_cls}">'
            f'<div class="rk-top"><span class="rk-rank">#{d["rank"]}</span>{tag}'
            f'<span class="rk-conf">afinidad {d["confidence"]}%</span></div>'
            f'<div class="rk-title">{title}</div>'
            f'{pills_html}{snip_html}'
            f'<a class="rk-link" href="{url}" target="_blank">Ver fuente ↗ ({did})</a>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ==========================================================================
# Recuperación y render de las dos columnas
# ==========================================================================
if not pregunta:
    st.info("Elige un ejemplo o escribe una pregunta para ver la comparación.",
            icon=":material/touch_app:")
else:
    st.markdown(f"### :material/quiz: {html.escape(pregunta)}")
    try:
        with st.spinner("Recuperando evidencia con los dos modelos…"):
            res_izq = compare.retrieve_ranked(
                pregunta, IZQ[1], IZQ[2], target_drugs=target_drugs)
            res_der = compare.retrieve_ranked(
                pregunta, DER[1], DER[2], target_drugs=target_drugs)
    except Exception as e:
        st.error(f"No se pudo completar la comparación: {e}\n\n"
                 "Comprueba que existe la colección de OpenAI (ejecuta "
                 "`index_openai.py`) y que `OPENAI_API_KEY` está en el `.env`.",
                 icon=":material/error:")
    else:
        # Redacción opcional: dos llamadas al LLM local (una por backend). Solo si
        # el toggle está activo (por eso el spinner y el coste van aquí dentro).
        ans_izq = ans_der = None
        if redactar:
            try:
                with st.spinner("Redactando la respuesta de cada modelo con el LLM "
                                "local (esto tarda unos segundos)…"):
                    ans_izq = compare.answer_from_backend(pregunta, IZQ[1], IZQ[2])
                    ans_der = compare.answer_from_backend(pregunta, DER[1], DER[2])
            except Exception as e:
                st.warning(f"No se pudieron redactar las respuestas: {e}. Se muestra "
                           "solo la recuperación.", icon=":material/warning:")

        c_izq, c_der = st.columns(2, gap="large")
        with c_izq:
            _render_columna(IZQ[0], IZQ[3], "mia", res_izq, ans_izq)
        with c_der:
            _render_columna(DER[0], DER[3], "cen", res_der, ans_der)

        nota_redaccion = (
            "Arriba de cada columna ves la <b>respuesta que redactaría el MISMO LLM "
            "local</b> con la evidencia de cada modelo: como el redactor es idéntico, "
            "cualquier diferencia entre las dos respuestas viene del <b>embedding</b>. "
            if redactar else
            "Activa <b>«Redactar también la respuesta de cada modelo»</b> para ver, "
            "además del ranking, la respuesta que produciría cada uno. "
        )
        st.markdown(
            f'<div class="cmp-note">{nota_redaccion}Los <b>% de afinidad no son '
            'comparables entre columnas</b>: MedCPT usa producto escalar (~55-75) y '
            'OpenAI coseno (0-1), escalas distintas. Lo que sí se compara de forma '
            'justa es el <b>orden</b> (qué pone cada modelo arriba) y cuántos '
            'documentos son del fármaco correcto.</div>',
            unsafe_allow_html=True,
        )
