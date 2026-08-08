"""Launch the UniPulse AI Streamlit dashboard.

Usage
-----
    python run_frontend.py

(or manually with ``streamlit run frontend/app.py``).

The FastAPI backend must be running first::

    uvicorn app.main:app --reload

The dashboard then opens at http://localhost:8501.
"""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "frontend/app.py",
        "--server.port=8501",
        "--server.headless=false",
    ]
    print("Starting UniPulse AI dashboard at http://localhost:8501 ...")
    print("Backend must be running:  uvicorn app.main:app --reload")
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
