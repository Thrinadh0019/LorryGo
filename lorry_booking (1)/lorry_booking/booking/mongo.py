"""
MongoDB connection helper.

Connects to a local MongoDB instance and exposes the collections used by
this app. Everything funnels through get_db() so there is a single,
reused pymongo client (pymongo's MongoClient is already thread-safe and
pools connections internally, so we don't want to open a new client per
request).

Database : VTR
Collections:
    - users     : customer login credentials
    - admins    : admin-panel login credentials
    - owners    : lorry-owner login credentials
    - drivers   : vehicles/drivers available for booking (may be linked to an owner)
    - bookings  : lorry booking records
"""

from django.conf import settings
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

_client = None


def get_client():
    """Return a lazily-created, shared MongoClient instance."""
    global _client
    if _client is None:
        _client = MongoClient(
            settings.MONGO_URI,
            serverSelectionTimeoutMS=5000,
        )
    return _client


def get_db():
    """Return the VTR database handle."""
    return get_client()[settings.MONGO_DB_NAME]


def get_users_collection():
    return get_db()['users']


def get_bookings_collection():
    return get_db()['bookings']


def get_drivers_collection():
    return get_db()['drivers']


def get_admins_collection():
    return get_db()['admins']


def get_owners_collection():
    return get_db()['owners']


def check_connection():
    """
    Ping MongoDB to verify the connection is alive.
    Returns (True, None) on success or (False, error_message) on failure.
    """
    try:
        get_client().admin.command('ping')
        return True, None
    except ConnectionFailure as exc:
        return False, str(exc)
