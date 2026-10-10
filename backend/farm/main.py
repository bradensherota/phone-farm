from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import settings as settings_mod
from .api import accounts, devices, jobs, misc
from .config import FRONTEND_DIST
from .db import init_db
from .state import manager, runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("farm")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings_mod.ensure_defaults()
    manager.seed_if_empty()
    try:
        from .db import session_scope
        from .models import Device
        with session_scope() as s:
            sims = s.query(Device).filter(Device.kind == "sim").all()
            if sims and not __import__("os").environ.get("PHONEFARM_SEED_SIMS"):
                for d in sims:
                    s.delete(d)
                log.info("removed %d sample (simulated) phones", len(sims))
    except Exception:
        log.exception("removing sample phones")
    manager.load()
    # Thumbnail generation can take a while on a big roll; don't hold up the UI for it.
    sync_task = asyncio.create_task(asyncio.to_thread(manager.sync_all), name="initial-sync")
    runner.start()
    manager.start_watchdog()
    from . import hover
    hover.start_warmer(manager)  # silent hover clips for album videos, while phones sit idle on the cable
    log.info("Phone Farm ready")
    try:
        yield
    finally:
        sync_task.cancel()
        await runner.stop()
        manager.shutdown()  # stops each phone's runner + port forward


app = FastAPI(title="Phone Farm", lifespan=lifespan)

api = FastAPI()
api.include_router(devices.router)
api.include_router(accounts.router)
api.include_router(jobs.router)
api.include_router(misc.router)
app.mount("/api", api)


@app.get("/healthz")
def healthz():
    return {"ok": True}


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"])
    def spa(path: str):
        target = FRONTEND_DIST / path
        if path and target.is_file():
            return FileResponse(target)
        return FileResponse(FRONTEND_DIST / "index.html")
else:

    @app.get("/")
    def no_frontend():
        return JSONResponse({"error": "frontend not built. Run: cd frontend && npm run build"}, status_code=503)
