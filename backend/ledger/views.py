from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.static import serve as static_serve


def healthz(request):
    return JsonResponse({"status": "ok", "app": "roost"})


def spa_index(request):
    """Serve the built single-page app (or a build hint if it's missing)."""
    index = Path(settings.SPA_ROOT) / "index.html"
    if index.exists():
        return HttpResponse(index.read_text(encoding="utf-8"))
    return HttpResponse(
        "<h1>Roost</h1><p>The front-end hasn't been built. Run "
        "<code>npm install &amp;&amp; npm run build</code> in <code>frontend/</code>, "
        "or use the Django admin at <a href='/admin/'>/admin/</a>.</p>",
        content_type="text/html",
        status=200,
    )


def serve_media(request, path):
    """Serve uploaded receipts/bills — **authenticated only**.

    WhiteNoise serves the built SPA, but media needs a login check (these are
    private financial documents), so we stream it through Django instead.
    """
    if not request.user.is_authenticated:
        # Don't reveal whether the file exists.
        return JsonResponse({"detail": "Authentication required."}, status=403)
    return static_serve(request, path, document_root=settings.MEDIA_ROOT)
