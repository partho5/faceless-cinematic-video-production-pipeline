"""End-to-end orchestrator (planning/07 §F build trigger).

topic -> script (review gate) -> control JSON -> TTS -> align -> reflow ->
master -> render (10 variation layers) -> thumbnail/metadata -> QA + manifest
-> save output/<slug>/  (local save IS the deliverable) -> best-effort
non-blocking YouTube upload.

Usage:
  python -m vp.run "The 7 Signs Someone Is Quietly Manipulating You" \
      --approve [--preset preview|final] [--segments N] [--no-upload]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import OUTPUT, get_config
from .pipeline.align import align_segment
from .pipeline.master import build_master
from .pipeline.metadata import MetadataStage
from .pipeline.pexels import ClipProvider
from .pipeline.qa import run_qa, write_manifest
from .pipeline.render import RenderEngine
from .pipeline.script_gen import ScriptStage, SegmentStage, review_gate, slugify
from .pipeline.timeline import reflow
from .pipeline.youtube import upload as yt_upload
from .schema.validator import validate


def _log(msg: str) -> None:
    print(f"[vp] {msg}", flush=True)


def run(topic: str, *, preset: str = "preview", approve: bool = False,
        segments: int | None = None, do_upload: bool = True,
        tts_scene: str | None = None, tts_context: str | None = None) -> dict:
    cfg = get_config()
    for w in cfg.validate():
        _log(f"warn: {w}")

    slug = slugify(topic)
    out = OUTPUT / slug
    (out / "audio").mkdir(parents=True, exist_ok=True)
    _log(f"output dir: {out}")

    # 1. Stage 1 + review gate
    script_path = ScriptStage(cfg).generate(topic, out)
    _log(f"stage1 script -> {script_path.name}")
    if not review_gate(script_path, auto_approve=approve):
        _log("review gate: NOT approved (create script.APPROVED or pass "
             "--approve). Stopping before TTS/render spend.")
        return {"status": "awaiting_review", "script": str(script_path)}

    # 2. Stage 2 -> validated control doc
    doc = SegmentStage(cfg).generate(script_path, out)
    if segments:
        doc.segments = doc.segments[:segments]
    res = validate(doc)
    _log(f"stage2 doc: {len(doc.segments)} segments, validate ok={res.ok} "
         f"({len(res.warnings)} warns)")
    if not res.ok:
        _log("FATAL: control doc failed validation:\n" + str(res))
        return {"status": "invalid_document"}

    # 3. VOICE (continuous per-chapter read, sliced per segment) -> align
    from .pipeline.voice import VoiceStage

    _vs = VoiceStage(cfg)
    if tts_scene:
        _vs.scene = tts_scene
    if tts_context:
        _vs.context = tts_context
    vr = _vs.synthesize(doc, out / "audio")
    _log(f"voice: {vr.chapters} chapters -> {vr.segments} segment slices "
         f"(keys={vr.key_count}, offline={vr.offline})")
    aligns = {}
    align_offline = cfg.model("forced_alignment").offline or vr.offline
    for s in doc.segments:
        aligns[s.id] = align_segment(
            s.id, s.text_overlay, Path(s.audio_path),
            pre_ms=s.pre_silence_ms, post_ms=s.post_silence_ms,
            offline=align_offline,
        )
    tl = reflow(doc.segments)
    _log(f"timeline reflowed (G1): {tl.total_duration:.2f}s")

    # 6. master audio
    master = build_master(doc.segments, out / "master.wav", doc.video_meta)
    _log(f"master: {master['duration']:.2f}s, {len(master['sfx_events'])} sfx")

    # 7. render (all 10 layers) on the mastered track
    cp = ClipProvider(cfg)
    eng = RenderEngine(cfg)
    eng.visual_source = cp.visual_source
    from .fx.ambient import FilmAmbience, KenBurns
    from .fx.camera import CameraMotion, CutRhythm
    from .fx.color import ColorGrade, FilmFX
    from .fx.text import TextRenderer
    for x in (KenBurns(), CameraMotion(), ColorGrade(), TextRenderer(),
              FilmFX(), FilmAmbience(), CutRhythm()):
        eng.register_xform(x)
    info = eng.render(doc, aligns, out / "final.mp4", preset=preset,
                      work_dir=out / "_work",
                      audio_track=out / "master.wav")
    _log(f"render -> final.mp4 {info['resolution']} {info['duration']:.2f}s")

    # 8. metadata + thumbnail
    meta = MetadataStage(cfg).run(out / "final.mp4", doc,
                                  script_path.read_text(encoding="utf-8"), out)
    _log(f"metadata: thumbnail.jpg + {len(meta['tags'])} tags")

    # 9. QA + manifest
    qa = run_qa(out / "final.mp4", out / "master.wav",
                len(doc.segments), len(aligns))
    _log(f"QA passed={qa['passed']}")
    for c in qa["checks"]:
        _log(f"  [{'OK' if c['pass'] else 'XX'}] {c['check']}: {c['detail']}")

    # 10. best-effort, non-blocking upload (local copy already saved)
    up = {"status": "disabled"}
    if do_upload:
        up = yt_upload(out / "final.mp4", meta, cfg)
        _log(f"upload: {up['status']} ({up.get('reason', up.get('url',''))})")

    runtime = {
        "voice": "piper-offline-stub" if vr.offline
        else f"gemini:{cfg.model('tts_audio').model}/{VoiceStage(cfg).voice}",
        "voice_continuous_per_chapter": not vr.offline,
        "tts_keys": vr.key_count,
        "alignment_method": next(iter(aligns.values())).method,
        "script_stage_offline": ScriptStage(cfg).spec.offline,
        "segmentation_offline": SegmentStage(cfg).spec.offline,
        # true if ANY stage fell back to a stub (drives the cost note, G14)
        "any_stub": vr.offline
        or next(iter(aligns.values())).method == "proportional",
    }
    write_manifest(out, cfg=cfg, doc=doc, render_info=info,
                   master_info=master, clip_provenance=cp.manifest,
                   qa=qa, upload=up, script_topic=topic, runtime=runtime)
    _log(f"manifest -> render_manifest.json")
    _log(f"DONE — deliverable at {out}/final.mp4")
    return {"status": "ok", "output": str(out), "qa": qa["passed"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="vp.run")
    ap.add_argument("topic")
    ap.add_argument("--preset", choices=["preview", "final"], default="preview")
    ap.add_argument("--approve", action="store_true",
                    help="auto-approve the Stage-1 script review gate")
    ap.add_argument("--segments", type=int, default=None,
                    help="limit number of segments (quick runs)")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--tts-scene", default=None,
                    help="override TTS scene framing (per-niche steering)")
    ap.add_argument("--tts-context", default=None,
                    help="override TTS context framing (per-niche steering)")
    a = ap.parse_args(argv)
    r = run(a.topic, preset=a.preset, approve=a.approve,
            segments=a.segments, do_upload=not a.no_upload,
            tts_scene=a.tts_scene, tts_context=a.tts_context)
    return 0 if r["status"] in ("ok", "awaiting_review") else 1


if __name__ == "__main__":
    sys.exit(main())
