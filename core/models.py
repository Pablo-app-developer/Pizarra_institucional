from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone


# Módulos opcionales. Lo básico (estudiantes, notas, asistencia, tareas y boletines) siempre está.
MODULOS = [
    ("plan", "Plan de estudios y malla", "Asignaturas por grado, logros por periodo y malla curricular."),
    ("horarios", "Horarios", "Jornada escolar, generación automática y horario de grupos, docentes y familias."),
    ("recuperaciones", "Recuperaciones", "Notas de recuperación por periodo con tope configurable."),
    ("observador", "Observador del estudiante", "Anotaciones académicas, de convivencia y reconocimientos."),
    ("entregas", "Entrega de tareas en línea", "Los estudiantes envían respuesta, enlace o archivo."),
    ("comunicados", "Comunicados", "Mensajes del colegio a toda la comunidad o a un grupo."),
    ("avisos", "Avisos a las familias", "Notificaciones por nota baja, falta, tarea, comunicado y pago."),
    ("pensiones", "Pensiones", "Cobros, pagos, recibos, cartera y estado de cuenta para acudientes."),
]


def todos_los_modulos():
    return [m[0] for m in MODULOS]


class Colegio(models.Model):
    nombre = models.CharField(max_length=160)
    lema = models.CharField(max_length=200, blank=True)
    nit = models.CharField("NIT", max_length=30, blank=True)
    codigo_dane = models.CharField("código DANE", max_length=30, blank=True)
    resolucion = models.CharField(
        "resolución de aprobación", max_length=200, blank=True,
        help_text="Aparece en el encabezado del boletín.",
    )
    ciudad = models.CharField(max_length=80, blank=True)
    direccion = models.CharField("dirección", max_length=160, blank=True)
    telefono = models.CharField("teléfono", max_length=40, blank=True)
    correo = models.EmailField(blank=True)
    nombre_rector = models.CharField("nombre del rector(a)", max_length=120, blank=True)

    # Escala de valoración (Decreto 1290 de 2009). Cada colegio define la suya.
    nota_minima = models.DecimalField("nota mínima", max_digits=4, decimal_places=1, default=Decimal("1.0"))
    nota_maxima = models.DecimalField("nota máxima", max_digits=4, decimal_places=1, default=Decimal("5.0"))
    desde_basico = models.DecimalField(
        "Básico desde", max_digits=4, decimal_places=1, default=Decimal("3.0"),
        help_text="Nota mínima para aprobar. Por debajo es desempeño Bajo.",
    )
    desde_alto = models.DecimalField("Alto desde", max_digits=4, decimal_places=1, default=Decimal("4.0"))
    desde_superior = models.DecimalField("Superior desde", max_digits=4, decimal_places=1, default=Decimal("4.6"))
    tope_recuperacion = models.DecimalField(
        "nota máxima por recuperación", max_digits=4, decimal_places=1, null=True, blank=True,
        default=Decimal("3.0"),
        help_text="Quien recupera queda, como máximo, con esta nota en el periodo. Vacío: sin tope.",
    )
    decimales = models.PositiveSmallIntegerField(
        "decimales de las notas", default=1, choices=[(0, "Sin decimales (85)"), (1, "Un decimal (4,5)")],
    )
    perdidas_para_reprobar = models.PositiveSmallIntegerField(
        "asignaturas perdidas para reprobar el año", default=3,
        help_text="Con este número de asignaturas perdidas al final, el sistema propone reprobar.",
    )
    version_demo = models.PositiveSmallIntegerField(default=0, editable=False)
    modulos = models.JSONField(
        "módulos activos", default=todos_los_modulos, blank=True,
        help_text="Lo que este colegio tiene contratado. Lo que no esté activo no aparece para nadie.",
    )

    # Identidad: el escudo se guarda en la base de datos para que no dependa del disco del servidor.
    escudo = models.BinaryField(null=True, blank=True, editable=False)
    escudo_tipo = models.CharField(max_length=20, blank=True, editable=False)
    escudo_version = models.PositiveIntegerField(default=0, editable=False)
    color = models.CharField(
        "color institucional", max_length=7, blank=True,
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", "Use un color en formato #RRGGBB.")],
        help_text="Se usa en el menú, los botones y los informes. Vacío: verde Pizarra. Elija un tono oscuro para que el texto blanco se lea.",
    )

    class Meta:
        verbose_name = "colegio"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def clean(self):
        orden = [self.nota_minima, self.desde_basico, self.desde_alto, self.desde_superior, self.nota_maxima]
        if any(v is None for v in orden):
            return
        if any(a >= b for a, b in zip(orden[:-1], orden[1:-1])) or self.desde_superior > self.nota_maxima:
            raise ValidationError(
                "La escala debe ir en orden: nota mínima < Básico < Alto < Superior ≤ nota máxima."
            )
        if self.tope_recuperacion is not None and not (self.nota_minima <= self.tope_recuperacion <= self.nota_maxima):
            raise ValidationError("La nota máxima por recuperación debe estar dentro de la escala.")

    def desempeno(self, nota):
        """Devuelve (clave, etiqueta) del desempeño para una nota, o None."""
        if nota is None:
            return None
        if nota >= self.desde_superior:
            return ("superior", "Superior")
        if nota >= self.desde_alto:
            return ("alto", "Alto")
        if nota >= self.desde_basico:
            return ("basico", "Básico")
        return ("bajo", "Bajo")

    @property
    def anio_activo(self):
        return self.anios.filter(activo=True).first()

    def tiene(self, modulo):
        return modulo in (self.modulos or [])

    @property
    def cortes(self):
        """Los límites de la escala, para convertir notas de una escala a otra."""
        return [self.nota_minima, self.desde_basico, self.desde_alto, self.desde_superior, self.nota_maxima]

    @property
    def escudo_data_uri(self):
        """El escudo incrustado, para el PDF."""
        if not self.escudo:
            return ""
        import base64

        return f"data:{self.escudo_tipo};base64,{base64.b64encode(bytes(self.escudo)).decode()}"


