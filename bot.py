"""
WhatsApp Shopping List Bot
Uses: Twilio WhatsApp Sandbox + Flask + MongoDB Atlas
"""

from flask import Flask, request 
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from datetime import datetime, timezone
import os
from dotenv import load_dotenv
from db import (
    create_list, get_active_list, add_item, get_list_items,
    check_item, close_list, get_list_by_id, is_admin,
    get_all_active_lists, delete_item
)

load_dotenv()

app = Flask(__name__)

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

HELP_TEXT = """🛒 *בוט רשימת קניות*

*פקודות זמינות:*
📋 *רשימה חדשה* - צור רשימת קניות חדשה
📝 *הוסף [מוצר]* - הוסף מוצר לרשימה הפעילה
📋 *הצג רשימה* - הצג את הרשימה הנוכחית
✅ *סמן [מספר]* - סמן פריט כנקנה
❌ *מחק [מספר]* - מחק פריט מהרשימה (מנהל בלבד)
🔒 *סגור רשימה* - סגור את הרשימה (מנהל בלבד)
❓ *עזרה* - הצג הודעה זו"""


def send_whatsapp(to: str, body: str):
    """Send a WhatsApp message via Twilio"""
    try:
        client.messages.create(
            body=body,
            from_=TWILIO_WHATSAPP_NUMBER,
            to=to
        )
    except Exception as e:
        print(f"Error sending message: {e}")


def format_items_list(items: list) -> str:
    """Format items list for display"""
    if not items:
        return "📭 הרשימה ריקה"
    
    lines = []
    for i, item in enumerate(items, 1):
        status = "✅" if item.get("checked") else "⬜"
        added_by = item.get("added_by_name", "לא ידוע")
        lines.append(f"{status} {i}. {item['name']} _(הוסף על ידי {added_by})_")
    
    return "\n".join(lines)


def handle_new_list(sender: str, sender_name: str, message_parts: list) -> str:
    """Handle creating a new shopping list"""
    # Check if there's already an active list
    existing = get_active_list(sender)
    if existing:
        return (
            f"⚠️ כבר יש לך רשימה פעילה: *{existing['name']}*\n"
            f"סגור אותה קודם עם: *סגור רשימה*"
        )
    
    # Parse list name and expiry
    # Format: "רשימה חדשה [שם] עד [DD/MM HH:MM]"
    list_name = "רשימת קניות"
    expiry = None
    
    # Try to extract name
    text = " ".join(message_parts[2:]) if len(message_parts) > 2 else ""
    
    if "עד" in text:
        parts = text.split("עד")
        list_name = parts[0].strip() or "רשימת קניות"
        expiry_str = parts[1].strip()
        try:
            # Parse date/time like "25/12 18:00" or "25/12/2025 18:00"
            expiry = parse_expiry(expiry_str)
        except:
            return (
                "❌ פורמט תאריך שגוי.\n"
                "השתמש: *רשימה חדשה [שם] עד DD/MM HH:MM*\n"
                "לדוגמה: *רשימה חדשה חג עד 25/12 18:00*"
            )
    elif text:
        list_name = text.strip()
    
    list_id = create_list(
        admin_phone=sender,
        admin_name=sender_name,
        name=list_name,
        expiry=expiry
    )
    
    expiry_str = f"\n⏰ פתוחה עד: {expiry.strftime('%d/%m/%Y %H:%M')}" if expiry else ""
    
    return (
        f"✅ *רשימה חדשה נוצרה!*\n"
        f"📋 שם: *{list_name}*{expiry_str}\n"
        f"🆔 מזהה: `{list_id}`\n\n"
        f"שתף את המזהה עם חברים כדי שיוכלו להצטרף:\n"
        f"הצטרף לרשימה `{list_id}`\n\n"
        f"הוסף פריטים: *הוסף [מוצר]*"
    )


def handle_add_item(sender: str, sender_name: str, message_parts: list) -> str:
    """Handle adding an item to the active list"""
    if len(message_parts) < 2:
        return "❌ ציין מה להוסיף: *הוסף [מוצר]*\nלדוגמה: *הוסף חלב*"
    
    item_name = " ".join(message_parts[1:])
    
    # Find active list for this user (either as admin or member)
    active_list = get_active_list(sender)
    
    if not active_list:
        return (
            "❌ אין לך רשימה פעילה.\n"
            "צור רשימה: *רשימה חדשה*\n"
            "או הצטרף לרשימה: *הצטרף [מזהה]*"
        )
    
    # Check if list is expired
    if active_list.get("expiry"):
        expiry = active_list["expiry"]
        if datetime.now(timezone.utc) > expiry:
            return f"⏰ הרשימה *{active_list['name']}* פגה תוקף ואינה ניתנת לעריכה."
    
    success = add_item(
        list_id=str(active_list["_id"]),
        item_name=item_name,
        added_by=sender,
        added_by_name=sender_name
    )
    
    if success:
        items = get_list_items(str(active_list["_id"]))
        total = len(items)
        return (
            f"✅ *{item_name}* נוסף לרשימה *{active_list['name']}*\n"
            f"📊 סה\"כ {total} פריטים ברשימה"
        )
    return "❌ שגיאה בהוספת הפריט, נסה שוב."


