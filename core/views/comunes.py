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
from django.contrib.auth import views as auth_views
from django.http import FileResponse, Http404
from django.views.decorators.cache import cache_control

from ..forms import MiCuentaForm
from ..models import (
    Aviso, Colegio, ComentarioBoletin, Comunicado, Entrega, Grupo, Logro, Matricula, Periodo,
    ValoracionDescriptiva,
)
from ..permisos import cualquiera, estudiantes_de, modulo


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
@modulo("comunicados")
def comunicados(request):
    return render(request, "base/comunicados.html", {"comunicados": comunicados_para(request.user)[:50]})


# ---------------------------------------------------------------- Boletín PDF


def logros_de(grado, periodo):
    resultado = {}
    for logro in Logro.objects.filter(grado=grado, periodo_numero=periodo.numero):
        resultado.setdefault(logro.asignatura_id, []).append(logro.texto)
    return resultado


def datos_boletin(matricula, periodo, puestos=None, total=None, logros=None):
    colegio = matricula.grupo.anio.colegio
    periodos = [p for p in matricula.grupo.anio.periodos.all() if p.numero <= periodo.numero]
    filas, promedios, _ = calculos.resumen_estudiante(matricula, periodos)
    if puestos is None:
        puestos, total = calculos.puestos_del_grupo(matricula.grupo, periodo)
    grado = matricula.grupo.grado
    logros = logros if logros is not None else logros_de(grado, periodo)
    descriptivas = {
        v.clase_id: v.texto for v in ValoracionDescriptiva.objects.filter(matricula=matricula, periodo=periodo)
    } if grado.descriptivo else {}
    for f in filas:
        f["notas"] = [f["periodos"][p.id] for p in periodos]
        f["actual"] = f["periodos"][periodo.id]
        f["desempeno"] = colegio.desempeno(f["actual"])
        f["logros"] = logros.get(f["clase"].asignatura_id, [])
        f["valoracion"] = descriptivas.get(f["clase"].id, "")
    comentario = ComentarioBoletin.objects.filter(matricula=matricula, periodo=periodo).first()
    return {
        "matricula": matricula, "est": matricula.estudiante, "grupo": matricula.grupo,
        "periodo": periodo, "periodos": periodos, "filas": filas,
        "promedio": promedios[periodo.id], "desempeno": colegio.desempeno(promedios[periodo.id]),
        "puesto": puestos.get(matricula.id), "total": total,
        "asistencia": calculos.resumen_asistencia(matricula, periodo.fecha_inicio, periodo.fecha_fin),
        "comentario": comentario.texto if comentario else "",
        "descriptivo": grado.descriptivo,
    }


def generar_pdf(matriculas, periodo, colegio):
    from weasyprint import HTML  # se importa aquí: es pesado y solo se usa al generar PDF

    boletines, cache = [], {}
    for m in matriculas:
        if m.grupo_id not in cache:
            cache[m.grupo_id] = (*calculos.puestos_del_grupo(m.grupo, periodo), logros_de(m.grupo.grado, periodo))
        puestos, total, logros = cache[m.grupo_id]
        boletines.append(datos_boletin(m, periodo, puestos, total, logros))
    html = render_to_string("pdf/boletin.html", {
        "boletines": boletines, "colegio": colegio, "periodo": periodo, "emitido": timezone.localdate(),
        "escudo": colegio.escudo_data_uri,
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
    elif u.es_directivo and not u.puede("boletines"):
        raise PermissionDenied
    pdf = generar_pdf([matricula], periodo, u.colegio)
    return _respuesta_pdf(pdf, slugify(f"boletin {matricula.estudiante} {periodo.nombre}"))


@cualquiera
def boletines_grupo(request, grupo_id, periodo_id):
    u = request.user
    grupo = get_object_or_404(
        Grupo.objects.select_related("grado", "anio__colegio", "director"), pk=grupo_id, anio__colegio=u.colegio
    )
    if not (u.puede("boletines") or (u.es_docente and grupo.director_id == u.id)):
        raise PermissionDenied
    periodo = get_object_or_404(Periodo, pk=periodo_id, anio=grupo.anio)
    matriculas = list(grupo.matriculas.filter(activa=True).select_related("estudiante"))
    for m in matriculas:
        m.grupo = grupo
    pdf = generar_pdf(matriculas, periodo, u.colegio)
    return _respuesta_pdf(pdf, slugify(f"boletines {grupo} {periodo.nombre}"))


# ------------------------------------------------------- Cuenta, avisos, escudo


@cualquiera
def mi_cuenta(request):
    form = MiCuentaForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Sus datos quedaron actualizados.")
        return redirect("mi_cuenta")
    return render(request, "registration/mi_cuenta.html", {"form": form})


@cualquiera
@modulo("avisos")
def avisos(request):
    lista = list(Aviso.objects.filter(usuario=request.user)[:60])
    nuevos = [a.pk for a in lista if not a.leido]
    Aviso.objects.filter(pk__in=nuevos).update(leido=True)
    return render(request, "base/avisos.html", {"avisos": lista, "nuevos": set(nuevos)})


@cache_control(public=True, max_age=60 * 60 * 24 * 30)
def escudo(request, pk):
    """El escudo es público (aparece en la pantalla de ingreso). La versión en la URL permite cachearlo."""
    colegio = get_object_or_404(Colegio.objects.only("escudo", "escudo_tipo"), pk=pk)
    if not colegio.escudo:
        raise Http404
    respuesta = HttpResponse(bytes(colegio.escudo), content_type=colegio.escudo_tipo or "image/png")
    respuesta["X-Content-Type-Options"] = "nosniff"
    return respuesta


@cualquiera
def archivo_entrega(request, pk):
    """Descarga del archivo de una tarea: solo el docente de la clase, coordinación y la familia del estudiante."""
    u = request.user
    entrega = get_object_or_404(
        Entrega.objects.select_related("tarea__clase", "matricula"), pk=pk,
        tarea__clase__grupo__anio__colegio=u.colegio,
    )
    if u.es_familia:
        permitido = estudiantes_de(u).filter(pk=entrega.matricula.estudiante_id).exists()
    else:
        permitido = u.puede("notas") or entrega.tarea.clase.docente_id == u.id
    if not permitido:
        raise PermissionDenied
    if not entrega.archivo:
        raise Http404
    try:
        return FileResponse(entrega.archivo.open("rb"), as_attachment=True)
    except FileNotFoundError:
        raise Http404("El archivo ya no está disponible en el servidor.")


class RecuperarClave(auth_views.PasswordResetView):
    template_name = "registration/recuperar.html"
    email_template_name = "registration/recuperar_correo.txt"
    subject_template_name = "registration/recuperar_asunto.txt"
    success_url = "/recuperar/enviado/"


class NuevaClave(auth_views.PasswordResetConfirmView):
    template_name = "registration/recuperar_nueva.html"
    success_url = "/ingresar/?clave=1"

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        usuario = form.user
        if usuario.debe_cambiar_clave:
            usuario.debe_cambiar_clave = False
            usuario.save(update_fields=["debe_cambiar_clave"])
        return respuesta
