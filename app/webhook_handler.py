"""
GHL Webhook Handler
Receives events from GHL and processes them
"""
from fastapi import APIRouter, Request, BackgroundTasks
from app.claude_agent import get_ai_response
from app.ghl_client import send_sms, send_whatsapp, get_contact, get_latest_inbound_message
from app.supabase_client import log_message, save_conversation_message

router = APIRouter()

# GHL user IDs for agents
AGENTS = {
    "Allison Herrera": "RGSzf4hQ3OvSTYPcVaYT",
    "Barbara Quintero": "XIWNWHYdv3OzZ7EsHbCu",
    "Nancy Martinez":  "crgvDrxKXD1o7ceciD6u",
    "Fatima Lopez":    "6ElAdSHFu1hi0qopsDco",
    "Sharon Jones":    "axXwrCLjvTuDMBSiMoPa",
}

async def process_inbound_message(contact_id: str, message: str, channel: str, contact_name: str):
    """Process incoming message from a lead"""
    print(f"[Webhook] Inbound {channel} from {contact_name} ({contact_id}): {message[:50]}...")

    # Get AI response
    ai_result = await get_ai_response(contact_id, message, contact_name)
    response_text = ai_result["response"]
    should_transfer = ai_result["should_transfer"]

    # Send response
    if channel == "SMS":
        result = await send_sms(contact_id, response_text)
    else:
        result = await send_whatsapp(contact_id, response_text)

    status = "sent" if result.get("conversationId") else "failed"
    await log_message(contact_id, contact_name, "", channel.lower(),
                      "lead_response", response_text, status,
                      {"intent": ai_result.get("intent"), "transferred": should_transfer})

    if should_transfer:
        transfer_msg = ("Un asesor de Green Insurance se comunicara contigo en breve. "
                        "Gracias por tu paciencia! 🌿")
        if channel == "SMS":
            await send_sms(contact_id, transfer_msg)
        else:
            await send_whatsapp(contact_id, transfer_msg)
        print(f"[Webhook] Transfer triggered for {contact_name}")

def extract_message_body(payload: dict) -> str:
    """Extract message text from various GHL payload formats"""
    # Direct message field (string)
    msg = payload.get("message", "")
    if isinstance(msg, str) and msg.strip():
        return msg.strip()
    # Nested message object with body
    if isinstance(msg, dict):
        body = msg.get("body", "") or msg.get("text", "") or msg.get("content", "")
        if body:
            return str(body).strip()
    # Other common fields
    for key in ("body", "text", "content", "messageBody", "smsMessage"):
        val = payload.get(key, "")
        if val and isinstance(val, str):
            return val.strip()
    return ""


def extract_contact_id(payload: dict) -> str:
    """Extract contact ID from various GHL payload formats"""
    for key in ("contactId", "contact_id", "ContactId", "contact_Id"):
        val = payload.get(key, "")
        if val and isinstance(val, str) and len(val) > 5:
            return str(val)
    # Try nested contact object
    contact = payload.get("contact", {})
    if isinstance(contact, dict):
        val = contact.get("id", "") or contact.get("contactId", "")
        if val:
            return str(val)
    # GHL sometimes sends 'id' at top level for contact webhooks
    val = payload.get("id", "")
    if val and isinstance(val, str) and len(val) > 10:
        return str(val)
    return ""


def extract_channel(payload: dict) -> str:
    """Extract message channel from payload"""
    for key in ("messageType", "channel", "medium"):
        val = payload.get(key, "")
        if val and isinstance(val, str) and val.upper() in ("SMS", "WHATSAPP", "EMAIL"):
            return val.upper()
    # 'type' field may be int in GHL payloads — skip if not string
    val = payload.get("type", "")
    if val and isinstance(val, str) and val.upper() in ("SMS", "WHATSAPP", "EMAIL"):
        return val.upper()
    msg = payload.get("message", {})
    if isinstance(msg, dict):
        t = msg.get("type", "") or msg.get("channel", "")
        if t and isinstance(t, str) and t.upper() in ("SMS", "WHATSAPP", "EMAIL"):
            return t.upper()
    return "WhatsApp"  # default to WhatsApp since that's the main channel


