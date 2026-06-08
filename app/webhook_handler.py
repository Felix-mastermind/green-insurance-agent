"""
GHL Webhook Handler
Receives events from GHL and processes them
"""
from datetime import datetime
from fastapi import APIRouter, Request, BackgroundTasks
from app.claude_agent import get_ai_response
from app.ghl_client import send_sms, send_whatsapp, get_contact
from app.supabase_client import log_message, save_conversation_message
import pytz

router = APIRouter()

# GHL user IDs for agents
AGENTS = {
    "Allison Herrera": "RGSzf4hQ3OvSTYPcVaYT",
    "Barbara Quintero": "XIWNWHYdv3OzZ7EsHbCu",
    "Nancy Martinez":  "crgvDrxKXD1o7ceciD6u",
    "Fatima Lopez":    "6ElAdSHFu1hi0qopsDco",
    "Sharon Jones":    "axXwrCLjvTuDMBSiMoPa",
}

ET = pytz.timezone("America/New_York")
BUSINESS_HOURS_START = 9   # 9:00 AM ET
BUSINESS_HOURS_END   = 18  # 6:00 PM ET
BUSINESS_DAYS        = {0, 1, 2, 3, 4}  # Lunes a Viernes


def is_business_hours() -> bool:
    """Retorna True si es horario de oficina: L-V 9am-6pm ET."""
    now = datetime.now(ET)
    return now.weekday() in BUSINESS_DAYS and BUSINESS_HOURS_START <= now.hour < BUSINESS_HOURS_END


async def process_inbound_message(contact_id: str, message: str, channel: str, contact_name: str):
    """Process incoming message from a lead"""
    print(f"[Webhook] Inbound {channel} from {contact_name} ({contact_id}): {message[:50]}...")

    # --- Verificar horario de oficina ---
    if not is_business_hours():
        now_et = datetime.now(ET).strftime("%I:%M %p ET")
        out_of_hours_msg = (
            "Gracias por contactar a Green Insurance! 🌿 "
            "Nuestro horario de atencion es de lunes a viernes de 9am a 6pm ET. "
            "Un asesor se comunicara contigo al proximo dia habil."
        )
        if channel == "SMS":
            await send_sms(contact_id, out_of_hours_msg)
        else:
            await send_whatsapp(contact_id, out_of_hours_msg)
        print(f"[Webhook] Out of hours ({now_et}) — sent OOO message to {contact_name}")
        await log_message(contact_id, contact_name, "", channel.lower(),
                          "out_of_hours", out_of_hours_msg, "sent", {})
        return

    # Get AI response
    ai_result = await get_ai_response(contact_id, message, contact_name)
    response_text = ai_result["response"]
    should_transfer = ai_result["should_transfer"]

    # Send AI response
    if channel == "SMS":
        result = await send_sms(contact_id, response_text)
    else:
        result = await send_whatsapp(contact_id, response_text)

    status = "sent" if result.get("conversationId") else "failed"
    await log_message(contact_id, contact_name, "", channel.lower(),
                      "lead_response", response_text, status,
                      {"intent": ai_result.get("intent"), "transferred": should_transfer})

    # NOTA: NO se envía un segundo mensaje de transferencia para evitar duplicados.
    # Claude ya incluye en su respuesta el aviso de que un asesor contactará al cliente.
    if should_transfer:
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
