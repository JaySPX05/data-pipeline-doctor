import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import failures, incidents, memory, metrics
from app.config import settings
from app.db.database import init_db
from app.deps import get_llm, get_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Pipeline Failure Doctor",
    description="On-call agent that diagnoses data pipeline failures from the team's Hindsight memory.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (failures.router, incidents.router, memory.router, metrics.router):
    app.include_router(router)


@app.get("/api/health")
def health() -> dict:
    llm = get_llm()
    return {
        "status": "ok",
        "llm": {"available": llm.available, "model": settings.llm_model, "fallback": settings.llm_fallback_model},
        "memory": get_store().status(),
    }
