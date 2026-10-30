from datetime import datetime

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand

from booking.mongo import get_admins_collection


class Command(BaseCommand):
    help = "Create (or update the password of) an admin-panel account in MongoDB."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--password", required=True)

    def handle(self, *args, **options):
        username = options["username"].strip()
        password = options["password"]

        admins = get_admins_collection()
        existing = admins.find_one({"username": username})

        if existing:
            admins.update_one(
                {"_id": existing["_id"]},
                {"$set": {"password": make_password(password)}},
            )
            self.stdout.write(
                self.style.SUCCESS(f"Updated password for admin '{username}'.")
            )
        else:
            admins.insert_one(
                {
                    "username": username,
                    "password": make_password(password),
                    "created_at": datetime.utcnow(),
                }
            )
            self.stdout.write(
                self.style.SUCCESS(f"Created admin '{username}'.")
            )
