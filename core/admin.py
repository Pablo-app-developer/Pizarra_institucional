"""Administrador técnico (superusuario): crear colegios y su primer directivo."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from django import forms

from .models import MODULOS, Colegio, Usuario


class ColegioAdminForm(forms.ModelForm):
    modulos = forms.MultipleChoiceField(
        label="Módulos activos", required=False, widget=forms.CheckboxSelectMultiple,
        choices=[(clave, f"{nombre}: {detalle}") for clave, nombre, detalle in MODULOS],
        help_text="Lo básico (estudiantes, notas, asistencia, tareas y boletines) siempre está activo.",
    )

    class Meta:
        model = Colegio
        fields = ["nombre", "ciudad", "nit", "modulos"]


@admin.register(Colegio)
class ColegioAdmin(admin.ModelAdmin):
    form = ColegioAdminForm
    list_display = ("nombre", "ciudad", "cuantos_modulos")
    search_fields = ("nombre", "nit")

    @admin.display(description="Módulos")
    def cuantos_modulos(self, obj):
        return f"{len(obj.modulos or [])} de {len(MODULOS)}"

    def get_queryset(self, request):
        return super().get_queryset(request).defer("escudo")


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    list_display = ("username", "first_name", "last_name", "rol", "colegio", "is_active")
    list_filter = ("rol", "colegio", "is_active")
    fieldsets = UserAdmin.fieldsets + (
        ("Colegio", {"fields": ("colegio", "rol", "cargo", "permisos", "documento", "telefono", "debe_cambiar_clave")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Colegio", {"fields": ("colegio", "rol", "first_name", "last_name", "documento")}),
    )
