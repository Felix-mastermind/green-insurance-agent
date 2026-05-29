"""
Green Insurance CRM Agent
Main FastAPI application with scheduled jobs
"""
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
import asyncio
from contextlib import asynccontextmanager

from app.webhook_handler import router as webhook_router
from app.payment_reminders import run_payment_reminders
from app.renewal_reminders import run_renewal_reminders
from app.ghl_client import (
    GHLIntegrationError,
    get_contacts,
    get_opportunities,
    get_users,
    verify_location,
)

ET = pytz.timezone("America/New_York")
scheduler = AsyncIOScheduler(timezone=ET)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start scheduler on app startup"""
    print("[Agent] Green Insurance CRM Agent starting...")

    # Payment reminders - daily at 9:00am ET
    scheduler.add_job(
        run_payment_reminders,
        CronTrigger(hour=9, minute=0, timezone=ET),
        id="payment_reminders",
        name="Payment Reminders",
        replace_existing=True
    )

    # Renewal reminders - daily at 10:00am ET
    scheduler.add_job(
        run_renewal_reminders,
        CronTrigger(hour=10, minute=0, timezone=ET),
        id="renewal_reminders",
        name="Renewal Reminders",
        replace_existing=True
    )

    scheduler.start()
    print("[Agent] Scheduler started. Jobs: payment reminders (9am ET), renewals (10am ET)")
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

@app.get("/")
async def root():
    return {
        "name": "Green Insurance CRM Agent",
        "version": "1.0.0",
        "status": "running",
        "modules": [
            "payment_reminders",
            "renewal_reminders",
            "webhook_handler",
            "claude_agent"
        ]
    }

@app.post("/run/payment-reminders")
async def trigger_payment_reminders():
    """Manually trigger payment reminders (for testing)"""
    result = await run_payment_reminders()
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
