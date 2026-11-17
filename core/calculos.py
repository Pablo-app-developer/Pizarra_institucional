"""Cálculo de notas. Toda la aritmética académica vive aquí para poder probarla."""
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from .models import Actividad, Asistencia, Nota

UNA_DECIMAL = Decimal("0.1")


def redondear(valor):
    if valor is None:
        return None
    return Decimal(valor).quantize(UNA_DECIMAL, rounding=ROUND_HALF_UP)


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


def notas_de_clase(clase, periodo):
    """Planilla de una clase en un periodo.

    Devuelve (actividades, {matricula_id: {actividad_id: valor}}, {matricula_id: definitiva}).
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


def resumen_estudiante(matricula, periodos=None):
    """Notas de un estudiante en todas sus clases.

    Devuelve una lista de filas, una por clase:
    {clase, periodos: {periodo_id: nota}, final, faltas}
    y un dict de promedios generales por periodo.
    """
    grupo = matricula.grupo
    periodos = periodos if periodos is not None else list(grupo.anio.periodos.all())
    clases = list(grupo.clases.select_related("asignatura", "docente"))

    acumulado = defaultdict(list)  # (clase_id, periodo_id) -> [(valor, peso)]
    notas = Nota.objects.filter(matricula=matricula).select_related("actividad")
    for n in notas:
        acumulado[(n.actividad.clase_id, n.actividad.periodo_id)].append((n.valor, n.actividad.peso))

    faltas = defaultdict(int)
    for a in Asistencia.objects.filter(
        matricula=matricula, estado__in=[Asistencia.Estado.AUSENTE, Asistencia.Estado.EXCUSA]
    ):
        faltas[a.clase_id] += 1

    filas = []
    for clase in clases:
        por_periodo = {p.id: promedio_ponderado(acumulado.get((clase.id, p.id), [])) for p in periodos}
        final = promedio_ponderado([(por_periodo[p.id], p.peso) for p in periodos])
        filas.append({"clase": clase, "periodos": por_periodo, "final": final, "faltas": faltas[clase.id]})

    promedios = {
        p.id: promedio_ponderado([(f["periodos"][p.id], 1) for f in filas]) for p in periodos
    }
    promedio_final = promedio_ponderado([(f["final"], 1) for f in filas])
    return filas, promedios, promedio_final


def puestos_del_grupo(grupo, periodo):
    """Puesto de cada matrícula en el grupo según el promedio del periodo."""
    promedios = {}
    for m in grupo.matriculas.filter(activa=True):
        _, proms, _ = resumen_estudiante(m, periodos=[periodo])
        if proms[periodo.id] is not None:
            promedios[m.id] = proms[periodo.id]
    ordenados = sorted(promedios.items(), key=lambda par: par[1], reverse=True)
    puestos, anterior, puesto = {}, None, 0
    for indice, (m_id, prom) in enumerate(ordenados, start=1):
        if prom != anterior:
            puesto = indice
            anterior = prom
        puestos[m_id] = puesto
    return puestos, len(ordenados)


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