def handle_show_list(sender: str) -> str:
    """Handle showing the active list"""
    active_list = get_active_list(sender)
    
    if not active_list:
        return (
            "❌ אין לך רשימה פעילה.\n"
            "צור רשימה: *רשימה חדשה*\n"
            "או הצטרף לרשימה: *הצטרף [מזהה]*"
        )
    
    items = get_list_items(str(active_list["_id"]))
    items_text = format_items_list(items)
    
    checked = sum(1 for i in items if i.get("checked"))
    total = len(items)
    
    expiry_str = ""
    if active_list.get("expiry"):
        expiry_str = f"\n⏰ פתוחה עד: {active_list['expiry'].strftime('%d/%m/%Y %H:%M')}"
    
    admin_str = "👑 אתה המנהל" if is_admin(str(active_list["_id"]), sender) else f"👤 מנהל: {active_list.get('admin_name', 'לא ידוע')}"
    
    return (
        f"📋 *{active_list['name']}*\n"
        f"{admin_str}{expiry_str}\n"
        f"✅ {checked}/{total} נקנו\n"
        f"{'─' * 20}\n"
        f"{items_text}\n"
        f"{'─' * 20}\n"
        f"💡 *הוסף [מוצר]* | *סמן [מספר]*"
    )


def handle_check_item(sender: str, message_parts: list) -> str:
    """Handle checking/unchecking an item"""
    if len(message_parts) < 2 or not message_parts[1].isdigit():
        return "❌ ציין מספר פריט: *סמן [מספר]*\nלדוגמה: *סמן 3*"
    
    item_num = int(message_parts[1])
    active_list = get_active_list(sender)
    
    if not active_list:
        return "❌ אין לך רשימה פעילה."
    
    # Only admin can check items
    if not is_admin(str(active_list["_id"]), sender):
        return "❌ רק המנהל יכול לסמן פריטים כנקנים."
    
    items = get_list_items(str(active_list["_id"]))
    
    if item_num < 1 or item_num > len(items):
        return f"❌ מספר פריט לא תקין. יש {len(items)} פריטים ברשימה."
    
    item = items[item_num - 1]
    new_status = not item.get("checked", False)
    
    success = check_item(str(active_list["_id"]), str(item["_id"]), new_status)
    
    if success:
        status_text = "נקנה ✅" if new_status else "לא נקנה ⬜"
        return f"{'✅' if new_status else '↩️'} *{item['name']}* סומן כ{status_text}"
    return "❌ שגיאה בסימון הפריט."


def handle_close_list(sender: str) -> str:
    """Handle closing the active list"""
    active_list = get_active_list(sender)
    
    if not active_list:
        return "❌ אין לך רשימה פעילה לסגור."
    
    if not is_admin(str(active_list["_id"]), sender):
        return "❌ רק המנהל יכול לסגור את הרשימה."
    
    items = get_list_items(str(active_list["_id"]))
    checked = sum(1 for i in items if i.get("checked"))
    total = len(items)
    
    success = close_list(str(active_list["_id"]))
    
    if success:
        items_text = format_items_list(items)
        return (
            f"🔒 *הרשימה '{active_list['name']}' נסגרה!*\n"
            f"📊 סיכום: {checked}/{total} פריטים נקנו\n"
            f"{'─' * 20}\n"
            f"{items_text}"
        )
    return "❌ שגיאה בסגירת הרשימה."


