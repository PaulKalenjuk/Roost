from pathlib import Path

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path, re_path

from ledger import views as ledger_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", ledger_views.healthz, name="healthz"),
    path("api/", include("ledger.api_urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Single-page app: serve index.html for any non-API/admin/static path so
# client-side routing works. Static assets are served by WhiteNoise from
# WHITENOISE_ROOT (backend/spa).
urlpatterns += [
    re_path(
        r"^(?!api/|admin/|static/|media/|healthz).*$",
        ledger_views.spa_index,
        name="spa",
    ),
]
