# 10 — Model Configuration (per-purpose models)

Different purposes use different models. This is **config-driven** via
`config.yaml` (template: [../config.example.yaml](../config.example.yaml)) —
swap a model for any purpose without code changes. Secrets stay in `.env`;
config only names provider/model/params and which env var holds the key.

## Purposes → default model

| Purpose | Default | Why / notes |
|---------|---------|-------------|
| `script_generation` | Anthropic `claude-opus-4-7` | Creative narration; quality drives the whole video |
| `segmentation_direction` | Anthropic `claude-opus-4-7` | Script → control JSON; swap to `claude-sonnet-4-6` to cut cost |
| `metadata_text` | Anthropic `claude-sonnet-4-6` | Titles/description/tags — cheap, high volume |
| `thumbnail_image` | `frame_compose` (no model) | Best video frame + Pillow text; optional `image_gen` mode via `gemini-2.5-flash-image` |
| `tts_audio` | Google `gemini-2.5-pro-preview-tts` | Voiceover; fixed `voice_name`; per-segment scene+delivery ([06](06-voice-and-timing.md)) |
| `forced_alignment` | local `stable-ts` (`base`) | Word timing; no API; `mfa` fallback ([06](06-voice-and-timing.md)) |

## Rules for the build

- Every model-calling module reads its model from `config.yaml` by purpose
  key — **no hardcoded model IDs anywhere**.
- Resolve the API key via the purpose's `api_key_env` against `.env`.
- Validate `config.yaml` at startup (purpose keys present, referenced env
  vars set for non-local providers); fail fast with a clear message.
- Preview model IDs (e.g. Gemini TTS) change — config makes that a one-line
  edit, not a code change. Verify current IDs at build time.
- Record the resolved model per purpose into `render_manifest.json` for
  traceability and per-video cost attribution (gap G14).

## Adding a new purpose later

Add a block under `models:` in `config.yaml`, give the module a purpose key,
done. Examples that may appear later: `b_roll_image_gen`, `music_selection`,
`content_safety_check`.
