import os
from supabase import create_client, Client
from datetime import datetime

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

async def log_message(contact_id: str, contact_name: str, contact_phone: str,
                       channel: str, message_type: str, message_body: str,
                       status: str = "sent", metadata: dict = None):
    """Log a sent message to Supabase"""
    try:
        sb = get_supabase()
        sb.table("messages_log").insert({
            "contact_id": contact_id,
            "contact_name": contact_name,
            "contact_phone": contact_phone,
            "channel": channel,
            "message_type": message_type,
            "message_body": message_body,
            "status": status,
            "metadata": metadata or {}
        }).execute()
    except Exception as e:
        print(f"[Supabase] Error logging message: {e}")

async def check_reminder_sent(contact_id: str, reminder_type: str, month_year: str) -> bool:
    """Check if a payment reminder was already sent this month"""
    try:
        sb = get_supabase()
        result = sb.table("payment_reminders_log").select("id").eq(
            "contact_id", contact_id
        ).eq("reminder_type", reminder_type).eq("month_year", month_year).execute()
        return len(result.data) > 0
    except Exception as e:
        print(f"[Supabase] Error checking reminder: {e}")
        return False

async def log_reminder_sent(contact_id: str, contact_name: str,
                             payment_day: int, reminder_type: str, month_year: str):
    """Log that a payment reminder was sent"""
    try:
        sb = get_supabase()
        sb.table("payment_reminders_log").insert({
            "contact_id": contact_id,
            "contact_name": contact_name,
            "payment_day": payment_day,
            "reminder_type": reminder_type,
            "month_year": month_year
        }).execute()
    except Exception as e:
        print(f"[Supabase] Error logging reminder: {e}")

async def save_conversation_message(contact_id: str, role: str, content: str, channel: str = "sms"):
    """Save a conversation message"""
    try:
        sb = get_supabase()
        sb.table("conversation_messages").insert({
            "contact_id": contact_id,
            "role": role,
            "content": content,
            "channel": channel
        }).execute()
    except Exception as e:
        print(f"[Supabase] Error saving message: {e}")

async def get_conversation_history(contact_id: str, limit: int = 10) -> list:
    """Get recent conversation messages for a contact"""
    try:
        sb = get_supabase()
        result = sb.table("conversation_messages").select("*").eq(
            "contact_id", contact_id
        ).order("created_at", desc=True).limit(limit).execute()
        return list(reversed(result.data))
    except Exception as e:
        print(f"[Supabase] Error getting history: {e}")
        return []
