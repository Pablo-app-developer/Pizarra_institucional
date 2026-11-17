"""Avisos a las familias: quedan en la plataforma y, si hay correo configurado, también se envían."""
import threading

from django.conf import settings
from django.core.mail import send_mass_mail

from .models import Acudiencia, Aviso, Estudiante, Matricula, Usuario


def destinatarios_de(estudiante_ids):
    """{estudiante_id: [usuarios]}: sus acudientes y, si tiene cuenta, el propio estudiante."""
    mapa = {}
    for ac in Acudiencia.objects.filter(estudiante_id__in=estudiante_ids, acudiente__is_active=True).select_related("acudiente"):
        mapa.setdefault(ac.estudiante_id, []).append(ac.acudiente)
    for est in Estudiante.objects.filter(pk__in=estudiante_ids, usuario__isnull=False, usuario__is_active=True).select_related("usuario"):
        mapa.setdefault(est.pk, []).append(est.usuario)
    return mapa


def _avisos_activos(colegio_id):
    from .models import Colegio

    modulos = Colegio.objects.filter(pk=colegio_id).values_list("modulos", flat=True).first() or []
    return "avisos" in modulos


def _enviar_correos(mensajes):
    try:
        send_mass_mail(mensajes, fail_silently=True)
    except Exception:
        pass  # un fallo del servidor de correo nunca debe tumbar el registro de notas


def avisar(pares, asunto):
    """pares: lista de (usuario, texto, url). Crea los avisos y envía los correos en segundo plano."""
    pares = [par for par in pares if par[0].colegio_id and _avisos_activos(par[0].colegio_id)]
    if not pares:
        return 0
    Aviso.objects.bulk_create([Aviso(usuario=u, texto=t[:300], url=url) for u, t, url in pares])
    if settings.CORREO_ACTIVO:
        mensajes = [
            (asunto, f"{t}\n\nIngrese a la plataforma del colegio para ver el detalle.", settings.DEFAULT_FROM_EMAIL, [u.email])
            for u, t, _ in pares if u.email and u.recibir_correos
        ]
        if mensajes:
            threading.Thread(target=_enviar_correos, args=(mensajes,), daemon=True).start()
    return len(pares)


def avisar_estudiantes(items, asunto):
    """items: lista de (estudiante, texto, url). Reparte cada aviso entre sus destinatarios."""
    mapa = destinatarios_de({est.pk for est, _, _ in items})
    return avisar([(u, texto, url) for est, texto, url in items for u in mapa.get(est.pk, [])], asunto)


def avisar_grupo(grupo, texto_para, url_para, asunto):
    """Un aviso por cada estudiante activo del grupo. texto_para y url_para reciben el estudiante."""
    estudiantes = [m.estudiante for m in grupo.matriculas.filter(activa=True).select_related("estudiante")]
    return avisar_estudiantes([(e, texto_para(e), url_para(e)) for e in estudiantes], asunto)


def avisar_comunicado(comunicado):
    from django.urls import reverse

    usuarios = Usuario.objects.filter(colegio=comunicado.colegio, is_active=True, rol__in=["acudiente", "estudiante"])
    if comunicado.grupo_id:
        estudiantes = Matricula.objects.filter(grupo=comunicado.grupo, activa=True).values("estudiante_id")
        usuarios = usuarios.filter(acudidos__in=estudiantes) | usuarios.filter(estudiante__in=estudiantes)
    url = reverse("comunicados")
    return avisar([(u, f"Nuevo comunicado: {comunicado.titulo}", url) for u in usuarios.distinct()], f"Comunicado: {comunicado.titulo}")