def handle_join_list(sender: str, sender_name: str, message_parts: list) -> str:
    """Handle joining an existing list"""
    if len(message_parts) < 2:
        return "❌ ציין מזהה רשימה: *הצטרף [מזהה]*"
    
    list_id = message_parts[1].strip()
    
    from db import join_list
    result = join_list(list_id, sender, sender_name)
    
    if result == "not_found":
        return f"❌ רשימה עם מזהה `{list_id}` לא נמצאה."
    elif result == "closed":
        return f"❌ הרשימה סגורה ואי אפשר להצטרף אליה."
    elif result == "already_member":
        lst = get_list_by_id(list_id)
        return f"ℹ️ אתה כבר חבר ברשימה *{lst['name']}*"
    elif result == "success":
        lst = get_list_by_id(list_id)
        items = get_list_items(list_id)
        items_text = format_items_list(items)
        return (
            f"✅ הצטרפת לרשימה *{lst['name']}*!\n"
            f"👑 מנהל: {lst.get('admin_name', 'לא ידוע')}\n"
            f"{'─' * 20}\n"
            f"{items_text}\n"
            f"{'─' * 20}\n"
            f"הוסף פריטים: *הוסף [מוצר]*"
        )
    return "❌ שגיאה בהצטרפות לרשימה."


def handle_delete_item(sender: str, message_parts: list) -> str:
    """Handle deleting an item (admin only)"""
    if len(message_parts) < 2 or not message_parts[1].isdigit():
        return "❌ ציין מספר פריט: *מחק [מספר]*"
    
    item_num = int(message_parts[1])
    active_list = get_active_list(sender)
    
    if not active_list:
        return "❌ אין לך רשימה פעילה."
    
    if not is_admin(str(active_list["_id"]), sender):
        return "❌ רק המנהל יכול למחוק פריטים."
    
    items = get_list_items(str(active_list["_id"]))
    
    if item_num < 1 or item_num > len(items):
        return f"❌ מספר לא תקין. יש {len(items)} פריטים."
    
    item = items[item_num - 1]
    success = delete_item(str(active_list["_id"]), str(item["_id"]))
    
    if success:
        return f"🗑️ *{item['name']}* נמחק מהרשימה."
    return "❌ שגיאה במחיקת הפריט."


def parse_expiry(expiry_str: str) -> datetime:
    """Parse expiry string like '25/12 18:00' or '25/12/2025 18:00'"""
    from dateutil import parser as date_parser
    now = datetime.now(timezone.utc)
    
    # Try various formats
    formats = [
        "%d/%m %H:%M",
        "%d/%m/%Y %H:%M",
        "%d.%m %H:%M",
        "%d.%m.%Y %H:%M",
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(expiry_str.strip(), fmt)
            # If no year, assume current year (or next year if date passed)
            if dt.year == 1900:
                dt = dt.replace(year=now.year)
                if dt.replace(tzinfo=timezone.utc) < now:
                    dt = dt.replace(year=now.year + 1)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    
    raise ValueError(f"Cannot parse date: {expiry_str}")


def process_message(sender: str, sender_name: str, body: str) -> str:
    """Main message processing logic"""
    body = body.strip()
    parts = body.split()
    
    if not parts:
        return HELP_TEXT
    
    cmd = parts[0].lower()
    
    # Command routing
    if cmd in ["עזרה", "help", "הצג", "?", "/?", "/help"]:
        return HELP_TEXT
    
    elif cmd == "רשימה" and len(parts) > 1 and parts[1] == "חדשה":
        return handle_new_list(sender, sender_name, parts)
    
    elif cmd == "הוסף":
        return handle_add_item(sender, sender_name, parts)
    
    elif cmd in ["הצג"] or body in ["הצג רשימה", "רשימה", "show"]:
        return handle_show_list(sender)
    
    elif cmd == "סמן":
        return handle_check_item(sender, parts)
    
    elif cmd in ["סגור"] or body == "סגור רשימה":
        return handle_close_list(sender)
    
    elif cmd == "הצטרף":
        return handle_join_list(sender, sender_name, parts)
    
    elif cmd == "מחק":
        return handle_delete_item(sender, parts)
    
    else:
        # Try to detect intent
        if "רשימה" in body and "חדשה" in body:
            return handle_new_list(sender, sender_name, parts)
        return (
            f"❓ לא הבנתי את הפקודה: *{body}*\n\n"
            f"הקלד *עזרה* לרשימת פקודות"
        )


@app.route("/webhook", methods=["POST"])
def webhook():
    """Twilio WhatsApp webhook endpoint"""
    sender = request.form.get("From", "")
    body = request.form.get("Body", "")
    profile_name = request.form.get("ProfileName", sender)
    
    print(f"📩 From: {sender} | Name: {profile_name} | Message: {body}")
    
    response_text = process_message(sender, profile_name, body)
    
    resp = MessagingResponse()
    resp.message(response_text)
    
    return str(resp)


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "bot": "WhatsApp Shopping List Bot"}, 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"🚀 Bot running on port {port}")
    print(f"📱 Webhook URL: http://localhost:{port}/webhook")
    app.run(debug=True, host="0.0.0.0", port=port)
