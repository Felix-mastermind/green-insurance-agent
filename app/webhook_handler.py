"""
GHL Webhook Handler
Receives events from GHL and processes them
"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, BackgroundTasks
from app.claude_agent import get_ai_response
from app.scheduler import scheduler, ET
from app.ghl_client import send_sms, send_whatsapp, get_contact, get_latest_inbound_message, add_contact_tag, add_internal_note, create_task, move_to_hot_lead, human_agent_active, get_contact_channel, move_to_wrong_number, move_to_not_interested, move_to_already_insured, send_email, is_valid_email, get_opportunity_assigned_user, notify_advisor_call_requested, create_appointment, move_to_appointment_booked, create_opportunity, CROSS_SELL_PIPELINES, AGENTS_CONTACTS, BARBARA_CONTACT_ID, get_contact_pipeline, get_contact_opportunities
from app.supabase_client import log_message, save_conversation_message, check_survey_pending, mark_survey_answered

router = APIRouter()

# GHL user IDs for agents
AGENTS = {
    "Allison Herrera": "RGSzf4hQ3OvSTYPcVaYT",
    "Barbara Quintero": "XIWNWHYdv3OzZ7EsHbCu",
    "Nancy Martinez":  "crgvDrxKXD1o7ceciD6u",
    "Fatima Lopez":    "6ElAdSHFu1hi0qopsDco",
    "Sharon Jones":    "axXwrCLjvTuDMBSiMoPa",
}

BARBARA_CONTACT_ID = "Fr2WbOMJcsnKPC01S0Dz"
REVIEW_LINK = "https://share.google/07auFx6a4aT7D7ht6"

BUSINESS_HOURS_START = 11  # 11:00 AM ET
BUSINESS_HOURS_END   = 19  # 7:00 PM ET
BUSINESS_DAYS        = {0, 1, 2, 3, 4}  # Lunes a Viernes


def is_business_hours() -> bool:
    """Retorna True si es L-V 11am-7pm ET."""
    now = datetime.now(ET)
    return now.weekday() in BUSINESS_DAYS and BUSINESS_HOURS_START <= now.hour < BUSINESS_HOURS_END


def next_business_opening() -> datetime:
    """Retorna el datetime del próximo inicio de horario hábil (11am ET)."""
    now = datetime.now(ET)
    candidate = now.replace(hour=BUSINESS_HOURS_START, minute=0, second=0, microsecond=0)
    if now >= candidate:
        candidate += timedelta(days=1)
    while candidate.weekday() not in BUSINESS_DAYS:
        candidate += timedelta(days=1)
    return candidate


async def send_advisor_next_day_reminder(advisor_contact_id: str, advisor_uid: str,
                                          contact_name: str, contact_id: str,
                                          preferred_time: str, product: str):
    """Envía texto al asesor recordándole que debe llamar al lead."""
    ghl_link = f"https://app.gohighlevel.com/contacts/{contact_id}"
    product_str = f" | Seguro: {product}" if product else ""
    time_str = f" a las {preferred_time}" if preferred_time else " en el horario acordado"
    msg = (
        f"📞 Recordatorio de llamada: {contact_name}{time_str}{product_str}. "
        f"El cliente agendó llamada para hoy. Ver lead: {ghl_link}"
    )
    await send_sms(advisor_contact_id, msg)
    await send_sms(BARBARA_CONTACT_ID, msg)
    print(f"[Reminder] Sent next-day reminder for {contact_name} to advisor {advisor_uid}")


async def is_new_lead_stage(contact_id: str) -> bool:
    """
    Retorna True si la oportunidad del contacto está en 'New Lead'.
    En ese caso el bot no responde — deja que la automatización de GHL
    y el asesor hagan las 3 llamadas y muevan el lead a 'Contacted'.
    """
    opportunities = await get_contact_opportunities(contact_id)
    for opp in opportunities:
        # GHL puede devolver el nombre del stage en distintos campos
        pipeline_stage = opp.get("pipelineStage") or {}
        stage_name = (
            pipeline_stage.get("name", "")
            if isinstance(pipeline_stage, dict)
            else str(pipeline_stage)
        )
        if not stage_name:
            stage_name = opp.get("stageName", "") or ""
        if "new lead" in stage_name.lower():
            return True
    return False


async def handle_survey_response(contact_id: str, contact_name: str, score: int, channel: str):
    """Handle a 1-5 survey response from a Won contact"""
    await mark_survey_answered(contact_id)
    if score >= 3:
        stars = "⭐" * score
        msg = (f"Hola {contact_name}! Gracias por tu calificación {stars} "
               f"¡Nos alegra mucho saberlo! Si puedes dejarnos una reseña rápida "
               f"te lo agradecemos mucho: {REVIEW_LINK}")
        if channel == "SMS":
            await send_sms(contact_id, msg)
        else:
            await send_whatsapp(contact_id, msg)
        print(f"[Survey] ✅ {contact_name} calificó {score}/5 — se envió link de reseña")
    else:
        msg = (f"Hola {contact_name}, lamentamos que tu experiencia no haya sido la mejor. "
               f"¿Podrías contarnos qué podemos mejorar? "
               f"Un asesor se comunicará contigo pronto.")
        if channel == "SMS":
            await send_sms(contact_id, msg)
        else:
            await send_whatsapp(contact_id, msg)
        barbara_msg = (f"⚠️ Alerta: El cliente {contact_name} calificó el servicio con {score}/5. "
                       f"Por favor verifica el caso.")
        await send_sms(BARBARA_CONTACT_ID, barbara_msg)
        print(f"[Survey] ⚠️ {contact_name} calificó {score}/5 — mensaje de feedback + tarea a Barbara")

async def process_inbound_message(contact_id: str, message: str, channel: str, contact_name: str, assigned_user_id: str = ""):
    """Process incoming message from a lead"""
    print(f"[Webhook] Inbound {channel} from {contact_name} ({contact_id}): {message[:50]}...")


    # If contact has a pending survey, handle the 1-5 response before AI
    msg_stripped = message.strip()
    if msg_stripped in ("1", "2", "3", "4", "5") and await check_survey_pending(contact_id):
        await handle_survey_response(contact_id, contact_name, int(msg_stripped), channel)
        return
    # Pre-fetch pipeline so AI can give product-specific responses (e.g. already_insured)
    _, product = await get_contact_pipeline(contact_id)

    # Get AI response — pasar si es horario hábil para ajustar el mensaje de cierre
    in_hours = is_business_hours()
    ai_result = await get_ai_response(contact_id, message, contact_name, business_hours=in_hours, product=product)
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

    intent = ai_result.get("intent", "")

    # Client wants immediate call
    if intent == "wants_call":
        assigned_uid = assigned_user_id or await get_opportunity_assigned_user(contact_id)
        await notify_advisor_call_requested(contact_id, contact_name, assigned_uid, product)
        await move_to_hot_lead(contact_id)
        print(f"[Webhook] {contact_name} wants call — HOT Lead + advisor notified")

    # Client wants appointment
    elif intent == "wants_appointment":
        assigned_uid = assigned_user_id or await get_opportunity_assigned_user(contact_id)
        preferred_time = ai_result.get("preferred_time", "")
        appt = await create_appointment(contact_id, contact_name, assigned_uid, preferred_time)
        if appt.get("id") or appt.get("appointmentId"):
            await move_to_appointment_booked(contact_id)
            contact_info = await get_contact(contact_id)
            phone = (contact_info or {}).get("phone", "") or ""
            phone_str = " | Tel: " + phone if phone else ""
            product_str = " | Seguro: " + product if product else ""
            advisor_contact_id = AGENTS_CONTACTS.get(assigned_uid, "")

            if in_hours:
                # Dentro de horario: notificar al asesor de inmediato
                msg = f"📅 Cita agendada | Lead: {contact_name}{phone_str}{product_str}. Revisa tu calendario."
                if advisor_contact_id:
                    await send_sms(advisor_contact_id, msg)
                await send_sms(BARBARA_CONTACT_ID, msg)
                print(f"[Webhook] {contact_name} appointment booked (in hours) — advisor notified now")
            else:
                # Fuera de horario: programar recordatorio al asesor para el día siguiente a las 11am ET
                reminder_time = next_business_opening()
                job_id = f"reminder_{contact_id}_{int(reminder_time.timestamp())}"
                scheduler.add_job(
                    send_advisor_next_day_reminder,
                    "date",
                    run_date=reminder_time,
                    id=job_id,
                    replace_existing=True,
                    args=[advisor_contact_id or BARBARA_CONTACT_ID, assigned_uid,
                          contact_name, contact_id, preferred_time, product or ""],
                )
                print(f"[Webhook] {contact_name} appointment booked (out of hours) — reminder scheduled for {reminder_time.strftime('%Y-%m-%d %I:%M %p ET')}")

    # Handle wrong number
    if ai_result.get("intent") == "wrong_number":
        moved = await move_to_wrong_number(contact_id)
        contact_data = await get_contact(contact_id)
        email = (contact_data or {}).get("email", "")
        if email and is_valid_email(email):
            subject = "Green Insurance - Verificacion de contacto"
            body = f"Hola, recibimos un mensaje indicando que este numero no corresponde a {contact_name}. Si esto es un error, por favor contactenos. Green Insurance - Marietta, GA"
            await send_email(contact_id, subject, body)
            print(f"[Webhook] Wrong number for {contact_name} — moved to stage, email sent to {email}")
        else:
            print(f"[Webhook] Wrong number for {contact_name} — moved to stage, no valid email")

    # Handle already insured
    elif ai_result.get("intent") == "already_insured":
        await move_to_already_insured(contact_id)
        print(f"[Webhook] Already insured: {contact_name} — moved to Already Insured stage")

    # Handle cross-sell: client in one pipeline asks about a different product
    elif ai_result.get("intent") == "cross_sell":
        target_product = ai_result.get("preferred_time", "")  # product type detected
        pipeline_info = CROSS_SELL_PIPELINES.get(target_product)
        if pipeline_info:
            new_pipeline_id, new_stage_id = pipeline_info
            contact_info = await get_contact(contact_id)
            phone = (contact_info or {}).get("phone", "") or ""
            phone_str = " | Tel: " + phone if phone else ""
            opp_title = f"{contact_name} - {target_product.title()}"
            new_opp = await create_opportunity(contact_id, new_pipeline_id, new_stage_id, opp_title)
            opp_id = (new_opp.get("opportunity") or new_opp).get("id", "")
            notify_msg = (
                f"🔄 Cross-sell: {contact_name}{phone_str} estaba en pipeline '{product}' "
                f"y ahora quiere cotizar '{target_product}'. "
                f"Nueva oportunidad creada{' (ID: ' + opp_id + ')' if opp_id else ''}."
            )
            await send_sms(BARBARA_CONTACT_ID, notify_msg)
            print(f"[Webhook] Cross-sell {contact_name}: {product} → {target_product} | opp={opp_id}")
        else:
            print(f"[Webhook] Cross-sell detected but no pipeline found for '{target_product}'")

    # Handle not interested
    elif ai_result.get("intent") == "not_interested":
        await move_to_not_interested(contact_id)
        print(f"[Webhook] Not interested: {contact_name} — moved to Not Interested stage")

    elif should_transfer:
        if in_hours:
            # Dentro de horario: avisar que el asesor llama pronto
            transfer_msg = ("Un asesor de Green Insurance se comunicara contigo en breve. "
                            "Gracias por tu paciencia!")
            if channel == "SMS":
                await send_sms(contact_id, transfer_msg)
            else:
                await send_whatsapp(contact_id, transfer_msg)
            moved = await move_to_hot_lead(contact_id)
            await add_contact_tag(contact_id, "necesita-asesor")
            print(f"[Webhook] Transfer (in hours) for {contact_name} — HOT Lead: {moved}")
        else:
            # Fuera de horario: el AI ya le pidió la hora preferida para mañana.
            # No mandamos mensaje extra aquí — esperamos que el cliente responda con la hora.
            # Solo marcamos como HOT Lead para que el asesor lo vea.
            moved = await move_to_hot_lead(contact_id)
            await add_contact_tag(contact_id, "llamar-manana")
            print(f"[Webhook] Transfer (out of hours) for {contact_name} — waiting for preferred time")

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
    # GHL Workflow sends contact_id (underscore format)
    for key in ("contact_id", "contactId", "ContactId", "contact_Id"):
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
    # GHL Workflow uses underscore format: first_name, last_name, full_name
    full = payload.get("full_name", "")
    if full:
        return full
    first = payload.get("first_name", "") or payload.get("firstName", "")
    last = payload.get("last_name", "") or payload.get("lastName", "")
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
            contact_name = extract_contact_name(payload)

            # STOP bot if a human agent has already responded in this conversation
            if await human_agent_active(contact_id):
                print(f"[Webhook] SKIPPED — human agent is active for {contact_name} ({contact_id})")
                return {"status": "ok", "event": "skipped_human_active"}

            # STOP bot if lead is still in "New Lead" stage —
            # let GHL automation run + advisor calls 3 times first.
            # Bot only kicks in once advisor moves lead to "Contacted" or beyond.
            if await is_new_lead_stage(contact_id):
                print(f"[Webhook] SKIPPED — {contact_name} ({contact_id}) is in 'New Lead' stage, bot stays silent")
                return {"status": "ok", "event": "skipped_new_lead"}

            # Get the real channel from GHL conversation
            channel = await get_contact_channel(contact_id)

            # If message body not in payload, fetch from GHL API
            if not message_body:
                print(f"[Webhook] No message body for {contact_id} — fetching from GHL API")
                latest = await get_latest_inbound_message(contact_id)
                if latest:
                    message_body = latest.get("body", "")
                    print(f"[Webhook] Fetched message: {message_body[:80]}")

            if message_body:
                print(f"[Webhook] Processing {channel} from {contact_name or contact_id}: {message_body[:80]}")
                user_obj = payload.get("user", {})
                assigned_uid = user_obj.get("id", "") if isinstance(user_obj, dict) else ""
                background_tasks.add_task(
                    process_inbound_message,
                    contact_id, message_body, channel, contact_name, assigned_uid
                )
            else:
                print(f"[Webhook] No message found for {contact_id} — skipping")

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
