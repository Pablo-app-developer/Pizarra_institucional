"""Plan de estudios, malla, horarios y cierre de año."""
from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .. import calculos, horarios as motor
from ..bitacora import registrar
from ..forms import LogroForm, PlanForm
from ..models import (
    DIAS, AnioLectivo, Bloque, Clase, Grado, Grupo, HorarioClase, Logro, Matricula, Periodo, PlanEstudio,
)
from ..permisos import capacidad, estudiante_para, familia, modulo, personal

# ------------------------------------------------------------ Plan de estudios


def _grado(request):
    grados = Grado.objects.filter(colegio=request.user.colegio)
    return grados, grados.filter(pk=request.GET.get("grado") or request.POST.get("grado") or 0).first() or grados.first()


@capacidad("academico")
@modulo("plan")
def plan(request):
    grados, grado = _grado(request)
    if grado is None:
        return render(request, "academico/plan.html", {"grados": grados, "grado": None})
    form = PlanForm(request.POST or None, grado=grado)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.grado = grado
        item.save()
        messages.success(request, f"{item.asignatura} agregada al plan de {grado}.")
        return redirect(f"{request.path}?grado={grado.pk}")
    conteo = {
        fila["asignatura_id"]: fila["n"]
        for fila in Logro.objects.filter(grado=grado).values("asignatura_id").annotate(n=Count("id"))
    }
    filas = [{"item": p, "logros": conteo.get(p.asignatura_id, 0)} for p in grado.plan.select_related("asignatura")]
    anio = request.user.colegio.anio_activo
    return render(request, "academico/plan.html", {
        "grados": grados, "grado": grado, "filas": filas, "form": form,
        "total_horas": sum(f["item"].horas_semanales for f in filas),
        "grupos": Grupo.objects.filter(anio=anio, grado=grado) if anio else [],
    })


@capacidad("academico")
@require_POST
@modulo("plan")
def plan_quitar(request, pk):
    item = get_object_or_404(PlanEstudio, pk=pk, grado__colegio=request.user.colegio)
    grado_id = item.grado_id
    item.delete()
    messages.success(request, "Asignatura retirada del plan. Las clases ya creadas no se tocan.")
    return redirect(f"/colegio/plan/?grado={grado_id}")


def aplicar_plan(grupo):
    """Crea en el grupo las clases del plan de su grado que aún no existan. Devuelve cuántas creó."""
    existentes = set(grupo.clases.values_list("asignatura_id", flat=True))
    nuevas = [
        Clase(grupo=grupo, asignatura_id=p.asignatura_id, horas_semanales=p.horas_semanales)
        for p in grupo.grado.plan.all() if p.asignatura_id not in existentes
    ]
    Clase.objects.bulk_create(nuevas)
    return len(nuevas)


@capacidad("academico")
@require_POST
@modulo("plan")
def plan_aplicar(request, grado_id):
    grado = get_object_or_404(Grado, pk=grado_id, colegio=request.user.colegio)
    anio = request.user.colegio.anio_activo
    creadas = sum(aplicar_plan(g) for g in Grupo.objects.filter(anio=anio, grado=grado).select_related("grado"))
    if creadas:
        messages.success(request, f"Se crearon {creadas} clases en los grupos de {grado}. Asigne los docentes en Grupos y clases.")
    else:
        messages.success(request, "Los grupos de este grado ya tenían todas las clases del plan.")
    return redirect(f"/colegio/plan/?grado={grado.pk}")


def _puede_editar_logros(usuario, item):
    if usuario.puede("academico"):
        return True
    return usuario.es_docente and Clase.objects.filter(
        docente=usuario, asignatura=item.asignatura, grupo__grado=item.grado, grupo__anio__activo=True
    ).exists()


@personal
@modulo("plan")
def logros(request, pk):
    """Logros de una asignatura en un grado. Los editan coordinación y el docente que la dicta."""
    item = get_object_or_404(
        PlanEstudio.objects.select_related("grado", "asignatura"), pk=pk, grado__colegio=request.user.colegio
    )
    if not _puede_editar_logros(request.user, item):
        raise PermissionDenied("Solo coordinación y el docente de la asignatura editan estos logros.")
    form = LogroForm(request.POST or None)
    if request.method == "POST":
        if "eliminar" in request.POST:
            Logro.objects.filter(pk=request.POST["eliminar"], grado=item.grado, asignatura=item.asignatura).delete()
            messages.success(request, "Logro eliminado.")
            return redirect(request.path)
        if form.is_valid():
            logro = form.save(commit=False)
            logro.grado, logro.asignatura = item.grado, item.asignatura
            logro.save()
            messages.success(request, "Logro agregado. Aparecerá en la malla y en el boletín de ese periodo.")
            return redirect(request.path)
    por_periodo = {}
    for logro in Logro.objects.filter(grado=item.grado, asignatura=item.asignatura):
        por_periodo.setdefault(logro.periodo_numero, []).append(logro)
    return render(request, "academico/logros.html", {
        "item": item, "form": form, "por_periodo": sorted(por_periodo.items()),
    })


