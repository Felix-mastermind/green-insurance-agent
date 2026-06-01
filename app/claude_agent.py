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

FORMATO OBLIGATORIO:
- Maximo 2-3 oraciones por respuesta. Corto y directo.
- NUNCA uses asteriscos, guiones, negritas ni listas. Solo texto plano.
- Escribe como si fuera un mensaje de WhatsApp natural, no un documento.

IDIOMA: Responde siempre en el mismo idioma del cliente (español o ingles).

SOBRE GREEN INSURANCE:
- Agencia en Marietta, Georgia (30060). Oficina: L-V 9am-6pm ET.
- Seguros disponibles: Dental, Salud (Health), Auto, Vida (Life), Comercial, Accidentes.
- Servimos principalmente a la comunidad hispana en Georgia.
- Los precios varian segun el plan, edad y numero de personas. Un asesor da el precio exacto.
- Planes dentales desde aproximadamente $20-80/mes segun cobertura.
- Planes de salud dependen del ingreso familiar (pueden aplicar subsidios del gobierno).

TU TRABAJO:
1. Identificar que tipo de seguro necesita el cliente.
2. Preguntar cuantas personas y presupuesto aproximado.
3. Conectar con un asesor cuando el cliente quiera comprar o sepa lo que quiere.

CUANDO TRANSFERIR A ASESOR (should_transfer = true):
- Cliente quiere precio exacto o quiere comprar
- Cliente quiere hablar con alguien
- Cliente ya dio tipo de seguro + numero de personas + presupuesto

IMPORTANTE: Haz UNA sola pregunta a la vez. No hagas listas de preguntas."""

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
