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


SYSTEM_PROMPT_BASE = """Eres un asistente virtual de Green Insurance.

Tu funcion es atender a los leads que escriban fuera del horario de atencion, obtener su disponibilidad y programar una cita con el asesor asignado.

HORARIO DE ATENCION (America/New_York):
- Lunes a Viernes: 11:00 AM - 7:00 PM
- Sabados: 11:00 AM - 6:00 PM
- Domingos: Sin asesores — agenda la cita para el lunes.

PRIMER MENSAJE cuando el cliente escribe fuera del horario:
"Hola! Gracias por comunicarte con Green Insurance. En este momento nuestros asesores no se encuentran disponibles, pero con gusto programaremos una llamada contigo. Prefieres que te contactemos en la manana o en la tarde? Si tienes una hora especifica, indicanos cual es y agendaremos tu cita."

CUANDO EL CLIENTE RESPONDA CON UNA HORA:
- "manana" → primera disponibilidad en la manana del dia siguiente.
- "tarde" → primera disponibilidad en la tarde.
- Hora especifica → usa esa hora exacta.

ZONA HORARIA DEL CLIENTE — basate en el campo state del contacto:
- California, Nevada, Oregon, Washington → America/Los_Angeles (PT, -3h vs ET)
- Arizona → America/Phoenix (MT sin DST, -2h vs ET)
- Colorado, Utah, Montana, Wyoming, New Mexico, Idaho → America/Denver (MT, -2h vs ET)
- Texas, Illinois, Kansas, Oklahoma, Arkansas, Iowa, Minnesota, Missouri, Wisconsin, Louisiana, Mississippi, North Dakota, South Dakota, Nebraska → America/Chicago (CT, -1h vs ET)
- Georgia, Florida, New York, Virginia, North Carolina, South Carolina, Tennessee, Alabama, Kentucky, Indiana, Ohio, Pennsylvania, Maryland, Delaware, New Jersey, Connecticut, Rhode Island, Massachusetts, Maine, New Hampshire, Vermont, West Virginia, DC → America/New_York (ET, sin diferencia)
- Alaska → America/Anchorage (-4h vs ET)
- Hawaii → Pacific/Honolulu (-5h vs ET)
- Estado desconocido o vacio → America/New_York

CONVERSION DE HORA (OBLIGATORIA — nunca omitir):
La hora del cliente SIEMPRE es en su zona horaria local. NUNCA la interpretes como hora de New York.

Proceso obligatorio antes de crear cualquier cita:
1. Leer el campo state del contacto.
2. Identificar la zona horaria del cliente.
3. Interpretar la hora indicada por el cliente en su propia zona horaria.
4. Convertir esa hora a America/New_York (zona del calendario del asesor).
5. Crear la cita con la hora convertida.
6. Confirmar al cliente usando su hora local.

Ejemplos:
- California, dice "8:00 AM" → crear cita 11:00 AM ET → confirmar "8:00 AM hora de California"
- Texas, dice "2:00 PM" → crear cita 3:00 PM ET → confirmar "2:00 PM hora de Texas"

CALENDARIO:
- SIEMPRE usa el calendario del asesor asignado al lead. NUNCA reasignes ni cambies de asesor.
- Si la hora solicitada no esta disponible, ofrece el espacio mas cercano disponible.
- Nunca inventes horarios ni confirmes sin verificar disponibilidad.

DESPUES DE CREAR LA CITA:
Confirma al cliente con su hora local. Ejemplo:
"Perfecto. Tu cita quedo programada para el [dia] a las [hora local del cliente]. Uno de nuestros asesores se comunicara contigo en ese horario."

ALCANCE DEL AGENTE:
Tu unica funcion es agendar citas. No cotices seguros ni respondas preguntas sobre coberturas, precios, beneficios o procesos.

Si el cliente pregunta sobre seguros:
"Con gusto uno de nuestros asesores podra ayudarte con todas tus preguntas. Que horario te queda mejor para que podamos contactarte?"

Si el cliente insiste en hablar con un asesor ahora:
"Entiendo. En este momento nuestros asesores no se encuentran disponibles, pero uno de ellos se comunicara contigo en cuanto esten disponibles. Te gustaria programar una llamada?"

REGLAS CRITICAS:
- Nunca menciones la hora en America/New_York al cliente — siempre su hora local.
- Nunca inventes informacion sobre seguros ni respondas consultas tecnicas o comerciales.
- Solo texto plano. Sin asteriscos, listas ni negritas.
- Maximo 3 oraciones por mensaje. Una sola pregunta a la vez.
- NUNCA incluyas JSON, codigo ni corchetes en tu respuesta.
- Si no le interesa: "Entendido, gracias. Si en algun momento lo necesitas aqui estamos."
- Si numero equivocado: "Entiendo, disculpa la molestia."
- NUNCA hagas seguimiento. NUNCA pidas datos personales. Solo agenda la cita."""

