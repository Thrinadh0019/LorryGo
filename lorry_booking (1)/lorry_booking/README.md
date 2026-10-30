# LorryGo — Uber/Ola-style Lorry Booking App (Django + MongoDB)

A ride-hailing style lorry booking app with **four separate interfaces**:

| Role | What they do | Login |
|---|---|---|
| **Customer** | Drop pickup/drop pins on a live map, get fare estimates, book, track the trip in real time | `/login/` |
| **Admin** | Oversee all bookings/drivers/customers, manually update trip status | `/panel/login/` |
| **Lorry Owner** | Register a fleet, **add drivers and issue them login credentials**, watch trips & earnings | `/owner/login/` |
| **Driver** | Log in with the credentials their owner gave them, see their assigned trip, mark it in-transit / delivered | `/driver/login/` |

Backed entirely by **MongoDB** — database **`VTR`** at
`mongodb://localhost:27017/` — via `pymongo`, with no Django ORM models and
no `django.contrib.auth`.

## How rides get matched now: owners accept, they don't get auto-assigned

1. A **lorry owner** registers at `/owner/register/`.
2. From their **My Drivers** page (`/owner/drivers/`), the owner fills in the
   driver's name, phone, vehicle number, **and sets a driver username +
   password** (no lorry type to pick — see below). That single form creates
   one `drivers` collection document that's *both* the vehicle listing *and*
   the driver's login account (`booking/views.py::owner_drivers_view`).
3. The owner gets a one-time on-screen confirmation with the password to
   pass along to the driver (nothing else displays it again — same pattern
   as a typical "invite a teammate" flow).
4. The **driver** logs in separately at `/driver/login/` with those
   credentials. They only ever see their own vehicle and their own trips —
   every query in the driver views is scoped to `session["driver_id"]`.
5. When a customer books a lorry, **nothing gets auto-assigned**. The
   booking just sits at `status="requested"`.
6. Every owner sees it on their **Ride Requests** page (`/owner/requests/`)
   — like an incoming-request list. To accept, the owner picks one of
   *their own* currently-available drivers from a dropdown and hits
   Accept (`owner_accept_ride_view`). That atomically flips the booking to
   `driver_assigned` and the chosen driver to `on_trip` — guarded so a
   second owner can't accept the same ride a moment later (checked in
   `test_flow.py`'s race-condition test).
7. From there, **either the driver or their owner** can mark the trip "in
   transit" then "delivered"; whoever does it first, the other panel just
   reflects the updated status. On delivery the driver is freed back to
   `available`.

Admin-added drivers (`/panel/drivers/`) don't get login credentials and
have no `owner_username`, so they're the "LorryGo Fleet" baseline — they
won't show up in any owner's Accept dropdown. If you want fleet vehicles to
also take rides, that'd need a similar accept flow added to the admin panel
(not currently wired up, since the ask here was specifically the owner
side).

**Note on lorry type:** adding a driver no longer asks for a lorry type —
that field only exists on the *customer's* booking now (for the vehicle
picker + fare calculation), shown to owners as context on each ride request
but not used to filter which drivers can accept it. Any available driver
can be assigned to any ride.

## Return-trip discount (50% off)

When a driver completes a delivery, we remember where they just dropped
off (`last_drop_lat`/`last_drop_lng` on their `drivers` doc, set in
`_mark_driver_returning_from()`) as they're freed back to `available`. If a
**new** booking's pickup point falls within **50km** of that drop-off point,
the app assumes the truck is heading back that way anyway and automatically
prices the ride at **half the normal fare** — see
`find_nearby_returning_driver()` and the discount logic in
`book_lorry_view()`, both in `booking/views.py`.

- **Live preview while booking:** the map page polls a small JSON endpoint
  (`/api/return-discount-check/`) every time the pickup pin is set or
  dragged, showing a green "🎉 50% off" banner and updating each vehicle
  card's price (with the original struck through) *before* the customer
  submits. That preview is just for UX — the discount is always
  recalculated authoritatively server-side when the booking is actually
  created, so the client can't be trusted to set its own price.
