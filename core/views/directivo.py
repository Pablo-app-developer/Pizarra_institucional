from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, ProtectedError, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .. import calculos, forms
from ..importar import importar_estudiantes, plantilla_xlsx
from .. import escala as escala_mod
from ..avisos import avisar_comunicado
from ..bitacora import registrar
from ..models import MODULOS
from ..models import (
    Acudiencia, AnioLectivo, Asignatura, Asistencia, Bitacora, Bloque, Clase, Cobro, Comunicado, Estudiante,
    Grado, Grupo, HorarioClase, Matricula, Nota, Pago, Periodo, PlanEstudio, Tarea, Usuario,
)
from ..permisos import capacidad, directivo
from .comunes import periodo_vigente

# --------------------------------------------------------------------------
# Gestión genérica: una sola pareja de plantillas sirve a todos los catálogos.
# --------------------------------------------------------------------------


def _grupo_de(est):
    m = est.matricula_actual()
    return str(m.grupo) if m else "Sin matricular"


ENTIDADES = {
    "estudiantes": dict(
        capacidad="estudiantes",
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
        capacidad="docentes",
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
        capacidad="estudiantes",
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
        capacidad="academico",
        modelo=AnioLectivo, form=forms.AnioForm, singular="año lectivo", plural="Años lectivos",
        nuevo="Nuevo año lectivo", volver="configuracion",
        qs=lambda c: AnioLectivo.objects.filter(colegio=c),
        columnas=[("Año", str), ("Estado", lambda o: "Activo" if o.activo else "—")],
        vacio="Cree el año lectivo para empezar.",
    ),
    "periodos": dict(
        capacidad="academico",
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
        capacidad="academico",
        modelo=Grado, form=forms.GradoForm, singular="grado", plural="Grados",
        nuevo="Nuevo grado", volver="configuracion",
        qs=lambda c: Grado.objects.filter(colegio=c),
        columnas=[
            ("Grado", str), ("Abreviatura", lambda o: o.abreviatura), ("Orden", lambda o: o.orden),
            ("Valoración", lambda o: "Descriptiva" if o.descriptivo else "Numérica"),
            ("Pensión", lambda o: f"$ {o.pension:,.0f}".replace(",", ".") if o.pension else "—"),
        ],
        vacio="Registre los grados que ofrece el colegio.",
    ),
    "asignaturas": dict(
        capacidad="academico",
        modelo=Asignatura, form=forms.AsignaturaForm, singular="asignatura", plural="Asignaturas",
        nuevo="Nueva asignatura", volver="configuracion",
        qs=lambda c: Asignatura.objects.filter(colegio=c),
        columnas=[("Asignatura", str), ("Área", lambda o: o.area or "—")],
        vacio="Registre las asignaturas del plan de estudios.",
    ),
    "grupos": dict(
        capacidad="academico",
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
        capacidad="academico",
        modelo=Clase, form=forms.ClaseForm, singular="clase", plural="Clases",
        nuevo="Nueva clase", volver="grupos",
        qs=lambda c: Clase.objects.filter(grupo__anio__colegio=c, grupo__anio__activo=True).select_related(
            "grupo__grado", "asignatura", "docente"
        ),
        columnas=[
            ("Asignatura", lambda o: o.asignatura.nombre), ("Grupo", lambda o: str(o.grupo)),
            ("Docente", lambda o: o.docente.nombre_completo if o.docente else "Sin asignar"), ("Horas", lambda o: o.horas_semanales),
        ],
        vacio="Asigne a cada grupo sus asignaturas y docentes.",
    ),
    "comunicados": dict(
        capacidad="comunicados",
        modelo=Comunicado, form=forms.ComunicadoForm, singular="comunicado", plural="Comunicados",
        nuevo="Nuevo comunicado", volver="comunicados",
        qs=lambda c: Comunicado.objects.filter(colegio=c).select_related("grupo__grado"),
        columnas=[
            ("Título", str), ("Dirigido a", lambda o: str(o.grupo) if o.grupo else "Todo el colegio"),
            ("Publicado", lambda o: f"{timezone.localtime(o.publicado):%d/%m/%Y}"),
        ],
        vacio="Aún no se han publicado comunicados.",
    ),
    "directivos": dict(
        capacidad="configuracion",
        modelo=Usuario, form=forms.DirectivoForm, singular="directivo", plural="Directivos",
        nuevo="Nuevo directivo", volver="configuracion",
        qs=lambda c: Usuario.objects.filter(colegio=c, rol="directivo").order_by("last_name", "first_name"),
        columnas=[
            ("Nombre", lambda o: o.nombre_completo), ("Documento", lambda o: o.documento),
            ("Cargo", lambda o: o.get_cargo_display() or "Rectoría"),
            ("Cuenta", lambda o: "Activa" if o.is_active else "Inactiva"),
        ],
        es_usuario=True,
        vacio="Aún no hay otros directivos.",
    ),
    "bloques": dict(
        capacidad="academico",
        modelo=Bloque, form=forms.BloqueForm, singular="bloque", plural="Jornada escolar",
        nuevo="Nuevo bloque", volver="horarios",
        qs=lambda c: Bloque.objects.filter(colegio=c),
        columnas=[
            ("Bloque", lambda o: o.nombre), ("Horario", lambda o: f"{o.inicio:%H:%M} a {o.fin:%H:%M}"),
            ("Tipo", lambda o: "Descanso" if o.descanso else "Clase"),
        ],
        vacio="Defina las horas de clase y los descansos de la jornada.",
    ),
}


