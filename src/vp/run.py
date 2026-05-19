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
import json
import os
import subprocess
import sys
from pathlib import Path


def _ensure_venv_python() -> None:
    """If a user runs `python -m vp.run ...` with a non-venv Python while
    .venv exists, re-exec under the venv. Catches the common 'I have a
    system Python on PATH that imports vp from src/ but is missing the
    pinned deps (google-genai, modern anthropic)' failure mode.

    Checked via sys.prefix (the venv root, not a symlink) — comparing
    resolved sys.executable would falsely match on Linux, where the
    venv's python3 is a symlink to the system interpreter."""
    here = Path(__file__).resolve()
    root = here.parents[2]  # src/vp/run.py -> repo root
    venv_dir = root / ".venv"
    venv_py = venv_dir / ("Scripts/python.exe" if os.name == "nt"
                          else "bin/python3")
    if not venv_py.exists():
        return
    try:
        if Path(sys.prefix).resolve() == venv_dir.resolve():
            return
    except Exception:
        return
    print(f"[vp] re-launching under venv: {venv_py}", flush=True)
    raise SystemExit(subprocess.call(
        [str(venv_py), "-m", "vp.run", *sys.argv[1:]]))


_ensure_venv_python()


from .config import OUTPUT, get_config
from .pipeline.align import align_segment
from .pipeline.master import build_master
from .pipeline.metadata import MetadataStage
from .pipeline.pexels import ClipProvider
from .pipeline.qa import run_qa, write_manifest
from .pipeline.render import RenderEngine
from .pipeline.script_gen import ScriptStage, SegmentStage, review_gate, slugify
from .schema.model import ControlDocument
from .pipeline.timeline import reflow
from .pipeline.youtube import upload as yt_upload
from .schema.validator import validate


def _log(msg: str) -> None:
    print(f"[vp] {msg}", flush=True)


def _embed_mp4_metadata(
    path: Path, *,
    title: str | None = None,
    artist: str | None = None,
    copyright: str | None = None,
    encoder: str | None = None,
) -> None:
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    tmp = path.with_name(path.stem + "._meta_tmp.mp4")
    cmd = ["ffmpeg", "-y", "-i", str(path), "-c", "copy",
           "-metadata", f"creation_time={now}"]
    if title:     cmd += ["-metadata", f"title={title}"]
    if artist:    cmd += ["-metadata", f"artist={artist}"]
    if copyright: cmd += ["-metadata", f"copyright={copyright}"]
    if encoder:   cmd += ["-metadata", f"encoder={encoder}"]
    cmd.append(str(tmp))
    subprocess.run(cmd, check=True, capture_output=True)
    os.replace(str(tmp), str(path))


