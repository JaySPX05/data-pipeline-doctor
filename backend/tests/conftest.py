"""Tests run fully offline: no Groq key, no Hindsight, temp SQLite."""
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.sqlite3"
os.environ["GROQ_API_KEY"] = ""
os.environ["MEMORY_ENABLED"] = "false"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
