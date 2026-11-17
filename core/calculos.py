"""Cálculo de notas. Toda la aritmética académica vive aquí para poder probarla.

Las funciones de grupo traen las notas en pocas consultas, sin importar cuántos
estudiantes haya: un colegio de 500 estudiantes cuesta lo mismo que uno de 50.
"""
from collections import defaultdict
from decimal import Decimal

from .escala import redondear  # redondea a los decimales que usa el colegio
from .models import Actividad, Asistencia, Clase, Nota, Recuperacion


def promedio_ponderado(pares):
    """pares: lista de (valor, peso). Ignora valores None.

    Si ningún elemento calificado tiene peso, promedia de forma simple. Si solo
    algunos tienen peso, el peso se normaliza sobre lo ya calificado, de modo
    que la nota parcial siempre está en la escala del colegio.
    """
    pares = [(Decimal(v), Decimal(p)) for v, p in pares if v is not None]
    if not pares:
        return None
    total_peso = sum(p for _, p in pares)
    if total_peso == 0:
        return redondear(sum(v for v, _ in pares) / len(pares))
    return redondear(sum(v * p for v, p in pares) / total_peso)


def aplicar_recuperacion(original, recuperacion, tope):
    """La recuperación reemplaza la nota del periodo solo si la mejora, y hasta el tope del colegio."""
    if recuperacion is None:
        return original
    efectiva = min(recuperacion, tope) if tope is not None else recuperacion
    if original is None or efectiva > original:
        return efectiva
    return original


def notas_de_clase(clase, periodo):
    """Planilla de una clase en un periodo.

    Devuelve (actividades, {matricula_id: {actividad_id: valor}}, {matricula_id: definitiva}).
    La definitiva aquí es la de las actividades, sin recuperación.
    """
    actividades = list(Actividad.objects.filter(clase=clase, periodo=periodo))
    por_matricula = defaultdict(dict)
    for n in Nota.objects.filter(actividad__in=actividades):
        por_matricula[n.matricula_id][n.actividad_id] = n.valor
    definitivas = {
        m_id: promedio_ponderado([(vals.get(a.id), a.peso) for a in actividades])
        for m_id, vals in por_matricula.items()
    }
    return actividades, por_matricula, definitivas


def tabla_notas(matricula_ids, periodos, colegio):
    """{(matricula_id, clase_id, periodo_id): nota del periodo}, con recuperaciones aplicadas.

    Dos consultas en total.
    """
    periodo_ids = [p.id for p in periodos]
    pares = defaultdict(list)
    filas = Nota.objects.filter(
        matricula_id__in=matricula_ids, actividad__periodo_id__in=periodo_ids
    ).values_list("matricula_id", "actividad__clase_id", "actividad__periodo_id", "valor", "actividad__peso")
    for m_id, clase_id, periodo_id, valor, peso in filas:
        pares[(m_id, clase_id, periodo_id)].append((valor, peso))
    tabla = {clave: promedio_ponderado(lista) for clave, lista in pares.items()}

    tope = colegio.tope_recuperacion
    for m_id, clase_id, periodo_id, valor in Recuperacion.objects.filter(
        matricula_id__in=matricula_ids, periodo_id__in=periodo_ids
    ).values_list("matricula_id", "clase_id", "periodo_id", "valor"):
        clave = (m_id, clase_id, periodo_id)
        tabla[clave] = aplicar_recuperacion(tabla.get(clave), valor, tope)
    return tabla


