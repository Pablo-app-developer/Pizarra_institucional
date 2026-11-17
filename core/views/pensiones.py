"""Pensiones: cobros, pagos y cartera. No procesa pagos en línea: registra los que recibe el colegio."""
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..avisos import avisar_estudiantes
from ..forms import CobroForm, GenerarCobrosForm, PagoForm
from ..models import Cobro, Estudiante, Grupo, Matricula, Pago
from ..permisos import capacidad, estudiante_para, familia


def pesos(valor):
    return f"$ {valor:,.0f}".replace(",", ".")


def estado_de_cuenta(estudiante, anio):
    """Cobros del estudiante con lo pagado y el saldo de cada uno, y los totales."""
    hoy = timezone.localdate()
    cobros = list(Cobro.objects.filter(estudiante=estudiante, anio=anio).prefetch_related("pagos"))
    total = {"cobrado": 0, "pagado": 0, "saldo": 0, "vencido": 0}
    for c in cobros:
        c.pagado = sum(p.valor for p in c.pagos.all())
        c.saldo = c.valor - c.pagado
        c.vencido = c.saldo > 0 and c.vence < hoy
        total["cobrado"] += c.valor
        total["pagado"] += c.pagado
        total["saldo"] += c.saldo
        if c.vencido:
            total["vencido"] += c.saldo
    return cobros, total


def cartera_del_anio(anio, grupo=None):
    """Una fila por estudiante matriculado, en tres consultas."""
    hoy = timezone.localdate()
    matriculas = Matricula.objects.filter(grupo__anio=anio, activa=True).select_related("estudiante", "grupo__grado")
    if grupo:
        matriculas = matriculas.filter(grupo=grupo)
    pagado_por_cobro = dict(
        Pago.objects.filter(cobro__anio=anio).values_list("cobro_id").annotate(t=Sum("valor"))
    )
    por_estudiante = {}
    for c in Cobro.objects.filter(anio=anio):
        fila = por_estudiante.setdefault(c.estudiante_id, {"cobrado": 0, "pagado": 0, "vencido": 0})
        pagado = pagado_por_cobro.get(c.id, 0)
        fila["cobrado"] += c.valor
        fila["pagado"] += pagado
        if c.vence < hoy:
            fila["vencido"] += c.valor - pagado
    filas = []
    for m in matriculas:
        f = por_estudiante.get(m.estudiante_id, {"cobrado": 0, "pagado": 0, "vencido": 0})
        filas.append({"matricula": m, **f, "saldo": f["cobrado"] - f["pagado"]})
    return filas


@capacidad("pensiones")
def cartera(request):
    colegio = request.user.colegio
    anio = colegio.anio_activo
    if not anio:
        return render(request, "pensiones/cartera.html", {"anio": None})
    grupos = Grupo.objects.filter(anio=anio).select_related("grado")
    grupo = grupos.filter(pk=request.GET.get("grupo") or 0).first()
    filas = cartera_del_anio(anio, grupo)
    solo_mora = request.GET.get("estado") == "mora"
    totales = {k: sum(f[k] for f in filas) for k in ("cobrado", "pagado", "saldo", "vencido")}
    if solo_mora:
        filas = [f for f in filas if f["vencido"] > 0]
    return render(request, "pensiones/cartera.html", {
        "anio": anio, "grupos": grupos, "grupo": grupo, "filas": filas, "totales": totales,
        "solo_mora": solo_mora, "en_mora": sum(1 for f in filas if f["vencido"] > 0),
        "form": GenerarCobrosForm(),
    })


@capacidad("pensiones")
@require_POST
def generar_cobros(request):
    anio = request.user.colegio.anio_activo
    form = GenerarCobrosForm(request.POST)
    if not anio or not form.is_valid():
        messages.error(request, "Escriba el concepto y la fecha de vencimiento del cobro.")
        return redirect("cartera")
    concepto, vence = form.cleaned_data["concepto"].strip(), form.cleaned_data["vence"]
    ya = set(Cobro.objects.filter(anio=anio, concepto=concepto).values_list("estudiante_id", flat=True))
    nuevos, sin_tarifa = [], 0
    for m in Matricula.objects.filter(grupo__anio=anio, activa=True).select_related("grupo__grado"):
        if m.estudiante_id in ya:
            continue
        if m.grupo.grado.pension <= 0:
            sin_tarifa += 1
            continue
        nuevos.append(Cobro(estudiante_id=m.estudiante_id, anio=anio, concepto=concepto, valor=m.grupo.grado.pension, vence=vence))
    Cobro.objects.bulk_create(nuevos)
    texto = f"Se generaron {len(nuevos)} cobros de «{concepto}»."
    if ya:
        texto += f" {len(ya)} estudiantes ya lo tenían."
    if sin_tarifa:
        texto += f" {sin_tarifa} quedaron sin cobro porque su grado no tiene valor de pensión (Configuración, Grados)."
    messages.success(request, texto)
    return redirect("cartera")


