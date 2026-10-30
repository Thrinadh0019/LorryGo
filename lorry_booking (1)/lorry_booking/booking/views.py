import math
import random
from datetime import datetime
from functools import wraps

from bson import ObjectId
from bson.errors import InvalidId
from django.contrib import messages
from django.contrib.auth.hashers import check_password, make_password
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .mongo import (
    get_admins_collection,
    get_bookings_collection,
    get_drivers_collection,
    get_owners_collection,
    get_users_collection,
)

# ---------------------------------------------------------------------------
# Lorry types & fare model (like Uber/Ola's vehicle-option cards)
# ---------------------------------------------------------------------------
LORRY_TYPES = [
    {"id": "mini", "name": "Mini Truck", "capacity": "Up to 1 ton",
     "base_fare": 50, "per_km": 15, "eta_min": 5,
     "icon": "🛻"},
    {"id": "pickup", "name": "Pickup Van", "capacity": "1.5 ton",
     "base_fare": 70, "per_km": 18, "eta_min": 7,
     "icon": "🚚"},
    {"id": "medium", "name": "Medium Truck", "capacity": "5 ton",
     "base_fare": 120, "per_km": 25, "eta_min": 10,
     "icon": "🚛"},
    {"id": "heavy", "name": "Heavy Truck", "capacity": "10+ ton",
     "base_fare": 200, "per_km": 35, "eta_min": 15,
     "icon": "🚛"},
    {"id": "container", "name": "Container Lorry", "capacity": "20+ ton",
     "base_fare": 300, "per_km": 45, "eta_min": 20,
     "icon": "🚛"},
]
LORRY_TYPES_BY_ID = {lt["id"]: lt for lt in LORRY_TYPES}

# Human-friendly labels for each trip status, used across templates.
STATUS_LABELS = {
    "requested": "Finding a driver...",
    "driver_assigned": "Driver assigned",
    "in_transit": "In transit",
    "completed": "Completed",
    "cancelled": "Cancelled",
}


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two lat/lng points, in km."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def calc_fare(lorry_type_id, distance_km):
    lt = LORRY_TYPES_BY_ID.get(lorry_type_id, LORRY_TYPES[0])
    fare = lt["base_fare"] + lt["per_km"] * distance_km
    return round(fare, 2)


# ---------------------------------------------------------------------------
# Return-trip discount
# ---------------------------------------------------------------------------
# When a driver completes a delivery, we remember where they dropped off
# (last_drop_lat/lng on their `drivers` doc) and free them back to
# "available". If a *new* booking's pickup point falls within
# RETURN_TRIP_DISCOUNT_RADIUS_KM of that drop-off point, the truck is
# presumably heading back that way anyway — so we offer the ride at half
# price rather than have it drive back empty.
RETURN_TRIP_DISCOUNT_RADIUS_KM = 50
RETURN_TRIP_DISCOUNT_RATE = 0.5  # 50% off


def find_nearby_returning_driver(pickup_lat, pickup_lng):
    """
    Look for the closest *available* driver whose last completed drop-off
    is within RETURN_TRIP_DISCOUNT_RADIUS_KM of this pickup point.
    Returns (driver_doc, distance_km) or (None, None) if no match.
    """
    if pickup_lat is None or pickup_lng is None:
        return None, None

    best_driver, best_distance = None, None
    for driver in get_drivers_collection().find(
        {
            "status": "available",
            "last_drop_lat": {"$ne": None},
            "last_drop_lng": {"$ne": None},
        }
    ):
        distance = haversine_km(
            pickup_lat, pickup_lng, driver["last_drop_lat"], driver["last_drop_lng"]
        )
        if distance <= RETURN_TRIP_DISCOUNT_RADIUS_KM and (
            best_distance is None or distance < best_distance
        ):
            best_driver, best_distance = driver, distance

    if best_driver is None:
        return None, None
    return best_driver, round(best_distance, 2)


def _mark_driver_returning_from(driver_id, drop_lat, drop_lng):
    """Called when a trip completes: free the driver up and remember where
    they just dropped off, so the return-trip discount can find them."""
    get_drivers_collection().update_one(
        {"_id": driver_id},
        {
            "$set": {
                "status": "available",
                "last_drop_lat": drop_lat,
                "last_drop_lng": drop_lng,
                "last_drop_at": datetime.utcnow(),
            }
        },
    )


def serialize_booking(doc):
    """Make a Mongo booking document template/JSON friendly."""
    doc["id"] = str(doc["_id"])
    if doc.get("driver_id"):
        doc["driver_id"] = str(doc["driver_id"])
    doc["status_label"] = STATUS_LABELS.get(doc.get("status"), doc.get("status"))
    doc.setdefault("is_return_discount", False)
    doc.setdefault("original_fare", None)
    doc.setdefault("return_discount_distance_km", None)
    return doc


