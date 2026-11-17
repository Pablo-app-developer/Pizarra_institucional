from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, ProtectedError, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .. import calculos, forms
from ..importar import importar_estudiantes, plantilla_xlsx
from ..models import (
    Acudiencia, AnioLectivo, Asignatura, Asistencia, Clase, Comunicado, Estudiante, Grado,
    Grupo, Matricula, Nota, Periodo, Tarea, Usuario,
)
from ..permisos import directivo
from .comunes import periodo_vigente

# --------------------------------------------------------------------------
# Gestión genérica: una sola pareja de plantillas sirve a todos los catálogos.
# --------------------------------------------------------------------------


def _grupo_de(est):
    m = est.matricula_actual()
    return str(m.grupo) if m else "Sin matricular"


ENTIDADES = {
    "estudiantes": dict(
        modelo=Estudiante, form=forms.EstudianteForm, singular="estudiante", plural="Estudiantes",
        nuevo="Nuevo estudiante",
        qs=lambda c: Estudiante.objects.filter(colegio=c),
        buscar=["nombres", "apellidos", "documento"],
        columnas=[
            ("Estudiante", str), ("Documento", lambda o: f"{o.tipo_documento} {o.documento}"),
            ("Grupo", _grupo_de), ("Estado", lambda o: "Activo" if o.activo else "Retirado"),
        ],
        detalle="ficha_estudiante",
        vacio="Aún no hay estudiantes. Créelos uno a uno o impórtelos desde Excel.",
    ),
    "docentes": dict(
        modelo=Usuario, form=forms.DocenteForm, singular="docente", plural="Docentes",
        nuevo="Nuevo docente",
        qs=lambda c: Usuario.objects.filter(colegio=c, rol="docente").annotate(n=Count("clases")).order_by("last_name", "first_name"),
        buscar=["first_name", "last_name", "documento"],
        columnas=[
            ("Docente", lambda o: o.nombre_completo), ("Documento", lambda o: o.documento),
            ("Correo", lambda o: o.email or "—"), ("Clases", lambda o: o.n),
            ("Cuenta", lambda o: "Activa" if o.is_active else "Inactiva"),
        ],
        es_usuario=True,
        vacio="Aún no hay docentes registrados.",
    ),
    "acudientes": dict(
        modelo=Usuario, form=forms.AcudienteForm, singular="acudiente", plural="Acudientes",
        nuevo="Nuevo acudiente",
        qs=lambda c: Usuario.objects.filter(colegio=c, rol="acudiente").prefetch_related("acudidos"),
        buscar=["first_name", "last_name", "documento"],
        columnas=[
            ("Acudiente", lambda o: o.nombre_completo), ("Documento", lambda o: o.documento),
            ("Teléfono", lambda o: o.telefono or "—"),
            ("Estudiantes a cargo", lambda o: ", ".join(e.nombre_natural for e in o.acudidos.all()) or "Ninguno"),
        ],
        es_usuario=True,
        vacio="Los acudientes se crean aquí o desde la ficha de cada estudiante.",
    ),
    "anios": dict(
        modelo=AnioLectivo, form=forms.AnioForm, singular="año lectivo", plural="Años lectivos",
        nuevo="Nuevo año lectivo", volver="configuracion",
        qs=lambda c: AnioLectivo.objects.filter(colegio=c),
        columnas=[("Año", str), ("Estado", lambda o: "Activo" if o.activo else "—")],
        vacio="Cree el año lectivo para empezar.",
    ),
    "periodos": dict(
        modelo=Periodo, form=forms.PeriodoForm, singular="periodo", plural="Periodos",
        nuevo="Nuevo periodo", volver="configuracion",
        qs=lambda c: Periodo.objects.filter(anio__colegio=c).select_related("anio"),
        columnas=[
            ("Periodo", str), ("Año", lambda o: o.anio.nombre),
            ("Fechas", lambda o: f"{o.fecha_inicio:%d/%m/%Y} a {o.fecha_fin:%d/%m/%Y}"),
            ("Peso", lambda o: f"{o.peso:g} %"), ("Estado", lambda o: "Cerrado" if o.cerrado else "Abierto"),
        ],
        vacio="Defina los periodos del año lectivo.",
    ),
    "grados": dict(
        modelo=Grado, form=forms.GradoForm, singular="grado", plural="Grados",
        nuevo="Nuevo grado", volver="configuracion",
        qs=lambda c: Grado.objects.filter(colegio=c),
        columnas=[("Grado", str), ("Abreviatura", lambda o: o.abreviatura), ("Orden", lambda o: o.orden)],
        vacio="Registre los grados que ofrece el colegio.",
    ),
    "asignaturas": dict(
        modelo=Asignatura, form=forms.AsignaturaForm, singular="asignatura", plural="Asignaturas",
        nuevo="Nueva asignatura", volver="configuracion",
        qs=lambda c: Asignatura.objects.filter(colegio=c),
        columnas=[("Asignatura", str), ("Área", lambda o: o.area or "—")],
        vacio="Registre las asignaturas del plan de estudios.",
    ),
    "grupos": dict(
        modelo=Grupo, form=forms.GrupoForm, singular="grupo", plural="Grupos",
        nuevo="Nuevo grupo", volver="grupos",
        qs=lambda c: Grupo.objects.filter(anio__colegio=c).select_related("grado", "anio", "director"),
        columnas=[
            ("Grupo", lambda o: o.nombre_largo), ("Año", lambda o: o.anio.nombre),
            ("Director(a)", lambda o: o.director.nombre_completo if o.director else "Sin asignar"),
        ],
        vacio="Cree los grupos del año lectivo.",
    ),
    "clases": dict(
        modelo=Clase, form=forms.ClaseForm, singular="clase", plural="Clases",
        nuevo="Nueva clase", volver="grupos",
        qs=lambda c: Clase.objects.filter(grupo__anio__colegio=c, grupo__anio__activo=True).select_related(
            "grupo__grado", "asignatura", "docente"
        ),
        columnas=[
            ("Asignatura", lambda o: o.asignatura.nombre), ("Grupo", lambda o: str(o.grupo)),
            ("Docente", lambda o: o.docente.nombre_completo), ("Horas", lambda o: o.horas_semanales),
        ],
        vacio="Asigne a cada grupo sus asignaturas y docentes.",
    ),
    "comunicados": dict(
        modelo=Comunicado, form=forms.ComunicadoForm, singular="comunicado", plural="Comunicados",
        nuevo="Nuevo comunicado", volver="comunicados",
        qs=lambda c: Comunicado.objects.filter(colegio=c).select_related("grupo__grado"),
        columnas=[
            ("Título", str), ("Dirigido a", lambda o: str(o.grupo) if o.grupo else "Todo el colegio"),
            ("Publicado", lambda o: f"{timezone.localtime(o.publicado):%d/%m/%Y}"),
        ],
        vacio="Aún no se han publicado comunicados.",
    ),
}


