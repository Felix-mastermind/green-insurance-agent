"""
GHL Webhook Handler
Receives events from GHL and processes them
"""
from fastapi import APIRouter, Request, BackgroundTasks
from app.claude_agent import get_ai_response
from app.ghl_client import send_sms, send_whatsapp, get_contact
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

@router.post("/webhook/ghl")
async def ghl_webhook(request: Request, background_tasks: BackgroundTasks):
    """Main GHL webhook endpoint"""
    try:
        payload = await request.json()
        event_type = payload.get("type", "")

        print(f"[Webhook] Event received: {event_type}")

        # Inbound message from contact
        if event_type == "InboundMessage":
            contact_id = payload.get("contactId", "")
            message = payload.get("message", "")
            channel = payload.get("messageType", "SMS")
            contact_name = f"{payload.get('firstName', '')} {payload.get('lastName', '')}".strip()

            if contact_id and message:
                background_tasks.add_task(
                    process_inbound_message,
                    contact_id, message, channel, contact_name
                )

        # New contact/lead created
        elif event_type == "ContactCreate":
            contact_id = payload.get("id", "")
            first_name = payload.get("firstName", "")
            print(f"[Webhook] New contact created: {first_name} ({contact_id})")

        # Opportunity stage changed
        elif event_type == "OpportunityStageUpdate":
            opp_id = payload.get("id", "")
            stage = payload.get("stage", {}).get("name", "")
            contact_id = payload.get("contactId", "")
            print(f"[Webhook] Stage updated for {contact_id}: {stage}")

        return {"status": "ok", "event": event_type}

    except Exception as e:
        print(f"[Webhook] Error: {e}")
        return {"status": "error", "message": str(e)}

@router.get("/webhook/health")
async def health_check():
    return {"status": "online", "agent": "Green Insurance CRM Agent v1.0"}
