"""
Follow-Up Module
Runs every 2 hours during business hours
Reviews leads by stage in active pipelines and sends product-specific follow-up messages
"""
import pytz
from datetime import datetime, timedelta
from app.ghl_client import (
    get_opportunities, get_pipelines, send_whatsapp, send_sms, send_email, is_valid_email,
    get_contact, add_contact_tag, create_task, get_contact_channel, add_bot_stamp
)
from app.supabase_client import check_reminder_sent, log_reminder_sent, get_conversation_history, save_conversation_message
from app.claude_agent import get_ai_followup

ET = pytz.timezone("America/New_York")

PIPELINES = {
    "HzCwe9SCtirKXGFdFLVT": "dental",
    "BdzkOH5twVi9sCK2ag96": "auto",
    "XrTzKSNz9VpYuSvVZzyH": "life",
}

SKIP_STAGES = {
    # Cerrados / finales
    "Won", "won",
    "DND",
    "Wrong number", "Wrong Number",
    # New Lead: asesor llama 3 veces primero — bot en silencio
    "New Lead", "New Leads",
    # Asesor activo — no interferir
    "HOT Leads", "Hot Lead",
    "Appointment Booked",
    "Quoted",
    # Descartados — no contactar
    "Not interested", "Not Interested", "Not Insterested",
    "Not Eligible",
    "Offer not accepted",
    "REJECT",
}

ASESOR_ACTIVE_STAGES = {"Appointment Booked", "Quoted", "HOT Leads", "Hot Lead"}

