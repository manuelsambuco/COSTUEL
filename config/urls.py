from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from challenge import views, views_social as social

urlpatterns = [
    path("", views.home, name="home"),
    path("board/", views.board, name="board"),
    path("add/", views.add, name="add"),
    path("undo/", views.undo, name="undo"),
    path("signup/", views.signup, name="signup"),
    path("profilo/", views.profile, name="profile"),
    path("profilo/password/", views.PasswordChangeView.as_view(), name="password_change"),
    # Amici
    path("amici/", social.friends, name="friends"),
    path("amici/richiesta/", social.friend_request, name="friend_request"),
    path("amici/richiesta/<int:pk>/accetta/", social.friend_accept, name="friend_accept"),
    path("amici/richiesta/<int:pk>/elimina/", social.friend_decline, name="friend_decline"),
    path("amici/<int:user_id>/rimuovi/", social.friend_remove, name="friend_remove"),
    # Gruppi
    path("gruppi/", social.groups, name="groups"),
    path("gruppi/nuovo/", social.group_create, name="group_create"),
    path("gruppi/entra/", social.group_join_by_code, name="group_join_by_code"),
    path("gruppi/<int:pk>/", social.group_detail, name="group_detail"),
    path("gruppi/<int:pk>/esci/", social.group_leave, name="group_leave"),
    path("gruppi/<int:pk>/elimina/", social.group_delete, name="group_delete"),
    path("gruppi/<int:pk>/nuovo-link/", social.group_new_link, name="group_new_link"),
    path("gruppi/<int:pk>/rimuovi/<int:user_id>/", social.group_remove_member, name="group_remove_member"),
    path("g/<str:code>/", social.group_invite, name="group_invite"),
    # Statistiche
    path("statistiche/", social.my_stats, name="stats"),
    path("login/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("push/subscribe/", views.push_subscribe, name="push_subscribe"),
    path("push/unsubscribe/", views.push_unsubscribe, name="push_unsubscribe"),
    path("push/test/", views.push_test, name="push_test"),
    path("sw.js", views.service_worker, name="service_worker"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("admin/", admin.site.urls),
]
