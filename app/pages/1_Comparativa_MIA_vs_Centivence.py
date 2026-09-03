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
from src import compare, verdict

st.set_page_config(
    page_title="MIA · Model comparison",
    page_icon=":material/compare_arrows:",
    layout="wide",
)

# Backends a enfrentar: (etiqueta, backend, colección, sublínea). Las colecciones
# salen de config (dependen del PERFIL DE DOMINIO activo, ver config.collection_name).
IZQ = ("MIA", "medcpt", config.collection_name("medcpt"), "MedCPT · biomedical · 100% local")
DER = ("Centivence", "openai", config.collection_name("openai"),
       "text-embedding-3-small · generalist · OpenAI API")

# Preguntas de ejemplo DIFÍCILES (por mecanismo/sinónimo, sin nombrar el fármaco):
# es donde un embedding biomédico debería destacar. 'drugs' = objetivo correcto.
# Vienen del perfil (`mechanism_questions`); si el perfil no trae, se usan las del
# caso original de dermatitis atópica.
_EJEMPLOS_AD = [
    {"q": "Antibody targeting the IL-4 receptor alpha for atopic eczema",
     "drugs": ["dupilumab"]},
    {"q": "Therapy targeting IL-31 signaling to relieve itch in atopic eczema",
     "drugs": ["nemolizumab"]},
    {"q": "Biologics that block IL-13 to treat eczema",
     "drugs": ["tralokinumab", "lebrikizumab"]},
    {"q": "JAK1-selective inhibitors for severe atopic eczema",
     "drugs": ["upadacitinib", "abrocitinib"]},
]
EJEMPLOS = [{"q": e["q"], "drugs": list(e.get("drugs") or [])}
            for e in config.MECHANISM_QUESTIONS] or _EJEMPLOS_AD