def run(topic: str, *, preset: str = "preview", approve: bool = False,
        segments: int | None = None, do_upload: bool = True,
        tts_scene: str | None = None, tts_context: str | None = None,
        target_minutes: float = 6.0, hint: str | None = None,
        resume: bool = False,
        meta_embed: bool = False,
        meta_title: str | None = None,
        meta_artist: str | None = None,
        meta_copyright: str | None = None,
        meta_encoder: str | None = None) -> dict:
    cfg = get_config()
    for w in cfg.validate():
        _log(f"warn: {w}")

    slug = slugify(topic)
    out = OUTPUT / slug
    (out / "audio").mkdir(parents=True, exist_ok=True)
    _log(f"output dir: {out}")

    from .cost import TRACKER as COST
    COST.start(title=topic, slug=slug, path=str(out / "final.mp4"))

    # 1. Stage 1 + review gate
    _script_path = out / "script.md"
    if resume and _script_path.exists():
        script_path = _script_path
        _log(f"resume: stage1 skipped (script.md exists)")
        _log(f"stage1 script -> {script_path.name} (~{target_minutes:g} min target)")
    else:
        script_path = ScriptStage(cfg).generate(topic, out,
                                                 target_minutes=target_minutes,
                                                 hint=hint)
        _log(f"stage1 script -> {script_path.name} (~{target_minutes:g} min target)")
    if not review_gate(script_path, auto_approve=approve):
        _log("review gate: NOT approved. Read the script, then approve to "
             "continue (the GUI shows an Approve button; CLI: create the "
             "file 'script.APPROVED' next to script.md, or pass --approve).")
        cs = COST.save(out, ledger=OUTPUT / "llm_cost_ledger.jsonl")
        _log(f"llm cost (so far): ${cs['total_cost_usd']:.4f} "
             f"-> llm_cost.json")
        _log(f"REVIEW_REQUIRED {script_path}")
        return {"status": "awaiting_review", "script": str(script_path)}

    # 2. Stage 2 -> validated control doc
    doc: ControlDocument | None = None
    _video_json = out / "video.json"
    if resume and _video_json.exists():
        try:
            doc = ControlDocument.from_dict(
                json.loads(_video_json.read_text(encoding="utf-8")))
            _log(f"resume: stage2 skipped (video.json exists)")
        except Exception:
            doc = None
    if doc is None:
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

    if not (tts_scene and tts_context):
        from .llm import tts_framing
        fr = tts_framing(cfg.model("metadata_text"), topic)
        if fr:
            tts_scene = tts_scene or fr[0]
            tts_context = tts_context or fr[1]
            _log("voice framing: auto-derived from topic")
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

    # 5b. editorial sound design — one restraint-trained LLM pass picks at
    #     most a few cues from the curated 20 (usually none) and resolves
    #     them onto the aligned word timings. Mutates doc.segments.sound_fx.
    from .pipeline.sound_design import SoundDesigner

    sd = SoundDesigner(cfg).design(doc, aligns)
    _log(f"sound design: {sd['n_cues']} cue(s), {sd['n_dropped']} dropped "
         f"(model={sd['model']}{', offline' if sd['offline'] else ''})")
    for c in sd["cues"]:
        _log(f"  sfx {c['sfx_id']} @ {c['segment_id']} +{c['at_s']:.2f}s "
             f"[{c['intensity']}] — {c['reason']}")

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

    if meta_embed:
        _embed_mp4_metadata(out / "final.mp4", title=meta_title,
                            artist=meta_artist, copyright=meta_copyright,
                            encoder=meta_encoder)
        _log("mp4 metadata: embedded")

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
        "sound_design": {
            "n_cues": sd["n_cues"], "n_dropped": sd["n_dropped"],
            "offline": sd["offline"], "model": sd["model"],
            "cues": sd["cues"],
        },
        # true if ANY stage fell back to a stub (drives the cost note, G14)
        "any_stub": vr.offline
        or next(iter(aligns.values())).method == "proportional",
    }
    write_manifest(out, cfg=cfg, doc=doc, render_info=info,
                   master_info=master, clip_provenance=cp.manifest,
                   qa=qa, upload=up, script_topic=topic, runtime=runtime)
    _log(f"manifest -> render_manifest.json")
    cs = COST.save(out, ledger=OUTPUT / "llm_cost_ledger.jsonl")
    _log(f"llm cost: ${cs['total_cost_usd']:.4f} ({cs['llm_calls']} calls) "
         f"-> llm_cost.json | {cs['context']}")
    _log(f"DONE — deliverable at {out}/final.mp4")
    return {"status": "ok", "output": str(out), "qa": qa["passed"],
            "llm_cost_usd": cs["total_cost_usd"]}


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
    ap.add_argument("--minutes", type=float, default=6.0,
                    help="approx target video length in minutes (~150 wpm)")
    ap.add_argument("--hint", default=None,
                    help="optional script hints or a raw story to base the "
                         "narration on (free text)")
    ap.add_argument("--resume", action="store_true",
                    help="reuse existing script.md / video.json artifacts "
                         "to skip completed API stages")
    ap.add_argument("--meta-embed", action="store_true",
                    help="embed MP4 metadata tags into the final video")
    ap.add_argument("--meta-title",     default=None)
    ap.add_argument("--meta-author",    default=None)
    ap.add_argument("--meta-copyright", default=None)
    ap.add_argument("--meta-encoder",   default=None)
    a = ap.parse_args(argv)
    r = run(a.topic, preset=a.preset, approve=a.approve,
            segments=a.segments, do_upload=not a.no_upload,
            tts_scene=a.tts_scene, tts_context=a.tts_context,
            target_minutes=a.minutes, hint=a.hint, resume=a.resume,
            meta_embed=a.meta_embed, meta_title=a.meta_title,
            meta_artist=a.meta_author, meta_copyright=a.meta_copyright,
            meta_encoder=a.meta_encoder)
    return 0 if r["status"] in ("ok", "awaiting_review") else 1


if __name__ == "__main__":
    sys.exit(main())
