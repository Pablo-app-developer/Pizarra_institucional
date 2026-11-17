"""Carga un colegio de ejemplo completo para mostrar la plataforma.

    python manage.py datos_demo

Todos los nombres son inventados. No usar sobre una base con datos reales:
el comando se niega a correr si ya existe el colegio de ejemplo, salvo con --reiniciar.
"""
import random
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core import horarios
from core.models import (
    Actividad, Acudiencia, AnioLectivo, Asignatura, Asistencia, Bloque, Clase, Cobro, Colegio, ComentarioBoletin,
    Comunicado, Entrega, Estudiante, Grado, Grupo, Logro, Matricula, Nota, Observacion, Pago, Periodo,
    PlanEstudio, Recuperacion, Tarea, Usuario, ValoracionDescriptiva,
)

NOMBRE = "Colegio Los Arrayanes"
CLAVE = "pizarra2026"
VERSION = 3  # súbalo cuando cambie el contenido del ejemplo: --actualizar lo recrea

LOGROS = {
    "Matemáticas": ["Resuelve problemas con fracciones y decimales en contextos cotidianos.", "Plantea y soluciona ecuaciones de primer grado.", "Interpreta tablas y gráficas estadísticas.", "Calcula áreas y perímetros de figuras planas."],
    "Lengua castellana": ["Comprende textos narrativos e identifica su estructura.", "Escribe textos argumentativos con tesis y razones.", "Expone oralmente con claridad sobre una lectura.", "Aplica las reglas ortográficas en sus escritos."],
    "Inglés": ["Se presenta y describe su entorno en inglés.", "Sostiene diálogos cortos sobre rutinas.", "Comprende textos breves en presente simple.", "Narra hechos pasados con verbos regulares."],
    "Ciencias naturales": ["Diferencia mezclas y sustancias puras.", "Explica la estructura y funciones de la célula.", "Describe las relaciones dentro de un ecosistema.", "Compara fuentes de energía renovables y no renovables."],
    "Ciencias sociales": ["Ubica las regiones naturales de Colombia.", "Ordena hechos históricos en una línea de tiempo.", "Explica las causas de la Independencia.", "Reconoce mecanismos de participación ciudadana."],
    "Educación física": ["Mejora su resistencia aeróbica.", "Ejecuta los fundamentos del voleibol.", "Coordina movimientos en secuencias rítmicas.", "Valora la actividad física como hábito saludable."],
}
DIMENSIONES = ["Dimensión cognitiva", "Dimensión comunicativa", "Dimensión corporal", "Dimensión socioafectiva"]
VALORACIONES = {
    "Dimensión cognitiva": ["Reconoce los números hasta el 20 y clasifica objetos por forma y color. Disfruta los juegos de lógica.", "Cuenta con seguridad hasta el 10. Estamos reforzando la relación entre número y cantidad."],
    "Dimensión comunicativa": ["Narra con sus palabras los cuentos leídos en clase y escribe su nombre sin ayuda.", "Se expresa con claridad y participa en las conversaciones. Avanza en el reconocimiento de las vocales."],
    "Dimensión corporal": ["Tiene buen equilibrio y coordinación. Recorta y colorea respetando los límites.", "Disfruta el juego al aire libre. Seguimos fortaleciendo el agarre del lápiz."],
    "Dimensión socioafectiva": ["Comparte con sus compañeros y sigue las normas del salón. Expresa lo que siente.", "Es cariñoso y colaborador. Estamos trabajando en esperar el turno."],
}
JORNADA = [("1ª hora", "07:00", "07:55", False), ("2ª hora", "07:55", "08:50", False), ("3ª hora", "08:50", "09:45", False),
           ("Descanso", "09:45", "10:15", True), ("4ª hora", "10:15", "11:10", False), ("5ª hora", "11:10", "12:05", False),
           ("6ª hora", "12:05", "13:00", False)]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def escudo_de_ejemplo():
    """Un escudo genérico dibujado aquí mismo, para mostrar la personalización."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    lado = 400
    img = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    contorno = [(60, 40), (340, 40), (340, 210), (200, 370), (60, 210)]
    d.polygon(contorno, fill=(20, 82, 63, 255))
    d.polygon([(84, 64), (316, 64), (316, 200), (200, 336), (84, 200)], outline=(242, 194, 48, 255), width=6)
    # Un árbol sencillo: el arrayán que da nombre al colegio.
    d.rectangle([190, 200, 210, 280], fill=(242, 194, 48, 255))
    for cx, cy, r in [(200, 150, 52), (160, 180, 36), (240, 180, 36)]:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 255))
    salida = BytesIO()
    img.save(salida, format="PNG", optimize=True)
    return salida.getvalue()


NOMBRES_F = ["María José", "Valentina", "Sara Sofía", "Isabella", "Luciana", "Mariana", "Gabriela", "Salomé",
             "Juliana", "Antonella", "Daniela", "Laura Camila", "Paula Andrea", "Manuela", "Catalina"]
NOMBRES_M = ["Santiago", "Samuel", "Juan José", "Matías", "Emiliano", "Tomás", "Nicolás", "Sebastián",
             "Juan Pablo", "Andrés Felipe", "David", "Miguel Ángel", "Jerónimo", "Simón", "Felipe"]
APELLIDOS = ["Rojas", "Pinto", "Ardila", "Mantilla", "Serrano", "Rueda", "Vargas", "Gómez", "Díaz", "Suárez",
             "Prada", "Quintero", "Jaimes", "Carreño", "Villamizar", "Ortiz", "Cáceres", "Niño", "Afanador", "Pabón"]
ADULTOS_F = ["Claudia", "Sandra", "Luz Marina", "Adriana", "Diana", "Martha", "Yolanda", "Carolina", "Mónica", "Patricia"]
ADULTOS_M = ["Carlos", "Jorge", "Luis Fernando", "Óscar", "Jairo", "Hernán", "Wilson", "Fabián", "Germán", "Ricardo"]

ASIGNATURAS = [
    ("Matemáticas", "Matemáticas", 5), ("Lengua castellana", "Humanidades", 5), ("Inglés", "Humanidades", 4),
    ("Ciencias naturales", "Ciencias naturales", 4), ("Ciencias sociales", "Ciencias sociales", 4),
    ("Educación física", "Educación física", 2),
]
ACTIVIDADES = {
    "Matemáticas": [("Taller de fracciones", "taller"), ("Evaluación de ecuaciones", "evaluacion"), ("Proyecto de estadística", "proyecto"), ("Quiz de geometría", "evaluacion")],
    "Lengua castellana": [("Comprensión de lectura", "taller"), ("Ensayo argumentativo", "tarea"), ("Exposición del plan lector", "exposicion"), ("Evaluación de ortografía", "evaluacion")],
    "Inglés": [("Vocabulary quiz", "evaluacion"), ("Diálogo en parejas", "exposicion"), ("Workbook unidad 4", "taller"), ("Listening test", "evaluacion")],
    "Ciencias naturales": [("Laboratorio de mezclas", "taller"), ("Evaluación de la célula", "evaluacion"), ("Maqueta del ecosistema", "proyecto"), ("Consulta sobre energías", "tarea")],
    "Ciencias sociales": [("Mapa de regiones", "taller"), ("Línea de tiempo", "tarea"), ("Evaluación de la Independencia", "evaluacion"), ("Debate sobre participación", "exposicion")],
    "Educación física": [("Test de resistencia", "evaluacion"), ("Fundamentos de voleibol", "participacion"), ("Rutina de coordinación", "exposicion"), ("Autoevaluación", "autoevaluacion")],
}
TAREAS = {
    "Matemáticas": [("Ejercicios de la página 84", "Resolver los ejercicios 1 al 12 en el cuaderno, con procedimiento."), ("Guía de proporcionalidad", "Desarrollar la guía entregada en clase. Se revisa en el cuaderno.")],
    "Lengua castellana": [("Leer capítulos 5 y 6 del plan lector", "Traer tres preguntas sobre la lectura para el conversatorio."), ("Borrador del cuento", "Mínimo una página, con inicio, nudo y desenlace.")],
    "Inglés": [("Workbook páginas 40 a 42", "Completar las actividades de la unidad 5."), ("Grabar presentación personal", "Un audio de un minuto presentándose en inglés.")],
    "Ciencias naturales": [("Informe de laboratorio", "Entregar el informe con dibujo, materiales, procedimiento y conclusión."), ("Consulta sobre el ciclo del agua", "Una página con dibujo y fuentes.")],
    "Ciencias sociales": [("Entrevista a un adulto mayor", "Cinco preguntas sobre cómo era el barrio hace 30 años."), ("Mapa político de Colombia", "Ubicar departamentos y capitales, a color.")],
    "Educación física": [("Registro de actividad física", "Anotar durante una semana la actividad diaria y su duración."), ("Traer uniforme completo", "Sudadera, camiseta y tenis blancos para la prueba.")],
}
OBSERVACIONES = [
    ("reconocimiento", "Se destacó por su liderazgo en el trabajo en grupo y por ayudar a sus compañeros."),
    ("academica", "Ha bajado el ritmo en la entrega de trabajos. Se acordó con el estudiante un plan para ponerse al día."),
    ("convivencia", "Usó el celular en clase después de un primer llamado. Se dialogó y se comprometió a guardarlo."),
    ("reconocimiento", "Excelente participación en la feria de la ciencia."),
    ("academica", "Se recomienda reforzar en casa la lectura diaria de 20 minutos."),
]
COMENTARIOS = [
    "Estudiante responsable y participativa. Mantiene un rendimiento constante en todas las áreas.",
    "Muestra avances importantes frente al periodo anterior. Debe mantener la constancia en las tareas.",
    "Tiene buenas capacidades, pero necesita mayor acompañamiento en casa para cumplir con los compromisos.",
    "Se destaca por su respeto y colaboración. Puede mejorar su participación en clase.",
]


def dias_habiles(desde, hasta):
    d = desde
    while d <= hasta:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


class Command(BaseCommand):
    help = "Crea un colegio de ejemplo con estudiantes, notas, tareas y asistencia."

    def add_arguments(self, parser):
        parser.add_argument("--reiniciar", action="store_true", help="Borra el colegio de ejemplo y lo crea de nuevo.")
        parser.add_argument("--actualizar", action="store_true",
                            help="Lo crea si no existe y lo recrea solo si el ejemplo cambió de versión.")

    @transaction.atomic
    def handle(self, *args, **opciones):
        existente = Colegio.objects.filter(nombre=NOMBRE).defer("escudo").first()
        if existente:
            if opciones["actualizar"] and existente.version_demo >= VERSION:
                self.stdout.write("El colegio de ejemplo ya está al día.")
                return
            if not (opciones["reiniciar"] or opciones["actualizar"]):
                raise CommandError("El colegio de ejemplo ya existe. Use --reiniciar para recrearlo.")
            # Orden obligado por las relaciones protegidas: estudiantes, clases, grupos y al final el colegio.
            Estudiante.objects.filter(colegio=existente).delete()
            Clase.objects.filter(grupo__anio__colegio=existente).delete()
            Grupo.objects.filter(anio__colegio=existente).delete()
            existente.delete()

        rnd = random.Random(2026)
        hoy = timezone.localdate()
        anio_n = hoy.year
        col = Colegio.objects.create(
            nombre=NOMBRE, lema="Aprender para servir", nit="900.123.456-7", codigo_dane="368547000123",
            resolucion="Resolución de aprobación 04512 de 2015", ciudad="Piedecuesta, Santander",
            direccion="Carrera 6 # 12-40", telefono="607 655 0100", correo="secretaria@losarrayanes.example",
            nombre_rector="Beatriz Helena Mantilla Rueda", version_demo=VERSION,
            escudo=escudo_de_ejemplo(), escudo_tipo="image/png", escudo_version=1,
        )

        def usuario(doc, nombres, apellidos, rol, **extra):
            if "cargo" in extra and "permisos" not in extra:
                extra["permisos"] = Usuario.PERMISOS_POR_CARGO[extra["cargo"]]
            u = Usuario(username=doc, documento=doc, first_name=nombres, last_name=apellidos, rol=rol, colegio=col, **extra)
            u.set_password(CLAVE)
            u.save()
            return u

        usuario("1001", "Beatriz Helena", "Mantilla Rueda", "directivo", cargo="rector", email="rectoria@losarrayanes.example")
        usuario("1002", "Hernán Darío", "Pabón Suárez", "directivo", cargo="coordinador")
        secretaria = usuario("1003", "Yolanda", "Afanador Rojas", "directivo", cargo="secretaria")
        # Un administrativo con permisos a la medida: solo cartera y comunicados.
        usuario("1004", "Wilson", "Carreño Díaz", "directivo", cargo="administrativo", permisos=["comunicados", "pensiones"])
        usuario("1005", "Ricardo", "Niño Prada", "directivo", cargo="sistemas")
        docentes = [
            usuario("2001", "Jorge Enrique", "Serrano Prada", "docente", telefono="300 555 0101"),
            usuario("2002", "Diana Marcela", "Ardila Gómez", "docente", telefono="300 555 0102"),
            usuario("2003", "Andrea", "Villamizar Ortiz", "docente", telefono="300 555 0103"),
            usuario("2004", "Óscar Iván", "Jaimes Carreño", "docente", telefono="300 555 0104"),
            usuario("2005", "Luz Dary", "Quintero Niño", "docente", telefono="300 555 0105"),
            usuario("2006", "Fabián", "Cáceres Díaz", "docente", telefono="300 555 0106"),
        ]

        anio = AnioLectivo.objects.create(colegio=col, nombre=str(anio_n), activo=True)
        # Cuatro periodos; el vigente se ubica alrededor de hoy para que el ejemplo siempre se vea "en curso".
        inicio_p4 = hoy - timedelta(days=56)
        cortes = [inicio_p4 - timedelta(days=210), inicio_p4 - timedelta(days=140), inicio_p4 - timedelta(days=70), inicio_p4]
        nombres_p = ["Primer periodo", "Segundo periodo", "Tercer periodo", "Cuarto periodo"]
        periodos = []
        for i, ini in enumerate(cortes):
            fin = cortes[i + 1] - timedelta(days=1) if i < 3 else hoy + timedelta(days=13)
            periodos.append(Periodo.objects.create(
                anio=anio, numero=i + 1, nombre=nombres_p[i], fecha_inicio=ini, fecha_fin=fin,
                peso=Decimal("25"), cerrado=i < 3,
            ))

        asignaturas = [Asignatura.objects.create(colegio=col, nombre=n, area=a) for n, a, _ in ASIGNATURAS]
        horas = {n: h for n, _, h in ASIGNATURAS}
        grados = [
            Grado.objects.create(colegio=col, nombre="Sexto", abreviatura="6°", orden=6, pension=420000),
            Grado.objects.create(colegio=col, nombre="Séptimo", abreviatura="7°", orden=7, pension=420000),
            Grado.objects.create(colegio=col, nombre="Octavo", abreviatura="8°", orden=8, pension=440000),
        ]
        for g in grados:
            for asig in asignaturas:
                PlanEstudio.objects.create(grado=g, asignatura=asig, horas_semanales=horas[asig.nombre])
                Logro.objects.bulk_create([
                    Logro(grado=g, asignatura=asig, periodo_numero=n + 1, texto=texto)
                    for n, texto in enumerate(LOGROS[asig.nombre])
                ])
        Bloque.objects.bulk_create([
            Bloque(colegio=col, nombre=n, inicio=i, fin=f, descanso=d) for n, i, f, d in JORNADA
        ])
        grupos = [Grupo.objects.create(anio=anio, grado=g, nombre="A", director=docentes[i]) for i, g in enumerate(grados)]
        clases = []
        for grupo in grupos:
            for j, asig in enumerate(asignaturas):
                clases.append(Clase.objects.create(grupo=grupo, asignatura=asig, docente=docentes[j], horas_semanales=horas[asig.nombre]))

        # Preescolar: un grupo de Transición con valoración descriptiva por dimensiones.
        transicion = Grado.objects.create(colegio=col, nombre="Transición", abreviatura="TR", orden=0, descriptivo=True, pension=380000)
        maestra = usuario("2007", "Carolina", "Rueda Vargas", "docente", telefono="300 555 0107")
        grupo_tr = Grupo.objects.create(anio=anio, grado=transicion, nombre="A", director=maestra)
        clases_tr = []
        for nombre_dim in DIMENSIONES:
            dim = Asignatura.objects.create(colegio=col, nombre=nombre_dim, area="Preescolar")
            PlanEstudio.objects.create(grado=transicion, asignatura=dim, horas_semanales=5)
            clases_tr.append(Clase.objects.create(grupo=grupo_tr, asignatura=dim, docente=maestra, horas_semanales=5))
        horarios.generar(anio, semilla=7)

        # Estudiantes y acudientes. Algunos acudientes tienen dos hijos en grupos distintos.
        matriculas, doc_est, doc_ac = [], 1100000000, 3000
        acudientes_previos = []
        for gi, grupo in enumerate(grupos):
            for k in range(16):
                ap1, ap2 = rnd.sample(APELLIDOS, 2)
                nina = rnd.random() < 0.5
                doc_est += rnd.randint(11, 97)
                est = Estudiante.objects.create(
                    colegio=col, nombres=rnd.choice(NOMBRES_F if nina else NOMBRES_M), apellidos=f"{ap1} {ap2}",
                    documento=str(doc_est), fecha_nacimiento=date(anio_n - 11 - gi, rnd.randint(1, 12), rnd.randint(1, 28)),
                )
                m = Matricula.objects.create(estudiante=est, grupo=grupo, fecha=cortes[0])
                m.nivel = rnd.choice([4.6, 4.3, 4.0, 4.0, 3.7, 3.7, 3.4, 3.1, 2.8])  # tendencia académica del estudiante
                m.constancia = rnd.choice([0.98, 0.95, 0.95, 0.9, 0.85, 0.7])
                matriculas.append(m)
                if gi > 0 and k < 3 and acudientes_previos:
                    ac = acudientes_previos.pop()
                    est.apellidos = ac.apellidos_hijo
                    est.save()
                else:
                    doc_ac += 1
                    madre = rnd.random() < 0.65
                    ac = usuario(str(doc_ac), rnd.choice(ADULTOS_F if madre else ADULTOS_M),
                                 f"{ap2 if madre else ap1} {rnd.choice(APELLIDOS)}", "acudiente",
                                 telefono=f"31{rnd.randint(0, 9)} 555 {rnd.randint(1000, 9999)}")
                    ac.parentesco = "madre" if madre else "padre"
                    ac.apellidos_hijo = est.apellidos
                    if gi < 2 and k < 3:
                        acudientes_previos.append(ac)
                Acudiencia.objects.create(estudiante=est, acudiente=ac, parentesco=ac.parentesco)

        por_grupo = {g.id: [m for m in matriculas if m.grupo_id == g.id] for g in grupos}

        # Niños de Transición, con su acudiente y sus valoraciones de los periodos ya cerrados y del vigente.
        matriculas_tr = []
        for k in range(10):
            ap1, ap2 = rnd.sample(APELLIDOS, 2)
            nina = rnd.random() < 0.5
            doc_est += rnd.randint(11, 97)
            est = Estudiante.objects.create(
                colegio=col, nombres=rnd.choice(NOMBRES_F if nina else NOMBRES_M), apellidos=f"{ap1} {ap2}",
                tipo_documento="RC", documento=str(doc_est), fecha_nacimiento=date(anio_n - 5, rnd.randint(1, 12), rnd.randint(1, 28)),
            )
            m = Matricula.objects.create(estudiante=est, grupo=grupo_tr, fecha=cortes[0])
            m.constancia = rnd.choice([0.98, 0.95, 0.9])
            matriculas_tr.append(m)
            doc_ac += 1
            ac = usuario(str(doc_ac), rnd.choice(ADULTOS_F), f"{ap2} {rnd.choice(APELLIDOS)}", "acudiente",
                         telefono=f"31{rnd.randint(0, 9)} 555 {rnd.randint(1000, 9999)}")
            Acudiencia.objects.create(estudiante=est, acudiente=ac, parentesco="madre")
        ValoracionDescriptiva.objects.bulk_create([
            ValoracionDescriptiva(matricula=m, clase=c, periodo=p, texto=rnd.choice(VALORACIONES[c.asignatura.nombre]))
            for m in matriculas_tr for c in clases_tr for p in periodos
        ])

        # Notas
        notas = []
        for clase in clases:
            plan = ACTIVIDADES[clase.asignatura.nombre]
            dificultad = rnd.uniform(-0.25, 0.2)
            for pi, periodo in enumerate(periodos):
                vigente = pi == 3
                titulos = plan[:3] if not vigente else plan[:2]
                pesos = [30, 30, 40] if not vigente else [0, 0]
                for ai, (titulo, tipo) in enumerate(titulos):
                    dias = (periodo.fecha_fin - periodo.fecha_inicio).days
                    fecha = periodo.fecha_inicio + timedelta(days=int(dias * (ai + 1) / (len(titulos) + 1)))
                    if vigente:
                        fecha = min(fecha, hoy - timedelta(days=3 + 9 * (1 - ai)))
                    act = Actividad.objects.create(
                        clase=clase, periodo=periodo, titulo=titulo if pi == 0 or vigente else f"{titulo} {pi + 1}",
                        tipo=tipo, fecha=fecha, peso=Decimal(pesos[ai]),
                    )
                    for m in por_grupo[clase.grupo_id]:
                        if vigente and ai == 1 and rnd.random() < 0.12:
                            continue  # aún sin calificar
                        valor = m.nivel + dificultad + rnd.gauss(0, 0.45)
                        valor = max(1.0, min(5.0, round(valor, 1)))
                        notas.append(Nota(actividad=act, matricula=m, valor=Decimal(str(valor))))
        Nota.objects.bulk_create(notas)

        # Recuperaciones del tercer periodo: la mitad de quienes perdieron Matemáticas la presentaron.
        from core import calculos

        for clase in [c for c in clases if c.asignatura.nombre == "Matemáticas"]:
            _, _, definitivas = calculos.notas_de_clase(clase, periodos[2])
            perdieron = [m for m in por_grupo[clase.grupo_id] if definitivas.get(m.id) is not None and definitivas[m.id] < col.desde_basico]
            Recuperacion.objects.bulk_create([
                Recuperacion(matricula=m, clase=clase, periodo=periodos[2], valor=Decimal(rnd.choice(["3.2", "3.6", "4.0", "2.5"])),
                             fecha=periodos[2].fecha_fin)
                for m in perdieron[::2]
            ])

        # Asistencia de las últimas tres semanas
        asistencias = []
        for clase in clases:
            dias_clase = [d for d in dias_habiles(hoy - timedelta(days=21), hoy - timedelta(days=1))
                          if (d.weekday() + clase.asignatura_id) % 5 < clase.horas_semanales]
            for d in dias_clase:
                for m in por_grupo[clase.grupo_id]:
                    r = rnd.random()
                    estado = "P" if r < m.constancia else rnd.choice(["A", "A", "T", "E"])
                    asistencias.append(Asistencia(clase=clase, matricula=m, fecha=d, estado=estado))
        Asistencia.objects.bulk_create(asistencias)

        # Tareas: una ya vencida y revisada, otra por entregar
        entregas = []
        for clase in clases:
            (t1, d1), (t2, d2) = TAREAS[clase.asignatura.nombre]
            vencida = Tarea.objects.create(clase=clase, titulo=t1, descripcion=d1,
                                           fecha_asignacion=hoy - timedelta(days=12), fecha_entrega=hoy - timedelta(days=rnd.randint(3, 6)))
            Tarea.objects.create(clase=clase, titulo=t2, descripcion=d2, fecha_asignacion=hoy - timedelta(days=2),
                                 fecha_entrega=hoy + timedelta(days=rnd.randint(1, 6)),
                                 entrega_en_linea=clase.asignatura.nombre in ("Lengua castellana", "Inglés", "Ciencias naturales"))
            for m in por_grupo[clase.grupo_id]:
                r = rnd.random()
                estado = "entregada" if r < m.constancia - 0.05 else rnd.choice(["tarde", "no_entregada", "no_entregada"])
                entregas.append(Entrega(tarea=vencida, matricula=m, estado=estado,
                                        comentario="Debe traerla la próxima clase." if estado == "no_entregada" and rnd.random() < 0.5 else ""))
        Entrega.objects.bulk_create(entregas)

        for m in matriculas:
            if rnd.random() < 0.45:
                tipo, texto = rnd.choice(OBSERVACIONES)
                Observacion.objects.create(matricula=m, autor=m.grupo.director, tipo=tipo, texto=texto,
                                           fecha=hoy - timedelta(days=rnd.randint(2, 40)))
            for periodo in periodos[:3]:
                ComentarioBoletin.objects.create(matricula=m, periodo=periodo, texto=rnd.choice(COMENTARIOS))

        # Pensiones: tres meses cobrados; la mayoría al día y algunos en mora.
        cobros, pagos = [], []
        for desfase in (2, 1, 0):
            mes = (hoy.month - 1 - desfase) % 12
            vence = (hoy.replace(day=10) - timedelta(days=30 * desfase)).replace(day=10)
            for m in matriculas + matriculas_tr:
                cobros.append(Cobro(estudiante=m.estudiante, anio=anio, concepto=f"Pensión de {MESES[mes]}",
                                    valor=m.grupo.grado.pension, vence=vence))
        Cobro.objects.bulk_create(cobros)
        cumplido = {}
        for c in Cobro.objects.filter(anio=anio).order_by("vence"):
            cumplido.setdefault(c.estudiante_id, rnd.random())
            r = cumplido[c.estudiante_id]
            al_dia = r < 0.72 or (r < 0.9 and c.vence < hoy - timedelta(days=20))
            if c.vence <= hoy + timedelta(days=5) and al_dia:
                pagos.append(Pago(cobro=c, valor=c.valor, fecha=min(hoy, c.vence - timedelta(days=rnd.randint(0, 6))),
                                  medio=rnd.choice(["transferencia", "transferencia", "efectivo", "consignacion"]),
                                  referencia=str(rnd.randint(100000, 999999)), registrado_por=secretaria))
        Pago.objects.bulk_create(pagos)

        rectora = Usuario.objects.get(username="1001")
        Comunicado.objects.create(colegio=col, autor=rectora, titulo="Entrega de boletines del cuarto periodo",
                                  cuerpo="Apreciadas familias:\n\nLa entrega de boletines del cuarto periodo será el último viernes del mes, de 7:00 a 11:00 a. m., en cada salón.\n\nLos esperamos puntualmente.",
                                  publicado=timezone.now() - timedelta(days=2))
        Comunicado.objects.create(colegio=col, autor=rectora, titulo="Jornada pedagógica: no hay clases el viernes",
                                  cuerpo="El próximo viernes los docentes estarán en jornada pedagógica. Los estudiantes no asisten al colegio.",
                                  publicado=timezone.now() - timedelta(days=9))
        Comunicado.objects.create(colegio=col, autor=rectora, grupo=grupos[0], titulo="Salida pedagógica de Sexto A",
                                  cuerpo="La salida al Jardín Botánico es el próximo miércoles. Por favor envíen el permiso firmado antes del lunes.",
                                  publicado=timezone.now() - timedelta(days=4))

        ejemplo = Acudiencia.objects.filter(estudiante__matriculas__grupo=grupos[0]).select_related("acudiente", "estudiante").first()
        doble = next((a for a in Usuario.objects.filter(colegio=col, rol="acudiente") if a.acudidos.count() > 1), None)
        self.stdout.write(self.style.SUCCESS(f"\nColegio de ejemplo creado: {NOMBRE}"))
        self.stdout.write(f"  {len(matriculas)} estudiantes, {len(clases)} clases, {len(notas)} notas, {len(asistencias)} registros de asistencia\n")
        self.stdout.write(f"Contraseña de todas las cuentas de ejemplo: {CLAVE}")
        self.stdout.write("  Rectoría:      usuario 1001")
        self.stdout.write("  Coordinación:  usuario 1002")
        self.stdout.write("  Secretaría:    usuario 1003")
        self.stdout.write("  Administrativo (solo cartera y comunicados): usuario 1004")
        self.stdout.write("  Sistemas:      usuario 1005")
        self.stdout.write("  Docente:       usuario 2001 (Matemáticas, director de 6°A)")
        self.stdout.write("  Preescolar:    usuario 2007 (Transición, valoración descriptiva)")
        self.stdout.write(f"  Acudiente:  usuario {ejemplo.acudiente.username} ({ejemplo.acudiente.nombre_completo}, de {ejemplo.estudiante.nombre_natural})")
        if doble:
            self.stdout.write(f"  Acudiente con dos hijos: usuario {doble.username}")