class Usuario(AbstractUser):
    class Rol(models.TextChoices):
        DIRECTIVO = "directivo", "Directivo"
        DOCENTE = "docente", "Docente"
        ACUDIENTE = "acudiente", "Acudiente"
        ESTUDIANTE = "estudiante", "Estudiante"

    colegio = models.ForeignKey(Colegio, on_delete=models.CASCADE, null=True, blank=True, related_name="usuarios")
    rol = models.CharField(max_length=12, choices=Rol.choices, default=Rol.ACUDIENTE)
    documento = models.CharField(max_length=30, blank=True)
    telefono = models.CharField("teléfono", max_length=40, blank=True)
    class Cargo(models.TextChoices):
        RECTOR = "rector", "Rectoría"
        COORDINADOR = "coordinador", "Coordinación"
        SECRETARIA = "secretaria", "Secretaría"
        ADMINISTRATIVO = "administrativo", "Administrativo"
        SISTEMAS = "sistemas", "Sistemas"

    cargo = models.CharField(
        max_length=14, choices=Cargo.choices, blank=True,
        help_text="Al elegirlo se marcan los permisos habituales de ese cargo; luego puede ajustarlos.",
    )
    permisos = models.JSONField(
        default=list, blank=True,
        help_text="Secciones a las que este directivo tiene acceso.",
    )
    recibir_correos = models.BooleanField(
        "recibir avisos por correo", default=True,
        help_text="Notas bajas, faltas, tareas sin entregar y comunicados.",
    )
    debe_cambiar_clave = models.BooleanField(
        default=False, help_text="Pide una contraseña nueva en el próximo ingreso."
    )

    class Meta:
        verbose_name = "usuario"
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return self.nombre_completo

    @property
    def nombre_completo(self):
        return self.get_full_name() or self.username

    @property
    def es_directivo(self):
        return self.rol == self.Rol.DIRECTIVO

    @property
    def es_docente(self):
        return self.rol == self.Rol.DOCENTE

    @property
    def es_familia(self):
        return self.rol in (self.Rol.ACUDIENTE, self.Rol.ESTUDIANTE)

    # Permisos que se pueden dar o quitar a cada directivo, uno por uno.
    PERMISOS = [
        ("estudiantes", "Estudiantes y acudientes", "Crear, editar, matricular e importar estudiantes; administrar sus acudientes."),
        ("docentes", "Docentes", "Crear y editar docentes y restablecer sus contraseñas."),
        ("academico", "Estructura académica", "Años, periodos, grados, asignaturas, grupos, clases, plan de estudios y horarios."),
        ("notas", "Notas y observador", "Ver y corregir planillas de cualquier clase, el consolidado y el observador."),
        ("cierres", "Cierres", "Cerrar y reabrir periodos, y hacer el cierre de año con la promoción."),
        ("boletines", "Boletines", "Generar los informes en PDF de cualquier grupo."),
        ("comunicados", "Comunicados", "Publicar y editar comunicados para las familias."),
        ("pensiones", "Pensiones", "Cobros, pagos, recibos y cartera."),
        ("configuracion", "Configuración y permisos", "Identidad del colegio, sistema de evaluación, cuentas directivas y sus permisos, y la bitácora."),
    ]
    # Lo que trae marcado cada cargo al crearlo.
    PERMISOS_POR_CARGO = {
        "rector": [p[0] for p in PERMISOS],
        "coordinador": ["estudiantes", "docentes", "academico", "notas", "cierres", "boletines", "comunicados"],
        "secretaria": ["estudiantes", "boletines", "comunicados", "pensiones"],
        "administrativo": ["estudiantes", "comunicados"],
        "sistemas": ["estudiantes", "docentes", "academico", "comunicados", "configuracion"],
    }

    def puede(self, permiso):
        return self.es_directivo and permiso in (self.permisos or [])

    @property
    def etiqueta_rol(self):
        if self.es_directivo and self.cargo:
            return self.get_cargo_display()
        return self.get_rol_display()


