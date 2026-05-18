"""Fast finisher: build a watchable video from already-generated artifacts.

Reuses the real Stage-1 script.md + Stage-2 video.json already on disk
(no re-spend on Anthropic), takes the first N segments, forces the stub
voice (free-tier Gemini TTS is exhausted), then runs align -> reflow ->
master -> render -> metadata -> QA. No upload.

Usage: python scripts/finish_from_artifacts.py <slug> [N]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vp.audio_util import pad_silence, write_wav  # noqa: E402
from vp.pipeline.tts_gemini import _offline_voice  # noqa: E402
from vp.config import OUTPUT, get_config            # noqa: E402
from vp.pipeline.align import align_segment         # noqa: E402
from vp.pipeline.master import build_master         # noqa: E402
from vp.pipeline.metadata import MetadataStage      # noqa: E402
from vp.pipeline.pexels import ClipProvider         # noqa: E402
from vp.pipeline.qa import run_qa, write_manifest   # noqa: E402
from vp.pipeline.render import RenderEngine         # noqa: E402
from vp.pipeline.timeline import reflow             # noqa: E402
from vp.schema.model import ControlDocument         # noqa: E402
from vp.schema.validator import validate            # noqa: E402


def _stub_voice(segments, audio_dir):
    """Write stub-voice WAVs directly (bypass TTSEngine; guaranteed offline)."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    from vp.pacing import effective_silences
    for s in segments:
        pre, post = effective_silences(s)
        samples = pad_silence(_offline_voice(s.text_overlay), pre, post)
        p = audio_dir / f"{s.id}.wav"
        write_wav(p, samples)
        s.audio_path = str(p)


def main():
    slug = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    out = OUTPUT / slug
    cfg = get_config()

    doc = ControlDocument.from_dict(
        json.loads((out / "video.json").read_text(encoding="utf-8")))
    doc.segments = doc.segments[:n]
    res = validate(doc)
    print(f"[fin] {len(doc.segments)} segments, validate ok={res.ok}", flush=True)

    _stub_voice(doc.segments, out / "audio")
    print("[fin] tts (local Piper voice) done", flush=True)

    aligns = {}
    for s in doc.segments:
        aligns[s.id] = align_segment(s.id, s.text_overlay, Path(s.audio_path),
                                     pre_ms=s.pre_silence_ms,
                                     post_ms=s.post_silence_ms, offline=True)
    tl = reflow(doc.segments)
    print(f"[fin] timeline {tl.total_duration:.1f}s", flush=True)

    master = build_master(doc.segments, out / "master.wav", doc.video_meta)
    cp = ClipProvider(cfg)
    eng = RenderEngine(cfg)
    eng.visual_source = cp.visual_source
    from vp.fx.ambient import FilmAmbience, KenBurns
    from vp.fx.camera import CameraMotion, CutRhythm
    from vp.fx.color import ColorGrade, FilmFX
    from vp.fx.text import TextRenderer
    for x in (KenBurns(), CameraMotion(), ColorGrade(), TextRenderer(),
              FilmFX(), FilmAmbience(), CutRhythm()):
        eng.register_xform(x)
    info = eng.render(doc, aligns, out / "final.mp4", preset="preview",
                      work_dir=out / "_work", audio_track=out / "master.wav")
    print(f"[fin] render {info['resolution']} {info['duration']:.1f}s", flush=True)

    meta = MetadataStage(cfg).run(out / "final.mp4", doc,
                                  (out / "script.md").read_text(encoding="utf-8"),
                                  out)
    qa = run_qa(out / "final.mp4", out / "master.wav",
                len(doc.segments), len(aligns))
    write_manifest(out, cfg=cfg, doc=doc, render_info=info, master_info=master,
                   clip_provenance=cp.manifest, qa=qa,
                   upload={"status": "disabled"}, script_topic=slug,
                   runtime={"tts_offline_stub": True,
                            "alignment_method": "proportional",
                            "any_stub": True,
                            "note": "voice=piper-local-neural (offline); "
                                    "script/segmentation/footage real"})
    print(f"[fin] QA passed={qa['passed']} -> {out/'final.mp4'}", flush=True)
    print("[fin] DONE", flush=True)


if __name__ == "__main__":
    main()