def _entidad(nombre):
    try:
        return ENTIDADES[nombre]
    except KeyError:
        raise Http404


@directivo
def gestion_lista(request, entidad):
    conf = _entidad(entidad)
    qs = conf["qs"](request.user.colegio)
    q = request.GET.get("q", "").strip()
    if q and conf.get("buscar"):
        filtro = Q()
        for campo in conf["buscar"]:
            filtro |= Q(**{f"{campo}__icontains": q})
        qs = qs.filter(filtro)
    pagina = Paginator(qs, 40).get_page(request.GET.get("pagina"))
    filas = [
        {"obj": o, "celdas": [f(o) for _, f in conf["columnas"]]} for o in pagina.object_list
    ]
    return render(request, "admin_colegio/lista.html", {
        "conf": conf, "entidad": entidad, "filas": filas, "pagina": pagina, "q": q,
        "titulos": [t for t, _ in conf["columnas"]],
    })


def _despues_de_guardar(entidad, conf, obj):
    if conf.get("detalle"):
        return redirect(conf["detalle"], obj.pk)
    if conf.get("volver"):
        return redirect(conf["volver"])
    return redirect("gestion_lista", entidad)


@directivo
def gestion_form(request, entidad, pk=None):
    conf = _entidad(entidad)
    colegio = request.user.colegio
    obj = get_object_or_404(conf["qs"](colegio), pk=pk) if pk else None
    form = conf["form"](request.POST or None, instance=obj, colegio=colegio)
    if request.method == "POST" and form.is_valid():
        guardado = form.save(commit=False)
        if entidad == "comunicados" and not guardado.autor_id:
            guardado.autor = request.user
        guardado.save()
        if entidad == "estudiantes":
            forms.matricular(guardado, form.cleaned_data.get("grupo"))
        clave = getattr(form, "clave_generada", None)
        if clave:
            messages.success(
                request,
                f"Cuenta creada. Usuario: {guardado.username}. Contraseña temporal: {clave}. "
                "Anótela ahora: no se vuelve a mostrar. El sistema pedirá cambiarla al primer ingreso.",
            )
        else:
            messages.success(request, f"Se guardó {conf['singular']}: {guardado}.")
        return _despues_de_guardar(entidad, conf, guardado)
    return render(request, "admin_colegio/form.html", {
        "conf": conf, "entidad": entidad, "form": form, "obj": obj,
        "titulo": f"Editar {conf['singular']}" if obj else conf["nuevo"],
        "cancelar": reverse(conf["volver"]) if conf.get("volver") else reverse("gestion_lista", args=[entidad]),
    })


