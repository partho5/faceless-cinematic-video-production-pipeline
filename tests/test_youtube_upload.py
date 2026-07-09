"""Manual / integration test: YouTube upload for a finished video.

Usage (from project root):
    python tests/test_youtube_upload.py

Or via pytest (skipped automatically when YT creds are absent):
    pytest tests/test_youtube_upload.py -v

What it tests
-------------
1. YT OAuth credentials are present in .env
2. The refresh token is still valid (not expired / revoked)
3. The full upload flow succeeds for the given video + metadata
4. The returned status dict is well-formed

If the refresh token is invalid you will see:
    FAIL  invalid_grant — YT_REFRESH_TOKEN is expired or revoked.
    FIX   python scripts/youtube_oauth_setup.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Allow running as a plain script from any directory
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

VIDEO = Path(
    "/home/haku/Videos/Faceless Studio/"
    "how-to-reduce-belly-fat/"
    "How to Lose Belly Fat Fast Start With Breakfast.mp4"
)
METADATA = VIDEO.parent / "metadata.json"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load() -> tuple:
    from vp.config import Config
    cfg = Config.load()
    meta = json.loads(METADATA.read_text(encoding="utf-8"))
    return cfg, meta


def _creds_present(cfg) -> bool:
    return all(cfg.env(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET",
                                     "YT_REFRESH_TOKEN"))


# ---------------------------------------------------------------------------
# pytest tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cfg_and_meta():
    return _load()


def test_video_file_exists():
    assert VIDEO.exists(), f"Video file not found: {VIDEO}"


def test_metadata_file_exists():
    assert METADATA.exists(), f"metadata.json not found: {METADATA}"


def test_metadata_has_required_fields():
    meta = json.loads(METADATA.read_text(encoding="utf-8"))
    for field in ("title", "description", "tags", "category_id"):
        assert field in meta, f"metadata.json missing field: {field}"
    assert len(meta["title"]) <= 100, "title exceeds YouTube 100-char limit"
    assert len(meta["description"]) <= 5000, "description exceeds 5000-char limit"
    assert len(meta["tags"]) <= 500, "tags list too long"


@pytest.mark.skipif(
    not VIDEO.exists(),
    reason="video file not present — run the pipeline first",
)
def test_upload_succeeds(cfg_and_meta):
    cfg, meta = cfg_and_meta
    if not _creds_present(cfg):
        pytest.skip("YT OAuth creds absent in .env — skipping upload test")

    from vp.pipeline.youtube import upload
    result = upload(VIDEO, meta, cfg)

    assert result["status"] in ("uploaded", "skipped"), (
        f"Upload FAILED — {result.get('reason', 'unknown error')}\n\n"
        f"If the error mentions 'invalid_grant':\n"
        f"  → Run: python scripts/youtube_oauth_setup.py\n"
        f"  → Then update YT_REFRESH_TOKEN in .env"
    )

    if result["status"] == "uploaded":
        assert result.get("video_id"), "No video_id in upload result"
        print(f"\n✓ Uploaded: {result.get('url')}")


# ---------------------------------------------------------------------------
# standalone runner
# ---------------------------------------------------------------------------

def _run_standalone() -> int:
    print("=" * 60)
    print("YouTube Upload Test")
    print("=" * 60)

    # 1. file checks
    ok = True
    for label, path in [("Video", VIDEO), ("Metadata", METADATA)]:
        exists = path.exists()
        print(f"  [{' OK' if exists else 'FAIL'}] {label}: {path.name}")
        if not exists:
            ok = False
    if not ok:
        print("\nERROR: fix missing files above, then retry.")
        return 1

    # 2. metadata sanity
    meta = json.loads(METADATA.read_text(encoding="utf-8"))
    print(f"\n  Title     : {meta.get('title', '')!r}")
    print(f"  Tags      : {len(meta.get('tags', []))} tags")
    print(f"  Category  : {meta.get('category_id')} ({meta.get('category')})")
    print(f"  Privacy   : {meta.get('privacy_status')}")

    # 3. credentials check
    cfg, _ = _load()
    cid = cfg.env("YT_CLIENT_ID")
    sec = cfg.env("YT_CLIENT_SECRET")
    tok = cfg.env("YT_REFRESH_TOKEN")
    print(f"\n  YT_CLIENT_ID     : {'set (' + cid[:20] + '...)' if cid else 'MISSING'}")
    print(f"  YT_CLIENT_SECRET : {'set' if sec else 'MISSING'}")
    print(f"  YT_REFRESH_TOKEN : {'set (' + tok[:20] + '...)' if tok else 'MISSING'}")

    if not (cid and sec and tok):
        print("\nERROR: YT OAuth creds missing in .env")
        print("  Run: python scripts/youtube_oauth_setup.py")
        return 1

    # 4. upload
    from vp.pipeline.youtube import upload
    print("\nRunning upload...")
    result = upload(VIDEO, meta, cfg)
    status = result["status"]
    print(f"\n  Status : {status}")

    if status == "uploaded":
        print(f"  URL    : {result.get('url')}")
        print(f"  ID     : {result.get('video_id')}")
        print("\n✓ Upload succeeded.")
        return 0
    elif status == "skipped":
        print(f"  Reason : {result.get('reason')}")
        print("\n⚠ Upload skipped (no creds or disabled).")
        return 0
    else:
        print(f"  Reason : {result.get('reason')}")
        if "invalid_grant" in (result.get("reason") or ""):
            print("\n" + "=" * 60)
            print("FIX: YT_REFRESH_TOKEN is expired or revoked.")
            print("  1. Run: python scripts/youtube_oauth_setup.py")
            print("  2. A browser window will open — approve the OAuth consent.")
            print("  3. Copy the printed YT_REFRESH_TOKEN into .env")
            print("  4. Re-run this test to confirm the fix.")
            print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(_run_standalone())
