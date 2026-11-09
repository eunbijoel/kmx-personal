from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.frontend_routes import router as frontend_router
from api.manufacturing_routes import router as manufacturing_router
from api.routes import router
from db.database import init_db
from pathlib import Path

app = FastAPI(
    title="KMX Manufacturing-X Data Space Platform",
    version="0.1.0",
    description="Reference implementation for DataSpace + AI + Governance integration.",
)
app.include_router(router)
app.include_router(frontend_router)
app.include_router(manufacturing_router)

ROOT_DIR = Path(__file__).resolve().parents[1]
app.mount("/static", StaticFiles(directory=str(ROOT_DIR / "frontend" / "static")), name="static")


@app.on_event("startup")
async def on_startup() -> None:
    await init_db()
