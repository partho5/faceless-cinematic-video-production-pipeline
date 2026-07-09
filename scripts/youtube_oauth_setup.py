"""One-time YouTube OAuth consent → saves token to .yt_token.json + prints
YT_REFRESH_TOKEN for .env (G15).

After the first run, the pipeline auto-refreshes the access token silently.
If the refresh token itself expires (Google 7-day Testing limit), the
upload step will relaunch this browser flow automatically.

Setup (once, in Google Cloud Console):
  1. Create a project; enable "YouTube Data API v3".
  2. OAuth consent screen: External, add yourself as a test user.
  3. Credentials → OAuth client ID → type "Desktop app".
  4. Put the client id/secret in .env as YT_CLIENT_ID / YT_CLIENT_SECRET.
  5. Run:  python scripts/youtube_oauth_setup.py
  6. The browser opens → approve the consent.
  7. The token is saved automatically to .yt_token.json (gitignored) and
     printed so you can also paste it into .env as YT_REFRESH_TOKEN.

To stop the 7-day expiry permanently:
  Google Cloud Console → OAuth consent screen → Publishing status
  → click "Publish App".  Once published, refresh tokens never time out.

Note: until the OAuth app is verified by Google, API uploads are forced
`private` (G15) — flip to public/schedule in YouTube Studio.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vp.config import get_config          # noqa: E402
from vp.pipeline.youtube import (         # noqa: E402
    SCOPES, TOKEN_FILE, _save_token, _update_env_refresh_token,
)


def main() -> int:
    cfg = get_config()
    cid, secret = cfg.env("YT_CLIENT_ID"), cfg.env("YT_CLIENT_SECRET")
    if not (cid and secret):
        print("ERROR: set YT_CLIENT_ID and YT_CLIENT_SECRET in .env first.")
        return 1
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Install: pip install google-auth-oauthlib google-api-python-client")
        return 1

    flow = InstalledAppFlow.from_client_config(
        {"installed": {
            "client_id":     cid,
            "client_secret": secret,
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }},
        scopes=SCOPES,
    )
    creds = flow.run_local_server(port=0)

    # Persist to .yt_token.json (auto-loaded by youtube.upload on every run)
    _save_token(creds)
    print(f"\n✓ Token saved to {TOKEN_FILE}")

    # Also write back into .env for portability / backup
    if creds.refresh_token:
        _update_env_refresh_token(creds.refresh_token)
        print("✓ YT_REFRESH_TOKEN updated in .env")

    print("\n=== Refresh token (also in .env) ===")
    print(f"YT_REFRESH_TOKEN={creds.refresh_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