class AnioLectivo(models.Model):
    colegio = models.ForeignKey(Colegio, on_delete=models.CASCADE, related_name="anios")
    nombre = models.CharField(max_length=20, help_text="Por ejemplo: 2026")
    activo = models.BooleanField(default=False, help_text="Solo un año puede estar activo a la vez.")

    class Meta:
        verbose_name = "año lectivo"
        verbose_name_plural = "años lectivos"
        ordering = ["-nombre"]
        constraints = [models.UniqueConstraint(fields=["colegio", "nombre"], name="anio_unico_por_colegio")]

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.activo:
            AnioLectivo.objects.filter(colegio=self.colegio).exclude(pk=self.pk).update(activo=False)


class Periodo(models.Model):
    anio = models.ForeignKey(AnioLectivo, on_delete=models.CASCADE, related_name="periodos", verbose_name="año lectivo")
    numero = models.PositiveSmallIntegerField("número")
    nombre = models.CharField(max_length=40, help_text="Por ejemplo: Primer periodo")
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    peso = models.DecimalField(
        "peso en la nota final (%)", max_digits=5, decimal_places=2, default=Decimal("25"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    cerrado = models.BooleanField(
        default=False,
        help_text="Un periodo cerrado no admite cambios de notas y publica el boletín a las familias.",
    )

    class Meta:
        verbose_name = "periodo"
        ordering = ["anio", "numero"]
        constraints = [models.UniqueConstraint(fields=["anio", "numero"], name="periodo_unico_por_anio")]

    def __str__(self):
        return self.nombre

    def clean(self):
        if self.fecha_inicio and self.fecha_fin and self.fecha_fin < self.fecha_inicio:
            raise ValidationError("La fecha final no puede ser anterior a la inicial.")


class Grado(models.Model):
    colegio = models.ForeignKey(Colegio, on_delete=models.CASCADE, related_name="grados")
    nombre = models.CharField(max_length=40, help_text="Por ejemplo: Sexto")
    abreviatura = models.CharField(max_length=6, help_text="Por ejemplo: 6°")
    orden = models.PositiveSmallIntegerField(
        default=0, help_text="Define el orden en las listas y a qué grado se promueve (el siguiente en orden)."
    )
    descriptivo = models.BooleanField(
        "valoración descriptiva (preescolar)", default=False,
        help_text="En lugar de notas, los docentes escriben una valoración por dimensión.",
    )
    pension = models.DecimalField(
        "pensión mensual", max_digits=10, decimal_places=0, default=0,
        help_text="Valor que se cobra cada mes a los estudiantes de este grado.",
    )

    class Meta:
        verbose_name = "grado"
        ordering = ["orden", "nombre"]

    def __str__(self):
        return self.nombre


class Grupo(models.Model):
    anio = models.ForeignKey(AnioLectivo, on_delete=models.CASCADE, related_name="grupos", verbose_name="año lectivo")
    grado = models.ForeignKey(Grado, on_delete=models.PROTECT, related_name="grupos")
    nombre = models.CharField(max_length=10, help_text="Por ejemplo: A")
    director = models.ForeignKey(
        Usuario, on_delete=models.SET_NULL, null=True, blank=True, related_name="grupos_dirigidos",
        verbose_name="director(a) de grupo", limit_choices_to={"rol": "docente"},
    )

    class Meta:
        verbose_name = "grupo"
        ordering = ["grado__orden", "nombre"]
        constraints = [models.UniqueConstraint(fields=["anio", "grado", "nombre"], name="grupo_unico_por_anio")]

    def __str__(self):
        return f"{self.grado.abreviatura}{self.nombre}"

    @property
    def nombre_largo(self):
        return f"{self.grado.nombre} {self.nombre}"


class Asignatura(models.Model):
    colegio = models.ForeignKey(Colegio, on_delete=models.CASCADE, related_name="asignaturas")
    nombre = models.CharField(max_length=80)
    area = models.CharField("área", max_length=80, blank=True, help_text="Por ejemplo: Humanidades")

    class Meta:
        verbose_name = "asignatura"
        ordering = ["area", "nombre"]

    def __str__(self):
        return self.nombre


class Clase(models.Model):
    """Una asignatura dictada por un docente a un grupo (carga académica)."""

    grupo = models.ForeignKey(Grupo, on_delete=models.CASCADE, related_name="clases")
    asignatura = models.ForeignKey(Asignatura, on_delete=models.PROTECT, related_name="clases")
    docente = models.ForeignKey(
        Usuario, on_delete=models.PROTECT, related_name="clases", limit_choices_to={"rol": "docente"},
        null=True, blank=True, help_text="Puede asignarlo después.",
    )
    horas_semanales = models.PositiveSmallIntegerField(default=4)

    class Meta:
        verbose_name = "clase"
        ordering = ["grupo__grado__orden", "grupo__nombre", "asignatura__nombre"]
        constraints = [models.UniqueConstraint(fields=["grupo", "asignatura"], name="clase_unica_por_grupo")]

    def __str__(self):
        return f"{self.asignatura} {self.grupo}"


class Estudiante(models.Model):
    class TipoDocumento(models.TextChoices):
        TI = "TI", "Tarjeta de identidad"
        RC = "RC", "Registro civil"
        CC = "CC", "Cédula de ciudadanía"
        CE = "CE", "Cédula de extranjería"
        PPT = "PPT", "Permiso por protección temporal"

    colegio = models.ForeignKey(Colegio, on_delete=models.CASCADE, related_name="estudiantes")
    nombres = models.CharField(max_length=80)
    apellidos = models.CharField(max_length=80)
    tipo_documento = models.CharField(max_length=4, choices=TipoDocumento.choices, default=TipoDocumento.TI)
    documento = models.CharField(max_length=30)
    fecha_nacimiento = models.DateField(null=True, blank=True)
    usuario = models.OneToOneField(
        Usuario, on_delete=models.SET_NULL, null=True, blank=True, related_name="estudiante",
        help_text="Cuenta con la que el estudiante consulta sus notas (opcional).",
    )
    acudientes = models.ManyToManyField(Usuario, through="Acudiencia", related_name="acudidos", blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "estudiante"
        ordering = ["apellidos", "nombres"]
        constraints = [
            models.UniqueConstraint(fields=["colegio", "documento"], name="documento_unico_por_colegio")
        ]

    def __str__(self):
        return f"{self.apellidos} {self.nombres}"

    @property
    def nombre_natural(self):
        return f"{self.nombres} {self.apellidos}"

    @property
    def iniciales(self):
        return (self.nombres[:1] + self.apellidos[:1]).upper()

    def matricula_actual(self):
        return (
            self.matriculas.filter(grupo__anio__activo=True, activa=True)
            .select_related("grupo__grado", "grupo__anio", "grupo__director")
            .first()
        )


class Acudiencia(models.Model):
    class Parentesco(models.TextChoices):
        MADRE = "madre", "Madre"
        PADRE = "padre", "Padre"
        ABUELO = "abuelo", "Abuelo(a)"
        TIO = "tio", "Tío(a)"
        HERMANO = "hermano", "Hermano(a)"
        OTRO = "otro", "Otro"

    estudiante = models.ForeignKey(Estudiante, on_delete=models.CASCADE)
    acudiente = models.ForeignKey(Usuario, on_delete=models.CASCADE, limit_choices_to={"rol": "acudiente"})
    parentesco = models.CharField(max_length=10, choices=Parentesco.choices, default=Parentesco.MADRE)

    class Meta:
        verbose_name = "acudiente del estudiante"
        constraints = [
            models.UniqueConstraint(fields=["estudiante", "acudiente"], name="acudiencia_unica")
        ]

    def __str__(self):
        return f"{self.acudiente} ({self.get_parentesco_display()}) de {self.estudiante}"


class Matricula(models.Model):
    estudiante = models.ForeignKey(Estudiante, on_delete=models.CASCADE, related_name="matriculas")
    grupo = models.ForeignKey(Grupo, on_delete=models.PROTECT, related_name="matriculas")
    class Resultado(models.TextChoices):
        PROMOVIDO = "promovido", "Promovido"
        REPROBADO = "reprobado", "Reprobado"
        GRADUADO = "graduado", "Graduado"

    activa = models.BooleanField(default=True, help_text="Desmarque si el estudiante se retiró del grupo.")
    fecha = models.DateField(default=timezone.localdate)
    resultado = models.CharField("resultado del año", max_length=10, choices=Resultado.choices, blank=True)

    class Meta:
        verbose_name = "matrícula"
        ordering = ["estudiante__apellidos", "estudiante__nombres"]
        constraints = [
            models.UniqueConstraint(fields=["estudiante", "grupo"], name="matricula_unica_por_grupo")
        ]

    def __str__(self):
        return f"{self.estudiante} en {self.grupo}"

    def clean(self):
        if not self.estudiante_id or not self.grupo_id:
            return
        otra = Matricula.objects.filter(
            estudiante=self.estudiante, grupo__anio=self.grupo.anio, activa=True
        ).exclude(pk=self.pk)
        if self.activa and otra.exists():
            raise ValidationError("El estudiante ya tiene una matrícula activa en este año lectivo.")


class Actividad(models.Model):
    """Actividad evaluativa de una clase en un periodo: columna de la planilla."""

    class Tipo(models.TextChoices):
        TALLER = "taller", "Taller"
        EVALUACION = "evaluacion", "Evaluación"
        TAREA = "tarea", "Tarea"
        EXPOSICION = "exposicion", "Exposición"
        PROYECTO = "proyecto", "Proyecto"
        PARTICIPACION = "participacion", "Participación"
        AUTOEVALUACION = "autoevaluacion", "Autoevaluación"

    clase = models.ForeignKey(Clase, on_delete=models.CASCADE, related_name="actividades")
    periodo = models.ForeignKey(Periodo, on_delete=models.CASCADE, related_name="actividades")
    titulo = models.CharField("título", max_length=120)
    tipo = models.CharField(max_length=16, choices=Tipo.choices, default=Tipo.TALLER)
    fecha = models.DateField(default=timezone.localdate)
    peso = models.DecimalField(
        "peso (%)", max_digits=5, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Deje todas en 0 para que todas las actividades valgan lo mismo.",
    )

    class Meta:
        verbose_name = "actividad evaluativa"
        verbose_name_plural = "actividades evaluativas"
        ordering = ["fecha", "id"]

    def __str__(self):
        return self.titulo


class Nota(models.Model):
    actividad = models.ForeignKey(Actividad, on_delete=models.CASCADE, related_name="notas")
    matricula = models.ForeignKey(Matricula, on_delete=models.CASCADE, related_name="notas")
    valor = models.DecimalField(max_digits=4, decimal_places=1)
    actualizada = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "nota"
        constraints = [models.UniqueConstraint(fields=["actividad", "matricula"], name="nota_unica")]

    def __str__(self):
        return f"{self.valor}"


def ruta_entrega(instancia, nombre):
    # Nombre impredecible: el archivo solo se sirve por una vista que verifica permisos.
    import uuid
    from pathlib import PurePath

    return f"entregas/{instancia.tarea_id}/{uuid.uuid4().hex}{PurePath(nombre).suffix.lower()[:8]}"


class Tarea(models.Model):
    clase = models.ForeignKey(Clase, on_delete=models.CASCADE, related_name="tareas")
    titulo = models.CharField("título", max_length=140)
    descripcion = models.TextField("instrucciones", blank=True)
    fecha_asignacion = models.DateField("asignada el", default=timezone.localdate)
    fecha_entrega = models.DateField("se entrega el")
    enlace = models.URLField("enlace de apoyo", blank=True, help_text="Guía, video o documento (opcional).")
    entrega_en_linea = models.BooleanField(
        "recibir la entrega por la plataforma", default=False,
        help_text="El estudiante podrá escribir su respuesta, pegar un enlace o subir un archivo.",
    )

    class Meta:
        verbose_name = "tarea"
        ordering = ["-fecha_entrega", "-id"]

    def __str__(self):
        return self.titulo

    def clean(self):
        if self.fecha_entrega and self.fecha_asignacion and self.fecha_entrega < self.fecha_asignacion:
            raise ValidationError("La fecha de entrega no puede ser anterior a la de asignación.")

    @property
    def vencida(self):
        return self.fecha_entrega < timezone.localdate()


class Entrega(models.Model):
    class Estado(models.TextChoices):
        ENVIADA = "enviada", "Enviada, por revisar"
        ENTREGADA = "entregada", "Entregada"
        TARDE = "tarde", "Entregada tarde"
        NO_ENTREGADA = "no_entregada", "No entregada"

    tarea = models.ForeignKey(Tarea, on_delete=models.CASCADE, related_name="entregas")
    matricula = models.ForeignKey(Matricula, on_delete=models.CASCADE, related_name="entregas")
    estado = models.CharField(max_length=14, choices=Estado.choices)
    comentario = models.CharField(max_length=240, blank=True)
    # Lo que envía el estudiante cuando la tarea se recibe por la plataforma.
    respuesta = models.TextField(blank=True)
    enlace = models.URLField(blank=True)
    archivo = models.FileField(upload_to=ruta_entrega, blank=True)
    enviada = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "entrega"
        constraints = [models.UniqueConstraint(fields=["tarea", "matricula"], name="entrega_unica")]


class Asistencia(models.Model):
    class Estado(models.TextChoices):
        PRESENTE = "P", "Asistió"
        TARDE = "T", "Llegó tarde"
        AUSENTE = "A", "Faltó"
        EXCUSA = "E", "Faltó con excusa"

    clase = models.ForeignKey(Clase, on_delete=models.CASCADE, related_name="asistencias")
    matricula = models.ForeignKey(Matricula, on_delete=models.CASCADE, related_name="asistencias")
    fecha = models.DateField()
    estado = models.CharField(max_length=1, choices=Estado.choices, default=Estado.PRESENTE)

    class Meta:
        verbose_name = "registro de asistencia"
        ordering = ["-fecha"]
        constraints = [
            models.UniqueConstraint(fields=["clase", "matricula", "fecha"], name="asistencia_unica")
        ]


class Observacion(models.Model):
    """Anotación en el observador del estudiante."""

    class Tipo(models.TextChoices):
        RECONOCIMIENTO = "reconocimiento", "Reconocimiento"
        ACADEMICA = "academica", "Académica"
        CONVIVENCIA = "convivencia", "Convivencia"

    matricula = models.ForeignKey(Matricula, on_delete=models.CASCADE, related_name="observaciones")
    autor = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, related_name="observaciones")
    tipo = models.CharField(max_length=16, choices=Tipo.choices, default=Tipo.ACADEMICA)
    texto = models.TextField("observación")
    fecha = models.DateField(default=timezone.localdate)
    visible_familia = models.BooleanField(
        "visible para la familia", default=True,
        help_text="Desmarque para dejarla como nota interna del colegio.",
    )

    class Meta:
        verbose_name = "observación"
        verbose_name_plural = "observaciones"
        ordering = ["-fecha", "-id"]


class ComentarioBoletin(models.Model):
    """Observación general del director de grupo para el boletín de un periodo."""

    matricula = models.ForeignKey(Matricula, on_delete=models.CASCADE, related_name="comentarios_boletin")
    periodo = models.ForeignKey(Periodo, on_delete=models.CASCADE)
    texto = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["matricula", "periodo"], name="comentario_boletin_unico")
        ]


