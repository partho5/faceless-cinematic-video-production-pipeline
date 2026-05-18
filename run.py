#!/usr/bin/env python3
"""Easy-run GUI launcher.

    python run.py

Opens a small window: fill the core fields, optionally tweak the advanced
section, tick "auto-upload" if you want it on YouTube, press Create Video.
The full pipeline (src/vp/run.py) runs in a subprocess and its progress is
streamed live into the log pane — the window never freezes.

Pure standard library (Tkinter). No extra dependencies. On minimal Linux
you may need the system package `python3-tk`.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
ENV_FILE = ROOT / ".env"

try:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, ttk
except Exception:  # pragma: no cover - environment without Tk
    sys.stderr.write(
        "Tkinter is not available. Install it (e.g. `sudo apt install "
        "python3-tk`) or use the CLI:\n"
        '  python -m vp.run "Your topic" --approve --preset final\n'
    )
    sys.exit(1)


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
        "Claude (script)": has("ANTHROPIC_API_KEY"),
        "Gemini (voice)": has("GEMINI_API_KEY", "GEMINI_API_KEY_1"),
        "Pexels (footage)": has("PEXELS_API_KEY"),
        "YouTube (upload)": has("YT_CLIENT_ID") and has("YT_CLIENT_SECRET")
        and has("YT_REFRESH_TOKEN"),
    }


class App:
    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        self.proc: subprocess.Popen | None = None
        self.q: queue.Queue[str] = queue.Queue()
        master.title("Video Production Pipeline")
        master.geometry("760x640")

        pad = dict(padx=8, pady=4)

        # ---------------- Core ------------------------------------------
        core = ttk.LabelFrame(master, text="Core")
        core.pack(fill="x", **pad)

        ttk.Label(core, text="Topic *").grid(row=0, column=0, sticky="w",
                                              padx=6, pady=6)
        self.topic = ttk.Entry(core, width=70)
        self.topic.grid(row=0, column=1, columnspan=3, sticky="we",
                        padx=6, pady=6)

        ttk.Label(core, text="Quality").grid(row=1, column=0, sticky="w",
                                             padx=6)
        self.preset = tk.StringVar(value="final")
        ttk.Radiobutton(core, text="Final 1080p (production)",
                        variable=self.preset, value="final").grid(
            row=1, column=1, sticky="w")
        ttk.Radiobutton(core, text="Preview (fast 540p)",
                        variable=self.preset, value="preview").grid(
            row=1, column=2, sticky="w")

        self.approve = tk.BooleanVar(value=True)
        ttk.Checkbutton(core, text="Auto-approve script (no manual gate)",
                        variable=self.approve).grid(
            row=2, column=1, sticky="w", pady=2)

        self.upload = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            core, text="Auto-upload to YouTube (forced PRIVATE)",
            variable=self.upload).grid(row=3, column=1, sticky="w", pady=2)
        core.columnconfigure(1, weight=1)

        # ---------------- Optional --------------------------------------
        opt = ttk.LabelFrame(master, text="Optional / Advanced")
        opt.pack(fill="x", **pad)

        ttk.Label(opt, text="Segment limit").grid(row=0, column=0, sticky="w",
                                                  padx=6, pady=4)
        self.segments = ttk.Entry(opt, width=8)
        self.segments.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(opt, text="(blank = all; small number = quick test)").grid(
            row=0, column=2, sticky="w")

        ttk.Label(opt, text="TTS scene").grid(row=1, column=0, sticky="w",
                                              padx=6, pady=4)
        self.scene = ttk.Entry(opt, width=64)
        self.scene.grid(row=1, column=1, columnspan=2, sticky="we", padx=6)

        ttk.Label(opt, text="TTS context").grid(row=2, column=0, sticky="w",
                                                padx=6, pady=4)
        self.context = ttk.Entry(opt, width=64)
        self.context.grid(row=2, column=1, columnspan=2, sticky="we", padx=6)
        ttk.Label(
            opt, text="(voice steering for non-default niches; blank = "
            "channel default)").grid(row=3, column=1, columnspan=2,
                                     sticky="w", padx=6)
        opt.columnconfigure(1, weight=1)

        # ---------------- Status ----------------------------------------
        keys = _detect_keys()
        txt = "  ".join(f"{'●' if v else '○'} {k}" for k, v in keys.items())
        ttk.Label(master, text="Configured: " + txt,
                  foreground="#444").pack(anchor="w", padx=12)
        ttk.Label(
            master,
            text="○ = missing in .env → that stage falls back offline. "
            "Upload needs all 3 YT_* values.",
            foreground="#888").pack(anchor="w", padx=12)

        # ---------------- Actions + log ---------------------------------
        bar = ttk.Frame(master)
        bar.pack(fill="x", **pad)
        self.run_btn = ttk.Button(bar, text="Create Video",
                                  command=self.on_run)
        self.run_btn.pack(side="left", padx=6)
        ttk.Button(bar, text="Quit", command=master.destroy).pack(
            side="right", padx=6)

        self.log = scrolledtext.ScrolledText(master, height=18,
                                             state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=8, pady=6)

        self.master.after(120, self._drain)

    # -- logging -----------------------------------------------------------
    def _append(self, s: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", s)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain(self) -> None:
        try:
            while True:
                self._append(self.q.get_nowait())
        except queue.Empty:
            pass
        self.master.after(120, self._drain)

    # -- run ---------------------------------------------------------------
    def on_run(self) -> None:
        topic = self.topic.get().strip()
        if not topic:
            messagebox.showwarning("Missing topic",
                                   "Please enter a topic.")
            return
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("Busy", "A render is already running.")
            return

        argv = [sys.executable, "-m", "vp.run", topic,
                "--preset", self.preset.get()]
        if self.approve.get():
            argv.append("--approve")
        if not self.upload.get():
            argv.append("--no-upload")
        seg = self.segments.get().strip()
        if seg:
            if not seg.isdigit():
                messagebox.showwarning("Bad value",
                                       "Segment limit must be a number.")
                return
            argv += ["--segments", seg]
        if self.scene.get().strip():
            argv += ["--tts-scene", self.scene.get().strip()]
        if self.context.get().strip():
            argv += ["--tts-context", self.context.get().strip()]

        self.run_btn.configure(state="disabled", text="Running…")
        self._append(f"\n$ {' '.join(argv)}\n\n")
        threading.Thread(target=self._worker, args=(argv,),
                         daemon=True).start()

    def _worker(self, argv: list[str]) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        try:
            self.proc = subprocess.Popen(
                argv, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self.q.put(line)
            code = self.proc.wait()
            self.q.put(
                f"\n{'✅ DONE' if code == 0 else f'❌ exited {code}'} — "
                f"see output/<slug>/final.mp4\n"
            )
        except Exception as e:  # pragma: no cover
            self.q.put(f"\n❌ launcher error: {e}\n")
        finally:
            self.master.after(
                0, lambda: self.run_btn.configure(
                    state="normal", text="Create Video"))


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
