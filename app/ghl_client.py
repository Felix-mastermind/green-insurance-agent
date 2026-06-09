import os
import logging
from typing import Optional

import httpx

BASE_URL = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"
DEFAULT_LIMIT = 100

logger = logging.getLogger(__name__)


class GHLIntegrationError(Exception):
    """Raised when the GoHighLevel API cannot be reached or authenticated."""

    def __init__(
        self,
        message: str,
        status_code: int = 502,
        endpoint: str = "",
        ghl_status: int | None = None,
        ghl_response: str = "",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint
        self.ghl_status = ghl_status
        self.ghl_response = ghl_response or message


def get_ghl_config() -> tuple[str, str]:
    token = os.getenv("GHL_TOKEN")
    location_id = os.getenv("GHL_LOCATION")

    if not token:
        raise GHLIntegrationError("GHL_TOKEN is not configured", status_code=500)
    if not location_id:
        raise GHLIntegrationError("GHL_LOCATION is not configured", status_code=500)

    return token, location_id


def get_headers() -> dict:
    token, _ = get_ghl_config()
    return {
        "Authorization": f"Bearer {token}",
        "Version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def request_ghl(method: str, path: str, **kwargs) -> dict:
    url = f"{BASE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(method, url, headers=get_headers(), **kwargs)
    except httpx.HTTPError as exc:
        logger.exception("[GHL] Request failed: %s %s", method, path)
        raise GHLIntegrationError(
            f"GHL request failed: {exc}",
            endpoint=path,
            ghl_response=str(exc),
        ) from exc

    if response.status_code >= 400:
        logger.error(
            "[GHL] API error | endpoint=%s | status_code=%s | response_body=%s",
            path,
            response.status_code,
            response.text,
        )
        raise GHLIntegrationError(
            response.text,
            status_code=response.status_code,
            endpoint=path,
            ghl_status=response.status_code,
            ghl_response=response.text,
        )

    try:
        return response.json()
    except ValueError as exc:
        logger.exception("[GHL] Invalid JSON response from %s %s", method, path)
        raise GHLIntegrationError("GHL API returned invalid JSON") from exc


def extract_items(data: dict, key: str) -> list:
    value = data.get(key, [])
    return value if isinstance(value, list) else []


def get_conversation_cursor(conversation: dict) -> str | None:
    for key in ("lastMessageDate", "lastMessageAt", "dateUpdated", "updatedAt", "dateAdded", "createdAt"):
        value = conversation.get(key)
        if value:
            return value
    return None


async def paginate_by_skip(path: str, key: str, params: dict, limit: int = DEFAULT_LIMIT) -> list:
    items = []
    skip = 0

    while True:
        page_params = {**params, "limit": limit, "skip": skip}
        data = await request_ghl("GET", path, params=page_params)
        batch = extract_items(data, key)
        items.extend(batch)

        meta = data.get("meta") or {}
        total = meta.get("total")
        if len(batch) < limit or (isinstance(total, int) and len(items) >= total):
            break

        skip += limit

    logger.info("[GHL] Fetched %s %s", len(items), key)
    return items


async def paginate_contacts(params: dict, limit: int = DEFAULT_LIMIT) -> list:
    items = []
    cursor_params = {}

    while True:
        page_params = {**params, **cursor_params, "limit": limit}
        data = await request_ghl("GET", "/contacts/", params=page_params)
        batch = extract_items(data, "contacts")
        items.extend(batch)

        meta = data.get("meta") or {}
        total = meta.get("total")
        start_after = meta.get("startAfter")
        start_after_id = meta.get("startAfterId")

        if len(batch) < limit or (isinstance(total, int) and len(items) >= total):
            break
        if not start_after or not start_after_id:
            break

        cursor_params = {
            "startAfter": start_after,
            "startAfterId": start_after_id,
        }

    logger.info("[GHL] Fetched %s contacts", len(items))
    return items


async def paginate_conversations(params: dict, limit: int = DEFAULT_LIMIT) -> list:
    items = []
    cursor_params = {}

    while True:
        page_params = {**params, **cursor_params, "limit": limit}
        data = await request_ghl("GET", "/conversations/search", params=page_params)
        batch = extract_items(data, "conversations")
        items.extend(batch)

        meta = data.get("meta") or {}
        total = meta.get("total")
        start_after_date = meta.get("startAfterDate")
        if not start_after_date and batch:
            start_after_date = get_conversation_cursor(batch[-1])

        if len(batch) < limit or (isinstance(total, int) and len(items) >= total):
            break
        if not start_after_date:
            break

        cursor_params = {"startAfterDate": start_after_date}

    logger.info("[GHL] Fetched %s conversations", len(items))
    return items


async def paginate_by_page(path: str, key: str, params: dict, limit: int = DEFAULT_LIMIT) -> list:
    items = []
    page = 1

    while True:
        page_params = {**params, "limit": limit, "page": page}
        data = await request_ghl("GET", path, params=page_params)
        batch = extract_items(data, key)
        items.extend(batch)

        meta = data.get("meta") or {}
        next_page = meta.get("nextPage")
        total = meta.get("total")
        if not next_page and (len(batch) < limit or (isinstance(total, int) and len(items) >= total)):
            break

        page = next_page or page + 1

    logger.info("[GHL] Fetched %s %s", len(items), key)
    return items


async def verify_location() -> dict:
    _, location_id = get_ghl_config()
    data = await request_ghl("GET", f"/locations/{location_id}")
    logger.info("[GHL] Location verified: %s", location_id)
    return data


async def get_contacts() -> list:
    _, location_id = get_ghl_config()
    return await paginate_contacts({"locationId": location_id})


async def get_opportunities() -> list:
    _, location_id = get_ghl_config()
    return await paginate_by_page("/opportunities/search", "opportunities", {"location_id": location_id})


async def get_pipelines() -> list:
    _, location_id = get_ghl_config()
    data = await request_ghl("GET", "/opportunities/pipelines", params={"locationId": location_id})
    pipelines = extract_items(data, "pipelines")
    logger.info("[GHL] Fetched %s pipelines", len(pipelines))
    return pipelines


async def get_conversations() -> list:
    _, location_id = get_ghl_config()
    return await paginate_conversations({"locationId": location_id})


async def get_users() -> list:
    _, location_id = get_ghl_config()
    data = await request_ghl("GET", "/users/", params={"locationId": location_id})
    users = extract_items(data, "users")
    logger.info("[GHL] Fetched %s users", len(users))
    return users

async def get_contacts_by_tag(tag: str, limit: int = 100) -> list:
    """Get all contacts with a specific tag"""
    _, location_id = get_ghl_config()
    return await paginate_contacts(
        {"locationId": location_id, "tags": tag},
        limit=limit,
    )

async def get_contact(contact_id: str) -> Optional[dict]:
    """Get a single contact by ID"""
    data = await request_ghl("GET", f"/contacts/{contact_id}")
    return data.get("contact")

async def get_contact_channel(contact_id: str) -> str:
    """Get the actual channel type of the contact's main conversation"""
    try:
        _, location_id = get_ghl_config()
        data = await request_ghl(
            "GET",
            "/conversations/search",
            params={"locationId": location_id, "contactId": contact_id, "limit": 1}
        )
        convs = extract_items(data, "conversations")
        if not convs:
            return "WhatsApp"
        conv = convs[0]
        # GHL conversation type field
        # Use lastMessageType — most reliable indicator of channel
        last_msg_type = conv.get("lastMessageType", "") or conv.get("type", "") or ""
        logger.info("[GHL] lastMessageType for %s: %s", contact_id, last_msg_type)
        last_upper = str(last_msg_type).upper()
        if "SMS" in last_upper or last_msg_type in ("1", 1, "TYPE_SMS"):
            return "SMS"
        if "WHATSAPP" in last_upper or last_msg_type in (19, "19", 7, "7", "TYPE_WHATSAPP"):
            return "WhatsApp"
        if "EMAIL" in last_upper:
            return "Email"
        # Default to WhatsApp (most common channel)
        return "WhatsApp"
    except Exception as e:
        logger.error("[GHL] Error getting channel for %s: %s", contact_id, e)
        return "WhatsApp"

async def get_contact_conversation_id(contact_id: str) -> str:
    """Get the main conversation ID for a contact"""
    _, location_id = get_ghl_config()
    try:
        data = await request_ghl(
            "GET",
            "/conversations/search",
            params={"locationId": location_id, "contactId": contact_id, "limit": 1}
        )
        convs = extract_items(data, "conversations")
        if convs:
            return convs[0].get("id", "")
    except Exception as e:
        logger.error("[GHL] Error getting conversation for %s: %s", contact_id, e)
    return ""

async def add_internal_note(contact_id: str, note: str) -> dict:
    """Add an internal note to a contact's conversation (visible only to agents)"""
    try:
        conv_id = await get_contact_conversation_id(contact_id)
        if not conv_id:
            logger.error("[GHL] No conversation found for contact %s", contact_id)
            return {}
        return await request_ghl(
            "POST",
            "/conversations/messages",
            json={
                "type": "Activity",
                "conversationId": conv_id,
                "html": f"<p>🤖 <strong>Agente IA:</strong> {note}</p>",
                "body": f"🤖 Agente IA: {note}",
            }
        )
    except Exception as e:
        logger.error("[GHL] Error adding internal note to %s: %s", contact_id, e)
        return {}

async def create_task(contact_id: str, title: str, assigned_to: str = "", due_hours: int = 2) -> dict:
    """Create a task in GHL assigned to an agent"""
    from datetime import datetime, timezone, timedelta
    due_date = (datetime.now(timezone.utc) + timedelta(hours=due_hours)).isoformat()
    body = {
        "title": title,
        "contactId": contact_id,
        "dueDate": due_date,
        "completed": False,
    }
    if assigned_to:
        body["assignedTo"] = assigned_to
    try:
        return await request_ghl("POST", f"/contacts/{contact_id}/tasks", json=body)
    except Exception as e:
        logger.error("[GHL] Error creating task for %s: %s", contact_id, e)
        return {}

async def add_contact_tag(contact_id: str, tag: str) -> dict:
    """Add a tag to a contact in GHL"""
    try:
        return await request_ghl(
            "POST",
            f"/contacts/{contact_id}/tags",
            json={"tags": [tag]}
        )
    except Exception as e:
        logger.error("[GHL] Error adding tag %s to %s: %s", tag, contact_id, e)
        return {}


async def remove_contact_tag(contact_id: str, tag: str) -> dict:
    """Remove a tag from a contact in GHL"""
    try:
        return await request_ghl(
            "DELETE",
            f"/contacts/{contact_id}/tags",
            json={"tags": [tag]}
        )
    except Exception as e:
        logger.error("[GHL] Error removing tag %s from %s: %s", tag, contact_id, e)
        return {}


async def is_bot_paused(contact_id: str) -> bool:
    """Returns True if contact has 'bot-pausado' tag — bot should not respond."""
    try:
        contact = await get_contact(contact_id)
        if not contact:
            return False
        tags = contact.get("tags", []) or []
        return "bot-pausado" in [t.lower() for t in tags]
    except Exception as e:
        logger.error("[GHL] Error checking bot-pausado tag for %s: %s", contact_id, e)
        return False

# Bot user ID — messages appear as "Asistente Green" in GHL
BOT_USER_ID = "rM9FFKJ79TshgMmOZ7Nn"

async def send_sms(contact_id: str, message: str) -> dict:
    """Send SMS to a contact as Asistente Green.
    Uses conversationId as primary key so GHL respects userId attribution.
    """
    conv_id = await get_contact_conversation_id(contact_id)
    if conv_id:
        # Preferred: conversationId only — GHL respects userId when contactId is absent
        payload = {
            "type": "SMS",
            "conversationId": conv_id,
            "contactId": contact_id,
            "message": message,
            "userId": BOT_USER_ID,
        }
    else:
        # Fallback: contactId only (GHL may attribute to contact owner)
        payload = {
            "type": "SMS",
            "contactId": contact_id,
            "message": message,
            "userId": BOT_USER_ID,
        }
    result = await request_ghl("POST", "/conversations/messages", json=payload)
    logger.info("[GHL] SMS sent | convId=%s userId=%s | resp=%s",
                conv_id or "none", BOT_USER_ID, str(result)[:120])
    return result


async def send_whatsapp(contact_id: str, message: str) -> dict:
    """Send WhatsApp message to a contact as Asistente Green.
    Uses conversationId as primary key so GHL respects userId attribution.
    """
    conv_id = await get_contact_conversation_id(contact_id)
    if conv_id:
        payload = {
            "type": "WhatsApp",
            "conversationId": conv_id,
            "contactId": contact_id,
            "message": message,
            "userId": BOT_USER_ID,
        }
    else:
        payload = {
            "type": "WhatsApp",
            "contactId": contact_id,
            "message": message,
            "userId": BOT_USER_ID,
        }
    result = await request_ghl("POST", "/conversations/messages", json=payload)
    logger.info("[GHL] WhatsApp sent | convId=%s userId=%s | resp=%s",
                conv_id or "none", BOT_USER_ID, str(result)[:120])
    return result


async def add_bot_stamp(contact_id: str) -> None:
    """Add a visible internal Activity note identifying the previous message as bot-sent.
    This lets advisors distinguish bot messages from their own in the GHL conversation view.
    """
    try:
        conv_id = await get_contact_conversation_id(contact_id)
        if not conv_id:
            return
        await request_ghl(
            "POST",
            "/conversations/messages",
            json={
                "type": "Activity",
                "conversationId": conv_id,
                "html": "<p>🤖 <strong>Asistente Green</strong> — mensaje automático</p>",
                "body": "🤖 Asistente Green — mensaje automático",
            }
        )
    except Exception as e:
        logger.warning("[GHL] Could not add bot stamp for %s: %s", contact_id, e)

# HOT Leads stage IDs per pipeline
HOT_LEADS_STAGES = {
    "HzCwe9SCtirKXGFdFLVT": "32534212-1d9f-460b-90cc-f1eb40e3e04d",  # Dental
    "BdzkOH5twVi9sCK2ag96": "e53564ac-4518-4b5d-9a51-daaaf4eb10e2",  # AUTO - Mastermind
    "XrTzKSNz9VpYuSvVZzyH": "cbc91e6d-7750-4788-ba1b-8d1fd30cba3a",  # Life
}

async def get_contact_opportunities(contact_id: str) -> list:
    """Get opportunities for a contact"""
    _, location_id = get_ghl_config()
    try:
        data = await request_ghl(
            "GET",
            "/opportunities/search",
            params={"location_id": location_id, "contact_id": contact_id, "limit": 5}
        )
        return extract_items(data, "opportunities")
    except Exception as e:
        logger.error("[GHL] Error getting opportunities for %s: %s", contact_id, e)
        return []

async def move_to_hot_lead(contact_id: str) -> bool:
    """Move contact's opportunity to HOT Leads stage based on their pipeline"""
    opportunities = await get_contact_opportunities(contact_id)
    if not opportunities:
        logger.warning("[GHL] No opportunities found for contact %s", contact_id)
        return False
    moved = False
    for opp in opportunities:
        pipeline_id = opp.get("pipelineId", "")
        hot_stage_id = HOT_LEADS_STAGES.get(pipeline_id)
        if hot_stage_id:
            opp_id = opp.get("id", "")
            await update_contact_stage(opp_id, hot_stage_id)
            logger.info("[GHL] Moved opportunity %s to HOT Leads (pipeline %s)", opp_id, pipeline_id)
            moved = True
    return moved

async def update_contact_stage(opportunity_id: str, stage_id: str) -> dict:
    """Update opportunity stage"""
    return await request_ghl(
        "PUT",
        f"/opportunities/{opportunity_id}",
        json={"stageId": stage_id}
    )

async def reassign_opportunity(opportunity_id: str, user_id: str) -> dict:
    """Reassign an opportunity to a different user"""
    return await request_ghl(
        "PUT",
        f"/opportunities/{opportunity_id}",
        json={"assignedTo": user_id}
    )

async def get_contact_custom_field(contact: dict, field_id: str) -> Optional[str]:
    """Extract a custom field value from a contact"""
    for cf in contact.get("customFields", []):
        if cf.get("id") == field_id:
            return cf.get("value")
    return None

async def search_contacts(query: str) -> list:
    """Search contacts by phone or name"""
    _, location_id = get_ghl_config()
    data = await request_ghl(
        "GET",
        "/contacts/",
        params={"locationId": location_id, "query": query, "limit": 5}
    )
    return extract_items(data, "contacts")

async def get_contact_conversations(contact_id: str) -> list:
    """Get conversations for a contact"""
    _, location_id = get_ghl_config()
    data = await request_ghl(
        "GET",
        "/conversations/search",
        params={"locationId": location_id, "contactId": contact_id, "limit": 5}
    )
    return extract_items(data, "conversations")

async def get_conversation_messages(conversation_id: str, limit: int = 10) -> list:
    """Get messages from a conversation"""
    data = await request_ghl(
        "GET",
        f"/conversations/{conversation_id}/messages",
        params={"limit": limit}
    )
    # Messages can be under different keys
    for key in ("messages", "data", "items"):
        msgs = data.get(key, [])
        if msgs:
            return msgs if isinstance(msgs, list) else []
    return []

def is_business_hours() -> bool:
    """Returns True if current time is within business hours: 11am-7pm ET Mon-Sun"""
    import pytz
    from datetime import datetime
    ET = pytz.timezone("America/New_York")
    now = datetime.now(ET)
    return 11 <= now.hour < 19  # 11:00am to 6:59pm ET

async def human_agent_active(contact_id: str, takeover_minutes: int = 5) -> bool:
    """
    Returns True if a human agent responded recently and bot should stay silent.

    Logic:
    - Outside business hours (before 11am or after 7pm ET): always False (bot responds)
    - During business hours: True only if human responded within the last `takeover_minutes`
    """
    try:
        # Outside business hours — bot always responds
        if not is_business_hours():
            logger.info("[GHL] Outside business hours — bot responds for %s", contact_id)
            return False

        conversations = await get_contact_conversations(contact_id)
        if not conversations:
            return False
        conv_id = conversations[0].get("id", "")
        if not conv_id:
            return False
        messages = await get_conversation_messages(conv_id, limit=20)

        from datetime import datetime, timezone, timedelta
        import dateutil.parser
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=takeover_minutes)

        # Log first 3 messages for debugging
        for i, msg in enumerate(messages[:3]):
            logger.info("[GHL] msg[%d] keys=%s direction=%s userId=%s date=%s",
                i,
                list(msg.keys())[:8],
                msg.get("direction", msg.get("messageType", "?")),
                msg.get("userId", msg.get("user", {}).get("id", "none") if isinstance(msg.get("user"), dict) else "none"),
                msg.get("dateAdded", msg.get("createdAt", "?"))[:20] if msg.get("dateAdded") or msg.get("createdAt") else "?"
            )

        for msg in messages:
            direction = (msg.get("direction", "") or "").lower()
            is_outbound = direction == "outbound"
            if not is_outbound:
                continue

            user_id = (msg.get("userId", "") or msg.get("user_id", ""))
            if not user_id:
                continue  # No userId = not from a real agent

            # KEY CHECK: Bot/API messages have meta.marketplace — human messages don't
            meta = msg.get("meta", {}) or {}
            marketplace = meta.get("marketplace", "") if isinstance(meta, dict) else ""
            if marketplace:
                # This is a bot/API message — skip it
                logger.debug("[GHL] Skipping API message from userId=%s (has marketplace)", user_id)
                continue

            # This is a real human message (userId present, no marketplace meta)
            date_str = (msg.get("dateAdded", "") or msg.get("createdAt", "") or msg.get("date", ""))
            logger.info("[GHL] Human message found: userId=%s date=%s", user_id, date_str[:19] if date_str else "?")

            if date_str:
                try:
                    msg_time = dateutil.parser.parse(str(date_str))
                    if msg_time.tzinfo is None:
                        msg_time = msg_time.replace(tzinfo=timezone.utc)
                    age_min = (datetime.now(timezone.utc) - msg_time).total_seconds() / 60
                    if age_min <= takeover_minutes:
                        logger.info("[GHL] Human active — responded %.1f min ago — bot silent for %s", age_min, contact_id)
                        return True
                    else:
                        logger.info("[GHL] Human responded %.1f min ago (>%d) — bot retakes %s", age_min, takeover_minutes, contact_id)
                        return False
                except Exception as ex:
                    logger.warning("[GHL] Date parse error: %s — staying silent", ex)
                    return True
            else:
                return True  # Human message, no date — stay silent to be safe

        logger.info("[GHL] No human messages found — bot responds for %s", contact_id)
        return False
    except Exception as e:
        logger.error("[GHL] Error checking human agent for %s: %s", contact_id, e)
        return False  # On error, let bot respond

async def get_latest_inbound_message(contact_id: str) -> dict | None:
    """Get the most recent inbound message from a contact"""
    try:
        conversations = await get_contact_conversations(contact_id)
        if not conversations:
            return None
        # Use most recent conversation
        conv = conversations[0]
        conv_id = conv.get("id", "")
        if not conv_id:
            return None
        messages = await get_conversation_messages(conv_id, limit=20)
        # Find most recent inbound message
        for msg in messages:
            direction = msg.get("direction", "") or msg.get("messageType", "")
            if direction in ("inbound", "TYPE_INCOMING", "incoming"):
                return {
                    "body": msg.get("body", "") or msg.get("text", "") or msg.get("message", ""),
                    "type": msg.get("type", "SMS"),
                    "conversationId": conv_id,
                    "messageId": msg.get("id", ""),
                }
        return None
    except Exception as e:
        logger.error("[GHL] Error fetching latest message for %s: %s", contact_id, e)
        return None

# Stage IDs por pipeline para Wrong Number, Not Interested y Already Insured
WRONG_NUMBER_STAGES = {
    "BdzkOH5twVi9sCK2ag96": "e1a24e5d-1781-4d15-b03c-6e17b4535fdd",  # Auto Mastermind
    "ZJtWRO02c1YLYRE4w8Vz": "ccd6cd2c-f582-42b5-bd10-86e8131300c8",  # Auto
    "HzCwe9SCtirKXGFdFLVT": "dcc131c8-62c9-4f7f-9eda-b575c536c299",  # Dental
    "XrTzKSNz9VpYuSvVZzyH": "a0dd748d-6935-4a78-9972-0bc9fb0a3874",  # Life
    "jIL4s1pxTh7a4XIyrdVy": "313124c2-f770-4fdc-99d7-dc80f227b750",  # Commercial Workers Comp
}

NOT_INTERESTED_STAGES = {
    "BdzkOH5twVi9sCK2ag96": "ae120912-5fba-4fb0-af67-8c8aa12da6f4",  # Auto Mastermind
    "ZJtWRO02c1YLYRE4w8Vz": "9f28ff58-da56-4938-9843-21bc60281b28",  # Auto
    "HzCwe9SCtirKXGFdFLVT": "66a31fce-a500-4e3c-9367-e31a72f074d7",  # Dental
    "XrTzKSNz9VpYuSvVZzyH": "634f6a6c-3176-4859-9202-220ea86717c1",  # Life
    "tq3UJ2n4L2WzITHXcGA2": "961fa6d1-a387-4404-9637-e8e01b3d2b5e",  # Health
    "jIL4s1pxTh7a4XIyrdVy": "24a37287-9ed4-4372-b164-a3078e17b240",  # Commercial Workers Comp
    "daAutexAB2Tj02taaSSf": "bbda9122-3539-4f8b-843a-b002d8213a78",  # Commercial Auto
}

ALREADY_INSURED_STAGES = {
    "BdzkOH5twVi9sCK2ag96": "834a442d-ce94-4a06-81c3-f5a49c2dc53c",  # Auto Mastermind
    "ZJtWRO02c1YLYRE4w8Vz": "808b8b5a-d88f-4184-b231-8a3dd0b7ccf4",  # Auto
    "daAutexAB2Tj02taaSSf": "e001962d-1d28-4f07-ae65-94e73c750974",  # Commercial Auto
}

async def send_email(contact_id: str, subject: str, body: str) -> dict:
    """Send email to a contact via GHL conversations"""
    conv_id = await get_contact_conversation_id(contact_id)
    if not conv_id:
        logger.error("[GHL] No conversation found for email to %s", contact_id)
        return {}
    return await request_ghl(
        "POST",
        "/conversations/messages",
        json={
            "type": "Email",
            "conversationId": conv_id,
            "subject": subject,
            "html": body,
            "body": body,
        }
    )

def is_valid_email(email: str) -> bool:
    """Basic email validation to avoid bounces"""
    import re
    if not email or not isinstance(email, str):
        return False
    pattern = r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email.strip()))

