# Vercel entry point (Day 5). Serves the unchanged FastAPI app from backend/app.
# config.py finds the project root three folders above itself, so data/ is read
# from the repository root exactly as in local runs; vercel.json bundles it.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.main import app  # noqa: E402,F401
