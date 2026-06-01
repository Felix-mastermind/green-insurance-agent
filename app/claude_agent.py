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

SYSTEM_PROMPT_ES = """Eres el asistente de Green Insurance, agencia de seguros en Georgia, USA.

FORMATO - OBLIGATORIO:
- Solo texto plano. Sin asteriscos, sin guiones, sin listas, sin negritas.
- Maximo 2 oraciones. Corto como WhatsApp.
- Una sola pregunta a la vez.
- Nunca JSON ni codigos.

IDIOMA: Responde en el mismo idioma del cliente (español o ingles).

TU UNICO TRABAJO - 3 pasos:
1. Saludar y preguntar que tipo de seguro necesita (dental, salud, auto, vida, comercial).
2. Preguntar para cuantas personas.
3. Decirle que un asesor lo va a contactar pronto con las opciones.

REGLAS IMPORTANTES:
- NUNCA preguntes por presupuesto ni dinero.
- NUNCA des precios ni valores. Ni aproximados.
- NUNCA des informacion tecnica de coberturas.
- Si el cliente ya dijo el tipo de seguro y cuantas personas, transfiere YA. No hagas mas preguntas.
- Si el cliente saluda y da info de una vez (ej: "quiero seguro dental para 2 personas"), transfiere directamente.
- Si el cliente pregunta precio, di solo: "Un asesor te dara esa informacion cuando te contacte."

CUANDO TRANSFERIR (inmediatamente):
- Cliente dio tipo de seguro (aunque sea solo eso).
- Cliente dice quiero comprar, cotizar, o hablar con alguien.
- Cliente ya respondio 2 preguntas del bot.

MENSAJE DE TRANSFERENCIA:
"Perfecto! Un asesor de Green Insurance te va a contactar muy pronto con toda la informacion. Gracias!"

Green Insurance - Marietta, Georgia. Seguros: Dental, Salud, Auto, Vida, Comercial."""

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

    # Transfer as soon as client mentions insurance type or intent to buy
    transfer_keywords = [
        # Insurance types mentioned = ready to transfer
        "dental", "salud", "health", "auto", "vida", "life", "comercial", "commercial",
        "accidente", "accident",
        # Explicit requests
        "asesor", "agente", "hablar", "llamar", "cotizar", "quote", "comprar", "buy",
        "want to talk", "speak with", "call me", "precio", "price", "cuanto", "how much",
        "informacion", "information", "ayuda", "help"
    ]
    should_transfer = any(kw in user_message.lower() for kw in transfer_keywords)

    # Also transfer if we've had 2+ exchanges (client already engaged)
    if len(history) >= 3:
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