def _entidad(nombre, usuario):
    try:
        conf = ENTIDADES[nombre]
    except KeyError:
        raise Http404
    requerido = {"bloques": "horarios", "comunicados": "comunicados"}.get(nombre)
    if requerido and not usuario.colegio.tiene(requerido):
        raise Http404("Este módulo no está activo para su colegio.")
    if not usuario.puede(conf["capacidad"]):
        raise PermissionDenied("Su cuenta no tiene permiso para esta sección. Pídalo a rectoría o a sistemas.")
    return conf


@directivo
def gestion_lista(request, entidad):
    conf = _entidad(entidad, request.user)
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
    conf = _entidad(entidad, request.user)
    colegio = request.user.colegio
    obj = get_object_or_404(conf["qs"](colegio), pk=pk) if pk else None
    form = conf["form"](request.POST or None, instance=obj, colegio=colegio)
    if request.method == "POST" and form.is_valid():
        guardado = form.save(commit=False)
        nuevo_comunicado = entidad == "comunicados" and obj is None
        if entidad == "comunicados" and not guardado.autor_id:
            guardado.autor = request.user
        guardado.save()
        if nuevo_comunicado:
            avisar_comunicado(guardado)
        if entidad == "directivos":
            nombres = dict((c, n) for c, n, _ in Usuario.PERMISOS)
            registrar(request, "Permisos de directivo", f"{guardado.nombre_completo} ({guardado.get_cargo_display()}): "
                      + (", ".join(nombres[p] for p in guardado.permisos) or "sin permisos"))
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
        "permisos_por_cargo": Usuario.PERMISOS_POR_CARGO,
        "cancelar": reverse(conf["volver"]) if conf.get("volver") else reverse("gestion_lista", args=[entidad]),
    })


