"""
Green Insurance CRM Agent
Bot de citas — solo responde inbound fuera de horario para agendar citas.
"""
from fastapi import FastAPI
from fastapi.routing import APIRoute
from contextlib import asynccontextmanager
from app.scheduler import scheduler

from app.webhook_handler import router as webhook_router
from app.supervisor import router as supervisor_router


def registered_routes() -> list:
    routes = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            routes.append({
                "path": route.path,
                "name": route.name,
                "methods": sorted(route.methods),
            })
    return sorted(routes, key=lambda item: item["path"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[Agent] Green Insurance CRM Agent starting...")
    scheduler.start()
    print("[Agent] Scheduler started. Sin jobs proactivos — bot solo agenda citas.")
    yield
    scheduler.shutdown()
    print("[Agent] Scheduler stopped")


app = FastAPI(
    title="Green Insurance CRM Agent",
    description="Bot de citas — solo responde inbound fuera de horario",
    version="2.0.0",
    lifespan=lifespan
)

app.include_router(webhook_router)
app.include_router(supervisor_router)


@app.get("/")
async def root():
    return {
        "name": "Green Insurance CRM Agent",
        "version": "2.0.0",
        "status": "running",
        "mode": "appointments-only",
    }


@app.get("/routes")
async def routes():
    return {"routes": registered_routes()}
