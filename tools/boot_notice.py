"""Run at laptop start (Start Everything.bat). If the heartbeat file is old, the laptop was off/asleep: tell the owner on
Telegram how long, and that everything is resuming. Heartbeat is written every few minutes by 'Factory Worker Always On.bat'."""
import json
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
HB = ROOT / "work" / "heartbeat.txt"


def main() -> None:
    if not HB.exists():
        return
    gap = (time.time() - HB.stat().st_mtime) / 60
    if gap < 20:
        return
    try:
        tok = (ROOT / "rocky" / "telegram_token.txt").read_text().strip()
        o = json.loads((ROOT / "rocky" / "telegram_owner.json").read_text())
        cid = o.get("chat_id") or o.get("id") or o.get("owner_id") or next(iter(o.values()))
        h, m = int(gap // 60), int(gap % 60)
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", timeout=30, data={
            "chat_id": cid, "text": f"Rocky: laptop was off or asleep for about {h} h {m} min. It is back on; all loops "
                                    "(footage tagging, factory renders, Rocky rounds) are restarting and continue from saved state."})
    except Exception as e:  # never block startup
        print("boot notice failed:", e)
    (ROOT / "rocky" / "notes.md").open("a", encoding="utf-8").write(
        f"\n{time.strftime('%Y-%m-%d %H:%M')} (boot) laptop was off/asleep ~{int(gap)} min; loops restarted by Start Everything.\n")


if __name__ == "__main__":
    main()