@directivo
@require_POST
def gestion_eliminar(request, entidad, pk):
    conf = _entidad(entidad, request.user)
    obj = get_object_or_404(conf["qs"](request.user.colegio), pk=pk)
    if obj == request.user:
        messages.error(request, "No puede eliminar su propia cuenta.")
        return redirect("gestion_lista", entidad)
    nombre = str(obj)
    try:
        obj.delete()
        registrar(request, f"Eliminó {conf['singular']}", nombre)
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
    necesaria = {"directivo": "configuracion", "docente": "docentes"}.get(usuario.rol, "estudiantes")
    if not request.user.puede(necesaria):
        raise PermissionDenied("Su cargo no puede restablecer la contraseña de esta cuenta.")
    clave = forms.clave_temporal()
    usuario.set_password(clave)
    usuario.debe_cambiar_clave = True
    usuario.save()
    registrar(request, "Restableció contraseña", f"{usuario.nombre_completo} ({usuario.get_rol_display()})")
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
    u = request.user
    colegio = u.colegio
    anio = colegio.anio_activo
    hoy = timezone.localdate()
    contexto = {"anio": anio, "hoy": hoy, "pasos": primeros_pasos(colegio, anio, u)}
    contexto["pasos_pendientes"] = sum(1 for p in contexto["pasos"] if not p["hecho"] and not p["opcional"])
    if not anio:
        return render(request, "admin_colegio/panel.html", contexto)

    periodo = periodo_vigente(anio)
    matriculas = list(Matricula.objects.filter(grupo__anio=anio, activa=True))
    semana = hoy - timedelta(days=7)
    asistencia = Asistencia.objects.filter(clase__grupo__anio=anio, fecha__gte=semana)
    total_asis = asistencia.count()
    faltas = asistencia.filter(estado__in=["A", "E"]).count()

    # Todas las notas del periodo en dos consultas, sin importar el tamaño del colegio.
    en_riesgo = {}
    if periodo and u.puede("notas"):
        datos, _ = calculos.consolidar(matriculas, [periodo], colegio)
        for m in matriculas:
            if calculos.perdidas(datos[m.id], colegio, periodo) >= 2:
                en_riesgo[m.grupo_id] = en_riesgo.get(m.grupo_id, 0) + 1
    por_grupo = {}
    for m in matriculas:
        por_grupo[m.grupo_id] = por_grupo.get(m.grupo_id, 0) + 1
    grupos = [
        {"grupo": g, "estudiantes": por_grupo.get(g.id, 0), "en_riesgo": en_riesgo.get(g.id, 0)}
        for g in Grupo.objects.filter(anio=anio).select_related("grado", "director")
    ]

    # Clases sin ninguna nota en el periodo vigente: lo que coordinación debe perseguir.
    sin_notas = []
    if periodo and not periodo.cerrado and u.puede("notas"):
        con_notas = set(
            Nota.objects.filter(actividad__periodo=periodo).values_list("actividad__clase_id", flat=True)
        )
        sin_notas = list(
            Clase.objects.filter(grupo__anio=anio, grupo__grado__descriptivo=False).exclude(id__in=con_notas)
            .select_related("grupo__grado", "asignatura", "docente")[:8]
        )

    cartera = None
    if u.puede("pensiones") and colegio.tiene("pensiones"):
        vencidos = Cobro.objects.filter(anio=anio, vence__lt=hoy)
        cobrado = vencidos.aggregate(t=Sum("valor"))["t"] or 0
        pagado = Pago.objects.filter(cobro__in=vencidos).aggregate(t=Sum("valor"))["t"] or 0
        cartera = cobrado - pagado

    contexto.update({
        "periodo": periodo,
        "n_estudiantes": len(matriculas),
        "n_docentes": Usuario.objects.filter(colegio=colegio, rol="docente", is_active=True).count(),
        "n_grupos": len(grupos),
        "inasistencia": round(faltas * 100 / total_asis, 1) if total_asis else None,
        "grupos": grupos,
        "sin_notas": sin_notas,
        "cartera": cartera,
        "tareas_semana": Tarea.objects.filter(
            clase__grupo__anio=anio, fecha_entrega__gte=hoy, fecha_entrega__lte=hoy + timedelta(days=7)
        ).count(),
        "comunicados": Comunicado.objects.filter(colegio=colegio)[:3],
    })
    return render(request, "admin_colegio/panel.html", contexto)


# --------------------------------------------------------------------------
# Grupos, ficha del estudiante, configuración
# --------------------------------------------------------------------------


@capacidad("academico")
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


@capacidad("estudiantes")
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


@capacidad("estudiantes")
@require_POST
def desvincular_acudiente(request, pk):
    ac = get_object_or_404(Acudiencia, pk=pk, estudiante__colegio=request.user.colegio)
    est_id = ac.estudiante_id
    ac.delete()
    messages.success(request, "Se retiró el acudiente de este estudiante. Su cuenta sigue existiendo.")
    return redirect("ficha_estudiante", est_id)


