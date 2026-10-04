"""One-time: authorize the uploader on your YouTube channel and print the three GitHub secrets.

    python tools/youtube_auth.py path/to/client_secret.json

A browser opens; sign in with the Google account that owns The Luxury Lane and pick that channel.
"""
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        sys.exit("Google did not return a refresh token. Remove the app's access at "
                 "https://myaccount.google.com/permissions and run this again.")
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
    app = data.get("installed") or data.get("web")
    print("\nAdd these as GitHub repository secrets (Settings -> Secrets and variables -> Actions):\n")
    print(f"YT_CLIENT_ID      = {app['client_id']}")
    print(f"YT_CLIENT_SECRET  = {app['client_secret']}")
    print(f"YT_REFRESH_TOKEN  = {creds.refresh_token}")


if __name__ == "__main__":
    main()
