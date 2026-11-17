from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import include, path

urlpatterns = [
    path("ingresar/", auth.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("salir/", auth.LogoutView.as_view(), name="logout"),
    path("superadmin/", admin.site.urls),
    path("", include("core.urls")),
]