async def move_to_wrong_number(contact_id: str) -> bool:
    """Move contact's opportunity to Wrong Number stage"""
    opportunities = await get_contact_opportunities(contact_id)
    if not opportunities:
        return False
    moved = False
    for opp in opportunities:
        pipeline_id = opp.get("pipelineId", "")
        stage_id = WRONG_NUMBER_STAGES.get(pipeline_id)
        if stage_id:
            await update_contact_stage(opp.get("id", ""), stage_id)
            moved = True
    return moved

async def move_to_not_interested(contact_id: str) -> bool:
    """Move contact's opportunity to Not Interested stage"""
    opportunities = await get_contact_opportunities(contact_id)
    if not opportunities:
        return False
    moved = False
    for opp in opportunities:
        pipeline_id = opp.get("pipelineId", "")
        stage_id = NOT_INTERESTED_STAGES.get(pipeline_id)
        if stage_id:
            await update_contact_stage(opp.get("id", ""), stage_id)
            moved = True
    return moved

async def move_to_already_insured(contact_id: str) -> bool:
    """Move contact's opportunity to Already Insured stage"""
    opportunities = await get_contact_opportunities(contact_id)
    if not opportunities:
        return False
    moved = False
    for opp in opportunities:
        pipeline_id = opp.get("pipelineId", "")
        stage_id = ALREADY_INSURED_STAGES.get(pipeline_id)
        if stage_id:
            await update_contact_stage(opp.get("id", ""), stage_id)
            moved = True
    return moved

