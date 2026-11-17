"""Da de alta un colegio real con su primera cuenta directiva.

    python manage.py crear_colegio "Colegio San Pedro" --documento 63500111 --nombres "Ana" --apellidos "Ruiz"
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.forms import clave_temporal
from core.models import Colegio, Usuario


class Command(BaseCommand):
    help = "Crea un colegio y su primer usuario directivo."

    def add_arguments(self, parser):
        parser.add_argument("nombre")
        parser.add_argument("--documento", required=True, help="Documento del directivo; será su usuario.")
        parser.add_argument("--nombres", required=True)
        parser.add_argument("--apellidos", required=True)
        parser.add_argument("--correo", default="")

    @transaction.atomic
    def handle(self, *args, **o):
        if Usuario.objects.filter(username=o["documento"]).exists():
            raise CommandError("Ya existe una cuenta con ese documento.")
        colegio = Colegio.objects.create(nombre=o["nombre"])
        clave = clave_temporal(10)
        u = Usuario(username=o["documento"], documento=o["documento"], first_name=o["nombres"],
                    last_name=o["apellidos"], email=o["correo"], rol="directivo", colegio=colegio,
                    debe_cambiar_clave=True)
        u.set_password(clave)
        u.save()
        self.stdout.write(self.style.SUCCESS(f"Colegio creado: {colegio.nombre}"))
        self.stdout.write(f"Usuario: {u.username}\nContraseña temporal: {clave}\n(Se pedirá cambiarla en el primer ingreso.)")
