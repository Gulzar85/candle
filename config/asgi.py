"""ASGI config for Candle project.

Supports HTTP and WebSocket protocols via Django Channels.
"""
import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from django.urls import path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # WebSocket routing will be added in Phase 1+
        # "websocket": URLRouter([
        #     path("ws/", ...),
        # ]),
    }
)