@directivo
@require_POST
def gestion_eliminar(request, entidad, pk):
    conf = _entidad(entidad)
    obj = get_object_or_404(conf["qs"](request.user.colegio), pk=pk)
    if obj == request.user:
        messages.error(request, "No puede eliminar su propia cuenta.")
        return redirect("gestion_lista", entidad)
    nombre = str(obj)
    try:
        obj.delete()
        messages.success(request, f"Se eliminó {conf['singular']}: {nombre}.")
    except ProtectedError:
        messages.error(
            request,
            f"No se puede eliminar {nombre} porque tiene información asociada. "
            "Puede desactivarlo o reasignar primero lo que depende de él.",
        )
    if conf.get("volver"):
        return redirect(conf["volver"])
    return redirect("gestion_lista", entidad)


@directivo
@require_POST
def restablecer_clave(request, pk):
    usuario = get_object_or_404(Usuario, pk=pk, colegio=request.user.colegio)
    clave = forms.clave_temporal()
    usuario.set_password(clave)
    usuario.debe_cambiar_clave = True
    usuario.save()
    messages.success(
        request,
        f"Nueva contraseña temporal de {usuario.nombre_completo} (usuario {usuario.username}): {clave}. "
        "Anótela ahora: no se vuelve a mostrar.",
    )
    volver = request.POST.get("volver", "")
    if not url_has_allowed_host_and_scheme(volver, allowed_hosts={request.get_host()}):
        volver = "panel"
    return redirect(volver)


# --------------------------------------------------------------------------
# Panel
# --------------------------------------------------------------------------


@directivo
def panel(request):
    colegio = request.user.colegio
    anio = colegio.anio_activo
    hoy = timezone.localdate()
    contexto = {"anio": anio, "hoy": hoy}
    if not anio:
        return render(request, "admin_colegio/panel.html", contexto)

    periodo = periodo_vigente(anio)
    matriculas = Matricula.objects.filter(grupo__anio=anio, activa=True)
    semana = hoy - timedelta(days=7)
    asistencia = Asistencia.objects.filter(clase__grupo__anio=anio, fecha__gte=semana)
    total_asis = asistencia.count()
    faltas = asistencia.filter(estado__in=["A", "E"]).count()

    grupos = []
    for g in Grupo.objects.filter(anio=anio).select_related("grado", "director").annotate(
        n=Count("matriculas", filter=Q(matriculas__activa=True))
    ):
        en_riesgo = 0
        if periodo:
            for m in g.matriculas.filter(activa=True):
                filas, _, _ = calculos.resumen_estudiante(m, periodos=[periodo])
                perdidas = sum(
                    1 for f in filas
                    if f["periodos"][periodo.id] is not None and f["periodos"][periodo.id] < colegio.desde_basico
                )
                if perdidas >= 2:
                    en_riesgo += 1
        grupos.append({"grupo": g, "estudiantes": g.n, "en_riesgo": en_riesgo})

    # Clases sin ninguna nota en el periodo vigente: lo que coordinación debe perseguir.
    sin_notas = []
    if periodo and not periodo.cerrado:
        con_notas = set(
            Nota.objects.filter(actividad__periodo=periodo).values_list("actividad__clase_id", flat=True)
        )
        sin_notas = list(
            Clase.objects.filter(grupo__anio=anio).exclude(id__in=con_notas)
            .select_related("grupo__grado", "asignatura", "docente")[:8]
        )

    contexto.update({
        "periodo": periodo,
        "n_estudiantes": matriculas.count(),
        "n_docentes": Usuario.objects.filter(colegio=colegio, rol="docente", is_active=True).count(),
        "n_grupos": len(grupos),
        "inasistencia": round(faltas * 100 / total_asis, 1) if total_asis else None,
        "grupos": grupos,
        "sin_notas": sin_notas,
        "tareas_semana": Tarea.objects.filter(
            clase__grupo__anio=anio, fecha_entrega__gte=hoy, fecha_entrega__lte=hoy + timedelta(days=7)
        ).count(),
        "comunicados": Comunicado.objects.filter(colegio=colegio)[:3],
    })
    return render(request, "admin_colegio/panel.html", contexto)


