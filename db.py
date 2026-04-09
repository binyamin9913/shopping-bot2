"""
MongoDB Database Layer for WhatsApp Shopping List Bot
"""
 
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime, timezone
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
DB_NAME = os.getenv("DB_NAME", "shopping_bot")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

lists_col = db["lists"]
items_col = db["items"]


def create_list(admin_phone: str, admin_name: str, name: str, expiry=None) -> str:
    """Create a new shopping list. Returns the list ID."""
    doc = {
        "name": name,
        "admin_phone": admin_phone,
        "admin_name": admin_name,
        "members": [admin_phone],
        "member_names": {admin_phone: admin_name},
        "status": "open",
        "created_at": datetime.now(timezone.utc),
        "expiry": expiry,
    }
    result = lists_col.insert_one(doc)
    return str(result.inserted_id)


def get_active_list(phone: str):
    """Get the active (open) list for a user (as admin or member)."""
    # Check expiry: lists past expiry are still 'open' but locked for editing
    lst = lists_col.find_one({
        "members": phone,
        "status": "open"
    }, sort=[("created_at", -1)])
    return lst


def get_list_by_id(list_id: str):
    """Get a list by its ID."""
    try:
        return lists_col.find_one({"_id": ObjectId(list_id)})
    except:
        return None


def get_all_active_lists(phone: str):
    """Get all active lists for a user."""
    return list(lists_col.find({
        "members": phone,
        "status": "open"
    }, sort=[("created_at", -1)]))


def join_list(list_id: str, phone: str, name: str) -> str:
    """
    Join an existing list.
    Returns: 'success', 'not_found', 'closed', 'already_member'
    """
    try:
        lst = lists_col.find_one({"_id": ObjectId(list_id)})
    except:
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
            "$set": {f"member_names.{phone.replace('+', 'p').replace(':', '_')}": name}
        }
    )
    return "success"


def add_item(list_id: str, item_name: str, added_by: str, added_by_name: str) -> bool:
    """Add an item to a list."""
    try:
        doc = {
            "list_id": ObjectId(list_id),
            "name": item_name,
            "checked": False,
            "added_by": added_by,
            "added_by_name": added_by_name,
            "created_at": datetime.now(timezone.utc),
        }
        items_col.insert_one(doc)
        return True
    except Exception as e:
        print(f"Error adding item: {e}")
        return False


def get_list_items(list_id: str) -> list:
    """Get all items for a list, sorted by creation time."""
    try:
        return list(items_col.find(
            {"list_id": ObjectId(list_id)},
            sort=[("created_at", 1)]
        ))
    except:
        return []


def check_item(list_id: str, item_id: str, checked: bool) -> bool:
    """Check or uncheck an item."""
    try:
        result = items_col.update_one(
            {"_id": ObjectId(item_id), "list_id": ObjectId(list_id)},
            {"$set": {"checked": checked, "checked_at": datetime.now(timezone.utc) if checked else None}}
        )
        return result.modified_count > 0
    except Exception as e:
        print(f"Error checking item: {e}")
        return False


def delete_item(list_id: str, item_id: str) -> bool:
    """Delete an item from a list."""
    try:
        result = items_col.delete_one(
            {"_id": ObjectId(item_id), "list_id": ObjectId(list_id)}
        )
        return result.deleted_count > 0
    except Exception as e:
        print(f"Error deleting item: {e}")
        return False


def close_list(list_id: str) -> bool:
    """Close a shopping list."""
    try:
        result = lists_col.update_one(
            {"_id": ObjectId(list_id)},
            {"$set": {"status": "closed", "closed_at": datetime.now(timezone.utc)}}
        )
        return result.modified_count > 0
    except Exception as e:
        print(f"Error closing list: {e}")
        return False


def is_admin(list_id: str, phone: str) -> bool:
    """Check if a user is the admin of a list."""
    try:
        lst = lists_col.find_one({"_id": ObjectId(list_id)})
        return lst and lst.get("admin_phone") == phone
    except:
        return False


def cleanup_expired_lists():
    """Mark expired lists — can be run as a cron job."""
    now = datetime.now(timezone.utc)
    result = lists_col.update_many(
        {
            "status": "open",
            "expiry": {"$lt": now, "$ne": None}
        },
        {"$set": {"status": "expired"}}
    )
    return result.modified_count
