from django.http import JsonResponse
from django.shortcuts import redirect


def home(request):
    """Tiny landing page: send people to the admin."""
    return redirect("/admin/")


def healthz(request):
    return JsonResponse({"status": "ok"})
