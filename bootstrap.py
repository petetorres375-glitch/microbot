#!/usr/bin/env python3
"""
bootstrap.py
------------
One command to get microbot ready: `python bootstrap.py`

It is pure Python so it runs identically on macOS, Linux, and Windows:
  1. Checks your Python version.
  2. Creates a local virtual environment in .venv (keeps deps isolated).
  3. Installs everything from requirements.txt into it.
  4. Copies .env.example -> .env if you don't have one yet.
  5. Tells you whether your Alpaca keys are filled in, and if so, offers to run
     a research-only scan (no orders) so you can see it work end to end.

Nothing here places a trade. Run it, then follow the printed next steps.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
MIN_PY = (3, 9)


def info(msg):  print(f"  -> {msg}")
def step(msg):  print(f"\n=== {msg} ===")
def warn(msg):  print(f"  ! {msg}")


def venv_python() -> Path:
    """Path to the python interpreter inside .venv (OS-dependent)."""
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def activate_hint() -> str:
    if os.name == "nt":
        return r".venv\Scripts\activate"
    return "source .venv/bin/activate"


def check_python():
    step("Checking Python")
    if sys.version_info < MIN_PY:
        warn(f"Python {MIN_PY[0]}.{MIN_PY[1]}+ required; you have "
             f"{sys.version_info.major}.{sys.version_info.minor}.")
        sys.exit(1)
    info(f"Python {sys.version.split()[0]} OK")


def make_venv():
    step("Virtual environment")
    if VENV.exists():
        info(".venv already exists — reusing it")
        return
    info("creating .venv ...")
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    info(".venv created")


def install_deps():
    step("Installing dependencies")
    py = str(venv_python())
    subprocess.check_call([py, "-m", "pip", "install", "--upgrade", "pip", "-q"])
    req = ROOT / "requirements.txt"
    if not req.exists():
        warn("requirements.txt not found — skipping")
        return
    subprocess.check_call([py, "-m", "pip", "install", "-r", str(req), "-q"])
    info("dependencies installed into .venv")


def setup_env() -> bool:
    """Return True if Alpaca keys look filled in."""
    step("Environment file (.env)")
    env, example = ROOT / ".env", ROOT / ".env.example"
    if not env.exists():
        if example.exists():
            shutil.copy(example, env)
            info("created .env from .env.example")
        else:
            warn(".env.example missing — create .env manually")
            return False
    else:
        info(".env already exists — leaving it untouched")

    text = env.read_text()
    placeholder = "your_paper_key_here" in text or "ALPACA_API_KEY=\n" in text
    has_key = "ALPACA_API_KEY=" in text and not placeholder
    if not has_key:
        warn("Add your Alpaca PAPER keys to .env before trading.")
        warn("Get free paper keys at https://app.alpaca.markets/")
    else:
        info("Alpaca keys appear to be set")
    return has_key


def maybe_research(has_key: bool):
    step("First run")
    if not has_key:
        info("Skipping the research scan until your keys are in .env.")
        return
    try:
        ans = input("Run a research-only scan now (no orders placed)? [y/N] ").strip().lower()
    except EOFError:
        ans = "n"
    if ans in ("y", "yes"):
        info("running research scan ...")
        subprocess.call([str(venv_python()), "run_research.py"])
    else:
        info("skipped — run it later with: python run_research.py")


def main():
    print("microbot bootstrap")
    check_python()
    make_venv()
    install_deps()
    has_key = setup_env()
    maybe_research(has_key)

    step("Next steps")
    print(f"""  1. Activate the environment:   {activate_hint()}
  2. (If not done) put PAPER keys in .env
  3. Research only, no orders:    python run_research.py
  4. Run one trading cycle:       python -m microbot.engine
  5. Open the dashboard:          streamlit run microbot/dashboard.py
  6. Analyze the bot's trades:    python run_analyzer.py

  Keep LIVE_TRADING=false for weeks. Not investment advice.
""")


if __name__ == "__main__":
    main()
