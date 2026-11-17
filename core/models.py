from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


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
    nota_minima = models.DecimalField("nota mínima", max_digits=3, decimal_places=1, default=Decimal("1.0"))
    nota_maxima = models.DecimalField("nota máxima", max_digits=3, decimal_places=1, default=Decimal("5.0"))
    desde_basico = models.DecimalField(
        "Básico desde", max_digits=3, decimal_places=1, default=Decimal("3.0"),
        help_text="Nota mínima para aprobar. Por debajo es desempeño Bajo.",
    )
    desde_alto = models.DecimalField("Alto desde", max_digits=3, decimal_places=1, default=Decimal("4.0"))
    desde_superior = models.DecimalField("Superior desde", max_digits=3, decimal_places=1, default=Decimal("4.6"))

    class Meta:
        verbose_name = "colegio"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def clean(self):
        orden = [self.nota_minima, self.desde_basico, self.desde_alto, self.desde_superior, self.nota_maxima]
        if any(a >= b for a, b in zip(orden, orden[1:])):
            raise ValidationError(
                "La escala debe ir en orden: nota mínima < Básico < Alto < Superior < nota máxima."
            )

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
    orden = models.PositiveSmallIntegerField(default=0, help_text="Define el orden en las listas.")

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
        Usuario, on_delete=models.PROTECT, related_name="clases", limit_choices_to={"rol": "docente"}
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
    activa = models.BooleanField(default=True, help_text="Desmarque si el estudiante se retiró del grupo.")
    fecha = models.DateField(default=timezone.localdate)

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
    valor = models.DecimalField(max_digits=3, decimal_places=1)
    actualizada = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "nota"
        constraints = [models.UniqueConstraint(fields=["actividad", "matricula"], name="nota_unica")]

    def __str__(self):
        return f"{self.valor}"


class Tarea(models.Model):
    clase = models.ForeignKey(Clase, on_delete=models.CASCADE, related_name="tareas")
    titulo = models.CharField("título", max_length=140)
    descripcion = models.TextField("instrucciones", blank=True)
    fecha_asignacion = models.DateField("asignada el", default=timezone.localdate)
    fecha_entrega = models.DateField("se entrega el")
    enlace = models.URLField("enlace de apoyo", blank=True, help_text="Guía, video o documento (opcional).")

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
        ENTREGADA = "entregada", "Entregada"
        TARDE = "tarde", "Entregada tarde"
        NO_ENTREGADA = "no_entregada", "No entregada"

    tarea = models.ForeignKey(Tarea, on_delete=models.CASCADE, related_name="entregas")
    matricula = models.ForeignKey(Matricula, on_delete=models.CASCADE, related_name="entregas")
    estado = models.CharField(max_length=14, choices=Estado.choices)
    comentario = models.CharField(max_length=240, blank=True)

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
