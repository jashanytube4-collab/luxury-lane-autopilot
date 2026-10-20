"""Store a new Instagram access key for the autopilot (after the owner generated a System User token).

    python -m tools.set_ig_token          (reads work/ig_token.txt, checks it, stores IG_TOKEN on GitHub, deletes the file)

The key is never printed. It is checked by reading @faz3 through Business Discovery exactly like the daily runs do.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "work" / "ig_token.txt"
GH = ROOT / "models" / "gh" / "bin" / "gh.exe"
REPO = "jashanytube4-collab/luxury-lane-autopilot"
GRAPH = "https://graph.facebook.com/v21.0"
IG_USER_ID = "17841458443348623"          # @thelaneluxury


def main() -> None:
    token = FILE.read_text(encoding="utf-8-sig").strip() if FILE.exists() else ""
    if len(token) < 50:
        sys.exit("No key found in work/ig_token.txt (paste the token there and save).")
    me = requests.get(f"{GRAPH}/me", params={"access_token": token, "fields": "id,name"}, timeout=30).json()
    if "error" in me:
        sys.exit(f"Meta did not accept this key: {me['error'].get('message')}")
    test = requests.get(f"{GRAPH}/{IG_USER_ID}", params={
        "access_token": token,
        "fields": "business_discovery.username(faz3){username,followers_count,media_count}"}, timeout=30).json()
    if "error" in test:
        sys.exit(f"The key works but cannot read @faz3 yet: {test['error'].get('message')}\n"
                 "Check that the system user has the Instagram account assigned and the permissions "
                 "instagram_basic + instagram_manage_insights + pages_read_engagement.")
    bd = test["business_discovery"]
    print(f"Key OK (belongs to: {me.get('name')}). Reads @{bd['username']}: {bd['followers_count']:,} followers, "
          f"{bd['media_count']:,} posts.")
    p = subprocess.run([str(GH), "secret", "set", "IG_TOKEN", "-R", REPO], input=token, text=True,
                       capture_output=True)
    if p.returncode != 0:
        sys.exit(f"Could not store the key on GitHub: {p.stderr.strip()}")
    FILE.unlink()
    print("Stored as the IG_TOKEN secret on GitHub; the local copy was deleted. The next run uses it.")


if __name__ == "__main__":
    main()