MESSAGES = {
    "dental": {
        "New Lead": {
            "es": "Hola {name}! Sonrisa Te escribimos de Green Insurance. Vimos que estas interesado en un seguro dental. Cuando tienes unos minutos para que un asesor te llame?",
            "en": "Hi {name}! This is Green Insurance. We saw you are interested in dental insurance. When is a good time for one of our advisors to call you?",
            "days": 2,
        },
        "Contacted": {
            "es": "Hola {name}! Solo queria confirmar que recibiste la informacion sobre el seguro dental. Tienes alguna pregunta para nuestro asesor?",
            "en": "Hi {name}! Just checking that you received the dental insurance information. Do you have any questions for our advisor?",
            "days": 2,
        },
        "No answer 1-3": {
            "es": "Hola {name}! Hemos intentado comunicarnos contigo sobre tu seguro dental. Hay un mejor horario para llamarte? Estamos disponibles de L-V 11am-7pm.",
            "en": "Hi {name}! We have been trying to reach you about dental insurance. Is there a better time to call? We are available Mon-Fri 11am-7pm.",
            "days": 2,
        },
        "No answer 4-6": {
            "es": "Hola {name}! Seguimos aqui para ayudarte con tu seguro dental. Si prefieres, puedes escribirnos aqui mismo y un asesor te responde hoy.",
            "en": "Hi {name}! We are still here to help you with dental insurance. Feel free to reply here and an advisor will get back to you today.",
            "days": 2,
        },
        "No answer 7-9": {
            "es": "Hola {name}! Ultimo mensaje de nuestra parte sobre el seguro dental. Si en algun momento necesitas cobertura dental, aqui estaremos. Que tengas un excelente dia!",
            "en": "Hi {name}! Last follow-up from us about dental insurance. Whenever you need coverage, we are here. Have a great day!",
            "days": 2,
        },
        "No answer 7-9 Allison": {
            "es": "Hola {name}! Ultimo mensaje de nuestra parte sobre el seguro dental. Si en algun momento necesitas cobertura dental, aqui estaremos. Que tengas un excelente dia!",
            "en": "Hi {name}! Last follow-up from us about dental insurance. Whenever you need coverage, we are here. Have a great day!",
            "days": 2,
        },
        "No answer 7-9 Fatima": {
            "es": "Hola {name}! Ultimo mensaje de nuestra parte sobre el seguro dental. Si en algun momento necesitas cobertura dental, aqui estaremos. Que tengas un excelente dia!",
            "en": "Hi {name}! Last follow-up from us about dental insurance. Whenever you need coverage, we are here. Have a great day!",
            "days": 2,
        },
        "Reactivation - 60+ Days": {
            "es": "Hola {name}! Han pasado unos meses desde tu consulta sobre seguro dental. Tenemos nuevos planes disponibles. Te interesa una cotizacion actualizada?",
            "en": "Hi {name}! It has been a while since you asked about dental insurance. We have new plans available. Interested in an updated quote?",
            "days": 2,
        },
        "Follow Up to Close": {
            "es": "Hola {name}! Pudiste revisar las opciones de seguro dental? Podemos activar tu poliza esta semana. Te llamo hoy?",
            "en": "Hi {name}! Were you able to review the dental insurance options? We can get your policy started this week. Shall I call you today?",
            "days": 2,
        },
        "Missed Appointment": {
            "es": "Hola {name}! Vimos que no pudiste asistir a tu cita. No hay problema, cuando te queda bien reagendar para tu seguro dental?",
            "en": "Hi {name}! We noticed you missed your appointment. No worries, when would you like to reschedule for your dental insurance?",
            "days": 2,
        },
    },

    "auto": {
        "New Lead": {
            "es": "Hola {name}! Auto Te escribimos de Green Insurance. Vimos que necesitas seguro de auto. Tienes unos minutos para que un asesor te llame y te de opciones?",
            "en": "Hi {name}! This is Green Insurance. We saw you need auto insurance. Do you have a few minutes for an advisor to call you with options?",
            "days": 2,
        },
        "Contacted": {
            "es": "Hola {name}! Solo un seguimiento rapido sobre tu seguro de auto. Ya tuviste oportunidad de revisar la informacion? Alguna pregunta?",
            "en": "Hi {name}! Quick follow-up on your auto insurance. Did you get a chance to review? Any questions?",
            "days": 2,
        },
        "No answer 1-3": {
            "es": "Hola {name}! Hemos intentado contactarte sobre tu seguro de auto. Hay un mejor momento para llamarte? Disponibles L-V 11am-7pm.",
            "en": "Hi {name}! We have tried reaching you about auto insurance. Is there a better time to call? Available Mon-Fri 11am-7pm.",
            "days": 2,
        },
        "No answer 4-6": {
            "es": "Hola {name}! Sabemos que estas ocupado. Si quieres info sobre tu seguro de auto, escribenos aqui y te respondemos al momento.",
            "en": "Hi {name}! We know you are busy. If you want info on auto insurance, just reply here and we will get back to you right away.",
            "days": 2,
        },
        "No answer 7-9": {
            "es": "Hola {name}! Ultimo intento de contacto sobre tu seguro de auto. Cuando estes listo, aqui estaremos. Saludos de Green Insurance!",
            "en": "Hi {name}! Last follow-up on auto insurance. When you are ready, we are here. Best, Green Insurance!",
            "days": 2,
        },
        "No Answer 7-9 Valeria": {
            "es": "Hola {name}! Ultimo intento de contacto sobre tu seguro de auto. Cuando estes listo, aqui estaremos. Saludos de Green Insurance!",
            "en": "Hi {name}! Last follow-up on auto insurance. When you are ready, we are here. Best, Green Insurance!",
            "days": 2,
        },
        "Reactivation - 60+ Days": {
            "es": "Hola {name}! Han pasado unos meses desde tu consulta sobre seguro de auto. Tenemos nuevas opciones disponibles. Te interesa una cotizacion actualizada?",
            "en": "Hi {name}! It has been a while since you asked about auto insurance. We have new options available. Interested in an updated quote?",
            "days": 2,
        },
        "Follow Up to Close": {
            "es": "Hola {name}! Ya decidiste sobre tu seguro de auto? Podemos activar tu cobertura hoy mismo. Te llamamos?",
            "en": "Hi {name}! Have you decided on your auto insurance? We can get you covered today. Should we call?",
            "days": 2,
        },
        "Missed Appointment": {
            "es": "Hola {name}! No pudiste asistir a tu cita para el seguro de auto. Cuando te queda bien reagendar?",
            "en": "Hi {name}! You missed your auto insurance appointment. When can we reschedule?",
            "days": 2,
        },
        "HOT Leads": {"dynamic": True, "days": 2},
        "Quoted": {
            "es": "Hola {name}! Pudiste revisar la cotizacion de tu seguro de auto? Estamos para resolver cualquier duda antes de que tomes tu decision.",
            "en": "Hi {name}! Were you able to review your auto insurance quote? We are here to answer any questions before you decide.",
            "days": 2,
        },
    },

    "life": {
        "New Leads": {
            "es": "Hola {name}! Te escribimos de Green Insurance. Vimos que estas interesado en seguro de vida. Es una decision importante, cuando puedo conectarte con un asesor?",
            "en": "Hi {name}! This is Green Insurance. We saw you are interested in life insurance. It is an important decision, when can I connect you with an advisor?",
            "days": 2,
        },
        "Contacted": {
            "es": "Hola {name}! Seguimiento sobre tu seguro de vida. Tuviste oportunidad de revisar la informacion? Tienes preguntas?",
            "en": "Hi {name}! Following up on your life insurance inquiry. Did you have a chance to review? Any questions?",
            "days": 2,
        },
        "No answer 1-3": {
            "messages": [
                {
                    "es": "Hola {name}! Hemos intentado comunicarnos sobre tu seguro de vida. Hay un mejor horario para llamarte? Estamos disponibles L-V 11am-7pm.",
                    "en": "Hi {name}! We have been trying to reach you about life insurance. Is there a better time to call? We are available Mon-Fri 11am-7pm.",
                },
                {
                    "es": "Hola {name}! Seguimos intentando contactarte de Green Insurance. Un asesor tiene opciones disponibles para ti, puedes responder aqui cuando tengas un momento?",
                    "en": "Hi {name}! Still trying to reach you from Green Insurance. An advisor has options ready for you, can you reply here when you get a chance?",
                },
                {
                    "es": "Hola {name}! Sabemos que estas ocupado. Si prefieres, cuentanos aqui que tipo de cobertura de vida buscas y te respondemos de inmediato.",
                    "en": "Hi {name}! We know you are busy. If you prefer, just tell us here what kind of life coverage you are looking for and we will reply right away.",
                },
            ],
            "days": 2,
        },
        "No answer 4-6": {
            "messages": [
                {
                    "es": "Hola {name}! Seguimos disponibles para ayudarte con tu seguro de vida. Hay algo que podamos aclarar antes de que hables con un asesor?",
                    "en": "Hi {name}! We are still here to help with your life insurance. Is there anything we can clarify before you speak with an advisor?",
                },
                {
                    "es": "Hola {name}! Sabias que un seguro de vida puede proteger a tu familia por muy poco al mes? Escribenos aqui y buscamos la mejor opcion para ti.",
                    "en": "Hi {name}! Did you know life insurance can protect your family for very little per month? Message us here and we will find the best option for you.",
                },
                {
                    "es": "Hola {name}! Queremos asegurarnos de que tengas la informacion que necesitas sobre seguro de vida. Tienes alguna duda especifica que podamos resolver?",
                    "en": "Hi {name}! We want to make sure you have the information you need about life insurance. Do you have any specific questions we can answer?",
                },
            ],
            "days": 2,
        },
        "No answer 7-9": {
            "messages": [
                {
                    "es": "Hola {name}! Seguimos aqui de Green Insurance. Si necesitas ayuda con tu seguro de vida, responde cuando puedas.",
                    "en": "Hi {name}! Still here at Green Insurance. If you need help with life insurance, reply whenever you are ready.",
                },
                {
                    "es": "Hola {name}! Sabemos que a veces no es el momento correcto. Cuando quieras retomar la conversacion sobre tu seguro de vida, aqui estaremos.",
                    "en": "Hi {name}! We know timing is not always right. Whenever you want to revisit life insurance, we are here.",
                },
                {
                    "es": "Hola {name}! Ultimo mensaje de nuestra parte. Cuando estes listo para proteger a tu familia, aqui estaremos en Green Insurance. Que tengas un excelente dia!",
                    "en": "Hi {name}! Last message from us. When you are ready to protect your family, Green Insurance will be here. Have a great day!",
                },
            ],
            "days": 2,
        },
        "HOT Leads": {
            "es": "Hola {name}! Un asesor de Green Insurance te llamara muy pronto para ayudarte con tu seguro de vida. Hay algun horario que prefieras?",
            "en": "Hi {name}! A Green Insurance advisor will call you very soon about your life insurance. Is there a preferred time?",
            "days": 2,
        },
        "Follow Up to Close": {
            "es": "Hola {name}! Pudiste pensar en las opciones de seguro de vida? Podemos activar tu poliza esta semana. Hablamos hoy?",
            "en": "Hi {name}! Were you able to think about the life insurance options? We can get your policy started this week. Shall we talk today?",
            "days": 2,
        },
        "Missed Appointment": {
            "es": "Hola {name}! No pudiste asistir a tu cita para el seguro de vida. Cuando podemos reagendar?",
            "en": "Hi {name}! You missed your life insurance appointment. When can we reschedule?",
            "days": 2,
        },
    },
}

