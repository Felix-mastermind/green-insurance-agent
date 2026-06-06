"""
Follow-Up Module
Runs every 2 hours during business hours
Reviews leads by stage in active pipelines and sends product-specific follow-up messages
"""
import pytz
from datetime import datetime, timedelta
from app.ghl_client import (
    get_opportunities, get_pipelines, send_whatsapp, send_sms, send_email, is_valid_email,
    get_contact, add_contact_tag, create_task, get_contact_channel
)
from app.supabase_client import check_reminder_sent, log_reminder_sent

ET = pytz.timezone("America/New_York")

# ─── Active Pipelines ────────────────────────────────────────────────────────
PIPELINES = {
    "HzCwe9SCtirKXGFdFLVT": "dental",
    "BdzkOH5twVi9sCK2ag96": "auto",
    "XrTzKSNz9VpYuSvVZzyH": "life",
}

# ─── Stages to skip (won, lost, DND, etc.) ───────────────────────────────────
SKIP_STAGES = {
    "Won", "won",
    "DND",
    "Wrong number", "Wrong Number",
}

# Stages where we DO NOT send follow-ups (asesor is actively working them)
ASESOR_ACTIVE_STAGES = {"Appointment Booked", "Quoted", "HOT Leads", "Hot Lead"}

# ─── Follow-up messages by product and stage ─────────────────────────────────
MESSAGES = {
    "dental": {
        "New Lead": {
            "es": "Hola {name}! 😊 Te escribimos de Green Insurance. Vimos que estás interesado en un seguro dental. ¿Cuándo tienes unos minutos para que un asesor te llame?",
            "en": "Hi {name}! 😊 This is Green Insurance. We saw you're interested in dental insurance. When is a good time for one of our advisors to call you?",
            "days": 2,
        },
        "Contacted": {
            "es": "Hola {name}! Solo quería confirmar que recibiste la información sobre el seguro dental. ¿Tienes alguna pregunta para nuestro asesor?",
            "en": "Hi {name}! Just checking that you received the dental insurance information. Do you have any questions for our advisor?",
            "days": 2,
        },
        "No answer 1-3": {
            "es": "Hola {name}! Hemos intentado comunicarnos contigo sobre tu seguro dental. ¿Hay un mejor horario para llamarte? Estamos disponibles de L-V 11am-7pm.",
            "en": "Hi {name}! We've been trying to reach you about dental insurance. Is there a better time to call? We're available Mon-Fri 11am-7pm.",
            "days": 2,
        },
        "No answer 4-6": {
            "es": "Hola {name}! Seguimos aquí para ayudarte con tu seguro dental. Si prefieres, puedes escribirnos aquí mismo y un asesor te responde hoy.",
            "en": "Hi {name}! We're still here to help you with dental insurance. Feel free to reply here and an advisor will get back to you today.",
            "days": 2,
        },
        "No answer 7-9": {
            "es": "Hola {name}! Último mensaje de nuestra parte sobre el seguro dental. Si en algún momento necesitas cobertura dental, aquí estaremos. ¡Que tengas un excelente día!",
            "en": "Hi {name}! Last follow-up from us about dental insurance. Whenever you need coverage, we're here. Have a great day!",
            "days": 2,
        },
        "No answer 7-9 Allison": {
            "es": "Hola {name}! Último mensaje de nuestra parte sobre el seguro dental. Si en algún momento necesitas cobertura dental, aquí estaremos. ¡Que tengas un excelente día!",
            "en": "Hi {name}! Last follow-up from us about dental insurance. Whenever you need coverage, we're here. Have a great day!",
            "days": 2,
        },
        "No answer 7-9 Fatima": {
            "es": "Hola {name}! Último mensaje de nuestra parte sobre el seguro dental. Si en algún momento necesitas cobertura dental, aquí estaremos. ¡Que tengas un excelente día!",
            "en": "Hi {name}! Last follow-up from us about dental insurance. Whenever you need coverage, we're here. Have a great day!",
            "days": 2,
        },
        "Reactivation - 60+ Days": {
            "es": "Hola {name}! Han pasado unos meses desde tu consulta sobre seguro dental. Tenemos nuevos planes disponibles. ¿Te interesa una cotización actualizada?",
            "en": "Hi {name}! It's been a while since you asked about dental insurance. We have new plans available. Interested in an updated quote?",
            "days": 2,
        },
        "Follow Up to Close": {
            "es": "Hola {name}! ¿Pudiste revisar las opciones de seguro dental? Podemos activar tu póliza esta semana. ¿Te llamo hoy?",
            "en": "Hi {name}! Were you able to review the dental insurance options? We can get your policy started this week. Shall I call you today?",
            "days": 2,
        },
        "Missed Appointment": {
            "es": "Hola {name}! Vimos que no pudiste asistir a tu cita. No hay problema, ¿cuándo te queda bien reagendar para tu seguro dental?",
            "en": "Hi {name}! We noticed you missed your appointment. No worries — when would you like to reschedule for your dental insurance?",
            "days": 2,
        },
    },

    "auto": {
        "New Lead": {
            "es": "Hola {name}! 🚗 Te escribimos de Green Insurance. Vimos que necesitas seguro de auto. ¿Tienes unos minutos para que un asesor te llame y te dé opciones?",
            "en": "Hi {name}! 🚗 This is Green Insurance. We saw you need auto insurance. Do you have a few minutes for an advisor to call you with options?",
            "days": 2,
        },
        "Contacted": {
            "es": "Hola {name}! Solo un seguimiento rápido sobre tu seguro de auto. ¿Ya tuviste oportunidad de revisar la información? ¿Alguna pregunta?",
            "en": "Hi {name}! Quick follow-up on your auto insurance. Did you get a chance to review? Any questions?",
            "days": 2,
        },
        "No answer 1-3": {
            "es": "Hola {name}! Hemos intentado contactarte sobre tu seguro de auto. ¿Hay un mejor momento para llamarte? Disponibles L-V 11am-7pm.",
            "en": "Hi {name}! We've tried reaching you about auto insurance. Is there a better time to call? Available Mon-Fri 11am-7pm.",
            "days": 2,
        },
        "No answer 4-6": {
            "es": "Hola {name}! Sabemos que estás ocupado. Si quieres info sobre tu seguro de auto, escríbenos aquí y te respondemos al momento.",
            "en": "Hi {name}! We know you're busy. If you want info on auto insurance, just reply here and we'll get back to you right away.",
            "days": 2,
        },
        "No answer 7-9": {
            "es": "Hola {name}! Último intento de contacto sobre tu seguro de auto. Cuando estés listo, aquí estaremos. ¡Saludos de Green Insurance!",
            "en": "Hi {name}! Last follow-up on auto insurance. When you're ready, we're here. Best, Green Insurance!",
            "days": 2,
        },
        "No Answer 7-9 Valeria": {
            "es": "Hola {name}! Último intento de contacto sobre tu seguro de auto. Cuando estés listo, aquí estaremos. ¡Saludos de Green Insurance!",
            "en": "Hi {name}! Last follow-up on auto insurance. When you're ready, we're here. Best, Green Insurance!",
            "days": 2,
        },
        "Reactivation - 60+ Days": {
            "es": "Hola {name}! Han pasado unos meses desde tu consulta sobre seguro de auto. Tenemos nuevas opciones disponibles. ¿Te interesa una cotización actualizada?",
            "en": "Hi {name}! It's been a while since you asked about auto insurance. We have new options available. Interested in an updated quote?",
            "days": 2,
        },
        "Follow Up to Close": {
            "es": "Hola {name}! ¿Ya decidiste sobre tu seguro de auto? Podemos activar tu cobertura hoy mismo. ¿Te llamamos?",
            "en": "Hi {name}! Have you decided on your auto insurance? We can get you covered today. Should we call?",
            "days": 2,
        },
        "Missed Appointment": {
            "es": "Hola {name}! No pudiste asistir a tu cita para el seguro de auto. ¿Cuándo te queda bien reagendar?",
            "en": "Hi {name}! You missed your auto insurance appointment. When can we reschedule?",
            "days": 2,
        },
        "Quoted": {
            "es": "Hola {name}! ¿Pudiste revisar la cotización de tu seguro de auto? Estamos para resolver cualquier duda antes de que tomes tu decisión.",
            "en": "Hi {name}! Were you able to review your auto insurance quote? We're here to answer any questions before you decide.",
            "days": 2,
        },
    },

    "life": {
        "New Leads": {
            "es": "Hola {name}! 🌿 Te escribimos de Green Insurance. Vimos que estás interesado en seguro de vida. Es una decisión importante — ¿cuándo puedo conectarte con un asesor?",
            "en": "Hi {name}! 🌿 This is Green Insurance. We saw you're interested in life insurance. It's an important decision — when can I connect you with an advisor?",
            "days": 2,
        },
        "Contacted": {
            "es": "Hola {name}! Seguimiento sobre tu seguro de vida. ¿Tuviste oportunidad de revisar la información? ¿Tienes preguntas?",
            "en": "Hi {name}! Following up on your life insurance inquiry. Did you have a chance to review? Any questions?",
            "days": 2,
        },
        "No answer 1-3": {
            "es": "Hola {name}! Hemos intentado comunicarnos sobre tu seguro de vida. ¿Hay un mejor horario para llamarte?",
            "en": "Hi {name}! We've been trying to reach you about life insurance. Is there a better time to call?",
            "days": 2,
        },
        "No answer 4-6": {
            "es": "Hola {name}! Seguimos disponibles para ayudarte con tu seguro de vida. Escríbenos aquí cuando gustes.",
            "en": "Hi {name}! We're still available to help with your life insurance. Feel free to message us here anytime.",
            "days": 2,
        },
        "No answer 7-9": {
            "es": "Hola {name}! Último mensaje sobre tu seguro de vida. Cuando estés listo para proteger a tu familia, aquí estaremos.",
            "en": "Hi {name}! Last message about life insurance. When you're ready to protect your family, we're here.",
            "days": 2,
        },
        "HOT Leads": {
            "es": "Hola {name}! Un asesor de Green Insurance te llamará muy pronto para ayudarte con tu seguro de vida. ¿Hay algún horario que prefieras?",
            "en": "Hi {name}! A Green Insurance advisor will call you very soon about your life insurance. Is there a preferred time?",
            "days": 2,
        },
        "Follow Up to Close": {
            "es": "Hola {name}! ¿Pudiste pensar en las opciones de seguro de vida? Podemos activar tu póliza esta semana. ¿Hablamos hoy?",
            "en": "Hi {name}! Were you able to think about the life insurance options? We can get your policy started this week. Shall we talk today?",
            "days": 2,
        },
        "Missed Appointment": {
            "es": "Hola {name}! No pudiste asistir a tu cita para el seguro de vida. ¿Cuándo podemos reagendar?",
            "en": "Hi {name}! You missed your life insurance appointment. When can we reschedule?",
            "days": 2,
        },
    },
}

