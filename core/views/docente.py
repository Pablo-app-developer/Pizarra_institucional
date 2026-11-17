from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .. import calculos
from ..forms import ActividadForm, ObservacionForm, TareaForm
from ..models import (
    Actividad, Asistencia, Clase, ComentarioBoletin, Entrega, Grupo, Matricula, Nota, Periodo, Tarea,
)
from ..permisos import clase_para, docente_conoce, grupo_para, personal
from .comunes import periodo_vigente


def _matriculas(grupo):
    return list(grupo.matriculas.filter(activa=True).select_related("estudiante"))


def _periodo(request, clase):
    periodos = list(clase.grupo.anio.periodos.all())
    elegido = request.GET.get("periodo") or request.POST.get("periodo")
    periodo = next((p for p in periodos if str(p.pk) == str(elegido)), None)
    return periodos, periodo or periodo_vigente(clase.grupo.anio)


@personal
def mis_clases(request):
    u = request.user
    hoy = timezone.localdate()
    clases = (
        Clase.objects.filter(docente=u, grupo__anio__activo=True)
        .select_related("grupo__grado", "asignatura")
        .annotate(
            n=Count("grupo__matriculas", filter=Q(grupo__matriculas__activa=True), distinct=True),
            tareas_abiertas=Count("tareas", filter=Q(tareas__fecha_entrega__gte=hoy), distinct=True),
        )
    )
    tomadas = set(
        Asistencia.objects.filter(clase__in=clases, fecha=hoy).values_list("clase_id", flat=True)
    )
    for c in clases:
        c.asistencia_hoy = c.id in tomadas
    dirigidos = Grupo.objects.filter(director=u, anio__activo=True).select_related("grado")
    return render(request, "docente/mis_clases.html", {
        "clases": clases, "dirigidos": dirigidos, "hoy": hoy,
        "por_revisar": Tarea.objects.filter(
            clase__docente=u, clase__grupo__anio__activo=True, fecha_entrega__lt=hoy,
            fecha_entrega__gte=hoy - timedelta(days=14),
        ).annotate(n=Count("entregas")).filter(n=0).select_related("clase__asignatura", "clase__grupo__grado")[:6],
    })


# ------------------------------------------------------------------- Planilla


def _leer_nota(texto, colegio):
    """Convierte lo digitado en Decimal. Acepta coma o punto. '' significa sin nota."""
    texto = (texto or "").strip().replace(",", ".")
    if texto == "":
        return None
    try:
        valor = Decimal(texto)
    except InvalidOperation:
        raise ValueError(f"«{texto}» no es un número.")
    if not valor.is_finite() or valor < colegio.nota_minima or valor > colegio.nota_maxima:
        raise ValueError(f"{texto} está fuera de la escala ({colegio.nota_minima} a {colegio.nota_maxima}).")
    return calculos.redondear(valor)