# Life-Sebastian: leads antiguos (llenaron datos hace +1 ano), enfoque reactivacion
LIFE_SEBASTIAN_NO_ANSWER = {
    "No answer 1-3": {
        "messages": [
            {
                "es": "Hola {name}! Te escribimos de Green Insurance. Hace un tiempo mostraste interes en un seguro de vida, sigues con tu cobertura de vida actual o ha cambiado algo en tu situacion?",
                "en": "Hi {name}! This is Green Insurance. A while back you showed interest in life insurance, do you still have your current coverage, or has anything changed?",
            },
            {
                "es": "Hola {name}! Seguimos aqui de Green Insurance. Si tu situacion familiar ha cambiado o quieres revisar tu cobertura de vida actual, un asesor puede ayudarte sin compromiso. Tienes unos minutos?",
                "en": "Hi {name}! Green Insurance here. If your family situation has changed or you want to review your current coverage, an advisor can help with no obligation. Do you have a few minutes?",
            },
            {
                "es": "Hola {name}! Actualmente tienes seguro de vida? Si es asi, podriamos revisar si tienes la cobertura adecuada para tu familia hoy. Escribenos aqui cuando gustes.",
                "en": "Hi {name}! Do you currently have life insurance? If so, we could review whether you have the right coverage for your family today. Message us here whenever you are ready.",
            },
        ],
        "days": 2,
    },
    "No answer 4-6": {
        "messages": [
            {
                "es": "Hola {name}! De Green Insurance. Si tu poliza de vida actual ya no te convence o quieres comparar opciones, podemos ayudarte a encontrar algo mejor. Hablamos?",
                "en": "Hi {name}! Green Insurance here. If your current life policy no longer works for you or you want to compare options, we can help you find something better. Shall we talk?",
            },
            {
                "es": "Hola {name}! Sabias que muchas personas con seguros de vida antiguos pueden obtener mejor cobertura al mismo precio hoy? Podemos revisar tu situacion sin compromiso.",
                "en": "Hi {name}! Did you know many people with older life policies can get better coverage at the same price today? We can review your situation with no obligation.",
            },
            {
                "es": "Hola {name}! Seguimos disponibles si quieres comparar tu cobertura de vida actual con lo que podemos ofrecerte. Sin prisa, escribenos cuando gustes.",
                "en": "Hi {name}! We are still here if you would like to compare your current coverage with what we can offer. No rush, message us whenever you are ready.",
            },
        ],
        "days": 2,
    },
    "No answer 7-9": {
        "messages": [
            {
                "es": "Hola {name}! Ultimo seguimiento de Green Insurance. Si en algun momento quieres revisar o mejorar tu seguro de vida, aqui estaremos.",
                "en": "Hi {name}! Last follow-up from Green Insurance. Whenever you want to review or improve your life insurance, we are here to help.",
            },
            {
                "es": "Hola {name}! No queremos ser insistentes. Solo queremos que sepas que tenemos opciones para actualizar tu cobertura de vida cuando lo necesites.",
                "en": "Hi {name}! We do not want to be pushy. We just want you to know we have options to update your life coverage whenever you need.",
            },
            {
                "es": "Hola {name}! Cerramos el contacto por ahora. Cuando necesites revisar tu seguro de vida, Green Insurance estara aqui. Hasta pronto!",
                "en": "Hi {name}! We will close out contact for now. When you need to review your life insurance, Green Insurance will be here. See you soon!",
            },
        ],
        "days": 2,
    },
}

