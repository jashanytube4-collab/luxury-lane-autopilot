"""YouTube Data API: scheduled uploads, plus a read-back of recent uploads so nothing is ever posted twice."""
from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import httplib2
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

log = logging.getLogger("lane.youtube")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
RETRY_STATUS = {500, 502, 503, 504}


class QuotaError(Exception):
    pass


class AuthError(Exception):
    pass


def key_tag(key: str) -> str:
    """Short hidden tag that ties a video back to what it was made from (stable across runs)."""
    import hashlib
    return "ll" + hashlib.md5(key.encode("utf-8")).hexdigest()[:10]


class YouTube:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str) -> None:
        if not (client_id and client_secret and refresh_token):
            raise AuthError("YouTube credentials are not set (YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN)")
        creds = Credentials(None, refresh_token=refresh_token, client_id=client_id, client_secret=client_secret,
                            token_uri="https://oauth2.googleapis.com/token", scopes=SCOPES)
        self.api = build("youtube", "v3", credentials=creds, cache_discovery=False)
        try:
            self.channel = self.api.channels().list(part="snippet,contentDetails", mine=True).execute()["items"][0]
        except RefreshError as e:
            raise AuthError(f"YouTube refresh token rejected — run tools/youtube_auth.py again ({e})") from e

    @property
    def channel_title(self) -> str:
        return self.channel["snippet"]["title"]

    def recent_tags(self, pages: int = 2) -> set[str]:
        """Source tags of the latest uploads (scheduled ones included)."""
        playlist = self.channel["contentDetails"]["relatedPlaylists"]["uploads"]
        tags, token = set(), None
        for _ in range(pages):
            resp = self.api.playlistItems().list(part="contentDetails", playlistId=playlist, maxResults=50,
                                                 pageToken=token).execute()
            ids = [it["contentDetails"]["videoId"] for it in resp.get("items", [])]
            if ids:
                for v in self.api.videos().list(part="snippet", id=",".join(ids)).execute().get("items", []):
                    tags.update(t for t in v["snippet"].get("tags", []) if t.startswith("ll") and len(t) == 12)
            token = resp.get("nextPageToken")
            if not token:
                break
        return tags

    def upload(self, path: Path, *, title: str, description: str, tags: list[str], category_id: str,
               publish_at: datetime | None) -> str:
        status = {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "embeddable": True}
        if publish_at is not None:
            status["publishAt"] = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        body = {
            "snippet": {"title": title[:100], "description": description[:4900], "tags": tags,
                        "categoryId": category_id, "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": status,
        }
        media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        request = self.api.videos().insert(part="snippet,status", body=body, media_body=media)
        response, attempt = None, 0
        while response is None:
            try:
                _, response = request.next_chunk()
            except HttpError as e:
                reason = _reason(e)
                if reason in ("quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
                    raise QuotaError(reason) from e
                if e.resp.status not in RETRY_STATUS or attempt >= 6:
                    raise
                attempt += 1
                time.sleep(min(64, 2 ** attempt) + random.random())
            except (httplib2.HttpLib2Error, ConnectionError, TimeoutError, OSError) as e:
                if attempt >= 6:
                    raise
                attempt += 1
                log.warning("upload hiccup (%s), retrying", e)
                time.sleep(min(64, 2 ** attempt) + random.random())
        return response["id"]


    def set_thumbnail(self, video_id: str, image: Path) -> bool:
        """Custom thumbnail (needs a phone-verified channel; returns False instead of failing the upload)."""
        try:
            self.api.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(image), mimetype="image/jpeg")
                                      ).execute()
            return True
        except HttpError as e:
            log.warning("thumbnail not set (%s) — verify the channel at youtube.com/verify to enable it", _reason(e))
            return False


def _reason(e: HttpError) -> str:
    try:
        return json.loads(e.content)["error"]["errors"][0]["reason"]
    except Exception:
        return ""