class Comunicado(models.Model):
    colegio = models.ForeignKey(Colegio, on_delete=models.CASCADE, related_name="comunicados")
    autor = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, related_name="comunicados")
    titulo = models.CharField("título", max_length=140)
    cuerpo = models.TextField("mensaje")
    grupo = models.ForeignKey(
        Grupo, on_delete=models.CASCADE, null=True, blank=True, related_name="comunicados",
        help_text="Déjelo vacío para enviarlo a todo el colegio.",
    )
    publicado = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "comunicado"
        ordering = ["-publicado"]

    def __str__(self):
        return self.titulo


# ---------------------------------------------------------------- Plan de estudios


class PlanEstudio(models.Model):
    """Qué asignaturas se ven en un grado y con qué intensidad horaria."""

    grado = models.ForeignKey(Grado, on_delete=models.CASCADE, related_name="plan")
    asignatura = models.ForeignKey(Asignatura, on_delete=models.CASCADE, related_name="planes")
    horas_semanales = models.PositiveSmallIntegerField(default=4)

    class Meta:
        verbose_name = "asignatura del plan"
        ordering = ["grado__orden", "asignatura__area", "asignatura__nombre"]
        constraints = [models.UniqueConstraint(fields=["grado", "asignatura"], name="plan_unico")]

    def __str__(self):
        return f"{self.asignatura} en {self.grado}"


