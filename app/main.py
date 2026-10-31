import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.api.routes import router
from app.config import settings
from app.storage import StorageUnavailable, store


async def cleanup_reports() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(store.purge)
        except StorageUnavailable:
            pass


@asynccontextmanager
async def lifespan(application: FastAPI):
    await asyncio.to_thread(store.initialize)
    cleanup = asyncio.create_task(cleanup_reports())
    try:
        yield
    finally:
        cleanup.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup

app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description="Read-only RCA workspace. Live Azure/AWS diagnostic adapters are not yet implemented.",
    lifespan=lifespan,
)
app.include_router(router, prefix="/api/v1")
static_directory = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_directory, check_dir=False), name="static")


@app.exception_handler(StorageUnavailable)
async def storage_error(request, exception):
    return JSONResponse(status_code=503, content={"detail": "Database unavailable; retry later"})


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    if request.url.path == "/" or request.url.path.startswith("/static"):
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(static_directory / "index.html")


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


@app.get("/ready", tags=["system"])
def ready():
    available = store.ready()
    return JSONResponse(status_code=200 if available else 503, content={
        "status": "ready" if available else "unavailable",
        "storage": store.mode,
        "cloud_diagnostics": "not_configured",
    })
