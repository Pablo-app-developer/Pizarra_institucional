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
from django.urls import reverse

from ..avisos import avisar_estudiantes, avisar_grupo
from ..forms import ActividadForm, ObservacionForm, TareaForm
from ..models import (
    Actividad, Asistencia, Clase, ComentarioBoletin, Entrega, Grupo, Matricula, Nota, Periodo, PlanEstudio,
    Recuperacion, Tarea, ValoracionDescriptiva,
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
    if clase.grupo.grado.descriptivo:
        return redirect("clase_valoraciones", clase.pk)
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
            por_id = {m.id: m for m in matriculas}
            avisar_estudiantes([
                (por_id[n.matricula_id].estudiante,
                 f"{por_id[n.matricula_id].estudiante.nombres} obtuvo {str(n.valor).replace('.', ',')} en «{n.actividad.titulo}» de {clase.asignatura}.",
                 reverse("familia_notas", args=[por_id[n.matricula_id].estudiante_id]))
                for n in crear + cambiar if n.valor < colegio.desde_basico
            ], f"Nota baja en {clase.asignatura}")
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
        "form_actividad": ActividadForm(), "plan": plan_de(clase),
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
        if tarea is None:
            avisar_grupo(
                clase.grupo,
                lambda e: f"Nueva tarea de {clase.asignatura} para {e.nombres}: «{t.titulo}», se entrega el {t.fecha_entrega:%d/%m/%Y}.",
                lambda e: reverse("familia_tarea", args=[e.pk, t.pk]), f"Nueva tarea de {clase.asignatura}",
            )
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
        nuevas_faltas = []
        with transaction.atomic():
            for m in matriculas:
                estado = request.POST.get(f"e_{m.id}", "")
                comentario = request.POST.get(f"c_{m.id}", "").strip()[:240]
                previa = actuales.get(m.id)
                if estado in estados:
                    Entrega.objects.update_or_create(
                        tarea=tarea, matricula=m, defaults={"estado": estado, "comentario": comentario}
                    )
                    if estado == "no_entregada" and (previa is None or previa.estado != "no_entregada"):
                        nuevas_faltas.append(m.estudiante)
                elif previa is not None and not (previa.respuesta or previa.enlace or previa.archivo):
                    previa.delete()  # nunca se borra lo que envió el estudiante
        avisar_estudiantes([
            (e, f"{e.nombres} no entregó la tarea «{tarea.titulo}» de {clase.asignatura}.",
             reverse("familia_tarea", args=[e.pk, tarea.pk])) for e in nuevas_faltas
        ], f"Tarea sin entregar en {clase.asignatura}")
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
        faltaron = []
        with transaction.atomic():
            for m in matriculas:
                estado = request.POST.get(f"a_{m.id}", "P")
                if estado not in estados:
                    estado = "P"
                Asistencia.objects.update_or_create(
                    clase=clase, matricula=m, fecha=fecha, defaults={"estado": estado}
                )
                if estado == "A" and (m.id not in actuales or actuales[m.id].estado != "A"):
                    faltaron.append(m.estudiante)
        avisar_estudiantes([
            (e, f"{e.nombres} faltó a {clase.asignatura} el {fecha:%d/%m/%Y}. Si tiene excusa, entréguela al director de grupo.",
             reverse("familia_asistencia", args=[e.pk])) for e in faltaron
        ], f"Inasistencia a {clase.asignatura}")
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

    colegio = request.user.colegio
    for m in matriculas:
        m.grupo = grupo
    datos, clases_por_grupo = calculos.consolidar(matriculas, [periodo], colegio)
    puestos, _ = calculos.puestos({m.id: datos[m.id]["promedios"][periodo.id] for m in matriculas})
    filas = []
    for m in matriculas:
        notas = datos[m.id]["clases"]
        perdidas = [
            c.asignatura.nombre for c in clases_por_grupo[grupo.id]
            if notas[c.id]["periodos"][periodo.id] is not None and notas[c.id]["periodos"][periodo.id] < colegio.desde_basico
        ]
        filas.append({
            "matricula": m, "promedio": datos[m.id]["promedios"][periodo.id], "puesto": puestos.get(m.id),
            "perdidas": perdidas, "comentario": comentarios[m.id].texto if m.id in comentarios else "",
        })
    return render(request, "docente/direccion_grupo.html", {
        "grupo": grupo, "periodos": periodos, "periodo": periodo, "filas": filas,
    })


# ------------------------------------------------------------ Recuperaciones


@personal
def recuperaciones(request, pk):
    """Quienes perdieron la asignatura en un periodo y su nota de recuperación."""
    clase = clase_para(request.user, pk)
    colegio = request.user.colegio
    periodos, periodo = _periodo(request, clase)
    if periodo is None:
        return redirect("mis_clases")
    matriculas = _matriculas(clase.grupo)
    _, _, definitivas = calculos.notas_de_clase(clase, periodo)
    actuales = {r.matricula_id: r for r in Recuperacion.objects.filter(clase=clase, periodo=periodo)}
    candidatos = [
        m for m in matriculas
        if m.id in actuales or (definitivas.get(m.id) is not None and definitivas[m.id] < colegio.desde_basico)
    ]
    errores = {}
    if request.method == "POST":
        guardar, borrar = [], []
        for m in candidatos:
            texto = request.POST.get(f"r_{m.id}", "")
            try:
                valor = _leer_nota(texto, colegio)
            except ValueError as e:
                errores[m.id] = (texto, str(e))
                continue
            if valor is None:
                if m.id in actuales:
                    borrar.append(actuales[m.id].pk)
            else:
                guardar.append((m, valor))
        if errores:
            messages.error(request, "No se guardó: hay notas inválidas, marcadas en rojo.")
        else:
            with transaction.atomic():
                for m, valor in guardar:
                    Recuperacion.objects.update_or_create(matricula=m, clase=clase, periodo=periodo, defaults={"valor": valor})
                Recuperacion.objects.filter(pk__in=borrar).delete()
            messages.success(request, "Recuperaciones guardadas.")
            return redirect(f"{request.path}?periodo={periodo.pk}")
    filas = []
    for m in candidatos:
        original = definitivas.get(m.id)
        rec = actuales[m.id].valor if m.id in actuales else None
        filas.append({
            "matricula": m, "original": original,
            "valor": errores[m.id][0] if m.id in errores else (rec if rec is not None else ""),
            "error": errores[m.id][1] if m.id in errores else "",
            "queda": calculos.aplicar_recuperacion(original, rec, colegio.tope_recuperacion),
        })
    return render(request, "docente/recuperaciones.html", {
        "clase": clase, "periodos": periodos, "periodo": periodo, "filas": filas, "seccion": "recuperaciones",
    })


# ------------------------------------------- Valoración descriptiva (preescolar)


@personal
def valoraciones(request, pk):
    clase = clase_para(request.user, pk)
    periodos, periodo = _periodo(request, clase)
    if periodo is None:
        return redirect("mis_clases")
    matriculas = _matriculas(clase.grupo)
    actuales = {v.matricula_id: v for v in ValoracionDescriptiva.objects.filter(clase=clase, periodo=periodo)}
    if request.method == "POST":
        if periodo.cerrado:
            raise PermissionDenied("El periodo está cerrado.")
        with transaction.atomic():
            for m in matriculas:
                texto = request.POST.get(f"v_{m.id}", "").strip()
                if texto:
                    ValoracionDescriptiva.objects.update_or_create(matricula=m, clase=clase, periodo=periodo, defaults={"texto": texto})
                elif m.id in actuales:
                    actuales[m.id].delete()
        messages.success(request, "Valoraciones guardadas.")
        return redirect(f"{request.path}?periodo={periodo.pk}")
    filas = [{"matricula": m, "texto": actuales[m.id].texto if m.id in actuales else ""} for m in matriculas]
    return render(request, "docente/valoraciones.html", {
        "clase": clase, "periodos": periodos, "periodo": periodo, "filas": filas, "seccion": "notas",
    })


def plan_de(clase):
    """El renglón del plan de estudios de esta clase, para enlazar sus logros."""
    return PlanEstudio.objects.filter(grado=clase.grupo.grado, asignatura=clase.asignatura).first()