- **Visible everywhere, not hidden:** unlike the (unrelated) platform
  commission feature from an earlier version of this app, this is a plain
  promotional discount, so it's shown on the customer's trip page and "My
  Bookings" list, the owner's Ride Requests and Trip history, and the
  admin's bookings views — everyone can see *why* a fare came in low.
- **Tunable constants** at the top of `booking/views.py`:
  `RETURN_TRIP_DISCOUNT_RADIUS_KM = 50` and `RETURN_TRIP_DISCOUNT_RATE = 0.5`.
- A booking that doesn't qualify stores `is_return_discount: False` and
  `original_fare: None`; a qualifying one stores the pre-discount amount in
  `original_fare` and the distance to the matched driver in
  `return_discount_distance_km`.
- Once that "returning" driver gets assigned to a new trip (their status
  flips to `on_trip`), they stop counting as available/returning for any
  further discount matches — same as any other busy driver.

## Collections in `VTR`

| Collection | Purpose |
|---|---|
| `users` | Customer accounts — username, email, hashed password |
| `admins` | Admin-panel accounts — username, hashed password |
| `owners` | Lorry-owner accounts — username, phone, hashed password |
| `drivers` | Vehicle **+** driver in one document: name, phone, vehicle_number, status (`available` / `on_trip` / `offline`), rating, `owner_username` (who added them, `None` for admin-added fleet vehicles), `last_drop_lat`/`last_drop_lng`/`last_drop_at` (where/when they last completed a trip — powers the return-trip discount), and — only for owner-added drivers — `username`/`password` login fields |
| `bookings` | Full trip record — pickup/drop coords + addresses, lorry_type (customer's choice, informational), `fare`, `is_return_discount`, `original_fare` (pre-discount amount, `None` if not discounted), `return_discount_distance_km`, distance_km, status, driver snapshot, timestamps |

Booking `status` values: `requested` → `driver_assigned` (via an owner
accepting) → `in_transit` → `completed` (or `cancelled` at any point before
completion).

## Project layout

```
lorry_booking/
├── manage.py
├── requirements.txt
├── lorry_booking/settings.py       # MONGO_HOST / MONGO_PORT / MONGO_DB_NAME
└── booking/
    ├── mongo.py                     # shared pymongo client + collection helpers
    ├── views.py                     # customer + admin + owner + driver views, fare calc, matching
    ├── urls.py
    ├── management/commands/
    │   ├── create_admin.py          # `python manage.py create_admin --username x --password y`
    │   └── seed_drivers.py          # `python manage.py seed_drivers` — sample admin-owned fleet
    └── templates/booking/
        ├── login.html, register.html, dashboard.html
        ├── book_lorry.html          # the map + vehicle picker
        ├── trip_status.html         # live tracking page
        ├── my_bookings.html
        ├── admin/                   # admin panel (own base_admin.html)
        ├── owner/                   # owner panel: register, drivers (add + credentials), trips
        └── driver/                  # driver panel: login, dashboard, trip management
```

## Setup

1. **Start MongoDB locally:**
   ```bash
   mongod --dbpath /path/to/your/data
   ```
   `VTR` and its collections are created automatically on first write.

2. **Install dependencies:**
   ```bash
   python -m venv venv
   source venv/bin/activate        # venv\Scripts\activate on Windows
   pip install -r requirements.txt
   ```

3. **Run Django's own migrations** (only needed for the session table):
   ```bash
   python manage.py migrate
   ```

4. **Create an admin account:**
   ```bash
   python manage.py create_admin --username admin --password changeme123
   ```

5. **Seed a few demo fleet drivers** (optional, admin-owned, no login) so
   bookings can auto-match even before any owner signs up:
   ```bash
   python manage.py seed_drivers
   ```

6. **Run the app:**
   ```bash
   python manage.py runserver
   ```

7. Try the owner → driver flow yourself:
   - Register an owner at `/owner/register/`
   - Add a driver (name, phone, vehicle number, and a username/password) at
     `/owner/drivers/`
   - Open a private/incognito window and log in as that driver at `/driver/login/`
   - Book a lorry as a customer at `/book/` — it'll sit as "requested"
   - Back in the owner window, go to `/owner/requests/`, pick that driver
     from the dropdown, and hit Accept
   - Advance the trip from either the driver's or the owner's panel and
     watch `/trip/<id>/` update on the customer side

## Where to go

| Page | URL |
|---|---|
| Customer login / register | `/login/`, `/register/` |
| Book a lorry (map) | `/book/` |
| Live return-discount check (used by the map JS) | `/api/return-discount-check/?lat=..&lng=..` |
| Track a trip | `/trip/<booking_id>/` |
| My bookings | `/my-bookings/` |
| **Admin panel login** | `/panel/login/` |
| Manage bookings / drivers / customers | `/panel/bookings/`, `/panel/drivers/`, `/panel/users/` |
| **Owner panel login / register** | `/owner/login/`, `/owner/register/` |
| Owner: **accept ride requests & assign a driver** | `/owner/requests/` |
| Owner: add drivers & set their credentials | `/owner/drivers/` |
| Owner: trip history | `/owner/trips/` |
| **Driver panel login** | `/driver/login/` (no self-registration — owner-issued only) |
| Driver: dashboard (active trip, availability toggle) | `/driver/` |
| Driver: trip history | `/driver/trips/` |

## Notes on the maps

- Map tiles: OpenStreetMap (no API key needed).
- Address search: Nominatim's public API (no key, called directly from the
  browser — be mindful of their usage policy if you deploy this for real
  traffic; for production use, run your own Nominatim instance or switch to
  a paid geocoder).
