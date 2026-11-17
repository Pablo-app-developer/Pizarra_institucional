"""Menú lateral según el rol."""
from django.urls import reverse

from .permisos import estudiantes_de

MENU = {
    "directivo": [
        ("Inicio", "panel", "inicio"),
        ("Estudiantes", "gestion_lista:estudiantes", "personas"),
        ("Docentes", "gestion_lista:docentes", "docente"),
        ("Acudientes", "gestion_lista:acudientes", "familia"),
        ("Grupos y clases", "grupos", "grupos"),
        ("Consolidado de notas", "consolidado", "notas"),
        ("Boletines", "boletines", "boletin"),
        ("Comunicados", "comunicados", "comunicado"),
        ("Configuración", "configuracion", "ajustes"),
    ],
    "docente": [
        ("Mis clases", "mis_clases", "inicio"),
        ("Comunicados", "comunicados", "comunicado"),
    ],
}


def navegacion(request):
    usuario = getattr(request, "user", None)
    if not usuario or not usuario.is_authenticated or not usuario.colegio_id:
        return {}
    actual = request.path
    items = []
    if usuario.rol in MENU:
        for etiqueta, nombre, icono in MENU[usuario.rol]:
            if ":" in nombre:
                ruta, arg = nombre.split(":")
                url = reverse(ruta, args=[arg])
            else:
                url = reverse(nombre)
            items.append({"etiqueta": etiqueta, "url": url, "icono": icono})
    else:
        hijos = list(estudiantes_de(usuario))
        for h in hijos:
            items.append({
                "etiqueta": h.nombres.split()[0] if len(hijos) > 1 else "Resumen",
                "url": reverse("familia_resumen", args=[h.pk]), "icono": "inicio",
            })
        items.append({"etiqueta": "Comunicados", "url": reverse("comunicados"), "icono": "comunicado"})
    # El ítem activo es el de URL más larga que sea prefijo de la ruta actual.
    activo = None
    for it in items:
        if actual == it["url"] or (actual.startswith(it["url"]) and it["url"] != "/"):
            if activo is None or len(it["url"]) > len(activo["url"]):
                activo = it
    for it in items:
        it["activo"] = it is activo
    return {"menu": items, "colegio": usuario.colegio}
