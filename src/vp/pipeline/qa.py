"""Automated QA gate + render_manifest.json (G13/G14/G11).

QA (a bad render is otherwise only caught by watching):
  - final video duration ~= mastered audio duration,
  - no long fully-black span, no long fully-silent span,
  - segment/alignment sanity spot-check.
Manifest: resolved model per purpose (G14), clip provenance + licence note
(G11), sfx events, qa results, rough cost line, upload status.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from ..audio_util import read_wav
from ..config import Config


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def _max_black_span(video: Path) -> float:
    r = subprocess.run(
        ["ffmpeg", "-i", str(video), "-vf", "blackdetect=d=0.5:pic_th=0.98",
         "-an", "-f", "null", "-"], capture_output=True, text=True
    )
    spans = [float(x.split(":")[1]) for line in r.stderr.splitlines()
             if "black_duration" in line
             for x in line.split() if x.startswith("black_duration")]
    return max(spans) if spans else 0.0


def run_qa(video: Path, master_audio: Path, n_segments: int,
           n_aligned: int) -> dict:
    vdur = _duration(video)
    adur = _duration(master_audio)
    checks = []

    drift = abs(vdur - adur)
    checks.append({"check": "duration_match", "pass": drift < 0.5,
                   "detail": f"video={vdur:.2f}s audio={adur:.2f}s drift={drift:.2f}s"})

    black = _max_black_span(video)
    checks.append({"check": "no_long_black", "pass": black < 2.0,
                   "detail": f"max_black_span={black:.2f}s"})

    a, sr = read_wav(master_audio) if master_audio.exists() else (np.zeros(1), 1)
    win = sr // 2
    silent = 0.0
    if len(a) > win:
        rms = np.sqrt(np.convolve(a**2, np.ones(win) / win, "same"))
        run = (rms < 1e-3).astype(int)
        # longest run of silence
        best = cur = 0
        for v in run:
            cur = cur + 1 if v else 0
            best = max(best, cur)
        silent = best / sr
    checks.append({"check": "no_long_silence", "pass": silent < 3.0,
                   "detail": f"max_silent_span={silent:.2f}s"})

    checks.append({"check": "alignment_coverage",
                   "pass": n_aligned == n_segments,
                   "detail": f"{n_aligned}/{n_segments} segments aligned"})

    return {"passed": all(c["pass"] for c in checks), "checks": checks}


def write_manifest(out_dir: Path, *, cfg: Config, doc, render_info: dict,
                   master_info: dict, clip_provenance: list, qa: dict,
                   upload: dict, script_topic: str,
                   runtime: dict | None = None) -> Path:
    n_clips = len(clip_provenance)
    manifest = {
        "title": doc.video_meta.get("title"),
        "topic": script_topic,
        "render": render_info,
        "audio": {k: v for k, v in master_info.items() if k != "sfx_events"},
        "sfx_events": master_info.get("sfx_events", []),
        "models_resolved": cfg.resolved_models_manifest(),  # G14 config intent
        "runtime": runtime or {},                            # what ACTUALLY ran
        "clip_provenance": clip_provenance,                  # G11 traceability
        "clip_licence": "Pexels (no attribution required); ids logged for provenance",
        "qa": qa,                                            # G13
        "upload": upload,                                    # G15
        "cost_estimate": {                                   # G14
            "note": "offline stubs — no API spend" if (runtime or {}).get(
                "any_stub", True
            ) else "live API run — set per-provider pricing to populate",
            "segments": len(doc.segments),
            "clips_sourced": n_clips,
        },
        "synthetic_media_disclosure": True,                  # G11
    }
    p = out_dir / "render_manifest.json"
    p.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return p
