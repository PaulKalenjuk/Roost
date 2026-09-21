from django.contrib import admin
from django.urls import include, path, re_path

from ledger import views as ledger_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", ledger_views.healthz, name="healthz"),
    path("api/", include("ledger.api_urls")),
    # Uploaded receipts/bills, streamed through an authenticated view (works in
    # production too, unlike the DEBUG-only static() helper).
    path("media/<path:path>", ledger_views.serve_media, name="media"),
]

# Single-page app: serve index.html for any non-API/admin/static/media path so
# client-side routing works. Static assets are served by WhiteNoise from
# WHITENOISE_ROOT (backend/spa).
urlpatterns += [
    re_path(
        r"^(?!api/|admin/|static/|media/|healthz).*$",
        ledger_views.spa_index,
        name="spa",
    ),
]
