"""
Supabase client using direct REST API calls via httpx
Avoids supabase-py proxy/httpx version incompatibilities
"""
import os
import httpx
from datetime import datetime

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

def get_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

def rest_url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"


async def _insert(table: str, data: dict) -> bool:
    """Insert a row into a Supabase table"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(rest_url(table), headers=get_headers(), json=data)
            return r.status_code in (200, 201)
    except Exception as e:
        print(f"[Supabase] Insert error ({table}): {e}")
        return False


async def _select(table: str, filters: dict, columns: str = "*", limit: int = 100, order: str = None) -> list:
    """Select rows from a Supabase table"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        headers = {**get_headers(), "Prefer": ""}
        params = {**filters, "select": columns, "limit": limit}
        if order:
            params["order"] = order
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(rest_url(table), headers=headers, params=params)
            if r.status_code == 200:
                return r.json()
            return []
    except Exception as e:
        print(f"[Supabase] Select error ({table}): {e}")
        return []


async def log_message(contact_id: str, contact_name: str, contact_phone: str,
                      channel: str, message_type: str, message_body: str,
                      status: str = "sent", metadata: dict = None):
    """Log a sent message to Supabase"""
    await _insert("messages_log", {
        "contact_id": contact_id,
        "contact_name": contact_name,
        "contact_phone": contact_phone,
        "channel": channel,
        "message_type": message_type,
        "message_body": message_body,
        "status": status,
        "metadata": metadata or {}
    })


async def check_reminder_sent(contact_id: str, reminder_type: str, month_year: str) -> bool:
    """Check if a payment reminder was already sent this month"""
    rows = await _select(
        "payment_reminders_log",
        {"contact_id": f"eq.{contact_id}", "reminder_type": f"eq.{reminder_type}", "month_year": f"eq.{month_year}"},
        columns="id",
        limit=1
    )
    return len(rows) > 0


async def log_reminder_sent(contact_id: str, contact_name: str,
                             payment_day: int, reminder_type: str, month_year: str):
    """Log that a payment reminder was sent"""
    await _insert("payment_reminders_log", {
        "contact_id": contact_id,
        "contact_name": contact_name,
        "payment_day": payment_day,
        "reminder_type": reminder_type,
        "month_year": month_year
    })


async def save_conversation_message(contact_id: str, role: str, content: str, channel: str = "whatsapp"):
    """Save a conversation message"""
    await _insert("conversation_messages", {
        "contact_id": contact_id,
        "role": role,
        "content": content,
        "channel": channel
    })


async def get_conversation_history(contact_id: str, limit: int = 10) -> list:
    """Get recent conversation messages for a contact"""
    rows = await _select(
        "conversation_messages",
        {"contact_id": f"eq.{contact_id}"},
        columns="role,content,created_at",
        limit=limit,
        order="created_at.desc"
    )
    return list(reversed(rows))
