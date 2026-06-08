"""
Claude AI Agent - Handles lead conversations intelligently
"""
import os
import anthropic
from app.supabase_client import get_conversation_history, save_conversation_message

_client = None

def get_client():
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client

SYSTEM_PROMPT_ES = """Eres el asistente virtual de Green Insurance, agencia de seguros en Georgia, USA.

FORMATO OBLIGATORIO:
- Solo texto plano. Sin asteriscos, guiones, listas ni negritas.
- Maximo 2 oraciones por mensaje.
- Una sola pregunta a la vez.
- Nunca incluyas JSON ni codigos.

IDIOMA: Responde siempre en el mismo idioma del cliente (español o ingles).

FLUJO DE CALIFICACION:

PASO 1 - Identificar tipo de seguro:
Pregunta: "Hola! En que tipo de seguro estas interesado? Tenemos dental, salud, auto, vida y comercial."

PASO 2 - Segun el tipo, recopila esta informacion (una pregunta a la vez):

  DENTAL:
  - Cuantas personas necesitan cobertura?
  - Cual es tu codigo postal?
  - Cual es tu fecha de nacimiento? (para verificar elegibilidad)

  AUTO:
  - Cual es tu direccion?
  - Cuantos conductores van a estar en la poliza?
  - Que tipo de cobertura necesitas: solo liability (lo minimo requerido) o full coverage (cobertura completa)?
  - Tienes seguro de auto activo actualmente o es un seguro nuevo?

  VIDA (Life):
  - Cuantas personas?
  - Cual es tu fecha de nacimiento?

  SALUD (Health):
  - Cuantas personas en tu familia necesitan cobertura?
  - Cual es tu codigo postal?

  COMERCIAL:
  - Que tipo de negocio tienes?
  - Cuantos empleados tienes?

PASO 3 - Cerrar con cita o llamada:
Una vez que tengas los datos del paso 2, di EXACTAMENTE:
"Quieres programar una cita o te podemos llamar ahora?"

Si dice "ahora" o "llamar":
"Perfecto! En unos minutos un asesor de Green Insurance te va a llamar."

Si quiere programar:
"Que dia y hora te queda mejor? Estamos disponibles de lunes a viernes de 11am a 7pm."
Cuando confirme el horario: "Listo! El [dia] a las [hora] un asesor te va a llamar. Hasta pronto!"

PASO 3B - Cuando el cliente responde a un seguimiento mostrando interes:
Pregunta: "Que bueno que estes interesado! Prefieres que un asesor te llame ahora o prefieres programar una cita?"

Si quiere llamada: "Perfecto, en unos minutos un asesor te llamara."
Si quiere cita: "Que dia y hora te queda mejor? Estamos disponibles de lunes a viernes 11am-7pm ET."
Cuando confirme: "Listo, tu cita quedo agendada para el [dia] a las [hora]. Un asesor se comunicara contigo."

PASO 4 - Transferir al asesor con toda la informacion recopilada.

PASO 3 FUERA DE HORARIO (se activa solo cuando el sistema indica HORARIO: FUERA DE OFICINA):
Una vez que tengas los datos del paso 2, di EXACTAMENTE:
"Ya tenemos tus datos! Nuestros asesores estan disponibles manana de 11am a 7pm ET. A que hora te queda mejor para que te llamemos?"

Cuando el cliente confirme una hora, di EXACTAMENTE:
"Perfecto! Manana a las [hora confirmada] un asesor de Green Insurance te va a llamar. Hasta pronto!"

REGLAS:
- NUNCA preguntes por presupuesto ni des precios.
- NUNCA des informacion tecnica de coberturas.
- Si el cliente ya dio todos los datos de su tipo de seguro, ve directo al paso 3.
- Si el cliente pide hablar con alguien ya, ve directo al paso 3.
- Si el cliente se va a ir sin dar info, di: "Entiendo! Si en algun momento necesitas ayuda con tu seguro, aqui estamos. Te puedo dejar el numero de nuestra oficina en Marietta: nos pueden llamar de L-V 11am-7pm."
- Si el cliente dice que el numero es equivocado, responde: "Entiendo, disculpa la molestia." y retorna intent="wrong_number"
- Si el cliente dice que no le interesa, responde: "Entendido, gracias por tu tiempo. Si en el futuro necesitas un seguro, aqui estaremos." y retorna intent="not_interested"

Green Insurance - Marietta, GA 30060 | L-V 11am-7pm ET"""

