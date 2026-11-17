from collections import defaultdict
from datetime import timedelta

from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from .. import calculos
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect

from ..forms import EntregaEnLineaForm
from ..models import Actividad, Asistencia, Entrega, Logro, Nota, Recuperacion, Tarea, ValoracionDescriptiva
from ..permisos import estudiante_para, estudiantes_de, familia
from .comunes import comunicados_para, periodo_vigente


def hermanos_de(usuario):
    return list(estudiantes_de(usuario))


def _base(request, pk):
    """Estudiante, su matrícula vigente y lo común a todas las pantallas de familia."""
    est = estudiante_para(request.user, pk)
    matricula = est.matricula_actual()
    contexto = {"est": est, "matricula": matricula, "hermanos": list(estudiantes_de(request.user))}
    if matricula:
        contexto["periodos"] = list(matricula.grupo.anio.periodos.all())
        elegido = request.GET.get("periodo")
        contexto["periodo"] = next(
            (p for p in contexto["periodos"] if str(p.pk) == elegido), None
        ) or periodo_vigente(matricula.grupo.anio)
    return est, matricula, contexto


def _tareas(matricula):
    """Tareas del grupo con el estado de entrega de este estudiante."""
    tareas = list(
        Tarea.objects.filter(clase__grupo=matricula.grupo)
        .select_related("clase__asignatura", "clase__docente").order_by("fecha_entrega")
    )
    entregas = {e.tarea_id: e for e in Entrega.objects.filter(matricula=matricula)}
    hoy = timezone.localdate()
    for t in tareas:
        t.entrega = entregas.get(t.id)
        if t.entrega:
            t.situacion = t.entrega.estado  # incluye "enviada": el estudiante la mandó y falta revisarla
        elif t.fecha_entrega >= hoy:
            t.situacion = "pendiente"
        else:
            t.situacion = "sin_revisar"
        t.dias = (t.fecha_entrega - hoy).days
    return tareas


@familia
def resumen(request, pk):
    est, matricula, ctx = _base(request, pk)
    if not matricula:
        return render(request, "familia/sin_matricula.html", ctx)
    periodo = ctx["periodo"]
    colegio = request.user.colegio
    hoy = timezone.localdate()
    if periodo:
        filas, promedios, _ = calculos.resumen_estudiante(matricula, periodos=[periodo])
        for f in filas:
            f["nota"] = f["periodos"][periodo.id]
        ctx["filas"] = filas
        ctx["promedio"] = promedios[periodo.id]
        ctx["por_reforzar"] = [f for f in filas if f["nota"] is not None and f["nota"] < colegio.desde_basico]
        ctx["asistencia"] = calculos.resumen_asistencia(matricula, periodo.fecha_inicio, periodo.fecha_fin)
    tareas = _tareas(matricula)
    ctx["proximas"] = [t for t in tareas if t.situacion == "pendiente"][:5]
    ctx["no_entregadas"] = [
        t for t in tareas if t.situacion == "no_entregada" and t.fecha_entrega >= hoy - timedelta(days=30)
    ][:5]
    ctx["observaciones"] = matricula.observaciones.filter(visible_familia=True).select_related("autor")[:3]
    ctx["comunicados"] = comunicados_para(request.user)[:3]
    ctx["ultimas_notas"] = (
        Nota.objects.filter(matricula=matricula)
        .select_related("actividad__clase__asignatura").order_by("-actualizada")[:5]
    )
    ctx["seccion"] = "resumen"
    return render(request, "familia/resumen.html", ctx)


@familia
def notas(request, pk):
    est, matricula, ctx = _base(request, pk)
    if not matricula or not ctx["periodo"]:
        return render(request, "familia/sin_matricula.html", ctx)
    periodo = ctx["periodo"]
    filas, promedios, _ = calculos.resumen_estudiante(matricula, periodos=[periodo])
    actividades = defaultdict(list)
    valores = {n.actividad_id: n.valor for n in Nota.objects.filter(matricula=matricula, actividad__periodo=periodo)}
    for a in Actividad.objects.filter(clase__grupo=matricula.grupo, periodo=periodo):
        actividades[a.clase_id].append({"actividad": a, "valor": valores.get(a.id)})
    recuperadas = {r.clase_id: r.valor for r in Recuperacion.objects.filter(matricula=matricula, periodo=periodo)}
    grado = matricula.grupo.grado
    logros = {}
    for logro in Logro.objects.filter(grado=grado, periodo_numero=periodo.numero):
        logros.setdefault(logro.asignatura_id, []).append(logro.texto)
    descriptivas = {
        v.clase_id: v.texto for v in ValoracionDescriptiva.objects.filter(matricula=matricula, periodo=periodo)
    } if grado.descriptivo else {}
    for f in filas:
        f["nota"] = f["periodos"][periodo.id]
        f["actividades"] = actividades.get(f["clase"].id, [])
        f["recuperacion"] = recuperadas.get(f["clase"].id)
        f["logros"] = logros.get(f["clase"].asignatura_id, [])
        f["valoracion"] = descriptivas.get(f["clase"].id, "")
    ctx.update({"filas": filas, "promedio": promedios[periodo.id], "seccion": "notas", "descriptivo": grado.descriptivo})
    return render(request, "familia/notas.html", ctx)


