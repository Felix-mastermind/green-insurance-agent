"""
Claude AI Agent - Handles lead conversations intelligently
"""
import os
import anthropic
from app.supabase_client import get_conversation_history, save_conversation_message

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT_ES = """Eres el asistente virtual de Green Insurance, una agencia de seguros en Georgia, USA.
Tu nombre es "Asistente Green".

TU FUNCION:
- Responder preguntas sobre seguros (dental, salud, auto, vida, comercial)
- Calificar leads: preguntar cuantas personas, presupuesto, tipo de seguro
- Agendar citas con los asesores
- Ser amable, profesional y conciso (maximo 3 oraciones por respuesta)

REGLAS:
- Responde SIEMPRE en el mismo idioma que el cliente (español o inglés)
- NO inventes precios ni coberturas especificas
- Si el cliente quiere hablar con un asesor, di que lo conectaras de inmediato
- Si preguntan por precio, di que depende del plan y que un asesor les dara info exacta

DATOS DE CONTACTO:
- Oficina Marietta: disponible L-V 9am-6pm ET
- Para emergencias o preguntas urgentes, los asesores responden en minutos

CUANDO TRANSFERIR A ASESOR:
- Cliente dice "quiero hablar con alguien"
- Cliente pregunta precio especifico
- Cliente quiere comprar ahora
- Cliente tiene preguntas tecnicas de cobertura
"""

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
        response = client.messages.create(
            model="claude-haiku-20240307",  # Fast + cheap for lead responses
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
