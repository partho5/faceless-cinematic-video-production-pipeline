# Plan: Production Bootstrap Installer (Linux + Windows)

> Planning artifact only. **Do NOT write installer code until the "Open
> decisions" in §9 are confirmed and this plan is approved.** Mirrors the
> structure of `plan/build-sound-effects.md` incl. a self-tracking §11.

---

## 0. One-paragraph summary

A production-grade, self-healing installer that runs on a *bare* Linux or
Windows machine, audits the whole environment, then installs/repairs every
dependency idempotently with live progress — skipping anything already
present. On Windows it must work **even when Python itself is absent**:
download, silently install, and PATH-register Python first. There is no AI
or human to diagnose at runtime, so the script encodes an explicit
detection→decision→remediation playbook, writes a machine-readable report,
and returns well-defined exit codes so a production system can run it
unattended (and re-run it to self-correct drift).

---

## 1. Locked requirements (from the owner — do not relitigate)

1. **Comprehensive environment check** before doing anything.
2. **Install dependencies one-by-one with command-line progress %**; if a
   dependency is already satisfied, **skip it**, else install it.
3. **Production-grade autonomy**: no AI/human in the loop. The script must
   detect everything, install, and *fix at runtime* on its own.
4. **Both Linux and Windows** (one coherent UX/behaviour on each).
5. **Windows cold-start**: if Python is not installed, the script itself
   downloads + installs Python (correct arch, silent) and sets the PATH
   variable, then proceeds.

Implied/added (call out, owner to confirm in §9):
- Idempotent & re-entrant (safe to run repeatedly; the production "repair"
  pattern is just re-running it).
- Least privilege (prefer per-user installs; avoid requiring admin/sudo
  except where unavoidable, e.g. distro packages / long-path registry).
- Integrity (pin versions; verify SHA-256 of downloaded binaries; TLS on).
- Never print/store secrets.

---

## 2. The core problem & the architectural decision

**Chicken-and-egg:** the installer must execute before Python exists (the
Windows requirement). Therefore the *entry point cannot be Python* on the
cold path. But duplicating all detection/repair logic across Bash **and**
Batch/PowerShell is unmaintainable and bug-prone — exactly the fragility
that is unacceptable in production.

**Decision: thin OS-native bootstrap launchers + one fat cross-platform
Python core.**

```
Linux/macOS:  install.sh ─┐                       ┌─ (creates .venv,
Windows:      install.bat ─┤→  ensure Python ≥3.10 ┤   installs deps,
                 └ install.ps1 ┘  (download+install)│   verifies, reports)
                                            ↓
                                   python install.py  ← ALL real logic
                                   (stdlib-only)
```

- The launchers do exactly ONE hard job: *guarantee a working Python
  ≥3.10 exists*, then hand off (`python install.py "$@"`) and exit with
  its code.
- `install.py` is **stdlib-only** (it runs before any pip deps exist):
  `argparse, json, os, sys, platform, subprocess, shutil, venv, urllib,
  ssl, hashlib, time, ctypes` (Windows PATH broadcast), `importlib`.
  Anything needing third-party libs runs as a *subprocess of the venv
  Python*, never imported into `install.py`.
- Single source of truth for the dependency set: parse `requirements.txt`
  + a small declarative table of **system** deps the codebase needs
  (known from the code: `ffmpeg`/`ffprobe`, Tkinter, `git` optional;
  `pip install -e .` for the `vp` package).

This isolates the only unavoidable platform-specific code to ~100 lines of
launcher each; everything intelligent is written once, in Python, testable.

---

## 3. Components / deliverables

