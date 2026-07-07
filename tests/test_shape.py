from pathlib import Path
from unittest.mock import MagicMock, patch
from vp.pipeline.pexels import ClipProvider
from vp.pipeline.render import RenderEngine
from vp.config import get_config


def test_clip_provider_initialization():
    cfg = get_config()
    cp = ClipProvider(cfg, shape="vertical")
    assert cp.shape == "vertical"


def test_clip_provider_pexels_search_params():
    cfg = get_config()
    with patch.object(cfg, "env", side_effect=lambda name: "dummy_key" if name == "PEXELS_API_KEY" else ""):
        cp = ClipProvider(cfg, shape="vertical")

        with patch("requests.Session") as mock_session_class:
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session
            mock_response = MagicMock()
            mock_response.json.return_value = {"videos": []}
            mock_session.get.return_value = mock_response

            cp._search_pexels("test query", 5.0)

            # Verify that get() was called with orientation="vertical"
            mock_session.get.assert_called_once()
            args, kwargs = mock_session.get.call_args
            assert kwargs["params"]["orientation"] == "vertical"


def test_render_engine_preset_swapping():
    cfg = get_config()
    eng = RenderEngine(cfg)

    with patch("vp.pipeline.render.reflow") as mock_reflow, \
         patch("vp.pipeline.render.RenderContext") as mock_ctx_class, \
         patch("moviepy.VideoClip") as mock_video_clip, \
         patch("pathlib.Path.mkdir"), \
         patch("subprocess.run"):

         # Mock doc & alignments
         doc = MagicMock()
         doc.segments = []
         alignments = {}

         try:
             eng.render(doc, alignments, Path("dummy.mp4"), preset="preview", shape="vertical")
         except Exception:
             pass

         mock_ctx_class.assert_called_once()
         args, kwargs = mock_ctx_class.call_args
         assert kwargs["w"] == 540
         assert kwargs["h"] == 960