@personal
def planilla(request, pk):
    clase = clase_para(request.user, pk)
    colegio = request.user.colegio
    periodos, periodo = _periodo(request, clase)
    if periodo is None:
        messages.error(request, "El colegio aún no ha definido los periodos de este año.")
        return redirect("mis_clases")
    matriculas = _matriculas(clase.grupo)
    actividades, valores, definitivas = calculos.notas_de_clase(clase, periodo)
    errores = {}

    if request.method == "POST":
        if periodo.cerrado:
            raise PermissionDenied("El periodo está cerrado. Pida a coordinación reabrirlo para corregir notas.")
        existentes = {
            (n.actividad_id, n.matricula_id): n for n in Nota.objects.filter(actividad__in=actividades)
        }
        crear, cambiar, borrar = [], [], []
        for m in matriculas:
            for a in actividades:
                clave = f"n_{m.id}_{a.id}"
                if clave not in request.POST:
                    continue
                try:
                    valor = _leer_nota(request.POST[clave], colegio)
                except ValueError as e:
                    errores[clave] = (request.POST[clave], str(e))
                    continue
                actual = existentes.get((a.id, m.id))
                if valor is None:
                    if actual:
                        borrar.append(actual.pk)
                elif actual is None:
                    crear.append(Nota(actividad=a, matricula=m, valor=valor))
                elif actual.valor != valor:
                    actual.valor = valor
                    cambiar.append(actual)
        if errores:
            # No se guarda nada a medias: el docente corrige y vuelve a guardar.
            messages.error(
                request,
                f"No se guardó la planilla: hay {len(errores)} "
                f"{'nota inválida' if len(errores) == 1 else 'notas inválidas'}, marcadas en rojo.",
            )
            for m in matriculas:
                for a in actividades:
                    clave = f"n_{m.id}_{a.id}"
                    if clave in request.POST and clave not in errores:
                        valores[m.id][a.id] = request.POST[clave]
        else:
            with transaction.atomic():
                Nota.objects.bulk_create(crear)
                for n in cambiar:
                    n.save()
                Nota.objects.filter(pk__in=borrar).delete()
            total = len(crear) + len(cambiar) + len(borrar)
            messages.success(
                request, "Planilla guardada." if total else "No había cambios por guardar."
            )
            return redirect(f"{request.path}?periodo={periodo.pk}")

    filas = []
    for m in matriculas:
        celdas = []
        for a in actividades:
            clave = f"n_{m.id}_{a.id}"
            if clave in errores:
                celdas.append({"nombre": clave, "valor": errores[clave][0], "error": errores[clave][1], "actividad": a.titulo})
            else:
                celdas.append({"nombre": clave, "valor": valores[m.id].get(a.id, ""), "error": "", "actividad": a.titulo})
        filas.append({"matricula": m, "celdas": celdas, "definitiva": definitivas.get(m.id)})

    calificadas = [f["definitiva"] for f in filas if f["definitiva"] is not None]
    suma_pesos = sum(a.peso for a in actividades)
    return render(request, "docente/planilla.html", {
        "clase": clase, "periodos": periodos, "periodo": periodo, "actividades": actividades,
        "filas": filas, "seccion": "notas", "suma_pesos": suma_pesos,
        "pesos_incompletos": suma_pesos not in (0, 100) and bool(actividades),
        "promedio_grupo": calculos.promedio_ponderado([(v, 1) for v in calificadas]),
        "perdiendo": sum(1 for v in calificadas if v < colegio.desde_basico),
        "form_actividad": ActividadForm(),
    })


@personal
def actividad_form(request, pk, actividad_id=None):
    clase = clase_para(request.user, pk)
    periodos, periodo = _periodo(request, clase)
    actividad = get_object_or_404(Actividad, pk=actividad_id, clase=clase) if actividad_id else None
    if actividad:
        periodo = actividad.periodo
    if periodo is None or periodo.cerrado:
        raise PermissionDenied("El periodo está cerrado.")
    form = ActividadForm(request.POST or None, instance=actividad)
    if request.method == "POST" and form.is_valid():
        a = form.save(commit=False)
        a.clase, a.periodo = clase, periodo
        a.save()
        messages.success(request, f"Actividad «{a.titulo}» guardada.")
        return redirect(f"/clases/{clase.pk}/notas/?periodo={periodo.pk}")
    return render(request, "docente/form.html", {
        "clase": clase, "form": form, "seccion": "notas", "obj": actividad,
        "titulo": "Editar actividad" if actividad else f"Nueva actividad de {periodo.nombre.lower()}",
        "cancelar": f"/clases/{clase.pk}/notas/?periodo={periodo.pk}",
        "eliminar": f"/clases/{clase.pk}/actividades/{actividad.pk}/eliminar/" if actividad else None,
        "aviso_eliminar": "Se borrarán también las notas registradas en esta actividad.",
        "periodo": periodo,
    })


@personal
@require_POST
def actividad_eliminar(request, pk, actividad_id):
    clase = clase_para(request.user, pk)
    actividad = get_object_or_404(Actividad, pk=actividad_id, clase=clase)
    if actividad.periodo.cerrado:
        raise PermissionDenied("El periodo está cerrado.")
    periodo_id = actividad.periodo_id
    actividad.delete()
    messages.success(request, "Actividad eliminada.")
    return redirect(f"/clases/{clase.pk}/notas/?periodo={periodo_id}")


# --------------------------------------------------------------------- Tareas


@personal
def tareas(request, pk):
    clase = clase_para(request.user, pk)
    n = len(_matriculas(clase.grupo))
    lista = clase.tareas.annotate(
        entregadas=Count("entregas", filter=Q(entregas__estado__in=["entregada", "tarde"])),
        revisadas=Count("entregas"),
    )
    return render(request, "docente/tareas.html", {
        "clase": clase, "tareas": lista, "n": n, "seccion": "tareas", "hoy": timezone.localdate(),
    })


