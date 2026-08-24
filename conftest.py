# Make the src/ layout importable for pytest without packaging or PYTHONPATH —
# the CI test step runs plain `python -m pytest tests/` from the repo root.
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))
