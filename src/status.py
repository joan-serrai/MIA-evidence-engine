"""
src/status.py — Chequeo del estado del sistema.  [MIA 1.0]

Comprueba, de forma ROBUSTA (nunca lanza), que MIA puede funcionar:
  - Ollama en marcha y con el modelo biomédico necesario,
  - corpus vectorial (ChromaDB) indexado y con contenido.

Lo usan el panel "Estado del sistema" de la UI y los avisos accionables de
arranque (si algo falta, la app lo dice con la solución en vez de romperse).
"""

import os
import sys

try:
    from .. import config
except (ImportError, ValueError):
    sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    import config


def ollama_host():
    """Host de Ollama (respeta OLLAMA_HOST del entorno; por defecto localhost)."""
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


def ollama_status(timeout=3):
    """(-> dict) ¿Está Ollama vivo y qué modelos tiene descargados?"""
    host = ollama_host()
    try:
        import requests
        r = requests.get(f"{host}/api/tags", timeout=timeout)
        r.raise_for_status()
        tags = {m.get("name") for m in (r.json().get("models") or [])}
        return {"up": True, "host": host, "tags": tags, "error": None}
    except Exception as e:
        return {"up": False, "host": host, "tags": set(), "error": str(e)}


def corpus_status():
    """(-> dict) Nº de fragmentos del corpus activo (colección según el backend)."""
    coll = config.CHROMA_COLLECTION
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        col = client.get_collection(coll)
        n = col.count()
        return {"ok": n > 0, "chunks": n, "collection": coll, "error": None}
    except Exception as e:
        return {"ok": False, "chunks": 0, "collection": coll, "error": str(e)}


def system_status(timeout=3):
    """(-> dict) Estado global: {ready, ollama, models, corpus}.

    `ready` es True solo si Ollama responde, tiene el modelo biomédico y el corpus
    tiene contenido. `models` mapea cada modelo imprescindible a si está presente.
    """
    oll = ollama_status(timeout)
    # Para el PRODUCTO solo es imprescindible el modelo biomédico. El generalista y
    # el juez (config.LLM_GENERALIST / LLM_JUDGE) son solo para la evaluación (Fase 4).
    needed = [config.LLM_MODEL]
    models = {m: (m in oll["tags"]) for m in needed}
    cor = corpus_status()
    ready = oll["up"] and all(models.values()) and cor["ok"]
    return {"ready": ready, "ollama": oll, "models": models, "corpus": cor}


def fix_hints(status):
    """(-> list[str]) Instrucciones accionables para lo que falte (o [] si todo OK)."""
    hints = []
    if not status["ollama"]["up"]:
        hints.append(f"Ollama no responde en {status['ollama']['host']}. "
                     "Arráncalo con:  ollama serve")
    else:
        faltan = [m for m, ok in status["models"].items() if not ok]
        for m in faltan:
            hints.append(f"Falta el modelo biomédico. Descárgalo con:  ollama pull {m}")
    if not status["corpus"]["ok"]:
        hints.append("El corpus vectorial está vacío. Indéxalo con:  "
                     "python run_phase1.py")
    return hints


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    s = system_status()
    print("Ollama:", "OK" if s["ollama"]["up"] else "CAÍDO", "·", s["ollama"]["host"])
    print("Modelos:", {m: ("OK" if ok else "FALTA") for m, ok in s["models"].items()})
    print("Corpus:", s["corpus"]["chunks"], "fragmentos ·", s["corpus"]["collection"])
    print("LISTO PARA PRODUCCIÓN:", s["ready"])
    for h in fix_hints(s):
        print("  →", h)