class Logro(models.Model):
    """Desempeño esperado de una asignatura en un grado y periodo. Sale en la malla y en el boletín."""

    grado = models.ForeignKey(Grado, on_delete=models.CASCADE, related_name="logros")
    asignatura = models.ForeignKey(Asignatura, on_delete=models.CASCADE, related_name="logros")
    periodo_numero = models.PositiveSmallIntegerField("periodo", default=1)
    texto = models.TextField("logro o desempeño")

    class Meta:
        verbose_name = "logro"
        ordering = ["grado__orden", "asignatura__nombre", "periodo_numero", "id"]

    def __str__(self):
        return self.texto[:60]


# ------------------------------------------------------------------------ Horarios


class Bloque(models.Model):
    """Franja de la jornada: una hora de clase o un descanso."""

    colegio = models.ForeignKey(Colegio, on_delete=models.CASCADE, related_name="bloques")
    nombre = models.CharField(max_length=30, help_text="Por ejemplo: 1ª hora o Descanso")
    inicio = models.TimeField()
    fin = models.TimeField()
    descanso = models.BooleanField(default=False, help_text="En los descansos no se programan clases.")

    class Meta:
        verbose_name = "bloque de la jornada"
        verbose_name_plural = "bloques de la jornada"
        ordering = ["inicio"]

    def __str__(self):
        return f"{self.nombre} ({self.inicio:%H:%M} a {self.fin:%H:%M})"

    def clean(self):
        if self.inicio and self.fin and self.fin <= self.inicio:
            raise ValidationError("La hora final debe ser posterior a la inicial.")