# ---------------------------------------------------------------------------
# Customer auth helpers / views
# ---------------------------------------------------------------------------
def login_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get("username"):
            messages.info(request, "Please log in to continue.")
            return redirect("booking:login")
        return view_func(request, *args, **kwargs)

    return wrapper


def register_view(request):
    if request.session.get("username"):
        return redirect("booking:dashboard")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")

        if not username or not password:
            messages.error(request, "Username and password are required.")
            return render(request, "booking/register.html")
        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, "booking/register.html")
        if len(password) < 6:
            messages.error(request, "Password must be at least 6 characters.")
            return render(request, "booking/register.html")

        users = get_users_collection()
        if users.find_one({"username": username}):
            messages.error(request, "That username is already taken.")
            return render(request, "booking/register.html")

        users.insert_one(
            {
                "username": username,
                "email": email,
                "password": make_password(password),
                "created_at": datetime.utcnow(),
            }
        )
        messages.success(request, "Account created successfully. Please log in.")
        return redirect("booking:login")

    return render(request, "booking/register.html")


def login_view(request):
    if request.session.get("username"):
        return redirect("booking:dashboard")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        users = get_users_collection()
        user = users.find_one({"username": username})

        if user and check_password(password, user["password"]):
            request.session["username"] = user["username"]
            messages.success(request, f"Welcome back, {user['username']}!")
            return redirect("booking:dashboard")

        messages.error(request, "Invalid username or password.")
        return render(request, "booking/login.html")

    return render(request, "booking/login.html")


def logout_view(request):
    request.session.flush()
    messages.success(request, "You have been logged out.")
    return redirect("booking:login")


# ---------------------------------------------------------------------------
# Customer ride-hailing views
# ---------------------------------------------------------------------------
@login_required
def dashboard_view(request):
    bookings = get_bookings_collection()
    active = bookings.find_one(
        {
            "username": request.session["username"],
            "status": {"$in": ["requested", "driver_assigned", "in_transit"]},
        },
        sort=[("created_at", -1)],
    )
    recent = list(
        bookings.find({"username": request.session["username"]})
        .sort("created_at", -1)
        .limit(5)
    )
    recent = [serialize_booking(b) for b in recent]
    active = serialize_booking(active) if active else None

    return render(
        request,
        "booking/dashboard.html",
        {"recent_bookings": recent, "active_trip": active},
    )


