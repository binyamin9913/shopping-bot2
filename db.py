"""
MongoDB Database Layer - v2
Adds: pending_actions collection for multi-step conversation state
"""

from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime, timezone
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
DB_NAME   = os.getenv("DB_NAME", "shopping_bot")

_client = MongoClient(MONGO_URI)
db      = _client[DB_NAME]

lists_col   = db["lists"]
items_col   = db["items"]
pending_col = db["pending_actions"]
invites_col = db["invites"]           # short tokens → list_id


# ─────────────────────────────────────────────
#  PENDING ACTIONS  (conversation state)
# ─────────────────────────────────────────────

def get_pending_action(phone: str) -> dict | None:
    doc = pending_col.find_one({"phone": phone})
    return doc.get("data") if doc else None


def set_pending_action(phone: str, data: dict):
    pending_col.update_one(
        {"phone": phone},
        {"$set": {"data": data, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )


def clear_pending_action(phone: str):
    pending_col.delete_one({"phone": phone})


# ─────────────────────────────────────────────
#  LISTS
# ─────────────────────────────────────────────

def create_list(admin_phone: str, admin_name: str, name: str, expiry=None) -> str:
    doc = {
        "name":         name,
        "admin_phone":  admin_phone,
        "admin_name":   admin_name,
        "members":      [admin_phone],
        "member_names": {_safe_key(admin_phone): admin_name},
        "status":       "open",
        "created_at":   datetime.now(timezone.utc),
        "expiry":       expiry,
    }
    result = lists_col.insert_one(doc)
    return str(result.inserted_id)


def get_active_list(phone: str):
    return lists_col.find_one(
        {"members": phone, "status": "open"},
        sort=[("created_at", -1)]
    )


def get_list_by_id(list_id: str):
    try:
        return lists_col.find_one({"_id": ObjectId(list_id)})
    except Exception:
        return None


def join_list(list_id: str, phone: str, name: str) -> str:
    try:
        lst = lists_col.find_one({"_id": ObjectId(list_id)})
    except Exception:
        return "not_found"

    if not lst:
        return "not_found"
    if lst["status"] != "open":
        return "closed"
    if phone in lst.get("members", []):
        return "already_member"

    lists_col.update_one(
        {"_id": ObjectId(list_id)},
        {
            "$push": {"members": phone},
            "$set":  {f"member_names.{_safe_key(phone)}": name}
        }
    )
    return "success"


def close_list(list_id: str) -> bool:
    try:
        result = lists_col.update_one(
            {"_id": ObjectId(list_id)},
            {"$set": {"status": "closed", "closed_at": datetime.now(timezone.utc)}}
        )
        return result.modified_count > 0
    except Exception as e:
        print(f"close_list error: {e}")
        return False


def is_admin(list_id: str, phone: str) -> bool:
    try:
        lst = lists_col.find_one({"_id": ObjectId(list_id)})
        return bool(lst and lst.get("admin_phone") == phone)
    except Exception:
        return False


# ─────────────────────────────────────────────
#  ITEMS
# ─────────────────────────────────────────────

def add_item(list_id: str, item_name: str, added_by: str, added_by_name: str) -> bool:
    try:
        items_col.insert_one({
            "list_id":       ObjectId(list_id),
            "name":          item_name,
            "checked":       False,
            "added_by":      added_by,
            "added_by_name": added_by_name,
            "created_at":    datetime.now(timezone.utc),
        })
        return True
    except Exception as e:
        print(f"add_item error: {e}")
        return False


def get_list_items(list_id: str) -> list:
    try:
        return list(items_col.find(
            {"list_id": ObjectId(list_id)},
            sort=[("created_at", 1)]
        ))
    except Exception:
        return []


def check_item(list_id: str, item_id: str, checked: bool) -> bool:
    try:
        result = items_col.update_one(
            {"_id": ObjectId(item_id), "list_id": ObjectId(list_id)},
            {"$set": {
                "checked":    checked,
                "checked_at": datetime.now(timezone.utc) if checked else None
            }}
        )
        return result.modified_count > 0
    except Exception as e:
        print(f"check_item error: {e}")
        return False


def delete_item(list_id: str, item_id: str) -> bool:
    try:
        result = items_col.delete_one(
            {"_id": ObjectId(item_id), "list_id": ObjectId(list_id)}
        )
        return result.deleted_count > 0
    except Exception as e:
        print(f"delete_item error: {e}")
        return False


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def _safe_key(phone: str) -> str:
    """MongoDB keys can't contain dots or start with $"""
    return phone.replace("+", "p").replace(":", "_").replace(".", "_")


# ─────────────────────────────────────────────
#  INVITE TOKENS
# ─────────────────────────────────────────────

def create_invite_token(list_id: str) -> str:
    """Create or reuse a short invite token for a list. Returns 6-char token."""
    import secrets, string
    # Reuse existing token for the same list if it exists
    existing = invites_col.find_one({"list_id": list_id})
    if existing:
        return existing["token"]
    # Generate a short memorable token: 6 uppercase letters/digits
    alphabet = string.ascii_uppercase + string.digits
    while True:
        token = "".join(secrets.choice(alphabet) for _ in range(6))
        if not invites_col.find_one({"token": token}):
            break
    invites_col.insert_one({
        "token":      token,
        "list_id":    list_id,
        "created_at": datetime.now(timezone.utc),
    })
    return token


def get_list_by_token(token: str):
    """Resolve a short token to a list document. Returns list doc or None."""
    doc = invites_col.find_one({"token": token.upper().strip()})
    if not doc:
        return None
    return lists_col.find_one({"_id": ObjectId(doc["list_id"])})