# ─── Cross-sell messages for rejected/lost stages ────────────────────────────
# Key: product currently in → other products to offer
OTHER_PRODUCTS = {
    "dental": "auto y vida",
    "auto":   "dental y vida",
    "life":   "auto y dental",
}
OTHER_PRODUCTS_EN = {
    "dental": "auto and life",
    "auto":   "dental and life",
    "life":   "auto and dental",
}

CROSS_SELL_STAGES = {"Not interested", "Not Interested", "Not Insterested", "Not Eligible", "Offer not accepted"}
ALREADY_INSURED_STAGES = {"Already Insured"}


def is_business_hours_followup() -> bool:
    """Only send follow-ups at scheduled times (2pm-5pm ET)"""
    now = datetime.now(ET)
    return 14 <= now.hour < 17


def detect_language(contact: dict, stage_name: str = "") -> str:
    """Detect language from contact tags or stage name"""
    if stage_name.lower() == "english":
        return "en"
    tags = contact.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip().lower() for t in tags.split(",")]
    tags_lower = [str(t).lower() for t in tags]
    if "english" in tags_lower or "en" in tags_lower:
        return "en"
    return "es"


def get_followup_key(today: datetime, contact_id: str, stage: str) -> str:
    """Generate unique key to prevent duplicate follow-ups"""
    return f"{today.strftime('%Y-%m-%d')}_{contact_id}_{stage[:20]}"