def consolidar(matriculas, periodos, colegio):
    """Notas de muchas matrículas a la vez.

    Devuelve {matricula_id: {"clases": {clase_id: {"periodos": {pid: nota}, "final": nota}},
                             "promedios": {pid: nota}, "final": nota}}
    y {grupo_id: [clases]}.
    """
    matriculas = list(matriculas)
    tabla = tabla_notas([m.id for m in matriculas], periodos, colegio)
    clases_por_grupo = defaultdict(list)
    for clase in Clase.objects.filter(grupo_id__in={m.grupo_id for m in matriculas}).select_related(
        "asignatura", "docente"
    ):
        clases_por_grupo[clase.grupo_id].append(clase)

    resultado = {}
    for m in matriculas:
        clases = {}
        for clase in clases_por_grupo[m.grupo_id]:
            por_periodo = {p.id: tabla.get((m.id, clase.id, p.id)) for p in periodos}
            clases[clase.id] = {
                "periodos": por_periodo,
                "final": promedio_ponderado([(por_periodo[p.id], p.peso) for p in periodos]),
            }
        resultado[m.id] = {
            "clases": clases,
            "promedios": {
                p.id: promedio_ponderado([(c["periodos"][p.id], 1) for c in clases.values()]) for p in periodos
            },
            "final": promedio_ponderado([(c["final"], 1) for c in clases.values()]),
        }
    return resultado, clases_por_grupo


def resumen_estudiante(matricula, periodos=None):
    """Notas de un estudiante en todas sus clases.

    Devuelve (filas, promedios por periodo, promedio final). Cada fila:
    {clase, periodos: {periodo_id: nota}, final, faltas}
    """
    grupo = matricula.grupo
    periodos = periodos if periodos is not None else list(grupo.anio.periodos.all())
    datos, clases_por_grupo = consolidar([matricula], periodos, grupo.anio.colegio)
    propio = datos[matricula.id]

    faltas = defaultdict(int)
    for clase_id in Asistencia.objects.filter(
        matricula=matricula, estado__in=[Asistencia.Estado.AUSENTE, Asistencia.Estado.EXCUSA]
    ).values_list("clase_id", flat=True):
        faltas[clase_id] += 1

    filas = [
        {
            "clase": clase, "periodos": propio["clases"][clase.id]["periodos"],
            "final": propio["clases"][clase.id]["final"], "faltas": faltas[clase.id],
        }
        for clase in clases_por_grupo[grupo.id]
    ]
    return filas, propio["promedios"], propio["final"]


def puestos(promedios):
    """{id: promedio} -> ({id: puesto}, cuántos tienen promedio). Empates comparten puesto."""
    ordenados = sorted(((i, p) for i, p in promedios.items() if p is not None), key=lambda par: par[1], reverse=True)
    resultado, anterior, puesto = {}, None, 0
    for indice, (identificador, promedio) in enumerate(ordenados, start=1):
        if promedio != anterior:
            puesto, anterior = indice, promedio
        resultado[identificador] = puesto
    return resultado, len(ordenados)


def puestos_del_grupo(grupo, periodo):
    """Puesto de cada matrícula en el grupo según el promedio del periodo."""
    matriculas = list(grupo.matriculas.filter(activa=True))
    for m in matriculas:
        m.grupo = grupo
    datos, _ = consolidar(matriculas, [periodo], grupo.anio.colegio)
    return puestos({m_id: d["promedios"][periodo.id] for m_id, d in datos.items()})


def perdidas(datos_matricula, colegio, periodo=None):
    """Cuántas asignaturas va perdiendo: en un periodo, o en el acumulado del año si periodo es None."""
    total = 0
    for clase in datos_matricula["clases"].values():
        nota = clase["periodos"][periodo.id] if periodo else clase["final"]
        if nota is not None and nota < colegio.desde_basico:
            total += 1
    return total


def resumen_asistencia(matricula, desde=None, hasta=None):
    qs = Asistencia.objects.filter(matricula=matricula)
    if desde:
        qs = qs.filter(fecha__gte=desde)
    if hasta:
        qs = qs.filter(fecha__lte=hasta)
    conteo = {"P": 0, "T": 0, "A": 0, "E": 0}
    for estado in qs.values_list("estado", flat=True):
        conteo[estado] += 1
    total = sum(conteo.values())
    asistio = conteo["P"] + conteo["T"]
    return {
        "presente": conteo["P"], "tarde": conteo["T"], "ausente": conteo["A"], "excusa": conteo["E"],
        "total": total, "porcentaje": round(asistio * 100 / total) if total else None,
    }