# Pipeline IDs + New Lead stage IDs for cross-sell opportunity creation
CROSS_SELL_PIPELINES = {
    "life":       ("XrTzKSNz9VpYuSvVZzyH", "fc03f7b2-9216-403a-b4ec-cbe4947d9d66"),  # Life
    "vida":       ("XrTzKSNz9VpYuSvVZzyH", "fc03f7b2-9216-403a-b4ec-cbe4947d9d66"),
    "health":     ("tq3UJ2n4L2WzITHXcGA2", "53786523-a6c8-45d8-bf58-a1cc6779db52"),  # Health
    "salud":      ("tq3UJ2n4L2WzITHXcGA2", "53786523-a6c8-45d8-bf58-a1cc6779db52"),
    "auto":       ("BdzkOH5twVi9sCK2ag96", "53879a8a-6df4-479c-956d-2ef52e2d3a3f"),  # Auto Mastermind
    "dental":     ("HzCwe9SCtirKXGFdFLVT", "a58c4225-2642-4752-a56b-7d7e81c27559"),  # Dental
    "comercial":  ("jIL4s1pxTh7a4XIyrdVy", "4cb2e3ae-0b72-4a81-bdcb-a61eb44cc064"),  # Commercial
    "commercial": ("jIL4s1pxTh7a4XIyrdVy", "4cb2e3ae-0b72-4a81-bdcb-a61eb44cc064"),
}