@login_required
def book_lorry_view(request):
    if request.method == "POST":
        source = request.POST.get("source", "").strip()
        destination = request.POST.get("destination", "").strip()
        pickup_lat = request.POST.get("pickup_lat", "")
        pickup_lng = request.POST.get("pickup_lng", "")
        drop_lat = request.POST.get("drop_lat", "")
        drop_lng = request.POST.get("drop_lng", "")
        distance_km_raw = request.POST.get("distance_km", "")
        lorry_type = request.POST.get("lorry_type", "").strip()
        contact_number = request.POST.get("contact_number", "").strip()
        notes = request.POST.get("notes", "").strip()

        errors = []
        if not source:
            errors.append("Please choose a pickup location.")
        if not destination:
            errors.append("Please choose a drop location.")
        if lorry_type not in LORRY_TYPES_BY_ID:
            errors.append("Please choose a valid lorry type.")
        if not contact_number:
            errors.append("Contact number is required.")
        if not all([pickup_lat, pickup_lng, drop_lat, drop_lng]):
            errors.append("Please select both pickup and drop points on the map.")

        if errors:
            for err in errors:
                messages.error(request, err)
            return render(
                request,
                "booking/book_lorry.html",
                {"lorry_types": LORRY_TYPES, "form_data": request.POST},
            )

        try:
            pickup_lat, pickup_lng = float(pickup_lat), float(pickup_lng)
            drop_lat, drop_lng = float(drop_lat), float(drop_lng)
        except ValueError:
            messages.error(request, "Invalid map coordinates, please reselect your points.")
            return render(
                request,
                "booking/book_lorry.html",
                {"lorry_types": LORRY_TYPES, "form_data": request.POST},
            )

        # Trust the client-side OSRM route distance if it was supplied,
        # otherwise fall back to a straight-line estimate.
        try:
            distance_km = round(float(distance_km_raw), 2)
        except (TypeError, ValueError):
            distance_km = round(
                haversine_km(pickup_lat, pickup_lng, drop_lat, drop_lng), 2
            )

        fare = calc_fare(lorry_type, distance_km)

        returning_driver, return_distance_km = find_nearby_returning_driver(
            pickup_lat, pickup_lng
        )
        is_return_discount = returning_driver is not None
        original_fare = fare
        if is_return_discount:
            fare = round(fare * (1 - RETURN_TRIP_DISCOUNT_RATE), 2)

        booking_doc = {
            "username": request.session["username"],
            "source": source,
            "destination": destination,
            "pickup_lat": pickup_lat,
            "pickup_lng": pickup_lng,
            "drop_lat": drop_lat,
            "drop_lng": drop_lng,
            "distance_km": distance_km,
            "lorry_type": lorry_type,
            "lorry_type_name": LORRY_TYPES_BY_ID[lorry_type]["name"],
            "fare": fare,
            "is_return_discount": is_return_discount,
            "original_fare": original_fare if is_return_discount else None,
            "return_discount_distance_km": return_distance_km,
            "contact_number": contact_number,
            "notes": notes,
            "status": "requested",
            "driver_id": None,
            "driver_name": None,
            "driver_phone": None,
            "driver_vehicle_number": None,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        result = get_bookings_collection().insert_one(booking_doc)
        booking_id = result.inserted_id

        if is_return_discount:
            messages.success(
                request,
                f"Ride requested! You got a return-trip discount — a nearby "
                f"returning truck cut this fare in half.",
            )
        else:
            messages.success(request, "Ride requested! Waiting for a nearby owner to accept and assign a driver.")
        return redirect("booking:trip_status", booking_id=str(booking_id))

    return render(request, "booking/book_lorry.html", {"lorry_types": LORRY_TYPES})


@login_required
def return_discount_check_api(request):
    """
    Polled by the booking page's map JS whenever the pickup pin is set or
    moved, so the customer can see the discount banner *before* they submit
    — the actual discount is always recalculated authoritatively server-side
    in book_lorry_view when the booking is created.
    """
    try:
        pickup_lat = float(request.GET.get("lat"))
        pickup_lng = float(request.GET.get("lng"))
    except (TypeError, ValueError):
        return JsonResponse({"eligible": False})

    driver, distance_km = find_nearby_returning_driver(pickup_lat, pickup_lng)
    return JsonResponse(
        {
            "eligible": driver is not None,
            "distance_km": distance_km,
            "discount_percent": int(RETURN_TRIP_DISCOUNT_RATE * 100),
            "radius_km": RETURN_TRIP_DISCOUNT_RADIUS_KM,
        }
    )


def _assign_driver_to_booking(booking, driver):
    """
    Assign a specific driver to a specific booking (used when an owner
    accepts a ride request and picks one of their drivers). Uses an atomic
    update guarded by status="requested" so two owners racing to accept the
    same ride can't both succeed.
    """
    pickup_lat = booking.get("pickup_lat")
    pickup_lng = booking.get("pickup_lng")

    # Place the driver at a small random offset from the pickup point so
    # the map has somewhere realistic to show them coming from.
    offset_lat = pickup_lat + random.uniform(-0.02, 0.02) if pickup_lat is not None else None
    offset_lng = pickup_lng + random.uniform(-0.02, 0.02) if pickup_lng is not None else None

    result = get_bookings_collection().update_one(
        {"_id": booking["_id"], "status": "requested"},
        {
            "$set": {
                "status": "driver_assigned",
                "driver_id": driver["_id"],
                "driver_name": driver["name"],
                "driver_phone": driver["phone"],
                "driver_vehicle_number": driver["vehicle_number"],
                "driver_start_lat": offset_lat,
                "driver_start_lng": offset_lng,
                "updated_at": datetime.utcnow(),
            }
        },
    )
    if result.matched_count == 0:
        # Someone else already accepted this ride.
        return False

    get_drivers_collection().update_one(
        {"_id": driver["_id"]},
        {"$set": {"status": "on_trip", "current_lat": offset_lat, "current_lng": offset_lng}},
    )
    return True


@login_required
def trip_status_view(request, booking_id):
    try:
        oid = ObjectId(booking_id)
    except InvalidId:
        messages.error(request, "Invalid booking reference.")
        return redirect("booking:my_bookings")

    booking = get_bookings_collection().find_one(
        {"_id": oid, "username": request.session["username"]}
    )
    if not booking:
        messages.error(request, "Booking not found.")
        return redirect("booking:my_bookings")

    booking = serialize_booking(booking)
    return render(request, "booking/trip_status.html", {"b": booking})


@login_required
def booking_status_api(request, booking_id):
    """Polled by the trip-tracking page's JS to reflect admin-side updates live."""
    try:
        oid = ObjectId(booking_id)
    except InvalidId:
        return JsonResponse({"error": "invalid id"}, status=400)

    booking = get_bookings_collection().find_one(
        {"_id": oid, "username": request.session.get("username")}
    )
    if not booking:
        return JsonResponse({"error": "not found"}, status=404)

    booking = serialize_booking(booking)
    return JsonResponse(
        {
            "status": booking["status"],
            "status_label": booking["status_label"],
            "driver_name": booking.get("driver_name"),
            "driver_phone": booking.get("driver_phone"),
            "driver_vehicle_number": booking.get("driver_vehicle_number"),
        }
    )


@login_required
def my_bookings_view(request):
    bookings = get_bookings_collection()
    all_bookings = list(
        bookings.find({"username": request.session["username"]}).sort(
            "created_at", -1
        )
    )
    all_bookings = [serialize_booking(b) for b in all_bookings]
    return render(request, "booking/my_bookings.html", {"bookings": all_bookings})


@login_required
def cancel_booking_view(request, booking_id):
    try:
        oid = ObjectId(booking_id)
    except InvalidId:
        messages.error(request, "Invalid booking reference.")
        return redirect("booking:my_bookings")

    bookings = get_bookings_collection()
    booking = bookings.find_one({"_id": oid, "username": request.session["username"]})
    if not booking:
        messages.error(request, "Booking not found.")
        return redirect("booking:my_bookings")

    if booking["status"] not in ("requested", "driver_assigned"):
        messages.error(request, "This booking can no longer be cancelled.")
        return redirect("booking:my_bookings")

    bookings.update_one(
        {"_id": oid},
        {"$set": {"status": "cancelled", "updated_at": datetime.utcnow()}},
    )
    # Free up the driver, if one had been assigned.
    if booking.get("driver_id"):
        get_drivers_collection().update_one(
            {"_id": booking["driver_id"]}, {"$set": {"status": "available"}}
        )

    messages.success(request, "Booking cancelled.")
    return redirect("booking:my_bookings")


# ---------------------------------------------------------------------------
# Admin panel (separate from the customer-facing site & from django.contrib.admin)
# ---------------------------------------------------------------------------
def admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get("admin_username"):
            messages.info(request, "Please log in to the admin panel.")
            return redirect("booking:admin_login")
        return view_func(request, *args, **kwargs)

    return wrapper


def admin_login_view(request):
    if request.session.get("admin_username"):
        return redirect("booking:admin_dashboard")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        admin = get_admins_collection().find_one({"username": username})
        if admin and check_password(password, admin["password"]):
            request.session["admin_username"] = admin["username"]
            messages.success(request, f"Welcome, {admin['username']}.")
            return redirect("booking:admin_dashboard")

        messages.error(request, "Invalid admin credentials.")
        return render(request, "booking/admin/login.html")

    return render(request, "booking/admin/login.html")


def admin_logout_view(request):
    request.session.pop("admin_username", None)
    messages.success(request, "Logged out of admin panel.")
    return redirect("booking:admin_login")


@admin_required
def admin_dashboard_view(request):
    bookings = get_bookings_collection()
    drivers = get_drivers_collection()
    users = get_users_collection()

    stats = {
        "total_users": users.count_documents({}),
        "total_drivers": drivers.count_documents({}),
        "available_drivers": drivers.count_documents({"status": "available"}),
        "total_bookings": bookings.count_documents({}),
        "active_trips": bookings.count_documents(
            {"status": {"$in": ["requested", "driver_assigned", "in_transit"]}}
        ),
        "completed_trips": bookings.count_documents({"status": "completed"}),
    }
    recent_bookings = [
        serialize_booking(b)
        for b in bookings.find().sort("created_at", -1).limit(8)
    ]
    return render(
        request,
        "booking/admin/dashboard.html",
        {"stats": stats, "recent_bookings": recent_bookings},
    )


@admin_required
def admin_bookings_view(request):
    bookings = get_bookings_collection()
    status_filter = request.GET.get("status", "")
    query = {"status": status_filter} if status_filter else {}
    all_bookings = [
        serialize_booking(b) for b in bookings.find(query).sort("created_at", -1)
    ]
    return render(
        request,
        "booking/admin/bookings.html",
        {
            "bookings": all_bookings,
            "status_filter": status_filter,
            "statuses": list(STATUS_LABELS.items()),
        },
    )


@admin_required
def admin_update_booking_view(request, booking_id):
    if request.method != "POST":
        return redirect("booking:admin_bookings")

    try:
        oid = ObjectId(booking_id)
    except InvalidId:
        messages.error(request, "Invalid booking reference.")
        return redirect("booking:admin_bookings")

    new_status = request.POST.get("status")
    if new_status not in STATUS_LABELS:
        messages.error(request, "Invalid status.")
        return redirect("booking:admin_bookings")

    bookings = get_bookings_collection()
    booking = bookings.find_one({"_id": oid})
    if not booking:
        messages.error(request, "Booking not found.")
        return redirect("booking:admin_bookings")

    bookings.update_one(
        {"_id": oid},
        {"$set": {"status": new_status, "updated_at": datetime.utcnow()}},
    )

    # Free the driver once a trip wraps up or is cancelled.
    if new_status in ("completed", "cancelled") and booking.get("driver_id"):
        get_drivers_collection().update_one(
            {"_id": booking["driver_id"]}, {"$set": {"status": "available"}}
        )

    messages.success(request, "Booking status updated.")
    return redirect("booking:admin_bookings")


@admin_required
def admin_drivers_view(request):
    drivers = list(get_drivers_collection().find().sort("created_at", -1))
    for d in drivers:
        d["id"] = str(d["_id"])
    return render(
        request,
        "booking/admin/drivers.html",
        {"drivers": drivers},
    )


@admin_required
def admin_add_driver_view(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        phone = request.POST.get("phone", "").strip()
        vehicle_number = request.POST.get("vehicle_number", "").strip()

        if not all([name, phone, vehicle_number]):
            messages.error(request, "Please fill in all driver fields correctly.")
            return redirect("booking:admin_drivers")

        get_drivers_collection().insert_one(
            {
                "name": name,
                "phone": phone,
                "vehicle_number": vehicle_number,
                "status": "available",
                "current_lat": None,
                "current_lng": None,
                "rating": round(random.uniform(4.2, 5.0), 1),
                "owner_username": None,
                "created_at": datetime.utcnow(),
            }
        )
        messages.success(request, f"Driver {name} added.")
    return redirect("booking:admin_drivers")


@admin_required
def admin_toggle_driver_view(request, driver_id):
    try:
        oid = ObjectId(driver_id)
    except InvalidId:
        messages.error(request, "Invalid driver reference.")
        return redirect("booking:admin_drivers")

    drivers = get_drivers_collection()
    driver = drivers.find_one({"_id": oid})
    if driver:
        new_status = "available" if driver["status"] != "available" else "offline"
        drivers.update_one({"_id": oid}, {"$set": {"status": new_status}})
        messages.success(request, f"{driver['name']} marked {new_status}.")
    return redirect("booking:admin_drivers")


@admin_required
def admin_delete_driver_view(request, driver_id):
    try:
        oid = ObjectId(driver_id)
    except InvalidId:
        messages.error(request, "Invalid driver reference.")
        return redirect("booking:admin_drivers")

    get_drivers_collection().delete_one({"_id": oid})
    messages.success(request, "Driver removed.")
    return redirect("booking:admin_drivers")


@admin_required
def admin_users_view(request):
    users = list(get_users_collection().find().sort("created_at", -1))
    for u in users:
        u["id"] = str(u["_id"])
    return render(request, "booking/admin/users.html", {"users": users})


# ---------------------------------------------------------------------------
# Lorry Owner panel
# ---------------------------------------------------------------------------
# Owners register separately from customers and admins (own "owners"
# collection). Once logged in, they add their own drivers into the shared
# `drivers` collection (each document gets an `owner_username` so we know
# who it belongs to, plus login credentials the owner sets for the driver).
# New bookings sit in status="requested" until an owner accepts one from
# the Ride Requests page and assigns it to one of their available drivers
# (see owner_ride_requests_view / owner_accept_ride_view below) — there is
# no automatic matching. Owners can then track trips and earnings.

# Statuses a driver/owner is allowed to move a trip through, and what each
# button in the UI does. Neither can cancel a trip (that's the customer/admin's
# call) — only progress one that's already been assigned to their fleet.
TRIP_STATUS_TRANSITIONS = {
    "driver_assigned": "in_transit",
    "in_transit": "completed",
}


def owner_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get("owner_username"):
            messages.info(request, "Please log in to the owner panel.")
            return redirect("booking:owner_login")
        return view_func(request, *args, **kwargs)

    return wrapper


def owner_register_view(request):
    if request.session.get("owner_username"):
        return redirect("booking:owner_dashboard")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        phone = request.POST.get("phone", "").strip()
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")

        if not username or not phone or not password:
            messages.error(request, "All fields are required.")
            return render(request, "booking/owner/register.html")
        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, "booking/owner/register.html")
        if len(password) < 6:
            messages.error(request, "Password must be at least 6 characters.")
            return render(request, "booking/owner/register.html")

        owners = get_owners_collection()
        if owners.find_one({"username": username}):
            messages.error(request, "That username is already taken.")
            return render(request, "booking/owner/register.html")

        owners.insert_one(
            {
                "username": username,
                "phone": phone,
                "password": make_password(password),
                "created_at": datetime.utcnow(),
            }
        )
        messages.success(request, "Owner account created. Please log in.")
        return redirect("booking:owner_login")

    return render(request, "booking/owner/register.html")


def owner_login_view(request):
    if request.session.get("owner_username"):
        return redirect("booking:owner_dashboard")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        owners = get_owners_collection()
        owner = owners.find_one({"username": username})

        if owner and check_password(password, owner["password"]):
            request.session["owner_username"] = owner["username"]
            messages.success(request, f"Welcome back, {owner['username']}!")
            return redirect("booking:owner_dashboard")

        messages.error(request, "Invalid owner credentials.")
        return render(request, "booking/owner/login.html")

    return render(request, "booking/owner/login.html")


def owner_logout_view(request):
    request.session.pop("owner_username", None)
    messages.success(request, "Logged out of the owner panel.")
    return redirect("booking:owner_login")


def _owner_vehicle_ids(username):
    """All driver/vehicle _ids belonging to this owner."""
    drivers = get_drivers_collection()
    return [d["_id"] for d in drivers.find({"owner_username": username}, {"_id": 1})]


@owner_required
def owner_dashboard_view(request):
    username = request.session["owner_username"]
    drivers = get_drivers_collection()
    bookings = get_bookings_collection()

    my_vehicle_ids = _owner_vehicle_ids(username)

    stats = {
        "total_vehicles": len(my_vehicle_ids),
        "available_vehicles": drivers.count_documents(
            {"owner_username": username, "status": "available"}
        ),
        "active_trips": bookings.count_documents(
            {
                "driver_id": {"$in": my_vehicle_ids},
                "status": {"$in": ["driver_assigned", "in_transit"]},
            }
        ),
        "completed_trips": bookings.count_documents(
            {"driver_id": {"$in": my_vehicle_ids}, "status": "completed"}
        ),
    }

    earnings = 0
    for b in bookings.find(
        {"driver_id": {"$in": my_vehicle_ids}, "status": "completed"}, {"fare": 1}
    ):
        earnings += b.get("fare", 0)
    stats["earnings"] = round(earnings, 2)
    stats["pending_requests"] = bookings.count_documents({"status": "requested"})

    active_trips = [
        serialize_booking(b)
        for b in bookings.find(
            {
                "driver_id": {"$in": my_vehicle_ids},
                "status": {"$in": ["driver_assigned", "in_transit"]},
            }
        ).sort("created_at", -1)
    ]

    return render(
        request,
        "booking/owner/dashboard.html",
        {"stats": stats, "active_trips": active_trips},
    )


@owner_required
def owner_drivers_view(request):
    username = request.session["owner_username"]
    drivers = get_drivers_collection()

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        phone = request.POST.get("phone", "").strip()
        vehicle_number = request.POST.get("vehicle_number", "").strip()
        driver_username = request.POST.get("driver_username", "").strip()
        driver_password = request.POST.get("driver_password", "")

        if not all([name, phone, vehicle_number, driver_username, driver_password]):
            messages.error(request, "Please fill in all driver & vehicle fields correctly.")
            return redirect("booking:owner_drivers")

        if len(driver_password) < 6:
            messages.error(request, "Driver password must be at least 6 characters.")
            return redirect("booking:owner_drivers")

        if drivers.find_one({"username": driver_username}):
            messages.error(request, "That driver username is already taken — choose another.")
            return redirect("booking:owner_drivers")

        drivers.insert_one(
            {
                "name": name,
                "phone": phone,
                "vehicle_number": vehicle_number,
                "status": "available",
                "current_lat": None,
                "current_lng": None,
                "rating": round(random.uniform(4.2, 5.0), 1),
                "owner_username": username,
                "username": driver_username,
                "password": make_password(driver_password),
                "created_at": datetime.utcnow(),
            }
        )
        messages.success(
            request,
            f"Driver '{name}' added. Share these login details with them — "
            f"they can sign in at /driver/login/ with username "
            f"\"{driver_username}\" and the password you just set "
            f"(shown here only once, so note it down now).",
        )
        return redirect("booking:owner_drivers")

    my_drivers = list(drivers.find({"owner_username": username}).sort("created_at", -1))
    for d in my_drivers:
        d["id"] = str(d["_id"])

    return render(
        request,
        "booking/owner/drivers.html",
        {"drivers": my_drivers},
    )


@owner_required
def owner_toggle_driver_view(request, driver_id):
    username = request.session["owner_username"]
    try:
        oid = ObjectId(driver_id)
    except InvalidId:
        messages.error(request, "Invalid driver reference.")
        return redirect("booking:owner_drivers")

    drivers = get_drivers_collection()
    driver = drivers.find_one({"_id": oid, "owner_username": username})
    if not driver:
        messages.error(request, "Driver not found.")
        return redirect("booking:owner_drivers")

    if driver["status"] == "on_trip":
        messages.error(request, "Can't change availability while a driver is on a trip.")
        return redirect("booking:owner_drivers")

    new_status = "available" if driver["status"] != "available" else "offline"
    drivers.update_one({"_id": oid}, {"$set": {"status": new_status}})
    messages.success(request, f"{driver['name']} marked {new_status}.")
    return redirect("booking:owner_drivers")


@owner_required
def owner_remove_driver_view(request, driver_id):
    username = request.session["owner_username"]
    try:
        oid = ObjectId(driver_id)
    except InvalidId:
        messages.error(request, "Invalid driver reference.")
        return redirect("booking:owner_drivers")

    drivers = get_drivers_collection()
    driver = drivers.find_one({"_id": oid, "owner_username": username})
    if not driver:
        messages.error(request, "Driver not found.")
        return redirect("booking:owner_drivers")

    if driver["status"] == "on_trip":
        messages.error(request, "Can't remove a driver while they're on a trip.")
        return redirect("booking:owner_drivers")

    drivers.delete_one({"_id": oid})
    messages.success(request, f"Driver '{driver['name']}' removed.")
    return redirect("booking:owner_drivers")


@owner_required
def owner_ride_requests_view(request):
    """
    Pending (status="requested") rides — visible to every owner, first to
    accept gets it, same as a ride-hailing driver app's incoming-request
    list. Accepting requires picking one of *this* owner's own available
    drivers to assign.
    """
    username = request.session["owner_username"]
    bookings = get_bookings_collection()
    drivers = get_drivers_collection()

    pending = [
        serialize_booking(b)
        for b in bookings.find({"status": "requested"}).sort("created_at", 1)
    ]

    my_available_drivers = list(
        drivers.find({"owner_username": username, "status": "available"}).sort("name", 1)
    )
    for d in my_available_drivers:
        d["id"] = str(d["_id"])

    return render(
        request,
        "booking/owner/requests.html",
        {"requests": pending, "available_drivers": my_available_drivers},
    )


@owner_required
def owner_accept_ride_view(request, booking_id):
    if request.method != "POST":
        return redirect("booking:owner_requests")

    username = request.session["owner_username"]
    driver_id = request.POST.get("driver_id", "").strip()

    try:
        booking_oid = ObjectId(booking_id)
        driver_oid = ObjectId(driver_id)
    except InvalidId:
        messages.error(request, "Invalid ride or driver reference.")
        return redirect("booking:owner_requests")

    bookings = get_bookings_collection()
    booking = bookings.find_one({"_id": booking_oid, "status": "requested"})
    if not booking:
        messages.error(request, "This ride has already been accepted by another owner.")
        return redirect("booking:owner_requests")

    driver = get_drivers_collection().find_one(
        {"_id": driver_oid, "owner_username": username, "status": "available"}
    )
    if not driver:
        messages.error(request, "Please choose one of your available drivers.")
        return redirect("booking:owner_requests")

    if _assign_driver_to_booking(booking, driver):
        messages.success(
            request,
            f"Ride accepted! {driver['name']} ({driver['vehicle_number']}) has been assigned.",
        )
    else:
        messages.error(request, "This ride was just accepted by another owner — try the next one.")

    return redirect("booking:owner_requests")


@owner_required
def owner_trips_view(request):
    username = request.session["owner_username"]
    my_vehicle_ids = _owner_vehicle_ids(username)

    bookings = get_bookings_collection()
    all_trips = [
        serialize_booking(b)
        for b in bookings.find({"driver_id": {"$in": my_vehicle_ids}}).sort(
            "created_at", -1
        )
    ]

    return render(
        request,
        "booking/owner/trips.html",
        {"trips": all_trips, "transitions": TRIP_STATUS_TRANSITIONS},
    )


@owner_required
def owner_advance_trip_view(request, booking_id):
    if request.method != "POST":
        return redirect("booking:owner_trips")

    username = request.session["owner_username"]
    try:
        oid = ObjectId(booking_id)
    except InvalidId:
        messages.error(request, "Invalid trip reference.")
        return redirect("booking:owner_trips")

    my_vehicle_ids = _owner_vehicle_ids(username)
    bookings = get_bookings_collection()
    booking = bookings.find_one({"_id": oid, "driver_id": {"$in": my_vehicle_ids}})
    if not booking:
        messages.error(request, "Trip not found or not assigned to your fleet.")
        return redirect("booking:owner_trips")

    next_status = TRIP_STATUS_TRANSITIONS.get(booking["status"])
    if not next_status:
        messages.error(request, f"Can't advance a trip that's currently '{booking['status']}'.")
        return redirect("booking:owner_trips")

    bookings.update_one(
        {"_id": oid},
        {"$set": {"status": next_status, "updated_at": datetime.utcnow()}},
    )

    # Free the vehicle back up once the trip is delivered, and remember
    # where they dropped off so a return-trip discount can find them.
    if next_status == "completed" and booking.get("driver_id"):
        _mark_driver_returning_from(
            booking["driver_id"], booking.get("drop_lat"), booking.get("drop_lng")
        )

    messages.success(request, f"Trip marked as {STATUS_LABELS.get(next_status, next_status)}.")
    return redirect("booking:owner_trips")


# ---------------------------------------------------------------------------
# Driver panel
# ---------------------------------------------------------------------------
# Drivers don't self-register — their owner creates their account (see
# owner_drivers_view above) and hands them a username/password. A driver
# document IS a `drivers` collection entry, so `session["driver_id"]` stores
# its Mongo _id as a string and every query below is scoped to that one
# document — a driver only ever sees their own vehicle and their own trips.

def driver_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get("driver_id"):
            messages.info(request, "Please log in to the driver panel.")
            return redirect("booking:driver_login")
        return view_func(request, *args, **kwargs)

    return wrapper


def _current_driver(request):
    """Fetch the logged-in driver's own document, or None."""
    try:
        oid = ObjectId(request.session["driver_id"])
    except (KeyError, InvalidId):
        return None
    return get_drivers_collection().find_one({"_id": oid})


def driver_login_view(request):
    if request.session.get("driver_id"):
        return redirect("booking:driver_dashboard")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        drivers = get_drivers_collection()
        # Only entries created via the owner panel have login credentials
        # (username/password) — fleet vehicles added straight by admin don't.
        driver = drivers.find_one({"username": username})

        if driver and driver.get("password") and check_password(password, driver["password"]):
            request.session["driver_id"] = str(driver["_id"])
            messages.success(request, f"Welcome back, {driver['name']}!")
            return redirect("booking:driver_dashboard")

        messages.error(request, "Invalid driver credentials.")
        return render(request, "booking/driver/login.html")

    return render(request, "booking/driver/login.html")


def driver_logout_view(request):
    request.session.pop("driver_id", None)
    messages.success(request, "Logged out of the driver panel.")
    return redirect("booking:driver_login")


@driver_required
def driver_dashboard_view(request):
    driver = _current_driver(request)
    if not driver:
        request.session.pop("driver_id", None)
        messages.error(request, "Your driver session expired. Please log in again.")
        return redirect("booking:driver_login")

    bookings = get_bookings_collection()

    active_trip_doc = bookings.find_one(
        {"driver_id": driver["_id"], "status": {"$in": ["driver_assigned", "in_transit"]}}
    )
    active_trip = serialize_booking(active_trip_doc) if active_trip_doc else None

    completed_trips = bookings.count_documents({"driver_id": driver["_id"], "status": "completed"})
    earnings = 0
    for b in bookings.find({"driver_id": driver["_id"], "status": "completed"}, {"fare": 1}):
        earnings += b.get("fare", 0)

    driver["id"] = str(driver["_id"])

    return render(
        request,
        "booking/driver/dashboard.html",
        {
            "driver": driver,
            "active_trip": active_trip,
            "completed_trips": completed_trips,
            "earnings": round(earnings, 2),
            "next_status": TRIP_STATUS_TRANSITIONS.get(active_trip["status"]) if active_trip else None,
        },
    )


@driver_required
def driver_toggle_availability_view(request):
    driver = _current_driver(request)
    if not driver:
        return redirect("booking:driver_login")

    if driver["status"] == "on_trip":
        messages.error(request, "Can't change availability while you're on a trip.")
        return redirect("booking:driver_dashboard")

    new_status = "available" if driver["status"] != "available" else "offline"
    get_drivers_collection().update_one({"_id": driver["_id"]}, {"$set": {"status": new_status}})
    messages.success(request, f"You're now marked as {new_status}.")
    return redirect("booking:driver_dashboard")


@driver_required
def driver_trips_view(request):
    driver = _current_driver(request)
    if not driver:
        return redirect("booking:driver_login")

    bookings = get_bookings_collection()
    all_trips = [
        serialize_booking(b)
        for b in bookings.find({"driver_id": driver["_id"]}).sort("created_at", -1)
    ]

    return render(
        request,
        "booking/driver/trips.html",
        {"trips": all_trips, "transitions": TRIP_STATUS_TRANSITIONS},
    )


@driver_required
def driver_advance_trip_view(request, booking_id):
    if request.method != "POST":
        return redirect("booking:driver_trips")

    driver = _current_driver(request)
    if not driver:
        return redirect("booking:driver_login")

    try:
        oid = ObjectId(booking_id)
    except InvalidId:
        messages.error(request, "Invalid trip reference.")
        return redirect("booking:driver_trips")

    bookings = get_bookings_collection()
    booking = bookings.find_one({"_id": oid, "driver_id": driver["_id"]})
    if not booking:
        messages.error(request, "Trip not found or not assigned to you.")
        return redirect("booking:driver_trips")

    next_status = TRIP_STATUS_TRANSITIONS.get(booking["status"])
    if not next_status:
        messages.error(request, f"Can't advance a trip that's currently '{booking['status']}'.")
        return redirect("booking:driver_trips")

    bookings.update_one(
        {"_id": oid},
        {"$set": {"status": next_status, "updated_at": datetime.utcnow()}},
    )

    # Free yourself back up once the trip is delivered, and remember where
    # you dropped off so a return-trip discount can find you.
    if next_status == "completed":
        _mark_driver_returning_from(
            driver["_id"], booking.get("drop_lat"), booking.get("drop_lng")
        )

    messages.success(request, f"Trip marked as {STATUS_LABELS.get(next_status, next_status)}.")
    return redirect("booking:driver_trips")

