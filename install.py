#!/usr/bin/env python3
"""Production bootstrap installer — the brain (plan/install-bootstrap.md).

STDLIB ONLY. This file runs *before* any third-party dependency exists, so
it must never `import` anything outside the standard library. Anything that
needs a third-party package runs as a subprocess of the project venv's
Python, never imported here.

Pipeline: audit (read-only) -> plan -> execute (live %) -> self-heal ->
verify -> report. Idempotent and re-entrant: re-running is the production
self-correct mechanism (satisfied steps are skipped). `--check` does the
audit+verify only (no mutations) and is the production health/drift gate.

The thin OS launchers (install.sh / install.bat+install.ps1) only guarantee
a Python >= 3.10 exists, then hand off here.

Exit codes (documented for CI/production):
  0 ready · 1 ready-with-warnings (optional deps skipped) ·
  2 fixable failure (re-run / take printed action) ·
  3 PATH changed — open a new shell · 4 unsupported / fatal
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "install.log"
REPORT_PATH = ROOT / "install_report.json"
VENV_DIR = ROOT / ".venv"
REQ_FILE = ROOT / "requirements.txt"
MIN_PY = (3, 10)

IS_WIN = os.name == "nt"
EXIT_OK, EXIT_WARN, EXIT_FIXABLE, EXIT_NEWSHELL, EXIT_FATAL = 0, 1, 2, 3, 4


# ───────────────────────────── console + log ────────────────────────────
class Con:
    """Colour only on a real TTY (and not NO_COLOR); plain everywhere else."""

    _tty = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    G = "\033[32m" if _tty else ""
    Y = "\033[33m" if _tty else ""
    R = "\033[31m" if _tty else ""
    B = "\033[34m" if _tty else ""
    DIM = "\033[2m" if _tty else ""
    BOLD = "\033[1m" if _tty else ""
    X = "\033[0m" if _tty else ""

    OK = (G + "✓" + X) if _tty else "[ ok ]"
    BAD = (R + "✗" + X) if _tty else "[fail]"
    WARN = (Y + "!" + X) if _tty else "[warn]"
    SKIP = (DIM + "·" + X) if _tty else "[skip]"


_VERBOSE = False


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str, level: str = "INFO") -> None:
    """Append to install.log always; echo to console per verbosity."""
    try:
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"{_ts()} [{level}] {msg}\n")
    except Exception:
        pass
    if level in ("ERROR", "WARN") or _VERBOSE:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()


def say(msg: str = "") -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def hr(title: str) -> None:
    say("")
    say(f"{Con.BOLD}── {title} {'─' * max(0, 56 - len(title))}{Con.X}")


# ───────────────────────────── shell helpers ────────────────────────────
def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None,
        timeout: int | None = None, check: bool = False):
    """Run a command, full output captured to install.log. Never raises
    unless check=True. Returns (rc, stdout, stderr)."""
    log(f"$ {' '.join(map(str, cmd))}", "CMD")
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError as e:
        log(f"  not found: {e}", "WARN")
        return 127, "", str(e)
    except subprocess.TimeoutExpired as e:
        log(f"  timeout after {timeout}s", "WARN")
        return 124, e.stdout or "", "timeout"
    if p.stdout:
        log(p.stdout.rstrip(), "OUT")
    if p.stderr:
        log(p.stderr.rstrip(), "ERR")
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {cmd}")
    return p.returncode, p.stdout or "", p.stderr or ""


def which(name: str) -> str | None:
    return shutil.which(name)


def cmd_ver(cmd: list[str], pat: str = r"(\d+\.\d+(?:\.\d+)?)") -> str | None:
    rc, out, err = run(cmd)
    if rc not in (0,):
        return None
    m = re.search(pat, (out or "") + (err or ""))
    return m.group(1) if m else "?"


# ───────────────────────────── progress UI ──────────────────────────────
class Progress:
    """Two-level progress: an overall weighted bar across steps + a live
    per-step sub-bar. Clean single rewritten line on a TTY; periodic plain
    lines when piped/CI so logs stay readable."""

    def __init__(self, total_weight: float):
        self.total = max(1e-9, total_weight)
        self.done_w = 0.0
        self.tty = sys.stdout.isatty()
        self.cur = ""
        self.cur_w = 0.0
        self.idx = 0
        self.n = 0
        self._last_emit = 0.0

    def begin(self, name: str, weight: float, idx: int, n: int) -> None:
        self.cur, self.cur_w, self.idx, self.n = name, weight, idx, n
        self._render(0.0, "…")

    def update(self, frac: float, note: str = "") -> None:
        self._render(max(0.0, min(1.0, frac)), note)

    def finish(self, status: str) -> None:
        # render at 100% of THIS step first, then bank its weight (banking
        # before rendering would double-count cur_w → >100%).
        self._render(1.0, status, final=True)
        self.done_w = min(self.total, self.done_w + self.cur_w)
        if self.tty:
            sys.stdout.write("\n")
            sys.stdout.flush()

    def _render(self, frac: float, note: str, final: bool = False) -> None:
        overall = (self.done_w + frac * self.cur_w) / self.total * 100.0
        overall = max(0.0, min(100.0, overall))
        bar_n = 22
        fill = max(0, min(bar_n, int(bar_n * overall / 100.0)))
        bar = "█" * fill + "·" * (bar_n - fill)
        line = (f"[{self.idx:>2}/{self.n}] {bar} {overall:5.1f}%  "
                f"{self.cur[:24]:<24} {note}")
        if self.tty:
            sys.stdout.write("\r\033[K" + line)
            sys.stdout.flush()
        else:
            now = time.time()
            if final or now - self._last_emit > 2.0:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
                self._last_emit = now


# ───────────────────────────── audit model ──────────────────────────────
OK, MISSING, OUTDATED, BROKEN, WARN_, UNKNOWN = (
    "OK", "MISSING", "OUTDATED", "BROKEN", "WARN", "UNKNOWN")


class Report:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.facts: dict = {}

    def add(self, name: str, status: str, detail: str = "",
            fix: str = "") -> None:
        self.items.append({"name": name, "status": status,
                            "detail": detail, "fix": fix})
        icon = {OK: Con.OK, MISSING: Con.BAD, OUTDATED: Con.WARN,
                BROKEN: Con.BAD, WARN_: Con.WARN,
                UNKNOWN: Con.WARN}.get(status, "?")
        say(f"  {icon} {name:<26} {Con.DIM}{detail}{Con.X}")

    def status_of(self, name: str) -> str | None:
        for it in self.items:
            if it["name"] == name:
                return it["status"]
        return None

    def worst(self) -> str:
        s = {it["status"] for it in self.items}
        if {MISSING, BROKEN} & s:
            return "FIX"
        if {OUTDATED, WARN_} & s:
            return "WARN"
        return "READY"

    def dump(self, extra: dict) -> None:
        try:
            REPORT_PATH.write_text(json.dumps(
                {"ts": _ts(), "facts": self.facts, "items": self.items,
                 **extra}, indent=2), encoding="utf-8")
        except Exception as e:
            log(f"could not write report: {e}", "WARN")


# ───────────────────────────── platform facts ───────────────────────────
def detect_platform() -> dict:
    f: dict = {
        "os": "windows" if IS_WIN else ("macos" if sys.platform == "darwin"
                                        else "linux"),
        "arch": platform.machine().lower(),
        "python": platform.python_version(),
        "python_exe": sys.executable,
        "in_venv": sys.prefix != getattr(sys, "base_prefix", sys.prefix),
        "cwd_writable": os.access(ROOT, os.W_OK),
    }
    if f["os"] == "linux":
        f["libc"] = "musl" if _is_musl() else "glibc"
        osr = Path("/etc/os-release")
        if osr.exists():
            kv: dict[str, str] = {}
            for ln in osr.read_text(errors="replace").splitlines():
                if "=" in ln and not ln.lstrip().startswith("#"):
                    k, _, v = ln.partition("=")
                    kv[k.strip()] = v.strip().strip('"').strip("'")
            f["distro"] = kv.get("ID", "?")
            f["distro_ver"] = kv.get("VERSION_ID", "?")
        f["pkg_mgr"] = next((m for m in (
            "apt-get", "dnf", "yum", "pacman", "zypper", "apk")
            if which(m)), None)
        f["wsl"] = "microsoft" in platform.release().lower()
        f["is_root"] = (os.geteuid() == 0) if hasattr(os, "geteuid") else False
        f["sudo"] = bool(which("sudo")) and not f["is_root"]
    elif f["os"] == "macos":
        f["pkg_mgr"] = "brew" if which("brew") else None
        f["sudo"] = bool(which("sudo"))
    else:  # windows
        f["win_ver"] = platform.version()
        f["winget"] = bool(which("winget"))
        f["choco"] = bool(which("choco"))
    return f


def _is_musl() -> bool:
    try:
        rc, out, err = run(["ldd", "--version"])
        return "musl" in (out + err).lower()
    except Exception:
        return False


def _free_gb(path: Path) -> float:
    try:
        return shutil.disk_usage(path).free / 1e9
    except Exception:
        return -1.0


def _net_ok(host: str, timeout: float = 4.0) -> bool:
    try:
        ctx = ssl.create_default_context()
        urllib.request.urlopen(f"https://{host}", timeout=timeout,
                                context=ctx).close()
        return True
    except Exception:
        return False


# ───────────────────────────── venv / pkg probes ────────────────────────
def venv_python() -> Path:
    return (VENV_DIR / ("Scripts/python.exe" if IS_WIN else "bin/python3"))


def installed_versions(py: Path) -> dict:
    """name(lower) -> version, from the *target* interpreter (stdlib only
    on its side, so this works even in a barebones venv). Skips dists
    with a missing Name (a partial/corrupted install record would
    otherwise crash the probe and silently mask real state)."""
    code = ("import json\n"
            "try:\n"
            " from importlib import metadata as m\n"
            "except Exception:\n"
            " import importlib_metadata as m\n"
            "out={}\n"
            "for d in m.distributions():\n"
            " n=(d.metadata or {}).get('Name')\n"
            " if n: out[n.lower()]=d.version\n"
            "print(json.dumps(out))")
    rc, out, _ = run([str(py), "-c", code])
    if rc == 0 and out.strip().startswith("{"):
        try:
            return json.loads(out.strip().splitlines()[-1])
        except Exception:
            return {}
    return {}


def parse_requirements() -> list[tuple[str, str, str]]:
    """-> [(pkgname_lower, full_spec, raw_line)] ignoring comments/blanks."""
    out: list[tuple[str, str, str]] = []
    if not REQ_FILE.exists():
        return out
    for raw in REQ_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)", line)
        if m:
            out.append((m.group(1).lower(), line, raw.strip()))
    return out


# heavy / optional pip deps (plan §9 #3: default-on, but degrade not fail)
OPTIONAL_HEAVY = {"stable-ts", "torch", "openai-whisper"}


def _spec_satisfied(spec: str, have: str | None) -> bool:
    """Tiny PEP-440-ish check: only the >= floor we actually pin in
    requirements.txt is enforced (good enough; pip does the real resolve)."""
    if have is None:
        return False
    m = re.search(r">=\s*([0-9][0-9.]*)", spec)
    if not m:
        return True
    want = [int(x) for x in re.findall(r"\d+", m.group(1))]
    got = [int(x) for x in re.findall(r"\d+", have)]
    return got[: len(want)] >= want or got >= want


# ───────────────────────────── PHASE A: audit ───────────────────────────
def audit(args) -> Report:
    r = Report()
    f = detect_platform()
    r.facts = f
    hr("Environment audit")

    osline = f"{f['os']} {f.get('distro','')} {f.get('distro_ver','')} " \
             f"{f['arch']} {f.get('libc','')}".strip()
    r.add("operating system", OK, osline)

    # privilege
    if f["os"] == "linux":
        priv = ("root" if f.get("is_root") else
                "sudo" if f.get("sudo") else "no-sudo")
        r.add("privilege", OK if priv != "no-sudo" else WARN_,
              f"{priv}; pkg-mgr={f.get('pkg_mgr')}",
              "" if priv != "no-sudo" else
              "no sudo — userland Python path will be used")
    elif f["os"] == "windows":
        r.add("privilege", OK,
              f"winget={f.get('winget')} choco={f.get('choco')}")

    # python (this interpreter is the bootstrap python; >=3.10 guaranteed
    # by the launcher, but verify defensively)
    pv = sys.version_info[:2]
    r.add("python (bootstrap)", OK if pv >= MIN_PY else BROKEN,
          f"{platform.python_version()} @ {sys.executable}",
          "" if pv >= MIN_PY else f"need >= {MIN_PY[0]}.{MIN_PY[1]}")

    # project venv. Two failure modes we must catch here, because both
    # show up in the wild after an interrupted install or a corrupted
    # write: (a) python.exe itself broken, (b) the venv's bundled pip
    # has null-byte-corrupted files — a working python BUT pip crashes
    # the moment it's invoked. Both look fine to a naive "does python
    # run?" probe, then explode mid-install. So we probe pip too.
    vp = venv_python()
    if vp.exists():
        rc, out, _ = run([str(vp), "-c",
                          "import sys;print('%d.%d'%sys.version_info[:2])"])
        if rc == 0:
            vok = tuple(map(int, out.strip().split("."))) >= MIN_PY
            if not vok:
                r.add(".venv", OUTDATED, f"{out.strip()} @ {VENV_DIR}",
                      "venv python too old — will rebuild")
            else:
                rc_pip, _, _ = run([str(vp), "-m", "pip", "--version"])
                if rc_pip == 0:
                    r.add(".venv", OK, f"{out.strip()} @ {VENV_DIR}")
                else:
                    r.add(".venv", BROKEN,
                          f"{out.strip()} @ {VENV_DIR}; pip unusable",
                          "will rebuild")
        else:
            r.add(".venv", BROKEN, "exists but not runnable",
                  "will rebuild")
    else:
        r.add(".venv", MISSING, "not created", "will create")

    # tools. git is intentionally not audited: it's only needed for the
    # one-time `git clone` step BEFORE install.bat is run; once the user
    # is running the installer they already have the repo on disk, so a
    # missing-git "warning" is pure noise that scares non-technical users.
    for tool, probe, required in (
        ("ffmpeg", ["ffmpeg", "-version"], True),
        ("ffprobe", ["ffprobe", "-version"], True),
    ):
        if which(probe[0]):
            r.add(tool, OK, cmd_ver(probe) or "present")
        else:
            r.add(tool, MISSING if required else WARN_,
                  "not on PATH",
                  "install (mandatory)" if required else "optional")

    # tkinter (GUI). Probe the bootstrap python; venv inherits it.
    rc, _, _ = run([sys.executable, "-c", "import tkinter"])
    r.add("tkinter (GUI)", OK if rc == 0 else WARN_,
          "available" if rc == 0 else "absent",
          "" if rc == 0 else "GUI disabled; CLI still works")

    # pip (PEP-668 "externally-managed" is sidestepped by always
    # installing into .venv, so it needs no separate remediation here)
    rc, _, _ = run([sys.executable, "-m", "pip", "--version"])
    r.add("pip", OK if rc == 0 else MISSING,
          "present" if rc == 0 else "absent",
          "" if rc == 0 else "ensurepip / get-pip (auto)")

    # python deps vs requirements.txt (checked in the venv if it exists)
    reqs = parse_requirements()
    have = installed_versions(vp) if vp.exists() else {}
    miss = [n for n, spec, _ in reqs
            if not _spec_satisfied(spec, have.get(n))]
    req_miss = [n for n in miss if n not in OPTIONAL_HEAVY]
    opt_miss = [n for n in miss if n in OPTIONAL_HEAVY]
    r.add("python deps", OK if not req_miss else MISSING,
          f"{len(reqs)-len(miss)}/{len(reqs)} satisfied"
          + (f"; heavy pending: {','.join(opt_miss)}" if opt_miss else ""),
          "" if not req_miss else f"install: {', '.join(req_miss)}")
    f["deps_missing"], f["deps_optional_missing"] = req_miss, opt_miss

    # resources
    free = _free_gb(ROOT)
    need = 6.0 if (args.profile == "full") else 2.0
    r.add("disk space", OK if free < 0 or free >= need else BROKEN,
          f"{free:.1f} GB free (need ~{need:.0f})",
          "" if free < 0 or free >= need else "free up disk")

    # network (audit must still complete offline)
    if not args.offline:
        nets = {h: _net_ok(h) for h in
                ("pypi.org", "files.pythonhosted.org", "github.com")}
        up = sum(nets.values())
        r.add("network", OK if up >= 2 else WARN_,
              ", ".join(f"{h}:{'✓' if v else '✗'}"
                        for h, v in nets.items()),
              "" if up >= 2 else "limited connectivity — installs may fail")

    # project config (scaffolded later, not an install dep)
    for name, ex in ((".env", ".env.example"),
                     ("config.yaml", "config.example.yaml")):
        if (ROOT / name).exists():
            r.add(name, OK, "present")
        elif (ROOT / ex).exists():
            r.add(name, WARN_, "missing", f"will scaffold from {ex}")
        else:
            r.add(name, WARN_, "missing", "no example to scaffold from")

    say("")
    say(f"  {Con.BOLD}Audit result: {r.worst()}{Con.X}")
    return r


# ───────────────────────────── PHASE C helpers ──────────────────────────
def download(url: str, dest: Path, prog: Progress | None = None,
              sha256: str | None = None, retries: int = 3) -> bool:
    """Verified download with byte-% progress + retry/backoff."""
    import hashlib
    ctx = ssl.create_default_context()
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vp-installer"})
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                h = hashlib.sha256()
                got = 0
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("wb") as fh:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        fh.write(chunk)
                        h.update(chunk)
                        got += len(chunk)
                        if prog and total:
                            prog.update(got / total,
                                        f"{got/1e6:5.0f}/{total/1e6:.0f} MB")
            if sha256 and h.hexdigest().lower() != sha256.lower():
                log(f"sha256 mismatch for {url}", "ERROR")
                dest.unlink(missing_ok=True)
                return False
            return True
        except Exception as e:
            log(f"download attempt {attempt} failed: {e}", "WARN")
            time.sleep(2 ** attempt)
    return False


def ensure_venv(base_py: str) -> bool:
    """Create/repair .venv from base_py; upgrade pip/setuptools/wheel.
    Treats a venv whose pip can't be invoked (corrupted pyc/py with
    null bytes — seen after interrupted installs) as broken and rebuilds
    it. Without this check, pip crashes mid-install with a confusing
    'source code string cannot contain null bytes' traceback."""
    vp = venv_python()
    if vp.exists():
        rc, out, _ = run([str(vp), "-c",
                          "import sys;print(sys.version_info[:2]>=%s)"
                          % repr(MIN_PY)])
        rc_pip, _, _ = run([str(vp), "-m", "pip", "--version"]) \
            if (rc == 0 and "True" in out) else (1, "", "")
        if rc == 0 and "True" in out and rc_pip == 0:
            return True
        log("venv broken/old — rebuilding", "WARN")
        shutil.rmtree(VENV_DIR, ignore_errors=True)
    rc, _, err = run([base_py, "-m", "venv", str(VENV_DIR)])
    if rc != 0:
        # self-heal: missing ensurepip/venv (Debian splits python3-venv)
        log(f"venv create failed: {err}", "ERROR")
        return False
    run([str(vp), "-m", "ensurepip", "--upgrade"])
    run([str(vp), "-m", "pip", "install", "--upgrade",
         "--disable-pip-version-check", "pip", "setuptools", "wheel"])
    return vp.exists()


def pip_install(pkgs: list[str], prog: Progress, label: str,
                 extra: list[str] | None = None) -> bool:
    """Install a set in ONE resolver pass (correct), streaming per-package
    progress parsed from pip output (plan §9 #2: hybrid)."""
    if not pkgs:
        return True
    vp = venv_python()
    cmd = [str(vp), "-m", "pip", "install", "--disable-pip-version-check",
           "--progress-bar", "off", *(extra or []), *pkgs]
    log(f"$ {' '.join(cmd)}", "CMD")
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="replace", bufsize=1)
    except Exception as e:
        log(f"pip spawn failed: {e}", "ERROR")
        return False
    seen, n = 0, max(1, len(pkgs))
    assert p.stdout is not None
    for line in p.stdout:
        log(line.rstrip(), "PIP")
        if line.startswith("Collecting "):
            seen = min(n, seen + 1)
            name = line.split()[1]
            prog.update(seen / (n + 1), f"{label}: {name}")
        elif "Installing collected packages" in line:
            prog.update(n / (n + 1), f"{label}: installing…")
    p.wait()
    ok = p.returncode == 0
    log(f"pip rc={p.returncode}", "INFO" if ok else "ERROR")
    return ok


