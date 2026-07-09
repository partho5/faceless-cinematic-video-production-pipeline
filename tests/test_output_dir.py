import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from vp.run import run, main

def test_run_with_custom_output_dir():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        
        # Create output dir structure to simulate run
        out_dir = tmp_path / "test-topic"
        out_dir.mkdir(parents=True, exist_ok=True)
        script_file = out_dir / "script.md"
        script_file.write_text("Dummy script content", encoding="utf-8")
        
        # We need to mock stages to avoid executing real LLM APIs or processing
        with patch("vp.run.ScriptStage") as mock_script_stage, \
             patch("vp.run.SegmentStage") as mock_segment_stage, \
             patch("vp.run.review_gate", return_value=True), \
             patch("vp.run.validate") as mock_validate, \
             patch("vp.pipeline.music_design.MusicDesigner") as mock_music_designer, \
             patch("vp.pipeline.voice.VoiceStage") as mock_voice_stage, \
             patch("vp.run.align_segment") as mock_align_segment, \
             patch("vp.run.reflow") as mock_reflow, \
             patch("vp.pipeline.sound_design.SoundDesigner") as mock_sound_designer, \
             patch("vp.run.build_master") as mock_build_master, \
             patch("vp.run.ClipProvider") as mock_clip_provider, \
             patch("vp.run.RenderEngine") as mock_render_engine, \
             patch("vp.run.run_qa") as mock_run_qa, \
             patch("vp.run.MetadataStage") as mock_metadata_stage, \
             patch("vp.run.yt_upload") as mock_yt_upload, \
             patch("vp.run.write_manifest"):

            # Setup mocks
            mock_script_stage.return_value.generate.return_value = script_file
            mock_script_stage.return_value.spec.offline = True
            
            mock_doc = MagicMock()
            mock_segment = MagicMock()
            mock_segment.id = "s1"
            mock_segment.text_overlay = "hello"
            mock_segment.audio_path = "/tmp/audio.wav"
            mock_segment.pre_silence_ms = 0
            mock_segment.post_silence_ms = 0
            mock_doc.segments = [mock_segment]
            mock_doc.video_meta = {}
            mock_segment_stage.return_value.generate.return_value = mock_doc
            mock_segment_stage.return_value.spec.offline = True
            
            mock_validate.return_value = MagicMock(ok=True, warnings=[])
            
            mock_music_designer.return_value.design.return_value = {"track": "track1", "presence": "bg", "model": "stub", "offline": True, "meta_patch": {}}
            
            mock_vr = MagicMock()
            mock_vr.chapters = 1
            mock_vr.segments = 1
            mock_vr.key_count = 0
            mock_vr.offline = True
            mock_voice_stage.return_value.synthesize.return_value = mock_vr
            mock_voice_stage.return_value.voice = "Leda"
            
            mock_align = MagicMock()
            mock_align.method = "stub"
            mock_align_segment.return_value = mock_align
            
            mock_tl = MagicMock()
            mock_tl.total_duration = 10.0
            mock_reflow.return_value = mock_tl
            
            mock_sound_designer.return_value.design.return_value = {"n_cues": 0, "n_dropped": 0, "offline": True, "model": "stub", "cues": []}
            mock_build_master.return_value = {"duration": 10.0, "sfx_events": []}
            mock_render_engine.return_value.render.return_value = {"resolution": "1920x1080", "duration": 10.0}
            mock_metadata_stage.return_value.run.return_value = {"tags": [], "hashtags": [], "thumbnail_prompt": ""}
            mock_run_qa.return_value = {"passed": True, "checks": []}
            mock_yt_upload.return_value = {"status": "ok", "url": "http://youtube.com/stub"}

            # Execute run
            res = run("Test Topic", preset="preview", approve=True, do_upload=True, output_dir=tmp_path)
            
            # Assert output directory resolves to our temp path
            assert res["status"] == "ok"
            assert Path(res["output"]).parent == tmp_path
            assert (tmp_path / "test-topic").exists()
            assert (tmp_path / "llm_cost_ledger.jsonl").exists()

def test_main_cli_arg_parsing():
    with patch("vp.run.run") as mock_run:
        mock_run.return_value = {"status": "ok", "total_cost_usd": 0.0}
        
        # Test CLI arguments parsing
        main(["Test Topic", "--output-dir", "/tmp/custom_out"])
        
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["output_dir"] == "/tmp/custom_out"
