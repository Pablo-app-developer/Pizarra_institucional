from decimal import Decimal
from io import BytesIO, StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from . import calculos
from .models import (
    Actividad, Acudiencia, Asistencia, Clase, Colegio, Estudiante, Grupo, Matricula, Nota, Periodo, Usuario,
)


class PromedioTests(TestCase):
    def test_sin_notas_es_none(self):
        self.assertIsNone(calculos.promedio_ponderado([(None, 30), (None, 70)]))

    def test_pesos_en_cero_promedia_simple(self):
        self.assertEqual(calculos.promedio_ponderado([(4, 0), (3, 0)]), Decimal("3.5"))

    def test_ponderado(self):
        self.assertEqual(calculos.promedio_ponderado([(5, 30), (3, 70)]), Decimal("3.6"))

    def test_parcial_se_normaliza_sobre_lo_calificado(self):
        # Solo se ha calificado el 30 %: la nota parcial no debe quedar en 1.5.
        self.assertEqual(calculos.promedio_ponderado([(5, 30), (None, 70)]), Decimal("5.0"))

    def test_redondeo_hacia_arriba_en_la_mitad(self):
        self.assertEqual(calculos.promedio_ponderado([(Decimal("2.9"), 1), (Decimal("3.0"), 1)]), Decimal("3.0"))
        self.assertEqual(calculos.redondear(Decimal("3.25")), Decimal("3.3"))

    def test_desempeno_en_los_limites(self):
        c = Colegio(nombre="x")
        self.assertEqual(c.desempeno(Decimal("2.9"))[0], "bajo")
        self.assertEqual(c.desempeno(Decimal("3.0"))[0], "basico")
        self.assertEqual(c.desempeno(Decimal("4.0"))[0], "alto")
        self.assertEqual(c.desempeno(Decimal("4.6"))[0], "superior")
        self.assertIsNone(c.desempeno(None))


