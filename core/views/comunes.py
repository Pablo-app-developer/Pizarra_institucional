from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify

from .. import calculos
from ..models import ComentarioBoletin, Comunicado, Grupo, Matricula, Periodo
from ..permisos import cualquiera, estudiantes_de


def periodo_vigente(anio):
    """El periodo que contiene hoy; si no hay, el último iniciado o el primero."""
    hoy = timezone.localdate()
    periodos = list(anio.periodos.all())
    for p in periodos:
        if p.fecha_inicio <= hoy <= p.fecha_fin:
            return p
    pasados = [p for p in periodos if p.fecha_inicio <= hoy]
    if pasados:
        return pasados[-1]
    return periodos[0] if periodos else None


@login_required
def inicio(request):
    u = request.user
    if u.debe_cambiar_clave:
        return redirect("cambiar_clave")
    if u.colegio_id is None:
        if u.is_staff:
            return redirect("admin:index")
        raise PermissionDenied("Su cuenta no está asociada a un colegio.")
    if u.es_directivo:
        return redirect("panel")
    if u.es_docente:
        return redirect("mis_clases")
    hijos = list(estudiantes_de(u))
    if len(hijos) == 1:
        return redirect("familia_resumen", hijos[0].pk)
    return render(request, "familia/elegir.html", {"hijos": hijos})


@login_required
def cambiar_clave(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        usuario = form.save()
        usuario.debe_cambiar_clave = False
        usuario.save(update_fields=["debe_cambiar_clave"])
        update_session_auth_hash(request, usuario)
        messages.success(request, "Su contraseña quedó actualizada.")
        return redirect("inicio")
    return render(request, "registration/cambiar_clave.html", {"form": form})


def comunicados_para(usuario):
    qs = Comunicado.objects.filter(colegio=usuario.colegio).select_related("grupo__grado", "autor")
    if usuario.es_directivo:
        return qs
    if usuario.es_docente:
        grupos = Grupo.objects.filter(Q(clases__docente=usuario) | Q(director=usuario))
    else:
        grupos = Grupo.objects.filter(
            matriculas__estudiante__in=estudiantes_de(usuario), matriculas__activa=True
        )
    return qs.filter(Q(grupo__isnull=True) | Q(grupo__in=grupos)).distinct()


@cualquiera
def comunicados(request):
    return render(request, "base/comunicados.html", {"comunicados": comunicados_para(request.user)[:50]})


# ---------------------------------------------------------------- Boletín PDF


def datos_boletin(matricula, periodo, puestos=None, total=None):
    colegio = matricula.grupo.anio.colegio
    periodos = [p for p in matricula.grupo.anio.periodos.all() if p.numero <= periodo.numero]
    filas, promedios, _ = calculos.resumen_estudiante(matricula, periodos)
    if puestos is None:
        puestos, total = calculos.puestos_del_grupo(matricula.grupo, periodo)
    for f in filas:
        f["notas"] = [f["periodos"][p.id] for p in periodos]
        f["actual"] = f["periodos"][periodo.id]
        f["desempeno"] = colegio.desempeno(f["actual"])
    comentario = ComentarioBoletin.objects.filter(matricula=matricula, periodo=periodo).first()
    return {
        "matricula": matricula, "est": matricula.estudiante, "grupo": matricula.grupo,
        "periodo": periodo, "periodos": periodos, "filas": filas,
        "promedio": promedios[periodo.id], "desempeno": colegio.desempeno(promedios[periodo.id]),
        "puesto": puestos.get(matricula.id), "total": total,
        "asistencia": calculos.resumen_asistencia(matricula, periodo.fecha_inicio, periodo.fecha_fin),
        "comentario": comentario.texto if comentario else "",
    }


def generar_pdf(matriculas, periodo, colegio):
    from weasyprint import HTML  # se importa aquí: es pesado y solo se usa al generar PDF

    boletines, cache = [], {}
    for m in matriculas:
        if m.grupo_id not in cache:
            cache[m.grupo_id] = calculos.puestos_del_grupo(m.grupo, periodo)
        puestos, total = cache[m.grupo_id]
        boletines.append(datos_boletin(m, periodo, puestos, total))
    html = render_to_string("pdf/boletin.html", {
        "boletines": boletines, "colegio": colegio, "periodo": periodo, "emitido": timezone.localdate(),
    })
    return HTML(string=html).write_pdf()


def _respuesta_pdf(contenido, nombre):
    r = HttpResponse(contenido, content_type="application/pdf")
    r["Content-Disposition"] = f'inline; filename="{nombre}.pdf"'
    return r


@cualquiera
def boletin_estudiante(request, matricula_id, periodo_id):
    u = request.user
    matricula = get_object_or_404(
        Matricula.objects.select_related("estudiante", "grupo__grado", "grupo__anio__colegio", "grupo__director"),
        pk=matricula_id, grupo__anio__colegio=u.colegio,
    )
    periodo = get_object_or_404(Periodo, pk=periodo_id, anio=matricula.grupo.anio)
    if u.es_familia:
        if not estudiantes_de(u).filter(pk=matricula.estudiante_id).exists():
            raise PermissionDenied
        if not periodo.cerrado:
            raise PermissionDenied("El boletín de este periodo aún no ha sido publicado por el colegio.")
    elif u.es_docente and matricula.grupo.director_id != u.id:
        raise PermissionDenied("Solo el director de grupo puede generar boletines.")
    pdf = generar_pdf([matricula], periodo, u.colegio)
    return _respuesta_pdf(pdf, slugify(f"boletin {matricula.estudiante} {periodo.nombre}"))


@cualquiera
def boletines_grupo(request, grupo_id, periodo_id):
    u = request.user
    grupo = get_object_or_404(
        Grupo.objects.select_related("grado", "anio__colegio", "director"), pk=grupo_id, anio__colegio=u.colegio
    )
    if not (u.es_directivo or (u.es_docente and grupo.director_id == u.id)):
        raise PermissionDenied
    periodo = get_object_or_404(Periodo, pk=periodo_id, anio=grupo.anio)
    matriculas = list(grupo.matriculas.filter(activa=True).select_related("estudiante"))
    for m in matriculas:
        m.grupo = grupo
    pdf = generar_pdf(matriculas, periodo, u.colegio)
    return _respuesta_pdf(pdf, slugify(f"boletines {grupo} {periodo.nombre}"))