# ==========================================================================
# Estilos (paleta Centivence; la página principal inyecta los suyos aparte).
# ==========================================================================
st.markdown(
    """
    <style>
      /* Paleta Aurora LSHC — misma que la página principal (ver streamlit_app.py).
         Aquí el VIOLETA cobra sentido semántico: MIA = verde-azulado (producto,
         local); Centivence = violeta/índigo (la línea generalista de contraste).
         Los dos velos de la aurora sirven así para distinguir los dos motores. */
      :root {
        --mia-bg:#070d14; --mia-bg-soft:#0e1723; --mia-bg-2:#132030;
        --mia-teal:#2fe0a8; --mia-teal-d:#16b98a; --mia-cyan:#38bdf8;
        --mia-green:#7ee787; --mia-violet:#8b7cf6; --mia-amber:#fbbf24;
        --mia-ink:#e8f1f5; --mia-slate:#93a7b8;
        --mia-line:rgba(148,180,200,.16); --mia-mint:rgba(47,224,168,.12);
        --mia-success:#2fe0a8; --mia-track:rgba(148,180,200,.13);
        --cen-slate:#8b7cf6; --cen-slate-d:#6d5de0; --cen-bg:rgba(139,124,246,.10);
        --mia-font:'Inter','Segoe UI',-apple-system,BlinkMacSystemFont,Roboto,Helvetica,Arial,sans-serif;
        --mia-mono:'JetBrains Mono','Cascadia Code',Consolas,ui-monospace,monospace;
      }
      html, body, [class*="css"], .stMarkdown { font-family: var(--mia-font); }
      .block-container { padding-top: 2rem; max-width: 1200px; position: relative; z-index: 1; }
      .stApp { background: var(--mia-bg); }

      /* Fondo aurora (misma técnica que la página principal: radiales muy
         difuminados, fijos, sin capturar clics). Aquí los tonos se reparten
         izquierda-verde / derecha-violeta para acompañar a las dos columnas. */
      .stApp::before {
        content:""; position:fixed; inset:-20% -10% auto -10%; height:80vh;
        z-index:0; pointer-events:none;
        background:
          radial-gradient(44% 40% at 20% 18%, rgba(47,224,168,.28) 0%, transparent 68%),
          radial-gradient(42% 36% at 55% 10%, rgba(56,189,248,.20) 0%, transparent 66%),
          radial-gradient(44% 40% at 84% 24%, rgba(139,124,246,.24) 0%, transparent 66%);
        filter: blur(70px) saturate(125%);
        animation: auroraDrift 46s ease-in-out infinite alternate;
      }
      @keyframes auroraDrift {
        0%   { transform: translate3d(0,0,0) scale(1);      opacity:.92; }
        50%  { transform: translate3d(3%,2%,0) scale(1.09); opacity:1;   }
        100% { transform: translate3d(-3%,-1%,0) scale(1.03); opacity:.85; }
      }
      @media (prefers-reduced-motion: reduce) { .stApp::before { animation:none; } }
      section[data-testid="stSidebar"] {
        background: rgba(10,17,26,.86); border-right:1px solid var(--mia-line);
        backdrop-filter: blur(12px); position:relative; z-index:1;
      }

      .cmp-hero {
        position:relative; overflow:hidden;
        background: linear-gradient(120deg, rgba(47,224,168,.15) 0%,
                    rgba(56,189,248,.09) 50%, rgba(139,124,246,.16) 100%), var(--mia-bg-soft);
        border:1px solid var(--mia-line);
        border-radius:20px; padding:24px 28px; color:var(--mia-ink); margin-bottom:8px;
        box-shadow: 0 18px 50px -24px rgba(47,224,168,.30), inset 0 1px 0 rgba(255,255,255,.06);
      }
      .cmp-hero::before {
        content:""; position:absolute; inset:0 0 auto 0; height:2px;
        background: linear-gradient(90deg, transparent, var(--mia-teal) 25%,
                    var(--mia-cyan) 50%, var(--mia-violet) 75%, transparent);
      }
      .cmp-hero h1 {
        font-size:1.55rem; font-weight:800; margin:0; letter-spacing:-.025em;
        background: linear-gradient(100deg,#fff 0%, var(--mia-teal) 50%, var(--mia-violet) 100%);
        -webkit-background-clip:text; background-clip:text;
        -webkit-text-fill-color:transparent; color:var(--mia-teal);
      }
      .cmp-hero p { margin:8px 0 0; font-size:.92rem; color:var(--mia-slate); }
      .cmp-hero p b { color:var(--mia-ink); }

      /* Cabecera de cada columna (marca del motor). */
      .col-head { border-radius:16px; padding:14px 18px; margin:2px 0 12px;
                  color:var(--mia-ink); border:1px solid var(--mia-line); }
      .col-head.mia {
        background: linear-gradient(135deg, rgba(47,224,168,.20), rgba(22,185,138,.08));
        border-color: rgba(47,224,168,.35);
        box-shadow: 0 0 30px -14px rgba(47,224,168,.7);
      }
      .col-head.cen {
        background: linear-gradient(135deg, rgba(139,124,246,.20), rgba(109,93,224,.08));
        border-color: rgba(139,124,246,.35);
        box-shadow: 0 0 30px -14px rgba(139,124,246,.7);
      }
      .col-head .h-name { font-size:1.12rem; font-weight:800; letter-spacing:-.01em; }
      .col-head.mia .h-name { color: var(--mia-teal); }
      .col-head.cen .h-name { color: var(--mia-violet); }
      .col-head .h-sub  { font-size:.72rem; color:var(--mia-slate); font-family:var(--mia-mono);
                          text-transform:uppercase; letter-spacing:.06em; margin-top:3px; }
      .col-head .h-sum  { margin-top:11px; display:flex; gap:8px; flex-wrap:wrap; }
      .col-head .h-chip {
        background:rgba(148,180,200,.10); border:1px solid var(--mia-line);
        border-radius:999px; padding:3px 11px; font-size:.75rem; font-weight:700;
        color:var(--mia-ink);
      }
      .col-head .h-chip.ok { background:rgba(47,224,168,.18); border-color:rgba(47,224,168,.45);
                             color:var(--mia-teal); }
      .col-head .h-chip.no { background:rgba(251,191,36,.15); border-color:rgba(251,191,36,.40);
                             color:var(--mia-amber); }

      /* Tarjeta de documento recuperado. */
      .rk { border:1px solid var(--mia-line); border-radius:14px; padding:13px 15px;
            margin-bottom:10px; background:var(--mia-bg-soft); }
      .rk.hit  { border-left:3px solid var(--mia-teal); }
      .rk.miss { border-left:3px solid rgba(148,180,200,.25); }
      .rk-top  { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
      .rk-rank { font-family:var(--mia-mono); font-weight:800; font-size:.8rem;
                 color:var(--mia-slate); background:rgba(148,180,200,.12);
                 border-radius:6px; padding:1px 8px; }
      .rk.hit  .rk-rank { background:linear-gradient(120deg,var(--mia-teal-d),var(--mia-teal));
                          color:#04231a; }
      .rk-tag  { font-size:.71rem; font-weight:700; padding:1px 8px; border-radius:999px; }
      .rk-tag.ok { color:var(--mia-teal); background:var(--mia-mint);
                   border:1px solid rgba(47,224,168,.35); }
      .rk-tag.no { color:var(--mia-slate); background:rgba(148,180,200,.08);
                   border:1px solid var(--mia-line); }
      .rk-conf { margin-left:auto; font-family:var(--mia-mono); font-size:.73rem;
                 font-weight:700; color:var(--mia-slate); }
      .rk-title { font-size:.92rem; font-weight:600; color:var(--mia-ink);
                  margin:9px 0 6px; line-height:1.35; }
      .rk-drugs { display:flex; gap:5px; flex-wrap:wrap; margin:0 0 7px; }
      .rk-drugs span { font-family:var(--mia-mono); font-size:.67rem; font-weight:500;
                       background:var(--mia-mint); color:var(--mia-teal);
                       border:1px solid rgba(47,224,168,.25); padding:1px 7px; border-radius:999px; }
      .rk-snip { font-size:.82rem; color:#c3d4e0; line-height:1.5;
                 border-left:2px solid rgba(47,224,168,.3); padding:4px 0 4px 10px; margin:2px 0 8px; }
      .rk-link { font-size:.78rem; font-weight:600; color:var(--mia-teal); text-decoration:none; }
      .rk-link:hover { text-decoration:underline; }

      /* Bloque de respuesta redactada. */
      .rk-answer { border:1px solid var(--mia-line); border-radius:14px;
                   padding:13px 16px; margin:2px 0 14px; background:var(--mia-bg-soft); }
      .rk-answer.mia { border-left:3px solid var(--mia-teal); }
      .rk-answer.cen { border-left:3px solid var(--mia-violet); }
      .rk-answer .a-head { font-family:var(--mia-mono); font-size:.65rem; font-weight:700;
                           letter-spacing:.08em; text-transform:uppercase;
                           color:var(--mia-slate); margin-bottom:8px; }
      .rk-answer .a-body { font-size:.9rem; line-height:1.6; color:var(--mia-ink); }
      .rk-answer .a-body p { margin:0 0 8px; }
      .cmp-cite { display:inline-block; background:var(--mia-mint); color:var(--mia-teal);
                  border:1px solid rgba(47,224,168,.35); font-size:.72rem; font-weight:700;
                  padding:1px 6px; border-radius:6px; margin:0 1px; white-space:nowrap; }

      .cmp-note { font-size:.8rem; color:var(--mia-slate); background:var(--mia-bg-soft);
                  border:1px solid var(--mia-line); border-radius:12px;
                  padding:11px 15px; margin:6px 0 2px; }
      .cmp-note b { color:var(--mia-ink); }

      /* Banner del VEREDICTO por pregunta. */
      .verdict { border-radius:16px; padding:15px 19px; margin:4px 0 14px;
                 color:var(--mia-ink); border:1px solid var(--mia-line); }
      .verdict.win {
        background: linear-gradient(135deg, rgba(47,224,168,.20), rgba(56,189,248,.08));
        border-color: rgba(47,224,168,.40);
        box-shadow: 0 0 40px -18px rgba(47,224,168,.8);
      }
      .verdict.tie {
        background: linear-gradient(135deg, rgba(139,124,246,.18), rgba(56,189,248,.08));
        border-color: rgba(139,124,246,.38);
      }
      .verdict.abstain { background: var(--mia-bg-soft); border:1px dashed rgba(148,180,200,.3);
                         color: var(--mia-slate); }
      .verdict .v-head { font-size:.68rem; font-weight:800; letter-spacing:.09em;
                         text-transform:uppercase; color:var(--mia-slate); margin-bottom:5px; }
      .verdict .v-text { font-size:1.02rem; font-weight:700; line-height:1.45; }
      .verdict .v-agent{ font-size:.8rem; color:var(--mia-slate); margin-top:9px;
                         font-family:var(--mia-mono); }
      .verdict .v-agent b { color:var(--mia-ink); }
      .verdict .v-metrics { display:flex; gap:8px; flex-wrap:wrap; margin-top:11px; }
      .verdict .v-chip { background:rgba(148,180,200,.10); border:1px solid var(--mia-line);
                         border-radius:999px; padding:3px 11px; font-size:.74rem;
                         font-weight:700; color:var(--mia-ink); }
      .verdict.abstain .v-chip { background:transparent; }

      /* Widgets nativos, a juego con el fondo oscuro. */
      div[data-testid="stExpander"] {
        border:1px solid var(--mia-line) !important; border-radius:14px !important;
        background: rgba(14,23,35,.7) !important; backdrop-filter: blur(8px);
      }
      .stButton > button {
        border:1px solid var(--mia-line); background:var(--mia-bg-soft);
        color:var(--mia-ink); border-radius:10px; font-size:.82rem;
        transition: border-color .15s ease, box-shadow .15s ease;
      }
      .stButton > button:hover {
        border-color:rgba(47,224,168,.55); color:var(--mia-teal);
        box-shadow:0 0 18px -4px rgba(47,224,168,.5);
      }
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
      <h1>Retrieval comparison · MIA vs Centivence</h1>
      <p>Same question, same corpus (~8,900 PubMed chunks), same chunking.
      <b>The only thing that changes is the embedding model.</b> So you can see what
      evidence each one brings and, above all, <b>in what order</b>.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ==========================================================================
# Entrada: ejemplos difíciles + pregunta libre
# ==========================================================================
st.caption("Try a **mechanism** example (without naming the drug) — this is where "
           "a biomedical embedding should stand out:")
cols_ej = st.columns(len(EJEMPLOS))
for col, ej in zip(cols_ej, EJEMPLOS):
    if col.button(ej["q"], width="stretch"):
        st.session_state.cmp_q = ej["q"]
        st.session_state.cmp_drugs = ej["drugs"]

pregunta_libre = st.chat_input("…or type your own question (in English)")
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
    "Also draft each model's answer (uses the local LLM · slower)",
    value=False,
    help="Generates the answer each model would give from ITS retrieved evidence. "
         "The writer is the same for both, so any difference comes "
         "solo del embedding. Son 2 llamadas al modelo local, tarda unos segundos.",
)


def _resumen_chips(res):
    """Chips de resumen (hit@1 y on-target) para la cabecera de columna."""
    if res.get("n_on_target") is None:       # pregunta libre: sin objetivo
        return ""
    hit = res.get("hit1")
    hit_cls = "ok" if hit else "no"
    hit_txt = "1st result correct" if hit else "1st result NOT correct"
    return (f'<div class="h-sum">'
            f'<span class="h-chip {hit_cls}">hit@1 · {hit_txt}</span>'
            f'<span class="h-chip">{res["n_on_target"]}/{res["total"]} from the correct drug</span>'
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
        f'<div class="a-head">Drafted answer · same local LLM</div>'
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
            card_cls, tag = "hit", '<span class="rk-tag ok">✓ correct drug</span>'
        elif ot is False:
            card_cls, tag = "miss", '<span class="rk-tag no">other drug</span>'
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
            f'<span class="rk-conf">affinity {d["confidence"]}%</span></div>'
            f'<div class="rk-title">{title}</div>'
            f'{pills_html}{snip_html}'
            f'<a class="rk-link" href="{url}" target="_blank">Open source ↗ ({did})</a>'
            f'</div>',
            unsafe_allow_html=True,
        )


def _render_verdict(v):
    """Banner del veredicto por pregunta: qué embedding entendió mejor (o, si el
    agente no pudo fijar un objetivo verificado, una abstención sin ganador)."""
    if v["abstained"]:
        st.markdown(
            '<div class="verdict abstain">'
            '<div class="v-head">Per-question verdict</div>'
            '<div class="v-text">No winner declared</div>'
            f'<div class="v-agent">{html.escape(v["verdict_text"])}</div>'
            '</div>', unsafe_allow_html=True)
        return

    css = "win" if v["winner"] else "tie"
    head = ("Which model understood the question better" if v["winner"]
            else "Tie between the two models")

    # Si el agente catalogó una pregunta libre, mostramos cómo la interpretó.
    agent_html = ""
    if v.get("positive") and v.get("agent_source") in ("llm", "fallback"):
        agent_html = (
            f'<div class="v-agent">The agent read it as: anchor «{html.escape(v["anchor"])}» → '
            f'expected <b>{html.escape(v["positive"])}</b>, no '
            f'<b>{html.escape(v["negative"] or "—")}</b> (source: {html.escape(v["agent_source"])})</div>')

    # Chips de métricas por motor (para la defensa: se ve el porqué del veredicto).
    chips = []
    for m in v["per_backend"]:
        r = m["first_correct_rank"]
        pos = f"1st correct at rank {r}" if r else "no correct doc in top-k"
        auc = f" · AUC {m['auc']}" if m["auc"] is not None else ""
        tri = (" · triplet ✓" if m["triplet_ok"] else
               (" · triplet ✗" if m["triplet_ok"] is False else ""))
        chips.append(f'<span class="v-chip">{html.escape(m["engine"])}: {pos}{auc}{tri}</span>')
    chips_html = f'<div class="v-metrics">{"".join(chips)}</div>'

    st.markdown(
        f'<div class="verdict {css}">'
        f'<div class="v-head">{head}</div>'
        f'<div class="v-text">{html.escape(v["verdict_text"])}</div>'
        f'{agent_html}{chips_html}'
        '</div>', unsafe_allow_html=True)


# ==========================================================================
# Recuperación y render de las dos columnas
# ==========================================================================
if not pregunta:
    st.info("Pick an example or type a question to see the comparison.",
            icon=":material/touch_app:")
else:
    st.markdown(f"### :material/quiz: {html.escape(pregunta)}")
    try:
        # question_verdict recupera con los DOS backends y, si es pregunta libre,
        # llama al agente catalogador (por eso el spinner menciona el análisis).
        with st.spinner("Analysing the question and retrieving with both models…"):
            veredicto = verdict.question_verdict(pregunta, target_drugs=target_drugs or None)
    except Exception as e:
        st.error(f"Could not complete the comparison: {e}\n\n"
                 "Check that the OpenAI collection exists (run "
                 "`index_openai.py`) and that `OPENAI_API_KEY` is set in `.env`.",
                 icon=":material/error:")
    else:
        # Reutilizamos la recuperación que YA hizo el veredicto (no re-recuperamos).
        # per_backend[0]=MIA(MedCPT), [1]=Centivence(OpenAI) — mismo orden que IZQ/DER.
        res_izq = veredicto["per_backend"][0]
        res_der = veredicto["per_backend"][1]

        # Banner del veredicto por pregunta ARRIBA de las columnas.
        _render_verdict(veredicto)

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
                st.warning(f"Could not draft the answers: {e}. Showing retrieval only.",
                           icon=":material/warning:")

        c_izq, c_der = st.columns(2, gap="large")
        with c_izq:
            _render_columna(IZQ[0], IZQ[3], "mia", res_izq, ans_izq)
        with c_der:
            _render_columna(DER[0], DER[3], "cen", res_der, ans_der)

        nota_redaccion = (
            "At the top of each column is the <b>answer the SAME local LLM would write</b> "
            "from each model's evidence: since the writer is identical, any difference "
            "between the two answers comes from the <b>embedding</b>. "
            if redactar else
            "Turn on <b>«Also draft each model's answer»</b> to see, alongside the "
            "ranking, the answer each one would produce. "
        )
        st.markdown(
            f'<div class="cmp-note">{nota_redaccion}The <b>affinity % are not '
            'comparable across columns</b>: MedCPT uses inner product (~55-75) and '
            'OpenAI cosine (0-1) — different scales. What IS fairly comparable '
            'is the <b>ranking</b> (what each model puts on top) and how many '
            'documents come from the correct drug.</div>',
            unsafe_allow_html=True,
        )


# ==========================================================================
# BENCHMARK AGREGADO (respaldo científico del veredicto por pregunta)
# ==========================================================================
# El veredicto de arriba mide UNA pregunta. Este benchmark, precomputado por
# `evaluate_embeddings_semantics.py`, mide la COMPRENSIÓN semántica sobre un set
# etiquetado a mano (triplet accuracy + AUC por nivel). Es la capa "dura" del
# experimento: verdad = biología humana, no el juicio del agente.
st.divider()
with st.expander("📊 Aggregate semantic-understanding benchmark (triplet accuracy + AUC)",
                 expanded=False):
    _bench_csv = config.DATA_DIR / "semantics_summary.csv"
    if not _bench_csv.exists():
        st.info("No benchmark yet. Run it once with "
                "`./.venv/Scripts/python.exe evaluate_embeddings_semantics.py` "
                "to generate `data/semantics_summary.csv`.",
                icon=":material/info:")
    else:
        try:
            import pandas as pd
            _df = pd.read_csv(_bench_csv)
            st.caption("Two layers of rigour: here, AGGREGATE evidence over a "
                       "hand-labelled set; above, the LIVE per-question verdict. The "
                       "**Mechanism→drug** level is where a biomedical embedding "
                       "should stand out.")
            st.dataframe(_df, width="stretch", hide_index=True)
            st.caption("triplet_acc = % of triplets where the positive is closer than "
                       "the negative (0.5 = chance). AUC = margin/cleanliness of the "
                       "separation (1.0 = perfect).")
        except Exception as e:
            st.warning(f"Could not read the benchmark: {e}", icon=":material/warning:")