def extract_contact_name(payload: dict) -> str:
    """Extract contact name from payload"""
    # Top-level fields
    first = payload.get("firstName", "") or payload.get("first_name", "")
    last = payload.get("lastName", "") or payload.get("last_name", "")
    if first or last:
        return f"{first} {last}".strip()
    # Nested contact object
    contact = payload.get("contact", {})
    if isinstance(contact, dict):
        first = contact.get("firstName", "") or contact.get("first_name", "")
        last = contact.get("lastName", "") or contact.get("last_name", "")
        name = contact.get("name", "") or contact.get("fullName", "")
        return (f"{first} {last}".strip()) or name
    return ""


@router.post("/webhook/ghl")
async def ghl_webhook(request: Request, background_tasks: BackgroundTasks):
    """Main GHL webhook endpoint"""
    try:
        payload = await request.json()
        event_type = payload.get("type", "")

        # Log full payload for debugging (first 500 chars)
        import json
        print(f"[Webhook] Event: {event_type or 'NO_TYPE'} | Payload: {json.dumps(payload)[:500]}")

        # Detect inbound message — GHL Workflows may omit 'type' or use different names
        inbound_events = {"InboundMessage", "CustomerReplied", "customer_replied", "inbound_message", ""}
        is_inbound = (
            event_type in inbound_events
            and payload.get("direction", "inbound").lower() != "outbound"
        )

        # If no type, treat as inbound if it has a message body
        message_body = extract_message_body(payload)
        contact_id = extract_contact_id(payload)

        if is_inbound and contact_id:
            channel = extract_channel(payload)
            contact_name = extract_contact_name(payload)

            # If message body not in payload, fetch from GHL API
            if not message_body:
                print(f"[Webhook] No message body in payload for {contact_id} — fetching from GHL API")
                latest = await get_latest_inbound_message(contact_id)
                if latest:
                    message_body = latest.get("body", "")
                    if latest.get("type"):
                        channel = latest["type"].upper() if latest["type"].upper() in ("SMS","WHATSAPP","EMAIL") else channel
                    print(f"[Webhook] Fetched message from GHL: {message_body[:80]}")

            if message_body:
                print(f"[Webhook] Processing {channel} from {contact_name or contact_id}: {message_body[:80]}")
                background_tasks.add_task(
                    process_inbound_message,
                    contact_id, message_body, channel, contact_name
                )
            else:
                print(f"[Webhook] No message found for contact {contact_id} — skipping")

        # New contact/lead created
        elif event_type in ("ContactCreate", "ContactCreated", "contact_created"):
            first_name = payload.get("firstName", "")
            print(f"[Webhook] New contact: {first_name} ({contact_id})")

        # Opportunity stage changed
        elif event_type in ("OpportunityStageUpdate", "opportunity_stage_update"):
            stage = payload.get("stage", {})
            stage_name = stage.get("name", "") if isinstance(stage, dict) else str(stage)
            print(f"[Webhook] Stage updated for {contact_id}: {stage_name}")

        return {"status": "ok", "event": event_type or "inbound_message"}

    except Exception as e:
        print(f"[Webhook] Error: {e}")
        return {"status": "error", "message": str(e)}

@router.get("/webhook/health")
async def health_check():
    return {"status": "online", "agent": "Green Insurance CRM Agent v1.0"}

# Temporary debug endpoint — stores last webhook payload
_last_payload = {}

@router.post("/webhook/debug")
async def ghl_webhook_debug(request: Request):
    """Debug endpoint — returns the raw payload GHL sends"""
    global _last_payload
    try:
        _last_payload = await request.json()
    except Exception:
        _last_payload = {"error": "could not parse JSON", "raw": await request.body()}
    import json
    print(f"[DEBUG] Full payload: {json.dumps(_last_payload)}")
    return {"status": "ok", "received": _last_payload}

@router.get("/webhook/last-payload")
async def get_last_payload():
    """See the last payload received for debugging"""
    return _last_payload
