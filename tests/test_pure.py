"""
tests/test_pure.py — Pruebas de las funciones PURAS de MIA (sin Ollama ni ChromaDB).

Hasta ahora la única "prueba" de cada módulo era su bloque `if __name__ ==
"__main__"`, que hay que ejecutar a mano y leer a ojo. Estas pruebas cubren lo
que se rompe con más facilidad y menos se nota:
  - los guardianes de la generación (modo examen, rendición, eco del contexto),
  - el reparto y la validación deterministas de citas,
  - la extracción de cifras (y que NO coja intervalos de confianza),
  - la traducción de títulos de ClinicalTrials.gov a endpoints canónicos,
  - la carga de perfiles de dominio (incluido cambiar de patología en caliente).

Ejecutar:   ./.venv/Scripts/python.exe -m pytest -q
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from src import citations, outcomes, processing, rag  # noqa: E402


@pytest.fixture(autouse=True)
def _dominio_por_defecto():
    """Las pruebas asumen el perfil original. Si el usuario dejó otro perfil activo
    (domains/active.txt o MIA_DOMAIN), lo activamos aquí SOLO en memoria (sin
    persistir) para que la suite no dependa del estado de la máquina."""
    config.activate_domain("atopic_dermatitis")
    yield
    config.activate_domain("atopic_dermatitis")


# --------------------------------------------------------------------------
# Perfil de dominio por defecto: los valores del capstone siguen intactos
# --------------------------------------------------------------------------
def test_default_domain_values():
    assert config.DOMAIN_SLUG == "atopic_dermatitis"
    assert config.DISEASE == "Atopic dermatitis"
    assert config.DRUGS == ["dupilumab", "tralokinumab", "lebrikizumab", "nemolizumab",
                            "upadacitinib", "baricitinib", "abrocitinib"]
    assert config.ALL_DRUGS[-3:] == ["ruxolitinib", "crisaborole", "delgocitinib"]
    assert config.BIOLOGICS and config.JAK_INHIBITORS
    # Colecciones históricas: el índice de 9.734 chunks sigue siendo válido.
    assert config.collection_name("medcpt") == "mia_evidence_medcpt"
    assert config.collection_name("openai") == "mia_evidence_openai"
    assert config.collection_name("sentence-transformers") == "mia_evidence"
    assert config.BRONZE_DIR == config.DATA_DIR / "bronze"


@pytest.mark.parametrize("label, hit, miss", [
    ("EASI 75", ["EASI-75", "EASI 75", "easi75"], ["EASI 750", "EASI-7"]),
    ("IGA 0/1", ["IGA 0/1", "IGA 0 / 1"], ["IGA 0/2"]),
    ("PASI 90", ["PASI 90", "PASI-90"], ["PASI 900"]),
    ("ACR20", ["ACR20", "ACR 20"], ["ACR200"]),
    ("vIGA-AD 0/1", ["vIGA-AD 0/1", "vIGA AD 0/1"], []),
])
def test_endpoint_spec_regex(label, hit, miss):
    import re
    spec = config.endpoint_spec(label)
    assert spec["label"] == label
    for h in hit:
        assert re.search(spec["regex"], h, re.I), (label, h, spec["regex"])
    for m in miss:
        assert not re.search(spec["regex"], m, re.I), (label, m, spec["regex"])


def test_domain_switch_roundtrip(tmp_path, monkeypatch):
    """Un perfil nuevo cambia enfermedad, fármacos, colección y endpoints; volver
    al perfil por defecto restaura todo. Se usa una carpeta temporal para no
    tocar domains/ del repositorio."""
    monkeypatch.setattr(config, "DOMAINS_DIR", tmp_path)
    monkeypatch.setattr(config, "ACTIVE_DOMAIN_FILE", tmp_path / "active.txt")
    # copiamos el perfil por defecto para poder volver a él
    (tmp_path / "atopic_dermatitis.json").write_text(
        (ROOT / "domains" / "atopic_dermatitis.json").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "psoriasis_test.json").write_text(json.dumps({
        "disease": "Plaque psoriasis", "synonyms": ["psoriasis"],
        "drug_classes": {"il17": ["secukinumab", "ixekizumab"], "oral": ["apremilast"]},
        "mechanisms": {"il-17": ["secukinumab", "ixekizumab"]},
        "efficacy_endpoints": ["PASI 90", "PASI 75"],
    }), encoding="utf-8")
    try:
        config.activate_domain("psoriasis_test")
        assert config.DISEASE == "Plaque psoriasis"
        assert config.DISEASE_QUERY == "(Plaque psoriasis OR psoriasis)"
        assert config.DRUGS == ["secukinumab", "ixekizumab", "apremilast"]
        assert config.BIOLOGICS == [] and config.JAK_INHIBITORS == []
        assert config.CHROMA_COLLECTION == "mia_psoriasis_test_medcpt"
        assert config.BRONZE_DIR.name == "psoriasis_test"
        assert [e["label"] for e in config.EFFICACY_ENDPOINTS] == ["PASI 90", "PASI 75"]
        # outcomes y processing siguen al dominio activo sin reiniciar
        pts = outcomes.extract_outcomes("PASI 75 was achieved by 81.0% at week 12.", 1)
        assert [(p["metric"], p["value"], p["week"]) for p in pts] == [("PASI 75", 81.0, "12")]
        assert processing._canonical_endpoint(
            "Percentage of Participants Achieving PASI 75 Percent Improvement at Week 16") == "PASI 75"
        assert processing._canonical_endpoint("EASI 75 percent response") is None
    finally:
        config.activate_domain("atopic_dermatitis")
    assert config.CHROMA_COLLECTION == "mia_evidence_medcpt"
    assert outcomes.extract_outcomes("PASI 75 was achieved by 81.0%.", 1) == []


# --------------------------------------------------------------------------
# Guardianes de la generación (rag)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("texto, degenerado, empieza", [
    ("The Answer is: Dupilumab is effective in treating atopic dermatitis, improving "
     "signs and symptoms. However, results vary.", False, "Dupilumab is effective"),
    ("**The answer is:** Dupilumab improves EASI-75 at week 16.", False, "Dupilumab improves"),
    ("The answer to this question is that lebrikizumab reduces itch.", False, "That lebrikizumab"),
    ("The answer is yes.", True, None),
    ("Answer: B", True, None),
    ("The provided CONTEXT does not contain a specific question or task. Please provide "
     "the necessary information to generate an answer.", True, None),
    ("The provided CONTEXT is insufficient for answering the QUESTION without additional "
     "information.", True, None),
    ("The evidence does not report discontinuation rates for abrocitinib. A phase 3 trial "
     "found EASI-75 in 62% at week 12. Conjunctivitis was rare. Serious adverse events "
     "were 2%.", False, "The evidence"),
    ("Answering this question requires the pediatric data. A pediatric trial reported "
     "EASI-75 in 40%. Adults reached 50%. Both converge.", False, "Answering"),
    ("Dupilumab is effective. [Doc N]", True, None),
    ("[Doc 1] (pubmed:123) Title of the paper. Dupilumab was studied.", True, None),
    ("Лебрикизумаб is effective in adults with atopic dermatitis.", True, None),
])
def test_looks_degenerate_and_exam_prefix(texto, degenerado, empieza):
    assert rag._looks_degenerate(texto) is degenerado
    if not degenerado:
        assert rag._strip_exam_prefix(texto).startswith(empieza)


def test_detect_intent():
    assert rag.detect_intent("What are the adverse events of upadacitinib?") == "safety"
    assert rag.detect_intent("Dupilumab versus tralokinumab efficacy") == "comparative"
    assert rag.detect_intent("What is the mechanism of action of nemolizumab?") == "mechanism"
    assert rag.detect_intent("Is lebrikizumab effective?") == "efficacy"


def test_prompt_uses_domain_endpoints():
    assert "{endpoints}" not in rag._build_system_prompt("efficacy")
    assert config.ENDPOINT_EXAMPLES in rag._build_system_prompt("efficacy")


# --------------------------------------------------------------------------
# Citas deterministas
# --------------------------------------------------------------------------
def _fuentes():
    return [
        {"n": 1, "title": "Real-World Effectiveness of Lebrikizumab ... Head and Neck Involvement",
         "snippet": "real-world European cohort head and neck", "drugs": "lebrikizumab", "outcomes": []},
        {"n": 2, "title": "Efficacy and Safety of Lebrikizumab: systematic review and meta-analysis",
         "snippet": "systematic review meta-analysis randomized controlled trials placebo",
         "drugs": "lebrikizumab", "outcomes": []},
        {"n": 3, "title": "Lebrikizumab in the elderly: a case series",
         "snippet": "elderly case series", "drugs": "lebrikizumab", "outcomes": []},
        {"n": 4, "title": "Lebrikizumab-induced psoriasis in a patient",
         "snippet": "psoriasis induced", "drugs": "lebrikizumab", "outcomes": []},
    ]


def test_redistribute_citations_assigns_by_overlap():
    respuesta = ("Lebrikizumab is effective for moderate-to-severe atopic dermatitis. "
                 "The efficacy was notable in the head and neck. "
                 "Systematic reviews and meta-analyses confirmed its efficacy versus placebo. "
                 "Some patients developed lebrikizumab-induced psoriasis.")
    out = citations.redistribute_citations(respuesta, _fuentes())
    assert "head and neck. [Doc 1]" in out
    assert "versus placebo. [Doc 2]" in out
    assert "psoriasis. [Doc 4]" in out
    assert out.startswith("Lebrikizumab is effective for moderate-to-severe atopic dermatitis.")


def test_strip_invalid_and_cited_docs():
    txt = "Claim A. [Doc 1] Claim B. [Doc 7] Claim C. [Doc 2, Doc 9, Doc 3]"
    limpio = citations.strip_invalid_citations(txt, 3)
    assert "[Doc 7]" not in limpio and "[Doc 9]" not in limpio
    assert citations.cited_docs(limpio, 3) == {1, 2, 3}
    assert citations.cited_docs("nothing cited", 3) == set()


# --------------------------------------------------------------------------
# Cifras verbatim (outcomes) y endpoints canónicos (processing)
# --------------------------------------------------------------------------
def test_extract_outcomes_efficacy_safety_and_ci_filter():
    texto = ("EASI 75 was achieved by 65.0%, 68.9%, and 82.6% of patients at weeks 16, 24, "
             "and 52, respectively. IGA 0/1 was achieved by 82.6% of patients at week 52. "
             "Adverse events were reported in 62.3% of patients. The most frequent were "
             "nasopharyngitis (12.5%) and acne (9.1%). Serious adverse events occurred in 2.8%. "
             "The pooled risk ratio was 1.42 (95% CI 1.10-1.83) with I2 = 77.5%.")
    pts = outcomes.extract_outcomes(texto, doc_n=3, max_points=30)
    got = {(p["metric"], p["value"], p["week"], p["kind"]) for p in pts}
    assert ("EASI 75", 82.6, "52", "efficacy") in got
    assert ("EASI 75", 65.0, "16", "efficacy") in got
    assert ("IGA 0/1", 82.6, "52", "efficacy") in got
    assert ("Nasopharyngitis", 12.5, None, "safety") in got
    assert ("Serious adverse events", 2.8, None, "safety") in got
    valores = {p["value"] for p in pts}
    assert 95.0 not in valores and 77.5 not in valores      # IC y heterogeneidad fuera
    assert all(p["doc_n"] == 3 for p in pts)


def test_canonical_endpoint_from_ct_titles():
    assert processing._canonical_endpoint(
        "Percentage of Participants Achieving Eczema Area and Severity Index (EASI) "
        "Response >=75 Percent (%) Improvement From Baseline at Week 12") == "EASI 75"
    assert processing._canonical_endpoint(
        "Percentage of Participants With IGA Score of Clear (0) or Almost Clear (1)") == "IGA 0/1"
    assert processing._canonical_endpoint("Change From Baseline in Pruritus NRS") is None


def test_comparator_arm_detection():
    assert processing._is_comparator_arm("Placebo") is True
    assert processing._is_comparator_arm("Matching Placebo up to Week 16") is True
    assert processing._is_comparator_arm("Dupilumab 300 mg + Oral Placebo") is False


# --------------------------------------------------------------------------
# suggest_drugs.normalize — agrupa variantes de una intervención de CT.gov
# --------------------------------------------------------------------------
# Es una función pura (sin red): el script consulta ClinicalTrials.gov, pero la
# limpieza de nombres se prueba aquí con casos reales vistos en el registro.

def test_normalize_strips_dose_route_and_brand():
    from suggest_drugs import normalize
    assert normalize("Secukinumab 300 mg SC (Cosentyx)") == "secukinumab"
    assert normalize("Adalimumab 40mg injection") == "adalimumab"
    assert normalize("Certolizumab Pegol") == "certolizumab pegol"


def test_normalize_drops_placebo_and_comparators():
    from suggest_drugs import normalize
    for nombre in ("Placebo", "Matching placebo", "Vehicle cream", "Standard of care", ""):
        assert normalize(nombre) == "", nombre
