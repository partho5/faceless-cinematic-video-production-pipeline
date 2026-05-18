"""Production-ready short video from a tiny script.

Real path end-to-end: Stage-2 segmentation (Anthropic) -> continuous Leda
voice (Gemini 3.1, pipeline/voice.py) -> stable-ts align -> reflow ->
master -> 1080p render (all 10 layers) -> thumbnail/metadata -> QA ->
render_manifest. No upload (local file is the deliverable).

Usage: python scripts/make_short_video.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vp.config import OUTPUT, get_config            # noqa: E402
from vp.pipeline.align import align_segment         # noqa: E402
from vp.pipeline.master import build_master         # noqa: E402
from vp.pipeline.metadata import MetadataStage      # noqa: E402
from vp.pipeline.pexels import ClipProvider         # noqa: E402
from vp.pipeline.qa import run_qa, write_manifest   # noqa: E402
from vp.pipeline.render import RenderEngine         # noqa: E402
from vp.pipeline.script_gen import SegmentStage, slugify  # noqa: E402
from vp.pipeline.timeline import reflow             # noqa: E402
from vp.pipeline.voice import VoiceStage            # noqa: E402
from vp.schema.validator import validate            # noqa: E402

TITLE = "The Illusion of Love"
SCRIPT = """**[HOOK · 0:00–0:25]**
It felt like love. It was supposed to feel like love. But that intensity \
was never love. It was a strategy. By the time you noticed, you had already \
rearranged your life around someone else's comfort. The cruelest part? You \
thanked them for it.
"""


def log(m):
    print(f"[short] {m}", flush=True)


def main():
    cfg = get_config()
    out = OUTPUT / slugify(TITLE)
    (out / "audio").mkdir(parents=True, exist_ok=True)
    (out / "script.md").write_text(f"# {TITLE}\n\n{SCRIPT}", encoding="utf-8")
    log(f"out={out}")

    doc = SegmentStage(cfg).generate(out / "script.md", out)
    doc.video_meta["title"] = TITLE
    res = validate(doc)
    log(f"stage2: {len(doc.segments)} segments validate ok={res.ok}")
    if not res.ok:
        log("FATAL invalid doc:\n" + str(res))
        return 1

    vr = VoiceStage(cfg).synthesize(doc, out / "audio")
    log(f"voice: {vr.chapters} chapter -> {vr.segments} slices "
        f"(Leda, keys={vr.key_count}, offline={vr.offline})")

    aligns = {}
    for s in doc.segments:
        aligns[s.id] = align_segment(s.id, s.text_overlay, Path(s.audio_path),
                                     offline=cfg.model("forced_alignment").offline
                                     or vr.offline)
    tl = reflow(doc.segments)
    log(f"timeline {tl.total_duration:.2f}s")

    master = build_master(doc.segments, out / "master.wav", doc.video_meta)
    log(f"master {master['duration']:.2f}s, {len(master['sfx_events'])} sfx")

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
    info = eng.render(doc, aligns, out / "final.mp4", preset="final",
                      work_dir=out / "_work", audio_track=out / "master.wav")
    log(f"render {info['resolution']} {info['duration']:.2f}s")

    meta = MetadataStage(cfg).run(out / "final.mp4", doc,
                                  (out / "script.md").read_text(encoding="utf-8"),
                                  out)
    qa = run_qa(out / "final.mp4", out / "master.wav",
                len(doc.segments), len(aligns))
    write_manifest(out, cfg=cfg, doc=doc, render_info=info, master_info=master,
                   clip_provenance=cp.manifest, qa=qa,
                   upload={"status": "disabled"}, script_topic=TITLE,
                   runtime={"voice": f"gemini-3.1/Leda continuous",
                            "alignment_method":
                            next(iter(aligns.values())).method,
                            "any_stub": vr.offline})
    log(f"QA passed={qa['passed']} ({[c['check'] for c in qa['checks'] if not c['pass']] or 'all ok'})")
    log(f"DONE -> {out/'final.mp4'}  thumb={out/'thumbnail.jpg'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
