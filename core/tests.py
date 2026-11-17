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
        intruso = Usuario.objects.create_user("9001", password="x", rol="directivo", colegio=otro)
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
        self.assertContains(r, "No se guardó la planilla")
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
