"""One-time YouTube OAuth consent -> prints YT_REFRESH_TOKEN for .env (G15).

Setup (once, in Google Cloud Console):
  1. Create a project; enable "YouTube Data API v3".
  2. OAuth consent screen: External, add yourself as a test user.
  3. Credentials -> OAuth client ID -> type "Desktop app".
  4. Put the client id/secret in .env as YT_CLIENT_ID / YT_CLIENT_SECRET.
  5. Run:  python scripts/youtube_oauth_setup.py
  6. Approve in the browser; paste the printed refresh token into .env as
     YT_REFRESH_TOKEN.

Note: until the OAuth app is verified by Google, API uploads are forced
`private` (G15) — flip to public/schedule in YouTube Studio.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vp.config import get_config  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


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
            "client_id": cid, "client_secret": secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }},
        scopes=SCOPES,
    )
    creds = flow.run_local_server(port=0)
    print("\n=== Add this line to .env ===")
    print(f"YT_REFRESH_TOKEN={creds.refresh_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
