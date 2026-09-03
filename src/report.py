"""
src/report.py — Informe de evidencia EXPORTABLE (feature de producto).  [MIA 1.0]

Genera un documento HTML AUTÓNOMO (CSS embebido, sin llamadas a red) a partir de
una respuesta de MIA. El usuario lo descarga desde la app y lo abre en el
navegador; con Ctrl+P → "Guardar como PDF" obtiene un informe con marca, listo
para compartir con un clínico o presentar.

IDIOMA: el informe va ÍNTEGRAMENTE EN INGLÉS. Antes mezclaba cabeceras en español
con una respuesta en inglés (el corpus y el LLM lo son), y como documento para
compartir con un clínico quedaba poco serio. Los comentarios del código siguen en
español, que es el idioma de trabajo del proyecto.

ORDEN DE LECTURA (rediseñado): primero lo que responde a la pregunta — respuesta,
fuentes citadas y cifras clave —; después, plegado en un <details>, todo el
aparato de recuperación (confianza, papers recuperados que no se usaron). Antes
iba todo al mismo nivel y el informe mezclaba dos intenciones: contestar y
justificar cómo de seguro estaba.

Reutiliza src/citations.py para saber qué papers cita DE VERDAD la respuesta.
No depende de Streamlit ni de red.
"""

import sys
import html
from datetime import datetime

try:
    from .. import config
    from . import citations
except (ImportError, ValueError):
    sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    import config
    from src import citations


# --------------------------------------------------------------------------
# Helpers de presentación (copias locales y pequeñas: el informe NO importa la app)
# --------------------------------------------------------------------------
# RETIRADA el 1-sep-2026: `_confidence_pct`. El informe llevaba una tabla
# "Retrieval confidence" con el % de cada paper. Se quita por el mismo motivo que
# en la app (ver app/streamlit_app.py): el lector la interpretaba como "este paper
# responde mejor a la pregunta", cuando solo mide proximidad en el espacio del
# embedding — y en preguntas genéricas los 5 papers empatan. Un informe destinado
# a imprimirse y circular es justo donde un número mal entendido hace más daño.


def _source_label(source):
    s = (source or "").lower()
    if "pubmed" in s:
        return "PubMed"
    if "clinical" in s or s in {"ct", "ctgov"}:
        return "ClinicalTrials.gov"
    return source or "Source"


def _id_label(source, doc_id):
    s = (source or "").lower()
    doc_id = str(doc_id or "")
    if "pubmed" in s:
        return f"PMID {doc_id}"
    if doc_id.upper().startswith("NCT"):
        return doc_id.upper()
    return doc_id


def _relevance_reason(f):
    """Por qué se recuperó una fuente que la respuesta NO citó (para el apéndice)."""
    partes = []
    drugs = [d.strip() for d in (f.get("drugs") or "").split(";") if d.strip()][:3]
    if drugs:
        partes.append("covers " + html.escape(", ".join(drugs)))
    mets = sorted({str(o.get("metric", "")) for o in (f.get("outcomes") or [])
                   if o.get("metric")})
    if mets:
        partes.append("reports " + html.escape(", ".join(mets[:3])))
    detalle = ("; ".join(partes) + ". " if partes else "")
    return (detalle + "Retrieved for the same topic, but no sentence in the answer "
            "could be traced to it with confidence.")


def _split_cited(answer_text, sources):
    """Separa las fuentes REALMENTE citadas de las solo recuperadas.

    OJO — aquí había un bug grave: si la respuesta no traía ninguna cita, el
    código ASUMÍA que la primera fuente estaba citada (`cited_n = {sources[0]}`).
    Medido en un informe real: la respuesta hablaba de nasofaringitis y el informe
    se la atribuyó a un meta-análisis que no menciona esa palabra en todo el
    abstract. Es exactamente lo contrario de lo que promete MIA, así que se ha
    eliminado: si no hay citas, no hay fuentes citadas, y el informe lo dice.
    """
    cited_n = citations.cited_docs(answer_text, len(sources))
    citadas = [s for s in sources if s.get("n") in cited_n]
    otras = [s for s in sources if s.get("n") not in cited_n]
    return citadas, otras