# --------------------------------------------------------------------------
# Grupos, ficha del estudiante, configuración
# --------------------------------------------------------------------------


@directivo
def grupos(request):
    anio = request.user.colegio.anio_activo
    lista = []
    if anio:
        for g in Grupo.objects.filter(anio=anio).select_related("grado", "director").annotate(
            n=Count("matriculas", filter=Q(matriculas__activa=True))
        ):
            lista.append({
                "grupo": g, "n": g.n,
                "clases": g.clases.select_related("asignatura", "docente"),
            })
    return render(request, "admin_colegio/grupos.html", {"anio": anio, "grupos": lista})


@directivo
def ficha_estudiante(request, pk):
    colegio = request.user.colegio
    est = get_object_or_404(Estudiante, pk=pk, colegio=colegio)
    matricula = est.matricula_actual()
    vincular = forms.VincularAcudienteForm(request.POST or None, colegio=colegio)
    if request.method == "POST" and vincular.is_valid():
        datos = vincular.cleaned_data
        with transaction.atomic():
            acudiente = vincular.existente
            clave = None
            if acudiente is None:
                clave = forms.clave_temporal()
                acudiente = Usuario(
                    username=datos["documento"], documento=datos["documento"], colegio=colegio,
                    rol="acudiente", first_name=datos["first_name"], last_name=datos["last_name"],
                    email=datos["email"], telefono=datos["telefono"], debe_cambiar_clave=True,
                )
                acudiente.set_password(clave)
                acudiente.save()
            Acudiencia.objects.update_or_create(
                estudiante=est, acudiente=acudiente, defaults={"parentesco": datos["parentesco"]}
            )
        if clave:
            messages.success(
                request,
                f"Acudiente creado y vinculado. Usuario: {acudiente.username}. Contraseña temporal: {clave}. "
                "Anótela ahora: no se vuelve a mostrar.",
            )
        else:
            messages.success(request, f"{acudiente.nombre_completo} quedó como acudiente de {est.nombre_natural}.")
        return redirect("ficha_estudiante", est.pk)

    contexto = {
        "est": est, "matricula": matricula, "vincular": vincular,
        "acudiencias": Acudiencia.objects.filter(estudiante=est).select_related("acudiente"),
    }
    if matricula:
        periodos = list(matricula.grupo.anio.periodos.all())
        filas, promedios, final = calculos.resumen_estudiante(matricula, periodos)
        contexto.update({
            "periodos": periodos, "filas": filas, "promedios": promedios, "final": final,
            "asistencia": calculos.resumen_asistencia(matricula),
            "observaciones": matricula.observaciones.select_related("autor")[:20],
        })
    return render(request, "admin_colegio/ficha_estudiante.html", contexto)


@directivo
@require_POST
def desvincular_acudiente(request, pk):
    ac = get_object_or_404(Acudiencia, pk=pk, estudiante__colegio=request.user.colegio)
    est_id = ac.estudiante_id
    ac.delete()
    messages.success(request, "Se retiró el acudiente de este estudiante. Su cuenta sigue existiendo.")
    return redirect("ficha_estudiante", est_id)