- Routing: the public OSRM demo server (`router.project-osrm.org`) — free,
  no key, but rate-limited and not meant for production load. For a real
  deployment, self-host OSRM or use a commercial routing API (Google
  Directions, Mapbox, etc.) and swap the `fetch()` URL in `book_lorry.html`
  / `trip_status.html`.

## Testing note

This app was verified end-to-end with `mongomock` (an in-memory MongoDB
simulator), including the full ride-request chain: owner registers → owner
adds a driver with no lorry-type prompt (credentials hashed, duplicate
usernames rejected) → driver logs in with those exact credentials →
customer books a ride, which stays `requested` with **no auto-assignment**
→ the ride appears on the owner's Ride Requests page → owner accepts and
picks that driver from the dropdown → booking flips to `driver_assigned`
and the driver to `on_trip` → a **second owner cannot accept the same
already-accepted ride** (explicit race-condition test) → the driver
advances the trip `in_transit` → `completed` from their own panel →
vehicle is freed → both the driver's and owner's dashboards/trip history
reflect it correctly. The original customer and admin flows (register →
login → book → track → cancel; admin login → update status → manage
drivers/customers, also without a lorry-type prompt) still pass too.

The return-trip discount was verified separately: a booking made before any
driver has completed a trip nearby gets no discount; completing a trip
correctly records the driver's drop-off point and frees them; the live
`/api/return-discount-check/` endpoint correctly returns eligible for a
pickup ~0.6km from that point and not-eligible for one clear across the
country; a new booking placed near that point gets exactly 50% off with the
original fare preserved in `original_fare`; a booking far away stays full
price; the discount is visible on both the customer's trip page and the
owner's Ride Requests page; and once that driver is assigned to the
discounted trip, they correctly stop being offered as "returning" for any
further bookings.

Point it at a real `mongod` and it behaves the same way — the only things
that need a real browser are the map rendering, geolocation prompt, and the
OSRM/Nominatim calls, since those run client-side.
