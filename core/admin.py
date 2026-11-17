"""Administrador técnico (superusuario): crear colegios y su primer directivo."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Colegio, Usuario


@admin.register(Colegio)
class ColegioAdmin(admin.ModelAdmin):
    list_display = ("nombre", "ciudad", "nit")
    search_fields = ("nombre", "nit")


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    list_display = ("username", "first_name", "last_name", "rol", "colegio", "is_active")
    list_filter = ("rol", "colegio", "is_active")
    fieldsets = UserAdmin.fieldsets + (
        ("Colegio", {"fields": ("colegio", "rol", "documento", "telefono", "debe_cambiar_clave")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Colegio", {"fields": ("colegio", "rol", "first_name", "last_name", "documento")}),
    )