@capacidad("pensiones")
@require_POST
def recordar_mora(request):
    anio = request.user.colegio.anio_activo
    items = [
        (f["matricula"].estudiante,
         f"Recordatorio: {f['matricula'].estudiante.nombres} tiene un saldo vencido de {pesos(f['vencido'])} con el colegio.",
         reverse("familia_pagos", args=[f["matricula"].estudiante_id]))
        for f in cartera_del_anio(anio) if f["vencido"] > 0
    ] if anio else []
    enviados = avisar_estudiantes(items, "Recordatorio de pago")
    messages.success(request, f"Se envió el recordatorio a {enviados} cuentas de {len(items)} estudiantes con saldo vencido.")
    return redirect("cartera")


@capacidad("pensiones")
def cuenta_estudiante(request, pk):
    colegio = request.user.colegio
    est = get_object_or_404(Estudiante, pk=pk, colegio=colegio)
    anio = colegio.anio_activo
    form = CobroForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cobro = form.save(commit=False)
        cobro.estudiante, cobro.anio = est, anio
        if Cobro.objects.filter(estudiante=est, anio=anio, concepto=cobro.concepto).exists():
            form.add_error("concepto", "Este estudiante ya tiene un cobro con ese concepto.")
        else:
            cobro.save()
            messages.success(request, f"Cobro «{cobro.concepto}» agregado.")
            return redirect("cuenta_estudiante", est.pk)
    cobros, total = estado_de_cuenta(est, anio)
    return render(request, "pensiones/cuenta.html", {
        "est": est, "matricula": est.matricula_actual(), "cobros": cobros, "total": total, "form": form,
    })


@capacidad("pensiones")
def registrar_pago(request, pk):
    cobro = get_object_or_404(Cobro.objects.select_related("estudiante"), pk=pk, estudiante__colegio=request.user.colegio)
    saldo = cobro.valor - (cobro.pagos.aggregate(t=Sum("valor"))["t"] or 0)
    if saldo <= 0:
        messages.error(request, "Este cobro ya está pagado.")
        return redirect("cuenta_estudiante", cobro.estudiante_id)
    form = PagoForm(request.POST or None, saldo=saldo)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            pago = form.save(commit=False)
            pago.cobro, pago.registrado_por = cobro, request.user
            pago.save()
        avisar_estudiantes([(
            cobro.estudiante, f"El colegio registró un pago de {pesos(pago.valor)} por «{cobro.concepto}» de {cobro.estudiante.nombres}.",
            reverse("familia_pagos", args=[cobro.estudiante_id]),
        )], "Pago registrado")
        messages.success(request, f"Pago de {pesos(pago.valor)} registrado.")
        return redirect("recibo", pago.pk)
    return render(request, "pensiones/pago.html", {"cobro": cobro, "saldo": saldo, "form": form})


@capacidad("pensiones")
@require_POST
def anular_pago(request, pk):
    pago = get_object_or_404(Pago, pk=pk, cobro__estudiante__colegio=request.user.colegio)
    est_id = pago.cobro.estudiante_id
    pago.delete()
    messages.success(request, "Pago anulado. El saldo del cobro volvió a quedar pendiente.")
    return redirect("cuenta_estudiante", est_id)


@capacidad("pensiones")
@require_POST
def eliminar_cobro(request, pk):
    cobro = get_object_or_404(Cobro, pk=pk, estudiante__colegio=request.user.colegio)
    est_id = cobro.estudiante_id
    if cobro.pagos.exists():
        messages.error(request, "Este cobro tiene pagos registrados. Anule primero los pagos.")
    else:
        cobro.delete()
        messages.success(request, "Cobro eliminado.")
    return redirect("cuenta_estudiante", est_id)


def recibo(request, pk):
    """Recibo imprimible. Lo ven quienes manejan pensiones y los acudientes del estudiante."""
    from django.core.exceptions import PermissionDenied

    from ..permisos import cualquiera, estudiantes_de

    @cualquiera
    def vista(request, pk):
        u = request.user
        pago = get_object_or_404(
            Pago.objects.select_related("cobro__estudiante", "cobro__anio", "registrado_por"),
            pk=pk, cobro__estudiante__colegio=u.colegio,
        )
        permitido = u.puede("pensiones") or (
            u.rol == "acudiente" and estudiantes_de(u).filter(pk=pago.cobro.estudiante_id).exists()
        )
        if not permitido:
            raise PermissionDenied
        return render(request, "pensiones/recibo.html", {"pago": pago, "est": pago.cobro.estudiante})

    return vista(request, pk)


@familia
def familia_pagos(request, pk):
    from django.core.exceptions import PermissionDenied

    from .familia import hermanos_de

    if request.user.rol != "acudiente":
        raise PermissionDenied("El estado de cuenta solo lo consultan los acudientes.")
    est = estudiante_para(request.user, pk)
    matricula = est.matricula_actual()
    if not matricula:
        return render(request, "familia/sin_matricula.html", {"est": est})
    cobros, total = estado_de_cuenta(est, matricula.grupo.anio)
    return render(request, "familia/pagos.html", {
        "est": est, "matricula": matricula, "hermanos": hermanos_de(request.user), "seccion": "pagos",
        "cobros": cobros, "total": total,
    })
