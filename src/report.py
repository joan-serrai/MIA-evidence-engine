"""
src/report.py — Informe de evidencia EXPORTABLE (feature de producto).  [MIA 1.0]

Genera un documento HTML AUTÓNOMO (CSS embebido, sin llamadas a red) a partir de
una respuesta de MIA. El usuario lo descarga desde la app y lo abre en el
navegador; con Ctrl+P → "Guardar como PDF" obtiene un informe con marca, listo
para compartir con un clínico o presentar. Refuerza la tesis de MIA: cada dato es
trazable a su fuente, y todo se ha generado en local.

Reutiliza src/citations.py para saber qué papers cita DE VERDAD la respuesta, de
modo que el informe distingue las fuentes citadas de las solo recuperadas (estas
últimas van en un apéndice, con su motivo). No depende de Streamlit ni de red.
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
def _confidence_pct(sim):
    """Similarity cruda → % de confianza 0-100 (misma convención que la UI)."""
    if getattr(config, "EMBEDDING_BACKEND", "") == "medcpt":
        pct = (float(sim) - 55.0) / (80.0 - 55.0) * 100.0
    else:
        pct = float(sim) * 100.0
    return int(round(max(0.0, min(100.0, pct))))


def _source_label(source):
    s = (source or "").lower()
    if "pubmed" in s:
        return "PubMed"
    if "clinical" in s or s in {"ct", "ctgov"}:
        return "ClinicalTrials.gov"
    return source or "Fuente"


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
        partes.append("trata " + html.escape(", ".join(drugs)))
    mets = sorted({str(o.get("metric", "")).split()[0]
                   for o in (f.get("outcomes") or []) if o.get("metric")})
    if mets:
        partes.append("aporta cifras " + html.escape(", ".join(mets[:3])))
    detalle = ("; ".join(partes) + ". " if partes else "")
    return (detalle + "Recuperada por afinidad temática con la pregunta, "
            "pero la respuesta no se apoyó en ella.")


def _split_cited(answer_text, sources):
    cited_n = citations.cited_docs(answer_text, len(sources))
    if not cited_n and sources:
        cited_n = {sources[0].get("n")}
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

    # --- Bloque "Datos clave" agrupado por paper citado (cita una vez por paper) ---
    grupos = [(f, sorted((f.get("outcomes") or []),
                         key=lambda p: p.get("value", 0), reverse=True))
              for f in citadas]
    grupos = [(f, ocs) for f, ocs in grupos if ocs]
    if grupos:
        bloques = []
        for f, ocs in grupos:
            filas = "".join(
                f"<tr><td>{html.escape(str(o.get('metric','')))}"
                f"{(' (semana ' + html.escape(str(o.get('week'))) + ')') if o.get('week') else ''}"
                f"</td><td class='num'>{float(o.get('value',0) or 0):g}%</td></tr>"
                for o in ocs[:8]
            )
            bloques.append(
                f"<div class='kd-group'><div class='kd-head'>"
                f"<span class='doc'>Doc {int(f.get('n',0))}</span> "
                f"{html.escape((f.get('title') or '')[:110])}</div>"
                f"<table class='kd'>{filas}</table></div>"
            )
        datos_clave = ("<h2>Datos clave de la evidencia</h2>"
                       "<p class='muted'>Cifras extraídas literalmente de los abstracts "
                       "(no generadas por el modelo). La referencia se muestra una vez por "
                       "paper.</p>" + "".join(bloques))
    else:
        datos_clave = ("<h2>Datos clave de la evidencia</h2>"
                       "<p class='muted'>El/los paper(s) citado(s) no contienen cifras "
                       "numéricas extraíbles de forma fiable; no se listan para no inventar "
                       "datos.</p>")

    # --- Fuentes citadas ---
    def _src_row(f, mostrar_conf=True):
        pct = _confidence_pct(f.get("similarity", 0) or 0)
        conf = (f"<span class='conf'>{pct}% confianza</span>" if mostrar_conf
                else f"<span class='conf muted'>{pct}% afinidad</span>")
        url = html.escape(f.get("url") or "#")
        return (
            f"<li><div class='s-top'><span class='doc'>Doc {int(f.get('n',0))}</span> "
            f"<span class='s-type'>{html.escape(_source_label(f.get('source')))}</span> "
            f"{conf}</div>"
            f"<div class='s-title'>{html.escape(f.get('title') or '(sin título)')}</div>"
            f"<div class='s-meta'>{html.escape(_id_label(f.get('source'), f.get('doc_id')))} "
            f"· <a href='{url}'>{url}</a></div></li>"
        )

    citadas_html = ("<ol class='srcs'>" + "".join(_src_row(f) for f in citadas) + "</ol>"
                    if citadas else "<p class='muted'>Sin fuentes citadas.</p>")

    # --- Apéndice: también recuperadas (no citadas), con motivo ---
    if otras:
        filas = "".join(
            f"<li><div class='s-top'><span class='doc'>Doc {int(f.get('n',0))}</span> "
            f"<span class='s-type'>{html.escape(_source_label(f.get('source')))}</span></div>"
            f"<div class='s-title'>{html.escape(f.get('title') or '(sin título)')}</div>"
            f"<div class='s-reason'>{_relevance_reason(f)}</div></li>"
            for f in otras
        )
        apendice = (f"<h2>También recuperadas · no citadas ({len(otras)})</h2>"
                    "<p class='muted'>MIA las recuperó por afinidad con la pregunta, pero la "
                    "respuesta no se apoyó en ellas. Se listan con su motivo, por transparencia."
                    f"</p><ol class='srcs'>{filas}</ol>")
    else:
        apendice = ""

    return _TEMPLATE.format(
        disease=disease,
        fecha=fecha,
        pregunta=html.escape(question or ""),
        respuesta=_answer_html(answer_text),
        datos_clave=datos_clave,
        citadas=citadas_html,
        n_citadas=len(citadas),
        apendice=apendice,
        modelo=html.escape(config.LLM_MODEL),
    )


_TEMPLATE = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<title>Informe de evidencia · MIA</title>
<style>
  :root {{ --teal:#3f6e66; --ink:#1a1a1a; --slate:#555; --line:#e6e6e6; --mint:#e8f5ee; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:'Inter','Segoe UI',Arial,sans-serif; color:var(--ink);
         max-width:820px; margin:32px auto; padding:0 24px; line-height:1.5; }}
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
  .muted {{ color:var(--slate); font-size:.85rem; }}
  .cite {{ font-family:ui-monospace,Consolas,monospace; font-size:.72rem; font-weight:700;
           color:#2c524c; background:var(--mint); border:1px solid #cde8d8;
           padding:1px 6px; border-radius:6px; }}
  .kd-group {{ margin:12px 0; }}
  .kd-head {{ font-weight:600; font-size:.9rem; margin-bottom:5px; }}
  .doc {{ font-family:ui-monospace,Consolas,monospace; font-size:.7rem; font-weight:700;
          color:#2c524c; background:var(--mint); border:1px solid #cde8d8;
          padding:1px 6px; border-radius:6px; }}
  table.kd {{ border-collapse:collapse; width:100%; font-size:.88rem; }}
  table.kd td {{ border-bottom:1px solid var(--line); padding:5px 8px; }}
  table.kd td.num {{ text-align:right; font-family:ui-monospace,Consolas,monospace;
                     font-weight:600; width:90px; }}
  ol.srcs {{ padding-left:18px; }}
  ol.srcs li {{ margin:10px 0; }}
  .s-top {{ display:flex; gap:8px; align-items:center; font-size:.75rem; }}
  .s-type {{ color:var(--slate); }}
  .conf {{ color:var(--teal); font-weight:600; }}
  .s-title {{ font-weight:600; margin:2px 0; }}
  .s-meta {{ font-size:.8rem; color:var(--slate); word-break:break-all; }}
  .s-meta a {{ color:var(--teal); }}
  .s-reason {{ font-size:.83rem; color:var(--slate); background:#fafafa;
               border:1px solid var(--line); border-radius:8px; padding:7px 9px; margin-top:4px; }}
  footer {{ margin-top:30px; padding-top:12px; border-top:1px solid var(--line);
            color:var(--slate); font-size:.78rem; }}
  @media print {{ body {{ margin:0; }} h2 {{ page-break-after:avoid; }} li,.kd-group {{ page-break-inside:avoid; }} }}
</style></head><body>
  <div class="brand">
    <h1>MIA — Informe de evidencia</h1>
    <span class="seal">Generado 100% en local</span>
  </div>
  <div class="meta">{disease} · {fecha} · modelo biomédico local: {modelo}</div>

  <h2>Pregunta</h2>
  <div class="q">{pregunta}</div>

  <h2>Respuesta ({n_citadas} fuente(s) citada(s))</h2>
  {respuesta}

  {datos_clave}

  <h2>Fuentes citadas</h2>
  {citadas}

  {apendice}

  <footer>Generado localmente por <b>MIA</b> (Medical Intelligence Agent) — motor RAG
  biomédico soberano. Ningún dato salió de este ordenador. Cada cifra es trazable a su
  fuente; MIA no responde cuando no hay evidencia suficiente.</footer>
</body></html>"""


if __name__ == "__main__":
    # Prueba aislada: informe de ejemplo con datos ficticios (sin red ni LLM).
    demo = {
        "answer": "Dupilumab improves EASI scores versus placebo [Doc 1].",
        "sources": [
            {"n": 1, "source": "pubmed", "doc_id": "111", "title": "Pivotal dupilumab trial",
             "url": "https://pubmed.ncbi.nlm.nih.gov/111/", "drugs": "dupilumab",
             "similarity": 71.0, "outcomes": [{"metric": "EASI 75", "value": 51, "week": 16}]},
            {"n": 2, "source": "clinicaltrials", "doc_id": "NCT01", "title": "Other retrieved study",
             "url": "https://clinicaltrials.gov/study/NCT01", "drugs": "tralokinumab",
             "similarity": 68.0, "outcomes": []},
        ],
    }
    out = build_report_html("What is the efficacy of dupilumab?", demo)
    print(out[:400])
    print(f"\n[OK] informe generado: {len(out)} chars")
