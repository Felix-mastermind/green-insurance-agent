"""
Claude AI Agent - Handles lead conversations intelligently
"""
import os
import re
import anthropic
from app.supabase_client import get_conversation_history, save_conversation_message


def clean_ai_response(text: str) -> str:
    """Strip any JSON blocks, code fences or structured data the AI accidentally includes."""
    # Remove ```...``` code blocks (including ```json)
    text = re.sub(r'```[\s\S]*?```', '', text)
    # Remove standalone { ... } JSON objects
    text = re.sub(r'\{[^{}]*\}', '', text)
    # Collapse extra blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

_client = None

def get_client():
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client

SYSTEM_PROMPT_ES = """Eres el asistente virtual de Green Insurance. Tu UNICA funcion es agendar citas con un asesor.

HORARIO DE ASESORES: Lunes a Sabado de 11am a 7pm hora de Nueva York (ET).

TU UNICO OBJETIVO: Conseguir que el cliente agende una cita o acepte una llamada con un asesor en el proximo horario disponible.

FORMATO:
- Solo texto plano. Sin asteriscos, listas ni negritas.
- Maximo 2 oraciones por mensaje.
- Una sola pregunta a la vez.
- NUNCA incluyas JSON, codigo ni corchetes.

IDIOMA: Responde en el mismo idioma del cliente (espanol o ingles).

FLUJO — solo estos pasos:

1. Saluda brevemente e indica que puedes agendar una cita con un asesor de Green Insurance.

2. Pregunta que dia y hora le queda mejor (de lunes a sabado, 11am-7pm ET).

3. Cuando confirme: "Listo! El [dia] a las [hora] un asesor de Green Insurance se va a comunicar contigo. Hasta pronto!"
   Despues de confirmar la cita NO envies ningun mensaje mas.

Si el cliente pregunta sobre precios, coberturas u otra informacion: "Un asesor te puede dar todos los detalles. Que dia y hora te queda mejor para que te llame?"

Si el cliente dice que no le interesa: "Entendido, gracias. Si en algun momento lo necesitas aqui estamos."

Si el numero es equivocado: "Entiendo, disculpa la molestia."

NUNCA hagas seguimiento, NUNCA preguntes por tipo de seguro, NUNCA pidas datos personales. Solo agenda la cita."""

