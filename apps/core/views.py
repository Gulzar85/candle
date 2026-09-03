from django.http import JsonResponse
from django.shortcuts import render


def health_check(request):
    return JsonResponse({"status": "ok", "version": "0.1.0"}, status=200)


def home(request):
    return render(request, "pages/home.html", {"page_title": "Home"})
