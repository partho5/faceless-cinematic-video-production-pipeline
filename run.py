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

import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
ENV_FILE = ROOT / ".env"
SAMPLE_SEGMENTS = "6"  # "short sample" cap


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


# ----------------------------------------------------------------- theme ----
class T:
    BG = "#0E1116"          # app background
    PANEL = "#161B22"       # section panels
    INPUT = "#0D1117"       # entry / text fields
    FG = "#E6EDF3"          # primary text
    MUTED = "#8B949E"       # secondary text
    BORDER = "#30363D"      # hairlines / field borders
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
        "Claude · script": has("ANTHROPIC_API_KEY"),
        "Gemini · voice": has("GEMINI_API_KEY", "GEMINI_API_KEY_1"),
        "Pexels · footage": has("PEXELS_API_KEY"),
        "YouTube · upload": has("YT_CLIENT_ID") and has("YT_CLIENT_SECRET")
        and has("YT_REFRESH_TOKEN"),
    }


class App:
    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        self.proc: subprocess.Popen | None = None
        self.q: queue.Queue[str] = queue.Queue()
        self.last_argv: list[str] = []
        self.out_dir: Path | None = None
        self.review_script: Path | None = None
        self.review_pending = False
        self._pct = 0          # logical target %
        self._disp = 0         # currently displayed % (animated toward target)
        self._anim_job = None
        self._creep_job = None

        master.title("Video Production Studio")
        master.geometry("880x860")
        # small min size: the whole UI is scrollable, so it stays usable
        # (nothing hidden) even when the window is shrunk a lot.
        master.minsize(560, 380)
        master.configure(bg=T.BG)

        self._init_style()
        body = self._scrollable(master)

        self._build_header(body)
        self._build_content(body)
        self._build_output(body)
        ttk.Separator(body, style="Hair.TSeparator").pack(
            fill="x", pady=(14, 12))
        self._build_actions(body)
        self._build_status(body)
        self._build_progress(body)
        self._build_log(body)

        self.master.after(120, self._drain)

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
                     bordercolor=T.BORDER, insertcolor=T.FG,
                     lightcolor=T.BORDER, darkcolor=T.BORDER, padding=6)
        st.map("TEntry", bordercolor=[("focus", T.ACCENT)])

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

        st.configure("Bar.Horizontal.TProgressbar", background=T.ACCENT,
                     troughcolor=T.INPUT, bordercolor=T.BORDER,
                     lightcolor=T.ACCENT, darkcolor=T.ACCENT, thickness=10)

    # -- sections ----------------------------------------------------------
    def _build_header(self, p) -> None:
        h = ttk.Frame(p, style="App.TFrame")
        h.pack(fill="x")
        ttk.Label(h, text="Video Production Studio",
                  style="H1.TLabel").pack(anchor="w")
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
                            highlightthickness=1, highlightbackground=T.BORDER,
                            highlightcolor=T.ACCENT, padx=8, pady=6)
        self.hint.grid(row=4, column=0, sticky="we")
        c.columnconfigure(0, weight=1)

    def _build_output(self, p) -> None:
        o = self._card(p, "Output Settings")

        ttk.Label(o, text="Quality", style="On.TLabel").grid(
            row=0, column=0, sticky="w", pady=4)
        self.preset = tk.StringVar(value="final")
        ttk.Radiobutton(o, text="Best — full 1080p", variable=self.preset,
                        value="final").grid(row=0, column=1, sticky="w",
                                            padx=10)
        ttk.Radiobutton(o, text="Quick look — rough & fast",
                        variable=self.preset, value="preview").grid(
            row=0, column=2, sticky="w", padx=10)

        ttk.Label(o, text="Length (minutes)", style="On.TLabel").grid(
            row=1, column=0, sticky="w", pady=4)
        self.minutes = ttk.Entry(o, width=8)
        self.minutes.insert(0, "6")
        self.minutes.grid(row=1, column=1, sticky="w", padx=10, pady=4)
        ttk.Label(o, text="approximate — the story is written to fit",
                  style="MutedOn.TLabel").grid(row=1, column=2, columnspan=2,
                                               sticky="w")

        self.upload = tk.BooleanVar(value=False)
        self.review = tk.BooleanVar(value=False)
        self.sample = tk.BooleanVar(value=False)
        ttk.Checkbutton(o, variable=self.upload, style="Switch.TCheckbutton",
                        text="Upload to my YouTube (stays PRIVATE)").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(10, 1))
        ttk.Checkbutton(o, variable=self.review, style="Switch.TCheckbutton",
                        text="Let me read & approve the story before render"
                        ).grid(row=3, column=0, columnspan=3, sticky="w",
                               pady=1)
        ttk.Checkbutton(o, variable=self.sample, style="Switch.TCheckbutton",
                        text="Short sample first (opening only — faster)"
                        ).grid(row=4, column=0, columnspan=3, sticky="w",
                               pady=1)
        o.columnconfigure(2, weight=1)

    def _build_actions(self, p) -> None:
        bar = ttk.Frame(p, style="App.TFrame")
        bar.pack(fill="x")
        self.run_btn = ttk.Button(bar, text="Create Video",
                                  style="Accent.TButton", command=self.on_run)
        self.run_btn.pack(side="left")
        self.approve_btn = ttk.Button(bar, text="Approve & Continue",
                                      style="Ghost.TButton",
                                      command=self.on_approve,
                                      state="disabled")
        self.approve_btn.pack(side="left", padx=10)
        ttk.Button(bar, text="Quit", style="Ghost.TButton",
                   command=self.master.destroy).pack(side="right")

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
        if s.startswith("[vp] output dir:") or " output dir:" in s:
            try:
                self.out_dir = Path(s.split("output dir:", 1)[1].strip())
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
        mins = self.minutes.get().strip() or "6"
        try:
            if float(mins) <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Bad length",
                                   "Length must be a positive number.")
            return None
        argv = [sys.executable, "-m", "vp.run", topic,
                "--preset", self.preset.get(), "--minutes", mins]
        hint = self.hint.get("1.0", "end").strip()
        if hint:
            argv += ["--hint", hint]
        if not self.review.get():
            argv.append("--approve")
        if not self.upload.get():
            argv.append("--no-upload")
        if self.sample.get():
            argv += ["--segments", SAMPLE_SEGMENTS]
        return argv

    # -- run ---------------------------------------------------------------
    def on_run(self) -> None:
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("Busy", "A render is already running.")
            return
        argv = self._argv()
        if not argv:
            return
        self.out_dir = None
        self.review_script = None
        self.review_pending = False
        self._pct = 0
        self._disp = 0
        self._cancel("_anim_job")
        self._cancel("_creep_job")
        self.bar.configure(value=0)
        self.pct_lbl.configure(text="0%", foreground=T.FG)
        self.stage_lbl.configure(text="starting…")
        self.approve_btn.configure(state="disabled")
        self.last_argv = argv
        self._launch(argv)

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
        self.run_btn.configure(state="disabled", text="Running…")
        shown = list(argv)
        if "--hint" in shown:                       # keep echo readable
            i = shown.index("--hint")
            h = shown[i + 1].replace("\n", " ")
            shown[i + 1] = (h[:60] + "…") if len(h) > 60 else h
        self._append(f"\n$ {' '.join(shown)}\n\n")
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
            self.proc = subprocess.Popen(
                argv, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                encoding="utf-8", errors="replace",
            )
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self.q.put(line)
            self.proc.wait()
        except Exception as e:  # pragma: no cover
            self.q.put(f"\n[launcher error] {e}\n")
        finally:
            self.master.after(0, self._finished)

    def _finished(self) -> None:
        self.run_btn.configure(state="normal", text="Create Video")
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
                self._set_pct(100, "Finished")
            else:
                self.stage_lbl.configure(text="Stopped")
            self._append(
                "\n✅ Finished — see output/<slug>/final.mp4\n" if done
                else "\n■ Process ended.\n")


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