@directivo
def configuracion(request):
    colegio = request.user.colegio
    form = forms.ColegioForm(request.POST or None, instance=colegio)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Se guardaron los datos del colegio.")
        return redirect("configuracion")
    return render(request, "admin_colegio/configuracion.html", {
        "form": form,
        "anios": AnioLectivo.objects.filter(colegio=colegio),
        "periodos": Periodo.objects.filter(anio__colegio=colegio, anio__activo=True),
        "grados": Grado.objects.filter(colegio=colegio),
        "asignaturas": Asignatura.objects.filter(colegio=colegio),
    })


@directivo
@require_POST
def alternar_periodo(request, pk):
    periodo = get_object_or_404(Periodo, pk=pk, anio__colegio=request.user.colegio)
    periodo.cerrado = not periodo.cerrado
    periodo.save(update_fields=["cerrado"])
    if periodo.cerrado:
        messages.success(
            request, f"{periodo.nombre} cerrado. Las familias ya pueden descargar el boletín y las notas no se pueden cambiar."
        )
    else:
        messages.success(request, f"{periodo.nombre} reabierto. Los docentes pueden corregir notas.")
    return redirect("configuracion")


# --------------------------------------------------------------------------
# Importación desde Excel
# --------------------------------------------------------------------------


@directivo
def importar(request):
    form = forms.ImportarForm(request.POST or None, request.FILES or None)
    resultado = None
    if request.method == "POST" and form.is_valid():
        resultado = importar_estudiantes(form.cleaned_data["archivo"], request.user.colegio)
    return render(request, "admin_colegio/importar.html", {"form": form, "resultado": resultado})


@directivo
def plantilla_importacion(request):
    respuesta = HttpResponse(
        plantilla_xlsx(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    respuesta["Content-Disposition"] = 'attachment; filename="plantilla_estudiantes.xlsx"'
    return respuesta


# --------------------------------------------------------------------------
# Consolidado y boletines
# --------------------------------------------------------------------------


def _seleccion(request):
    """Grupo y periodo elegidos en los filtros de consolidado y boletines."""
    anio = request.user.colegio.anio_activo
    grupos_qs = Grupo.objects.filter(anio=anio).select_related("grado") if anio else Grupo.objects.none()
    periodos = list(anio.periodos.all()) if anio else []
    grupo = grupos_qs.filter(pk=request.GET.get("grupo") or 0).first() or grupos_qs.first()
    periodo = next((p for p in periodos if str(p.pk) == request.GET.get("periodo")), None)
    if periodo is None and anio:
        periodo = periodo_vigente(anio)
    return anio, grupos_qs, periodos, grupo, periodo


@directivo
def consolidado(request):
    anio, grupos_qs, periodos, grupo, periodo = _seleccion(request)
    filas, clases = [], []
    if grupo and periodo:
        clases = list(grupo.clases.select_related("asignatura"))
        puestos, _ = calculos.puestos_del_grupo(grupo, periodo)
        for m in grupo.matriculas.filter(activa=True).select_related("estudiante"):
            detalle, proms, _ = calculos.resumen_estudiante(m, periodos=[periodo])
            por_clase = {f["clase"].id: f["periodos"][periodo.id] for f in detalle}
            notas = [por_clase.get(c.id) for c in clases]
            filas.append({
                "matricula": m, "notas": notas, "promedio": proms[periodo.id],
                "puesto": puestos.get(m.id),
                "perdidas": sum(1 for n in notas if n is not None and n < request.user.colegio.desde_basico),
            })
    return render(request, "admin_colegio/consolidado.html", {
        "grupos": grupos_qs, "periodos": periodos, "grupo": grupo, "periodo": periodo,
        "clases": clases, "filas": filas,
    })


@directivo
def boletines(request):
    anio, grupos_qs, periodos, grupo, periodo = _seleccion(request)
    matriculas = (
        grupo.matriculas.filter(activa=True).select_related("estudiante") if grupo else []
    )
    return render(request, "admin_colegio/boletines.html", {
        "grupos": grupos_qs, "periodos": periodos, "grupo": grupo, "periodo": periodo,
        "matriculas": matriculas,
    })
