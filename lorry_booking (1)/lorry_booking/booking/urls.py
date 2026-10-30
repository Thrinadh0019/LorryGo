from django.urls import path

from . import views

app_name = "booking"

urlpatterns = [
    # Customer auth
    path("", views.login_view, name="home"),
    path("register/", views.register_view, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),

    # Customer ride-hailing flow
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("book/", views.book_lorry_view, name="book_lorry"),
    path("api/return-discount-check/", views.return_discount_check_api, name="return_discount_check_api"),
    path("trip/<str:booking_id>/", views.trip_status_view, name="trip_status"),
    path("trip/<str:booking_id>/status.json", views.booking_status_api, name="booking_status_api"),
    path("my-bookings/", views.my_bookings_view, name="my_bookings"),
    path("cancel/<str:booking_id>/", views.cancel_booking_view, name="cancel_booking"),

    # Admin panel
    path("panel/login/", views.admin_login_view, name="admin_login"),
    path("panel/logout/", views.admin_logout_view, name="admin_logout"),
    path("panel/", views.admin_dashboard_view, name="admin_dashboard"),
    path("panel/bookings/", views.admin_bookings_view, name="admin_bookings"),
    path("panel/bookings/<str:booking_id>/update/", views.admin_update_booking_view, name="admin_update_booking"),
    path("panel/drivers/", views.admin_drivers_view, name="admin_drivers"),
    path("panel/drivers/add/", views.admin_add_driver_view, name="admin_add_driver"),
    path("panel/drivers/<str:driver_id>/toggle/", views.admin_toggle_driver_view, name="admin_toggle_driver"),
    path("panel/drivers/<str:driver_id>/delete/", views.admin_delete_driver_view, name="admin_delete_driver"),
    path("panel/users/", views.admin_users_view, name="admin_users"),

    # Lorry owner panel
    path("owner/register/", views.owner_register_view, name="owner_register"),
    path("owner/login/", views.owner_login_view, name="owner_login"),
    path("owner/logout/", views.owner_logout_view, name="owner_logout"),
    path("owner/", views.owner_dashboard_view, name="owner_dashboard"),
    path("owner/drivers/", views.owner_drivers_view, name="owner_drivers"),
    path("owner/drivers/<str:driver_id>/toggle/", views.owner_toggle_driver_view, name="owner_toggle_driver"),
    path("owner/drivers/<str:driver_id>/remove/", views.owner_remove_driver_view, name="owner_remove_driver"),
    path("owner/requests/", views.owner_ride_requests_view, name="owner_requests"),
    path("owner/requests/<str:booking_id>/accept/", views.owner_accept_ride_view, name="owner_accept_ride"),
    path("owner/trips/", views.owner_trips_view, name="owner_trips"),
    path("owner/trips/<str:booking_id>/advance/", views.owner_advance_trip_view, name="owner_advance_trip"),

    # Driver panel (accounts are created by the owner, not self-registered)
    path("driver/login/", views.driver_login_view, name="driver_login"),
    path("driver/logout/", views.driver_logout_view, name="driver_logout"),
    path("driver/", views.driver_dashboard_view, name="driver_dashboard"),
    path("driver/toggle/", views.driver_toggle_availability_view, name="driver_toggle_availability"),
    path("driver/trips/", views.driver_trips_view, name="driver_trips"),
    path("driver/trips/<str:booking_id>/advance/", views.driver_advance_trip_view, name="driver_advance_trip"),
]