async def get_ai_response(contact_id: str, user_message: str, contact_name: str = "", business_hours: bool = True, product: str = "", next_opening: str = "") -> dict:
    """
    Get AI response for a lead message
    Returns: {"response": str, "should_transfer": bool, "intent": str}
    """
    # Get conversation history
    history = await get_conversation_history(contact_id, limit=8)

    # Build messages for Claude
    messages = []
    for msg in history:
        messages.append({
            "role": msg["role"] if msg["role"] in ["user", "assistant"] else "user",
            "content": msg["content"]
        })

    # Add current message
    messages.append({"role": "user", "content": user_message})

    # Transfer only when client explicitly wants to speak with someone
    transfer_keywords = [
        "asesor", "agente", "hablar con", "cotizar", "quiero comprar",
        "want to talk", "speak with", "call me",
        "precio exacto", "exact price", "quiero hablar", "conectame",
    ]
    # "quiero una cita" removed — handled by wants_appt_kw / wants_appointment intent
    should_transfer = any(kw in user_message.lower() for kw in transfer_keywords)

    # Context check: if client says "sí/yes/ok" and last bot message asked about an advisor
    if not should_transfer and history:
        affirmatives = {"sí", "si", "yes", "ok", "claro", "dale", "está bien", "esta bien",
                        "adelante", "perfecto", "por favor", "please", "sure", "yep", "yeah"}
        msg_clean = user_message.strip().lower().rstrip("!.¡")
        if msg_clean in affirmatives or len(msg_clean) <= 4:
            last_bot = next((m["content"] for m in reversed(history) if m["role"] == "assistant"), "")
            advisor_question_kw = ["asesor", "agente", "llamar", "llamarte", "contactar",
                                   "comunic", "hablar", "te llame", "te contacte"]
            if any(kw in last_bot.lower() for kw in advisor_question_kw):
                should_transfer = True

    # Detect wrong_number and not_interested before calling AI
    msg_lower = user_message.lower()
    wrong_number_keywords = [
        "numero equivocado", "wrong number", "not my number", "equivocado",
        "se equivocaron", "wrong person", "no soy", "not me"
    ]
    not_interested_keywords = [
        # Español — variantes directas
        "no me interesa", "no estoy interesado", "no interesado", "no gracias",
        "no necesito", "no quiero", "no por favor",
        # Variantes "por el momento / ahora"
        "por el momento", "por ahora no", "ahorita no", "no por ahora",
        "no por el momento", "en este momento no", "al momento no",
        "de momento no", "por los momentos", "no en este momento",
        "por el momento no", "no por el momento gracias",
        # Rechazo suave
        "no estoy interesada", "no me interesaria", "no me interesaría",
        "no está en mis planes", "no esta en mis planes",
        "no lo necesito", "no aplica", "no aplica para mi",
        # Inglés
        "not interested", "no thank you", "don't need", "dont need",
        "not right now", "maybe later", "no thanks",
        # "No me molesten" y variantes directas de parar contacto
        "no molesten", "ya no molesten", "no me molesten", "dejen de escribir",
        "dejen de mandar", "no me escriban", "no me manden", "paren de escribir",
        "ya no quiero", "borrenme", "quitenme", "no quiero que me llamen",
        "no quiero mensajes", "no contacten", "no me contacten",
        # Opt-out / SMS unsubscribe keywords (GHL enables DND automatically on these)
        "stop", "unsubscribe", "cancel", "opt out", "optout",
        "remove me", "no more messages", "stop messages", "do not contact",
    ]
    already_insured_keywords = [
        # Español — frases completas
        "ya tengo seguro", "tengo seguro", "ya tengo un seguro", "tengo un seguro",
        "ya tengo cobertura", "tengo cobertura", "ya estoy asegurado", "ya estoy asegurada",
        "ya tengo uno", "ya tengo una",
        # Frases cortas comunes
        "ya tengo", "ya lo tengo", "ya tenemos", "tenemos seguro",
        "ya cuento con", "cuento con seguro",
        # Inglés
        "already have insurance", "already insured", "i have insurance",
        "i have coverage", "i'm already covered", "already covered",
        "i have a policy", "have insurance",
    ]
    if any(kw in msg_lower for kw in wrong_number_keywords):
        reply = "Entiendo, disculpa la molestia."
        await save_conversation_message(contact_id, "user", user_message)
        await save_conversation_message(contact_id, "assistant", reply)
        return {"response": reply, "should_transfer": False, "intent": "wrong_number", "preferred_time": ""}
    if any(kw in msg_lower for kw in already_insured_keywords):
        product_lower = product.lower()
        if "life" in product_lower or "vida" in product_lower:
            reply = ("Entendido! Muchas veces podemos encontrar mejores precios o mayores beneficios "
                     "que tu poliza actual de vida. Te interesaria que un asesor compare tu cobertura "
                     "actual con nuestras opciones sin ningun compromiso?")
        elif "auto" in product_lower:
            reply = ("Entendido! Con frecuencia logramos conseguir mejores precios o mayores "
                     "beneficios que tu seguro actual de auto. Te gustaria que un asesor compare "
                     "tu cobertura y te diga si podemos mejorarla?")
        elif "dental" in product_lower:
            reply = ("Entendido! Ademas de dental, tambien ofrecemos seguros de salud, vida, auto "
                     "y comercial. Hay algun otro tipo de seguro en el que te podamos ayudar?")
        elif "health" in product_lower or "salud" in product_lower:
            reply = ("Entendido! Podemos revisar si hay opciones con mejores precios o coberturas "
                     "adicionales disponibles para ti. Te gustaria que un asesor te contacte?")
        else:
            reply = ("Entendido! Si en algun momento quieres comparar opciones o mejorar tu "
                     "cobertura, aqui estamos. Que tengas un buen dia!")
        await save_conversation_message(contact_id, "user", user_message)
        await save_conversation_message(contact_id, "assistant", reply)
        return {"response": reply, "should_transfer": False, "intent": "already_insured", "preferred_time": ""}
    if any(kw in msg_lower for kw in not_interested_keywords):
        reply = "Entendido, gracias por tu tiempo. Si en el futuro necesitas un seguro, aqui estaremos."
        await save_conversation_message(contact_id, "user", user_message)
        await save_conversation_message(contact_id, "assistant", reply)
        return {"response": reply, "should_transfer": False, "intent": "not_interested", "preferred_time": ""}

    # Detect language — default Spanish, switch to English only if clearly English
    # Check Spanish first: if Spanish words present, always respond in Spanish
    spanish_indicators = [
        "hola", "gracias", "que ", "como ", "cómo", "por favor", "buenos", "buenas",
        "tengo", "necesito", "quiero", "puedo", "puede", "para ", "con ", "del ",
        "los ", "las ", "una ", "uno ", "estoy", "soy ", "este", "seguro", "favor",
        "plis", "pliss", "porfa", "quisiera", "interesa", "lugares", "información",
        "informacion", "también", "tambien", "cuánto", "cuanto", "dónde", "donde",
        "cuál", "cual", "cuales", "cuáles", "me ", "mi ", "mí", "más", "mas ",
        "está", "esta ", "están", "estan", "tiene", "queda", "seria", "sería",
        "bien", "bueno", "buena", "mejor", "cuando", "cuándo",
    ]
    # English indicators — only unambiguous English words (avoid "me", "no", "si", etc.)
    english_indicators = [
        "hello", "hi ", "hey ", " i ", "i'm", "i am", "my name", "do you",
        "can you", "please", "thanks", "thank you",
        "what is", "how much", "where is", "when can", "the insurance",
    ]
    padded = f" {msg_lower} "
    is_spanish = any(ind in padded for ind in spanish_indicators)
    is_english = (not is_spanish) and any(ind in padded for ind in english_indicators)
    system_prompt = SYSTEM_PROMPT_ES

    # First contact — inject intro + product context so bot doesn't ask what they already told us
    is_first_contact = len(history) == 0
    name_hint = contact_name.split()[0] if contact_name else ""
    product_known = product.strip().lower() if product else ""

    # Product context: always inject when known so the AI never asks again mid-conversation
    if product_known:
        if is_english:
            system_prompt += (
                f"\n\nPRODUCTO CONOCIDO: El cliente esta en el pipeline de '{product}'. "
                f"NUNCA preguntes que tipo de seguro necesita — ya lo sabemos. "
                f"Sal de PASO 1 y ve directo a PASO 2 para {product}."
            )
        else:
            system_prompt += (
                f"\n\nPRODUCTO CONOCIDO: El cliente esta en el pipeline de '{product}'. "
                f"NUNCA preguntes que tipo de seguro necesita — ya lo sabemos. "
                f"Sal de PASO 1 y ve directo a PASO 2 para {product}."
            )

    if is_first_contact:
        if product_known:
            if is_english:
                system_prompt += (
                    f"\n\nPRIMER MENSAJE: Presentate brevemente y CONFIRMA el producto. Ejemplo: "
                    f"'Hi{' ' + name_hint if name_hint else ''}! I'm the Green Insurance virtual assistant 😊 "
                    f"I see you're interested in {product} insurance — is that right? "
                    f"[Then go straight to PASO 2 questions for {product}]'"
                )
            else:
                system_prompt += (
                    f"\n\nPRIMER MENSAJE: Presentate brevemente y CONFIRMA el producto directamente. Ejemplo: "
                    f"'Hola{' ' + name_hint if name_hint else ''}! Soy el Asistente Virtual de Green Insurance 😊 "
                    f"Veo que estas interesado en seguro de {product}, es correcto? "
                    f"[Luego ve directo a las preguntas del PASO 2 para {product}]'"
                )
        else:
            if is_english:
                system_prompt += (
                    f"\n\nFIRST MESSAGE: Introduce yourself briefly, then ask what type of insurance "
                    f"they need. Example: 'Hi{' ' + name_hint if name_hint else ''}! "
                    f"I'm the Green Insurance virtual assistant 😊 "
                    f"I'm here to help you find the best coverage. "
                    f"What type of insurance are you interested in?'"
                )
            else:
                system_prompt += (
                    f"\n\nPRIMER MENSAJE: Presentate brevemente y pregunta el tipo de seguro. Ejemplo: "
                    f"'Hola{' ' + name_hint if name_hint else ''}! Soy el Asistente Virtual de Green Insurance 😊 "
                    f"Estoy aqui para ayudarte a encontrar el mejor seguro. "
                    f"En que tipo de seguro estas interesado?'"
                )

    # Inyectar contexto de horario para que el AI sepa cómo cerrar
    if not business_hours:
        _when = next_opening if next_opening else "manana a las 11am"
        system_prompt += (
            f"\n\nHORARIO: FUERA DE OFICINA — Aplica el PASO 3 FUERA DE HORARIO. "
            f"No digas que un asesor llamara ahora. "
            f"Di EXACTAMENTE: 'Ya tenemos tus datos! Nuestros asesores estan disponibles {_when}. A que hora te queda mejor para que te llamemos?' "
            f"Cuando el cliente confirme una hora, di: 'Perfecto! {_when.replace('a las', 'a las').capitalize()} un asesor de Green Insurance te va a llamar. Hasta pronto!'"
        )
    if is_english:
        system_prompt += "\n\nIMPORTANT: The client is writing in English. Respond in English."

    # Push to close fast — after 3+ exchanges, stop collecting info and transfer
    if len(history) >= 3:
        system_prompt += (
            "\n\nATENCION — CIERRA YA: Ya tuviste suficientes intercambios con este lead. "
            "NO sigas haciendo preguntas. Ve DIRECTO al PASO 3: pregunta si prefiere llamada ahora o cita. "
            "Maximo 1 oracion. El asesor humano se encargara del resto."
        )

    try:
        response = get_client().messages.create(
            model="claude-sonnet-4-5",  # Current model (June 2026)
            max_tokens=200,  # Reduced to keep responses short
            system=system_prompt,
            messages=messages
        )
        ai_text = clean_ai_response(response.content[0].text)

        # Save to conversation history
        await save_conversation_message(contact_id, "user", user_message)
        await save_conversation_message(contact_id, "assistant", ai_text)

        # Detect intent from response
        intent = "general"
        preferred_time = ""
        wants_call_kw = [
            "llamar", "llamen", "call me", "call now", "ahora", "now",
            "inmediato", "quiero que me llamen", "me interesa", "si me interesa",
            "quiero cotizar", "quiero comparar", "si quiero", "claro que si",
        ]
        wants_appt_kw = [
            "cita", "appointment", "agendar", "schedule", "programar"
        ]
        # Detect appointment confirmation ONLY from specific phrases in AI response
        # (must be unambiguous — avoid short words like "listo" that appear in many contexts)
        appt_confirmed_kw = [
            "quedo agendada", "quedó agendada", "cita agendada", "appointment booked",
            "agendado para el", "te va a llamar el", "te llamara el",
        ]
        ai_lower = ai_text.lower()

        # Detect not_interested from AI response as fallback
        # (catches soft rejections the keyword list may have missed)
        not_interested_ai_kw = [
            "gracias por tu tiempo", "si en el futuro necesitas",
            "aqui estaremos", "aquí estaremos", "que tengas un buen",
            "if you ever need", "feel free to reach out",
            "good luck", "take care", "cuídate", "cuitate",
        ]

        if any(w in msg_lower for w in wants_call_kw):
            intent = "wants_call"
        elif any(w in msg_lower for w in wants_appt_kw) or (
                any(w in ai_lower for w in appt_confirmed_kw) and
                any(w in msg_lower for w in ["lunes","martes","miércoles","miercoles","jueves",
                                              "viernes","manana","mañana","monday","tuesday",
                                              "wednesday","thursday","friday","tomorrow",
                                              "am","pm",":"])):
            intent = "wants_appointment"
            preferred_time = user_message
        elif any(w in msg_lower for w in ["precio", "costo", "price", "cost", "cuanto"]):
            intent = "pricing"
        else:
            # Cross-sell detection: client mentions a DIFFERENT insurance type than current pipeline
            _product_type_kw = {
                "dental":     ["dental", "dientes", "teeth", "dentista"],
                "life":       ["vida", "life insurance", "seguro de vida"],
                "health":     ["salud", "health insurance", "seguro de salud", "medico"],
                "auto":       ["auto", "carro", "coche", "car ", "vehiculo", "truck"],
                "commercial": ["comercial", "commercial", "negocio", "business", "empresa"],
            }
            def _detect_type(text: str) -> str:
                tl = text.lower()
                for prod, kws in _product_type_kw.items():
                    if any(kw in tl for kw in kws):
                        return prod
                return ""

            current_type = _detect_type(product)
            mentioned_type = _detect_type(user_message)
            if mentioned_type and current_type and mentioned_type != current_type:
                intent = "cross_sell"
                preferred_time = mentioned_type  # reuse field to carry target product
            elif any(w in msg_lower for w in ["dental", "salud", "health", "auto", "vida", "life"]):
                intent = "product_interest"

        # Fallback: if AI said goodbye/farewell → not_interested (soft rejection)
        if intent == "general" and any(w in ai_lower for w in not_interested_ai_kw):
            intent = "not_interested"

        return {
            "response": ai_text,
            "should_transfer": should_transfer,
            "intent": intent,
            "preferred_time": preferred_time,
        }

    except Exception as e:
        print(f"[Claude Agent] Error: {e}")
        # Fallback message
        fallback = ("Hola! Gracias por contactar a Green Insurance. "
                    "Un asesor se comunicara contigo en breve. "
                    "Si es urgente, llamanos directamente.")
        return {
            "response": fallback,
            "should_transfer": True,
            "intent": "error",
            "preferred_time": "",
        }