@familia
def tareas(request, pk):
    est, matricula, ctx = _base(request, pk)
    if not matricula:
        return render(request, "familia/sin_matricula.html", ctx)
    todas = _tareas(matricula)
    ctx["pendientes"] = [t for t in todas if t.situacion == "pendiente"]
    ctx["anteriores"] = sorted(
        (t for t in todas if t.situacion != "pendiente"), key=lambda t: t.fecha_entrega, reverse=True
    )[:40]
    ctx["detalle"] = True
    ctx["seccion"] = "tareas"
    return render(request, "familia/tareas.html", ctx)


@familia
def asistencia(request, pk):
    est, matricula, ctx = _base(request, pk)
    if not matricula:
        return render(request, "familia/sin_matricula.html", ctx)
    ctx["total"] = calculos.resumen_asistencia(matricula)
    ctx["novedades"] = (
        Asistencia.objects.filter(matricula=matricula).exclude(estado="P")
        .select_related("clase__asignatura").order_by("-fecha")[:60]
    )
    ctx["seccion"] = "asistencia"
    return render(request, "familia/asistencia.html", ctx)


@familia
def observaciones(request, pk):
    est, matricula, ctx = _base(request, pk)
    if not matricula:
        return render(request, "familia/sin_matricula.html", ctx)
    ctx["observaciones"] = matricula.observaciones.filter(visible_familia=True).select_related("autor")
    ctx["seccion"] = "observaciones"
    return render(request, "familia/observaciones.html", ctx)


@familia
def boletines(request, pk):
    est, matricula, ctx = _base(request, pk)
    if not matricula:
        return render(request, "familia/sin_matricula.html", ctx)
    ctx["seccion"] = "boletines"
    return render(request, "familia/boletines.html", ctx)


@familia
def tarea(request, pk, tarea_id):
    """Detalle de una tarea. Si el docente la recibe por la plataforma, aquí se envía."""
    est, matricula, ctx = _base(request, pk)
    if not matricula:
        return render(request, "familia/sin_matricula.html", ctx)
    t = get_object_or_404(
        Tarea.objects.select_related("clase__asignatura", "clase__docente"), pk=tarea_id, clase__grupo=matricula.grupo
    )
    entrega = Entrega.objects.filter(tarea=t, matricula=matricula).first()
    # Se puede enviar o corregir el envío mientras el docente no la haya calificado.
    puede_enviar = t.entrega_en_linea and (entrega is None or entrega.estado == "enviada")
    form = EntregaEnLineaForm(request.POST or None, request.FILES or None) if puede_enviar else None
    if form is not None and request.method == "POST" and form.is_valid():
        datos = form.cleaned_data
        if entrega is None:
            entrega = Entrega(tarea=t, matricula=matricula)
        entrega.estado = "enviada"
        entrega.respuesta, entrega.enlace = datos["respuesta"], datos["enlace"]
        entrega.enviada = timezone.now()
        if datos["archivo"]:
            if entrega.archivo:
                entrega.archivo.delete(save=False)
            entrega.archivo = datos["archivo"]
        entrega.save()
        messages.success(request, "Tarea enviada. El docente la revisará y aquí verá el resultado.")
        return redirect("familia_tarea", est.pk, t.pk)
    if form is not None and request.method != "POST" and entrega:
        form = EntregaEnLineaForm(initial={"respuesta": entrega.respuesta, "enlace": entrega.enlace})
    ctx.update({
        "t": t, "entrega": entrega, "form": form, "seccion": "tareas",
        "tarde": t.fecha_entrega < timezone.localdate(),
    })
    return render(request, "familia/tarea.html", ctx)
