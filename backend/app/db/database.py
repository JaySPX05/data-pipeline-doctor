import logging
import shutil
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

log = logging.getLogger(__name__)

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from app.db import tables  # noqa: F401  (registers the models)

    if settings.database_url.startswith("sqlite:///"):
        db_file = Path(settings.database_url.removeprefix("sqlite:///"))
        db_file.parent.mkdir(parents=True, exist_ok=True)
        restore_demo_snapshot(db_file)
    Base.metadata.create_all(engine)


def restore_demo_snapshot(db_file: Path, snapshot: str | None = None) -> bool:
    """Copy the demo snapshot in when the database doesn't exist yet (hosted demo only)."""
    snapshot = settings.demo_snapshot if snapshot is None else snapshot
    if not snapshot or db_file.exists():
        return False
    src = Path(snapshot)
    if not src.is_absolute():
        src = settings.data_dir.parent / src
    if not src.exists():
        log.warning("DEMO_SNAPSHOT %s not found; starting with an empty incident store", src)
        return False
    shutil.copyfile(src, db_file)
    log.info("restored demo snapshot %s -> %s", src, db_file)
    return True


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
