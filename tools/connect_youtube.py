"""Authorise the uploader on your channel and store the keys as GitHub secrets in one go (nothing is printed).

    python tools/connect_youtube.py <client_secret.json> <owner/repo> <path-to-gh>
"""
import json
import subprocess
import sys

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]


def main() -> None:
    client_file, repo, gh = sys.argv[1:4]
    flow = InstalledAppFlow.from_client_secrets_file(client_file, SCOPES)
    print("A browser tab is opening: choose your account, then The Luxury Lane, then Allow.", flush=True)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline", open_browser=True)
    if not creds.refresh_token:
        sys.exit("Google did not return a refresh token; remove the app at https://myaccount.google.com/permissions "
                 "and run again.")
    channel = build("youtube", "v3", credentials=creds, cache_discovery=False).channels().list(
        part="snippet", mine=True).execute()["items"][0]["snippet"]["title"]
    app = json.load(open(client_file, encoding="utf-8"))
    app = app.get("installed") or app.get("web")
    for name, value in (("YT_CLIENT_ID", app["client_id"]), ("YT_CLIENT_SECRET", app["client_secret"]),
                        ("YT_REFRESH_TOKEN", creds.refresh_token)):
        subprocess.run([gh, "secret", "set", name, "-R", repo, "--body", value], check=True,
                       capture_output=True)
    print(f"CONNECTED channel: {channel} — 3 secrets saved to {repo}", flush=True)


if __name__ == "__main__":
    main()
