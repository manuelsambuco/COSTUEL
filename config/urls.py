from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from challenge import views

urlpatterns = [
    path("", views.home, name="home"),
    path("board/", views.board, name="board"),
    path("add/", views.add, name="add"),
    path("undo/", views.undo, name="undo"),
    path("signup/", views.signup, name="signup"),
    path("login/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("push/subscribe/", views.push_subscribe, name="push_subscribe"),
    path("push/unsubscribe/", views.push_unsubscribe, name="push_unsubscribe"),
    path("push/test/", views.push_test, name="push_test"),
    path("sw.js", views.service_worker, name="service_worker"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("admin/", admin.site.urls),
]