DIAS = [(1, "Lunes"), (2, "Martes"), (3, "Miércoles"), (4, "Jueves"), (5, "Viernes")]


class HorarioClase(models.Model):
    grupo = models.ForeignKey(Grupo, on_delete=models.CASCADE, related_name="horario")
    dia = models.PositiveSmallIntegerField(choices=DIAS)
    bloque = models.ForeignKey(Bloque, on_delete=models.CASCADE, related_name="horario")
    clase = models.ForeignKey(Clase, on_delete=models.CASCADE, related_name="horario")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["grupo", "dia", "bloque"], name="horario_unico_por_grupo")]


# ------------------------------------------------------------------ Recuperaciones


class Recuperacion(models.Model):
    """Nota de recuperación de una asignatura en un periodo."""

    matricula = models.ForeignKey(Matricula, on_delete=models.CASCADE, related_name="recuperaciones")
    clase = models.ForeignKey(Clase, on_delete=models.CASCADE, related_name="recuperaciones")
    periodo = models.ForeignKey(Periodo, on_delete=models.CASCADE, related_name="recuperaciones")
    valor = models.DecimalField(max_digits=4, decimal_places=1)
    fecha = models.DateField(default=timezone.localdate)

    class Meta:
        verbose_name = "recuperación"
        constraints = [
            models.UniqueConstraint(fields=["matricula", "clase", "periodo"], name="recuperacion_unica")
        ]