async def create_opportunity(contact_id: str, pipeline_id: str, stage_id: str, title: str = "New Lead") -> dict:
    """Create a new opportunity for a contact in a specific pipeline"""
    _, location_id = get_ghl_config()
    return await request_ghl(
        "POST",
        "/opportunities/",
        json={
            "pipelineId": pipeline_id,
            "pipelineStageId": stage_id,
            "contactId": contact_id,
            "locationId": location_id,
            "name": title,
            "status": "open",
        }
    )

async def get_contact_pipeline(contact_id: str) -> tuple[str, str]:
    """Returns (pipeline_id, pipeline_name) for the contact's first active opportunity"""
    opportunities = await get_contact_opportunities(contact_id)
    for opp in opportunities:
        pid = opp.get("pipelineId", "")
        from app.follow_ups import PIPELINES
        name = PIPELINES.get(pid, "")
        if name:
            return pid, name
    return "", ""

BARBARA_CONTACT_ID = "Fr2WbOMJcsnKPC01S0Dz"

AGENTS_CONTACTS = {
    "RGSzf4hQ3OvSTYPcVaYT": "pagwWiwwr7OEGJP08bCc",   # Allison Herrera
    "XIWNWHYdv3OzZ7EsHbCu": "Fr2WbOMJcsnKPC01S0Dz",   # Barbara Quintero
    "crgvDrxKXD1o7ceciD6u": "mUef4ywsxG8deYKYioW5",   # Nancy Martinez
    "6ElAdSHFu1hi0qopsDco": "oznzKcsK4X0cPyx0foii",    # Fatima Lopez
    "axXwrCLjvTuDMBSiMoPa": "WGiwNgCRyB2dlUC7ipjj",   # Sharon Jones
}