def _answer_html(answer_text):
    """Respuesta en párrafos, con las citas [Doc N] resaltadas como insignias."""
    import re
    parrafos = [p.strip() for p in answer_text.split("\n") if p.strip()]
    out = []
    for p in parrafos:
        esc = html.escape(p)
        esc = re.sub(r"\[Doc\s*(\d+)\]", r'<span class="cite">Doc \1</span>', esc)
        out.append(f"<p>{esc}</p>")
    return "\n".join(out)


def _figures_block(citadas):
    """Tabla de cifras por paper citado, separando EFICACIA de SEGURIDAD.

    Las dos NO pueden ir juntas: un EASI-75 alto es bueno y una tasa de
    nasofaringitis alta es mala; en la misma tabla sin distinguir, se leen igual.

    Si un paper citado no trae cifras, se dice explícitamente CUÁL y por qué
    (muchos meta-análisis redactan los resultados en prosa: "a significant
    decrease in EASI scores", sin un solo número). Es más honesto —y más útil—
    que un mensaje genérico de "no hay cifras".
    """
    if not citadas:
        return ""

    bloques, sin_cifras = [], []
    for f in citadas:
        ocs = f.get("outcomes") or []
        if not ocs:
            sin_cifras.append(f)
            continue
        secciones = ""
        for kind, titulo in (("efficacy", "Efficacy"), ("safety", "Safety")):
            filas_kind = [o for o in ocs if o.get("kind", "efficacy") == kind]
            if not filas_kind:
                continue
            filas_kind.sort(key=lambda o: o.get("value", 0), reverse=True)
            filas = "".join(
                f"<tr><td>{html.escape(str(o.get('metric','')))}"
                f"{(' (week ' + html.escape(str(o.get('week'))) + ')') if o.get('week') else ''}"
                f"</td><td class='num'>{float(o.get('value',0) or 0):g}%</td></tr>"
                for o in filas_kind[:8]
            )
            secciones += (f"<div class='kd-kind'>{titulo}</div>"
                          f"<table class='kd'>{filas}</table>")
        bloques.append(
            f"<div class='kd-group'><div class='kd-head'>"
            f"<span class='doc'>Doc {int(f.get('n',0))}</span> "
            f"{html.escape((f.get('title') or '')[:110])}</div>{secciones}</div>"
        )

    aviso = ""
    if sin_cifras:
        nombres = "".join(
            f"<li><span class='doc'>Doc {int(f.get('n',0))}</span> "
            f"{html.escape((f.get('title') or '')[:110])}</li>" for f in sin_cifras)
        aviso = ("<p class='muted'>No extractable figures in the following cited "
                 "abstract(s) — the results are reported in narrative form (e.g. "
                 "\"a significant decrease in EASI scores\") with no numeric values. "
                 "MIA does not invent them.</p>"
                 f"<ul class='nofig'>{nombres}</ul>")

    if not bloques and not aviso:
        return ""
    intro = ("<p class='muted'>Figures extracted verbatim from the cited abstracts "
             "(not generated by the model). Each figure is traceable to its "
             "document.</p>") if bloques else ""
    return f"<h2>Key figures</h2>{intro}{''.join(bloques)}{aviso}"