class ValoracionDescriptiva(models.Model):
    """Valoración cualitativa por dimensión, para grados de preescolar."""

    matricula = models.ForeignKey(Matricula, on_delete=models.CASCADE, related_name="valoraciones")
    clase = models.ForeignKey(Clase, on_delete=models.CASCADE, related_name="valoraciones")
    periodo = models.ForeignKey(Periodo, on_delete=models.CASCADE, related_name="valoraciones")
    texto = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["matricula", "clase", "periodo"], name="valoracion_unica")
        ]


# -------------------------------------------------------------------------- Avisos


class Aviso(models.Model):
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="avisos")
    texto = models.CharField(max_length=300)
    url = models.CharField(max_length=200, blank=True)
    leido = models.BooleanField(default=False)
    creado = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-creado"]
        indexes = [models.Index(fields=["usuario", "leido"])]


# ----------------------------------------------------------------------- Pensiones


class Cobro(models.Model):
    estudiante = models.ForeignKey(Estudiante, on_delete=models.CASCADE, related_name="cobros")
    anio = models.ForeignKey(AnioLectivo, on_delete=models.CASCADE, related_name="cobros")
    concepto = models.CharField(max_length=80, help_text="Por ejemplo: Pensión de marzo")
    valor = models.DecimalField(max_digits=10, decimal_places=0, validators=[MinValueValidator(1)])
    vence = models.DateField("fecha de vencimiento")

    class Meta:
        verbose_name = "cobro"
        ordering = ["vence", "id"]
        constraints = [
            models.UniqueConstraint(fields=["estudiante", "anio", "concepto"], name="cobro_unico_por_concepto")
        ]

    def __str__(self):
        return self.concepto