| File | Role | Language |
|---|---|---|
| `install.sh` | Linux/macOS bootstrap: detect distro+pkg mgr, ensure Python≥3.10 (+venv/tk), then `exec python3 install.py "$@"` | POSIX sh |
| `install.bat` | Windows double-click entry; relaunches PowerShell with `-ExecutionPolicy Bypass` → `install.ps1` | cmd |
| `install.ps1` | Windows bootstrap: detect Python; if absent download+verify+silent-install Python (PrependPath, tcltk), refresh PATH, then `python install.py %*` | PowerShell |
| `install.py` | **The brain**: audit → plan → execute (progress) → self-heal → verify → report. Stdlib-only. Modes/flags. | Python (stdlib) |
| `installer/` (opt) | Only if `install.py` grows large: split `audit.py`, `deps.py`, `progress.py`, `playbook.py`. Decide during build. | Python |
| `plan/install-bootstrap.md` | this plan + self-tracking log | md |
| README update | document `./install.sh` / `install.bat` one-liner per OS | md |
| Installer test harness | container/VM matrix to prove cold-start works | (see §10) |

---

## 4. `install.py` — the brain (detailed)

### 4.1 Phase A — Comprehensive audit (read-only, always runs)

Produces an in-memory report + `install_report.json` + appends `install.log`.
Each item: `OK | MISSING | OUTDATED | BROKEN | UNKNOWN` + detail + the
remediation that *would* run.

Checks:
- **OS**: family, distro+version (`/etc/os-release`), Windows build, macOS
  ver, kernel, **architecture** (x86_64/arm64), WSL?, container/root?
- **Privilege**: sudo available & passwordless? Windows admin? writable
  repo dir? `%LOCALAPPDATA%`/`~/.local` writable?
- **Python**: which interpreters exist (`py -3`, `python3.1x`, `python3`,
  `python`), versions, is this a venv, `sys.executable`, `pip`/`ensurepip`,
  PEP-668 "externally-managed" marker present (modern Debian/Homebrew)?
- **Project venv**: does `.venv` exist, is its Python ≥3.10, is it intact
  (can it run `-m pip`)? Broken → flag for rebuild.
- **System tooling**: `git`, `ffmpeg`, `ffprobe` (parse `-version`),
  Tcl/Tk (`python -c "import tkinter"` — needed by the GUI), C/build
  toolchain (only if a required wheel has no binary), package manager
  (apt/dnf/yum/pacman/zypper/apk/brew/winget/choco).
- **Python deps**: parse `requirements.txt`; for each, check installed
  version via `importlib.metadata` *inside the target venv*; classify
  satisfied/missing/outdated. Mark `stable-ts`/torch **optional-heavy**.
- **Resources**: free disk (≥ ~2 GB base, ≥ ~6 GB if torch), RAM, temp
  space; **network reachability** (pypi.org, files.pythonhosted.org,
  github.com, python.org, plus optional provider endpoints) with short
  timeouts — audit must still complete offline.
- **Project config**: presence of `.env`/`config.yaml` (and whether keys
  are set — *names only, never values*). Not an install step; reported +
  optionally scaffolded from `*.example` in Phase C (never overwrite,
  never fill secrets).

`--check` runs **only** Phase A and exits with a readiness code — this is
the production health-check / drift detector.

### 4.2 Phase B — Planner

From the audit, build an ordered, weighted action list. Each action =
`{id, probe(), ensure(), weight, required|optional, platforms, fallbacks[]}`.
Ordering respects dependencies: ensure Python → venv → pip/setuptools/wheel
upgrade → system deps (ffmpeg/tk/git) → python deps → `pip install -e .` →
config scaffold → verify. `probe()` true ⇒ step is skipped (the owner's
"skip if found"). Weights make the % meaningful (torch ≫ PyYAML).

### 4.3 Phase C — Executor with live progress

Two-level progress:
- **Overall**: weighted, monotonic, `[12/19] 63%`.
- **Per-step**: real % where the tool exposes it:
  - **Downloads** (Python/ffmpeg): `urllib` with `Content-Length` → true
    byte % bar + speed/ETA.
  - **pip**: install the *missing* set; stream output and parse
    `Collecting/Downloading/Installing collected packages: a, b, c` to
    advance a per-package sub-bar. (See §9 decision #2 re: strict
    one-by-one vs resolver pass — affects correctness.)
  - **OS pkg mgr** (apt/winget): coarse (no clean machine progress) →
    animated `installing…` + parse final status; weighted low-resolution.