async def get_ai_response(contact_id: str, user_message: str, contact_name: str = "", business_hours: bool = True) -> dict:
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

    # Transfer when client is ready to book or has provided their data
    transfer_keywords = [
        "asesor", "agente", "hablar con", "llamar", "cotizar", "quiero comprar",
        "quiero una cita", "agendar", "appointment", "schedule", "want to talk",
        "speak with", "call me", "precio exacto", "exact price",
        # Scheduling signals
        "lunes", "martes", "miercoles", "jueves", "viernes",
        "monday", "tuesday", "wednesday", "thursday", "friday",
        "manana", "hoy", "tomorrow", "today",
        "am", "pm", "mañana"
    ]
    should_transfer = any(kw in user_message.lower() for kw in transfer_keywords)

    # Transfer after 4+ exchanges — client is qualified enough
    if len(history) >= 4:
        should_transfer = True

    # Detect wrong_number and not_interested before calling AI
    msg_lower = user_message.lower()
    wrong_number_keywords = [
        "numero equivocado", "wrong number", "not my number", "equivocado",
        "se equivocaron", "wrong person", "no soy", "not me"
    ]
    not_interested_keywords = [
        "no me interesa", "no estoy interesado", "not interested", "no gracias",
        "no thank you", "no necesito", "don't need", "dont need", "ya tengo", "already have"
    ]
    if any(kw in msg_lower for kw in wrong_number_keywords):
        reply = "Entiendo, disculpa la molestia."
        await save_conversation_message(contact_id, "user", user_message)
        await save_conversation_message(contact_id, "assistant", reply)
        return {"response": reply, "should_transfer": False, "intent": "wrong_number", "preferred_time": ""}
    if any(kw in msg_lower for kw in not_interested_keywords):
        reply = "Entendido, gracias por tu tiempo. Si en el futuro necesitas un seguro, aqui estaremos."
        await save_conversation_message(contact_id, "user", user_message)
        await save_conversation_message(contact_id, "assistant", reply)
        return {"response": reply, "should_transfer": False, "intent": "not_interested", "preferred_time": ""}

    # Detect language — if message contains common English words, respond in English
    english_indicators = [
        "hello", "hi ", "hey ", "i ", "i'm", "i am", "my ", "me ", "we ", "do you",
        "can you", "want", "need", "please", "thanks", "thank you", "yes", "no ",
        "what", "how", "where", "when", "is ", "are ", "have ", "has ", "the ", "and "
    ]
    is_english = any(ind in f" {msg_lower} " for ind in english_indicators)
    system_prompt = SYSTEM_PROMPT_ES

    # Inyectar contexto de horario para que el AI sepa cómo cerrar
    if not business_hours:
        system_prompt += "\n\nHORARIO: FUERA DE OFICINA — Aplica el PASO 3 FUERA DE HORARIO. No digas que un asesor llamara ahora. Pide la hora preferida para manana."
    if is_english:
        system_prompt += "\n\nIMPORTANT: The client is writing in English. Respond in English."

    try:
        response = get_client().messages.create(
            model="claude-sonnet-4-5",  # Current model (June 2026)
            max_tokens=300,
            system=system_prompt,
            messages=messages
        )
        ai_text = response.content[0].text

        # Save to conversation history
        await save_conversation_message(contact_id, "user", user_message)
        await save_conversation_message(contact_id, "assistant", ai_text)

        # Detect intent from response
        intent = "general"
        preferred_time = ""
        wants_call_kw = [
            "llamar", "llamen", "call me", "call now", "ahora", "now",
            "inmediato", "quiero que me llamen",
        ]
        wants_appt_kw = [
            "cita", "appointment", "agendar", "schedule", "programar"
        ]
        if any(w in msg_lower for w in wants_call_kw):
            intent = "wants_call"
        elif any(w in msg_lower for w in wants_appt_kw):
            intent = "wants_appointment"
            preferred_time = user_message
        elif any(w in msg_lower for w in ["precio", "costo", "price", "cost", "cuanto"]):
            intent = "pricing"
        elif any(w in msg_lower for w in ["dental", "salud", "health", "auto", "vida", "life"]):
            intent = "product_interest"

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


FOLLOWUP_SYSTEM_PROMPT = """Eres el asistente virtual de Green Insurance, agencia de seguros en Georgia, USA.

Tu tarea: generar UN mensaje de seguimiento para un lead que no ha respondido.

REGLAS ESTRICTAS:
- Solo texto plano. Sin asteriscos, guiones, listas ni negritas.
- Maximo 2 oraciones.
- NUNCA repitas lo que ya dijiste en mensajes anteriores.
- Continua naturalmente el hilo de la conversacion.
- Si no hay historial, saluda y pregunta por disponibilidad.
- Termina siempre con una pregunta o invitacion a responder.
- NUNCA menciones precios ni presupuestos.
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
        prompt = (
            f"El lead lleva dias sin responder y esta en el stage '{stage_name}' del pipeline de seguro de {product}."
            f"{name_hint} Genera un mensaje de seguimiento corto, diferente a los anteriores, que continue el hilo natural de la conversacion."
        )
        messages.append({"role": "user", "content": prompt})

        response = get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system=system,
            messages=messages,
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"[FollowUp AI] Error generating followup for {contact_id}: {e}")
        return ""
