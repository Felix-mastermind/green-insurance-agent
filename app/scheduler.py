"""
Shared APScheduler instance — importable from any module without circular imports.
Initialized and started in main.py lifespan.
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

ET = pytz.timezone("America/New_York")
scheduler = AsyncIOScheduler(timezone=ET)
