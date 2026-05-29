"""
Payment Reminder Module
Runs daily at 9am ET
Sends SMS + WhatsApp to contacts 3, 2, 1 days before payment day
"""
import os
from datetime import datetime
import pytz
from app.ghl_client import get_contacts_by_tag, send_sms, send_whatsapp, get_contact_custom_field
from app.supabase_client import check_reminder_sent, log_reminder_sent, log_message

# Custom field IDs
PAYMENT_DATE_FIELD = "yNa1CgeOzVqrBonymyhS"
EXPIRATION_DATE_FIELD = "QvkiNnmPfbbksTNAgY6u"

ET = pytz.timezone("America/New_York")

def get_payment_message(first_name: str, days_left: int, lang: str = "es") -> str:
    """Generate payment reminder message"""
    if lang == "en":
        if days_left == 3:
            return (f"Hi {first_name}! 🌿 Reminder: your insurance payment is due in 3 days. "
                    f"Please make sure to pay on time to keep your coverage active. "
                    f"Questions? Call us! - Green Insurance")
        elif days_left == 2:
            return (f"Hi {first_name} 👋 Your insurance payment is due in 2 days. "
                    f"Don't forget to make your payment to avoid interruptions. - Green Insurance")
        else:
            return (f"⚠️ Hi {first_name}, your insurance payment is due TOMORROW. "
                    f"Please pay today to keep your coverage active. - Green Insurance")
    else:
        if days_left == 3:
            return (f"Hola {first_name}! 🌿 Recordatorio: tu pago de seguro vence en 3 dias. "
                    f"Por favor realiza tu pago a tiempo para mantener tu cobertura activa. "
                    f"Preguntas? Llamanos! - Green Insurance Marietta")
        elif days_left == 2:
            return (f"Hola {first_name} 👋 Tu pago de seguro vence en 2 dias. "
                    f"No olvides realizar tu pago para evitar interrupciones en tu cobertura. "
                    f"- Green Insurance Marietta")
        else:
            return (f"Aviso {first_name}, tu pago de seguro vence MANANA. "
                    f"Por favor paga hoy para mantener tu cobertura activa. "
                    f"- Green Insurance Marietta")

async def run_payment_reminders():
    """Main payment reminder job - runs daily"""
    now = datetime.now(ET)
    today_day = now.day
    month_year = now.strftime("%Y-%m")

    print(f"[Payment Reminders] Running for {now.strftime('%Y-%m-%d')} | Today is day {today_day}")

    # Get all marietta contacts (and other tags as needed)
    tags_to_check = ["marietta", "active"]
    contacts = []
    seen_ids = set()

    for tag in tags_to_check:
        batch = await get_contacts_by_tag(tag)
        for c in batch:
            if c["id"] not in seen_ids:
                contacts.append(c)
                seen_ids.add(c["id"])

    print(f"[Payment Reminders] Found {len(contacts)} contacts to check")

    sent_count = 0
    skipped_count = 0

    for contact in contacts:
        try:
            # Get payment day number from custom field
            payment_day_str = await get_contact_custom_field(contact, PAYMENT_DATE_FIELD)
            if not payment_day_str:
                continue

            try:
                payment_day = int(payment_day_str.strip())
            except ValueError:
                continue

            # Check if today is 3, 2, or 1 day before payment day
            days_diff = payment_day - today_day
            if days_diff not in [1, 2, 3]:
                continue

            reminder_type = f"{days_diff}days"
            contact_id = contact["id"]
            first_name = contact.get("firstName", "Cliente")
            phone = contact.get("phone", "")
            full_name = f"{first_name} {contact.get('lastName', '')}".strip()

            # Check if already sent this month
            already_sent = await check_reminder_sent(contact_id, reminder_type, month_year)
            if already_sent:
                skipped_count += 1
                continue

            # Detect language (simple heuristic based on name/tags)
            lang = "es"
            contact_tags = [t.lower() for t in contact.get("tags", [])]
            if "english" in contact_tags or "en" in contact_tags:
                lang = "en"

            message = get_payment_message(first_name, days_diff, lang)

            # Send SMS
            sms_result = await send_sms(contact_id, message)
            sms_status = "sent" if sms_result.get("conversationId") else "failed"

            await log_message(contact_id, full_name, phone, "sms", "payment_reminder",
                              message, sms_status, {"days_before": days_diff, "payment_day": payment_day})

            # Send WhatsApp
            wa_result = await send_whatsapp(contact_id, message)
            wa_status = "sent" if wa_result.get("conversationId") else "failed"

            await log_message(contact_id, full_name, phone, "whatsapp", "payment_reminder",
                              message, wa_status, {"days_before": days_diff, "payment_day": payment_day})

            # Log reminder to avoid duplicates
            await log_reminder_sent(contact_id, full_name, payment_day, reminder_type, month_year)

            sent_count += 1
            print(f"[Payment Reminders] Sent {days_diff}-day reminder to {full_name} (day {payment_day})")

        except Exception as e:
            print(f"[Payment Reminders] Error processing contact {contact.get('id')}: {e}")

    print(f"[Payment Reminders] Done. Sent: {sent_count} | Skipped (already sent): {skipped_count}")
    return {"sent": sent_count, "skipped": skipped_count, "total_checked": len(contacts)}