def prefetch_whisper(prog: Progress) -> None:
    """Pre-pull the stable-ts/Whisper 'base' model so the first production
    render never stalls (plan §9 #3). Skip if already cached."""
    vp = venv_python()
    code = ("import stable_whisper,sys;"
            "m=stable_whisper.load_model('base');"
            "print('whisper-ready')")
    prog.update(0.1, "fetching Whisper base model…")
    rc, out, _ = run([str(vp), "-c", code], timeout=900)
    prog.update(1.0, "model ready" if rc == 0 else "skipped")
    log("whisper prefetch " + ("ok" if rc == 0 else "failed/optional"),
        "INFO" if rc == 0 else "WARN")


def scaffold_config() -> list[str]:
    """Copy *.example -> real config if missing (never overwrite, never
    fill secrets). Returns the files created."""
    made = []
    for name, ex in ((".env", ".env.example"),
                     ("config.yaml", "config.example.yaml")):
        tgt, src = ROOT / name, ROOT / ex
        if not tgt.exists() and src.exists():
            shutil.copyfile(src, tgt)
            made.append(name)
            log(f"scaffolded {name} from {ex}", "INFO")
    return made


# ───────────────────────── PHASE: ffmpeg self-heal ──────────────────────
def ensure_ffmpeg(f: dict, prog: Progress) -> bool:
    if which("ffmpeg") and which("ffprobe"):
        return True
    osn = f["os"]
    if osn == "linux" and f.get("pkg_mgr"):
        sudo = ["sudo"] if f.get("sudo") else []
        pm = f["pkg_mgr"]
        cmd = {
            "apt-get": sudo + ["apt-get", "install", "-y", "ffmpeg"],
            "dnf": sudo + ["dnf", "install", "-y", "ffmpeg"],
            "yum": sudo + ["yum", "install", "-y", "ffmpeg"],
            "pacman": sudo + ["pacman", "-S", "--noconfirm", "ffmpeg"],
            "zypper": sudo + ["zypper", "-n", "install", "ffmpeg"],
            "apk": sudo + ["apk", "add", "ffmpeg"],
        }.get(pm)
        if pm == "apt-get":
            run(sudo + ["apt-get", "update"])
        prog.update(0.3, f"{pm} install ffmpeg")
        if cmd:
            run(cmd, timeout=600)
        return bool(which("ffmpeg"))
    if osn == "macos" and which("brew"):
        prog.update(0.3, "brew install ffmpeg")
        run(["brew", "install", "ffmpeg"], timeout=900)
        return bool(which("ffmpeg"))
    if osn == "windows":
        if f.get("winget"):
            prog.update(0.3, "winget Gyan.FFmpeg")
            run(["winget", "install", "-e", "--id", "Gyan.FFmpeg",
                 "--accept-source-agreements", "--accept-package-agreements",
                 "--silent"], timeout=900)
        elif f.get("choco"):
            run(["choco", "install", "-y", "ffmpeg"], timeout=900)
        return bool(which("ffmpeg"))
    return False


