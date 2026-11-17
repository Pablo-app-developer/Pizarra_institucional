"""Bitácora de acciones sensibles."""
from .models import Bitacora


def registrar(request, accion, detalle=""):
    Bitacora.objects.create(
        colegio_id=request.user.colegio_id, usuario=request.user, accion=accion[:60], detalle=str(detalle)[:400]
    )