@personal
@modulo("plan")
def malla(request, grado_id):
    """Malla curricular del grado, lista para imprimir."""
    grado = get_object_or_404(Grado, pk=grado_id, colegio=request.user.colegio)
    logros_por = {}
    for logro in Logro.objects.filter(grado=grado):
        logros_por.setdefault((logro.asignatura_id, logro.periodo_numero), []).append(logro.texto)
    anio = request.user.colegio.anio_activo
    numeros = sorted(set(anio.periodos.values_list("numero", flat=True))) if anio else [1, 2, 3, 4]
    filas = [{
        "item": p, "periodos": [logros_por.get((p.asignatura_id, n), []) for n in numeros],
    } for p in grado.plan.select_related("asignatura")]
    return render(request, "academico/malla.html", {
        "grado": grado, "filas": filas, "numeros": numeros,
        "total_horas": sum(f["item"].horas_semanales for f in filas),
    })


# -------------------------------------------------------------------- Horarios


@capacidad("academico")
@modulo("horarios")
def horarios(request):
    colegio = request.user.colegio
    anio = colegio.anio_activo
    bloques = Bloque.objects.filter(colegio=colegio)
    grupos = []
    if anio:
        programadas = {
            f["grupo_id"]: f["n"]
            for f in HorarioClase.objects.filter(grupo__anio=anio).values("grupo_id").annotate(n=Count("id"))
        }
        for g in Grupo.objects.filter(anio=anio).select_related("grado"):
            requeridas = sum(g.clases.values_list("horas_semanales", flat=True))
            grupos.append({"grupo": g, "requeridas": requeridas, "programadas": programadas.get(g.id, 0)})
    return render(request, "academico/horarios.html", {
        "anio": anio, "bloques": bloques, "grupos": grupos,
        "franjas": bloques.filter(descanso=False).count() * len(DIAS),
    })


@capacidad("academico")
@require_POST
@modulo("horarios")
def horarios_generar(request):
    anio = request.user.colegio.anio_activo
    if not anio or not motor.bloques_de_clase(request.user.colegio):
        messages.error(request, "Primero defina la jornada escolar: las horas de clase y los descansos.")
        return redirect("horarios")
    with transaction.atomic():
        faltantes = motor.generar(anio)
    if faltantes:
        detalle = "; ".join(f"{c.asignatura} de {c.grupo} ({n} h)" for c, n in faltantes[:6])
        messages.error(
            request,
            f"Se generó el horario, pero no cupieron algunas horas: {detalle}. "
            "Revise que la jornada tenga suficientes bloques o que un docente no esté sobrecargado.",
        )
    else:
        messages.success(request, "Horario generado para todos los grupos, sin cruces de docentes. Puede ajustarlo grupo por grupo.")
    return redirect("horarios")


@capacidad("academico")
@modulo("horarios")
def horario_grupo(request, pk):
    colegio = request.user.colegio
    grupo = get_object_or_404(Grupo.objects.select_related("grado", "anio"), pk=pk, anio__colegio=colegio)
    clases = {c.id: c for c in grupo.clases.select_related("asignatura", "docente")}
    bloques = list(Bloque.objects.filter(colegio=colegio))
    errores = []
    elegido = {(h.dia, h.bloque_id): h.clase_id for h in HorarioClase.objects.filter(grupo=grupo)}

    if request.method == "POST":
        propuesta, elegido = {}, {}
        for b in bloques:
            if b.descanso:
                continue
            for dia, _ in DIAS:
                valor = request.POST.get(f"h_{dia}_{b.id}", "")
                if valor.isdigit() and int(valor) in clases:
                    propuesta[(dia, b.id)] = clases[int(valor)]
                    elegido[(dia, b.id)] = int(valor)
        errores = motor.cruces_de_docente(grupo, propuesta)
        if not errores:
            with transaction.atomic():
                HorarioClase.objects.filter(grupo=grupo).delete()
                HorarioClase.objects.bulk_create([
                    HorarioClase(grupo=grupo, dia=dia, bloque_id=bloque_id, clase=clase)
                    for (dia, bloque_id), clase in propuesta.items()
                ])
            messages.success(request, f"Horario de {grupo} guardado.")
            return redirect("horario_grupo", grupo.pk)

    filas = [{
        "bloque": b,
        "celdas": [{"nombre": f"h_{dia}_{b.id}", "valor": elegido.get((dia, b.id))} for dia, _ in DIAS],
    } for b in bloques]
    cuenta = {}
    for clase_id in elegido.values():
        cuenta[clase_id] = cuenta.get(clase_id, 0) + 1
    resumen = [{"clase": c, "programadas": cuenta.get(c.id, 0)} for c in clases.values()]
    return render(request, "academico/horario_grupo.html", {
        "grupo": grupo, "filas": filas, "dias": DIAS, "clases": clases.values(), "errores": errores,
        "resumen": resumen,
    })


