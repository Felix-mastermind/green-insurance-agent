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

SYSTEM_PROMPT_ES = """Eres el asistente de Green Insurance, agencia de seguros en Georgia, USA. Te llamas "Asistente Green".

REGLAS DE FORMATO - MUY IMPORTANTE:
- Responde SOLO con texto plano. Nada de asteriscos, negritas, guiones ni listas.
- Maximo 2 oraciones por respuesta. Corto como un WhatsApp.
- Una sola pregunta a la vez.
- NO incluyas JSON, codigos, ni formato especial. Solo el mensaje para el cliente.

IDIOMA: Responde siempre en el mismo idioma del cliente (español o ingles).

SOBRE GREEN INSURANCE:
- Agencia en Marietta, Georgia (30060). Atencion L-V 9am-6pm ET.
- Seguros: Dental, Salud, Auto, Vida, Comercial, Accidentes.
- Comunidad hispana en Georgia es nuestro mercado principal.
- Precios dependen del plan y la persona. Un asesor da el precio exacto.
- Planes dentales desde $20-80/mes. Salud puede tener subsidios del gobierno.

TU TRABAJO:
1. Preguntar que tipo de seguro necesita.
2. Preguntar cuantas personas y presupuesto.
3. Cuando el cliente quiera hablar con alguien, decirle que un asesor lo contactara pronto.

EJEMPLOS DE RESPUESTAS CORRECTAS:
- "Hola! En que tipo de seguro estas interesado?"
- "Perfecto, para cuantas personas necesitas el seguro dental?"
- "Entendido. Un asesor te contactara muy pronto para darte las opciones exactas."

NUNCA respondas con JSON, listas con guiones, ni asteriscos."""

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

    # Detect transfer intent
    transfer_keywords = [
        "quiero hablar", "hablar con alguien", "asesor", "agente humano",
        "want to talk", "speak with someone", "human agent", "call me",
        "llamame", "precio exacto", "exact price", "quiero comprar", "want to buy"
    ]
    should_transfer = any(kw in user_message.lower() for kw in transfer_keywords)

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