# ───────────────────────────── PHASE B + C ──────────────────────────────
def execute(r: Report, args) -> int:
    f = r.facts
    hr("Install / repair")

    base_py = sys.executable  # launcher guarantees >=3.10 here

    # weighted step list (probe -> skip; ensure -> do)
    req_miss = f.get("deps_missing", [])
    opt_miss = f.get("deps_optional_missing", []) if args.profile != "minimal" \
        else []

    steps: list[tuple[str, float, str]] = []
    if not (venv_python().exists() and r.status_of(".venv") == OK):
        steps.append(("python virtualenv", 2.0, "venv"))
    if not (which("ffmpeg") and which("ffprobe")):
        steps.append(("ffmpeg", 3.0, "ffmpeg"))
    if req_miss:
        steps.append(("python dependencies", 5.0, "reqs"))
    if opt_miss and args.profile == "full":
        steps.append(("precise alignment (torch)", 10.0, "heavy"))
        steps.append(("whisper model prefetch", 3.0, "model"))
    steps.append(("editable install (vp)", 1.0, "editable"))
    steps.append(("project config", 0.5, "config"))

    prog = Progress(sum(w for _, w, _ in steps))
    n = len(steps)
    warned = False

    for i, (name, w, kind) in enumerate(steps, 1):
        prog.begin(name, w, i, n)

        if kind == "venv":
            ok = ensure_venv(base_py)
            prog.finish(Con.OK if ok else Con.BAD)
            if not ok:
                say(f"\n{Con.R}FATAL: could not create .venv. Ensure the "
                    f"OS 'python3-venv' package (Debian/Ubuntu) is "
                    f"installed.{Con.X}")
                return EXIT_FIXABLE

        elif kind == "ffmpeg":
            ok = ensure_ffmpeg(f, prog)
            prog.finish(Con.OK if ok else Con.BAD)
            if not ok:
                say(f"\n{Con.R}FATAL: ffmpeg is mandatory and could not be "
                    f"installed automatically. Install it and re-run.{Con.X}")
                return EXIT_FIXABLE

        elif kind == "reqs":
            ok = pip_install([s for nm, s, _ in parse_requirements()
                              if nm in req_miss],
                             prog, "deps")
            prog.finish(Con.OK if ok else Con.BAD)
            if not ok:
                say(f"\n{Con.R}Required dependencies failed to install — "
                    f"see install.log.{Con.X}")
                return EXIT_FIXABLE

        elif kind == "heavy":
            say(f"\n{Con.Y}{Con.BOLD}  Heads-up:{Con.X}{Con.Y} installing "
                f"the precise word-alignment engine (PyTorch + stable-ts). "
                f"This is a large one-time download (~2 GB) plus ~1.5–2 GB "
                f"on disk and can take several minutes. Please wait. "
                f"(Use --profile minimal next time to skip.){Con.X}")
            ok = pip_install([s for nm, s, _ in parse_requirements()
                              if nm in opt_miss], prog, "torch")
            f["_heavy_ok"] = ok
            prog.finish(Con.OK if ok else Con.WARN)
            if not ok:
                warned = True
                say(f"\n{Con.Y}Note: precise alignment unavailable on this "
                    f"platform (no wheel). The pipeline still works with the "
                    f"built-in proportional fallback.{Con.X}")

        elif kind == "model":
            if r.facts.get("_heavy_ok", True):
                prefetch_whisper(prog)
            prog.finish(Con.OK)

        elif kind == "editable":
            rc = pip_install(["-e", "."], prog, "vp", extra=["--no-deps"])
            prog.finish(Con.OK if rc else Con.WARN)
            warned = warned or not rc

        elif kind == "config":
            made = scaffold_config()
            prog.update(1.0, f"created {', '.join(made)}" if made
                        else "already present")
            prog.finish(Con.OK)

    return EXIT_WARN if warned else EXIT_OK