class Pago(models.Model):
    class Medio(models.TextChoices):
        TRANSFERENCIA = "transferencia", "Transferencia"
        EFECTIVO = "efectivo", "Efectivo"
        CONSIGNACION = "consignacion", "Consignación"
        DATAFONO = "datafono", "Datáfono"
        OTRO = "otro", "Otro"

    cobro = models.ForeignKey(Cobro, on_delete=models.CASCADE, related_name="pagos")
    valor = models.DecimalField(max_digits=10, decimal_places=0, validators=[MinValueValidator(1)])
    fecha = models.DateField(default=timezone.localdate)
    medio = models.CharField(max_length=14, choices=Medio.choices, default=Medio.TRANSFERENCIA)
    referencia = models.CharField("referencia o comprobante", max_length=60, blank=True)
    registrado_por = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, related_name="pagos_registrados")

    class Meta:
        verbose_name = "pago"
        ordering = ["fecha", "id"]


# ------------------------------------------------------------------------ Bitácora


class Bitacora(models.Model):
    """Registro de las acciones sensibles: quién hizo qué y cuándo."""

    colegio = models.ForeignKey(Colegio, on_delete=models.CASCADE, related_name="bitacora")
    usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, related_name="bitacora")
    accion = models.CharField(max_length=60)
    detalle = models.CharField(max_length=400, blank=True)
    fecha = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-fecha", "-id"]
