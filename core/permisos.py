"""Control de acceso. Toda consulta parte del colegio y del rol del usuario."""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect

from .escala import usar_decimales
from .models import Clase, Colegio, Estudiante, Grupo


def rol_requerido(*roles):
    def decorador(vista):
        @wraps(vista)
        @login_required
        def envoltura(request, *args, **kwargs):
            usuario = request.user
            if usuario.debe_cambiar_clave:
                return redirect("cambiar_clave")
            if usuario.colegio_id is None:
                raise PermissionDenied("Su cuenta no está asociada a un colegio.")
            if roles and usuario.rol not in roles:
                raise PermissionDenied("No tiene permiso para ver esta página.")
            # El escudo (binario) no se carga en cada página: solo cuando un informe lo pide.
            usuario.colegio = Colegio.objects.defer("escudo").get(pk=usuario.colegio_id)
            usar_decimales(usuario.colegio.decimales)
            return vista(request, *args, **kwargs)

        return envoltura

    return decorador


def modulo(nombre):
    """La vista solo existe si el colegio tiene activo ese módulo. Va debajo del decorador de acceso."""
    def decorador(vista):
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            if not request.user.colegio.tiene(nombre):
                raise Http404("Este módulo no está activo para su colegio.")
            return vista(request, *args, **kwargs)

        return envoltura

    return decorador


directivo = rol_requerido("directivo")


def capacidad(*capacidades):
    """Directivo que tiene al menos uno de los permisos indicados."""
    def decorador(vista):
        @wraps(vista)
        @directivo
        def envoltura(request, *args, **kwargs):
            if not any(request.user.puede(c) for c in capacidades):
                raise PermissionDenied("Su cuenta no tiene permiso para esta sección. Pídalo a rectoría o a sistemas.")
            return vista(request, *args, **kwargs)

        return envoltura

    return decorador


personal = rol_requerido("directivo", "docente")
familia = rol_requerido("acudiente", "estudiante")
cualquiera = rol_requerido()


def clase_para(usuario, pk):
    """La clase, si el usuario la dicta o es directivo del mismo colegio."""
    clase = get_object_or_404(
        Clase.objects.select_related("grupo__grado", "grupo__anio", "asignatura", "docente"),
        pk=pk, grupo__anio__colegio=usuario.colegio,
    )
    if usuario.puede("notas") or (usuario.es_docente and clase.docente_id == usuario.id):
        return clase
    raise PermissionDenied("Esta clase está asignada a otro docente.")


def grupo_para(usuario, pk):
    grupo = get_object_or_404(
        Grupo.objects.select_related("grado", "anio", "director"), pk=pk, anio__colegio=usuario.colegio
    )
    if usuario.puede("notas") or (usuario.es_docente and grupo.director_id == usuario.id):
        return grupo
    raise PermissionDenied("Usted no dirige este grupo.")


def estudiantes_de(usuario):
    """Estudiantes que un acudiente o estudiante puede consultar."""
    base = Estudiante.objects.filter(colegio=usuario.colegio, activo=True)
    if usuario.rol == "acudiente":
        return base.filter(acudientes=usuario).distinct()
    if usuario.rol == "estudiante":
        return base.filter(usuario=usuario)
    return base.none()


def estudiante_para(usuario, pk):
    try:
        return estudiantes_de(usuario).get(pk=pk)
    except Estudiante.DoesNotExist:
        raise Http404("Estudiante no encontrado.")


def docente_conoce(usuario, matricula):
    """Un docente solo ve estudiantes de grupos donde dicta clase o que dirige."""
    if usuario.es_directivo:
        return usuario.puede("notas")
    grupo = matricula.grupo
    return grupo.director_id == usuario.id or grupo.clases.filter(docente=usuario).exists()
