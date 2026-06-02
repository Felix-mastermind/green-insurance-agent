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
  - Que tipo de cobertura necesitas: solo liability (lo minimo requerido) o full coverage (cobertura completa)?

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

PASO 4 - Transferir al asesor con toda la informacion recopilada.

REGLAS:
- NUNCA preguntes por presupuesto ni des precios.
- NUNCA des informacion tecnica de coberturas.
- Si el cliente ya dio todos los datos de su tipo de seguro, ve directo al paso 3.
- Si el cliente pide hablar con alguien ya, ve directo al paso 3.
- Si el cliente se va a ir sin dar info, di: "Entiendo! Si en algun momento necesitas ayuda con tu seguro, aqui estamos. Te puedo dejar el numero de nuestra oficina en Marietta: nos pueden llamar de L-V 11am-7pm."

Green Insurance - Marietta, GA 30060 | L-V 11am-7pm ET"""

async def get_ai_response(contact_id: str, user_message: str, contact_name: str = "") -> dict:
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

    try:
        response = get_client().messages.create(
            model="claude-sonnet-4-5",  # Current model (June 2026)
            max_tokens=300,
            system=SYSTEM_PROMPT_ES,
            messages=messages
        )
        ai_text = response.content[0].text

        # Save to conversation history
        await save_conversation_message(contact_id, "user", user_message)
        await save_conversation_message(contact_id, "assistant", ai_text)

        # Detect intent from response
        intent = "general"
        if any(w in user_message.lower() for w in ["cita", "appointment", "agendar", "schedule"]):
            intent = "appointment"
        elif any(w in user_message.lower() for w in ["precio", "costo", "price", "cost", "cuanto"]):
            intent = "pricing"
        elif any(w in user_message.lower() for w in ["dental", "salud", "health", "auto", "vida", "life"]):
            intent = "product_interest"

        return {
            "response": ai_text,
            "should_transfer": should_transfer,
            "intent": intent
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
            "intent": "error"
        }