@personal
def tarea_form(request, pk, tarea_id=None):
    clase = clase_para(request.user, pk)
    tarea = get_object_or_404(Tarea, pk=tarea_id, clase=clase) if tarea_id else None
    form = TareaForm(request.POST or None, instance=tarea)
    if request.method == "POST" and form.is_valid():
        t = form.save(commit=False)
        t.clase = clase
        t.save()
        messages.success(request, f"Tarea «{t.titulo}» guardada. Las familias ya la pueden ver.")
        return redirect("clase_tareas", clase.pk)
    return render(request, "docente/form.html", {
        "clase": clase, "form": form, "seccion": "tareas", "obj": tarea,
        "titulo": "Editar tarea" if tarea else "Nueva tarea",
        "cancelar": f"/clases/{clase.pk}/tareas/",
        "eliminar": f"/clases/{clase.pk}/tareas/{tarea.pk}/eliminar/" if tarea else None,
        "aviso_eliminar": "Se borrará también el registro de quién la entregó.",
    })


@personal
@require_POST
def tarea_eliminar(request, pk, tarea_id):
    clase = clase_para(request.user, pk)
    get_object_or_404(Tarea, pk=tarea_id, clase=clase).delete()
    messages.success(request, "Tarea eliminada.")
    return redirect("clase_tareas", clase.pk)


@personal
def tarea_entregas(request, pk, tarea_id):
    clase = clase_para(request.user, pk)
    tarea = get_object_or_404(Tarea, pk=tarea_id, clase=clase)
    matriculas = _matriculas(clase.grupo)
    actuales = {e.matricula_id: e for e in tarea.entregas.all()}
    estados = {e.value for e in Entrega.Estado}
    if request.method == "POST":
        with transaction.atomic():
            for m in matriculas:
                estado = request.POST.get(f"e_{m.id}", "")
                comentario = request.POST.get(f"c_{m.id}", "").strip()[:240]
                if estado in estados:
                    Entrega.objects.update_or_create(
                        tarea=tarea, matricula=m, defaults={"estado": estado, "comentario": comentario}
                    )
                elif m.id in actuales:
                    actuales[m.id].delete()
        messages.success(request, "Entregas guardadas.")
        return redirect("clase_tareas", clase.pk)
    filas = [{"matricula": m, "entrega": actuales.get(m.id)} for m in matriculas]
    return render(request, "docente/entregas.html", {
        "clase": clase, "tarea": tarea, "filas": filas, "seccion": "tareas",
        "estados": Entrega.Estado.choices,
    })


# ----------------------------------------------------------------- Asistencia


@personal
def asistencia(request, pk):
    clase = clase_para(request.user, pk)
    hoy = timezone.localdate()
    try:
        fecha = date.fromisoformat(request.GET.get("fecha") or request.POST.get("fecha") or "")
    except ValueError:
        fecha = hoy
    if fecha > hoy:
        fecha = hoy
    matriculas = _matriculas(clase.grupo)
    actuales = {
        a.matricula_id: a for a in Asistencia.objects.filter(clase=clase, fecha=fecha)
    }
    estados = {e.value for e in Asistencia.Estado}
    if request.method == "POST":
        with transaction.atomic():
            for m in matriculas:
                estado = request.POST.get(f"a_{m.id}", "P")
                if estado not in estados:
                    estado = "P"
                Asistencia.objects.update_or_create(
                    clase=clase, matricula=m, fecha=fecha, defaults={"estado": estado}
                )
        messages.success(request, f"Asistencia del {fecha:%d/%m/%Y} guardada.")
        return redirect(f"{request.path}?fecha={fecha.isoformat()}")

    acumulado = {}
    for fila in (
        Asistencia.objects.filter(clase=clase, estado__in=["A", "E"]).values("matricula_id")
        .annotate(n=Count("id"))
    ):
        acumulado[fila["matricula_id"]] = fila["n"]
    filas = [{
        "matricula": m, "estado": actuales[m.id].estado if m.id in actuales else "P",
        "faltas": acumulado.get(m.id, 0),
    } for m in matriculas]
    recientes = (
        Asistencia.objects.filter(clase=clase).values("fecha")
        .annotate(faltaron=Count("id", filter=Q(estado__in=["A", "E"])), tarde=Count("id", filter=Q(estado="T")))
        .order_by("-fecha")[:8]
    )
    return render(request, "docente/asistencia.html", {
        "clase": clase, "fecha": fecha, "hoy": hoy, "filas": filas, "ya_tomada": bool(actuales),
        "estados": Asistencia.Estado.choices, "recientes": recientes, "seccion": "asistencia",
    })


# ------------------------------------------------------------- Estudiantes