@capacidad("configuracion", "academico", "cierres")
def configuracion(request):
    colegio = request.user.colegio
    form = None
    if request.user.puede("configuracion"):
        form = forms.ColegioForm(request.POST or None, request.FILES or None, instance=colegio)
        if request.method == "POST" and form.is_valid():
            form.save()
            if form.cambio_escala:
                c = form.instance
                registrar(request, "Cambió el sistema de evaluación",
                          f"De {form.cortes_previos[0]}–{form.cortes_previos[4]} a {c.nota_minima}–{c.nota_maxima}; "
                          f"aprueba desde {c.desde_basico}. Notas convertidas: {form.notas_convertidas}.")
                messages.success(
                    request,
                    "Sistema de evaluación actualizado."
                    + (f" Se convirtieron {form.notas_convertidas} notas a la nueva escala." if form.notas_convertidas else ""),
                )
            else:
                messages.success(request, "Se guardaron los datos del colegio.")
            return redirect("configuracion")
    return render(request, "admin_colegio/configuracion.html", {
        "form": form,
        "anios": AnioLectivo.objects.filter(colegio=colegio),
        "periodos": Periodo.objects.filter(anio__colegio=colegio, anio__activo=True),
        "grados": Grado.objects.filter(colegio=colegio),
        "asignaturas": Asignatura.objects.filter(colegio=colegio),
        "directivos": Usuario.objects.filter(colegio=colegio, rol="directivo").count(),
        "lista_modulos": MODULOS,
        "escalas": {k: {"valores": list(v[1:6]), "decimales": v[6]} for k, v in escala_mod.PREDEFINIDAS.items()},
    })


@capacidad("cierres")
@require_POST
def alternar_periodo(request, pk):
    periodo = get_object_or_404(Periodo, pk=pk, anio__colegio=request.user.colegio)
    periodo.cerrado = not periodo.cerrado
    periodo.save(update_fields=["cerrado"])
    registrar(request, "Cerró periodo" if periodo.cerrado else "Reabrió periodo", f"{periodo.nombre} de {periodo.anio.nombre}")
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


@capacidad("estudiantes")
def importar(request):
    form = forms.ImportarForm(request.POST or None, request.FILES or None)
    resultado = None
    if request.method == "POST" and form.is_valid():
        resultado = importar_estudiantes(form.cleaned_data["archivo"], request.user.colegio)
        registrar(request, "Importó estudiantes", f"{resultado['creados']} nuevos, {resultado['actualizados']} actualizados, "
                  f"{len(resultado['errores'])} filas con error")
    return render(request, "admin_colegio/importar.html", {"form": form, "resultado": resultado})


@capacidad("estudiantes")
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


@capacidad("notas")
def consolidado(request):
    anio, grupos_qs, periodos, grupo, periodo = _seleccion(request)
    filas, clases = [], []
    colegio = request.user.colegio
    if grupo and periodo:
        matriculas = list(grupo.matriculas.filter(activa=True).select_related("estudiante"))
        datos, clases_por_grupo = calculos.consolidar(matriculas, [periodo], colegio)
        clases = clases_por_grupo[grupo.id]
        puestos, _ = calculos.puestos({m.id: datos[m.id]["promedios"][periodo.id] for m in matriculas})
        for m in matriculas:
            filas.append({
                "matricula": m,
                "notas": [datos[m.id]["clases"][c.id]["periodos"][periodo.id] for c in clases],
                "promedio": datos[m.id]["promedios"][periodo.id], "puesto": puestos.get(m.id),
                "perdidas": calculos.perdidas(datos[m.id], colegio, periodo),
            })
    return render(request, "admin_colegio/consolidado.html", {
        "grupos": grupos_qs, "periodos": periodos, "grupo": grupo, "periodo": periodo,
        "clases": clases, "filas": filas,
    })


@capacidad("boletines")
def boletines(request):
    anio, grupos_qs, periodos, grupo, periodo = _seleccion(request)
    matriculas = (
        grupo.matriculas.filter(activa=True).select_related("estudiante") if grupo else []
    )
    return render(request, "admin_colegio/boletines.html", {
        "grupos": grupos_qs, "periodos": periodos, "grupo": grupo, "periodo": periodo,
        "matriculas": matriculas,
    })


# --------------------------------------------------------------------------
# Puesta en marcha
# --------------------------------------------------------------------------