# --------------------------------------------------------------------------
# Construcción del informe
# --------------------------------------------------------------------------
def build_report_html(question, data, generated_at=None):
    """Devuelve el HTML completo del informe de evidencia para `data` (el dict que
    devuelve rag/scout: 'answer', 'sources', ...). `question` es la pregunta del
    usuario. `generated_at` (datetime) es opcional (por defecto, ahora)."""
    answer_text = data.get("answer") or data.get("content") or ""
    sources = data.get("sources") or []
    citadas, otras = _split_cited(answer_text, sources)
    fecha = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    disease = html.escape(config.DISEASE)

    # --- Fuentes citadas (arriba, sin ruido de confianza) ---
    def _src_row(f):
        url = html.escape(f.get("url") or "#")
        return (
            f"<li><div class='s-top'><span class='doc'>Doc {int(f.get('n',0))}</span> "
            f"<span class='s-type'>{html.escape(_source_label(f.get('source')))}</span></div>"
            f"<div class='s-title'>{html.escape(f.get('title') or '(untitled)')}</div>"
            f"<div class='s-meta'>{html.escape(_id_label(f.get('source'), f.get('doc_id')))} "
            f"· <a href='{url}'>{url}</a></div></li>"
        )

    if citadas:
        citadas_html = ("<ol class='srcs'>" + "".join(_src_row(f) for f in citadas)
                        + "</ol>")
        titulo_citadas = f"<h2>Cited sources ({len(citadas)})</h2>"
    else:
        # Honestidad: sin citas, no fingimos ninguna (ver _split_cited).
        citadas_html = ("<p class='muted'>None of the sentences in this answer could "
                        "be attributed to a single retrieved paper with confidence, so "
                        "MIA lists no cited source. The retrieved evidence is in the "
                        "appendix below.</p>")
        titulo_citadas = "<h2>Cited sources</h2>"

    # --- Apéndice PLEGADO: recuperadas no citadas ---
    # (La tabla "Retrieval confidence" con el % por paper se retiró el 1-sep-2026.)
    if otras:
        filas = "".join(
            f"<li><div class='s-top'><span class='doc'>Doc {int(f.get('n',0))}</span> "
            f"<span class='s-type'>{html.escape(_source_label(f.get('source')))}</span></div>"
            f"<div class='s-title'>{html.escape(f.get('title') or '(untitled)')}</div>"
            f"<div class='s-reason'>{_relevance_reason(f)}</div></li>"
            for f in otras
        )
        no_citadas = (f"<div class='kd-kind'>Also retrieved · not cited ({len(otras)})</div>"
                      f"<ol class='srcs'>{filas}</ol>")
    else:
        no_citadas = ""

    detalles = (
        "<details class='more'><summary>Retrieval details "
        "(papers read but not cited)</summary>"
        f"{no_citadas}</details>"
    ) if no_citadas else ""

    return _TEMPLATE.format(
        disease=disease,
        fecha=fecha,
        pregunta=html.escape(question or ""),
        respuesta=_answer_html(answer_text),
        titulo_citadas=titulo_citadas,
        citadas=citadas_html,
        figuras=_figures_block(citadas),
        detalles=detalles,
        modelo=html.escape(config.LLM_MODEL),
    )


_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Evidence report · MIA</title>
<style>
  :root {{ --teal:#3f6e66; --ink:#1a1a1a; --slate:#555; --line:#e6e6e6; --mint:#e8f5ee; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:'Inter','Segoe UI',Arial,sans-serif; color:var(--ink);
         max-width:820px; margin:32px auto; padding:0 24px; line-height:1.55; }}
  .brand {{ display:flex; align-items:center; justify-content:space-between;
            border-bottom:3px solid var(--teal); padding-bottom:12px; }}
  .brand h1 {{ font-size:1.5rem; margin:0; color:var(--teal); letter-spacing:-.01em; }}
  .brand .seal {{ font-size:.72rem; font-weight:700; text-transform:uppercase;
                  letter-spacing:.06em; color:var(--teal); border:1px solid var(--teal);
                  border-radius:999px; padding:5px 11px; }}
  .meta {{ color:var(--slate); font-size:.82rem; margin:8px 0 4px; }}
  h2 {{ font-size:1.05rem; margin:26px 0 8px; padding-top:14px;
        border-top:1px solid var(--line); }}
  .q {{ background:var(--mint); border-left:4px solid var(--teal); border-radius:8px;
        padding:12px 14px; font-weight:600; margin:14px 0; }}
  .answer p {{ margin:0 0 11px; }}
  .muted {{ color:var(--slate); font-size:.85rem; }}
  .cite {{ font-family:ui-monospace,Consolas,monospace; font-size:.72rem; font-weight:700;
           color:#2c524c; background:var(--mint); border:1px solid #cde8d8;
           padding:1px 6px; border-radius:6px; }}
  .kd-group {{ margin:14px 0; }}
  .kd-head {{ font-weight:600; font-size:.9rem; margin-bottom:5px; }}
  .kd-kind {{ font-size:.7rem; font-weight:800; letter-spacing:.07em;
              text-transform:uppercase; color:var(--teal); margin:10px 0 3px; }}
  .doc {{ font-family:ui-monospace,Consolas,monospace; font-size:.7rem; font-weight:700;
          color:#2c524c; background:var(--mint); border:1px solid #cde8d8;
          padding:1px 6px; border-radius:6px; }}
  table.kd {{ border-collapse:collapse; width:100%; font-size:.88rem; }}
  table.kd td {{ border-bottom:1px solid var(--line); padding:5px 8px; }}
  table.kd td.num {{ text-align:right; font-family:ui-monospace,Consolas,monospace;
                     font-weight:600; width:90px; }}
  ul.nofig {{ font-size:.85rem; color:var(--slate); padding-left:18px; }}
  ul.nofig li {{ margin:5px 0; }}
  ol.srcs {{ padding-left:18px; }}
  ol.srcs li {{ margin:10px 0; }}
  .s-top {{ display:flex; gap:8px; align-items:center; font-size:.75rem; }}
  .s-type {{ color:var(--slate); }}
  .s-title {{ font-weight:600; margin:2px 0; }}
  .s-meta {{ font-size:.8rem; color:var(--slate); word-break:break-all; }}
  .s-meta a {{ color:var(--teal); }}
  .s-reason {{ font-size:.83rem; color:var(--slate); background:#fafafa;
               border:1px solid var(--line); border-radius:8px; padding:7px 9px; margin-top:4px; }}
  details.more {{ margin-top:26px; border-top:1px solid var(--line); padding-top:14px; }}
  details.more > summary {{ cursor:pointer; font-size:.9rem; font-weight:600;
                            color:var(--teal); }}
  footer {{ margin-top:30px; padding-top:12px; border-top:1px solid var(--line);
            color:var(--slate); font-size:.78rem; }}
  @media print {{
    body {{ margin:0; }} h2 {{ page-break-after:avoid; }}
    li,.kd-group {{ page-break-inside:avoid; }}
    details.more {{ }} details.more > summary {{ list-style:none; }}
  }}