async def send_followup(contact: dict, message: str, channel: str = "WhatsApp") -> bool:
    """Send follow-up message via the right channel"""
    contact_id = contact.get("id", "")
    try:
        if channel == "SMS":
            result = await send_sms(contact_id, message)
        else:
            result = await send_whatsapp(contact_id, message)
        return bool(result.get("conversationId") or result.get("id") or result.get("messageId"))
    except Exception as e:
        print(f"[FollowUp] Error sending to {contact_id}: {e}")
        return False


async def run_follow_ups(force: bool = False):
    """Main follow-up runner — checks all leads in active pipelines by stage"""
    if not force and not is_business_hours_followup():
        print("[FollowUp] Outside business hours — skipping")
        return {"status": "skipped", "reason": "outside_hours"}

    now = datetime.now(ET)
    today = now.date()
    print(f"[FollowUp] Starting follow-up run at {now.strftime('%Y-%m-%d %H:%M ET')}")

    # Build stageId -> stageName map from pipeline data
    stage_map = {}
    try:
        pipelines = await get_pipelines()
        for pipeline in pipelines:
            for stage in pipeline.get("stages", []):
                stage_map[stage["id"]] = stage["name"]
    except Exception as e:
        print(f"[FollowUp] Warning: could not load pipeline stages: {e}")

    opportunities = await get_opportunities()
    print(f"[FollowUp] Fetched {len(opportunities)} opportunities total")
    processed = 0
    sent = 0
    skipped = 0

    for opp in opportunities:
        pipeline_id = opp.get("pipelineId", "")
        product = PIPELINES.get(pipeline_id)
        if not product:
            continue  # Not an active pipeline

        stage_id = opp.get("pipelineStageId", "")
        stage_name = stage_map.get(stage_id, "")
        if not stage_name:
            print(f"[FollowUp] No stage name for stageId={stage_id}")
            continue

        # Skip terminal/inactive stages
        if any(stage_name.lower() == s.lower() for s in SKIP_STAGES):
            continue

        # ─── Cross-sell: Not Interested / Not Eligible / Offer Not Accepted ───
        if stage_name in CROSS_SELL_STAGES:
            contact_id = opp.get("contactId", "")
            if not contact_id:
                continue
            already_sent = await check_reminder_sent(contact_id, f"crosssell_{product}", today.strftime("%Y-%m"))
            if already_sent:
                skipped += 1
                continue
            contact_data = await get_contact(contact_id)
            if not contact_data:
                continue
            lang = detect_language(contact_data, stage_name)
            first_name = contact_data.get("firstName", "") or "Hola"
            others = OTHER_PRODUCTS_EN.get(product, "") if lang == "en" else OTHER_PRODUCTS.get(product, "")
            if lang == "en":
                msg = f"Hi {first_name}! We understand {product} insurance wasn't the right fit. Did you know we also offer {others}? We'd love to help you find the right coverage."
            else:
                msg = f"Hola {first_name}! Entendemos que el seguro de {product} no era lo que buscabas. ¿Sabías que también ofrecemos {others}? Nos encantaría ayudarte a encontrar la cobertura ideal."
            channel = await get_contact_channel(contact_id)
            if channel == "SMS":
                await send_sms(contact_id, msg)
            else:
                await send_whatsapp(contact_id, msg)
            await log_reminder_sent(contact_id, first_name, 0, f"crosssell_{product}", today.strftime("%Y-%m"))
            sent += 1
            print(f"[FollowUp] \U0001f504 Cross-sell sent to {first_name} ({contact_id}) | was: {product}")
            continue

        # ─── Already Insured: send once a month ──────────────────────────────
        if stage_name in ALREADY_INSURED_STAGES:
            contact_id = opp.get("contactId", "")
            if not contact_id:
                continue
            already_sent = await check_reminder_sent(contact_id, f"already_insured_{product}", today.strftime("%Y-%m"))
            if already_sent:
                skipped += 1
                continue
            contact_data = await get_contact(contact_id)
            if not contact_data:
                continue
            lang = detect_language(contact_data, stage_name)
            first_name = contact_data.get("firstName", "") or "Hola"
            if lang == "en":
                msg = f"Hi {first_name}! We know you already have insurance, but we're still here whenever you want to compare options. We have a variety of plans that might surprise you!"
            else:
                msg = f"Hola {first_name}! Sabemos que ya cuentas con seguro, pero seguimos aquí por si algún día quieres cotizar. Tenemos varias opciones que podrían sorprenderte."
            channel = await get_contact_channel(contact_id)
            if channel == "SMS":
                await send_sms(contact_id, msg)
            else:
                await send_whatsapp(contact_id, msg)
            await log_reminder_sent(contact_id, first_name, 0, f"already_insured_{product}", today.strftime("%Y-%m"))
            sent += 1
            print(f"[FollowUp] \U0001f3e0 Already Insured msg sent to {first_name} ({contact_id})")
            continue

        # Get message template for this stage + product
        product_messages = MESSAGES.get(product, {})
        stage_config = product_messages.get(stage_name)
        if not stage_config:
            continue  # No follow-up configured for this stage

        processed += 1
        contact_id = opp.get("contactId", "")
        if not contact_id:
            continue

        # Check how long the lead has been in this stage
        stage_changed_at = opp.get("lastStageChangeAt", "") or opp.get("updatedAt", "")
        if stage_changed_at:
            try:
                import dateutil.parser
                stage_time = dateutil.parser.parse(stage_changed_at)
                if stage_time.tzinfo is None:
                    from datetime import timezone
                    stage_time = stage_time.replace(tzinfo=timezone.utc)
                days_in_stage = (datetime.now(ET).replace(tzinfo=None) - stage_time.replace(tzinfo=None)).days
                required_days = stage_config.get("days", 1)
                if not force and days_in_stage < required_days:
                    skipped += 1
                    continue  # Not enough time has passed
            except Exception:
                pass  # If can't parse date, proceed anyway

        # Check if already sent today
        followup_key = get_followup_key(now, contact_id, stage_name)
        already_sent = await check_reminder_sent(contact_id, f"followup_{product}_{stage_name[:15]}", today.strftime("%Y-%m-%d"))
        if already_sent:
            skipped += 1
            continue

        # Get contact details
        try:
            contact_data = await get_contact(contact_id)
            if not contact_data:
                continue
        except Exception:
            continue

        # Detect language and build message
        lang = detect_language(contact_data, stage_name)
        first_name = contact_data.get("firstName", "") or contact_data.get("first_name", "Hola")
        template = stage_config.get(lang, stage_config.get("es", ""))
        if not template:
            continue

        message = template.format(name=first_name)

        # Get the right channel
        try:
            channel = await get_contact_channel(contact_id)
        except Exception:
            channel = "WhatsApp"

        # Stages that trigger email in addition to WhatsApp/SMS
        NO_ANSWER_STAGES = {"No answer 1-3", "No answer 4-6", "No answer 7-9", "No answer 7-9 Allison", "No answer 7-9 Fatima", "No Answer 7-9 Valeria"}

        # Send the follow-up
        success = await send_followup(contact_data, message, channel)
        if success:
            # Also send email if contact has valid email and is in a No Answer stage
            if stage_name in NO_ANSWER_STAGES:
                email = contact_data.get("email", "") or ""
                if email and is_valid_email(email):
                    try:
                        subject = f"Green Insurance - Seguimiento de tu solicitud de seguro {product}"
                        await send_email(contact_id, subject, message)
                        print(f"[FollowUp] 📧 Email sent to {first_name} ({email}) | {stage_name}")
                    except Exception as e:
                        print(f"[FollowUp] 📧 Email failed for {first_name}: {e}")
                elif email:
                    print(f"[FollowUp] 📧 Invalid email for {first_name}: {email} — skipped")
            await log_reminder_sent(contact_id, first_name, 0, f"followup_{product}_{stage_name[:15]}", today.strftime("%Y-%m-%d"))
            sent += 1
            print(f"[FollowUp] ✅ Sent to {first_name} ({contact_id}) | {product} | {stage_name}")
        else:
            print(f"[FollowUp] ❌ Failed for {first_name} ({contact_id}) | {product} | {stage_name}")

    print(f"[FollowUp] Done — processed: {processed}, sent: {sent}, skipped: {skipped}")
    return {"status": "ok", "processed": processed, "sent": sent, "skipped": skipped}