def primeros_pasos(colegio, anio, usuario):
    """Lista de lo que un colegio nuevo debe dejar listo, con lo que ya está hecho."""
    def paso(texto, hecho, url, permiso, opcional=False):
        return {"texto": texto, "hecho": bool(hecho), "url": url, "puede": usuario.puede(permiso), "opcional": opcional}

    hay_grupos = anio and Grupo.objects.filter(anio=anio).exists()
    return [
        paso("Crear el año lectivo y activarlo", anio, reverse("gestion_lista", args=["anios"]), "academico"),
        paso("Definir los periodos y su peso", anio and anio.periodos.exists(), reverse("gestion_lista", args=["periodos"]), "academico"),
        paso("Registrar los grados", Grado.objects.filter(colegio=colegio).exists(), reverse("gestion_lista", args=["grados"]), "academico"),
        paso("Registrar las asignaturas", Asignatura.objects.filter(colegio=colegio).exists(), reverse("gestion_lista", args=["asignaturas"]), "academico"),
        paso("Armar el plan de estudios de cada grado", PlanEstudio.objects.filter(grado__colegio=colegio).exists(), reverse("plan"), "academico"),
        paso("Registrar los docentes", Usuario.objects.filter(colegio=colegio, rol="docente").exists(), reverse("gestion_lista", args=["docentes"]), "docentes"),
        paso("Crear los grupos y asignar docentes a sus clases", hay_grupos and Clase.objects.filter(grupo__anio=anio, docente__isnull=False).exists(), reverse("grupos"), "academico"),
        paso("Matricular a los estudiantes (uno a uno o desde Excel)", anio and Matricula.objects.filter(grupo__anio=anio, activa=True).exists(), reverse("importar"), "estudiantes"),
        paso("Subir el escudo y elegir el sistema de evaluación", colegio.escudo_tipo, reverse("configuracion"), "configuracion", opcional=True),
        paso("Definir la jornada y generar los horarios", anio and HorarioClase.objects.filter(grupo__anio=anio).exists(), reverse("horarios"), "academico", opcional=True),
    ]


ESTRUCTURA_GRADOS = [
    ("Prejardín", "PJ", 1, True), ("Jardín", "JA", 2, True), ("Transición", "TR", 3, True),
    ("Primero", "1°", 11, False), ("Segundo", "2°", 12, False), ("Tercero", "3°", 13, False),
    ("Cuarto", "4°", 14, False), ("Quinto", "5°", 15, False), ("Sexto", "6°", 16, False),
    ("Séptimo", "7°", 17, False), ("Octavo", "8°", 18, False), ("Noveno", "9°", 19, False),
    ("Décimo", "10°", 20, False), ("Once", "11°", 21, False),
]
# Asignaturas de partida, agrupadas en las nueve áreas obligatorias y fundamentales (Ley 115 de 1994, art. 23).
# (asignatura, área, horas semanales sugeridas)
ESTRUCTURA_ASIGNATURAS = [
    ("Matemáticas", "Matemáticas", 5),
    ("Lengua castellana", "Humanidades, lengua castellana e idiomas extranjeros", 5),
    ("Inglés", "Humanidades, lengua castellana e idiomas extranjeros", 3),
    ("Ciencias naturales", "Ciencias naturales y educación ambiental", 4),
    ("Ciencias sociales", "Ciencias sociales, historia, geografía, constitución política y democracia", 4),
    ("Tecnología e informática", "Tecnología e informática", 2),
    ("Educación artística", "Educación artística y cultural", 2),
    ("Educación física", "Educación física, recreación y deportes", 2),
    ("Ética y valores", "Educación ética y en valores humanos", 1),
    ("Educación religiosa", "Educación religiosa", 1),
]
# En educación media se suman estas dos (Ley 115 de 1994, art. 31).
ESTRUCTURA_MEDIA = [("Filosofía", "Filosofía", 2), ("Ciencias económicas y políticas", "Ciencias económicas y políticas", 1)]
ESTRUCTURA_DIMENSIONES = ["Dimensión cognitiva", "Dimensión comunicativa", "Dimensión corporal",
                          "Dimensión socioafectiva", "Dimensión estética", "Dimensión ética"]
ESTRUCTURA_JORNADA = [("1ª hora", "07:00", "07:55", False), ("2ª hora", "07:55", "08:50", False),
                      ("3ª hora", "08:50", "09:45", False), ("Descanso", "09:45", "10:15", True),
                      ("4ª hora", "10:15", "11:10", False), ("5ª hora", "11:10", "12:05", False),
                      ("6ª hora", "12:05", "13:00", False)]


