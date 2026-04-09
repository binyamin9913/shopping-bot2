"""
WhatsApp Shopping List Bot - v2 with Interactive Buttons
Uses: Twilio WhatsApp Sandbox + Flask + MongoDB Atlas
"""

from flask import Flask, request
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime, timezone
import os
from dotenv import load_dotenv
from db import (
    create_list, get_active_list, add_item, get_list_items,
    check_item, close_list, get_list_by_id, is_admin, delete_item,
    join_list, get_pending_action, set_pending_action, clear_pending_action,
    create_invite_token, get_list_by_token
)

load_dotenv()

app = Flask(__name__)

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER      = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
BOT_URL            = os.getenv("BOT_URL", "").rstrip("/")   # e.g. https://my-bot.onrender.com
# Twilio sandbox number digits only (for wa.me links)
SANDBOX_NUMBER     = TWILIO_NUMBER.replace("whatsapp:", "").replace("+", "").strip()

client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def make_invite_message(list_name: str, token: str, admin_name: str) -> str:
    """
    Build the invite message the admin forwards to friends.
    Two methods:
      1. wa.me deep-link  → opens WhatsApp with pre-filled message (works on mobile)
      2. Fallback: just type the token
    """
    pre_filled = f"הצטרף {token}"
    from urllib.parse import quote
    wa_link = f"https://wa.me/{SANDBOX_NUMBER}?text={quote(pre_filled)}"

    # If BOT_URL is set, also provide a nicer landing page link
    if BOT_URL:
        join_url = f"{BOT_URL}/join/{token}"
        link_line = f"🔗 {join_url}"
    else:
        link_line = f"📱 {wa_link}"

    lines = [
        f"🛒 *הוזמנת לרשימת קניות!*",
        f"📋 שם הרשימה: *{list_name}*",
        f"👑 יצר: {admin_name}",
        f"",
        f"👇 *לחץ להצטרפות:*",
        link_line,
        f"",
        f"_או שלח את הקוד_ *{token}* _ישירות לבוט_",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
#  SEND HELPERS
# ─────────────────────────────────────────────

def send_msg(to: str, body: str):
    """Plain text message"""
    client.messages.create(body=body, from_=TWILIO_NUMBER, to=to)


def send_buttons(to: str, body: str, buttons: list[dict]):
    """
    Send interactive button message via Twilio Content API.
    buttons = [{"id": "...", "title": "..."}]  (max 3 per message)
    Falls back to numbered text menu if buttons fail.
    """
    try:
        # Build quick-reply content template on the fly
        actions = [{"type": "QUICK_REPLY", "title": b["title"], "id": b["id"]} for b in buttons[:3]]
        content = client.content.v1.content_and_approvals.create(
            friendly_name=f"btn_{datetime.now().timestamp()}",
            types={
                "twilio/quick-reply": {
                    "body": body,
                    "actions": actions
                }
            }
        )
        client.messages.create(
            from_=TWILIO_NUMBER,
            to=to,
            content_sid=content.sid
        )
    except Exception:
        # Fallback: numbered text menu
        lines = [body, ""]
        for i, b in enumerate(buttons, 1):
            lines.append(f"{i}️⃣ {b['title']}")
        lines.append("\n_(השב עם המספר לבחירה)_")
        client.messages.create(body="\n".join(lines), from_=TWILIO_NUMBER, to=to)


def send_list_buttons(to: str, body: str, sections: list[dict]):
    """
    Send a list picker message (up to 10 items).
    sections = [{"title": "...", "rows": [{"id":"...", "title":"...", "description":"..."}]}]
    Falls back to numbered text on failure.
    """
    try:
        all_rows = []
        for sec in sections:
            all_rows.extend(sec.get("rows", []))
        actions = [
            {"type": "QUICK_REPLY", "title": r["title"][:20], "id": r["id"]}
            for r in all_rows[:3]
        ]
        content = client.content.v1.content_and_approvals.create(
            friendly_name=f"list_{datetime.now().timestamp()}",
            types={
                "twilio/quick-reply": {
                    "body": body,
                    "actions": actions
                }
            }
        )
        client.messages.create(from_=TWILIO_NUMBER, to=to, content_sid=content.sid)
    except Exception:
        # Fallback numbered list
        lines = [body, ""]
        idx = 1
        for sec in sections:
            if sec.get("title"):
                lines.append(f"*{sec['title']}*")
            for row in sec.get("rows", []):
                desc = f" — {row['description']}" if row.get("description") else ""
                lines.append(f"{idx}. {row['title']}{desc}")
                idx += 1
        lines.append("\n_(השב עם המספר לבחירה)_")
        client.messages.create(body="\n".join(lines), from_=TWILIO_NUMBER, to=to)


# ─────────────────────────────────────────────
#  MENUS
# ─────────────────────────────────────────────

def send_main_menu(to: str, name: str = ""):
    active = get_active_list(to)
    greeting = f"שלום {name}! 👋\n\n" if name else ""

    if active:
        items    = get_list_items(str(active["_id"]))
        checked  = sum(1 for i in items if i.get("checked"))
        body = (
            f"{greeting}"
            f"🛒 *רשימת קניות פעילה:* {active['name']}\n"
            f"📊 {checked}/{len(items)} פריטים נקנו\n\n"
            f"מה תרצה לעשות?"
        )
        buttons = [
            {"id": "show_list",   "title": "📋 הצג רשימה"},
            {"id": "add_item",    "title": "➕ הוסף מוצר"},
        ]
        if is_admin(str(active["_id"]), to):
            buttons.append({"id": "admin_menu", "title": "⚙️ ניהול"})
        else:
            buttons.append({"id": "share_list", "title": "🔗 שתף רשימה"})
    else:
        body = (
            f"{greeting}"
            f"🛒 *בוט רשימת קניות*\n\n"
            f"אין לך רשימה פעילה כרגע.\n"
            f"מה תרצה לעשות?"
        )
        buttons = [
            {"id": "new_list",  "title": "✨ רשימה חדשה"},
            {"id": "join_list", "title": "🔗 הצטרף לרשימה"},
        ]

    send_buttons(to, body, buttons)


def send_admin_menu(to: str, list_name: str):
    body = f"⚙️ *ניהול רשימה: {list_name}*\n\nבחר פעולה:"
    buttons = [
        {"id": "check_items", "title": "✅ סמן פריטים"},
        {"id": "share_list",  "title": "🔗 שתף רשימה"},
        {"id": "close_list",  "title": "🔒 סגור רשימה"},
    ]
    send_buttons(to, body, buttons)


def send_items_for_checking(to: str):
    """Show items as numbered list for admin to check/uncheck"""
    active = get_active_list(to)
    if not active:
        send_msg(to, "❌ אין רשימה פעילה.")
        return

    items = get_list_items(str(active["_id"]))
    if not items:
        send_msg(to, "📭 הרשימה ריקה — אין מה לסמן.")
        return

    lines = [f"📋 *{active['name']}* — בחר פריט לסימון:\n"]
    for i, item in enumerate(items, 1):
        icon = "✅" if item.get("checked") else "⬜"
        lines.append(f"{icon} *{i}.* {item['name']}")

    lines.append("\n_שלח מספר לסימון/ביטול סימון (לדוגמה: *3*)_")
    lines.append("_שלח *0* לחזרה לתפריט_")

    send_msg(to, "\n".join(lines))
    set_pending_action(to, {"action": "awaiting_check_number", "list_id": str(active["_id"])})


def send_full_list(to: str):
    """Display the full shopping list beautifully"""
    active = get_active_list(to)
    if not active:
        send_main_menu(to)
        return

    items   = get_list_items(str(active["_id"]))
    checked = sum(1 for i in items if i.get("checked"))
    total   = len(items)

    # Progress bar
    if total > 0:
        filled = round((checked / total) * 10)
        bar = "🟩" * filled + "⬜" * (10 - filled)
    else:
        bar = "⬜" * 10

    admin_tag = "👑 מנהל" if is_admin(str(active["_id"]), to) else "👤 חבר"
    expiry_str = ""
    if active.get("expiry"):
        expiry_str = f"\n⏰ פתוחה עד {active['expiry'].strftime('%d/%m %H:%M')}"

    lines = [
        f"🛒 *{active['name']}*",
        f"{admin_tag}{expiry_str}",
        f"{bar} {checked}/{total}",
        "─────────────────────",
    ]

    if not items:
        lines.append("📭 הרשימה ריקה")
    else:
        for i, item in enumerate(items, 1):
            if item.get("checked"):
                lines.append(f"✅ ~{i}. {item['name']}~")
            else:
                lines.append(f"⬜ {i}. {item['name']}")
            added = item.get("added_by_name", "")
            if added:
                lines.append(f"   _↳ {added}_")

    lines.append("─────────────────────")

    send_msg(to, "\n".join(lines))

    # Follow-up buttons
    buttons = [{"id": "add_item", "title": "➕ הוסף מוצר"}]
    if is_admin(str(active["_id"]), to):
        buttons.append({"id": "check_items", "title": "✅ סמן פריטים"})
    buttons.append({"id": "back_main", "title": "🏠 תפריט ראשי"})

    send_buttons(to, "מה עוד?", buttons)


# ─────────────────────────────────────────────
#  ACTION HANDLERS
# ─────────────────────────────────────────────

def handle_button(to: str, name: str, button_id: str):
    """Route button presses"""

    if button_id == "show_list":
        send_full_list(to)

    elif button_id == "add_item":
        active = get_active_list(to)
        if not active:
            send_msg(to, "❌ אין רשימה פעילה.")
            send_main_menu(to)
            return
        send_msg(to, "✏️ *הוספת מוצר*\n\nשלח את שם המוצר (ניתן לשלוח כמה מוצרים בשורות נפרדות)\n\n_שלח *ביטול* לחזרה_")
        set_pending_action(to, {"action": "awaiting_item_name", "list_id": str(active["_id"])})

    elif button_id == "new_list":
        existing = get_active_list(to)
        if existing:
            send_msg(to, f"⚠️ כבר יש לך רשימה פעילה: *{existing['name']}*\nסגור אותה קודם.")
            send_main_menu(to)
            return
        send_msg(to,
            "✨ *רשימה חדשה*\n\n"
            "שלח את שם הרשימה.\n"
            "אפשר גם להוסיף תאריך סגירה:\n"
            "_קניות שישי עד 25/04 20:00_\n\n"
            "_שלח *ביטול* לחזרה_"
        )
        set_pending_action(to, {"action": "awaiting_list_name"})

    elif button_id == "share_list":
        active = get_active_list(to)
        if not active:
            send_msg(to, "❌ אין רשימה פעילה לשיתוף.")
            send_main_menu(to)
            return
        token   = create_invite_token(str(active["_id"]))
        msg     = make_invite_message(active["name"], token, active.get("admin_name", ""))
        send_msg(to,
            f"📤 *העתק והעבר את ההודעה הבאה לחברים:*\n"
            f"{'─' * 30}\n"
            f"{msg}\n"
            f"{'─' * 30}\n"
            f"_הקוד: *{token}* — פשוט לשלוח אותו לבוט_"
        )
        send_buttons(to, "מה עוד?", [
            {"id": "show_list", "title": "📋 הצג רשימה"},
            {"id": "back_main", "title": "🏠 תפריט ראשי"},
        ])

    elif button_id == "join_list":
        send_msg(to,
            "🔗 *הצטרפות לרשימה*\n\n"
            "שלח את *קוד ההצטרפות* בן 6 תווים שקיבלת\n"
            "לדוגמה: *AB3X9K*\n\n"
            "_שלח *ביטול* לחזרה_"
        )
        set_pending_action(to, {"action": "awaiting_list_id"})

    elif button_id == "admin_menu":
        active = get_active_list(to)
        if active and is_admin(str(active["_id"]), to):
            send_admin_menu(to, active["name"])
        else:
            send_msg(to, "❌ אין לך הרשאות מנהל.")

    elif button_id == "check_items":
        if not is_admin_active(to):
            send_msg(to, "❌ רק המנהל יכול לסמן פריטים.")
            return
        send_items_for_checking(to)

    elif button_id == "close_list":
        active = get_active_list(to)
        if not active or not is_admin(str(active["_id"]), to):
            send_msg(to, "❌ אין לך הרשאות לסגור את הרשימה.")
            return
        send_buttons(
            to,
            f"🔒 לסגור את הרשימה *{active['name']}*?\nפעולה זו לא ניתנת לביטול.",
            [
                {"id": "confirm_close", "title": "✅ כן, סגור"},
                {"id": "back_main",     "title": "❌ ביטול"},
            ]
        )

    elif button_id == "confirm_close":
        active = get_active_list(to)
        if not active or not is_admin(str(active["_id"]), to):
            send_msg(to, "❌ שגיאה בסגירת הרשימה.")
            return
        items   = get_list_items(str(active["_id"]))
        checked = sum(1 for i in items if i.get("checked"))
        close_list(str(active["_id"]))
        send_msg(to,
            f"🔒 *הרשימה '{active['name']}' נסגרה!*\n"
            f"📊 סיכום: {checked}/{len(items)} פריטים נקנו\n\n"
            f"תודה! 🙏"
        )
        send_main_menu(to)

    elif button_id == "back_main":
        clear_pending_action(to)
        send_main_menu(to, name)

    else:
        send_main_menu(to, name)


def is_admin_active(phone: str) -> bool:
    active = get_active_list(phone)
    return bool(active and is_admin(str(active["_id"]), phone))


def handle_pending(to: str, name: str, body: str, pending: dict) -> bool:
    """
    Handle messages when a pending action is set.
    Returns True if handled, False if should fall through.
    """
    action = pending.get("action")

    if body.strip() in ["ביטול", "cancel", "0"] and action != "awaiting_check_number":
        clear_pending_action(to)
        send_main_menu(to, name)
        return True

    # ── waiting for new list name ──
    if action == "awaiting_list_name":
        clear_pending_action(to)
        text     = body.strip()
        list_name = "רשימת קניות"
        expiry    = None

        if "עד" in text:
            parts = text.split("עד", 1)
            list_name = parts[0].strip() or "רשימת קניות"
            try:
                expiry = parse_expiry(parts[1].strip())
            except Exception:
                send_msg(to, "❌ פורמט תאריך שגוי. נסה: *25/04 20:00*")
                return True
        elif text:
            list_name = text

        list_id = create_list(admin_phone=to, admin_name=name, name=list_name, expiry=expiry)
        expiry_str = f"\n⏰ פתוחה עד {expiry.strftime('%d/%m/%Y %H:%M')}" if expiry else ""

        send_msg(to,
            f"✅ *רשימה נוצרה בהצלחה!*\n"
            f"📋 שם: *{list_name}*{expiry_str}\n\n"
            f"🔗 *מזהה לשיתוף:*\n`{list_id}`\n\n"
            f"שלח מזהה זה לחברים כדי שיוכלו להצטרף."
        )
        send_main_menu(to)
        return True

    # ── waiting for list ID / token to join ──
    elif action == "awaiting_list_id":
        clear_pending_action(to)
        raw = body.strip()

        # Try short token first (6 chars), then fall back to full ObjectId
        lst_via_token = get_list_by_token(raw)
        if lst_via_token:
            list_id = str(lst_via_token["_id"])
        else:
            list_id = raw   # might be a full ObjectId

        result = join_list(list_id, to, name)

        if result == "not_found":
            send_msg(to,
                f"❌ קוד *{raw}* לא נמצא.\n"
                f"בדוק שהעתקת נכון ונסה שוב.\n\n"
                f"_שלח *הצטרף* לנסות שוב_"
            )
        elif result == "closed":
            send_msg(to, "❌ הרשימה סגורה ולא ניתן להצטרף אליה.")
        elif result == "already_member":
            lst = get_list_by_id(list_id) or lst_via_token
            send_msg(to, f"ℹ️ אתה כבר חבר ברשימה *{lst['name']}* 👍")
        elif result == "success":
            lst   = get_list_by_id(list_id) or lst_via_token
            items = get_list_items(list_id)
            send_msg(to,
                f"🎉 *ברוך הבא לרשימה {lst['name']}!*\n"
                f"👑 מנהל: {lst.get('admin_name', 'לא ידוע')}\n"
                f"📦 {len(items)} פריטים ברשימה\n\n"
                f"עכשיו תוכל להוסיף מוצרים!"
            )
        send_main_menu(to)
        return True

    # ── waiting for item name to add ──
    elif action == "awaiting_item_name":
        list_id = pending.get("list_id")
        if body.strip().lower() in ["ביטול", "cancel"]:
            clear_pending_action(to)
            send_main_menu(to, name)
            return True

        # Support multi-line items
        item_lines = [l.strip() for l in body.strip().splitlines() if l.strip()]
        added = []
        for item_name in item_lines:
            if add_item(list_id=list_id, item_name=item_name, added_by=to, added_by_name=name):
                added.append(item_name)

        clear_pending_action(to)

        if added:
            items_str = "\n".join(f"  • {i}" for i in added)
            all_items = get_list_items(list_id)
            send_msg(to,
                f"✅ *{len(added)} מוצר/ים נוספו:*\n{items_str}\n\n"
                f"📊 סה\"כ {len(all_items)} פריטים ברשימה"
            )
        else:
            send_msg(to, "❌ שגיאה בהוספת המוצרים, נסה שוב.")

        send_buttons(to, "מה עוד?", [
            {"id": "add_item",  "title": "➕ הוסף עוד"},
            {"id": "show_list", "title": "📋 הצג רשימה"},
            {"id": "back_main", "title": "🏠 תפריט ראשי"},
        ])
        return True

    # ── waiting for check number ──
    elif action == "awaiting_check_number":
        if body.strip() == "0":
            clear_pending_action(to)
            send_main_menu(to, name)
            return True

        if not body.strip().isdigit():
            send_msg(to, "❌ שלח מספר בלבד (לדוגמה: *3*) או *0* לחזרה")
            return True

        item_num = int(body.strip())
        list_id  = pending.get("list_id")
        items    = get_list_items(list_id)

        if item_num < 1 or item_num > len(items):
            send_msg(to, f"❌ מספר לא תקין. יש {len(items)} פריטים. נסה שוב:")
            return True

        item       = items[item_num - 1]
        new_status = not item.get("checked", False)
        check_item(list_id, str(item["_id"]), new_status)

        if new_status:
            status_icon = "✅"
            status_text = "סומן כנקנה"
        else:
            status_icon = "↩️"
            status_text = "סימון בוטל"

        send_msg(to, f"{status_icon} *{item['name']}* — {status_text}")

        # Refresh the item list
        updated_items = get_list_items(list_id)
        lines = ["📋 *עדכון רשימה:*\n"]
        for i, it in enumerate(updated_items, 1):
            icon = "✅" if it.get("checked") else "⬜"
            if it.get("checked"):
                lines.append(f"{icon} ~{i}. {it['name']}~")
            else:
                lines.append(f"{icon} {i}. {it['name']}")
        lines.append("\n_שלח מספר לסימון נוסף, או *0* לתפריט_")
        send_msg(to, "\n".join(lines))
        return True

    return False


# ─────────────────────────────────────────────
#  PARSE EXPIRY
# ─────────────────────────────────────────────

def parse_expiry(expiry_str: str) -> datetime:
    now = datetime.now(timezone.utc)
    formats = ["%d/%m %H:%M", "%d/%m/%Y %H:%M", "%d.%m %H:%M", "%d.%m.%Y %H:%M"]
    for fmt in formats:
        try:
            dt = datetime.strptime(expiry_str.strip(), fmt)
            if dt.year == 1900:
                dt = dt.replace(year=now.year)
                if dt.replace(tzinfo=timezone.utc) < now:
                    dt = dt.replace(year=now.year + 1)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse: {expiry_str}")


# ─────────────────────────────────────────────
#  WEBHOOK
# ─────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    sender  = request.form.get("From", "")
    body    = request.form.get("Body", "").strip()
    name    = request.form.get("ProfileName", "")
    btn_id  = request.form.get("ButtonPayload", "").strip()   # interactive button press

    print(f"📩 {sender} | btn={btn_id!r} | body={body!r}")

    # Empty TwiML response — we send messages proactively via REST API
    resp = MessagingResponse()

    try:
        # 1. Button press
        if btn_id:
            handle_button(sender, name, btn_id)
            return str(resp)

        # 2. Pending action (multi-step flow)
        pending = get_pending_action(sender)
        if pending:
            handled = handle_pending(sender, name, body, pending)
            if handled:
                return str(resp)

        # 3. Free-text fallback commands
        low = body.lower()

        # Auto-join via token: "הצטרף AB3X9K"
        parts = body.strip().split()
        if len(parts) == 2 and parts[0] in ["הצטרף", "join"]:
            token = parts[1].upper()
            lst   = get_list_by_token(token)
            if lst:
                result = join_list(str(lst["_id"]), sender, name)
                if result == "success":
                    items = get_list_items(str(lst["_id"]))
                    send_msg(sender,
                        f"🎉 *ברוך הבא לרשימה {lst['name']}!*\n"
                        f"👑 מנהל: {lst.get('admin_name', '')}\n"
                        f"📦 {len(items)} פריטים ברשימה\n\n"
                        f"עכשיו תוכל להוסיף מוצרים!"
                    )
                elif result == "already_member":
                    send_msg(sender, f"ℹ️ אתה כבר חבר ברשימה *{lst['name']}* 👍")
                elif result == "closed":
                    send_msg(sender, "❌ הרשימה כבר סגורה.")
                send_main_menu(sender, name)
                return str(resp)

        if low in ["היי", "הי", "שלום", "start", "התחל", "menu", "תפריט", ""]:
            send_main_menu(sender, name)
        elif "רשימה" in low and "חדשה" in low:
            handle_button(sender, name, "new_list")
        elif low in ["הצג", "רשימה", "show"]:
            handle_button(sender, name, "show_list")
        elif low in ["הצטרף", "join"]:
            handle_button(sender, name, "join_list")
        elif low in ["עזרה", "help", "?"]:
            send_main_menu(sender, name)
        else:
            send_main_menu(sender, name)

    except Exception as e:
        print(f"❌ Error: {e}")
        send_msg(sender, "⚠️ אירעה שגיאה. נסה שוב או שלח *תפריט*.")

    return str(resp)


@app.route("/join/<token>", methods=["GET"])
def join_page(token: str):
    """
    Landing page for invite links.
    Redirects to wa.me with pre-filled message so the user just taps Send.
    """
    from urllib.parse import quote
    lst = get_list_by_token(token)
    if not lst or lst.get("status") != "open":
        return """
        <html><head><meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <style>body{font-family:sans-serif;text-align:center;padding:40px;direction:rtl}
        h2{color:#e74c3c}</style></head>
        <body><h2>❌ הרשימה לא נמצאה או סגורה</h2>
        <p>הקישור אינו תקין. בקש קישור חדש מהמנהל.</p></body></html>
        """, 404

    list_name  = lst["name"]
    admin_name = lst.get("admin_name", "")
    pre_msg    = f"הצטרף {token.upper()}"
    wa_url     = f"https://wa.me/{SANDBOX_NUMBER}?text={quote(pre_msg)}"

    html = f"""<!DOCTYPE html>
<html lang='he' dir='rtl'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width,initial-scale=1'>
  <title>הצטרף לרשימה — {list_name}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, sans-serif;
      background: linear-gradient(135deg, #25D366 0%, #128C7E 100%);
      min-height: 100vh;
      display: flex; align-items: center; justify-content: center;
      padding: 20px;
    }}
    .card {{
      background: white; border-radius: 20px;
      padding: 36px 28px; max-width: 380px; width: 100%;
      text-align: center; box-shadow: 0 20px 60px rgba(0,0,0,.2);
    }}
    .icon {{ font-size: 56px; margin-bottom: 16px; }}
    h1 {{ font-size: 22px; color: #1a1a1a; margin-bottom: 8px; }}
    .list-name {{
      font-size: 26px; font-weight: 700;
      color: #128C7E; margin: 12px 0 4px;
    }}
    .admin {{ color: #666; font-size: 14px; margin-bottom: 28px; }}
    .btn {{
      display: block; width: 100%;
      background: #25D366; color: white;
      text-decoration: none; border-radius: 50px;
      padding: 16px; font-size: 18px; font-weight: 600;
      letter-spacing: .3px;
      transition: transform .15s, box-shadow .15s;
    }}
    .btn:active {{ transform: scale(.97); }}
    .note {{ color: #999; font-size: 12px; margin-top: 16px; line-height: 1.5; }}
    .token-badge {{
      display: inline-block; background: #f0f0f0;
      border-radius: 8px; padding: 4px 12px;
      font-family: monospace; font-size: 18px;
      font-weight: 700; letter-spacing: 3px;
      color: #333; margin: 8px 0 20px;
    }}
  </style>
  <script>
    // Auto-redirect on mobile after short delay
    setTimeout(() => {{ window.location.href = "{wa_url}"; }}, 1500);
  </script>
</head>
<body>
  <div class='card'>
    <div class='icon'>🛒</div>
    <h1>הוזמנת לרשימת קניות</h1>
    <div class='list-name'>{list_name}</div>
    <div class='admin'>👑 יצר: {admin_name}</div>
    <div class='token-badge'>{token.upper()}</div>
    <a href='{wa_url}' class='btn'>📱 פתח בווטסאפ</a>
    <p class='note'>לוחץ על הכפתור → ווטסאפ נפתח עם הודעה מוכנה → לחץ שלח ✓</p>
  </div>
</body>
</html>"""
    return html


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "version": "2.1-invite-links"}, 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"🚀 Bot v2 (Buttons) running on port {port}")
    app.run(debug=True, host="0.0.0.0", port=port)
