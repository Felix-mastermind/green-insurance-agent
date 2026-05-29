"""
Renewal Reminder Module
Runs daily at 10am ET
Sends SMS + WhatsApp 15 days before policy expiration
"""
from datetime import datetime, timedelta
import pytz
from app.ghl_client import get_contacts_by_tag, send_sms, send_whatsapp, get_contact_custom_field
from app.supabase_client import log_message, check_reminder_sent, log_reminder_sent

EXPIRATION_DATE_FIELD = "QvkiNnmPfbbksTNAgY6u"
ET = pytz.timezone("America/New_York")

def parse_date(date_str: str):
    """Parse date from various formats"""
    for fmt in ["%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y"]:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None

def get_renewal_message(first_name: str, expiration_date: str, days_left: int, lang: str = "es") -> str:
    if lang == "en":
        return (f"Hi {first_name}! 🌿 Your insurance policy expires in {days_left} days ({expiration_date}). "
                f"Contact us now to renew and keep your coverage without interruption. "
                f"We're here to help! - Green Insurance")
    else:
        return (f"Hola {first_name}! 🌿 Tu poliza de seguro vence en {days_left} dias ({expiration_date}). "
                f"Contactanos ahora para renovarla y mantener tu cobertura sin interrupciones. "
                f"Estamos para ayudarte! - Green Insurance Marietta")

async def run_renewal_reminders():
    """Main renewal reminder job - runs daily"""
    now = datetime.now(ET)
    target_date = now + timedelta(days=15)
    month_year = now.strftime("%Y-%m")

    print(f"[Renewal Reminders] Running for {now.strftime('%Y-%m-%d')} | Checking expirations on {target_date.strftime('%Y-%m-%d')}")

    # Get contacts with term-6 and term-12 tags
    contacts = []
    seen_ids = set()
    for tag in ["term-6", "term-12", "marietta"]:
        batch = await get_contacts_by_tag(tag)
        for c in batch:
            if c["id"] not in seen_ids:
                contacts.append(c)
                seen_ids.add(c["id"])

    print(f"[Renewal Reminders] Found {len(contacts)} contacts to check")

    sent_count = 0

    for contact in contacts:
        try:
            exp_str = await get_contact_custom_field(contact, EXPIRATION_DATE_FIELD)
            if not exp_str:
                continue

            exp_date = parse_date(exp_str)
            if not exp_date:
                continue

            days_left = (exp_date - now.replace(tzinfo=None)).days
            if days_left != 15:
                continue

            contact_id = contact["id"]
            first_name = contact.get("firstName", "Cliente")
            phone = contact.get("phone", "")
            full_name = f"{first_name} {contact.get('lastName', '')}".strip()

            # Avoid duplicate
            already_sent = await check_reminder_sent(contact_id, "renewal_15days", month_year)
            if already_sent:
                continue

            lang = "es"
            if "english" in [t.lower() for t in contact.get("tags", [])]:
                lang = "en"

            message = get_renewal_message(first_name, exp_str, days_left, lang)

            # Send SMS + WhatsApp
            sms_r = await send_sms(contact_id, message)
            await log_message(contact_id, full_name, phone, "sms", "renewal_reminder",
                              message, "sent" if sms_r.get("conversationId") else "failed",
                              {"days_left": days_left})

            wa_r = await send_whatsapp(contact_id, message)
            await log_message(contact_id, full_name, phone, "whatsapp", "renewal_reminder",
                              message, "sent" if wa_r.get("conversationId") else "failed",
                              {"days_left": days_left})

            await log_reminder_sent(contact_id, full_name, 0, "renewal_15days", month_year)
            sent_count += 1
            print(f"[Renewal Reminders] Sent renewal reminder to {full_name} | Expires: {exp_str}")

        except Exception as e:
            print(f"[Renewal Reminders] Error processing {contact.get('id')}: {e}")

    print(f"[Renewal Reminders] Done. Sent: {sent_count}")
    return {"sent": sent_count}
