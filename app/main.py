from fastapi import FastAPI
from app.api.routes import router
from app.config import settings

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Starter scaffold for a deterministic, read-only multi-cloud RCA engine. "
        "Cloud integrations and production event authentication must be configured before deployment."
    ),
)
app.include_router(router, prefix="/api/v1")


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


@app.get("/ready", tags=["system"])
def ready() -> dict[str, str]:
    return {"status": "ready", "note": "Application process is ready; this does not verify cloud connectivity."}
