"""One command for everything that must pass on THIS laptop before any GitHub render (no Claude, no render):

    .venv\Scripts\python.exe -m tools.ready_check zz_fazza_money

1. tools.prerender_gate  (script, clips vs sentences, rules 1-26, topic, repeats; writes the gate stamp)
2. tools.plan_check      (the real renderer's shot plan: repeats, filler, stock runs, first 30 s, ending, thumbnail)
Prints READY only when both pass; otherwise lists every failure of both, so they are fixed together.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(mod: str, story: str) -> tuple[int, str]:
    r = subprocess.run([sys.executable, "-m", mod, story], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    lines = [ln for ln in (r.stdout or "").splitlines() if "FAIL" in ln or "PASS" in ln or ln.strip().startswith("warn")]
    return r.returncode, "\n".join(lines)


def main(story: str) -> int:
    code = 0
    for mod in ("tools.prerender_gate", "tools.plan_check"):
        rc, text = run(mod, story)
        print(f"--- {mod}: {'PASS' if rc == 0 else 'FAIL'}")
        print(text)
        code = code or rc
    print("READY to render" if code == 0 else "NOT READY: fix everything listed above, then run this again")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
