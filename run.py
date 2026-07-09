#!/usr/bin/env python3
"""Easy-run GUI launcher (premium UI).

    python run.py

Fill the topic (and optional script hints / raw story), press Create Video.
The voice style is derived from your topic automatically. The full pipeline
(src/vp/run.py) runs in a subprocess; its progress streams live into the log
pane and drives the progress bar, so the window never freezes.

If you ask to review the story first, the app pauses after writing it and
gives you an Approve button to continue.

Pure standard library (Tkinter). No extra dependencies. On minimal Linux you
may need the system package `python3-tk`.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
ENV_FILE = ROOT / ".env"
RESUME_FILE = ROOT / ".resume_state.json"
METADATA_PROFILE = ROOT / ".metadata_profile.json"
RENDER_PROFILE = ROOT / ".render_profile.json"
SAMPLE_SEGMENTS = "6"  # "short sample" cap
_LOG_FILE = ROOT / "run.log"

_logger: logging.Logger | None = None


def _setup_logging() -> None:
    global _logger
    if _logger is not None:
        return
    handler = logging.FileHandler(str(_LOG_FILE), encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"))
    logger = logging.getLogger("vp.launcher")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = False
    _logger = logger
    _logger.info("run.py started (pid=%d)", os.getpid())


def _ensure_venv_python() -> None:
    """If invoked with anything other than the project venv's Python while
    .venv exists, re-exec under the venv interpreter. Without this, a user
    double-clicking run.py picks up whatever Python their .py association
    points at (often a stale system Python missing google-genai or with an
    ancient anthropic SDK), and the pipeline crashes mid-run. install.bat
    is run once; this guard makes the result stick.

    Compares sys.prefix (the venv root, never a symlink) instead of
    resolved sys.executable — on Linux the venv's python3 is a symlink to
    the system interpreter, so a resolved-path check would falsely match
    and let the wrong Python through."""
    venv_dir = ROOT / ".venv"
    venv_py = venv_dir / ("Scripts/python.exe" if os.name == "nt"
                          else "bin/python3")
    if not venv_py.exists():
        return
    try:
        if Path(sys.prefix).resolve() == venv_dir.resolve():
            return
    except Exception:
        return
    print(f"[run.py] re-launching under venv: {venv_py}", flush=True)
    raise SystemExit(subprocess.call(
        [str(venv_py), str(Path(__file__).resolve()), *sys.argv[1:]]))


_ensure_venv_python()
_setup_logging()


try:
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import messagebox, scrolledtext, ttk
except Exception:  # pragma: no cover - environment without Tk
    sys.stderr.write(
        "Tkinter is not available. Install it (e.g. `sudo apt install "
        "python3-tk`) or use the CLI:\n"
        '  python -m vp.run "Your topic" --approve --preset final\n'
    )
    sys.exit(1)


# ----------------------------------------------------------------- voice data ----
_GEMINI_VOICES = [
    "Achernar", "Aoede", "Algieba", "Charon", "Despina", "Enceladus",
    "Erinome", "Fenrir", "Gacrux", "Iapetus", "Kore", "Laomedeia",
    "Leda", "Orus", "Puck", "Pulcherrima", "Sadachbia", "Schedar",
    "Sulafat", "Umbriel", "Vindemiatrix", "Zephyr", "Zubenelgenubi",
]

# (display label, BCP-47 code passed to Gemini; "" = let the model auto-detect)
_GEMINI_LANGUAGES: list[tuple[str, str]] = [
    ("Auto (detect)",           ""),
    ("Arabic",                  "ar-XA"),
    ("Bengali",                 "bn-IN"),
    ("Chinese (Simplified)",    "cmn-CN"),
    ("Chinese (Traditional)",   "cmn-TW"),
    ("Dutch",                   "nl-NL"),
    ("English",                 "en-US"),
    ("French",                  "fr-FR"),
    ("German",                  "de-DE"),
    ("Greek",                   "el-GR"),
    ("Gujarati",                "gu-IN"),
    ("Hindi",                   "hi-IN"),
    ("Indonesian",              "id-ID"),
    ("Italian",                 "it-IT"),
    ("Japanese",                "ja-JP"),
    ("Kannada",                 "kn-IN"),
    ("Korean",                  "ko-KR"),
    ("Malayalam",               "ml-IN"),
    ("Marathi",                 "mr-IN"),
    ("Polish",                  "pl-PL"),
    ("Portuguese (Brazil)",     "pt-BR"),
    ("Portuguese (Portugal)",   "pt-PT"),
    ("Punjabi",                 "pa-IN"),
    ("Romanian",                "ro-RO"),
    ("Russian",                 "ru-RU"),
    ("Spanish",                 "es-ES"),
    ("Swedish",                 "sv-SE"),
    ("Tamil",                   "ta-IN"),
    ("Telugu",                  "te-IN"),
    ("Thai",                    "th-TH"),
    ("Turkish",                 "tr-TR"),
    ("Ukrainian",               "uk-UA"),
    ("Vietnamese",              "vi-VN"),
]
_LANG_LABEL_TO_CODE = {label: code for label, code in _GEMINI_LANGUAGES}
_LANG_CODE_TO_LABEL = {code: label for label, code in _GEMINI_LANGUAGES}
_LANG_LABELS = [label for label, _ in _GEMINI_LANGUAGES]

# ----------------------------------------------------------------- theme ----
class T:
    BG = "#0E1116"          # app background
    PANEL = "#161B22"       # section panels
    INPUT = "#0D1117"       # entry / text fields
    FG = "#E6EDF3"          # primary text
    MUTED = "#8B949E"       # secondary text
    BORDER = "#30363D"      # hairlines / separators
    BORDER_INPUT = "#484F58" # input field resting border (muted but visible)
    ACCENT = "#2F81F7"      # primary action
    ACCENT_HOVER = "#4493F8"
    OK = "#3FB950"          # success / configured
    OFF = "#6E7681"         # not configured
    WARN = "#D29922"


# progress milestones: substring found in a streamed line -> percent.
# (kept in order; the bar never moves backwards)
_MILESTONES: list[tuple[str, int, str]] = [
    ("output dir:", 3, "Preparing workspace…"),
    ("stage1: writing", 6, "Writing the script…"),
    ("stage1: reusing approved", 10, "Reusing approved script…"),
    ("stage1: offline", 8, "Script (offline sample)…"),
    ("stage1 script ->", 12, "Script ready"),
    ("stage2: segmenting", 16, "Directing scene segments…"),
    ("stage2 doc:", 45, "Segments validated"),
    ("voice framing:", 48, "Deriving voice style…"),
    ("voice:", 60, "Voiceover synthesized"),
    ("timeline reflowed", 64, "Timeline built"),
    ("sound design:", 66, "Designing sound…"),
    ("master:", 68, "Mastering audio…"),
    ("render ->", 90, "Render complete"),
    ("metadata:", 93, "Thumbnail + metadata…"),
    ("QA passed", 96, "Quality checks…"),
    ("manifest ->", 98, "Writing manifest…"),
    ("llm cost", 99, "Cost report saved"),
    ("DONE", 100, "Finished"),
]
_RE_CHAPTER = re.compile(r"stage2: chapter (\d+)/(\d+)")


def _app_name() -> str:
    """Read APP_NAME from .env; fall back to default."""
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("APP_NAME="):
                v = line.partition("=")[2].strip().strip('"').strip("'")
                if v:
                    return v
    return "Video Production Studio"


_APP_NAME = _app_name()


def _detect_keys() -> dict[str, bool]:
    """Read .env (names only, never values) to show what's configured."""
    present: dict[str, str] = {}
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            present[k.strip()] = v.strip().strip('"').strip("'")

    def has(*names: str) -> bool:
        return any(present.get(n) for n in names)

    return {
        "Claude/Groq · script": has("ANTHROPIC_API_KEY", "GROQ_API_KEY"),
        "Gemini · voice": has("GEMINI_API_KEY", "GEMINI_API_KEY_1"),
        "Pexels · footage": has("PEXELS_API_KEY"),
        "YouTube · upload": has("YT_CLIENT_ID") and has("YT_CLIENT_SECRET")
        and has("YT_REFRESH_TOKEN"),
    }