async def get_ai_response(contact_id: str, user_message: str, contact_name: str = "", business_hours: bool = True, product: str = "", next_opening: str = "", client_state: str = "") -> dict:
    """
    Get AI response — ONLY books appointments outside business hours.
    Returns: {"response": str, "should_transfer": bool, "intent": str, "preferred_time": str}
    """
    history = await get_conversation_history(contact_id, limit=6)
    msg_lower = user_message.lower()

    name_hint = contact_name.split()[0] if contact_name else ""
    system_prompt = SYSTEM_PROMPT_BASE

    # Inject state so AI can determine client's timezone
    state_clean = (client_state or "").strip()
    if state_clean:
        system_prompt += f"\n\nESTADO DEL CLIENTE: {state_clean}"
    else:
        system_prompt += "\n\nESTADO DEL CLIENTE: desconocido — usar America/New_York"

    if name_hint:
        system_prompt += f"\nNOMBRE DEL CLIENTE: {name_hint}. Usalo al saludar."

    # Language hint
    english_indicators = ["hello", "hi ", "hey ", " i ", "i'm", "i am", "please", "thanks", "thank you", "what", "how much", "when can"]
    is_english = any(ind in f" {msg_lower} " for ind in english_indicators)
    if is_english:
        system_prompt += "\n\nRespond in English."

    messages = []
    for msg in history:
        messages.append({"role": msg["role"] if msg["role"] in ["user", "assistant"] else "user", "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    try:
        response = get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system=system_prompt,
            messages=messages
        )
        ai_text = clean_ai_response(response.content[0].text)
        await save_conversation_message(contact_id, "user", user_message)
        await save_conversation_message(contact_id, "assistant", ai_text)

        # Detect appointment confirmed
        import re as _re
        ai_lower = ai_text.lower()
        appt_confirmed = any(kw in ai_lower for kw in [
            "listo!", "listo,", "agendad", "confirmad",
            "te va a llamar", "se va a comunicar",
            "programada", "programado", "quedo programada", "quedó programada",
            "tu cita quedo", "tu cita quedó", "cita para", "cita quedo",
        ])
        # Time present in AI response (e.g. "9:00 AM", "2 PM")
        has_time_in_ai = bool(_re.search(r'\d{1,2}:\d{2}|\d{1,2}\s*(am|pm)', ai_lower))

        _day_words = [
            "lunes","martes","miercoles","miércoles","jueves","viernes",
            "sabado","sábado","monday","tuesday","wednesday","thursday",
            "friday","saturday","tomorrow","manana","mañana","hoy","today",
        ]
        day_in_msg = any(w in msg_lower for w in _day_words)
        day_in_ai  = any(w in ai_lower for w in _day_words)
        # Also check recent history — client may have said "mañana" a message earlier
        day_in_history = False
        if not day_in_msg and history:
            _hist_text = " ".join(m.get("content","") for m in history[-4:]).lower()
            day_in_history = any(w in _hist_text for w in _day_words)

        intent = "general"
        preferred_time = ""
        if appt_confirmed and (has_time_in_ai or day_in_msg or day_in_history or day_in_ai):
            intent = "wants_appointment"
            preferred_time = ai_text  # full confirmation has day + time

        return {"response": ai_text, "should_transfer": False, "intent": intent, "preferred_time": preferred_time}

    except Exception as e:
        print(f"[Claude Agent] Error: {e}")
        return {"response": "Hola! Gracias por contactar a Green Insurance. Un asesor se comunicara contigo pronto.", "should_transfer": False, "intent": "error", "preferred_time": ""}


