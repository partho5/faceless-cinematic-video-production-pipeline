"""YouTube Data API v3 upload (planning/02 #13, G15).

CONTRACT: this step is best-effort and NON-BLOCKING. The render already
wrote final.mp4 + metadata locally (thumbnail is produced manually by
pasting `metadata.json::thumbnail_prompt` into Gemini's web UI) and the
run succeeds on that alone. This function NEVER raises and NEVER deletes
the local copy — it returns a status dict that the manifest records.

OAuth 2.0 (not an API key). Until the OAuth app passes Google verification,
every API upload is forced `privacyStatus=private` (G15) regardless of
request; the user flips to public/schedules in YouTube Studio.

Token lifecycle
---------------
- Access tokens  expire in ~1 hour  → refreshed **automatically** by
  google-auth without any user interaction.
- Refresh tokens expire in  ~7 days  when the OAuth consent screen is in
  "Testing" mode (Google policy for unverified apps). To avoid repeated
  manual re-authorizations either:

    A. Publish the consent screen in Google Cloud Console
       (OAuth consent screen → Publishing status → "Publish App").
       Once published, refresh tokens never expire from timeout.

    B. Keep the app in Testing: the pipeline will auto-relaunch the
       browser consent whenever the token is stale and write the new
       refresh token back to .yt_token.json and .env automatically.

Token storage
-------------
Credentials are persisted to `.yt_token.json` in the project root
(gitignored).  On every upload:
  1. Load .yt_token.json if it exists (already has a valid refresh token).
  2. If the access token is expired → refresh silently (no browser needed).
  3. If the refresh token itself is expired/revoked (invalid_grant) →
     launch a local browser consent flow automatically, save the new
     token to .yt_token.json AND update YT_REFRESH_TOKEN in .env.
  4. Upload the video.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import Config, ROOT

FORCED_PRIVACY = "private"   # G15: cannot be public via API until verified
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]
TOKEN_FILE = ROOT / ".yt_token.json"  # gitignored, persists refresh token


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

def _save_token(creds) -> None:
    """Persist credentials to TOKEN_FILE so the next run skips the browser."""
    data = {
        "token":         creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri":     creds.token_uri,
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "scopes":        list(creds.scopes or SCOPES),
    }
    TOKEN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _update_env_refresh_token(new_token: str) -> None:
    """Write the new refresh token back into .env so it survives a reboot."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    text = env_path.read_text(encoding="utf-8")
    pattern = r"^(YT_REFRESH_TOKEN\s*=\s*)(.*)$"
    replacement = f"YT_REFRESH_TOKEN={new_token}"
    new_text, n = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if n:
        env_path.write_text(new_text, encoding="utf-8")
    else:
        # Key not yet in .env — append it
        env_path.write_text(
            text.rstrip("\n") + f"\nYT_REFRESH_TOKEN={new_token}\n",
            encoding="utf-8",
        )