def _save_resume_state(state: dict) -> None:
    try:
        RESUME_FILE.write_text(json.dumps(state, ensure_ascii=False),
                               encoding="utf-8")
    except Exception:
        pass


def _load_resume_state() -> dict | None:
    if not RESUME_FILE.exists():
        return None
    try:
        return json.loads(RESUME_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _clear_resume_state() -> None:
    RESUME_FILE.unlink(missing_ok=True)


_RE_UNSAFE_FILENAME = re.compile(r'[\\/*?:"<>|\x00-\x1f]')


def _safe_filename(title: str, max_len: int = 120) -> str:
    s = _RE_UNSAFE_FILENAME.sub("", title)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:max_len] or "video"


def _embed_mp4_metadata(
    path: "Path",
    *,
    title: str | None = None,
    artist: str | None = None,
    copyright: str | None = None,
    encoder: str | None = None,
) -> None:
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S")
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


def _load_meta_profile() -> dict:
    try:
        return json.loads(METADATA_PROFILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_meta_profile(data: dict) -> None:
    try:
        METADATA_PROFILE.write_text(json.dumps(data, ensure_ascii=False,
                                                indent=2), encoding="utf-8")
    except Exception:
        pass


def _load_render_profile() -> dict:
    try:
        return json.loads(RENDER_PROFILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_render_profile(data: dict) -> None:
    try:
        RENDER_PROFILE.write_text(json.dumps(data, ensure_ascii=False,
                                              indent=2), encoding="utf-8")
    except Exception:
        pass


class App:
    def __init__(self, master: tk.Tk) -> None:
        self.master = master

        # Enable Ctrl+A select-all in all input fields
        def select_all_entry(event):
            event.widget.select_range(0, 'end')
            event.widget.icursor('end')
            return "break"

        def select_all_text(event):
            event.widget.tag_add("sel", "1.0", "end-1c")
            return "break"

        for key in ("<Control-Key-a>", "<Control-Key-A>", "<Control-a>", "<Control-A>"):
            master.bind_class("TEntry", key, select_all_entry)
            master.bind_class("Entry", key, select_all_entry)
            master.bind_class("Text", key, select_all_text)

        self.proc: subprocess.Popen | None = None
        self.q: queue.Queue[str] = queue.Queue()
        self.last_argv: list[str] = []
        self.out_dir: Path | None = None
        self.video_path: Path | None = None
        self.review_script: Path | None = None
        self.review_pending = False
        self._resuming = False
        self._pct = 0          # logical target %
        self._disp = 0         # currently displayed % (animated toward target)
        self._anim_job = None
        self._creep_job = None
        self._placeholders: dict = {}
        self._api_error: tuple[str, str] | None = None
        self._last_rc: int | None = None
        self._start_time: float | None = None

        master.title(_APP_NAME)
        master.geometry("880x860")
        # small min size: the whole UI is scrollable, so it stays usable
        # (nothing hidden) even when the window is shrunk a lot.
        master.minsize(560, 380)
        master.configure(bg=T.BG)

        self._init_style()
        body = self._scrollable(master)

        self._build_header(body)

        nb = ttk.Notebook(body)
        nb.pack(fill="x", pady=(16, 0))
        tab_content = ttk.Frame(nb, style="App.TFrame")
        tab_output = ttk.Frame(nb, style="App.TFrame")
        nb.add(tab_content, text="  Content  ")
        nb.add(tab_output, text="  Output  ")
        self._build_content(tab_content)
        self._build_output(tab_output)

        tk.Frame(body, bg=T.WARN, height=3).pack(fill="x", pady=(14, 12))
        self._build_actions(body)
        self._build_status(body)
        self._build_progress(body)
        self._build_log(body)

        self.master.after(100, self._check_resume)
        self.master.after(120, self._drain)

    # -- resume ------------------------------------------------------------
    def _check_resume(self) -> None:
        state = _load_resume_state()
        if not state:
            return
        topic = state.get("topic", "")
        if not messagebox.askyesno(
            "Unfinished task",
            f"Unfinished task found:\n\n\"{topic}\"\n\nFinish it first?",
            default="yes",
        ):
            _clear_resume_state()
            return
        self.topic.delete(0, "end")
        self.topic.insert(0, topic)
        self.preset.set(state.get("preset", "final"))
        self.shape.set(state.get("shape", "landscape"))
        self.duration_min.set(int(state.get("duration_min", 6)))
        self.duration_sec.set(int(state.get("duration_sec", 0)))
        hint = state.get("hint", "")
        self.hint.delete("1.0", "end")
        if hint:
            self.hint.insert("1.0", hint)
        self.upload.set(state.get("upload", False))
        self.review.set(state.get("review", False))
        self.sample.set(state.get("sample", False))
        self.voice_var.set(state.get("voice", "Leda"))
        self.lang_var.set(state.get("language_label", _LANG_LABELS[0]))
        self.sub_lang_var.set(state.get("subtitle_language_label",
                                        "Auto (same as Script Language)"))
        self._resuming = True
        self.on_run()
        self._resuming = False

    # -- scrollable shell --------------------------------------------------
    def _scrollable(self, master) -> ttk.Frame:
        """A vertically scrollable container.

        Everything is built inside the returned frame, so the whole UI
        (including anything added in the future) scrolls and nothing is
        ever clipped when the window is small. Content width tracks the
        viewport so the layout stays responsive.
        """
        outer = ttk.Frame(master, style="App.TFrame")
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=T.BG, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = ttk.Frame(canvas, style="App.TFrame")
        win = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _fit(_evt=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            # only show the scrollbar when content overflows
            need = inner.winfo_reqheight() > canvas.winfo_height()
            vsb.pack_forget() if not need else vsb.pack(
                side="right", fill="y", before=canvas)

        inner.bind("<Configure>", _fit)
        canvas.bind("<Configure>",
                    lambda e: (canvas.itemconfigure(win, width=e.width),
                               _fit()))

        def _wheel(e):
            # let the log pane (its own scrollbar) keep the wheel when
            # the pointer is over it; otherwise scroll the page
            w = e.widget
            while w is not None:
                if w is getattr(self, "log", None):
                    return
                w = getattr(w, "master", None)
            step = 1 if (getattr(e, "num", 0) == 5 or e.delta < 0) else -1
            canvas.yview_scroll(step, "units")

        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind_all(seq, _wheel)

        pad = ttk.Frame(inner, style="App.TFrame")
        pad.pack(fill="both", expand=True, padx=22, pady=18)
        return pad

    # -- custom checkbox tick ---------------------------------------------
    def _check_images(self) -> tuple[tk.PhotoImage, tk.PhotoImage]:
        """Two indicator images (unticked box / ticked box with a ✓).

        Drawn pixel-wise with PhotoImage so it renders identically on any
        Tk build (no theme glyphs, no PIL). W has a few trailing panel-
        coloured columns acting as the gap before the label.
        """
        W, H = 22, 16

        def grid(bg):
            return [[bg] * W for _ in range(H)]

        def box(g, edge, fill):
            for y in range(2, 14):
                for x in range(2, 14):
                    g[y][x] = fill
            for i in range(2, 14):
                g[2][i] = g[13][i] = edge
                g[i][2] = g[i][13] = edge

        def seg(g, x0, y0, x1, y1, col, t=2):
            n = max(abs(x1 - x0), abs(y1 - y0))
            for s in range(n + 1):
                x = round(x0 + (x1 - x0) * s / n)
                y = round(y0 + (y1 - y0) * s / n)
                for dy in range(t):
                    for dx in range(t):
                        xx, yy = x + dx, y + dy
                        if 0 <= xx < W and 0 <= yy < H:
                            g[yy][xx] = col

        def make(g):
            img = tk.PhotoImage(width=W, height=H)
            for y, row in enumerate(g):
                img.put("{" + " ".join(row) + "}", to=(0, y))
            return img

        off = grid(T.PANEL)
        box(off, T.BORDER, T.INPUT)
        on = grid(T.PANEL)
        box(on, T.ACCENT, T.ACCENT)
        seg(on, 4, 8, 7, 11, "#FFFFFF")
        seg(on, 7, 11, 12, 4, "#FFFFFF")
        return make(off), make(on)

    # -- styling -----------------------------------------------------------
    def _init_style(self) -> None:
        st = ttk.Style(self.master)
        st.theme_use("clam")
        base = ("Segoe UI", "Helvetica", "DejaVu Sans")
        self.f_base = tkfont.Font(family=base[0], size=10)
        self.f_small = tkfont.Font(family=base[0], size=9)
        self.f_h1 = tkfont.Font(family=base[0], size=19, weight="bold")
        self.f_sec = tkfont.Font(family=base[0], size=10, weight="bold")
        self.f_mono = tkfont.Font(family="JetBrains Mono", size=9)

        st.configure(".", background=T.BG, foreground=T.FG,
                     fieldbackground=T.INPUT, bordercolor=T.BORDER,
                     font=self.f_base, focuscolor=T.ACCENT)
        st.configure("App.TFrame", background=T.BG)
        st.configure("Panel.TFrame", background=T.PANEL)
        st.configure("TLabel", background=T.BG, foreground=T.FG)
        st.configure("On.TLabel", background=T.PANEL, foreground=T.FG)
        st.configure("Muted.TLabel", background=T.BG, foreground=T.MUTED,
                     font=self.f_small)
        st.configure("MutedOn.TLabel", background=T.PANEL,
                     foreground=T.MUTED, font=self.f_small)
        st.configure("H1.TLabel", background=T.BG, foreground=T.FG,
                     font=self.f_h1)
        st.configure("Sec.TLabel", background=T.BG, foreground=T.ACCENT,
                     font=self.f_sec)

        st.configure("Card.TLabelframe", background=T.PANEL,
                     bordercolor=T.BORDER, relief="solid", borderwidth=1)
        st.configure("Card.TLabelframe.Label", background=T.PANEL,
                     foreground=T.ACCENT, font=self.f_sec)

        st.configure("Hair.TSeparator", background=T.BORDER)

        for w in ("TCheckbutton", "TRadiobutton"):
            st.configure(w, background=T.PANEL, foreground=T.FG,
                         focuscolor=T.PANEL, indicatorforeground=T.FG,
                         indicatorbackground=T.INPUT, bordercolor=T.BORDER)
            st.map(
                w,
                background=[("active", T.PANEL)],
                foreground=[("disabled", T.MUTED)],
                # selected => accent fill + light check/dot (no more "X")
                indicatorbackground=[("selected", T.ACCENT),
                                     ("active", "#1F2630"),
                                     ("!selected", T.INPUT)],
                indicatorforeground=[("selected", "#FFFFFF")],
                bordercolor=[("selected", T.ACCENT),
                             ("focus", T.ACCENT)],
            )

        # Custom checkbox indicator: the clam theme draws an "✗" for a
        # selected checkbutton — replace it with a hand-drawn box that
        # shows a real ✓ tick when ticked. Used via "Switch.TCheckbutton".
        self._ck_off, self._ck_on = self._check_images()
        try:
            st.element_create("VPcheck", "image", self._ck_off,
                              ("selected", self._ck_on), sticky="")
            st.layout("Switch.TCheckbutton", [
                ("Checkbutton.padding", {"sticky": "nswe", "children": [
                    ("VPcheck", {"side": "left", "sticky": ""}),
                    ("Checkbutton.focus", {"side": "left", "sticky": "",
                     "children": [("Checkbutton.label", {"sticky": "nswe"})]}),
                ]}),
            ])
            st.configure("Switch.TCheckbutton", background=T.PANEL,
                         foreground=T.FG, font=self.f_base, padding=(0, 3))
            st.map("Switch.TCheckbutton",
                   background=[("active", T.PANEL)],
                   foreground=[("disabled", T.MUTED)])
        except tk.TclError:
            pass  # element already created (style re-init) — keep existing

        st.configure("TEntry", fieldbackground=T.INPUT, foreground=T.FG,
                     bordercolor=T.BORDER_INPUT, insertcolor=T.FG,
                     lightcolor=T.BORDER_INPUT, darkcolor=T.BORDER_INPUT,
                     relief="solid", borderwidth=1, padding=6)
        st.map("TEntry",
               bordercolor=[("focus", T.ACCENT)],
               lightcolor=[("focus", T.ACCENT)],
               darkcolor=[("focus", T.ACCENT)])

        st.configure("TCombobox", fieldbackground=T.INPUT, foreground=T.FG,
                     background=T.PANEL, bordercolor=T.BORDER_INPUT,
                     arrowcolor=T.FG, selectbackground=T.ACCENT,
                     selectforeground=T.FG, insertcolor=T.FG,
                     lightcolor=T.BORDER_INPUT, darkcolor=T.BORDER_INPUT,
                     relief="solid", borderwidth=1, padding=6)
        st.map("TCombobox",
               fieldbackground=[("readonly", T.INPUT), ("disabled", T.BG)],
               foreground=[("readonly", T.FG), ("disabled", T.MUTED)],
               bordercolor=[("focus", T.ACCENT)],
               lightcolor=[("focus", T.ACCENT)],
               darkcolor=[("focus", T.ACCENT)])
        # style the dropdown listbox (option_add must happen before any
        # combobox is shown, so we do it here at style-init time)
        self.master.option_add("*TCombobox*Listbox.background", T.INPUT)
        self.master.option_add("*TCombobox*Listbox.foreground", T.FG)
        self.master.option_add("*TCombobox*Listbox.selectBackground", T.ACCENT)
        self.master.option_add("*TCombobox*Listbox.selectForeground", T.FG)
        self.master.option_add("*TCombobox*Listbox.relief", "flat")

        st.map("TButton",
               foreground=[("active", "#000000")])

        st.configure("Accent.TButton", background=T.ACCENT,
                     foreground="#FFFFFF", borderwidth=0, focuscolor=T.ACCENT,
                     padding=(18, 9), font=self.f_sec)
        st.map("Accent.TButton",
               background=[("active", T.ACCENT_HOVER),
                           ("disabled", T.BORDER)],
               foreground=[("disabled", T.MUTED)])
        st.configure("Ghost.TButton", background=T.PANEL, foreground=T.FG,
                     bordercolor=T.BORDER, borderwidth=1, padding=(14, 8))
        st.map("Ghost.TButton",
               background=[("active", T.BORDER), ("disabled", T.BG)],
               foreground=[("disabled", T.MUTED)])
        st.configure("Danger.TButton", background="#C0392B",
                     foreground="#FFFFFF", borderwidth=0,
                     focuscolor="#C0392B", padding=(14, 8))
        st.map("Danger.TButton",
               background=[("active", "#E74C3C"), ("disabled", T.BORDER)],
               foreground=[("disabled", T.MUTED)])

        st.configure("Bar.Horizontal.TProgressbar", background=T.ACCENT,
                     troughcolor=T.INPUT, bordercolor=T.BORDER,
                     lightcolor=T.ACCENT, darkcolor=T.ACCENT, thickness=10)

        st.configure("Vertical.TScrollbar",
                     background=T.MUTED, troughcolor=T.PANEL,
                     bordercolor=T.PANEL, arrowcolor=T.FG,
                     lightcolor=T.BORDER, darkcolor=T.BORDER)
        st.map("Vertical.TScrollbar",
               background=[("active", "#A371F7"), ("pressed", "#8957E5")])

        st.configure("TNotebook", background=T.BG, borderwidth=1,
                     bordercolor=T.BORDER, tabmargins=(0, 0, 0, 0))
        st.configure("TNotebook.Tab", background=T.PANEL, foreground=T.MUTED,
                     padding=(16, 7), focuscolor=T.PANEL,
                     bordercolor=T.BORDER, lightcolor=T.BORDER)
        st.map("TNotebook.Tab",
               background=[("selected", T.BG), ("active", T.BORDER)],
               foreground=[("selected", T.FG), ("active", T.FG)],
               padding=[("selected", (16, 7))],
               lightcolor=[("selected", T.ACCENT)],
               bordercolor=[("selected", T.ACCENT)])

    # -- sections ----------------------------------------------------------
    def _build_header(self, p) -> None:
        h = ttk.Frame(p, style="App.TFrame")
        h.pack(fill="x")
        ttk.Label(h, text=_APP_NAME,
                  style="H1.TLabel").pack(anchor="w")
        import licensing
        style = ttk.Style()
        style.configure("License.TLabel", foreground="#50F726") 

        lic_text = f"Licensed to {licensing.LICENSED_TO}, valid till {licensing.EXPIRE_TIME_READABLE}"
        ttk.Label(h, text=lic_text, style="License.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Label(h, style="Muted.TLabel",
                text="Topic in, finished cinematic video out — script, "
                "voice, footage, render and metadata, fully automated."
                ).pack(anchor="w", pady=(2, 0))
        bar = tk.Frame(h, bg=T.ACCENT, height=3)
        bar.pack(fill="x", pady=(12, 0))

    def _card(self, p, title: str) -> ttk.Labelframe:
        lf = ttk.Labelframe(p, text="  " + title + "  ",
                            style="Card.TLabelframe")
        lf.pack(fill="x", pady=(16, 0), ipady=6)
        inner = ttk.Frame(lf, style="Panel.TFrame")
        inner.pack(fill="x", padx=14, pady=10)
        return inner

    def _build_content(self, p) -> None:
        c = self._card(p, "Content")

        ttk.Label(c, text="Video Topic", style="On.TLabel",
                  font=self.f_sec).grid(row=0, column=0, sticky="w")
        self.topic = ttk.Entry(c, width=70)
        self.topic.grid(row=1, column=0, sticky="we", pady=(4, 2))

        ttk.Label(c, text="Script Hints", style="On.TLabel",
                  font=self.f_sec).grid(row=2, column=0, sticky="w",
                                        pady=(14, 0))
        ttk.Label(c, style="MutedOn.TLabel",
                  text="Optional — paste raw story, bullet points or angle "
                  "notes. Left blank, the topic alone drives the script."
                  ).grid(row=3, column=0, sticky="w", pady=(1, 4))
        self.hint = tk.Text(c, height=5, wrap="word", relief="flat",
                            bg=T.INPUT, fg=T.FG, insertbackground=T.FG,
                            selectbackground=T.ACCENT, font=self.f_base,
                            highlightthickness=1, highlightbackground=T.BORDER_INPUT,
                            highlightcolor=T.ACCENT, padx=8, pady=6)
        self.hint.grid(row=4, column=0, sticky="we")
        c.columnconfigure(0, weight=1)
        self._build_voice_section(p)

    def _build_output(self, p) -> None:
        o = self._card(p, "Output Settings")

        ttk.Label(o, text="Video Orientation", style="On.TLabel").grid(
            row=0, column=0, sticky="w", pady=4)
        self.shape = tk.StringVar(value="landscape")
        ttk.Radiobutton(o, text="Landscape (long video)", variable=self.shape,
                        value="landscape").grid(row=0, column=1, sticky="w",
                                                padx=10)
        ttk.Radiobutton(o, text="Vertical (short video)", variable=self.shape,
                        value="vertical").grid(row=0, column=2, sticky="w",
                                               padx=10)

        ttk.Label(o, text="Quality", style="On.TLabel").grid(
            row=1, column=0, sticky="w", pady=4)
        self.preset = tk.StringVar(value="final")
        ttk.Radiobutton(o, text="Best — full 1080p", variable=self.preset,
                        value="final").grid(row=1, column=1, sticky="w",
                                            padx=10)
        ttk.Radiobutton(o, text="Quick look — rough & fast",
                        variable=self.preset, value="preview").grid(
            row=1, column=2, sticky="w", padx=10)

        ttk.Label(o, text="Length", style="On.TLabel").grid(
            row=2, column=0, sticky="w", pady=4)
        _prof_dur = _load_render_profile()
        self.duration_min = tk.IntVar(value=int(_prof_dur.get("duration_min", 6)))
        self.duration_sec = tk.IntVar(value=int(_prof_dur.get("duration_sec", 0)))
        dur_frame = ttk.Frame(o, style="App.TFrame")
        dur_frame.grid(row=2, column=1, columnspan=2, sticky="w", padx=10, pady=4)
        ttk.Spinbox(dur_frame, from_=0, to=60, increment=1, width=4,
                    textvariable=self.duration_min).pack(side="left")
        ttk.Label(dur_frame, text=" min ", style="On.TLabel").pack(side="left")
        ttk.Spinbox(dur_frame, values=(0, 10, 20, 30, 40, 50), width=4,
                    textvariable=self.duration_sec).pack(side="left")
        ttk.Label(dur_frame, text=" sec", style="On.TLabel").pack(side="left")
        ttk.Label(dur_frame, text="   approximate — the story is written to fit",
                  style="MutedOn.TLabel").pack(side="left")

        self.upload = tk.BooleanVar(value=False)
        self.review = tk.BooleanVar(value=False)
        self.sample = tk.BooleanVar(value=False)
        ttk.Checkbutton(o, variable=self.upload, style="Switch.TCheckbutton",
                        text="Upload to my YouTube (stays PRIVATE)").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(10, 1))
        ttk.Checkbutton(o, variable=self.review, style="Switch.TCheckbutton",
                        text="Let me read & approve the story before render"
                        ).grid(row=4, column=0, columnspan=3, sticky="w",
                               pady=1)
        ttk.Checkbutton(o, variable=self.sample, style="Switch.TCheckbutton",
                        text="Short sample first (opening only — faster)"
                        ).grid(row=5, column=0, columnspan=3, sticky="w",
                               pady=1)
        o.columnconfigure(2, weight=1)
        self._build_output_dir_section(p)
        self._build_render_section(p)
        self._build_metadata_section(p)

    def _build_output_dir_section(self, p) -> None:
        od = self._card(p, "Output Location")

        ttk.Label(od, text="Save Videos To", style="On.TLabel").grid(
            row=0, column=0, sticky="w", pady=4)

        prof = _load_render_profile()
        self.output_dir_var = tk.StringVar(value=prof.get("output_dir", ""))

        self.output_dir_entry = ttk.Entry(od, textvariable=self.output_dir_var, width=50)
        self.output_dir_entry.grid(row=0, column=1, sticky="ew", padx=10, pady=4)

        browse_btn = ttk.Button(od, text="Browse…", style="Ghost.TButton",
                                command=self.on_browse_output_dir)
        browse_btn.grid(row=0, column=2, sticky="w", pady=4)

        ttk.Label(od, text="Default: inside the project directory (output/)",
                  style="MutedOn.TLabel").grid(row=1, column=1, columnspan=2, sticky="w", pady=(0, 2))

        od.columnconfigure(1, weight=1)

    def on_browse_output_dir(self) -> None:
        import shutil
        import subprocess

        default_out = str(ROOT / "output")
        initial_dir = self.output_dir_var.get().strip() or default_out
        if not Path(initial_dir).exists():
            initial_dir = default_out

        chosen = ""

        if os.name != "nt":
            # 1. Try Zenity (modern GTK dialog, standard on Ubuntu/Debian/GNOME)
            if shutil.which("zenity"):
                cmd = ["zenity", "--file-selection", "--directory", "--title=Select Output Directory"]
                if Path(initial_dir).exists():
                    cmd += [f"--filename={initial_dir}/"]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0:
                    chosen = res.stdout.strip()
            # 2. Try Kdialog (modern KDE dialog)
            elif shutil.which("kdialog"):
                cmd = ["kdialog", "--getexistingdirectory", initial_dir, "--title", "Select Output Directory"]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0:
                    chosen = res.stdout.strip()

        # 3. Fallback to native Tkinter dialog (modern native dialog on Windows, fallback on Linux)
        if not chosen:
            from tkinter import filedialog
            chosen = filedialog.askdirectory(
                parent=self.master,
                title="Select Output Directory",
                initialdir=initial_dir
            )

        if chosen:
            chosen_path = Path(chosen).resolve()
            self.output_dir_var.set(str(chosen_path))

            # Save it immediately in the profile so the user doesn't lose it if they quit
            _save_render_profile({
                "add_music": self.add_music.get(),
                "highly_emotional": self.highly_emotional.get(),
                "output_dir": str(chosen_path),
            })

    def _build_render_section(self, p) -> None:
        r = self._card(p, "Rendering Settings")

        self.add_music = tk.BooleanVar(
            value=_load_render_profile().get("add_music", True))
        ttk.Checkbutton(r, variable=self.add_music,
                        style="Switch.TCheckbutton",
                        text="Add background music").grid(
            row=0, column=0, sticky="w", pady=(0, 2))
        ttk.Label(r,
                  text="uncheck to render voice-only (skips music stage)",
                  style="MutedOn.TLabel").grid(
            row=1, column=0, sticky="w", pady=(0, 2))
        r.columnconfigure(0, weight=1)

    def _build_voice_section(self, p) -> None:
        v = self._card(p, "Voice")

        ttk.Label(v, text="Voice", style="On.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 14), pady=4)
        self.voice_var = tk.StringVar(value="Leda")
        voice_cb = ttk.Combobox(v, textvariable=self.voice_var,
                                values=_GEMINI_VOICES, state="readonly", width=24)
        voice_cb.grid(row=0, column=1, sticky="w", pady=4)
        ttk.Label(v, text="Gemini prebuilt voice name",
                  style="MutedOn.TLabel").grid(row=0, column=2, sticky="w",
                                               padx=(10, 0))

        ttk.Label(v, text="Script Language", style="On.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 14), pady=4)
        self.lang_var = tk.StringVar(value=_LANG_LABELS[0])
        lang_cb = ttk.Combobox(v, textvariable=self.lang_var,
                               values=_LANG_LABELS, state="readonly", width=28)
        lang_cb.grid(row=1, column=1, sticky="w", pady=4)
        ttk.Label(v, text="spoken language for the Gemini TTS voice",
                  style="MutedOn.TLabel").grid(row=1, column=2, sticky="w",
                                               padx=(10, 0))

        _SUB_AUTO = "Auto (same as Script Language)"
        _sub_labels = [_SUB_AUTO] + _LANG_LABELS[1:]   # skip "Auto (detect)"
        ttk.Label(v, text="Subtitle Language", style="On.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 14), pady=4)
        self.sub_lang_var = tk.StringVar(value=_SUB_AUTO)
        sub_cb = ttk.Combobox(v, textvariable=self.sub_lang_var,
                              values=_sub_labels, state="readonly", width=28)
        sub_cb.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(v, text="on-screen text language (LLM prompts stay English)",
                  style="MutedOn.TLabel").grid(row=2, column=2, sticky="w",
                                               padx=(10, 0))

        self.highly_emotional = tk.BooleanVar(
            value=_load_render_profile().get("highly_emotional", True))
        ttk.Checkbutton(v, variable=self.highly_emotional,
                        style="Switch.TCheckbutton",
                        text="Highly emotional voice").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(10, 1))
        ttk.Label(v,
                  text="uncheck for flat / informational delivery",
                  style="MutedOn.TLabel").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(0, 2))

        v.columnconfigure(2, weight=1)

    def _build_metadata_section(self, p) -> None:
        m = self._card(p, "Meta Data")

        self.meta_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(m, variable=self.meta_enabled,
                        style="Switch.TCheckbutton",
                        text="Add metadata to exported video",
                        command=self._toggle_meta_fields
                        ).grid(row=0, column=0, columnspan=2, sticky="w",
                               pady=(0, 10))

        # Output Filename + Title are NOT persisted (auto-filled after each run)
        # Author / Copyright / Encoder ARE persisted
        fields = [
            ("Output Filename",  "meta_filename",  False, ""),
            ("Title",            "meta_title",     False, ""),
            ("Author / Artist",  "meta_author",    True,  "your name or channel name"),
            ("Copyright",        "meta_copyright", True,  "© 2026 Your Channel"),
            ("Encoder",          "meta_encoder",   True,  "software or studio name"),
        ]
        for i, (label, attr, _persisted, _ph) in enumerate(fields, start=1):
            ttk.Label(m, text=label, style="On.TLabel").grid(
                row=i, column=0, sticky="w", padx=(0, 14), pady=3)
            e = ttk.Entry(m)
            e.grid(row=i, column=1, sticky="we", pady=3)
            setattr(self, attr, e)

        row_cd = len(fields) + 1
        ttk.Label(m, text="Creation date", style="On.TLabel").grid(
            row=row_cd, column=0, sticky="w", padx=(0, 14), pady=(3, 0))
        ttk.Label(m, text="set automatically to export date",
                  style="MutedOn.TLabel").grid(
            row=row_cd, column=1, sticky="w", pady=(3, 0))

        self.set_meta_btn = ttk.Button(
            m, text="Set Metadata", style="Ghost.TButton",
            command=self.on_set_metadata, state="disabled")
        self.set_meta_btn.grid(row=row_cd + 1, column=0, columnspan=2,
                               sticky="e", pady=(12, 0))

        m.columnconfigure(1, weight=1)

        prof = _load_meta_profile()
        self.meta_enabled.set(prof.get("enabled", True))
        for attr in ("meta_author", "meta_copyright", "meta_encoder"):
            val = prof.get(attr.removeprefix("meta_"), "")
            if val:
                getattr(self, attr).insert(0, val)

        for _label, attr, _persisted, ph in fields:
            if ph:
                e = getattr(self, attr)
                self._placeholder_setup(e, ph)
                if not e.get():
                    e.insert(0, ph)
                    e.configure(foreground=T.MUTED)

        self._toggle_meta_fields()

    def _toggle_meta_fields(self) -> None:
        state = "normal" if self.meta_enabled.get() else "disabled"
        for attr in ("meta_filename", "meta_title", "meta_author",
                     "meta_copyright", "meta_encoder"):
            getattr(self, attr).configure(state=state)

    def _placeholder_setup(self, entry: ttk.Entry, hint: str) -> None:
        self._placeholders[entry] = hint

        def on_focus_in(_evt=None):
            if entry.get() == hint:
                entry.delete(0, "end")
                entry.configure(foreground=T.FG)

        def on_focus_out(_evt=None):
            if not entry.get().strip():
                entry.delete(0, "end")
                entry.insert(0, hint)
                entry.configure(foreground=T.MUTED)

        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)

    def _ph_val(self, entry: ttk.Entry) -> str:
        """Return entry value, or '' if the placeholder hint is showing."""
        val = entry.get().strip()
        return "" if val == self._placeholders.get(entry, "\x00") else val

    def _build_actions(self, p) -> None:
        bar = ttk.Frame(p, style="App.TFrame")
        bar.pack(fill="x")
        self._action_bar = bar
        self.run_btn = ttk.Button(bar, text="Create Video",
                                  style="Accent.TButton", command=self.on_run)
        self.run_btn.pack(side="left")
        self.approve_btn = ttk.Button(bar, text="Approve & Continue",
                                      style="Ghost.TButton",
                                      command=self.on_approve,
                                      state="disabled")
        self.approve_btn.pack(side="left", padx=10)
        ttk.Button(bar, text="Quit", style="Danger.TButton",
                   command=self.master.destroy).pack(side="right")
        self.open_btn = ttk.Button(bar, text="Open Output Folder",
                                   style="Ghost.TButton",
                                   command=self.on_open_folder)
        # packed dynamically when a render completes successfully

    def _build_status(self, p) -> None:
        row = ttk.Frame(p, style="App.TFrame")
        row.pack(fill="x", pady=(14, 0))
        for k, v in _detect_keys().items():
            chip = ttk.Frame(row, style="App.TFrame")
            chip.pack(side="left", padx=(0, 16))
            tk.Label(chip, text="●", bg=T.BG, font=self.f_small,
                     fg=(T.OK if v else T.OFF)).pack(side="left")
            ttk.Label(chip, text=" " + k, style="Muted.TLabel").pack(
                side="left")
        ttk.Label(p, style="Muted.TLabel",
                  text="● configured   ● not set → that stage uses an "
                  "offline fallback. YouTube needs all three YT_* values."
                  ).pack(anchor="w", pady=(6, 0))

    def _build_progress(self, p) -> None:
        head = ttk.Frame(p, style="App.TFrame")
        head.pack(fill="x", pady=(16, 4))
        ttk.Label(head, text="PROGRESS", style="Sec.TLabel").pack(side="left")
        self.pct_lbl = ttk.Label(head, text="0%", style="TLabel",
                                  font=self.f_sec)
        self.pct_lbl.pack(side="right")
        self.stage_lbl = ttk.Label(head, text="idle", style="Muted.TLabel")
        self.stage_lbl.pack(side="right", padx=10)
        self.bar = ttk.Progressbar(p, style="Bar.Horizontal.TProgressbar",
                                   mode="determinate", maximum=100)
        self.bar.pack(fill="x")

    def _build_log(self, p) -> None:
        ttk.Label(p, text="DETAILED LOG", style="Sec.TLabel").pack(
            anchor="w", pady=(16, 4))
        self.log = scrolledtext.ScrolledText(
            p, height=14, state="disabled", wrap="word", relief="flat",
            bg=T.INPUT, fg=T.MUTED, insertbackground=T.FG,
            selectbackground=T.ACCENT, font=self.f_mono,
            highlightthickness=1, highlightbackground=T.BORDER, padx=10,
            pady=8)
        self.log.pack(fill="both", expand=True)

    # -- logging + progress ------------------------------------------------
    def _append(self, s: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", s)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _cancel(self, attr: str) -> None:
        job = getattr(self, attr, None)
        if job is not None:
            try:
                self.master.after_cancel(job)
            except Exception:
                pass
            setattr(self, attr, None)

    def _set_pct(self, pct: int, stage: str | None = None) -> None:
        """Set the target %. Forward-only; the bar then animates to it so
        every intermediate number is shown (no 68 -> 90 jumps)."""
        pct = max(0, min(100, int(pct)))
        if stage:
            self.stage_lbl.configure(text=stage)
        if pct <= self._pct:
            return
        self._pct = pct
        self._cancel("_creep_job")     # a real milestone supersedes the creep
        self._animate()

    def _animate(self) -> None:
        """Step the displayed % one unit toward the target, ~60 fps."""
        self._cancel("_anim_job")
        if self._disp < self._pct:
            self._disp += 1
            self.bar.configure(value=self._disp)
            self.pct_lbl.configure(
                text=f"{self._disp}%",
                foreground=(T.OK if self._disp >= 100 else T.FG))
            self._anim_job = self.master.after(16, self._animate)

    def _creep(self, ceiling: int, period: int = 2000) -> None:
        """Gently raise the target during a long, log-silent step (render
        emits nothing between 'master:' and 'render ->'). Walks +1 every
        `period` ms up to `ceiling`; a real milestone cancels it."""
        self._cancel("_creep_job")

        def step():
            if self._pct < ceiling:
                self._pct += 1
                self._animate()
                self._creep_job = self.master.after(period, step)
            else:
                self._creep_job = None

        self._creep_job = self.master.after(period, step)

    def _scan(self, line: str) -> None:
        s = line.strip()
        _ae = re.match(r'\[vp\] \[API_ERROR:([A-Z_]+)\] (.*)', s)
        if _ae:
            self._api_error = (str(_ae.group(1)), str(_ae.group(2)))
        if s.startswith("[vp] output dir:") or " output dir:" in s:
            try:
                self.out_dir = Path(s.split("output dir:", 1)[1].strip())
            except Exception:
                pass
        if "DONE — deliverable at " in s:
            try:
                self.video_path = Path(s.split("DONE — deliverable at ", 1)[1].strip())
            except Exception:
                pass
        if "REVIEW_REQUIRED " in s:
            self.review_script = Path(s.split("REVIEW_REQUIRED ", 1)[1].strip())
            self.review_pending = True
            self._set_pct(12, "Awaiting your review")
            return
        m = _RE_CHAPTER.search(s)
        if m:
            i, n = int(m.group(1)), max(1, int(m.group(2)))
            self._set_pct(16 + int(26 * i / n),
                          f"Directing chapter {i}/{n}…")
            return
        for needle, pct, stage in _MILESTONES:
            if needle in s:
                self._set_pct(pct, stage)
                # render is the long, log-silent step: creep the % up
                # gently (≈68 -> 89) instead of freezing until it returns
                if needle == "master:":
                    self.stage_lbl.configure(
                        text="Rendering 1080p — longest step…")
                    self._creep(89)
                break

    def _drain(self) -> None:
        try:
            while True:
                line = self.q.get_nowait()
                self._scan(line)
                self._append(line)
        except queue.Empty:
            pass
        self.master.after(120, self._drain)

    # -- build command -----------------------------------------------------
    def _argv(self) -> list[str] | None:
        topic = self.topic.get().strip()
        if not topic:
            messagebox.showwarning("Missing topic",
                                   "Please enter a Video Topic.")
            return None
        dur_min = self.duration_min.get()
        dur_sec = self.duration_sec.get()
        total_minutes = dur_min + dur_sec / 60.0
        if total_minutes <= 0:
            messagebox.showwarning("Bad length",
                                   "Length must be at least 10 seconds.\n"
                                   "Set minutes and/or seconds above zero.")
            return None
        mins = f"{total_minutes:.6g}"
        argv = [sys.executable, "-m", "vp.run", topic,
                "--preset", self.preset.get(), "--minutes", mins,
                "--shape", self.shape.get()]
        out_dir = self.output_dir_var.get().strip()
        if out_dir:
            argv += ["--output-dir", out_dir]
        hint = self.hint.get("1.0", "end").strip()
        if hint:
            argv += ["--hint", hint]
        if not self.review.get():
            argv.append("--approve")
        if not self.upload.get():
            argv.append("--no-upload")
        if self.sample.get():
            argv += ["--segments", SAMPLE_SEGMENTS]
        if not self.add_music.get():
            argv.append("--no-music")
        argv.append("--highly-emotional" if self.highly_emotional.get()
                    else "--no-highly-emotional")
        if self._resuming:
            argv.append("--resume")
        voice = self.voice_var.get().strip()
        if voice and voice != "Leda":
            argv += ["--voice", voice]
        lang_code = _LANG_LABEL_TO_CODE.get(self.lang_var.get(), "")
        if lang_code:
            argv += ["--language", lang_code]
        sub_label = self.sub_lang_var.get()
        # only pass when the user explicitly chose a language (not "Auto")
        if not sub_label.startswith("Auto"):
            sub_code = _LANG_LABEL_TO_CODE.get(sub_label, "")
            if sub_code:
                argv += ["--subtitle-language", sub_code]
        return argv

    # -- run ---------------------------------------------------------------
    def on_run(self) -> None:
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("Busy", "A render is already running.")
            return
        _save_meta_profile({
            "enabled": self.meta_enabled.get(),
            "author": self._ph_val(self.meta_author),
            "copyright": self._ph_val(self.meta_copyright),
            "encoder": self._ph_val(self.meta_encoder),
        })
        _save_render_profile({
            "add_music": self.add_music.get(),
            "highly_emotional": self.highly_emotional.get(),
            "output_dir": self.output_dir_var.get().strip(),
            "duration_min": self.duration_min.get(),
            "duration_sec": self.duration_sec.get(),
        })
        argv = self._argv()
        if not argv:
            return
        self.out_dir = None
        self.video_path = None
        self.review_script = None
        self.review_pending = False
        self._api_error = None
        self._last_rc = None
        self._pct = 0
        self._disp = 0
        self._cancel("_anim_job")
        self._cancel("_creep_job")
        self.bar.configure(value=0)
        self.pct_lbl.configure(text="0%", foreground=T.FG)
        self.stage_lbl.configure(text="starting…")
        self.approve_btn.configure(state="disabled")
        self.set_meta_btn.configure(state="disabled")
        self.open_btn.pack_forget()
        self.last_argv = argv
        self._launch(argv)

    def on_open_folder(self) -> None:
        if not self.out_dir or not self.out_dir.exists():
            messagebox.showwarning("Not found", "Output folder not found.")
            return
        if os.name == "nt":
            os.startfile(str(self.out_dir))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(self.out_dir)])
        else:
            subprocess.Popen(["xdg-open", str(self.out_dir)])

    def on_approve(self) -> None:
        if not (self.out_dir and self.review_script):
            messagebox.showwarning(
                "Nothing to approve", "No script is awaiting review yet.")
            return
        try:
            (self.out_dir / "script.APPROVED").write_text("approved\n",
                                                          encoding="utf-8")
        except Exception as e:
            messagebox.showerror("Could not approve", str(e))
            return
        self.review_pending = False
        self.approve_btn.configure(state="disabled")
        self._append("\n--- approved; continuing ---\n")
        self._launch(self.last_argv)

    def _launch(self, argv: list[str]) -> None:
        _save_resume_state({
            "topic": self.topic.get().strip(),
            "preset": self.preset.get(),
            "shape": self.shape.get(),
            "duration_min": self.duration_min.get(),
            "duration_sec": self.duration_sec.get(),
            "hint": self.hint.get("1.0", "end").strip(),
            "upload": self.upload.get(),
            "review": self.review.get(),
            "sample": self.sample.get(),
            "voice": self.voice_var.get(),
            "language_label": self.lang_var.get(),
            "subtitle_language_label": self.sub_lang_var.get(),
        })
        self.run_btn.configure(state="disabled", text="Running…")
        shown = list(argv)
        if "--hint" in shown:                       # keep echo readable
            i = shown.index("--hint")
            h = shown[i + 1].replace("\n", " ")
            shown[i + 1] = (h[:60] + "…") if len(h) > 60 else h
        self._start_time = time.time()
        ts = time.strftime("%H:%M:%S", time.localtime(self._start_time))
        self._append(f"\n$ {' '.join(shown)}\n")
        self._append(f"Started at {ts}\n\n")
        threading.Thread(target=self._worker, args=(argv,),
                         daemon=True).start()

    def _worker(self, argv: list[str]) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        # Windows: the child prints UTF-8 (✅ ● → …); without this the
        # default console codepage (cp1252) garbles or crashes the stream.
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            if _logger:
                _logger.info("launching: %s", " ".join(argv))
            self.proc = subprocess.Popen(
                argv, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                encoding="utf-8", errors="replace",
            )
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self.q.put(line)
            self.proc.wait()
            rc = self.proc.returncode
            self._last_rc = rc
            if rc not in (0, None) and _logger:
                _logger.error("pipeline exited with code %d | cmd: %s",
                              rc, " ".join(argv))
        except Exception as e:  # pragma: no cover
            self.q.put(f"\n[launcher error] {e}\n")
            if _logger:
                _logger.error("worker exception:\n%s", traceback.format_exc())
        finally:
            self.master.after(0, self._finished)

    def _finished(self) -> None:
        # drain any lines that arrived after the last _drain() tick
        try:
            while True:
                line = self.q.get_nowait()
                self._scan(line)
                self._append(line)
        except queue.Empty:
            pass

        self.run_btn.configure(state="normal", text="Create Video")
        if self._start_time is not None:
            elapsed = time.time() - self._start_time
            m, s = divmod(int(elapsed), 60)
            ts = time.strftime("%H:%M:%S")
            self._append(f"\nFinished at {ts} — Total time: {m}m {s}s\n")
            self._start_time = None
        if self.review_pending and self.review_script \
                and self.review_script.exists():
            self.approve_btn.configure(state="normal")
            self._append(
                "\n================ STORY FOR YOUR REVIEW ================\n")
            try:
                self._append(self.review_script.read_text(encoding="utf-8"))
            except Exception:
                pass
            self._append(
                f"\n======================================================\n"
                f"Edit {self.review_script} if you want, then press "
                f"'Approve & Continue'.\n")
        else:
            self._cancel("_creep_job")
            done = self._pct >= 100
            if done:
                _clear_resume_state()
                self._set_pct(100, "Finished")
                self._post_run_meta_fill()
                final = str(self.video_path) if self.video_path else (
                    f"{self.out_dir}/{self.out_dir.name}.mp4"
                    if self.out_dir else "output/<slug>/<slug>.mp4")
                self._append(f"\n✅ Finished — see {final}\n")
                self.open_btn.pack(side="right", padx=(0, 10))
            else:
                self.stage_lbl.configure(text="Stopped")
                self._append("\n■ Process ended.\n")
                if self._api_error:
                    err_type, err_detail = self._api_error
                    self._api_error = None
                    self.master.after(
                        100, lambda t=err_type, d=err_detail:
                        self._show_api_error(t, d))
                elif self._last_rc not in (0, None):
                    self.master.after(100, self._show_process_error)

    _API_ERROR_MESSAGES: dict[str, tuple[str, str]] = {
        "KEY_NOT_SET": (
            "API Key Not Configured",
            "An API key is missing from your .env file.\n\n"
            "{detail}\n\n"
            "Steps to fix:\n"
            "  1. Open the .env file in the project folder\n"
            "  2. Add the missing key (e.g.  ANTHROPIC_API_KEY=sk-ant-…)\n"
            "  3. Save the file and click Create Video again.",
        ),
        "ANTHROPIC_KEY_INVALID": (
            "Anthropic API Key Invalid",
            "Your Anthropic API key was rejected (invalid or expired).\n\n"
            "Fix:\n"
            "  1. Open your .env file\n"
            "  2. Update ANTHROPIC_API_KEY with a valid key\n"
            "  3. Get or rotate keys at: console.anthropic.com → API Keys",
        ),
        "ANTHROPIC_CREDITS": (
            "Anthropic Credits Insufficient",
            "Your Anthropic account has insufficient credits to complete "
            "this request.\n\n"
            "Fix: add credits at console.anthropic.com → Billing",
        ),
        "GROQ_KEY_INVALID": (
            "Groq API Key Invalid",
            "Your Groq API key was rejected (invalid or expired).\n\n"
            "Fix:\n"
            "  1. Open your .env file\n"
            "  2. Update GROQ_API_KEY with a valid key\n"
            "  3. Get or rotate keys at: console.groq.com",
        ),
        "GROQ_CREDITS": (
            "Groq Credits/Rate Limit Exceeded",
            "Your Groq account has insufficient credits or has been rate limited.\n\n"
            "Fix: check your billing and limits at console.groq.com",
        ),
        "GEMINI_KEY_INVALID": (
            "Gemini API Key Invalid",
            "Your Gemini API key was rejected by Google (invalid or expired).\n\n"
            "Fix:\n"
            "  1. Open your .env file\n"
            "  2. Update GEMINI_API_KEY with a valid key\n"
            "  3. Get or rotate keys at: aistudio.google.com/app/apikey",
        ),
        "GEMINI_QUOTA": (
            "Gemini API Quota Exhausted",
            "All your Gemini API keys hit their rate limit or daily quota.\n\n"
            "Options:\n"
            "  • Wait and retry (quotas reset hourly or daily)\n"
            "  • Add more keys: GEMINI_API_KEY_1, GEMINI_API_KEY_2, …\n"
            "  • Get additional keys at: aistudio.google.com/app/apikey",
        ),
        "LLM_ALL_FAILED": (
            "All AI Providers Failed",
            "Both Anthropic and Gemini failed to respond.\n\n"
            "Possible causes:\n"
            "  • No internet connection\n"
            "  • Both API keys are invalid or expired\n"
            "  • A temporary service outage\n\n"
            "Check your .env file and try again.",
        ),
    }

    def _show_api_error(self, err_type: str, detail: str) -> None:
        title, body_tmpl = self._API_ERROR_MESSAGES.get(
            err_type,
            ("API Error", "An API error stopped the pipeline.\n\n{detail}"),
        )
        messagebox.showerror(title, body_tmpl.format(detail=detail))

    def _show_process_error(self) -> None:
        messagebox.showerror(
            "Video Production Failed",
            "Something went wrong and the pipeline stopped.\n\n"
            "Check the detailed log above for the specific error message.\n\n"
            "Common causes:\n"
            "  • A temporary API server error (just try again)\n"
            "  • Internet connection dropped mid-run\n"
            "  • A file permission or disk space issue",
        )

    def _post_run_meta_fill(self) -> None:
        title = ""

        if self.out_dir:
            meta_json = self.out_dir / "metadata.json"
            try:
                meta = json.loads(meta_json.read_text(encoding="utf-8"))
                title = meta.get("title", "")
            except Exception:
                pass

            if title:
                safe = _safe_filename(title)
                # auto-fill Title and Filename (not persisted)
                for attr, val in (("meta_title", title), ("meta_filename", safe)):
                    e = getattr(self, attr)
                    e.configure(state="normal")
                    e.delete(0, "end")
                    e.insert(0, val)
                if not self.meta_enabled.get():
                    self.meta_title.configure(state="disabled")
                    self.meta_filename.configure(state="disabled")

                # rename <slug>.mp4 → <safe title>.mp4 if needed
                if self.video_path and self.video_path.exists():
                    named = self.video_path.parent / f"{safe}.mp4"
                    if named != self.video_path:
                        try:
                            self.video_path.rename(named)
                            self.video_path = named
                        except Exception:
                            pass

        # auto-embed if checkbox is ticked and we have a video
        if self.meta_enabled.get() and self.video_path \
                and self.video_path.exists():
            try:
                _embed_mp4_metadata(
                    self.video_path,
                    title=title or None,
                    artist=self._ph_val(self.meta_author) or None,
                    copyright=self._ph_val(self.meta_copyright) or None,
                    encoder=self._ph_val(self.meta_encoder) or None,
                )
                self._append("\n✅ Metadata embedded automatically.\n")
            except Exception as exc:
                self._append(f"\n[warn] metadata embed failed: {exc}\n")

        self.set_meta_btn.configure(state="normal")

    def on_set_metadata(self) -> None:
        if not self.video_path or not self.video_path.exists():
            messagebox.showwarning("No video",
                                   "No completed video found in this session.")
            return

        new_name = _safe_filename(self.meta_filename.get().strip())
        new_path = self.video_path.parent / f"{new_name}.mp4"

        try:
            # rename if filename changed
            if new_path != self.video_path:
                self.video_path.rename(new_path)
                self.video_path = new_path

            _embed_mp4_metadata(
                self.video_path,
                title=self.meta_title.get().strip() or None,
                artist=self._ph_val(self.meta_author) or None,
                copyright=self._ph_val(self.meta_copyright) or None,
                encoder=self._ph_val(self.meta_encoder) or None,
            )

            _save_meta_profile({
                "enabled": self.meta_enabled.get(),
                "author": self._ph_val(self.meta_author),
                "copyright": self._ph_val(self.meta_copyright),
                "encoder": self._ph_val(self.meta_encoder),
            })

            messagebox.showinfo(
                "Metadata set",
                f"Metadata embedded successfully.\n\n{self.video_path.name}")
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to set metadata:\n{exc}")


def _set_window_icon(root: tk.Tk) -> None:
    if os.name == "nt":
        # iconbitmap(.ico) is the only Tkinter call that sets the taskbar
        # icon on Windows; wm_iconphoto only affects the title bar.
        ico_path = ROOT / "assets" / "icon.ico"
        if ico_path.exists():
            try:
                root.iconbitmap(str(ico_path))
                return
            except Exception:
                pass
    png_path = ROOT / "assets" / "icon.png"
    if not png_path.exists():
        return
    try:
        from PIL import Image, ImageTk
        img = Image.open(png_path)
        photo = ImageTk.PhotoImage(img)
        root.wm_iconphoto(True, photo)
        root._icon_ref = photo  # prevent GC
    except Exception:
        pass

def _set_window_icon(root: tk.Tk) -> None:
    """Loads and sets the window icon safely using an absolute path."""
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(current_dir, "assets", "icon.png")
        
        if os.path.exists(icon_path):
            icon_image = tk.PhotoImage(file=icon_path)
            # True means this icon applies to this window and all future popups
            root.iconphoto(True, icon_image) 
            root._icon_image = icon_image  # type: ignore
        else:
            if _logger:
                _logger.warning("Icon file missing at path: %s", icon_path)
    except Exception as e:
        if _logger:
            _logger.error("Failed to load window icon: %s", str(e))
            

def main() -> int:
    import licensing; licensing.enforce()
    if os.name == "nt":
        # Prevent Windows from grouping this window with other pythonw.exe
        # processes on the taskbar; must be called before the Tk window exists.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "VideoProductionStudio.Launcher.1")
        except Exception:
            pass
    try:
        app_name = os.environ.get("APP_NAME", "Video Studio")
        root = tk.Tk(className=app_name)
        root.title(app_name)
        
        _set_window_icon(root)
        App(root)
        root.mainloop()
        return 0
    except Exception:
        tb = traceback.format_exc()
        if _logger:
            _logger.error("unhandled exception in main:\n%s", tb)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
