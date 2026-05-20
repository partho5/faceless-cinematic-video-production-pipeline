"""YouTube Data API v3 upload (planning/02 #13, G15).

CONTRACT: this step is best-effort and NON-BLOCKING. The render already
wrote final.mp4 + thumbnail + metadata locally and the run succeeds on that
alone. This function NEVER raises and NEVER deletes the local copy — it
returns a status dict that the manifest records.

OAuth 2.0 (not an API key). Until the OAuth app passes Google verification,
every API upload is forced `privacyStatus=private` (G15) regardless of
request; the user flips to public/schedules in YouTube Studio.
"""
from __future__ import annotations

from pathlib import Path

from ..config import Config

FORCED_PRIVACY = "private"  # G15: cannot be public via API until verified


def upload(video: Path, metadata: dict, cfg: Config) -> dict:
    cid = cfg.env("YT_CLIENT_ID")
    secret = cfg.env("YT_CLIENT_SECRET")
    refresh = cfg.env("YT_REFRESH_TOKEN")
    if not (cid and secret and refresh):
        return {"status": "skipped",
                "reason": "YT OAuth creds absent (YT_CLIENT_ID/SECRET/"
                          "REFRESH_TOKEN) — local copy kept",
                "privacy": None}
    try:
        from google.oauth2.credentials import Credentials  # lazy
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        creds = Credentials(
            None, refresh_token=refresh, client_id=cid, client_secret=secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/youtube.upload"],
        )
        yt = build("youtube", "v3", credentials=creds)
        # Prefer the publish-ready SEO description (chapters + music credit
        # + disclosure + hashtags assembled by MetadataStage). Fall back to
        # the raw `description` for older metadata.json files.
        desc = (metadata.get("description_seo")
                or metadata.get("description", ""))[:4900]
        lang = metadata.get("default_language") or "en"
        snippet = {
            "title": metadata.get("title", "")[:100],
            "description": desc,
            "tags": metadata.get("tags", [])[:30],
            "categoryId": metadata.get("category_id", "27"),
            "defaultLanguage": lang,
            "defaultAudioLanguage": (
                metadata.get("default_audio_language") or lang),
        }
        body = {
            "snippet": snippet,
            "status": {
                "privacyStatus": FORCED_PRIVACY,
                "selfDeclaredMadeForKids": bool(
                    metadata.get("made_for_kids", False)),
                # synthetic/altered-media disclosure (G11); harmless if the
                # account/API rev ignores the hint.
                "containsSyntheticMedia": True,
            },
        }
        req = yt.videos().insert(
            part="snippet,status", body=body,
            media_body=MediaFileUpload(str(video), chunksize=-1, resumable=True),
        )
        resp = req.execute()
        vid = resp.get("id")
        thumb = metadata.get("thumbnail")
        if vid and thumb and Path(thumb).exists():
            try:
                yt.thumbnails().set(
                    videoId=vid, media_body=MediaFileUpload(str(thumb))
                ).execute()
            except Exception:
                pass
        return {"status": "uploaded", "video_id": vid,
                "privacy": FORCED_PRIVACY,
                "url": f"https://youtu.be/{vid}" if vid else None}
    except Exception as e:  # never block the run
        return {"status": "failed", "reason": str(e)[:300], "privacy": None}
