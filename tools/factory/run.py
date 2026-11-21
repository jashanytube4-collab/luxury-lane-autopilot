"""python -m tools.factory.run <script.json> [preview_beats]"""
import sys
from .core import build
if __name__ == "__main__":
    build(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 0)