FOLLOWUP_SYSTEM_PROMPT = """Eres el asistente virtual de Green Insurance, agencia de seguros en USA.

Tu tarea: generar UN mensaje de seguimiento corto para un lead que no ha respondido.

REGLAS ESTRICTAS:
- Solo texto plano. Sin asteriscos, guiones, listas ni negritas.
- Maximo 2 oraciones.
- PROHIBIDO repetir frases, preguntas o temas que ya aparezcan en el historial.
- Cada mensaje debe sentirse diferente al anterior: cambia el enfoque, el tono o el angulo.
- Si no hay historial previo, saluda brevemente y pregunta si tienen un momento para hablar sobre su seguro.
- Termina siempre con una pregunta concreta o una invitacion clara a responder.
- NUNCA menciones precios ni presupuestos.
- NUNCA pidas datos sensibles: SSN, cuentas bancarias, numeros de ruta ni informacion financiera.
- NUNCA expliques que eres un sistema automatico, que no tienes historial ni menciones tu funcionamiento interno.
- NUNCA le pidas al cliente que te muestre mensajes anteriores ni que te explique la conversacion.
- Si no tienes suficiente contexto, genera igual un mensaje amigable y natural sobre {product}.
- El seguro en cuestion es: {product}. Mantente enfocado en ese producto."""


