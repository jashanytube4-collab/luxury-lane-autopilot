"""One-time Instagram connection (official Graph API).

    python tools/connect_instagram.py <APP_ID> <APP_SECRET> <SHORT_LIVED_USER_TOKEN>

1. trades the short-lived token from the Graph API Explorer for a long-lived one,
2. finds the Facebook Page linked to @thelaneluxury and its never-expiring Page token,
3. checks that Prince Hamdan's @faz3 can be read through Business Discovery,
4. stores IG_TOKEN and IG_USER_ID as GitHub secrets so the daily runs can use them.
"""
import subprocess
import sys
from pathlib import Path

import requests

GRAPH = "https://graph.facebook.com/v21.0"
REPO = "jashanytube4-collab/luxury-lane-autopilot"
GH = Path(__file__).resolve().parents[1] / "models" / "gh" / "bin" / "gh.exe"


def main() -> None:
    app_id, app_secret, short = sys.argv[1:4]
    r = requests.get(f"{GRAPH}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
        "fb_exchange_token": short}, timeout=30).json()
    if "access_token" not in r:
        sys.exit(f"Could not extend the token: {r}")
    long_user = r["access_token"]
    pages = requests.get(f"{GRAPH}/me/accounts", params={
        "access_token": long_user, "fields": "name,access_token,instagram_business_account{id,username}"},
        timeout=30).json().get("data", [])
    linked = [p for p in pages if p.get("instagram_business_account")]
    if not linked:
        sys.exit(f"No Facebook Page with a linked Instagram professional account found. Pages: "
                 f"{[p.get('name') for p in pages]}")
    page = linked[0]
    ig = page["instagram_business_account"]
    print(f"Page: {page['name']}  Instagram: @{ig.get('username')} ({ig['id']})")
    test = requests.get(f"{GRAPH}/{ig['id']}", params={
        "access_token": page["access_token"],
        "fields": "business_discovery.username(faz3){username,followers_count,media_count,"
                  "media.limit(25){media_type,timestamp}}"}, timeout=30).json()
    if "error" in test:
        print(f"WARNING: @faz3 cannot be read through Business Discovery: {test['error'].get('message')}")
    else:
        bd = test["business_discovery"]
        vids = sum(1 for m in bd.get("media", {}).get("data", []) if m.get("media_type") == "VIDEO")
        print(f"@faz3 readable: {bd.get('followers_count')} followers, {bd.get('media_count')} posts, "
              f"{vids} videos among the latest 25")
    for name, value in (("IG_TOKEN", page["access_token"]), ("IG_USER_ID", ig["id"])):
        subprocess.run([str(GH), "secret", "set", name, "-R", REPO, "--body", value], check=True)
    print("Saved IG_TOKEN and IG_USER_ID to GitHub. Instagram is connected.")


if __name__ == "__main__":
    main()
