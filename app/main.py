from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import close_pool, init_pool
from app.routes import applications, auth, dashboard, sync


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_pool()
    except Exception:
        pass
    yield
    try:
        await close_pool()
    except Exception:
        pass


def create_app() -> FastAPI:
    app = FastAPI(title="AppTrack", lifespan=lifespan)

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(applications.router)
    app.include_router(sync.router)

    return app


app = create_app()