# ───────────────────────────── PHASE E: verify ──────────────────────────
def verify(args) -> tuple[bool, list[str]]:
    hr("Verify")
    vp = venv_python()
    notes: list[str] = []
    ok = True

    rc, _, _ = run([str(vp), "-c",
                    "import numpy,PIL,yaml,moviepy;print('core-ok')"])
    ok &= rc == 0
    say(f"  {Con.OK if rc==0 else Con.BAD} import core")

    # `import vp`: the app runs either as an editable install OR via
    # run.py which injects PYTHONPATH=src. Accept either (that is exactly
    # how the software is launched); a fixable note if only the latter.
    rc_plain, _, _ = run([str(vp), "-c", "import vp;print('vp-ok')"])
    if rc_plain == 0:
        say(f"  {Con.OK} import vp")
    else:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + \
            env.get("PYTHONPATH", "")
        rc_path, _, _ = run([str(vp), "-c", "import vp;print('vp-ok')"],
                            env=env)
        if rc_path == 0:
            say(f"  {Con.OK} import vp {Con.DIM}(via src; run.py handles "
                f"this — `pip install -e .` for a plain import){Con.X}")
        else:
            ok = False
            say(f"  {Con.BAD} import vp")

    rc, _, _ = run(["ffmpeg", "-version"])
    if rc != 0:
        notes.append("ffmpeg not callable from PATH (open a new shell)")
    say(f"  {Con.OK if rc==0 else Con.BAD} ffmpeg")

    rc, _, _ = run([str(vp), "-c", "import tkinter"])
    say(f"  {Con.OK if rc==0 else Con.WARN} tkinter (GUI)")
    if rc != 0:
        notes.append("Tkinter missing — GUI unavailable, CLI works")

    if args.run_tests:
        rc, out, _ = run([str(vp), "-m", "pytest", "-q"], cwd=ROOT,
                          timeout=600)
        say(f"  {Con.OK if rc==0 else Con.WARN} pytest "
            f"({'passed' if rc==0 else 'see install.log'})")
    return ok, notes