AGENTS_CALENDARS = {
    "RGSzf4hQ3OvSTYPcVaYT": "91JNjAkaA3SB1h8U5Dmu",   # Allison Herrera
    "XIWNWHYdv3OzZ7EsHbCu": "M3pvKiMzqOxWak2yFEcw",   # Barbara Quintero
    "6ElAdSHFu1hi0qopsDco": "NpNlS30qYFOWMUTUlMzE",   # Fatima Lopez
}
DEFAULT_CALENDAR_ID = "PydmDhNqVvTlKlaDTNtv"

APPOINTMENT_BOOKED_STAGES = {
    "BdzkOH5twVi9sCK2ag96": "8d04fdf3-04ae-4e77-a6b7-f0e6a0e220f8",  # Auto Mastermind
    "ZJtWRO02c1YLYRE4w8Vz": "b4c8b567-5e21-4ea8-bd9a-b290315f357e",  # Auto
    "HzCwe9SCtirKXGFdFLVT": "2d5ce6ff-096b-44f9-ad50-e45491b66269",  # Dental
    "XrTzKSNz9VpYuSvVZzyH": "443db9b7-2d78-4932-b7d0-870361d49b39",  # Life
    "tq3UJ2n4L2WzITHXcGA2": "7d1ef084-3ae1-49ae-93cb-a2d375bfc59c",  # Health
    "jIL4s1pxTh7a4XIyrdVy": "1f073556-02d2-4c8b-a915-41ae5cf3aaab",  # Commercial Workers Comp
    "daAutexAB2Tj02taaSSf": "278db8b6-c3c7-47af-a21a-c2d32a7d3e4e",  # Commercial Auto
}