class ConDatos(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("datos_demo", stdout=StringIO())
        cls.colegio = Colegio.objects.get()
        cls.directivo = Usuario.objects.get(username="1001")
        cls.docente = Usuario.objects.get(username="2001")
        cls.otro_docente = Usuario.objects.get(username="2002")
        cls.grupo = Grupo.objects.get(grado__orden=6)
        cls.clase = Clase.objects.get(docente=cls.docente, grupo=cls.grupo)
        cls.clase_ajena = Clase.objects.get(docente=cls.otro_docente, grupo=cls.grupo)
        cls.matricula = cls.grupo.matriculas.first()
        cls.est = cls.matricula.estudiante
        cls.acudiente = cls.est.acudientes.first()
        ajena = Matricula.objects.exclude(estudiante__acudientes=cls.acudiente).first()
        cls.est_ajeno, cls.matricula_ajena = ajena.estudiante, ajena
        cls.abierto = Periodo.objects.get(numero=4)
        cls.cerrado = Periodo.objects.get(numero=1)

    def entrar(self, usuario):
        self.client.force_login(usuario)


class AccesoTests(ConDatos):
    def test_anonimo_va_al_ingreso(self):
        r = self.client.get(reverse("panel"))
        self.assertRedirects(r, "/ingresar/?next=/colegio/")

    def test_inicio_redirige_por_rol(self):
        for usuario, destino in [(self.directivo, "/colegio/"), (self.docente, "/clases/")]:
            self.entrar(usuario)
            self.assertRedirects(self.client.get("/"), destino)

    def test_acudiente_no_entra_a_administracion_ni_a_clases(self):
        self.entrar(self.acudiente)
        for url in ["/colegio/", "/colegio/estudiantes/", "/clases/", f"/clases/{self.clase.pk}/notas/",
                    f"/observador/{self.matricula.pk}/", "/colegio/consolidado/"]:
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_acudiente_solo_ve_a_sus_estudiantes(self):
        self.entrar(self.acudiente)
        self.assertEqual(self.client.get(reverse("familia_resumen", args=[self.est.pk])).status_code, 200)
        for nombre in ["familia_resumen", "familia_notas", "familia_tareas", "familia_asistencia",
                       "familia_observaciones", "familia_boletines"]:
            self.assertEqual(self.client.get(reverse(nombre, args=[self.est_ajeno.pk])).status_code, 404, nombre)

    def test_boletin_de_otro_estudiante_prohibido(self):
        self.entrar(self.acudiente)
        r = self.client.get(reverse("boletin_estudiante", args=[self.matricula_ajena.pk, self.cerrado.pk]))
        self.assertEqual(r.status_code, 403)

    def test_familia_no_descarga_boletin_de_periodo_abierto(self):
        self.entrar(self.acudiente)
        r = self.client.get(reverse("boletin_estudiante", args=[self.matricula.pk, self.abierto.pk]))
        self.assertEqual(r.status_code, 403)

    def test_familia_descarga_boletin_de_periodo_cerrado(self):
        self.entrar(self.acudiente)
        r = self.client.get(reverse("boletin_estudiante", args=[self.matricula.pk, self.cerrado.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_observacion_interna_no_la_ve_la_familia(self):
        self.matricula.observaciones.create(autor=self.docente, texto="Nota reservada del comité", visible_familia=False)
        self.entrar(self.acudiente)
        r = self.client.get(reverse("familia_observaciones", args=[self.est.pk]))
        self.assertNotContains(r, "Nota reservada del comité")

    def test_docente_no_entra_a_clase_ajena(self):
        self.entrar(self.docente)
        self.assertEqual(self.client.get(reverse("clase_notas", args=[self.clase.pk])).status_code, 200)
        for nombre in ["clase_notas", "clase_tareas", "clase_asistencia", "clase_estudiantes"]:
            self.assertEqual(self.client.get(reverse(nombre, args=[self.clase_ajena.pk])).status_code, 403, nombre)
        self.assertEqual(self.client.get("/colegio/").status_code, 403)

    def test_otro_colegio_no_ve_nada(self):
        otro = Colegio.objects.create(nombre="Otro colegio")
        intruso = Usuario.objects.create_user("9001", password="x", rol="directivo", colegio=otro, cargo="rector",
                                              permisos=Usuario.PERMISOS_POR_CARGO["rector"])
        self.entrar(intruso)
        self.assertEqual(self.client.get(reverse("ficha_estudiante", args=[self.est.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("clase_notas", args=[self.clase.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("gestion_editar", args=["estudiantes", self.est.pk])).status_code, 404)
        self.assertNotContains(self.client.get("/colegio/estudiantes/"), self.est.apellidos)

    def test_clave_temporal_obliga_a_cambiarla(self):
        self.acudiente.debe_cambiar_clave = True
        self.acudiente.save()
        self.entrar(self.acudiente)
        r = self.client.get(reverse("familia_resumen", args=[self.est.pk]))
        self.assertRedirects(r, reverse("cambiar_clave"))


class PlanillaTests(ConDatos):
    def setUp(self):
        self.entrar(self.docente)
        self.url = reverse("clase_notas", args=[self.clase.pk])
        self.actividad = Actividad.objects.filter(clase=self.clase, periodo=self.abierto).first()
        self.campo = f"n_{self.matricula.pk}_{self.actividad.pk}"

    def test_guarda_con_coma_decimal(self):
        r = self.client.post(self.url, {"periodo": self.abierto.pk, self.campo: "4,7"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Nota.objects.get(actividad=self.actividad, matricula=self.matricula).valor, Decimal("4.7"))

    def test_vacio_borra_la_nota(self):
        self.client.post(self.url, {"periodo": self.abierto.pk, self.campo: ""})
        self.assertFalse(Nota.objects.filter(actividad=self.actividad, matricula=self.matricula).exists())

    def test_nota_fuera_de_escala_no_guarda_nada(self):
        otra = self.grupo.matriculas.all()[1]
        antes = Nota.objects.get(actividad=self.actividad, matricula=otra).valor
        r = self.client.post(self.url, {
            "periodo": self.abierto.pk, self.campo: "7", f"n_{otra.pk}_{self.actividad.pk}": "1,0",
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "No se guardó nada")
        self.assertEqual(Nota.objects.get(actividad=self.actividad, matricula=otra).valor, antes)

    def test_texto_no_numerico_se_rechaza(self):
        for malo in ["abc", "NaN", "inf", "-1", "5.1"]:
            r = self.client.post(self.url, {"periodo": self.abierto.pk, self.campo: malo})
            self.assertEqual(r.status_code, 200, malo)

    def test_periodo_cerrado_no_admite_cambios(self):
        act = Actividad.objects.filter(clase=self.clase, periodo=self.cerrado).first()
        r = self.client.post(self.url, {"periodo": self.cerrado.pk, f"n_{self.matricula.pk}_{act.pk}": "1"})
        self.assertEqual(r.status_code, 403)
        r = self.client.post(reverse("actividad_nueva", args=[self.clase.pk]) + f"?periodo={self.cerrado.pk}",
                             {"titulo": "x", "tipo": "taller", "fecha": "2026-01-01", "peso": "0"})
        self.assertEqual(r.status_code, 403)

    def test_no_puede_calificar_matricula_de_otro_grupo(self):
        self.client.post(self.url, {"periodo": self.abierto.pk, f"n_{self.matricula_ajena.pk}_{self.actividad.pk}": "5"})
        self.assertFalse(Nota.objects.filter(actividad=self.actividad, matricula=self.matricula_ajena).exists())

    def test_asistencia_se_guarda_y_se_corrige(self):
        url = reverse("clase_asistencia", args=[self.clase.pk])
        self.client.post(url, {"fecha": "2026-11-16", f"a_{self.matricula.pk}": "A"})
        self.client.post(url, {"fecha": "2026-11-16", f"a_{self.matricula.pk}": "E"})
        registros = Asistencia.objects.filter(clase=self.clase, matricula=self.matricula, fecha="2026-11-16")
        self.assertEqual([a.estado for a in registros], ["E"])


class GestionTests(ConDatos):
    def setUp(self):
        self.entrar(self.directivo)

    def test_crear_docente_muestra_clave_temporal_una_vez(self):
        r = self.client.post(reverse("gestion_nuevo", args=["docentes"]), {
            "first_name": "Ana", "last_name": "Pérez", "documento": "55555", "email": "", "telefono": "", "is_active": "on",
        }, follow=True)
        self.assertContains(r, "Contraseña temporal")
        nuevo = Usuario.objects.get(username="55555")
        self.assertEqual((nuevo.rol, nuevo.colegio, nuevo.debe_cambiar_clave), ("docente", self.colegio, True))

    def test_documento_repetido_se_rechaza(self):
        r = self.client.post(reverse("gestion_nuevo", args=["estudiantes"]), {
            "nombres": "X", "apellidos": "Y", "tipo_documento": "TI", "documento": self.est.documento, "activo": "on",
        })
        self.assertContains(r, "Ya hay un estudiante con este documento")

    def test_cambiar_de_grupo_deja_una_sola_matricula_activa(self):
        destino = Grupo.objects.get(grado__orden=7)
        self.client.post(reverse("gestion_editar", args=["estudiantes", self.est.pk]), {
            "nombres": self.est.nombres, "apellidos": self.est.apellidos, "tipo_documento": "TI",
            "documento": self.est.documento, "activo": "on", "grupo": destino.pk,
        })
        activas = Matricula.objects.filter(estudiante=self.est, activa=True)
        self.assertEqual([m.grupo for m in activas], [destino])

    def test_eliminar_docente_con_clases_avisa_sin_romper(self):
        r = self.client.post(reverse("gestion_eliminar", args=["docentes", self.docente.pk]), follow=True)
        self.assertContains(r, "tiene información asociada")
        self.assertTrue(Usuario.objects.filter(pk=self.docente.pk).exists())

    def test_vincular_acudiente_existente_por_documento(self):
        r = self.client.post(reverse("ficha_estudiante", args=[self.est_ajeno.pk]), {
            "documento": self.acudiente.username, "parentesco": "tio",
        })
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Acudiencia.objects.filter(estudiante=self.est_ajeno, acudiente=self.acudiente).exists())

    def test_cerrar_periodo(self):
        self.client.post(reverse("alternar_periodo", args=[self.abierto.pk]))
        self.abierto.refresh_from_db()
        self.assertTrue(self.abierto.cerrado)

    def test_importar_excel(self):
        libro = Workbook()
        hoja = libro.active
        hoja.append(["tipo_documento", "documento", "nombres", "apellidos", "grupo", "acudiente_documento",
                     "acudiente_nombres", "acudiente_apellidos", "acudiente_parentesco"])
        hoja.append(["TI", 1234567890.0, "Nuevo", "Estudiante Uno", "6°A", "777", "Mamá", "Nueva", "madre"])
        hoja.append(["TI", "1234567891", "Sin", "Grupo Válido", "99Z", "", "", "", ""])
        hoja.append(["TI", "", "Falta", "Documento", "", "", "", "", ""])
        hoja.append(["TI", self.est.documento, "Actualizado", self.est.apellidos, "", "", "", "", ""])
        archivo = BytesIO()
        libro.save(archivo)
        archivo.seek(0)
        archivo.name = "lista.xlsx"
        r = self.client.post(reverse("importar"), {"archivo": archivo})
        res = r.context["resultado"]
        self.assertEqual((res["creados"], res["actualizados"], len(res["errores"])), (1, 1, 2))
        nuevo = Estudiante.objects.get(documento="1234567890")
        self.assertEqual(str(nuevo.matricula_actual().grupo), "6°A")
        self.assertEqual(res["cuentas"][0]["usuario"], "777")
        self.assertTrue(Usuario.objects.get(username="777").check_password(res["cuentas"][0]["clave"]))
        self.est.refresh_from_db()
        self.assertEqual(self.est.nombres, "Actualizado")


class TodasLasPaginasTests(ConDatos):
    """Cada pantalla abre sin error para el rol que le corresponde."""

    def test_directivo(self):
        self.entrar(self.directivo)
        urls = ["/colegio/", "/colegio/grupos/", "/colegio/consolidado/", "/colegio/boletines/",
                "/colegio/configuracion/", "/colegio/importar/", "/colegio/importar/plantilla/", "/comunicados/",
                reverse("ficha_estudiante", args=[self.est.pk]), reverse("observador", args=[self.matricula.pk]),
                reverse("direccion_grupo", args=[self.grupo.pk]), reverse("clase_notas", args=[self.clase.pk]),
                reverse("boletines_grupo", args=[self.grupo.pk, self.cerrado.pk]), "/cuenta/clave/"]
        for entidad in ["estudiantes", "docentes", "acudientes", "anios", "periodos", "grados", "asignaturas", "clases", "comunicados"]:
            urls += [f"/colegio/{entidad}/", f"/colegio/{entidad}/nuevo/"]
        urls += ["/colegio/grupos/nuevo/", "/colegio/estudiantes/?q=rojas"]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_docente(self):
        self.entrar(self.docente)
        tarea = self.clase.tareas.first()
        act = self.clase.actividades.filter(periodo=self.abierto).first()
        urls = ["/clases/", "/comunicados/", reverse("direccion_grupo", args=[self.grupo.pk]),
                reverse("observador", args=[self.matricula.pk]),
                reverse("actividad_nueva", args=[self.clase.pk]), reverse("actividad_editar", args=[self.clase.pk, act.pk]),
                reverse("tarea_nueva", args=[self.clase.pk]), reverse("tarea_editar", args=[self.clase.pk, tarea.pk]),
                reverse("tarea_entregas", args=[self.clase.pk, tarea.pk]),
                reverse("clase_notas", args=[self.clase.pk]) + f"?periodo={self.cerrado.pk}"]
        urls += [reverse(n, args=[self.clase.pk]) for n in ["clase_notas", "clase_tareas", "clase_asistencia", "clase_estudiantes"]]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_familia(self):
        self.entrar(self.acudiente)
        for nombre in ["familia_resumen", "familia_notas", "familia_tareas", "familia_asistencia",
                       "familia_observaciones", "familia_boletines"]:
            self.assertEqual(self.client.get(reverse(nombre, args=[self.est.pk])).status_code, 200, nombre)
        self.assertEqual(self.client.get("/comunicados/").status_code, 200)


# ====================================================================
# Segunda entrega: cargos, plan, horarios, recuperaciones, cierre de año,
# avisos, entregas en línea, pensiones, preescolar y escudo.
# ====================================================================
from datetime import timedelta  # noqa: E402

from django.core import mail  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.utils import timezone  # noqa: E402

from . import horarios as motor  # noqa: E402
from .models import (  # noqa: E402
    AnioLectivo, Asignatura, Aviso, Bitacora, Bloque, Cobro, Entrega, HorarioClase, Logro, Pago, PlanEstudio, Recuperacion, Tarea,
    ValoracionDescriptiva,
)
from .views.academico import crear_anio_siguiente  # noqa: E402


class RecuperacionCalculoTests(TestCase):
    def test_reglas(self):
        f = calculos.aplicar_recuperacion
        self.assertEqual(f(Decimal("2.5"), None, Decimal("3.0")), Decimal("2.5"))
        self.assertEqual(f(Decimal("2.5"), Decimal("4.5"), Decimal("3.0")), Decimal("3.0"))   # tope
        self.assertEqual(f(Decimal("2.5"), Decimal("4.5"), None), Decimal("4.5"))             # sin tope
        self.assertEqual(f(Decimal("2.5"), Decimal("2.0"), Decimal("3.0")), Decimal("2.5"))   # nunca empeora
        self.assertEqual(f(None, Decimal("3.5"), Decimal("3.0")), Decimal("3.0"))

    def test_puestos_con_empate(self):
        puestos, total = calculos.puestos({1: Decimal("4.5"), 2: Decimal("4.5"), 3: Decimal("4.0"), 4: None})
        self.assertEqual((puestos, total), ({1: 1, 2: 1, 3: 3}, 3))


class CargosTests(ConDatos):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.coordinador = Usuario.objects.get(username="1002")
        cls.secretaria = Usuario.objects.get(username="1003")

    def test_secretaria_no_entra_a_lo_academico_ni_a_configurar(self):
        self.entrar(self.secretaria)
        for url in ["/colegio/consolidado/", "/colegio/grupos/", "/colegio/plan/", "/colegio/horarios/",
                    "/colegio/cierre/", "/colegio/docentes/", "/colegio/periodos/", "/colegio/directivos/",
                    "/colegio/configuracion/", reverse("clase_notas", args=[self.clase.pk]),
                    reverse("observador", args=[self.matricula.pk])]:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        r = self.client.post(reverse("alternar_periodo", args=[self.abierto.pk]))
        self.assertEqual(r.status_code, 403)
        self.abierto.refresh_from_db()
        self.assertFalse(self.abierto.cerrado)

    def test_secretaria_si_maneja_estudiantes_boletines_y_pensiones(self):
        self.entrar(self.secretaria)
        for url in ["/colegio/", "/colegio/estudiantes/", "/colegio/acudientes/", "/colegio/boletines/",
                    "/colegio/pensiones/", "/colegio/importar/", reverse("ficha_estudiante", args=[self.est.pk]),
                    reverse("cuenta_estudiante", args=[self.est.pk])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_coordinador_no_ve_pensiones_ni_configura_el_colegio(self):
        self.entrar(self.coordinador)
        for url in ["/colegio/pensiones/", reverse("cuenta_estudiante", args=[self.est.pk]), "/colegio/directivos/", "/colegio/bitacora/"]:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.client.post("/colegio/configuracion/", {"nombre": "Cambiado"})
        self.colegio.refresh_from_db()
        self.assertNotEqual(self.colegio.nombre, "Cambiado")
        for url in ["/colegio/consolidado/", "/colegio/plan/", "/colegio/horarios/", "/colegio/cierre/",
                    "/colegio/configuracion/", reverse("clase_notas", args=[self.clase.pk])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_solo_rectoria_restablece_clave_de_un_directivo(self):
        self.entrar(self.secretaria)
        r = self.client.post(reverse("restablecer_clave", args=[self.directivo.pk]))
        self.assertEqual(r.status_code, 403)
        self.assertTrue(Usuario.objects.get(pk=self.directivo.pk).check_password("pizarra2026"))

    def test_rector_crea_un_coordinador(self):
        self.entrar(self.directivo)
        self.client.post(reverse("gestion_nuevo", args=["directivos"]), {
            "first_name": "Luz", "last_name": "Gil", "documento": "88001", "cargo": "coordinador", "is_active": "on",
            "permisos": ["notas", "boletines"],
        })
        nuevo = Usuario.objects.get(username="88001")
        self.assertEqual((nuevo.rol, nuevo.cargo, nuevo.permisos), ("directivo", "coordinador", ["boletines", "notas"]))
        self.assertFalse(nuevo.puede("pensiones"))
        self.assertTrue(Bitacora.objects.filter(accion="Permisos de directivo", detalle__contains="Luz Gil").exists())


class PlanYHorarioTests(ConDatos):
    def setUp(self):
        self.entrar(self.directivo)
        self.anio = self.colegio.anio_activo

    def _cruces(self):
        vistos, cruces = set(), 0
        for h in HorarioClase.objects.filter(grupo__anio=self.anio).select_related("clase"):
            clave = (h.clase.docente_id, h.dia, h.bloque_id)
            cruces += clave in vistos
            vistos.add(clave)
        return cruces

    def test_horario_generado_completo_y_sin_cruces(self):
        faltantes = motor.generar(self.anio, semilla=1)
        self.assertEqual(faltantes, [])
        self.assertEqual(self._cruces(), 0)
        for clase in Clase.objects.filter(grupo__anio=self.anio):
            self.assertEqual(clase.horario.count(), clase.horas_semanales, str(clase))
        self.assertFalse(HorarioClase.objects.filter(bloque__descanso=True).exists())

    def test_horario_informa_lo_que_no_cabe(self):
        Bloque.objects.filter(colegio=self.colegio, descanso=False).exclude(nombre="1ª hora").delete()
        faltantes = motor.generar(self.anio, semilla=1)   # solo 5 horas a la semana por grupo
        self.assertTrue(faltantes)
        self.assertEqual(self._cruces(), 0)

    def test_editor_rechaza_cruce_de_docente(self):
        otro_grupo = Grupo.objects.get(grado__orden=7)
        ocupado = HorarioClase.objects.filter(grupo=otro_grupo, clase__docente=self.docente).first()
        antes = list(HorarioClase.objects.filter(grupo=self.grupo).values_list("dia", "bloque_id", "clase_id"))
        r = self.client.post(reverse("horario_grupo", args=[self.grupo.pk]), {
            f"h_{ocupado.dia}_{ocupado.bloque_id}": self.clase.pk,
        })
        self.assertContains(r, "ya tiene clase con")
        self.assertEqual(antes, list(HorarioClase.objects.filter(grupo=self.grupo).values_list("dia", "bloque_id", "clase_id")))

    def test_editor_ignora_clases_de_otro_grupo(self):
        ajena = Clase.objects.filter(grupo__grado__orden=7).first()
        bloque = Bloque.objects.filter(colegio=self.colegio, descanso=False).first()
        self.client.post(reverse("horario_grupo", args=[self.grupo.pk]), {f"h_1_{bloque.pk}": ajena.pk})
        self.assertFalse(HorarioClase.objects.filter(grupo=self.grupo).exists())

    def test_aplicar_plan_crea_solo_lo_que_falta(self):
        Clase.objects.filter(grupo=self.grupo, asignatura__nombre="Inglés").delete()
        self.client.post(reverse("plan_aplicar", args=[self.grupo.grado_id]))
        nueva = Clase.objects.get(grupo=self.grupo, asignatura__nombre="Inglés")
        self.assertIsNone(nueva.docente)
        self.assertEqual(self.grupo.clases.count(), 6)
        self.assertEqual(self.client.get("/colegio/grupos/").status_code, 200)      # clase sin docente no rompe
        self.assertEqual(self.client.get(reverse("clase_notas", args=[nueva.pk])).status_code, 200)

    def test_docente_edita_logros_solo_de_lo_que_dicta(self):
        self.entrar(self.docente)
        propio = PlanEstudio.objects.get(grado=self.grupo.grado, asignatura=self.clase.asignatura)
        ajeno = PlanEstudio.objects.get(grado=self.grupo.grado, asignatura=self.clase_ajena.asignatura)
        r = self.client.post(reverse("logros", args=[propio.pk]), {"periodo_numero": 4, "texto": "Logro nuevo de prueba"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Logro.objects.filter(texto="Logro nuevo de prueba", grado=self.grupo.grado).exists())
        self.assertEqual(self.client.get(reverse("logros", args=[ajeno.pk])).status_code, 403)

    def test_malla_y_horarios_de_cada_rol(self):
        self.assertEqual(self.client.get(reverse("malla", args=[self.grupo.grado_id])).status_code, 200)
        self.entrar(self.docente)
        self.assertContains(self.client.get("/horario/"), "6°A")
        self.entrar(self.acudiente)
        self.assertContains(self.client.get(reverse("familia_horario", args=[self.est.pk])), "Matemáticas")


class RecuperacionYCierreTests(ConDatos):
    def test_recuperacion_sube_la_nota_hasta_el_tope(self):
        self.entrar(self.docente)
        periodo = Periodo.objects.get(numero=2)
        actividades = Actividad.objects.filter(clase=self.clase, periodo=periodo)
        Nota.objects.filter(actividad__in=actividades, matricula=self.matricula).update(valor=Decimal("2.0"))
        url = reverse("clase_recuperaciones", args=[self.clase.pk]) + f"?periodo={periodo.pk}"
        self.assertContains(self.client.get(url), str(self.est))
        self.client.post(url, {"periodo": periodo.pk, f"r_{self.matricula.pk}": "4,8"})
        self.assertEqual(Recuperacion.objects.get(matricula=self.matricula, clase=self.clase, periodo=periodo).valor, Decimal("4.8"))
        filas, _, _ = calculos.resumen_estudiante(self.matricula, [periodo])
        nota = next(f for f in filas if f["clase"] == self.clase)["periodos"][periodo.id]
        self.assertEqual(nota, Decimal("3.0"))
        # Las notas de las actividades no se tocan.
        self.assertEqual(set(Nota.objects.filter(actividad__in=actividades, matricula=self.matricula).values_list("valor", flat=True)), {Decimal("2.0")})

    def test_recuperacion_invalida_no_guarda(self):
        self.entrar(self.docente)
        periodo = Periodo.objects.get(numero=2)
        Nota.objects.filter(actividad__clase=self.clase, actividad__periodo=periodo, matricula=self.matricula).update(valor=Decimal("2.0"))
        url = reverse("clase_recuperaciones", args=[self.clase.pk])
        r = self.client.post(url, {"periodo": periodo.pk, f"r_{self.matricula.pk}": "9"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Recuperacion.objects.filter(matricula=self.matricula, clase=self.clase, periodo=periodo).exists())

    def test_cierre_exige_todos_los_resultados(self):
        anio = self.colegio.anio_activo
        matriculas = list(Matricula.objects.filter(grupo__anio=anio, activa=True).select_related("grupo__grado", "estudiante"))
        with self.assertRaises(ValueError):
            crear_anio_siguiente(anio, matriculas)
        self.assertEqual(AnioLectivo.objects.count(), 1)

    def test_crear_anio_siguiente(self):
        self.entrar(self.directivo)
        anio = self.colegio.anio_activo
        self.assertEqual(self.client.get("/colegio/cierre/").status_code, 200)
        activas = Matricula.objects.filter(grupo__anio=anio, activa=True)
        datos = {f"r_{m.pk}": "promovido" for m in activas}
        repite = activas.filter(grupo=self.grupo).first()
        datos[f"r_{repite.pk}"] = "reprobado"
        datos["accion"] = "guardar"
        self.client.post("/colegio/cierre/", datos)
        r = self.client.post("/colegio/cierre/", {"accion": "crear"}, follow=True)
        self.assertContains(r, "creado")

        nuevo = AnioLectivo.objects.get(nombre=str(int(anio.nombre) + 1))
        self.assertFalse(nuevo.activo)
        anio.refresh_from_db()
        self.assertTrue(anio.activo)                                   # el año actual no se toca
        self.assertEqual(nuevo.periodos.count(), 4)
        self.assertFalse(nuevo.periodos.filter(cerrado=True).exists())
        self.assertEqual(nuevo.grupos.count(), anio.grupos.count())
        self.assertEqual(Matricula.objects.get(estudiante=repite.estudiante, grupo__anio=nuevo).grupo.grado.orden, 6)
        otro = activas.filter(grupo=self.grupo).exclude(pk=repite.pk).first()
        self.assertEqual(Matricula.objects.get(estudiante=otro.estudiante, grupo__anio=nuevo).grupo.grado.orden, 7)
        # Los de octavo (último grado) se gradúan: no tienen matrícula en el año nuevo.
        graduado = activas.filter(grupo__grado__orden=8).first()
        self.assertFalse(Matricula.objects.filter(estudiante=graduado.estudiante, grupo__anio=nuevo).exists())
        # Las clases del año nuevo conservan docente.
        self.assertFalse(Clase.objects.filter(grupo__anio=nuevo, grupo__grado__orden=6, docente__isnull=True).exists())
        # No se puede crear dos veces.
        self.client.post("/colegio/cierre/", {"accion": "crear"})
        self.assertEqual(AnioLectivo.objects.filter(nombre=nuevo.nombre).count(), 1)
        # Al activar el año nuevo, la familia ve la matrícula nueva.
        nuevo.activo = True
        nuevo.save()
        self.assertEqual(otro.estudiante.matricula_actual().grupo.anio, nuevo)


class AvisosTests(ConDatos):
    def setUp(self):
        self.entrar(self.docente)
        Aviso.objects.all().delete()

    def test_nota_baja_avisa_al_acudiente_y_no_a_otros(self):
        act = Actividad.objects.filter(clase=self.clase, periodo=self.abierto).first()
        self.client.post(reverse("clase_notas", args=[self.clase.pk]), {"periodo": self.abierto.pk, f"n_{self.matricula.pk}_{act.pk}": "1,5"})
        destinatarios = set(Aviso.objects.values_list("usuario_id", flat=True))
        self.assertEqual(destinatarios, set(self.est.acudientes.values_list("pk", flat=True)))
        self.assertIn("1,5", Aviso.objects.first().texto)

    def test_nota_buena_no_avisa(self):
        act = Actividad.objects.filter(clase=self.clase, periodo=self.abierto).first()
        self.client.post(reverse("clase_notas", args=[self.clase.pk]), {"periodo": self.abierto.pk, f"n_{self.matricula.pk}_{act.pk}": "4,5"})
        self.assertFalse(Aviso.objects.exists())

    def test_falta_avisa_una_sola_vez(self):
        url = reverse("clase_asistencia", args=[self.clase.pk])
        for _ in range(2):
            self.client.post(url, {"fecha": "2026-11-16", f"a_{self.matricula.pk}": "A"})
        self.assertEqual(Aviso.objects.filter(usuario=self.acudiente).count(), 1)

    @override_settings(CORREO_ACTIVO=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_correo_respeta_la_preferencia(self):
        from . import avisos

        self.acudiente.email = "acudiente@example.com"
        self.acudiente.save()
        with self.settings():
            avisos._enviar_correos  # existe
            mensajes = []
            original = avisos.threading.Thread
            avisos.threading.Thread = lambda target, args, daemon: type("H", (), {"start": lambda self: target(*args)})()
            try:
                avisos.avisar([(self.acudiente, "Aviso de prueba", "/")], "Asunto")
                self.assertEqual(len(mail.outbox), 1)
                self.acudiente.recibir_correos = False
                avisos.avisar([(self.acudiente, "Otro aviso", "/")], "Asunto")
                self.assertEqual(len(mail.outbox), 1)
            finally:
                avisos.threading.Thread = original
        self.assertEqual(Aviso.objects.filter(usuario=self.acudiente).count(), 2)

    def test_pagina_de_avisos_marca_leidos(self):
        Aviso.objects.create(usuario=self.acudiente, texto="Hola")
        self.entrar(self.acudiente)
        self.assertContains(self.client.get("/avisos/"), "Hola")
        self.assertFalse(Aviso.objects.filter(usuario=self.acudiente, leido=False).exists())

    def test_comunicado_de_grupo_solo_avisa_a_ese_grupo(self):
        self.entrar(self.directivo)
        self.client.post(reverse("gestion_nuevo", args=["comunicados"]), {"titulo": "Solo sexto", "cuerpo": "x", "grupo": self.grupo.pk})
        avisados = set(Aviso.objects.values_list("usuario_id", flat=True))
        self.assertIn(self.acudiente.pk, avisados)
        solo_octavo = Usuario.objects.filter(rol="acudiente", acudidos__matriculas__grupo__grado__orden=8).exclude(
            acudidos__matriculas__grupo=self.grupo).first()
        self.assertNotIn(solo_octavo.pk, avisados)


@override_settings(MEDIA_ROOT="/tmp/pizarra_test_media")
class EntregaEnLineaTests(ConDatos):
    def setUp(self):
        self.tarea = Tarea.objects.create(clase=self.clase, titulo="Ensayo", fecha_entrega=timezone.localdate() + timedelta(days=3), entrega_en_linea=True)
        self.url = reverse("familia_tarea", args=[self.est.pk, self.tarea.pk])
        self.entrar(self.acudiente)

    def test_enviar_con_archivo_y_descargar_solo_quien_debe(self):
        archivo = SimpleUploadedFile("trabajo.pdf", b"%PDF-1.4 contenido", content_type="application/pdf")
        r = self.client.post(self.url, {"respuesta": "Aquí va", "archivo": archivo})
        self.assertEqual(r.status_code, 302)
        entrega = Entrega.objects.get(tarea=self.tarea, matricula=self.matricula)
        self.assertEqual(entrega.estado, "enviada")
        self.assertNotIn("trabajo", entrega.archivo.name)             # nombre impredecible
        descarga = reverse("archivo_entrega", args=[entrega.pk])
        self.assertEqual(self.client.get(descarga).status_code, 200)
        otro = Usuario.objects.filter(rol="acudiente").exclude(acudidos=self.est).first()
        self.entrar(otro)
        self.assertEqual(self.client.get(descarga).status_code, 403)
        self.entrar(self.otro_docente)
        self.assertEqual(self.client.get(descarga).status_code, 403)
        self.entrar(self.docente)
        self.assertEqual(self.client.get(descarga).status_code, 200)
        self.assertContains(self.client.get(reverse("tarea_entregas", args=[self.clase.pk, self.tarea.pk])), "Descargar archivo")

    def test_rechaza_tipos_peligrosos_y_envio_vacio(self):
        malo = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/octet-stream")
        self.assertEqual(self.client.post(self.url, {"archivo": malo}).status_code, 200)
        html = SimpleUploadedFile("pagina.html", b"<script>alert(1)</script>", content_type="text/html")
        self.assertEqual(self.client.post(self.url, {"archivo": html}).status_code, 200)
        self.assertEqual(self.client.post(self.url, {}).status_code, 200)
        self.assertFalse(Entrega.objects.filter(tarea=self.tarea).exists())

    def test_no_se_reenvia_despues_de_calificada(self):
        Entrega.objects.create(tarea=self.tarea, matricula=self.matricula, estado="entregada")
        self.client.post(self.url, {"respuesta": "otra vez"})
        self.assertEqual(Entrega.objects.get(tarea=self.tarea, matricula=self.matricula).respuesta, "")

    def test_tarea_sin_entrega_en_linea_no_recibe(self):
        self.tarea.entrega_en_linea = False
        self.tarea.save()
        self.client.post(self.url, {"respuesta": "hola"})
        self.assertFalse(Entrega.objects.filter(tarea=self.tarea).exists())

    def test_tarea_de_otro_grupo_no_existe_para_esta_familia(self):
        ajena = Tarea.objects.filter(clase__grupo__grado__orden=8).first()
        self.assertEqual(self.client.get(reverse("familia_tarea", args=[self.est.pk, ajena.pk])).status_code, 404)

    def test_docente_no_borra_lo_enviado_al_dejar_pendiente(self):
        self.client.post(self.url, {"respuesta": "Mi respuesta"})
        self.entrar(self.docente)
        self.client.post(reverse("tarea_entregas", args=[self.clase.pk, self.tarea.pk]), {f"e_{self.matricula.pk}": ""})
        self.assertEqual(Entrega.objects.get(tarea=self.tarea, matricula=self.matricula).respuesta, "Mi respuesta")


class PensionesTests(ConDatos):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.secretaria = Usuario.objects.get(username="1003")

    def setUp(self):
        self.entrar(self.secretaria)
        Cobro.objects.all().delete()
        self.anio = self.colegio.anio_activo

    def test_generar_cobros_no_duplica(self):
        datos = {"concepto": "Pensión de prueba", "vence": "2026-12-10"}
        self.client.post(reverse("generar_cobros"), datos)
        total = Cobro.objects.count()
        self.assertEqual(total, Matricula.objects.filter(grupo__anio=self.anio, activa=True).count())
        self.client.post(reverse("generar_cobros"), datos)
        self.assertEqual(Cobro.objects.count(), total)
        self.assertEqual(Cobro.objects.get(estudiante=self.est).valor, self.grupo.grado.pension)

    def test_pago_parcial_total_y_exceso(self):
        cobro = Cobro.objects.create(estudiante=self.est, anio=self.anio, concepto="Matrícula", valor=500000,
                                     vence=timezone.localdate() - timedelta(days=5))
        url = reverse("registrar_pago", args=[cobro.pk])
        r = self.client.post(url, {"valor": "600000", "fecha": "2026-11-16", "medio": "efectivo"})
        self.assertContains(r, "supera el saldo")
        r = self.client.post(url, {"valor": "200000", "fecha": "2026-11-16", "medio": "efectivo"})
        pago = Pago.objects.get()
        self.assertRedirects(r, reverse("recibo", args=[pago.pk]))
        self.assertEqual(pago.registrado_por, self.secretaria)
        from .views.pensiones import estado_de_cuenta

        _, total = estado_de_cuenta(self.est, self.anio)
        self.assertEqual((total["saldo"], total["vencido"]), (300000, 300000))
        self.client.post(url, {"valor": "300000", "fecha": "2026-11-16", "medio": "transferencia"})
        self.assertEqual(self.client.get(url, follow=True).redirect_chain[0][0], reverse("cuenta_estudiante", args=[self.est.pk]))
        # Cobro con pagos no se elimina.
        self.client.post(reverse("eliminar_cobro", args=[cobro.pk]))
        self.assertTrue(Cobro.objects.filter(pk=cobro.pk).exists())
        fila = next(f for f in __import__("core.views.pensiones", fromlist=["x"]).cartera_del_anio(self.anio) if f["matricula"].estudiante_id == self.est.pk)
        self.assertEqual((fila["saldo"], fila["vencido"]), (0, 0))

    def test_familia_ve_su_cuenta_y_no_la_ajena(self):
        cobro = Cobro.objects.create(estudiante=self.est, anio=self.anio, concepto="Pensión X", valor=100000, vence="2026-12-01")
        pago = Pago.objects.create(cobro=cobro, valor=100000)
        ajeno = Pago.objects.create(cobro=Cobro.objects.create(estudiante=self.est_ajeno, anio=self.anio, concepto="Y", valor=5, vence="2026-12-01"), valor=5)
        self.entrar(self.acudiente)
        self.assertContains(self.client.get(reverse("familia_pagos", args=[self.est.pk])), "Pensión X")
        self.assertEqual(self.client.get(reverse("familia_pagos", args=[self.est_ajeno.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("recibo", args=[pago.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("recibo", args=[ajeno.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("cuenta_estudiante", args=[self.est.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("registrar_pago", args=[cobro.pk]), {"valor": 1}).status_code, 403)
        self.entrar(self.docente)
        self.assertEqual(self.client.get(reverse("recibo", args=[pago.pk])).status_code, 403)

    def test_recordatorio_solo_a_morosos(self):
        Aviso.objects.all().delete()
        Cobro.objects.create(estudiante=self.est, anio=self.anio, concepto="Vencido", valor=100, vence=timezone.localdate() - timedelta(days=3))
        Cobro.objects.create(estudiante=self.est_ajeno, anio=self.anio, concepto="Vigente", valor=100, vence=timezone.localdate() + timedelta(days=3))
        self.client.post(reverse("recordar_mora"))
        self.assertEqual(set(Aviso.objects.values_list("usuario_id", flat=True)), set(self.est.acudientes.values_list("pk", flat=True)))


class PreescolarTests(ConDatos):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.maestra = Usuario.objects.get(username="2007")
        cls.clase_tr = Clase.objects.filter(docente=cls.maestra).first()
        cls.m_tr = cls.clase_tr.grupo.matriculas.first()

    def test_planilla_redirige_a_valoraciones_y_se_guarda(self):
        self.entrar(self.maestra)
        r = self.client.get(reverse("clase_notas", args=[self.clase_tr.pk]))
        self.assertRedirects(r, reverse("clase_valoraciones", args=[self.clase_tr.pk]))
        self.client.post(reverse("clase_valoraciones", args=[self.clase_tr.pk]), {"periodo": self.abierto.pk, f"v_{self.m_tr.pk}": "Avanza muy bien."})
        self.assertEqual(ValoracionDescriptiva.objects.get(matricula=self.m_tr, clase=self.clase_tr, periodo=self.abierto).texto, "Avanza muy bien.")
        r = self.client.post(reverse("clase_valoraciones", args=[self.clase_tr.pk]), {"periodo": self.cerrado.pk, f"v_{self.m_tr.pk}": "x"})
        self.assertEqual(r.status_code, 403)

    def test_familia_y_boletin_muestran_el_texto(self):
        acudiente = self.m_tr.estudiante.acudientes.first()
        self.entrar(acudiente)
        texto = ValoracionDescriptiva.objects.filter(matricula=self.m_tr, periodo=self.cerrado).first().texto
        r = self.client.get(reverse("familia_notas", args=[self.m_tr.estudiante_id]) + f"?periodo={self.cerrado.pk}")
        self.assertContains(r, texto)
        self.assertEqual(self.client.get(reverse("familia_resumen", args=[self.m_tr.estudiante_id])).status_code, 200)
        pdf = self.client.get(reverse("boletin_estudiante", args=[self.m_tr.pk, self.cerrado.pk]))
        self.assertTrue(pdf.content.startswith(b"%PDF"))


class IdentidadTests(ConDatos):
    def setUp(self):
        self.entrar(self.directivo)

    def _datos(self, **extra):
        c = self.colegio
        base = {"nombre": c.nombre, "nota_minima": "1.0", "desde_basico": "3.0", "desde_alto": "4.0", "desde_superior": "4.6",
                "nota_maxima": "5.0", "decimales": "1", "tope_recuperacion": "3.0", "perdidas_para_reprobar": "3", "color": "#14523f"}
        base.update(extra)
        return base

    def _png(self, lado=900):
        from PIL import Image

        salida = BytesIO()
        Image.new("RGB", (lado, lado), (200, 30, 30)).save(salida, format="PNG")
        return SimpleUploadedFile("escudo.png", salida.getvalue(), content_type="image/png")

    def test_subir_escudo_lo_reduce_y_se_sirve(self):
        version = Colegio.objects.get(pk=self.colegio.pk).escudo_version
        r = self.client.post("/colegio/configuracion/", self._datos(imagen_escudo=self._png(), color="#7a1f2b"))
        self.assertEqual(r.status_code, 302)
        c = Colegio.objects.get(pk=self.colegio.pk)
        self.assertEqual((c.escudo_version, c.color), (version + 1, "#7a1f2b"))
        from PIL import Image

        self.assertLessEqual(max(Image.open(BytesIO(bytes(c.escudo))).size), 400)
        self.client.logout()
        r = self.client.get(reverse("escudo", args=[c.pk]))      # público: sale en la pantalla de ingreso
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "image/png"))
        self.assertContains(self.client.get("/ingresar/"), "#7a1f2b")

    def test_rechaza_svg_y_archivos_que_no_son_imagen(self):
        antes = bytes(Colegio.objects.get(pk=self.colegio.pk).escudo)
        for nombre, contenido in [("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"),
                                  ("x.png", b"esto no es una imagen")]:
            r = self.client.post("/colegio/configuracion/", self._datos(imagen_escudo=SimpleUploadedFile(nombre, contenido)))
            self.assertEqual(r.status_code, 200, nombre)
        self.assertEqual(bytes(Colegio.objects.get(pk=self.colegio.pk).escudo), antes)

    def test_color_invalido_se_rechaza(self):
        r = self.client.post("/colegio/configuracion/", self._datos(color="red;background:url(x)"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Colegio.objects.get(pk=self.colegio.pk).color, "")

    def test_quitar_escudo_y_boletin_sigue_saliendo(self):
        self.client.post("/colegio/configuracion/", self._datos(quitar_escudo="on"))
        self.assertIsNone(Colegio.objects.get(pk=self.colegio.pk).escudo)
        self.assertEqual(self.client.get(reverse("escudo", args=[self.colegio.pk])).status_code, 404)
        pdf = self.client.get(reverse("boletin_estudiante", args=[self.matricula.pk, self.cerrado.pk]))
        self.assertTrue(pdf.content.startswith(b"%PDF"))


class CuentaTests(ConDatos):
    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_recuperar_clave_por_correo(self):
        self.acudiente.email = "mama@example.com"
        self.acudiente.debe_cambiar_clave = True
        self.acudiente.save()
        self.assertEqual(self.client.get("/recuperar/").status_code, 200)
        self.client.post("/recuperar/", {"email": "mama@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        enlace = next(l for l in mail.outbox[0].body.splitlines() if "/recuperar/" in l).split("testserver")[1]
        r = self.client.get(enlace, follow=True)
        self.client.post(r.redirect_chain[-1][0], {"new_password1": "Arrayanes#2027", "new_password2": "Arrayanes#2027"})
        u = Usuario.objects.get(pk=self.acudiente.pk)
        self.assertTrue(u.check_password("Arrayanes#2027"))
        self.assertFalse(u.debe_cambiar_clave)

    def test_correo_desconocido_no_revela_nada(self):
        r = self.client.post("/recuperar/", {"email": "nadie@example.com"})
        self.assertRedirects(r, "/recuperar/enviado/")
        self.assertEqual(len(mail.outbox), 0)

    def test_mi_cuenta(self):
        self.entrar(self.acudiente)
        self.client.post("/cuenta/", {"email": "nuevo@example.com", "telefono": "300"})
        u = Usuario.objects.get(pk=self.acudiente.pk)
        self.assertEqual((u.email, u.recibir_correos), ("nuevo@example.com", False))


class DemoTests(TestCase):
    def test_actualizar_es_idempotente_y_reiniciar_funciona(self):
        call_command("datos_demo", stdout=StringIO())
        call_command("datos_demo", "--actualizar", stdout=StringIO())
        self.assertEqual(Colegio.objects.count(), 1)
        primero = Colegio.objects.values_list("pk", flat=True).get()
        Colegio.objects.update(version_demo=0)
        call_command("datos_demo", "--actualizar", stdout=StringIO())
        self.assertEqual(Colegio.objects.count(), 1)
        self.assertNotEqual(Colegio.objects.values_list("pk", flat=True).get(), primero)
        call_command("datos_demo", "--reiniciar", stdout=StringIO())
        self.assertEqual((Colegio.objects.count(), Estudiante.objects.count()), (1, 58))
        self.assertEqual(Usuario.objects.get(username="1004").permisos, ["comunicados", "pensiones"])


class NuevasPaginasTests(ConDatos):
    def test_todas_abren(self):
        self.entrar(self.directivo)
        plan = PlanEstudio.objects.filter(grado=self.grupo.grado).first()
        for url in ["/colegio/plan/", f"/colegio/plan/?grado={self.grupo.grado_id}", reverse("logros", args=[plan.pk]),
                    reverse("malla", args=[self.grupo.grado_id]), "/colegio/horarios/", reverse("horario_grupo", args=[self.grupo.pk]),
                    "/colegio/bloques/", "/colegio/bloques/nuevo/", "/colegio/cierre/", "/colegio/pensiones/",
                    "/colegio/pensiones/?estado=mora", "/colegio/directivos/", "/colegio/directivos/nuevo/",
                    "/colegio/grados/nuevo/", "/avisos/", "/cuenta/", "/colegio/",
                    reverse("clase_recuperaciones", args=[self.clase.pk]), reverse("clase_estudiantes", args=[self.clase.pk])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.entrar(self.docente)
        for url in ["/horario/", "/avisos/", "/cuenta/", reverse("clase_recuperaciones", args=[self.clase.pk]),
                    reverse("clase_notas", args=[self.clase.pk])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.entrar(self.acudiente)
        tarea = Tarea.objects.filter(clase__grupo=self.grupo).first()
        for url in [reverse("familia_horario", args=[self.est.pk]), reverse("familia_pagos", args=[self.est.pk]),
                    reverse("familia_tarea", args=[self.est.pk, tarea.pk]), reverse("familia_notas", args=[self.est.pk]) + "?periodo=%d" % Periodo.objects.get(numero=3).pk,
                    "/avisos/", "/cuenta/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)


# ====================================================================
# Tercera entrega: permisos por persona, sistema de evaluación, módulos,
# puesta en marcha, cuenta del estudiante, exportaciones y bitácora.
# ====================================================================
from . import escala as escala_mod  # noqa: E402
from .forms import convertir_notas_del_colegio  # noqa: E402
from .views.directivo import crear_estructura_tipica  # noqa: E402


class ConversionDeEscalaTests(TestCase):
    V = ["1.0", "3.0", "4.0", "4.6", "5.0"]
    N10 = ["0.0", "6.0", "8.0", "9.5", "10.0"]
    N100 = ["0", "60", "80", "95", "100"]

    def test_los_limites_se_conservan(self):
        for viejo, nuevo in zip(self.V, self.N10):
            self.assertEqual(escala_mod.convertir(viejo, self.V, self.N10), Decimal(nuevo))
        self.assertEqual(escala_mod.convertir("3.0", self.V, self.N100, 0), Decimal("60"))

    def test_nadie_cambia_de_nivel(self):
        c = Colegio(nombre="x")
        d = Colegio(nombre="y", nota_minima=0, desde_basico=6, desde_alto=8, desde_superior=Decimal("9.5"), nota_maxima=10)
        e = Colegio(nombre="z", nota_minima=0, desde_basico=60, desde_alto=80, desde_superior=95, nota_maxima=100)
        for decima in range(10, 51):
            nota = Decimal(decima) / 10
            self.assertEqual(c.desempeno(nota), d.desempeno(escala_mod.convertir(nota, self.V, self.N10)), nota)
            self.assertEqual(c.desempeno(nota), e.desempeno(escala_mod.convertir(nota, self.V, self.N100, 0)), nota)

    def test_ida_y_vuelta_entre_escalas_equivalentes(self):
        cero_a_cinco = ["0.0", "3.0", "4.0", "4.6", "5.0"]
        self.assertEqual(escala_mod.convertir("2.9", self.V, cero_a_cinco), Decimal("2.9"))   # 2.85 sube a 2.9, sigue Bajo
        self.assertEqual(escala_mod.convertir("4.5", self.V, cero_a_cinco), Decimal("4.5"))

    def test_predefinidas_son_escalas_validas(self):
        for clave, (_, minima, basico, alto, superior, maxima, dec) in escala_mod.PREDEFINIDAS.items():
            c = Colegio(nombre="x", nota_minima=Decimal(minima), desde_basico=Decimal(basico), desde_alto=Decimal(alto),
                        desde_superior=Decimal(superior), nota_maxima=Decimal(maxima), decimales=dec, tope_recuperacion=Decimal(basico))
            c.clean()
            self.assertEqual(escala_mod.clave_de(c), clave)
        self.assertEqual(escala_mod.clave_de(Colegio(nombre="x")), "1-5")   # el colegio nuevo nace con 1 a 5


class SistemaDeEvaluacionTests(ConDatos):
    def setUp(self):
        self.entrar(self.directivo)

    def _datos(self, **extra):
        base = {"nombre": self.colegio.nombre, "nota_minima": "1.0", "desde_basico": "3.0", "desde_alto": "4.0",
                "desde_superior": "4.6", "nota_maxima": "5.0", "decimales": "1", "tope_recuperacion": "3.0",
                "perdidas_para_reprobar": "3", "color": "#14523f"}
        base.update(extra)
        return base

    A_CIEN = {"nota_minima": "0", "desde_basico": "60", "desde_alto": "80", "desde_superior": "95", "nota_maxima": "100", "decimales": "0"}

    def test_cambiar_con_notas_exige_confirmar(self):
        r = self.client.post("/colegio/configuracion/", self._datos(**self.A_CIEN))
        self.assertContains(r, "Ya hay notas registradas")
        self.assertEqual(Colegio.objects.get(pk=self.colegio.pk).nota_maxima, Decimal("5.0"))

    def test_cambio_a_cien_convierte_y_conserva_desempenos(self):
        periodo = Periodo.objects.get(numero=1)
        filas_antes, proms_antes, _ = calculos.resumen_estudiante(self.matricula, [periodo])
        niveles_antes = [self.colegio.desempeno(f["periodos"][periodo.id]) for f in filas_antes]
        notas_antes = {n.pk: n.valor for n in Nota.objects.filter(matricula=self.matricula)}
        r = self.client.post("/colegio/configuracion/", self._datos(convertir_notas="on", **self.A_CIEN))
        self.assertEqual(r.status_code, 302)
        colegio = Colegio.objects.get(pk=self.colegio.pk)
        self.assertEqual((colegio.nota_maxima, colegio.decimales, colegio.tope_recuperacion), (Decimal("100"), 0, Decimal("60")))
        viejo = Colegio(nombre="v")
        for n in Nota.objects.filter(matricula=self.matricula):
            self.assertTrue(0 <= n.valor <= 100)
            self.assertEqual(n.valor, n.valor.to_integral_value())                       # sin decimales
            self.assertEqual(viejo.desempeno(notas_antes[n.pk]), colegio.desempeno(n.valor))
        self.assertTrue(Bitacora.objects.filter(accion="Cambió el sistema de evaluación").exists())
        # Las pantallas siguen funcionando con la escala nueva y muestran enteros.
        self.entrar(self.docente)
        r = self.client.get(reverse("clase_notas", args=[self.clase.pk]))
        self.assertContains(r, 'data-max="100.0"')
        act = Actividad.objects.filter(clase=self.clase, periodo=self.abierto).first()
        campo = f"n_{self.matricula.pk}_{act.pk}"
        self.client.post(reverse("clase_notas", args=[self.clase.pk]), {"periodo": self.abierto.pk, campo: "87"})
        self.assertEqual(Nota.objects.get(actividad=act, matricula=self.matricula).valor, Decimal("87"))
        r = self.client.post(reverse("clase_notas", args=[self.clase.pk]), {"periodo": self.abierto.pk, campo: "101"})
        self.assertContains(r, "No se guardó nada")
        self.entrar(self.acudiente)
        self.assertEqual(self.client.get(reverse("familia_resumen", args=[self.est.pk])).status_code, 200)
        pdf = self.client.get(reverse("boletin_estudiante", args=[self.matricula.pk, self.cerrado.pk]))
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        escala_mod.usar_decimales(1)

    def test_escala_desordenada_se_rechaza(self):
        r = self.client.post("/colegio/configuracion/", self._datos(desde_alto="2.0", convertir_notas="on"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Colegio.objects.get(pk=self.colegio.pk).desde_alto, Decimal("4.0"))

    def test_sin_notas_cambia_sin_confirmar(self):
        Nota.objects.all().delete()
        r = self.client.post("/colegio/configuracion/", self._datos(nota_minima="0.0"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(escala_mod.clave_de(Colegio.objects.get(pk=self.colegio.pk)), "0-5")


class PermisosPorPersonaTests(ConDatos):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.administrativo = Usuario.objects.get(username="1004")   # solo comunicados y pensiones
        cls.sistemas = Usuario.objects.get(username="1005")

    def test_administrativo_solo_ve_lo_que_tiene_marcado(self):
        self.entrar(self.administrativo)
        for url in ["/colegio/", "/colegio/pensiones/", "/colegio/comunicados/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        for url in ["/colegio/estudiantes/", "/colegio/docentes/", "/colegio/grupos/", "/colegio/consolidado/",
                    "/colegio/boletines/", "/colegio/configuracion/", "/colegio/cierre/", "/colegio/directivos/"]:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        menu = self.client.get("/colegio/").content.decode()
        self.assertIn("Pensiones", menu)
        self.assertNotIn("Consolidado de notas", menu)

    def test_dar_y_quitar_un_permiso_tiene_efecto_inmediato(self):
        self.entrar(self.directivo)
        datos = {"first_name": "Wilson", "last_name": "Carreño Díaz", "documento": "1004", "cargo": "administrativo", "is_active": "on"}
        self.client.post(reverse("gestion_editar", args=["directivos", self.administrativo.pk]), {**datos, "permisos": ["estudiantes"]})
        self.entrar(Usuario.objects.get(username="1004"))
        self.assertEqual(self.client.get("/colegio/estudiantes/").status_code, 200)
        self.assertEqual(self.client.get("/colegio/pensiones/").status_code, 403)

    def test_sistemas_administra_permisos_pero_no_ve_notas_ni_cartera(self):
        self.entrar(self.sistemas)
        self.assertEqual(self.client.get("/colegio/directivos/").status_code, 200)
        self.assertEqual(self.client.get("/colegio/bitacora/").status_code, 200)
        self.assertEqual(self.client.get("/colegio/consolidado/").status_code, 403)
        self.assertEqual(self.client.get("/colegio/pensiones/").status_code, 403)

    def test_quien_no_configura_no_se_da_permisos(self):
        self.entrar(self.administrativo)
        r = self.client.post(reverse("gestion_editar", args=["directivos", self.administrativo.pk]),
                             {"first_name": "W", "last_name": "C", "documento": "1004", "cargo": "rector", "is_active": "on",
                              "permisos": [p[0] for p in Usuario.PERMISOS]})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Usuario.objects.get(username="1004").permisos, ["comunicados", "pensiones"])

    def test_no_se_puede_dejar_al_colegio_sin_quien_configure(self):
        Usuario.objects.filter(username="1005").update(permisos=[])
        self.entrar(self.directivo)
        r = self.client.post(reverse("gestion_editar", args=["directivos", self.directivo.pk]),
                             {"first_name": "B", "last_name": "M", "documento": "1001", "cargo": "rector", "is_active": "on", "permisos": ["notas"]})
        self.assertContains(r, "única cuenta activa")
        self.assertIn("configuracion", Usuario.objects.get(username="1001").permisos)

    def test_permiso_inventado_se_rechaza(self):
        self.entrar(self.directivo)
        r = self.client.post(reverse("gestion_nuevo", args=["directivos"]),
                             {"first_name": "A", "last_name": "B", "documento": "777001", "cargo": "administrativo", "is_active": "on", "permisos": ["superpoder"]})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Usuario.objects.filter(username="777001").exists())

    def test_docente_y_acudiente_nunca_tienen_permisos_directivos(self):
        Usuario.objects.filter(pk=self.docente.pk).update(permisos=[p[0] for p in Usuario.PERMISOS])
        self.entrar(Usuario.objects.get(pk=self.docente.pk))
        self.assertEqual(self.client.get("/colegio/pensiones/").status_code, 403)
        self.assertEqual(self.client.get(reverse("clase_notas", args=[self.clase_ajena.pk])).status_code, 403)


class ModulosTests(ConDatos):
    def apagar(self, *modulos):
        c = Colegio.objects.get(pk=self.colegio.pk)
        c.modulos = [m for m in c.modulos if m not in modulos]
        c.save(update_fields=["modulos"])

    def test_modulo_apagado_desaparece_para_todos(self):
        self.apagar("pensiones", "horarios", "plan", "observador", "recuperaciones", "comunicados")
        self.entrar(self.directivo)
        for url in ["/colegio/pensiones/", "/colegio/horarios/", "/colegio/plan/", "/colegio/bloques/", "/comunicados/",
                    reverse("cuenta_estudiante", args=[self.est.pk]), reverse("observador", args=[self.matricula.pk]),
                    reverse("clase_recuperaciones", args=[self.clase.pk])]:
            self.assertEqual(self.client.get(url).status_code, 404, url)
        html = self.client.get("/colegio/").content.decode()
        for texto in ["Pensiones", "Horarios", "Plan de estudios", "Comunicados"]:
            self.assertNotIn(f">{texto}<", html.replace("\n", ""), texto)
        # Lo básico sigue completo.
        for url in ["/colegio/estudiantes/", "/colegio/consolidado/", "/colegio/boletines/", "/colegio/configuracion/",
                    "/colegio/grupos/", reverse("ficha_estudiante", args=[self.est.pk]), reverse("clase_notas", args=[self.clase.pk]),
                    reverse("clase_estudiantes", args=[self.clase.pk]), reverse("direccion_grupo", args=[self.grupo.pk])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.entrar(self.docente)
        self.assertEqual(self.client.get("/horario/").status_code, 404)
        self.assertEqual(self.client.get("/clases/").status_code, 200)
        self.entrar(self.acudiente)
        for nombre in ["familia_pagos", "familia_horario", "familia_observaciones"]:
            self.assertEqual(self.client.get(reverse(nombre, args=[self.est.pk])).status_code, 404, nombre)
        r = self.client.get(reverse("familia_resumen", args=[self.est.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Pagos")
        pdf = self.client.get(reverse("boletin_estudiante", args=[self.matricula.pk, self.cerrado.pk]))
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_sin_modulo_de_avisos_no_se_crean(self):
        self.apagar("avisos")
        Aviso.objects.all().delete()
        self.entrar(self.docente)
        act = Actividad.objects.filter(clase=self.clase, periodo=self.abierto).first()
        self.client.post(reverse("clase_notas", args=[self.clase.pk]), {"periodo": self.abierto.pk, f"n_{self.matricula.pk}_{act.pk}": "1,0"})
        self.assertFalse(Aviso.objects.exists())
        self.assertEqual(self.client.get("/avisos/").status_code, 404)

    def test_sin_entregas_en_linea_la_familia_no_puede_enviar(self):
        self.apagar("entregas")
        tarea = Tarea.objects.create(clase=self.clase, titulo="T", fecha_entrega=timezone.localdate() + timedelta(days=2), entrega_en_linea=True)
        self.entrar(self.acudiente)
        self.client.post(reverse("familia_tarea", args=[self.est.pk, tarea.pk]), {"respuesta": "hola"})
        self.assertFalse(Entrega.objects.filter(tarea=tarea).exists())
        self.entrar(self.docente)
        self.assertNotContains(self.client.get(reverse("tarea_nueva", args=[self.clase.pk])), "entrega_en_linea")

    def test_colegio_nuevo_nace_con_todos_los_modulos(self):
        self.assertEqual(len(Colegio.objects.create(nombre="Nuevo").modulos), 8)


class PuestaEnMarchaTests(TestCase):
    """Un colegio que empieza de cero: de crear_colegio a tener un boletín."""

    def test_flujo_completo_desde_cero(self):
        salida = StringIO()
        call_command("crear_colegio", "Colegio Nuevo", "--documento", "500100", "--nombres", "Ana", "--apellidos", "Ruiz", stdout=salida)
        clave = next(l for l in salida.getvalue().splitlines() if "temporal" in l).split(": ")[1]
        rectora = Usuario.objects.get(username="500100")
        self.assertEqual((rectora.cargo, len(rectora.permisos)), ("rector", 9))
        c = self.client
        self.assertTrue(c.login(username="500100", password=clave))
        self.assertRedirects(c.get("/colegio/"), "/cuenta/clave/")                     # clave temporal
        c.post("/cuenta/clave/", {"old_password": clave, "new_password1": "Colegio#Nuevo25", "new_password2": "Colegio#Nuevo25"})
        r = c.get("/colegio/")
        self.assertContains(r, "Puesta en marcha")
        # Todas las pantallas abren con el colegio vacío.
        for url in ["/colegio/estudiantes/", "/colegio/docentes/", "/colegio/grupos/", "/colegio/plan/", "/colegio/horarios/",
                    "/colegio/consolidado/", "/colegio/boletines/", "/colegio/pensiones/", "/colegio/configuracion/",
                    "/colegio/cierre/", "/colegio/importar/", "/comunicados/", "/avisos/", "/colegio/bitacora/",
                    "/colegio/exportar/estudiantes/", "/colegio/exportar/cartera/"]:
            self.assertEqual(c.get(url).status_code, 200, url)

        c.post("/colegio/estructura/")
        colegio = rectora.colegio
        anio = colegio.anio_activo
        self.assertEqual((anio.periodos.count(), colegio.grados.count()), (4, 14))
        self.assertEqual(sum(p.peso for p in anio.periodos.all()), 100)
        areas = set(Asignatura.objects.filter(colegio=colegio).exclude(area="Preescolar").values_list("area", flat=True))
        self.assertEqual(len(areas), 9 + 2)                                             # nueve áreas obligatorias y dos de media
        sexto = colegio.grados.get(nombre="Sexto")
        once = colegio.grados.get(nombre="Once")
        self.assertEqual((sexto.plan.count(), once.plan.count()), (10, 12))
        self.assertTrue(colegio.grados.get(nombre="Transición").descriptivo)
        self.assertEqual(crear_estructura_tipica(colegio, timezone.localdate()), [])    # no duplica

        # Docente, grupo con clases del plan, estudiante con acudiente.
        c.post("/colegio/docentes/nuevo/", {"first_name": "Luis", "last_name": "Mora", "documento": "600100", "is_active": "on"})
        docente = Usuario.objects.get(username="600100")
        c.post("/colegio/grupos/nuevo/", {"anio": anio.pk, "grado": sexto.pk, "nombre": "A", "director": docente.pk})
        grupo = Grupo.objects.get(anio=anio)
        c.post(reverse("plan_aplicar", args=[sexto.pk]))
        self.assertEqual(grupo.clases.count(), 10)
        clase = grupo.clases.get(asignatura__nombre="Matemáticas")
        c.post(reverse("gestion_editar", args=["clases", clase.pk]),
               {"grupo": grupo.pk, "asignatura": clase.asignatura_id, "docente": docente.pk, "horas_semanales": 5})
        c.post("/colegio/estudiantes/nuevo/", {"nombres": "Sofía", "apellidos": "Paz Ríos", "tipo_documento": "TI",
                                               "documento": "700100", "activo": "on", "grupo": grupo.pk})
        est = Estudiante.objects.get(documento="700100")
        c.post(reverse("ficha_estudiante", args=[est.pk]), {"documento": "800100", "parentesco": "madre", "first_name": "Rosa", "last_name": "Ríos"})
        r = c.post(reverse("crear_cuenta_estudiante", args=[est.pk]), follow=True)
        self.assertContains(r, "Cuenta del estudiante creada")
        est.refresh_from_db()
        self.assertEqual((est.usuario.rol, est.usuario.username), ("estudiante", "700100"))
        c.post(reverse("horarios_generar"))
        self.assertEqual(clase.horario.count(), 5)
        self.assertNotContains(c.get("/colegio/"), "Puesta en marcha")
        self.assertTrue(c.get("/colegio/exportar/estudiantes/").content.startswith(b"PK"))
        self.assertTrue(c.get(f"/colegio/exportar/consolidado/?grupo={grupo.pk}").content.startswith(b"PK"))

        # El docente califica; se cierra el periodo; la familia y el estudiante ven el boletín.
        periodo = anio.periodos.get(numero=1)
        Usuario.objects.filter(pk=docente.pk).update(debe_cambiar_clave=False)
        c.force_login(docente)
        self.assertEqual(c.get("/clases/").status_code, 200)
        c.post(reverse("actividad_nueva", args=[clase.pk]) + f"?periodo={periodo.pk}",
               {"periodo": periodo.pk, "titulo": "Taller 1", "tipo": "taller", "fecha": periodo.fecha_inicio.isoformat(), "peso": "0"})
        act = Actividad.objects.get(clase=clase)
        m = est.matricula_actual()
        c.post(reverse("clase_notas", args=[clase.pk]), {"periodo": periodo.pk, f"n_{m.pk}_{act.pk}": "4,2"})
        self.assertEqual(Nota.objects.get().valor, Decimal("4.2"))
        rectora.refresh_from_db()      # cambió su contraseña: la sesión debe usar la nueva
        c.force_login(rectora)
        c.post(reverse("alternar_periodo", args=[periodo.pk]))
        periodo.refresh_from_db()
        self.assertTrue(periodo.cerrado)
        for usuario in (Usuario.objects.get(username="800100"), est.usuario):
            Usuario.objects.filter(pk=usuario.pk).update(debe_cambiar_clave=False)
            c.force_login(usuario)
            self.assertRedirects(c.get("/"), reverse("familia_resumen", args=[est.pk]))
            self.assertContains(c.get(reverse("familia_notas", args=[est.pk]) + f"?periodo={periodo.pk}"), "4,2")
            pdf = c.get(reverse("boletin_estudiante", args=[m.pk, periodo.pk]))
            self.assertTrue(pdf.content.startswith(b"%PDF"), (usuario.rol, pdf.status_code, pdf.content[:300]))
        # El estudiante no ve el estado de cuenta; el acudiente sí.
        self.assertEqual(c.get(reverse("familia_pagos", args=[est.pk])).status_code, 403)



# ====================================================================
# Carga de notas sencilla para docentes
# ====================================================================
from openpyxl import load_workbook  # noqa: E402


class CargaSencillaTests(ConDatos):
    def setUp(self):
        self.entrar(self.docente)
        self.url = reverse("clase_notas", args=[self.clase.pk])
        self.act = Actividad.objects.filter(clase=self.clase, periodo=self.abierto).first()
        self.campo = f"n_{self.matricula.pk}_{self.act.pk}"

    def test_agregar_columna_guarda_lo_escrito_y_crea_la_actividad(self):
        r = self.client.post(self.url, {"periodo": self.abierto.pk, self.campo: "3,9", "nueva_actividad": "Quiz sorpresa", "agregar": "1"})
        nueva = Actividad.objects.get(clase=self.clase, periodo=self.abierto, titulo="Quiz sorpresa")
        self.assertRedirects(r, f"{self.url}?periodo={self.abierto.pk}&foco={nueva.pk}")
        self.assertEqual(Nota.objects.get(actividad=self.act, matricula=self.matricula).valor, Decimal("3.9"))
        self.assertEqual((nueva.peso, nueva.tipo), (0, "taller"))
        self.assertTrue(self.abierto.fecha_inicio <= nueva.fecha <= self.abierto.fecha_fin)
        self.assertContains(self.client.get(f"{self.url}?periodo={self.abierto.pk}&foco={nueva.pk}"), f'data-foco="{nueva.pk}"')

    def test_agregar_columna_con_una_nota_mala_no_crea_nada(self):
        antes = Actividad.objects.count()
        r = self.client.post(self.url, {"periodo": self.abierto.pk, self.campo: "8", "nueva_actividad": "Otra", "agregar": "1"})
        self.assertContains(r, "No se guardó nada")
        self.assertEqual(Actividad.objects.count(), antes)

    def test_agregar_sin_nombre_avisa(self):
        r = self.client.post(self.url, {"periodo": self.abierto.pk, "nueva_actividad": "  ", "agregar": "1"}, follow=True)
        self.assertContains(r, "Escriba el nombre de la actividad")

    def test_planilla_vacia_permite_empezar_con_un_nombre(self):
        Actividad.objects.filter(clase=self.clase, periodo=self.abierto).delete()
        self.assertContains(self.client.get(self.url), "Empiece por la primera actividad")
        self.client.post(self.url, {"periodo": self.abierto.pk, "nueva_actividad": "Taller 1", "agregar": "1"})
        self.assertTrue(Actividad.objects.filter(clase=self.clase, titulo="Taller 1").exists())

    def test_calificar_una_actividad(self):
        url = reverse("calificar", args=[self.clase.pk, self.act.pk])
        r = self.client.get(url)
        self.assertContains(r, str(self.est))
        otra = Actividad.objects.filter(clase=self.clase, periodo=self.abierto).exclude(pk=self.act.pk).first()
        campo_otra = f"n_{self.matricula.pk}_{otra.pk}"
        antes_otra = Nota.objects.filter(actividad=otra, matricula=self.matricula).values_list("valor", flat=True).first()
        r = self.client.post(url, {self.campo: "4.4", campo_otra: "1.0"})
        self.assertEqual(Nota.objects.get(actividad=self.act, matricula=self.matricula).valor, Decimal("4.4"))
        self.assertEqual(Nota.objects.filter(actividad=otra, matricula=self.matricula).values_list("valor", flat=True).first(), antes_otra)
        r = self.client.post(url, {self.campo: "4.6", "y_siguiente": "1"})
        self.assertRedirects(r, reverse("calificar", args=[self.clase.pk, otra.pk]))
        r = self.client.post(url, {self.campo: "abc"})
        self.assertContains(r, "no es un número")
        self.assertEqual(Nota.objects.get(actividad=self.act, matricula=self.matricula).valor, Decimal("4.6"))

    def test_calificar_no_sirve_para_actividad_de_otra_clase(self):
        ajena = Actividad.objects.filter(clase=self.clase_ajena).first()
        self.assertEqual(self.client.get(reverse("calificar", args=[self.clase.pk, ajena.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("calificar", args=[self.clase_ajena.pk, ajena.pk])).status_code, 403)

    def _descargar(self):
        r = self.client.get(reverse("planilla_excel", args=[self.clase.pk]) + f"?periodo={self.abierto.pk}")
        self.assertEqual(r.status_code, 200)
        return load_workbook(BytesIO(r.content))

    def _subir(self, libro):
        salida = BytesIO()
        libro.save(salida)
        archivo = SimpleUploadedFile("planilla.xlsx", salida.getvalue())
        return self.client.post(reverse("subir_planilla", args=[self.clase.pk]) + f"?periodo={self.abierto.pk}", {"archivo": archivo}, follow=True)

    def test_excel_ida_y_vuelta_con_columna_nueva(self):
        libro = self._descargar()
        hoja = libro["Notas"]
        self.assertTrue(hoja.row_dimensions[1].hidden)
        self.assertEqual(hoja.cell(row=2, column=1).value, "Documento")
        fila = next(f for f in range(3, hoja.max_row + 1) if hoja.cell(row=f, column=1).value == self.est.documento)
        hoja.cell(row=fila, column=3).value = 2.7
        nueva_col = hoja.max_column + 1
        hoja.cell(row=2, column=nueva_col).value = "Exposición final"
        hoja.cell(row=fila, column=nueva_col).value = "4,8"
        r = self._subir(libro)
        self.assertContains(r, "Planilla cargada desde Excel")
        primera = Actividad.objects.get(pk=int(hoja.cell(row=1, column=3).value[1:]))
        self.assertEqual(Nota.objects.get(actividad=primera, matricula=self.matricula).valor, Decimal("2.7"))
        nueva = Actividad.objects.get(clase=self.clase, titulo="Exposición final")
        self.assertEqual(Nota.objects.get(actividad=nueva, matricula=self.matricula).valor, Decimal("4.8"))
        self.assertTrue(Aviso.objects.filter(texto__contains="2,7").exists())         # nota baja: avisa a la familia

    def test_excel_con_errores_no_guarda_nada(self):
        libro = self._descargar()
        hoja = libro["Notas"]
        antes = list(Nota.objects.order_by("pk").values_list("valor", flat=True))
        hoja.cell(row=3, column=3).value = 9
        hoja.cell(row=4, column=3).value = 4.0
        r = self._subir(libro)
        self.assertContains(r, "No se guardó nada")
        self.assertContains(r, "Fila 3")
        self.assertEqual(list(Nota.objects.order_by("pk").values_list("valor", flat=True)), antes)

        libro = self._descargar()
        libro["Notas"].cell(row=3, column=1).value = "999999"
        self.assertContains(self._subir(libro), "no corresponde a un estudiante")

        libro = self._descargar()
        ajena = Actividad.objects.filter(clase=self.clase_ajena).first()
        libro["Notas"].cell(row=1, column=3).value = f"#{ajena.pk}"
        r = self._subir(libro)
        self.assertContains(r, "es de otra planilla")
        self.assertFalse(Nota.objects.filter(actividad=ajena, valor=Decimal("9")).exists())

    def test_excel_basura_y_permisos(self):
        archivo = SimpleUploadedFile("x.xlsx", b"no soy un excel")
        r = self.client.post(reverse("subir_planilla", args=[self.clase.pk]), {"archivo": archivo}, follow=True)
        self.assertContains(r, "No se pudo leer el archivo")
        self.assertEqual(self.client.get(reverse("planilla_excel", args=[self.clase_ajena.pk])).status_code, 403)
        self.entrar(self.acudiente)
        self.assertEqual(self.client.get(reverse("planilla_excel", args=[self.clase.pk])).status_code, 403)

    def test_excel_en_periodo_cerrado(self):
        self.entrar(self.docente)
        libro_r = self.client.get(reverse("planilla_excel", args=[self.clase.pk]) + f"?periodo={self.cerrado.pk}")
        r = self.client.post(reverse("subir_planilla", args=[self.clase.pk]) + f"?periodo={self.cerrado.pk}",
                             {"archivo": SimpleUploadedFile("p.xlsx", libro_r.content)})
        self.assertEqual(r.status_code, 403)