# ───────────────────────────── main ─────────────────────────────────────
def main(argv=None) -> int:
    global _VERBOSE
    # the installer prints ✓/✗/█ etc — force UTF-8 so a Windows console
    # codepage (cp1252) can't crash the bootstrap on its own output.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(
        prog="install.py",
        description="Production bootstrap installer (audit→install→verify).")
    ap.add_argument("--check", action="store_true",
                    help="audit + verify only; no changes (health/drift gate)")
    ap.add_argument("--profile", choices=["minimal", "standard", "full"],
                    default="full",
                    help="minimal=offline stack; standard=+API clients; "
                         "full=+precise alignment (torch). default: full")
    ap.add_argument("--offline", action="store_true",
                    help="no network; audit/verify only")
    ap.add_argument("--run-tests", action="store_true",
                    help="run pytest -q as a final acceptance gate")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    _VERBOSE = args.verbose

    try:
        LOG_PATH.write_text(f"{_ts()} [INFO] install.py start "
                            f"argv={sys.argv}\n", encoding="utf-8")
    except Exception:
        pass

    say(f"{Con.BOLD}Video Production — bootstrap installer{Con.X}")
    say(f"{Con.DIM}log: {LOG_PATH.name} · report: {REPORT_PATH.name}{Con.X}")

    if sys.version_info[:2] < MIN_PY:
        say(f"{Con.R}This interpreter is {platform.python_version()}; "
            f"need >= {MIN_PY[0]}.{MIN_PY[1]}. The OS launcher should have "
            f"provided one.{Con.X}")
        return EXIT_FATAL

    r = audit(args)

    if args.check or args.offline:
        ok, notes = verify(args)
        r.dump({"mode": "check", "verify_ok": ok, "notes": notes})
        worst = r.worst()
        say("")
        say(f"{Con.BOLD}Readiness: {worst}{Con.X}  "
            f"(report → {REPORT_PATH.name})")
        return EXIT_OK if worst == "READY" and ok else (
            EXIT_WARN if worst == "WARN" else EXIT_FIXABLE)

    code = execute(r, args)
    if code in (EXIT_FIXABLE, EXIT_FATAL):
        r.dump({"mode": "install", "result": "failed", "exit": code})
        say(f"\n{Con.R}Install incomplete (exit {code}). "
            f"Fix the above and re-run — it resumes.{Con.X}")
        return code

    ok, notes = verify(args)
    r2 = audit(args)
    worst = r2.worst()
    r2.dump({"mode": "install", "verify_ok": ok, "notes": notes,
             "exit": code})

    say("")
    if worst == "READY" and ok and code == EXIT_OK:
        say(f"{Con.G}{Con.BOLD}{Con.OK} Ready.{Con.X}  Run the app:")
    else:
        for nte in notes:
            say(f"  {Con.WARN} {nte}")
        say(f"{Con.Y}Completed with warnings - usable. "
            f"Run the app:{Con.X}")
    say(f"    {Con.BOLD}{venv_python()} run.py{Con.X}   (GUI)")
    say(f"    or: {venv_python()} -m vp.run \"<topic>\" --approve")
    return EXIT_OK if (worst == "READY" and ok and code == EXIT_OK) \
        else EXIT_WARN


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        say("\nInterrupted.")
        raise SystemExit(130)
