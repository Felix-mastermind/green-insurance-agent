"""
Green Insurance CRM Agent
Main FastAPI application with scheduled jobs
"""
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from apscheduler.triggers.cron import CronTrigger
import asyncio
from contextlib import asynccontextmanager
from app.scheduler import scheduler, ET

from app.webhook_handler import router as webhook_router
from app.supervisor import router as supervisor_router
from app.renewal_reminders import run_renewal_reminders
from app.follow_ups import run_follow_ups
from app.ghl_client import (
    GHLIntegrationError,
    get_conversations,
    get_contacts,
    get_opportunities,
    get_pipelines,
    get_users,
    verify_location,
)


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
    """Start scheduler on app startup"""
    print("[Agent] Green Insurance CRM Agent starting...")
    print("[Agent] Registered routes:")
    for route in registered_routes():
        print(f"[Route] {','.join(route['methods'])} {route['path']} -> {route['name']}")


    # Sin jobs proactivos — bot solo responde inbound para agendar citas

    scheduler.start()

    print("[Agent] Scheduler started. No proactive jobs.")
    print("[Agent] Ready to receive webhooks from GHL")

    yield

    scheduler.shutdown()
    print("[Agent] Scheduler stopped")

app = FastAPI(
    title="Green Insurance CRM Agent",
    description="Automated CRM agent for Green Insurance",
    version="1.0.0",
    lifespan=lifespan
)

# Include webhook routes
app.include_router(webhook_router)
app.include_router(supervisor_router)

@app.get("/")
async def root():
    return {
        "name": "Green Insurance CRM Agent",
        "version": "1.0.0",
        "status": "running",
        "modules": [
            "renewal_reminders",
            "webhook_handler",
            "claude_agent"
        ]
    }

@app.post("/run/follow-ups")
async def trigger_follow_ups():
    """Manually trigger follow-ups"""
    result = await run_follow_ups()
    return result

@app.post("/run/renewal-reminders")
async def trigger_renewal_reminders():
    """Manually trigger renewal reminders (for testing)"""
    result = await run_renewal_reminders()
    return result

@app.get("/health/ghl")
async def ghl_health_check():
    """Verify GoHighLevel token and location access."""
    try:
        await verify_location()
        contacts = await get_contacts()
        opportunities = await get_opportunities()
        users = await get_users()
    except GHLIntegrationError as e:
        print(
            "[GHL Health] Error | "
            f"endpoint={e.endpoint} | "
            f"status_code={e.ghl_status or e.status_code} | "
            f"response_body={e.ghl_response}"
        )
        return JSONResponse(
            status_code=e.status_code,
            content={
                "status": "error",
                "ghl_status": e.ghl_status or e.status_code,
                "ghl_response": e.ghl_response,
                "endpoint": e.endpoint,
            },
        )
    except Exception as e:
        print(f"[GHL Health] Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Unexpected GHL health check error")

    return {
        "status": "connected",
        "contacts": len(contacts),
        "opportunities": len(opportunities),
        "users": len(users)
    }

@app.get("/routes")
async def routes():
    return {"routes": registered_routes()}

@app.get("/health/contacts")
async def contacts_health_check():
    """Return the GoHighLevel contact count and a sample contact."""
    try:
        contacts = await get_contacts()
    except GHLIntegrationError as e:
        print(
            "[GHL Contacts Health] Error | "
            f"endpoint={e.endpoint} | "
            f"status_code={e.ghl_status or e.status_code} | "
            f"response_body={e.ghl_response}"
        )
        return JSONResponse(
            status_code=e.status_code,
            content={
                "status": "error",
                "ghl_status": e.ghl_status or e.status_code,
                "ghl_response": e.ghl_response,
                "endpoint": e.endpoint,
            },
        )
    except Exception as e:
        print(f"[GHL Contacts Health] Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Unexpected GHL contacts health check error")

    return {
        "count": len(contacts),
        "sample_contact": contacts[0] if contacts else None
    }

async def ghl_collection_health(label: str, loader):
    try:
        records = await loader()
    except GHLIntegrationError as e:
        print(
            f"[GHL {label} Health] Error | "
            f"endpoint={e.endpoint} | "
            f"status_code={e.ghl_status or e.status_code} | "
            f"response_body={e.ghl_response}"
        )
        return JSONResponse(
            status_code=e.status_code,
            content={
                "status": "error",
                "ghl_status": e.ghl_status or e.status_code,
                "ghl_response": e.ghl_response,
                "endpoint": e.endpoint,
            },
        )
    except Exception as e:
        print(f"[GHL {label} Health] Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Unexpected GHL {label.lower()} health check error")

    return {
        "count": len(records),
        "sample": records[0] if records else None
    }

@app.get("/health/users")
async def users_health_check():
    return await ghl_collection_health("Users", get_users)

@app.get("/health/pipelines")
async def pipelines_health_check():
    return await ghl_collection_health("Pipelines", get_pipelines)

@app.get("/health/opportunities")
async def opportunities_health_check():
    return await ghl_collection_health("Opportunities", get_opportunities)

@app.get("/health/conversations")
async def conversations_health_check():
    return await ghl_collection_health("Conversations", get_conversations)
