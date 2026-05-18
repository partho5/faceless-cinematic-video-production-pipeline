# 03 — Variation System (the actual differentiator)

## Core principle

Variation must come from **emotional intelligence, not random shuffling**.
Every visual/audio choice serves the psychological beat of that exact moment.
Goal: a viewer cannot predict the next cut / text style / motion, and cannot
tell it was automated. The video must feel **directed**.

## Layer 1 — Emotional Beat Mapping

Claude maps every 2–4s of script to a beat type, and each beat type triggers a
distinct visual treatment recipe:

`hook · curiosity · tension_build · reveal · shock · pause · recovery ·
question · confession · threat · empathy · climax`

## Layer 2 — Text Animation Library (30+)

- **Reveal:** word-by-word fade · char typewriter · word slam from random
  direction · settle-with-bounce · raindrop fall · materialize from blur ·
  split-character (top drops / bottom rises) · invisible-pen stroke ·
  emerge from expanding circles · emerge from drawn underline.
- **Emphasis:** scale-up 1.3x then settle · brief glow · 0.3s shake · color
  shift on emphasis word only · background dims on emphasis word · emphasis
  word stays while rest fade.
- **Exit:** shatter & fall · burn away · dissolve to particles · shrink to
  dot · slide off in reading direction · flash white then gone · hard cut ·
  inverse typewriter (delete char by char).
- **Special:** glitch corruption on shock words · censored bar then reveal ·
  word replaced mid-display (thought correction) · word splits and drifts ·
  zoom from far distance to camera.

## Layer 3 — Camera Motion Library

- **Static:** locked frame (dread) · imperceptible 1–2px/s drift.
- **Push/Pull:** slow push-in (4–6s, intimacy) · aggressive push-in (1–2s,
  threat) · slow pull-out (isolation) · crash zoom · dolly-zoom (vertigo).
- **Lateral:** subtle L/R drift · parallax · whip pan · crane down · tilt-up reveal.
- **Handheld:** light shake · heavy shake · breath-rhythm shake · recoil on impact.
- **Stylized:** dutch-angle tilt · 180° spin transition · mirror flip · zoom
  punch · time-freeze with rotation.

## Layer 4 — Color Grading System (mood-based, not just tension)

Each is an FFmpeg LUT + curve adjustments + grain settings:

`cold_isolation · warm_comfort · clinical · surveillance · memory · threat ·
madness · revelation · death(near-mono, one color) · dream · interrogation ·
nostalgia`

## Layer 5 — Cut Rhythm Intelligence

Cuts follow speech rhythm, not a fixed interval:

- Calm narration: 3–5s clips.
- Building tension: cuts shorten to 1–2s.
- Peak: rapid 0.3–0.8s.
- After reveal: long hold 4–8s (let it breathe).
- Pause beats: held freeze frame.

Cut types by context: hard · J-cut · L-cut · smash · flash frame (1–2f) ·
match cut · dip to black · glitch transition · cross dissolve (dream/memory
only) · whip-pan transition.

## Layer 6 — Sound Design Per Segment

- **Voice (always):** Google TTS; subtle reverb on whisper beats;
  de-ess + compression baseline; pitch down 1–2 semitones on threat beats.
- **Music bed:** tension drone · heartbeat (slow→fast) · strings ostinato ·
  piano single notes · sub-bass pulse · **silence** (strongest, at reveals).
- **SFX (JSON-triggered):** whoosh · thud · bass drop · glitch crackle ·
  heartbeat · breath · tinnitus ring · static · match strike · door close ·
  phone vibrate · camera shutter.
- **Ducking:** Pydub analyzes voice loudness per frame, auto-attenuates music.

## Layer 7 — Clip Selection Strategy

Per segment Claude emits: primary query · backup query · required visual
element (close-up face / wide isolation / hands / eyes / shadow) · pacing
need (still vs moving). Python tracks used clip IDs → **no repeat within a
video**; aims for B-roll diversity.

## Layer 8 — Typography Personality System

| Personality | Use | Style |
|-------------|-----|-------|
| Aggressive | threat, accusation | Anton/Bebas, ALL CAPS, red/white, hard shadow, slight skew |
| Clinical | facts, analysis | Inter/Helvetica, mixed case, off-white, tight tracking |
| Whisper | intimate | Cormorant light italic, soft glow, faded edges |
| Reveal | shock, conclusion | Playfair/DM Serif, large, cream/gold |
| Handwritten | personal, confession | Caveat/Kalam, off-baseline |

Text color shifts through the video (white / red / yellow) — never constant.

## Layer 9 — Visual FX Layer (always running, intensity varies)

Film grain (5–40%) · vignette (10–60%) · chromatic aberration (0% calm,
15–30% glitch) · light leaks (revelation flashes) · dust particles
(memory/dream) · scan lines (surveillance) · slight barrel distortion ·
color noise (shock).

## Layer 10 — Rhythm & Pacing Control (whole-video arc)

Opening hook = high density. Body = calmer/longer holds. Build = tightening
cuts + intensifying music. Climax = peak everything. Outro = slow down/fade.
The video has one emotional arc, not just per-segment treatment.

> Net effect: every ~3s window is a unique combination across 10 dimensions —
> variation space in the millions.