def _load_credentials(cid: str, secret: str, refresh: str):
    """Return a valid Credentials object, refreshing or re-authorizing as needed.

    Returns (creds, reauthed: bool).  reauthed=True means the browser was
    launched and a brand-new refresh token was obtained and saved.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google.auth.exceptions import TransportError

    # 1. Prefer the token cache (has the latest refresh_token after any
    #    previous auto-reauth).
    creds = None
    if TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            creds = Credentials(
                token=data.get("token"),
                refresh_token=data.get("refresh_token") or refresh,
                token_uri=data.get("token_uri",
                                   "https://oauth2.googleapis.com/token"),
                client_id=data.get("client_id", cid),
                client_secret=data.get("client_secret", secret),
                scopes=data.get("scopes", SCOPES),
            )
        except Exception:
            creds = None  # corrupted cache → fall through

    # 2. Fall back to the .env refresh token
    if creds is None:
        creds = Credentials(
            token=None,
            refresh_token=refresh,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=cid,
            client_secret=secret,
            scopes=SCOPES,
        )

    # 3. Try to refresh the access token silently.
    #    If the refresh token itself is expired → catch invalid_grant and
    #    fall through to browser re-auth.
    needs_reauth = False
    
    # Check if we have all requested scopes in cached creds
    has_all_scopes = creds and all(s in (creds.scopes or []) for s in SCOPES)
    if not has_all_scopes:
        needs_reauth = True

    if not needs_reauth and not creds.valid:
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds, False
        except Exception as e:
            if "invalid_grant" not in str(e):
                raise  # unexpected error — let the caller handle it
            needs_reauth = True

    if not needs_reauth and creds.valid:
        _save_token(creds)
        return creds, False

    # 4. Refresh token is expired/revoked → launch browser consent.
    print(
        "\n[youtube] YT_REFRESH_TOKEN expired (Google 7-day Testing limit).\n"
        "         Opening browser for a one-time re-authorization…\n"
        "         Approve the consent page; the new token will be saved\n"
        "         automatically to .yt_token.json and .env.\n",
        flush=True,
    )
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_config(
        {"installed": {
            "client_id":      cid,
            "client_secret":  secret,
            "auth_uri":       "https://accounts.google.com/o/oauth2/auth",
            "token_uri":      "https://oauth2.googleapis.com/token",
            "redirect_uris":  ["http://localhost"],
        }},
        scopes=SCOPES,
    )
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    if creds.refresh_token:
        _update_env_refresh_token(creds.refresh_token)
        print(
            f"[youtube] New refresh token saved to .yt_token.json and .env.\n"
            f"          YT_REFRESH_TOKEN={creds.refresh_token[:30]}…\n",
            flush=True,
        )
    return creds, True


# ---------------------------------------------------------------------------
# Public upload entry point
# ---------------------------------------------------------------------------

def upload(video: Path, metadata: dict, cfg: Config) -> dict:
    cid     = cfg.env("YT_CLIENT_ID")
    secret  = cfg.env("YT_CLIENT_SECRET")
    refresh = cfg.env("YT_REFRESH_TOKEN")
    if not (cid and secret and refresh):
        return {
            "status": "skipped",
            "reason": (
                "YT OAuth creds absent (YT_CLIENT_ID / YT_CLIENT_SECRET / "
                "YT_REFRESH_TOKEN) — local copy kept. "
                "Run `python scripts/youtube_oauth_setup.py` to set up."
            ),
            "privacy": None,
        }

    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from datetime import datetime, timezone

        # Load schedule profile settings
        profile_path = ROOT / ".render_profile.json"
        schedule_enabled = False
        schedule_slots = []
        if profile_path.exists():
            try:
                prof = json.loads(profile_path.read_text(encoding="utf-8"))
                schedule_enabled = prof.get("schedule_enabled", False)
                schedule_slots = prof.get("schedule_slots", [])
            except Exception:
                pass

        publish_at_str = None
        if schedule_enabled:
            if not schedule_slots:
                print("[vp] [API_ERROR:YT_SCHEDULE_WARNING] No active scheduling slots configured.", flush=True)
            else:
                try:
                    creds, reauthed = _load_credentials(cid, secret, refresh)
                    yt = build("youtube", "v3", credentials=creds)
                    
                    # Fetch last upload/schedule time
                    ch_resp = yt.channels().list(mine=True, part="contentDetails").execute()
                    uploads_playlist_id = ch_resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

                    playlist_resp = yt.playlistItems().list(
                        playlistId=uploads_playlist_id,
                        part="contentDetails",
                        maxResults=10
                    ).execute()

                    video_ids = [item["contentDetails"]["videoId"] for item in playlist_resp.get("items", [])]
                    
                    ref_time = None
                    if video_ids:
                        videos_resp = yt.videos().list(
                            id=",".join(video_ids),
                            part="snippet,status"
                        ).execute()
                        
                        times = []
                        for item in videos_resp.get("items", []):
                            publish_at = item.get("status", {}).get("publishAt")
                            if publish_at:
                                times.append(publish_at)
                            else:
                                published_at = item.get("snippet", {}).get("publishedAt")
                                if published_at:
                                    times.append(published_at)
                        
                        if times:
                            parsed_times = []
                            for t in times:
                                try:
                                    cleaned = t.replace("Z", "+00:00")
                                    parsed_times.append(datetime.fromisoformat(cleaned))
                                except Exception:
                                    pass
                            if parsed_times:
                                ref_time = max(parsed_times)
                    
                    now_utc = datetime.now(timezone.utc)
                    if not ref_time or ref_time < now_utc:
                        # ref_time is the last video already on the channel,
                        # which can be arbitrarily old (no uploads for a
                        # week+). Never compute a slot from a reference
                        # earlier than "now" — a past publishAt makes
                        # YouTube publish the video immediately instead of
                        # scheduling it.
                        ref_time = now_utc

                    from vp.schedule import calculate_next_slot
                    next_slot = calculate_next_slot(ref_time, schedule_slots)
                    publish_at_str = next_slot.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception as e:
                    print(f"[vp] [API_ERROR:YT_SCHEDULE_WARNING] Failed to fetch last scheduled time: {e}", flush=True)

        creds, reauthed = _load_credentials(cid, secret, refresh)
        yt = build("youtube", "v3", credentials=creds)

        # Prefer the publish-ready SEO description (chapters + music credit
        # + disclosure + hashtags assembled by MetadataStage). Fall back to
        # the raw `description` for older metadata.json files.
        desc = (metadata.get("description", ""))[:4900]
        lang = metadata.get("default_language") or "en"
        snippet = {
            "title":                metadata.get("title", "")[:100],
            "description":          desc,
            "tags":                 metadata.get("tags", [])[:30],
            "categoryId":           metadata.get("category_id", "27"),
            "defaultLanguage":      lang,
            "defaultAudioLanguage": metadata.get("default_audio_language") or lang,
        }
        status_body = {
            "privacyStatus":          FORCED_PRIVACY,
            "selfDeclaredMadeForKids": bool(
                metadata.get("made_for_kids", False)),
            # synthetic/altered-media disclosure (G11); harmless if the
            # account/API rev ignores the hint.
            "containsSyntheticMedia": True,
        }
        if publish_at_str:
            status_body["publishAt"] = publish_at_str

        body = {
            "snippet": snippet,
            "status": status_body,
        }
        req = yt.videos().insert(
            part="snippet,status",
            body=body,
            media_body=MediaFileUpload(str(video), chunksize=-1, resumable=True),
        )
        resp = req.execute()
        vid = resp.get("id")

        # Thumbnail upload intentionally skipped: thumbnails are now produced
        # manually from `metadata.json::thumbnail_prompt` via Gemini's web UI,
        # so there is no local image file to push. Set it in YouTube Studio.
        return {
            "status":    "uploaded" if not publish_at_str else "scheduled",
            "video_id":  vid,
            "privacy":   FORCED_PRIVACY,
            "url":       f"https://youtu.be/{vid}" if vid else None,
            "reauthed":  reauthed,
            "publish_at": publish_at_str,
        }

    except Exception as e:  # never block the run
        reason = str(e)
        if "invalid_grant" in reason:
            reason = (
                "invalid_grant — could not refresh the OAuth token automatically "
                "(browser may be unavailable in this environment). "
                "Run `python scripts/youtube_oauth_setup.py` manually, then retry."
            )
        return {"status": "failed", "reason": reason[:400], "privacy": None}