@personal
@modulo("horarios")
def mi_horario(request):
    colegio = request.user.colegio
    entradas = HorarioClase.objects.filter(
        clase__docente=request.user, grupo__anio__activo=True
    ).select_related("clase__asignatura", "grupo__grado", "bloque")
    return render(request, "academico/ver_horario.html", {
        "titulo": "Mi horario", "filas": motor.rejilla(colegio, entradas), "dias": DIAS,
        "modo": "docente", "hay": entradas.exists(),
    })


@familia
@modulo("horarios")
def familia_horario(request, pk):
    est = estudiante_para(request.user, pk)
    matricula = est.matricula_actual()
    if not matricula:
        return render(request, "familia/sin_matricula.html", {"est": est})
    entradas = HorarioClase.objects.filter(grupo=matricula.grupo).select_related(
        "clase__asignatura", "clase__docente", "bloque"
    )
    from .familia import hermanos_de

    return render(request, "familia/horario.html", {
        "est": est, "matricula": matricula, "hermanos": hermanos_de(request.user), "seccion": "horario",
        "filas": motor.rejilla(request.user.colegio, entradas), "dias": DIAS, "hay": entradas.exists(),
    })


# ---------------------------------------------------------------- Cierre de año


def _siguiente_grado(grado):
    return Grado.objects.filter(colegio=grado.colegio, orden__gt=grado.orden).order_by("orden").first()


def _propuesta(datos, colegio, grado, hay_siguiente):
    if not grado.descriptivo and calculos.perdidas(datos, colegio) >= colegio.perdidas_para_reprobar:
        return "reprobado"
    return "promovido" if hay_siguiente else "graduado"


@capacidad("cierres")
def cierre(request):
    colegio = request.user.colegio
    anio = colegio.anio_activo
    if not anio:
        return render(request, "academico/cierre.html", {"anio": None})
    periodos = list(anio.periodos.all())
    matriculas = list(
        Matricula.objects.filter(grupo__anio=anio, activa=True)
        .select_related("estudiante", "grupo__grado").order_by("grupo__grado__orden", "grupo__nombre", "estudiante__apellidos")
    )
    datos, _ = calculos.consolidar(matriculas, periodos, colegio)
    siguiente = {g.id: _siguiente_grado(g) for g in Grado.objects.filter(colegio=colegio)}
    validos = {r.value for r in Matricula.Resultado}

    if request.method == "POST":
        accion = request.POST.get("accion")
        if accion == "guardar":
            cambiadas = []
            for m in matriculas:
                valor = request.POST.get(f"r_{m.id}", "")
                if valor in validos and valor != m.resultado:
                    m.resultado = valor
                    cambiadas.append(m)
            Matricula.objects.bulk_update(cambiadas, ["resultado"])
            messages.success(request, "Resultados del año guardados.")
            return redirect("cierre")
        if accion == "crear":
            try:
                nuevo, resumen = crear_anio_siguiente(anio, matriculas)
            except ValueError as error:
                messages.error(request, str(error))
            else:
                registrar(request, "Creó el año siguiente", f"{nuevo.nombre}: {resumen['matriculados']} matriculados, {resumen['graduados']} graduados")
                messages.success(
                    request,
                    f"Año {nuevo.nombre} creado: {resumen['matriculados']} estudiantes matriculados, "
                    f"{resumen['graduados']} graduados. Revise grupos, docentes y fechas de los periodos, "
                    "y actívelo en Configuración cuando vaya a empezar.",
                )
            return redirect("cierre")

    grupos = {}
    for m in matriculas:
        d = datos[m.id]
        grupos.setdefault(m.grupo, []).append({
            "matricula": m, "final": d["final"], "perdidas": calculos.perdidas(d, colegio),
            "valor": m.resultado or _propuesta(d, colegio, m.grupo.grado, siguiente[m.grupo.grado_id] is not None),
            "guardado": bool(m.resultado),
        })
    return render(request, "academico/cierre.html", {
        "anio": anio, "grupos": grupos.items(), "opciones": Matricula.Resultado.choices,
        "pendientes": sum(1 for m in matriculas if not m.resultado),
        "periodos_abiertos": [p for p in periodos if not p.cerrado],
        "ya_existe": AnioLectivo.objects.filter(colegio=colegio, nombre=_nombre_siguiente(anio)).exists(),
        "siguiente_nombre": _nombre_siguiente(anio),
    })