LIFE_SEBASTIAN_STAGES = set(LIFE_SEBASTIAN_NO_ANSWER.keys())

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
    if stage_name.lower() == "english":
        return "en"
    tags = contact.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip().lower() for t in tags.split(",")]
    tags_lower = [str(t).lower() for t in tags]
    if "english" in tags_lower or "en" in tags_lower:
        return "en"
    return "es"


def has_tag(contact: dict, tag: str) -> bool:
    tags = contact.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip().lower() for t in tags.split(",")]
    else:
        tags = [str(t).lower() for t in tags]
    return tag.lower() in tags


def get_followup_key(today: datetime, contact_id: str, stage: str) -> str:
    return f"{today.strftime('%Y-%m-%d')}_{contact_id}_{stage[:20]}"


async def send_followup(contact: dict, message: str, channel: str = "WhatsApp") -> bool:
    contact_id = contact.get("id", "")
    try:
        if channel == "SMS":
            result = await send_sms(contact_id, message)
        else:
            result = await send_whatsapp(contact_id, message)
        success = bool(result.get("conversationId") or result.get("id") or result.get("messageId"))
        if success:
            await add_bot_stamp(contact_id)
        return success
    except Exception as e:
        print(f"[FollowUp] Error sending to {contact_id}: {e}")
        return False


