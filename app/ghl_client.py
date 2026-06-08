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

async def send_sms(contact_id: str, message: str) -> dict:
    """Send SMS to a contact"""
    return await request_ghl(
        "POST",
        "/conversations/messages",
        json={
            "type": "SMS",
            "contactId": contact_id,
            "message": message
        }
    )

async def send_whatsapp(contact_id: str, message: str) -> dict:
    """Send WhatsApp message to a contact"""
    return await request_ghl(
        "POST",
        "/conversations/messages",
        json={
            "type": "WhatsApp",
            "contactId": contact_id,
            "message": message
        }
    )

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
