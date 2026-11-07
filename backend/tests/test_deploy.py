"""Hosted-demo settings: the SQLite snapshot restore and the CORS origin regex. Offline."""
import re
from pathlib import Path

from app.db.database import restore_demo_snapshot

RENDER_YAML = Path(__file__).resolve().parents[2] / "render.yaml"


def test_snapshot_is_copied_when_the_database_is_missing(tmp_path):
    snap = tmp_path / "snap.sqlite3"
    snap.write_bytes(b"demo-db")
    db = tmp_path / "live" / "pfd.sqlite3"
    db.parent.mkdir()
    assert restore_demo_snapshot(db, str(snap)) is True
    assert db.read_bytes() == b"demo-db"


def test_an_existing_database_is_never_overwritten(tmp_path):
    snap = tmp_path / "snap.sqlite3"
    snap.write_bytes(b"demo-db")
    db = tmp_path / "pfd.sqlite3"
    db.write_bytes(b"live-data")
    assert restore_demo_snapshot(db, str(snap)) is False
    assert db.read_bytes() == b"live-data"


def test_missing_or_unset_snapshot_just_starts_empty(tmp_path):
    db = tmp_path / "pfd.sqlite3"
    assert restore_demo_snapshot(db, "") is False
    assert restore_demo_snapshot(db, str(tmp_path / "nope.sqlite3")) is False
    assert not db.exists()


def test_render_cors_regex_allows_only_our_hosted_ui():
    # the value in render.yaml; Starlette full-matches the Origin header against it
    value = next(line.split("value:", 1)[1].strip() for line in RENDER_YAML.read_text().splitlines()
                 if "pipeline-failure-doctor(" in line)
    rx = re.compile(value)
    assert rx.fullmatch("https://pipeline-failure-doctor.onrender.com")
    assert rx.fullmatch("https://pipeline-failure-doctor-x7k2.onrender.com")  # Render's suffix
    assert not rx.fullmatch("https://pipeline-failure-doctor-api.onrender.com.evil.com")
    assert not rx.fullmatch("http://pipeline-failure-doctor.onrender.com")
    assert not rx.fullmatch("https://someone-else.onrender.com")
    assert not rx.fullmatch("https://evil.example.com")
