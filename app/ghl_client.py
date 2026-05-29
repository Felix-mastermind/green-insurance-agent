import httpx
import os
from typing import Optional

GHL_TOKEN = os.getenv("GHL_TOKEN", "pit-355ec36f-db15-42ae-8488-b742bb700535")
GHL_LOCATION = os.getenv("GHL_LOCATION", "YtNhoKQqqoEZSVFceqZm")
BASE_URL = "https://services.leadconnectorhq.com"
HEADERS = {
    "Authorization": f"Bearer {GHL_TOKEN}",
    "Version": "2021-07-28",
    "Content-Type": "application/json"
}

async def get_contacts_by_tag(tag: str, limit: int = 100) -> list:
    """Get all contacts with a specific tag"""
    contacts = []
    offset = 0
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            resp = await client.get(
                f"{BASE_URL}/contacts/",
                headers=HEADERS,
                params={"locationId": GHL_LOCATION, "tags": tag, "limit": limit, "skip": offset}
            )
            data = resp.json()
            batch = data.get("contacts", [])
            contacts.extend(batch)
            if len(batch) < limit:
                break
            offset += limit
    return contacts

async def get_contact(contact_id: str) -> Optional[dict]:
    """Get a single contact by ID"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{BASE_URL}/contacts/{contact_id}", headers=HEADERS)
        if resp.status_code == 200:
            return resp.json().get("contact")
    return None

async def send_sms(contact_id: str, message: str) -> dict:
    """Send SMS to a contact"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/conversations/messages",
            headers=HEADERS,
            json={
                "type": "SMS",
                "contactId": contact_id,
                "message": message
            }
        )
        return resp.json()

async def send_whatsapp(contact_id: str, message: str) -> dict:
    """Send WhatsApp message to a contact"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/conversations/messages",
            headers=HEADERS,
            json={
                "type": "WhatsApp",
                "contactId": contact_id,
                "message": message
            }
        )
        return resp.json()

async def update_contact_stage(opportunity_id: str, stage_id: str) -> dict:
    """Update opportunity stage"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            f"{BASE_URL}/opportunities/{opportunity_id}",
            headers=HEADERS,
            json={"stageId": stage_id}
        )
        return resp.json()

async def get_contact_custom_field(contact: dict, field_id: str) -> Optional[str]:
    """Extract a custom field value from a contact"""
    for cf in contact.get("customFields", []):
        if cf.get("id") == field_id:
            return cf.get("value")
    return None

async def search_contacts(query: str) -> list:
    """Search contacts by phone or name"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{BASE_URL}/contacts/",
            headers=HEADERS,
            params={"locationId": GHL_LOCATION, "query": query, "limit": 5}
        )
        return resp.json().get("contacts", [])
