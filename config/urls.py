from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.defaults import bad_request, page_not_found, permission_denied, server_error

handler400 = bad_request
handler403 = permission_denied
handler404 = page_not_found
handler405 = page_not_found
handler500 = server_error

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("", include("apps.partnerships.urls")),
    path("", include("apps.whiteboard.urls")),
    path("", include("apps.core.urls")),
]

# Serve user-uploaded media in development only. WhiteNoise (STORAGES override
# in production.py) only serves STATIC_URL, never MEDIA_URL; production must
# put a reverse proxy (nginx, etc.) in front of MEDIA_ROOT for /media/.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
