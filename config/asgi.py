"""ASGI config for Candle project.

Supports HTTP and WebSocket protocols via Django Channels.

* HTTP      -> Django's ASGI handler.
* WebSocket -> AuthMiddlewareStack (session -> user) + the whiteboard URLRouter.
              Business authorization is performed inside the consumer, not here.
"""

import os

from channels.auth import AuthMiddlewareStack  # type: ignore[import-untyped]
from channels.routing import ProtocolTypeRouter, URLRouter  # type: ignore[import-untyped]
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

# Initialize Django (loads apps/registry) before importing routing modules that
# touch models.
django_asgi_app = get_asgi_application()

from apps.whiteboard import routing as whiteboard_routing  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AuthMiddlewareStack(URLRouter(whiteboard_routing.websocket_urlpatterns)),
    }
)
