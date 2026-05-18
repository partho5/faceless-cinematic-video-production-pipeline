#!/usr/bin/env python3
"""Easy-run GUI launcher.

    python run.py

Fill the topic, press Create Video. The voice style is figured out from
your topic automatically — you don't write any of that. The full pipeline
(src/vp/run.py) runs in a subprocess and its progress streams live into the
log pane, so the window never freezes.

If you ask to review the story first, the app pauses after writing it,
shows it to you, and gives you an Approve button to continue.

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
SAMPLE_SEGMENTS = "6"  # "short sample" cap

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
        self.last_argv: list[str] = []
        self.out_dir: Path | None = None
        self.review_script: Path | None = None
        self.review_pending = False

        master.title("Video Production Pipeline")
        master.geometry("760x660")
        pad = dict(padx=8, pady=4)

        # ---------------- Core ------------------------------------------
        core = ttk.LabelFrame(master, text="Core")
        core.pack(fill="x", **pad)

        ttk.Label(core, text="What should the video be about?").grid(
            row=0, column=0, sticky="w", padx=6, pady=6)
        self.topic = ttk.Entry(core, width=64)
        self.topic.grid(row=0, column=1, columnspan=3, sticky="we",
                        padx=6, pady=6)

        ttk.Label(core, text="Quality").grid(row=1, column=0, sticky="w",
                                             padx=6)
        self.preset = tk.StringVar(value="final")
        ttk.Radiobutton(core, text="Best (full 1080p video)",
                        variable=self.preset, value="final").grid(
            row=1, column=1, sticky="w")
        ttk.Radiobutton(core, text="Quick look (rough, fast)",
                        variable=self.preset, value="preview").grid(
            row=1, column=2, sticky="w")

        ttk.Label(core, text="About how long? (minutes)").grid(
            row=2, column=0, sticky="w", padx=6, pady=6)
        self.minutes = ttk.Entry(core, width=6)
        self.minutes.insert(0, "6")
        self.minutes.grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(core, foreground="#888",
                  text="(roughly — the story is written to fit)").grid(
            row=2, column=2, columnspan=2, sticky="w")

        self.upload = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            core, text="Put it on my YouTube automatically (stays PRIVATE)",
            variable=self.upload).grid(row=3, column=1, columnspan=3,
                                       sticky="w", pady=2)
        core.columnconfigure(1, weight=1)

        # ---------------- Optional --------------------------------------
        opt = ttk.LabelFrame(master, text="Optional")
        opt.pack(fill="x", **pad)

        self.review = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opt, text="Let me read the story first, before the video is made",
            variable=self.review).grid(row=0, column=0, sticky="w",
                                       padx=6, pady=2)
        ttk.Label(
            opt, foreground="#888",
            text="   If ticked, the app pauses and shows you the written "
            "story; press Approve to continue.").grid(
            row=1, column=0, sticky="w", padx=6)

        self.sample = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opt, text="Make a short sample first (faster — only the opening)",
            variable=self.sample).grid(row=2, column=0, sticky="w",
                                       padx=6, pady=2)
        ttk.Label(
            opt, foreground="#888",
            text="   Good for checking the look/voice before the full "
            "render.").grid(row=3, column=0, sticky="w", padx=6)

        # ---------------- Status ----------------------------------------
        keys = _detect_keys()
        txt = "  ".join(f"{'●' if v else '○'} {k}" for k, v in keys.items())
        ttk.Label(master, text="Configured: " + txt,
                  foreground="#444").pack(anchor="w", padx=12)
        ttk.Label(
            master,
            text="○ = not set in .env → that part runs in offline fallback. "
            "YouTube needs all 3 YT_* values.",
            foreground="#888").pack(anchor="w", padx=12)

        # ---------------- Actions + log ---------------------------------
        bar = ttk.Frame(master)
        bar.pack(fill="x", **pad)
        self.run_btn = ttk.Button(bar, text="Create Video",
                                  command=self.on_run)
        self.run_btn.pack(side="left", padx=6)
        self.approve_btn = ttk.Button(bar, text="Approve & Continue",
                                      command=self.on_approve,
                                      state="disabled")
        self.approve_btn.pack(side="left", padx=6)
        ttk.Button(bar, text="Quit", command=master.destroy).pack(
            side="right", padx=6)

        self.log = scrolledtext.ScrolledText(master, height=17,
                                             state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=8, pady=6)

        self.master.after(120, self._drain)

    # -- logging -----------------------------------------------------------
    def _append(self, s: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", s)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _scan(self, line: str) -> None:
        """Pick up the output dir and the review marker as they stream."""
        s = line.strip()
        if s.startswith("[vp] output dir:") or " output dir:" in s:
            try:
                self.out_dir = Path(s.split("output dir:", 1)[1].strip())
            except Exception:
                pass
        if "REVIEW_REQUIRED " in s:
            self.review_script = Path(s.split("REVIEW_REQUIRED ", 1)[1].strip())
            self.review_pending = True

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
                                   "Please type what the video is about.")
            return None
        mins = self.minutes.get().strip() or "6"
        try:
            if float(mins) <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning(
                "Bad length", "Minutes must be a positive number.")
            return None
        argv = [sys.executable, "-m", "vp.run", topic,
                "--preset", self.preset.get(), "--minutes", mins]
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
        # re-run the SAME command: the gate now passes via the sentinel and
        # the approved script is reused verbatim (no regen / no API spend).
        self._launch(self.last_argv)

    def _launch(self, argv: list[str]) -> None:
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
            self.proc.wait()
        except Exception as e:  # pragma: no cover
            self.q.put(f"\n❌ launcher error: {e}\n")
        finally:
            self.master.after(0, self._finished)

    def _finished(self) -> None:
        self.run_btn.configure(state="normal", text="Create Video")
        if self.review_pending and self.review_script \
                and self.review_script.exists():
            self.approve_btn.configure(state="normal")
            self._append(
                "\n================ STORY FOR YOUR REVIEW "
                "================\n")
            try:
                self._append(
                    self.review_script.read_text(encoding="utf-8"))
            except Exception:
                pass
            self._append(
                f"\n========================================================"
                f"\nEdit {self.review_script} if you want, then press "
                f"'Approve & Continue'.\n")
        else:
            self._append("\n✅ Finished — see output/<slug>/final.mp4\n")


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