async def get_opportunity_assigned_user(contact_id: str) -> str:
    """Get the userId assigned to the contact's opportunity"""
    opportunities = await get_contact_opportunities(contact_id)
    for opp in opportunities:
        assigned = opp.get("assignedTo", "")
        if assigned:
            return assigned
    return ""

async def notify_advisor_call_requested(contact_id: str, contact_name: str, assigned_user_id: str, product: str = "") -> None:
    """Notify assigned advisor + Barbara that client wants immediate call"""
    try:
        contact_data = await get_contact(contact_id)
        phone = (contact_data or {}).get("phone", "") or ""
    except Exception:
        phone = ""
    phone_str = " | Tel: " + phone if phone else ""
    product_str = " | Seguro: " + product if product else ""
    msg = f"📞 Lead: {contact_name}{phone_str}{product_str}. Quiere que lo llamen ahora. Por favor llamalo lo antes posible."

    # Notify assigned advisor
    advisor_contact_id = AGENTS_CONTACTS.get(assigned_user_id, "")
    if advisor_contact_id:
        await send_sms(advisor_contact_id, msg)

    # Always notify Barbara
    await send_sms(BARBARA_CONTACT_ID, msg)

async def create_appointment(contact_id: str, contact_name: str, assigned_user_id: str,
                              preferred_time_str: str, calendar_id: str = "") -> dict:
    """Create a GHL appointment for the contact"""
    from datetime import datetime, timedelta
    import pytz

    if not calendar_id:
        calendar_id = AGENTS_CALENDARS.get(assigned_user_id, DEFAULT_CALENDAR_ID)

    # Default to next business day 2pm ET if can't parse
    ET = pytz.timezone("America/New_York")
    now = datetime.now(ET)
    # Simple: book 24h from now at 2pm ET
    appt_time = now.replace(hour=14, minute=0, second=0, microsecond=0) + timedelta(days=1)
    if appt_time.weekday() >= 5:  # weekend → move to Monday
        appt_time += timedelta(days=(7 - appt_time.weekday()))

    start_time = appt_time.isoformat()
    end_time = (appt_time + timedelta(hours=1)).isoformat()

    body = {
        "calendarId": calendar_id,
        "locationId": os.getenv("GHL_LOCATION", ""),
        "contactId": contact_id,
        "startTime": start_time,
        "endTime": end_time,
        "title": f"Consulta de seguro — {contact_name}",
        "appointmentStatus": "confirmed",
        "assignedUserId": assigned_user_id,
        "address": "Phone Call",
    }
    try:
        return await request_ghl("POST", "/calendars/events/appointments", json=body)
    except Exception as e:
        logger.error("[GHL] Error creating appointment for %s: %s", contact_id, e)
        return {}

async def move_to_appointment_booked(contact_id: str) -> bool:
    """Move contact's opportunity to Appointment Booked stage"""
    opportunities = await get_contact_opportunities(contact_id)
    moved = False
    for opp in opportunities:
        pipeline_id = opp.get("pipelineId", "")
        stage_id = APPOINTMENT_BOOKED_STAGES.get(pipeline_id)
        if stage_id:
            await update_contact_stage(opp.get("id", ""), stage_id)
            moved = True
    return moved
