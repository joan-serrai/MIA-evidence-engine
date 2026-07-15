"""
check_setup.py — Comprobación rápida del entorno.

Ejecuta:  python check_setup.py

No instala nada ni descarga nada: solo te dice si tu entorno está listo
y qué te falta. Úsalo como primer "¿funciona?" del proyecto.
"""

import sys
from pathlib import Path

# En Windows la consola usa por defecto una codificación antigua (cp1252) que
# no sabe representar emojis (🎉) ni algunos símbolos. Forzamos UTF-8 para que
# el script no se rompa al imprimir caracteres especiales.
sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).parent


def check_python():
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 11)
    print(f"[{'OK' if ok else '!!'}] Python {v.major}.{v.minor}.{v.micro}"
          + ("" if ok else "  → se recomienda Python 3.11 o superior"))
    return ok


def check_folders():
    needed = ["data/bronze", "data/silver", "data/chroma", "src", "app"]
    all_ok = True
    for rel in needed:
        exists = (BASE_DIR / rel).exists()
        all_ok = all_ok and exists
        print(f"[{'OK' if exists else '!!'}] carpeta {rel}")
    return all_ok


def check_packages():
    # Comprobamos sin romper si aún no has hecho 'pip install -r requirements.txt'.
    paquetes = ["requests", "dotenv", "chromadb", "sentence_transformers",
                "ollama", "streamlit", "pandas", "tqdm"]
    faltan = []
    for p in paquetes:
        try:
            __import__(p)
            print(f"[OK] paquete {p}")
        except ImportError:
            faltan.append(p)
            print(f"[..] paquete {p}  → aún no instalado")
    return faltan


if __name__ == "__main__":
    print("=" * 55)
    print(" MIA · comprobación de entorno")
    print("=" * 55)
    py_ok = check_python()
    print("-" * 55)
    folders_ok = check_folders()
    print("-" * 55)
    faltan = check_packages()
    print("=" * 55)

    if faltan:
        print(f"\nSiguiente paso: instala las dependencias que faltan con:")
        print("   pip install -r requirements.txt")
    elif py_ok and folders_ok:
        print("\n¡Entorno listo! 🎉  Podemos empezar por la Fase 1 (ingesta de datos).")
    print()