def _nombre_siguiente(anio):
    return str(int(anio.nombre) + 1) if anio.nombre.isdigit() else f"{anio.nombre} (siguiente)"


def _un_anio_despues(fecha):
    try:
        return fecha.replace(year=fecha.year + 1)
    except ValueError:  # 29 de febrero
        return date(fecha.year + 1, 2, 28)


@transaction.atomic
def crear_anio_siguiente(anio, matriculas):
    """Crea el año siguiente (inactivo) con periodos, grupos, clases y matrículas según los resultados."""
    if any(not m.resultado for m in matriculas):
        raise ValueError("Faltan estudiantes por definir. Guarde el resultado de todos antes de crear el año.")
    nombre = _nombre_siguiente(anio)
    if AnioLectivo.objects.filter(colegio=anio.colegio, nombre=nombre).exists():
        raise ValueError(f"El año {nombre} ya existe.")
    nuevo = AnioLectivo.objects.create(colegio=anio.colegio, nombre=nombre, activo=False)
    Periodo.objects.bulk_create([
        Periodo(anio=nuevo, numero=p.numero, nombre=p.nombre, peso=p.peso,
                fecha_inicio=_un_anio_despues(p.fecha_inicio), fecha_fin=_un_anio_despues(p.fecha_fin))
        for p in anio.periodos.all()
    ])
    # Mismos grupos que este año; las clases salen del plan de estudios o, si no hay, se copian.
    grupos_nuevos = {}
    for g in Grupo.objects.filter(anio=anio).select_related("grado"):
        ng = Grupo.objects.create(anio=nuevo, grado=g.grado, nombre=g.nombre, director=g.director)
        grupos_nuevos[(g.grado_id, g.nombre)] = ng
        if not aplicar_plan(ng):
            Clase.objects.bulk_create([
                Clase(grupo=ng, asignatura_id=c.asignatura_id, docente_id=c.docente_id, horas_semanales=c.horas_semanales)
                for c in g.clases.all()
            ])
        else:
            docentes = dict(g.clases.values_list("asignatura_id", "docente_id"))
            for c in ng.clases.all():
                if docentes.get(c.asignatura_id):
                    c.docente_id = docentes[c.asignatura_id]
                    c.save(update_fields=["docente"])

    def grupo_destino(grado, letra):
        destino = grupos_nuevos.get((grado.id, letra))
        if destino is None:  # el grado siguiente no tenía ese grupo: se usa el primero o se crea
            destino = next((g for (gid, _), g in grupos_nuevos.items() if gid == grado.id), None)
            if destino is None:
                destino = Grupo.objects.create(anio=nuevo, grado=grado, nombre=letra)
                aplicar_plan(destino)
            grupos_nuevos[(grado.id, letra)] = destino
        return destino

    resumen = {"matriculados": 0, "graduados": 0}
    nuevas = []
    for m in matriculas:
        grado = m.grupo.grado
        if m.resultado == "promovido":
            grado = _siguiente_grado(grado)
            if grado is None:
                resumen["graduados"] += 1
                continue
        elif m.resultado == "graduado":
            resumen["graduados"] += 1
            continue
        nuevas.append(Matricula(estudiante=m.estudiante, grupo=grupo_destino(grado, m.grupo.nombre), fecha=nuevo_inicio(nuevo)))
    Matricula.objects.bulk_create(nuevas)
    resumen["matriculados"] = len(nuevas)
    return nuevo, resumen


def nuevo_inicio(anio):
    primero = anio.periodos.order_by("numero").first()
    return primero.fecha_inicio if primero else date.today()
