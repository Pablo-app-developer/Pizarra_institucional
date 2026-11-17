"""Horarios: generación automática y validación de cruces."""
import random
from collections import Counter, defaultdict

from .models import DIAS, Bloque, Clase, HorarioClase


def bloques_de_clase(colegio):
    return list(Bloque.objects.filter(colegio=colegio, descanso=False))


def _intento(clases, franjas, rnd):
    """Un intento de armar el horario. Devuelve (asignaciones, horas sin ubicar por clase)."""
    grupo_ocupado, docente_ocupado = set(), set()
    veces_dia = Counter()       # (clase, día) -> horas de esa clase ese día
    carga_dia = Counter()       # (grupo, día) -> horas del grupo ese día
    asignaciones, faltantes = [], Counter()

    # Primero lo más difícil de ubicar: docentes con más carga y clases con más horas.
    carga_docente = Counter()
    for c in clases:
        carga_docente[c.docente_id] += c.horas_semanales
    orden = sorted(clases, key=lambda c: (-carga_docente[c.docente_id], -c.horas_semanales, rnd.random()))

    for clase in orden:
        for _ in range(clase.horas_semanales):
            mejor, mejor_puntaje = None, None
            for dia, indice, bloque in franjas:
                if (clase.grupo_id, dia, bloque.id) in grupo_ocupado:
                    continue
                if clase.docente_id and (clase.docente_id, dia, bloque.id) in docente_ocupado:
                    continue
                # Reparte la asignatura en la semana, equilibra los días y llena desde la primera hora.
                puntaje = (veces_dia[(clase.id, dia)], carga_dia[(clase.grupo_id, dia)], indice, rnd.random())
                if mejor_puntaje is None or puntaje < mejor_puntaje:
                    mejor, mejor_puntaje = (dia, bloque), puntaje
            if mejor is None:
                faltantes[clase.id] += 1
                continue
            dia, bloque = mejor
            grupo_ocupado.add((clase.grupo_id, dia, bloque.id))
            if clase.docente_id:
                docente_ocupado.add((clase.docente_id, dia, bloque.id))
            veces_dia[(clase.id, dia)] += 1
            carga_dia[(clase.grupo_id, dia)] += 1
            asignaciones.append((clase, dia, bloque))
    return asignaciones, faltantes


def generar(anio, intentos=60, semilla=None):
    """Arma el horario de todos los grupos del año y reemplaza el existente.

    Garantiza que ningún docente ni grupo quede con dos clases a la misma hora.
    Devuelve [(clase, horas sin ubicar)] para lo que no cupo.
    """
    bloques = bloques_de_clase(anio.colegio)
    clases = list(Clase.objects.filter(grupo__anio=anio).select_related("grupo__grado", "asignatura", "docente"))
    franjas = [(dia, i, b) for dia, _ in DIAS for i, b in enumerate(bloques)]
    rnd = random.Random(semilla)
    mejor = None
    for _ in range(intentos):
        asignaciones, faltantes = _intento(clases, franjas, rnd)
        if mejor is None or sum(faltantes.values()) < sum(mejor[1].values()):
            mejor = (asignaciones, faltantes)
        if not faltantes:
            break
    asignaciones, faltantes = mejor
    HorarioClase.objects.filter(grupo__anio=anio).delete()
    HorarioClase.objects.bulk_create([
        HorarioClase(grupo_id=c.grupo_id, dia=dia, bloque=b, clase=c) for c, dia, b in asignaciones
    ])
    por_id = {c.id: c for c in clases}
    return [(por_id[i], n) for i, n in faltantes.items()]


def cruces_de_docente(grupo, propuesta):
    """propuesta: {(dia, bloque_id): clase}. Devuelve mensajes por cada docente ocupado en otro grupo."""
    docentes = {c.docente_id for c in propuesta.values() if c.docente_id}
    ocupados = {}
    for h in HorarioClase.objects.filter(
        grupo__anio=grupo.anio, clase__docente_id__in=docentes
    ).exclude(grupo=grupo).select_related("clase__docente", "grupo__grado", "bloque"):
        ocupados[(h.clase.docente_id, h.dia, h.bloque_id)] = h
    nombres_dia = dict(DIAS)
    errores = []
    for (dia, bloque_id), clase in propuesta.items():
        otro = ocupados.get((clase.docente_id, dia, bloque_id)) if clase.docente_id else None
        if otro:
            errores.append(
                f"{clase.docente.nombre_completo} ya tiene clase con {otro.grupo} el {nombres_dia[dia].lower()} "
                f"en {otro.bloque.nombre} ({clase.asignatura})."
            )
    return errores


def rejilla(colegio, entradas):
    """Filas para pintar un horario: [{bloque, celdas: [entrada o None por día]}]."""
    por_franja = {(h.dia, h.bloque_id): h for h in entradas}
    return [
        {"bloque": b, "celdas": [por_franja.get((dia, b.id)) for dia, _ in DIAS]}
        for b in Bloque.objects.filter(colegio=colegio)
    ]


def horas_programadas(grupo):
    cuenta = defaultdict(int)
    for clase_id in HorarioClase.objects.filter(grupo=grupo).values_list("clase_id", flat=True):
        cuenta[clase_id] += 1
    return cuenta