@transaction.atomic
def crear_estructura_tipica(colegio, hoy):
    """Deja un punto de partida editable. Solo crea lo que el colegio todavía no tiene."""
    from datetime import date

    creado = []
    anio = colegio.anio_activo
    if anio is None:
        anio = AnioLectivo.objects.create(colegio=colegio, nombre=str(hoy.year), activo=True)
        creado.append(f"año {anio.nombre}")
    if not anio.periodos.exists() and anio.nombre.isdigit():
        a = int(anio.nombre)
        fechas = [(date(a, 2, 1), date(a, 4, 10)), (date(a, 4, 11), date(a, 6, 20)),
                  (date(a, 7, 10), date(a, 9, 15)), (date(a, 9, 16), date(a, 11, 30))]
        nombres = ["Primer periodo", "Segundo periodo", "Tercer periodo", "Cuarto periodo"]
        Periodo.objects.bulk_create([
            Periodo(anio=anio, numero=i + 1, nombre=nombres[i], fecha_inicio=ini, fecha_fin=fin, peso=25)
            for i, (ini, fin) in enumerate(fechas)
        ])
        creado.append("4 periodos de 25 %")
    if not Grado.objects.filter(colegio=colegio).exists():
        Grado.objects.bulk_create([
            Grado(colegio=colegio, nombre=n, abreviatura=ab, orden=o, descriptivo=d) for n, ab, o, d in ESTRUCTURA_GRADOS
        ])
        creado.append("14 grados, de Prejardín a Once")
    if not Asignatura.objects.filter(colegio=colegio).exists():
        Asignatura.objects.bulk_create(
            [Asignatura(colegio=colegio, nombre=n, area=ar) for n, ar, _ in ESTRUCTURA_ASIGNATURAS + ESTRUCTURA_MEDIA]
            + [Asignatura(colegio=colegio, nombre=n, area="Preescolar") for n in ESTRUCTURA_DIMENSIONES]
        )
        creado.append("asignaturas de las áreas obligatorias y dimensiones de preescolar")
    if not PlanEstudio.objects.filter(grado__colegio=colegio).exists():
        por_nombre = {a.nombre: a for a in Asignatura.objects.filter(colegio=colegio)}
        plan = []
        for g in Grado.objects.filter(colegio=colegio):
            if g.descriptivo:
                items = [(n, 4) for n in ESTRUCTURA_DIMENSIONES]
            else:
                items = [(n, h) for n, _, h in ESTRUCTURA_ASIGNATURAS]
                if g.orden >= 20:
                    items += [(n, h) for n, _, h in ESTRUCTURA_MEDIA]
            plan += [PlanEstudio(grado=g, asignatura=por_nombre[n], horas_semanales=h) for n, h in items if n in por_nombre]
        PlanEstudio.objects.bulk_create(plan)
        creado.append("plan de estudios sugerido por grado")
    if not Bloque.objects.filter(colegio=colegio).exists():
        Bloque.objects.bulk_create([Bloque(colegio=colegio, nombre=n, inicio=i, fin=f, descanso=d) for n, i, f, d in ESTRUCTURA_JORNADA])
        creado.append("jornada de 6 horas con un descanso")
    return creado


@capacidad("academico")
@require_POST
def estructura_tipica(request):
    creado = crear_estructura_tipica(request.user.colegio, timezone.localdate())
    if creado:
        registrar(request, "Creó la estructura típica", "; ".join(creado))
        messages.success(
            request,
            "Se creó: " + "; ".join(creado) + ". Todo es un punto de partida: ajuste fechas, grados, "
            "asignaturas y horas a su proyecto educativo.",
        )
    else:
        messages.success(request, "El colegio ya tenía toda la estructura básica; no se creó nada.")
    return redirect("panel")


# --------------------------------------------------------------------------
# Cuenta del estudiante, exportaciones y bitácora
# --------------------------------------------------------------------------