async def get_ai_followup(contact_id: str, product: str, stage_name: str, contact_name: str = "", lang: str = "es") -> str:
    """
    Generate a contextual follow-up message based on conversation history.
    Used by follow_ups.py for dynamic, non-repeating messages.
    Returns the message text, or empty string on failure.
    """
    try:
        history = await get_conversation_history(contact_id, limit=10)

        system = FOLLOWUP_SYSTEM_PROMPT.format(product=product)
        if lang == "en":
            system += "\n\nIMPORTANT: Respond in English."

        messages = []
        for msg in history:
            role = msg["role"] if msg["role"] in ["user", "assistant"] else "user"
            messages.append({"role": role, "content": msg["content"]})

        name_hint = f" El nombre del cliente es {contact_name}." if contact_name else ""
        if stage_name == "Follow Up to Close":
            stage_hint = (
                " IMPORTANTE: Este lead ya recibio una oferta o cotizacion. "
                "El mensaje DEBE preguntar sobre la oferta: que penso, si pudo revisarla, si tiene preguntas sobre la cobertura. "
                "NO uses frases genericas de seguimiento ni preguntes si sigue interesado. Enfocate en la oferta ya presentada."
            )
        else:
            stage_hint = ""
        prompt = (
            f"El lead lleva dias sin responder y esta en el stage '{stage_name}' del pipeline de seguro de {product}."
            f"{name_hint}{stage_hint} Genera un mensaje de seguimiento corto, diferente a los anteriores, que continue el hilo natural de la conversacion."
        )
        messages.append({"role": "user", "content": prompt})

        response = get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system=system,
            messages=messages,
        )
        result = clean_ai_response(response.content[0].text.strip())

        # Safety check: if AI leaked internal reasoning, discard and use fallback
        _leak_phrases = [
            "no tengo acceso", "historial de mensajes", "no puedo ver",
            "necesito que me muestres", "mensajes previos", "sistema automatico",
            "i don't have access", "previous messages", "show me the",
        ]
        if any(phrase in result.lower() for phrase in _leak_phrases):
            print(f"[FollowUp AI] Leaked internal reasoning for {contact_id} — using fallback")
            name = contact_name.split()[0] if contact_name else ""
            if lang == "en":
                result = f"Hi{' ' + name if name else ''}! Just checking in — are you still interested in learning about your {product} insurance options?"
            else:
                result = f"Hola{' ' + name if name else ''}! Solo queria dar seguimiento. Sigues interesado en explorar opciones de seguro de {product}?"

        return result
    except Exception as e:
        print(f"[FollowUp AI] Error generating followup for {contact_id}: {e}")
        return ""