</style></head><body>
  <div class="brand">
    <h1>MIA — Evidence report</h1>
    <span class="seal">Generated 100% locally</span>
  </div>
  <div class="meta">{disease} · {fecha} · local biomedical model: {modelo}</div>

  <h2>Question</h2>
  <div class="q">{pregunta}</div>

  <h2>Answer</h2>
  <div class="answer">{respuesta}</div>

  {titulo_citadas}
  {citadas}

  {figuras}

  {detalles}

  <footer>Generated locally by <b>MIA</b> (Medical Intelligence Agent) — a sovereign
  biomedical RAG engine. No data left this computer. Every figure is traceable to its
  source; MIA declines to answer when the local evidence is insufficient.</footer>
</body></html>"""


if __name__ == "__main__":
    # Prueba aislada: informe de ejemplo con datos ficticios (sin red ni LLM).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    demo = {
        # Cita Doc 1 (trae cifras) y Doc 2 (meta-análisis narrativo, sin ninguna):
        # así la prueba ejercita los DOS caminos de "Key figures".
        "answer": ("Dupilumab improves EASI scores versus placebo. [Doc 1] "
                   "Nasopharyngitis was the most frequent adverse event. [Doc 1] "
                   "A pooled analysis reached the same conclusion. [Doc 2]"),
        "sources": [
            {"n": 1, "source": "pubmed", "doc_id": "111", "title": "Pivotal dupilumab trial",
             "url": "https://pubmed.ncbi.nlm.nih.gov/111/", "drugs": "dupilumab",
             "similarity": 71.0, "outcomes": [
                 {"metric": "EASI 75", "value": 51, "week": 16, "kind": "efficacy"},
                 {"metric": "Nasopharyngitis", "value": 12.5, "week": None, "kind": "safety"}]},
            {"n": 2, "source": "pubmed", "doc_id": "222", "title": "Narrative meta-analysis",
             "url": "https://pubmed.ncbi.nlm.nih.gov/222/", "drugs": "dupilumab",
             "similarity": 69.0, "outcomes": []},
            {"n": 3, "source": "clinicaltrials", "doc_id": "NCT01", "title": "Other retrieved study",
             "url": "https://clinicaltrials.gov/study/NCT01", "drugs": "tralokinumab",
             "similarity": 68.0, "outcomes": []},
        ],
    }
    out = build_report_html("What is the efficacy of dupilumab?", demo)
    destino = config.DATA_DIR / "_report_demo.html"
    destino.write_text(out, encoding="utf-8")
    print(f"[OK] informe generado: {len(out)} chars -> {destino}")
    for marca in ("Cited sources", "Key figures", "Retrieval details", "narrative form"):
        print(f"  contiene '{marca}': {marca in out}")
