from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health_check, name="health-check"),
    path("health/live/", views.health_check, name="health-live"),
    path("health/ready/", views.health_check, name="health-ready"),
    path("sw.js", views.service_worker, name="service-worker"),
]
