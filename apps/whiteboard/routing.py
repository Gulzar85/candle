"""WebSocket URL routing for the whiteboard app.

The URL carries the *partnership* ``public_id`` (the same identity used by the
whiteboard page and HTTP API) so all three surfaces resolve the resource through
one consistent path. Business authorization is NOT performed here — the consumer
re-authorizes every connection and re-checks access on each message.
"""

from django.urls import path

from .consumers import WhiteboardConsumer

websocket_urlpatterns = [
    path("ws/whiteboards/<uuid:public_id>/", WhiteboardConsumer.as_asgi()),
]