async def run_follow_ups(force: bool = False):
    """Main follow-up runner -- checks all leads in active pipelines by stage"""
    if not force and not is_business_hours_followup():
        print("[FollowUp] Outside business hours -- skipping")
        return {"status": "skipped", "reason": "outside_hours"}

    now = datetime.now(ET)
    today = now.date()
    print(f"[FollowUp] Starting follow-up run at {now.strftime('%Y-%m-%d %H:%M ET')}")

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
            continue

        stage_id = opp.get("pipelineStageId", "")
        stage_name = stage_map.get(stage_id, "")
        if not stage_name:
            print(f"[FollowUp] No stage name for stageId={stage_id}")
            continue

        if any(stage_name.lower() == s.lower() for s in SKIP_STAGES):
            continue

        # Cross-sell
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
                msg = f"Hi {first_name}! We understand {product} insurance was not the right fit. Did you know we also offer {others}? We would love to help you find the right coverage."
            else:
                msg = f"Hola {first_name}! Entendemos que el seguro de {product} no era lo que buscabas. Sabias que tambien ofrecemos {others}? Nos encantaria ayudarte a encontrar la cobertura ideal."
            channel = await get_contact_channel(contact_id)
            if channel == "SMS":
                await send_sms(contact_id, msg)
            else:
                await send_whatsapp(contact_id, msg)
            await log_reminder_sent(contact_id, first_name, 0, f"crosssell_{product}", today.strftime("%Y-%m"))
            sent += 1
            print(f"[FollowUp] Cross-sell sent to {first_name} ({contact_id}) | was: {product}")
            continue

        # Already Insured
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
                msg = f"Hi {first_name}! We know you already have insurance, but we are still here whenever you want to compare options. We have a variety of plans that might surprise you!"
            else:
                msg = f"Hola {first_name}! Sabemos que ya cuentas con seguro, pero seguimos aqui por si algun dia quieres cotizar. Tenemos varias opciones que podrian sorprenderte."
            channel = await get_contact_channel(contact_id)
            if channel == "SMS":
                await send_sms(contact_id, msg)
            else:
                await send_whatsapp(contact_id, msg)
            await log_reminder_sent(contact_id, first_name, 0, f"already_insured_{product}", today.strftime("%Y-%m"))
            sent += 1
            print(f"[FollowUp] Already Insured msg sent to {first_name} ({contact_id})")
            continue

        # Standard follow-up
        product_messages = MESSAGES.get(product, {})
        stage_config = product_messages.get(stage_name)
        if not stage_config:
            continue

        processed += 1
        contact_id = opp.get("contactId", "")
        if not contact_id:
            continue

        days_in_stage = 0
        stage_changed_at = opp.get("lastStageChangeAt", "") or opp.get("updatedAt", "")
        if stage_changed_at:
            try:
                import dateutil.parser
                stage_time = dateutil.parser.parse(stage_changed_at)
                if stage_time.tzinfo is None:
                    from datetime import timezone
                    stage_time = stage_time.replace(tzinfo=timezone.utc)
                days_in_stage = (datetime.now(ET).replace(tzinfo=None) - stage_time.replace(tzinfo=None)).days
            except Exception:
                pass

        required_days = stage_config.get("days", 2)
        messages_list = stage_config.get("messages")

        if messages_list:
            # Rotating messages: msg1 eligible after required_days, msg2 after 2x, etc.
            max_attempt = (days_in_stage // required_days) - 1
            if not force and max_attempt < 0:
                skipped += 1
                continue

            max_attempt = min(max_attempt, len(messages_list) - 1)

            try:
                contact_data = await get_contact(contact_id)
                if not contact_data:
                    continue
            except Exception:
                continue

            # Override for life-sebastian leads (old leads, reactivation messaging)
            if product == "life" and stage_name in LIFE_SEBASTIAN_STAGES:
                if has_tag(contact_data, "life-sebastian"):
                    stage_config = LIFE_SEBASTIAN_NO_ANSWER[stage_name]
                    messages_list = stage_config["messages"]
                    max_attempt = min(max_attempt, len(messages_list) - 1)

            lang = detect_language(contact_data, stage_name)
            first_name = contact_data.get("firstName", "") or contact_data.get("first_name", "Hola")
            year_str = today.strftime("%Y")

            template_dict = None
            chosen_attempt = None
            for idx in range(0, max_attempt + 1):
                attempt_key = f"followup_{product}_{stage_name[:12]}_m{idx + 1}"
                already_sent = await check_reminder_sent(contact_id, attempt_key, year_str)
                if not already_sent:
                    template_dict = messages_list[idx]
                    chosen_attempt = idx
                    break

            if template_dict is None:
                skipped += 1
                continue

            template = template_dict.get(lang, template_dict.get("es", ""))
            if not template:
                continue

            message = template.format(name=first_name)
            followup_key_to_log = f"followup_{product}_{stage_name[:12]}_m{chosen_attempt + 1}"
            log_period = year_str

        else:
            if not force and days_in_stage < required_days:
                skipped += 1
                continue

            already_sent = await check_reminder_sent(contact_id, f"followup_{product}_{stage_name[:15]}", today.strftime("%Y-%m-%d"))
            if already_sent:
                skipped += 1
                continue

            try:
                contact_data = await get_contact(contact_id)
                if not contact_data:
                    continue
            except Exception:
                continue

            lang = detect_language(contact_data, stage_name)
            first_name = contact_data.get("firstName", "") or contact_data.get("first_name", "Hola")
            template = stage_config.get(lang, stage_config.get("es", ""))
            is_dynamic = stage_config.get("dynamic", False)
            if not template and not is_dynamic:
                continue
            message = template.format(name=first_name) if template else ""
            followup_key_to_log = f"followup_{product}_{stage_name[:15]}"
            log_period = today.strftime("%Y-%m-%d")
        # Use AI to generate contextual message — reads full conversation so it never repeats
        is_stage_dynamic = stage_config.get("dynamic", False)
        conv_history = await get_conversation_history(contact_id, limit=10)
        if conv_history or is_stage_dynamic:
            ai_msg = await get_ai_followup(contact_id, product, stage_name, first_name, lang)
            if ai_msg:
                message = ai_msg
                print(f"[FollowUp] AI message for {first_name} | {product} | {stage_name}")
            elif not message:
                print(f"[FollowUp] AI failed and no template for {first_name} | {stage_name} -- skipping")
                skipped += 1
                continue
        try:
            channel = await get_contact_channel(contact_id)
        except Exception:
            channel = "WhatsApp"

        NO_ANSWER_STAGES = {"No answer 1-3", "No answer 4-6", "No answer 7-9", "No answer 7-9 Allison", "No answer 7-9 Fatima", "No Answer 7-9 Valeria"}

        success = await send_followup(contact_data, message, channel)
        if success:
            # Guardar en historial de Supabase para que el AI no repita en próximos follow-ups
            try:
                await save_conversation_message(contact_id, "assistant", message)
            except Exception as e:
                print(f"[FollowUp] Warning: could not save message to history for {contact_id}: {e}")

            if stage_name in NO_ANSWER_STAGES:
                email = contact_data.get("email", "") or ""
                if email and is_valid_email(email):
                    try:
                        subject = f"Green Insurance - Seguimiento de tu solicitud de seguro {product}"
                        await send_email(contact_id, subject, message)
                        print(f"[FollowUp] Email sent to {first_name} ({email}) | {stage_name}")
                    except Exception as e:
                        print(f"[FollowUp] Email failed for {first_name}: {e}")
                elif email:
                    print(f"[FollowUp] Invalid email for {first_name}: {email} -- skipped")
            await log_reminder_sent(contact_id, first_name, 0, followup_key_to_log, log_period)
            sent += 1
            attempt_label = f" (msg {chosen_attempt + 1})" if messages_list else ""
            print(f"[FollowUp] OK Sent to {first_name} ({contact_id}) | {product} | {stage_name}{attempt_label}")
        else:
            print(f"[FollowUp] FAIL for {first_name} ({contact_id}) | {product} | {stage_name}")

    print(f"[FollowUp] Done -- processed: {processed}, sent: {sent}, skipped: {skipped}")
    return {"status": "ok", "processed": processed, "sent": sent, "skipped": skipped}
