import random
from datetime import datetime

from django.core.management.base import BaseCommand

from booking.mongo import get_drivers_collection

SAMPLE_DRIVERS = [
    {"name": "Ramesh Kumar", "phone": "9876500001", "vehicle_number": "MH-31-AB-1234"},
    {"name": "Suresh Patil", "phone": "9876500002", "vehicle_number": "MH-31-CD-5678"},
    {"name": "Vijay Singh", "phone": "9876500003", "vehicle_number": "MH-12-EF-4321"},
    {"name": "Anil Sharma", "phone": "9876500004", "vehicle_number": "MH-14-GH-8765"},
    {"name": "Prakash Jadhav", "phone": "9876500005", "vehicle_number": "MH-04-IJ-1122"},
    {"name": "Deepak Rao", "phone": "9876500006", "vehicle_number": "MH-31-KL-3344"},
    {"name": "Mahesh Gupta", "phone": "9876500007", "vehicle_number": "MH-31-MN-5566"},
]


class Command(BaseCommand):
    help = (
        "Seed a handful of sample admin-owned drivers into MongoDB. "
        "Note: these have no owner_username and no login credentials, so "
        "they can only be managed from the admin panel (/panel/drivers/), "
        "not accepted into via the owner ride-requests flow."
    )

    def handle(self, *args, **options):
        drivers = get_drivers_collection()
        created = 0
        for d in SAMPLE_DRIVERS:
            if drivers.find_one({"vehicle_number": d["vehicle_number"]}):
                continue
            drivers.insert_one(
                {
                    **d,
                    "status": "available",
                    "current_lat": None,
                    "current_lng": None,
                    "rating": round(random.uniform(4.2, 5.0), 1),
                    "created_at": datetime.utcnow(),
                }
            )
            created += 1

        self.stdout.write(
            self.style.SUCCESS(f"Seeded {created} new driver(s) (skipped duplicates).")
        )
