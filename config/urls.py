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
    path("", include("apps.core.urls")),
]