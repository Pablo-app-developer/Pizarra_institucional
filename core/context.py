"""Menú lateral según el rol y, para directivos, según el cargo."""
from django.urls import reverse

from django.conf import settings

from .models import Aviso, Colegio
from .permisos import estudiantes_de

# (etiqueta, nombre de URL[:argumento], icono, capacidad requerida o None)
MENU_DIRECTIVO = [
    ("Inicio", "panel", "inicio", None),
    ("Estudiantes", "gestion_lista:estudiantes", "personas", "estudiantes"),
    ("Acudientes", "gestion_lista:acudientes", "familia", "estudiantes"),
    ("Docentes", "gestion_lista:docentes", "docente", "docentes"),
    ("Grupos y clases", "grupos", "grupos", "academico"),
    ("Plan de estudios", "plan", "plan", "academico"),
    ("Horarios", "horarios", "horario", "academico"),
    ("Consolidado de notas", "consolidado", "notas", "notas"),
    ("Boletines", "boletines", "boletin", "boletines"),
    ("Pensiones", "cartera", "pension", "pensiones"),
    ("Comunicados", "comunicados", "comunicado", "comunicados"),
    ("Configuración", "configuracion", "ajustes", ("configuracion", "academico", "cierres")),
]
MENU_DOCENTE = [
    ("Mis clases", "mis_clases", "inicio", None),
    ("Mi horario", "mi_horario", "horario", None),
    ("Comunicados", "comunicados", "comunicado", None),
]


# Entradas del menú que dependen de un módulo opcional.
POR_MODULO = {"plan": "plan", "horarios": "horarios", "mi_horario": "horarios", "cartera": "pensiones", "comunicados": "comunicados"}


def navegacion(request):
    usuario = getattr(request, "user", None)
    if not usuario or not usuario.is_authenticated:
        # Pantallas de ingreso: si la instalación atiende un solo colegio, se muestra su identidad.
        unicos = list(Colegio.objects.defer("escudo")[:2])
        return {"colegio_ingreso": unicos[0] if len(unicos) == 1 else None, "correo_activo": settings.CORREO_ACTIVO}
    if not usuario.colegio_id:
        return {}
    items = []
    colegio = usuario.colegio
    if usuario.es_directivo or usuario.es_docente:
        for etiqueta, nombre, icono, cap in MENU_DIRECTIVO if usuario.es_directivo else MENU_DOCENTE:
            if cap and not any(usuario.puede(c) for c in ([cap] if isinstance(cap, str) else cap)):
                continue
            if nombre in POR_MODULO and not colegio.tiene(POR_MODULO[nombre]):
                continue
            ruta, _, arg = nombre.partition(":")
            items.append({"etiqueta": etiqueta, "url": reverse(ruta, args=[arg] if arg else []), "icono": icono})
    else:
        hijos = list(estudiantes_de(usuario))
        for h in hijos:
            items.append({
                "etiqueta": h.nombres.split()[0] if len(hijos) > 1 else "Resumen",
                "url": reverse("familia_resumen", args=[h.pk]), "icono": "inicio",
            })
        if colegio.tiene("comunicados"):
            items.append({"etiqueta": "Comunicados", "url": reverse("comunicados"), "icono": "comunicado"})
    if colegio.tiene("avisos"):
        sin_leer = Aviso.objects.filter(usuario=usuario, leido=False).count()
        items.append({"etiqueta": "Avisos", "url": reverse("avisos"), "icono": "aviso", "contador": sin_leer})

    # El ítem activo es el de URL más larga que sea prefijo de la ruta actual.
    actual, activo = request.path, None
    inicio_directivo = reverse("panel")  # "/colegio/" es prefijo de todo: solo se marca si es exacto
    for it in items:
        if actual == it["url"] or (actual.startswith(it["url"]) and it["url"] not in ("/", inicio_directivo)):
            if activo is None or len(it["url"]) > len(activo["url"]):
                activo = it
    for it in items:
        it["activo"] = it is activo
    capacidades = set(usuario.permisos or []) if usuario.es_directivo else set()
    return {"menu": items, "colegio": colegio, "capacidades": capacidades, "modulos": set(colegio.modulos or [])}