@personal
def clase_estudiantes(request, pk):
    clase = clase_para(request.user, pk)
    periodos, periodo = _periodo(request, clase)
    definitivas = calculos.notas_de_clase(clase, periodo)[2] if periodo else {}
    faltas = {
        f["matricula_id"]: f["n"] for f in Asistencia.objects.filter(clase=clase, estado__in=["A", "E"])
        .values("matricula_id").annotate(n=Count("id"))
    }
    filas = [{
        "matricula": m, "definitiva": definitivas.get(m.id), "faltas": faltas.get(m.id, 0),
        "acudientes": list(m.estudiante.acudientes.all()),
    } for m in grupo_matriculas_con_acudientes(clase.grupo)]
    return render(request, "docente/estudiantes.html", {
        "clase": clase, "filas": filas, "periodo": periodo, "periodos": periodos, "seccion": "estudiantes",
    })


def grupo_matriculas_con_acudientes(grupo):
    return grupo.matriculas.filter(activa=True).select_related("estudiante").prefetch_related(
        "estudiante__acudientes"
    )


@personal
def observador(request, matricula_id):
    """Observador del estudiante: lo ven y escriben sus docentes y los directivos."""
    u = request.user
    matricula = get_object_or_404(
        Matricula.objects.select_related("estudiante", "grupo__grado", "grupo__anio", "grupo__director"),
        pk=matricula_id, grupo__anio__colegio=u.colegio,
    )
    if not docente_conoce(u, matricula):
        raise PermissionDenied("Este estudiante no pertenece a sus grupos.")
    form = ObservacionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obs = form.save(commit=False)
        obs.matricula, obs.autor = matricula, u
        obs.save()
        messages.success(request, "Observación registrada.")
        return redirect("observador", matricula.pk)
    periodos = list(matricula.grupo.anio.periodos.all())
    filas, promedios, final = calculos.resumen_estudiante(matricula, periodos)
    return render(request, "docente/observador.html", {
        "matricula": matricula, "est": matricula.estudiante, "form": form,
        "observaciones": matricula.observaciones.select_related("autor"),
        "periodos": periodos, "filas": filas, "promedios": promedios, "final": final,
        "asistencia": calculos.resumen_asistencia(matricula),
        "volver": request.GET.get("volver", "") if request.GET.get("volver", "").isdigit() else "",
    })


# --------------------------------------------------------- Dirección de grupo


@personal
def direccion_grupo(request, pk):
    grupo = grupo_para(request.user, pk)
    periodos = list(grupo.anio.periodos.all())
    elegido = request.GET.get("periodo") or request.POST.get("periodo")
    periodo = next((p for p in periodos if str(p.pk) == str(elegido)), None) or periodo_vigente(grupo.anio)
    matriculas = _matriculas(grupo)
    if periodo is None:
        messages.error(request, "El colegio aún no ha definido los periodos de este año.")
        return redirect("mis_clases")
    comentarios = {
        c.matricula_id: c for c in ComentarioBoletin.objects.filter(periodo=periodo, matricula__in=matriculas)
    }
    if request.method == "POST":
        if periodo.cerrado:
            raise PermissionDenied("El periodo está cerrado.")
        with transaction.atomic():
            for m in matriculas:
                texto = request.POST.get(f"c_{m.id}", "").strip()
                if texto:
                    ComentarioBoletin.objects.update_or_create(
                        matricula=m, periodo=periodo, defaults={"texto": texto}
                    )
                elif m.id in comentarios:
                    comentarios[m.id].delete()
        messages.success(request, "Observaciones del boletín guardadas.")
        return redirect(f"{request.path}?periodo={periodo.pk}")

    puestos, _ = calculos.puestos_del_grupo(grupo, periodo)
    colegio = request.user.colegio
    filas = []
    for m in matriculas:
        detalle, proms, _ = calculos.resumen_estudiante(m, periodos=[periodo])
        perdidas = [
            f["clase"].asignatura.nombre for f in detalle
            if f["periodos"][periodo.id] is not None and f["periodos"][periodo.id] < colegio.desde_basico
        ]
        filas.append({
            "matricula": m, "promedio": proms[periodo.id], "puesto": puestos.get(m.id),
            "perdidas": perdidas, "comentario": comentarios[m.id].texto if m.id in comentarios else "",
        })
    return render(request, "docente/direccion_grupo.html", {
        "grupo": grupo, "periodos": periodos, "periodo": periodo, "filas": filas,
    })