@capacidad("estudiantes")
@require_POST
def crear_cuenta_estudiante(request, pk):
    est = get_object_or_404(Estudiante, pk=pk, colegio=request.user.colegio)
    if est.usuario_id:
        messages.error(request, "Este estudiante ya tiene cuenta. Si olvidó la contraseña, genere una nueva.")
    elif Usuario.objects.filter(username=est.documento).exists():
        messages.error(request, f"Ya existe otra cuenta con el documento {est.documento}. Revise el documento del estudiante.")
    else:
        clave = forms.clave_temporal()
        with transaction.atomic():
            usuario = Usuario(username=est.documento, documento=est.documento, colegio=est.colegio, rol="estudiante",
                              first_name=est.nombres, last_name=est.apellidos, debe_cambiar_clave=True)
            usuario.set_password(clave)
            usuario.save()
            est.usuario = usuario
            est.save(update_fields=["usuario"])
        messages.success(
            request,
            f"Cuenta del estudiante creada. Usuario: {usuario.username}. Contraseña temporal: {clave}. "
            "Anótela ahora: no se vuelve a mostrar.",
        )
    return redirect("ficha_estudiante", est.pk)


def libro_excel(nombre, titulo, encabezados, filas):
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font

    libro = Workbook()
    hoja = libro.active
    hoja.title = titulo[:31]
    hoja.append(encabezados)
    for celda in hoja[1]:
        celda.font = Font(bold=True)
    for fila in filas:
        hoja.append(fila)
    for i, encabezado in enumerate(encabezados):
        ancho = max([len(str(encabezado))] + [len(str(f[i])) for f in filas if f[i] is not None] or [10])
        hoja.column_dimensions[hoja.cell(row=1, column=i + 1).column_letter].width = min(ancho + 2, 48)
    hoja.freeze_panes = "A2"
    salida = BytesIO()
    libro.save(salida)
    respuesta = HttpResponse(salida.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    respuesta["Content-Disposition"] = f'attachment; filename="{nombre}.xlsx"'
    return respuesta


@capacidad("estudiantes")
def exportar_estudiantes(request):
    anio = request.user.colegio.anio_activo
    filas = []
    for m in Matricula.objects.filter(grupo__anio=anio, activa=True).select_related("estudiante", "grupo__grado").prefetch_related(
        "estudiante__acudiencia_set__acudiente"
    ).order_by("grupo__grado__orden", "grupo__nombre", "estudiante__apellidos"):
        e = m.estudiante
        acs = list(e.acudiencia_set.all())
        filas.append([
            str(m.grupo), e.apellidos, e.nombres, e.tipo_documento, e.documento,
            e.fecha_nacimiento.strftime("%d/%m/%Y") if e.fecha_nacimiento else "",
            "; ".join(f"{a.acudiente.nombre_completo} ({a.get_parentesco_display()})" for a in acs),
            "; ".join(a.acudiente.telefono for a in acs if a.acudiente.telefono),
            "; ".join(a.acudiente.email for a in acs if a.acudiente.email),
        ])
    return libro_excel("estudiantes", "Estudiantes", ["Grupo", "Apellidos", "Nombres", "Tipo", "Documento", "Nacimiento",
                                                      "Acudientes", "Teléfonos", "Correos"], filas)


@capacidad("notas")
def exportar_consolidado(request):
    anio, grupos_qs, periodos, grupo, periodo = _seleccion(request)
    if not grupo or not periodo:
        raise Http404
    colegio = request.user.colegio
    matriculas = list(grupo.matriculas.filter(activa=True).select_related("estudiante"))
    datos, clases_por_grupo = calculos.consolidar(matriculas, [periodo], colegio)
    clases = clases_por_grupo[grupo.id]
    puestos, _ = calculos.puestos({m.id: datos[m.id]["promedios"][periodo.id] for m in matriculas})
    numero = lambda v: float(v) if v is not None else None  # noqa: E731
    filas = [
        [str(m.estudiante), m.estudiante.documento]
        + [numero(datos[m.id]["clases"][c.id]["periodos"][periodo.id]) for c in clases]
        + [numero(datos[m.id]["promedios"][periodo.id]), puestos.get(m.id), calculos.perdidas(datos[m.id], colegio, periodo)]
        for m in matriculas
    ]
    return libro_excel(f"consolidado_{grupo}_{periodo.numero}".replace("°", ""), f"{grupo} P{periodo.numero}",
                       ["Estudiante", "Documento"] + [c.asignatura.nombre for c in clases] + ["Promedio", "Puesto", "Perdidas"], filas)


@capacidad("configuracion")
def bitacora(request):
    pagina = Paginator(Bitacora.objects.filter(colegio=request.user.colegio).select_related("usuario"), 60).get_page(
        request.GET.get("pagina")
    )
    return render(request, "admin_colegio/bitacora.html", {"pagina": pagina})