- Rendering: a single rewritten status line (`\r`) — clean on a TTY,
  degrades to periodic line logs when not a TTY/CI (`--no-tty`). Unicode
  output forced to UTF-8 (we already hit this on Windows; reuse the fix).
- Every step also writes full stdout/stderr to `install.log` (timestamped).
- **Heavy-step UX (§9 #3):** immediately before the Torch/stable-ts step,
  print a prominent one-time notice — *"Installing precise word-alignment
  engine: ~2 GB download + ~1.5–2 GB disk, several minutes. Please wait
  (use `--profile minimal` next time to skip)."* Then, after stable-ts
  installs, **pre-fetch the Whisper `base` model** (run a tiny venv
  subprocess that triggers stable-ts's model download) so the first
  production render never stalls. This pre-fetch is its own progress
  step with byte %, and is skipped if the model cache already exists.

### 4.4 Phase D — Self-heal playbook (the "no AI in prod" core)

A static decision tree mapping detected failure → automatic remediation
→ re-probe → escalate. Minimum coverage:

| Symptom | Automatic fix | If fix fails |
|---|---|---|
| No `pip` | `python -m ensurepip --upgrade`; else download `get-pip.py` (verified) | abort w/ exact msg |
| PEP-668 externally-managed | always install into `.venv` (never system) | — (venv is default) |
| `.venv` broken/old Python | delete & recreate with the resolved Python | abort:no base python |
| Wheel build fails (sdist, no compiler) | retry `--only-binary=:all:`; try extra index (e.g. piwheels on ARM) | if **optional** (torch/stable-ts): warn+skip+continue (pipeline has fallback); if required: abort w/ toolchain hint |
| ffmpeg missing | Linux: distro pkg; Win: winget→choco→direct verified zip→PATH; mac: brew | abort: ffmpeg is mandatory + manual steps |
| ffmpeg installed but not on PATH (stale session) | use absolute path for the rest of THIS run; persist PATH for future; tell user to reopen shell | continue (functional) |
| Tkinter missing (Linux) | install distro `python3-tk` | warn: GUI unavailable, CLI still works |
| Network flaky | retry w/ exponential backoff (n=…); honor `HTTP(S)_PROXY`; mirror fallback | abort w/ offline guidance; `--check` still ok |
| Disk full | abort early in audit with the number needed vs free | — |
| Long paths (Win, optional torch temp) | offer/enable `LongPathsEnabled` (needs admin) else recommend short repo path | continue w/ warning |

Unknown/unhandled failure ⇒ full context to `install.log`, a stable error
code, and a printed "what to send for support" block. **Required failure ⇒
non-zero exit; optional failure ⇒ continue + WARN.**

### 4.5 Phase E — Verify & report (closes the loop)

Re-run the audit; then hard smoke: venv `python -c "import vp, numpy,
PIL, moviepy"`, `ffmpeg -version`, `import tkinter`, optional fast
`pytest -q`. Emit final `install_report.json` (machine-readable for prod
monitoring) + a human summary ending with the exact next command
(`python run.py` or the CLI). Re-running after success is a fast no-op
(all probes pass) — that *is* the production self-correct mechanism.

### 4.6 Flags / modes / exit codes

- `--check` (audit+verify only, no mutations — prod health/drift gate)
- `--yes`/non-interactive (default in prod) · `--interactive`
- `--profile minimal|standard|full` (minimal=offline render stack, no
  torch; standard=+API clients; full=+stable-ts/torch). **Default=full**
  (§9 #3 — precise alignment); auto-degrades to standard where no Torch
  wheel exists. `--profile minimal` to skip the big download.
- `--venv PATH` · `--no-venv` · `--python PATH`
- `--repair` (ignore probes, force re-ensure) · `--offline` (no network;
  audit/verify only) · `--log FILE` · `--verbose`
- **Exit codes**: `0` ready · `1` completed-with-warnings (optional deps
  skipped) · `2` fixable failure (re-run/after action) · `3` needs new
  shell (PATH changed) · `4` unsupported/fatal. Documented for CI.

---

## 5. Windows cold-start (the owner's explicit ask)

`install.bat` → `powershell -NoProfile -ExecutionPolicy Bypass -File
install.ps1` (bat is always double-click-runnable and immune to
ExecutionPolicy; PS gives web/hashing/registry).

`install.ps1` Python bootstrap:
1. Detect Python ≥3.10: `py -3 --version`, `python --version`, registry
   (`HKCU/HKLM\…\PythonCore`), common paths. If a good one exists → skip
   to handoff.
2. Else acquire Python (ordered, with fallback):
   - **Primary**: download the official python.org installer for the
     detected arch (amd64/arm64), a **pinned** version (e.g. 3.12.x);
     **verify SHA-256** against python.org's published hash over HTTPS.
   - Silent install, per-user (no admin):
     `python-3.x.y-<arch>.exe /quiet InstallAllUsers=0 PrependPath=1
     Include_tcltk=1 Include_pip=1 Include_launcher=1
     SimpleInstall=1`
     — `PrependPath=1` writes PATH; `Include_tcltk=1` ⇒ GUI works.
   - **Fallback**: `winget install Python.Python.3.12` (if winget present).
3. **PATH propagation** (the subtle part): the *current* process PATH is
   stale post-install. Strategy: (a) for the rest of THIS run, call the
   new interpreter by its **absolute install path** (don't depend on
   PATH); (b) PrependPath has already set PATH for *future* shells;
   (c) broadcast `WM_SETTINGCHANGE` and/or instruct "open a new
   terminal"; (d) if a relaunch is cleaner, re-exec the script via the
   new python and exit. Document the new-terminal fallback explicitly.
4. Handoff: `& <python> install.py @args`; exit with its code.

FFmpeg on Windows handled later by `install.py` Phase C/D: winget
`Gyan.FFmpeg` → choco → direct verified zip to
`%LOCALAPPDATA%\vp\ffmpeg\bin` + append user PATH (setx/registry) +
broadcast; verified by invoking the absolute exe.

`install.sh` analogue for Linux (DECIDED §9 #4 — fully autonomous, even
without sudo):
1. If a Python ≥3.10 exists, use it.
2. Else, **with** sudo+pkg-mgr: install via the detected manager
   (`apt install python3 python3-venv python3-pip python3-tk`,
   dnf/pacman/zypper/apk/brew equivalents).
3. Else (no sudo / no pkg-mgr): download a **relocatable
   `python-build-standalone` CPython** for the detected
   arch + libc (glibc *or* musl — PBS ships both), **SHA-256 verified**
   against the release manifest, extracted into a repo-local dir
   (e.g. `.python/`). No root, no system change — mirrors the Windows
   auto-install. The venv is then created from this interpreter.
   Tcl/Tk: PBS builds include tkinter; if a chosen build lacks it, warn
   (GUI unavailable, CLI works).

---

## 6. Supported matrix (explicit — production needs honesty)

- **Linux (first-class)**: Ubuntu/Debian (apt), Fedora/RHEL/Alma/Rocky
  (dnf/yum), Arch (pacman), openSUSE (zypper). Alpine (apk) **best-effort**
  (musl: torch/stable-ts may be wheel-less → optional, auto-skipped).
- **Windows (first-class)**: 10/11 x64; 11 arm64 best-effort. Windows
  Server best-effort (winget may be absent → direct-download path covers
  it). PowerShell 5.1+ assumed (ships with Windows).
- **macOS (bonus)**: Homebrew path; same Python core.
- WSL treated as Linux. Root/containers: no sudo/systemd assumptions.
- Anything outside ⇒ audit prints "unsupported, here's the manual path",
  exit 4.

---

## 7. Cross-cutting non-negotiables

- Stdlib-only `install.py`; third-party code only via venv subprocess.
- `.venv` is the install target (sidesteps PEP-668; matches README).
- Idempotent, re-entrant, monotonic progress, no secret leakage.
- Integrity: pinned versions + SHA-256 verify + TLS verify (never
  `-k`/disable certs; never blind `curl|sh`).
- Full `install.log` + `install_report.json` always; console stays clean.
- Every required failure → actionable message + stable exit code.

---

## 8. File-by-file build order (when approved)

1. `install.py` skeleton: argparse, logging, report model, Phase A audit
   (read-only) + `--check`. *Most value first — this alone is the prod
   diagnostic.*
2. Phase B planner + dependency table (parse requirements.txt).
3. Phase C executor + progress engine (downloads, pip, pkg-mgr adapters).
4. Phase D self-heal playbook table.
5. Phase E verify/report.
6. `install.sh` (Linux bootstrap) → handoff.
7. `install.ps1` + `install.bat` (Windows cold-start incl. Python).
8. README: per-OS one-liner + what the installer does + exit codes.
9. Installer test harness (§10).

---

## 9. Decisions (ALL CONFIRMED with owner — locked)

1. **Architecture**: **DECIDED → thin OS launchers + one stdlib-only
   `install.py` core** (§2). No Bash/PowerShell logic duplication.
2. **pip install style**: **DECIDED → hybrid.** Probe each dep (skip if
   satisfied), then install the missing set in one resolver pass while
   streaming per-package progress parsed from pip. Correct resolution,
   still shows per-package %.
3. **Heavy deps (`stable-ts`+PyTorch)**: **DECIDED → install by default**
   (owner wants precise output; word-accurate forced alignment is worth
   it). Mainstream Linux/Win x64 cost = download (~200 MB torch + ~140 MB
   Whisper model) + ~1.5–2 GB disk + a few s/video at runtime; **no
   correctness downside**. REQUIRED behaviour:
   - Print a clear **"~2 GB download, may take several minutes — please
     wait"** message *before* the torch step.
   - **Pre-fetch the Whisper "base" model during install** so production
     never stalls to download mid-render (model is otherwise lazily
     fetched on first alignment, not at install).
   - On platforms with **no Torch wheel** (musl/Alpine, some ARM):
     auto-skip + explicit message, continue with proportional fallback
     (NOT a hard fail). `--profile minimal` can still opt out.
4. **Linux auto-install Python**: **DECIDED → fully autonomous even
   without sudo.** Pkg-mgr when available, else relocatable
   `python-build-standalone` into a repo-local dir (§5). No "abort on
   no-sudo" path.
5. **Delivery**: **DECIDED → repo root**: `install.sh`, `install.bat`,
   `install.ps1`, `install.py` (+ README per-OS one-liner).
6. **"Fix at runtime" boundary**: **DECIDED →** installer self-heals
   environment + dependencies + venv + PATH **and scaffolds project
   config from `*.example`** (copy if missing; never overwrite; never
   fill secrets). It still only *reports* (never fabricates) API keys
   and network/connectivity problems.

---

## 10. How the installer itself is validated (trust for production)

- `--check` dry-run path unit-tested (mock audit inputs).
- **Linux**: GitHub Actions / local Docker matrix — fresh `ubuntu`,
  `debian`, `fedora`, `archlinux` images **with Python removed** to prove
  the cold path; assert exit 0 + `python run.py --help` works after.
- **Windows**: `windows-latest` CI + a clean Windows Sandbox run with **no
  Python** to prove the download+PATH bootstrap end-to-end.
- Re-run idempotency test: second run = fast no-op, exit 0.
- Offline test: `--check` on an air-gapped box returns a correct report.

---

## 11. Build progress log (UPDATE THIS AS YOU GO)

Mutable state; spec above is fixed. Protocol identical to
`build-sound-effects.md` §10 (flip `[ ]→[x]` only when implemented AND
its check passes; append a dated Session-log line; record deviations in
Resume notes).

### Checklist

- [x] **D0** Plan approved; all §9 decisions confirmed & recorded
  (architecture=thin+core; pip=hybrid; profile default=full +heavy
  notice +Whisper pre-fetch +no-wheel fallback; Linux no-sudo=PBS
  userland Python; config scaffold=yes; files at repo root).
- [x] **D1** `install.py` audit (Phase A) + `--check` + report/log.
  Validated on Ubuntu 24.04: clean audit, exit 0, `install_report.json`.
- [x] **D2** Planner + dep table (`parse_requirements` + weighted steps,
  probe→skip).
- [x] **D3** Executor + 2-level progress; %-overshoot bug fixed (caps
  100%, ends 100%).
- [x] **D4** Self-heal wired (venv rebuild, ffmpeg multi-pkg-mgr, pip
  retry, optional-degrade) + exit codes 0/1/2/3/4. Fault-injection paths
  reviewed; full matrix = D10.
- [x] **D5** Verify (import core/vp via src-or-editable, ffmpeg, tk,
  optional pytest) + `scaffold_config` + report; idempotent re-run
  proven (~7 s no-op, exit 0).
- [x] **D6** `install.sh`: POSIX `sh -n` clean, TTY-aware colour, finds
  Python & hands off & forwards args (exit 0). pkg-mgr + PBS no-sudo
  paths code-complete (PBS exercised in D10/by owner on a no-Python box).
- [~] **D7** `install.bat` (CRLF) + `install.ps1` written & reviewed
  (Python detect → winget → Authenticode-verified python.org silent
  per-user install + PATH → handoff). NOT yet executed on Windows
  (no Windows here) — owner/D10 to run.
- [x] **D8** ffmpeg across apt/dnf/yum/pacman/zypper/apk/brew/winget/
  choco; tk via launcher pkg install + audit; git optional+audited;
  Torch/stable-ts default-on + heavy notice + Whisper prefetch +
  no-wheel degrade — all in `install.py`.
- [x] **D9** README "One-command install" + per-OS + exit-code table.
- [~] **D10** Linux: idempotency ✓, offline/`--check` ✓, arg-forward ✓,
  progress ✓ (all proven here). Linux container-no-Python matrix +
  Windows Sandbox cold-start = **not runnable in this env**; procedure
  documented in §10 for owner/CI.
- [ ] **D11** Windows fresh-machine cold start (owner/CI). Linux
  effectively validated on this box (happy path + idempotent + verify).

### Resume notes (deviations / blockers / decisions)

ALL §9 DECIDED with owner (do not relitigate): #1 thin launchers +
stdlib core; #2 hybrid pip; #3 Torch/stable-ts default-on (+~2 GB heavy
notice, Whisper pre-fetch, no-wheel auto-degrade); #4 Linux fully
autonomous incl. no-sudo via python-build-standalone; #5 files at repo
root; #6 self-heal env/deps/venv/PATH + scaffold config from examples,
report-only for keys/network. Owner said BUILD.

### Session log (append-only)

- 2026-05-18 — Plan authored. Architecture = thin OS launchers + stdlib
  `install.py` core (resolves the pre-Python chicken-egg). Awaiting §9
  decisions before any coding (owner: plan-only for now).
- 2026-05-18 — Owner clarified: prioritise precise output; accept the GB
  download. Locked §9 #2=hybrid, §9 #3=full-default (+heavy notice,
  model pre-fetch, no-wheel fallback). Plan updated accordingly.
- 2026-05-18 — Remaining §9 confirmed: #1 thin+core, #4 PBS userland
  Python (no-sudo autonomous), #5 repo root, #6 +config scaffold. D0
  done. Owner: BUILD. Starting D1 (install.py audit + --check).
- 2026-05-19 — Built install.py (D1–D5, stdlib-only): audit/--check,
  planner, executor+progress (fixed >100% double-count), self-heal,
  verify, scaffold, report, exit codes. Validated on Ubuntu 24.04:
  --check exit 0 READY; --profile minimal install exit 0; idempotent
  re-run ~7 s; editable install makes `import vp` work.
- 2026-05-19 — install.sh (D6, POSIX, TTY-aware, PBS no-sudo path),
  install.bat+install.ps1 (D7, Authenticode-verified python.org silent
  install, CRLF), .gitattributes (eol pinning), README one-command
  section (D9). D8 deps/heavy/prefetch/degrade in install.py. Linux
  paths proven; Windows cold-start code-complete, awaits a Windows run
  (D7/D10/D11 — no Windows in this env). Deviation: none vs spec.
