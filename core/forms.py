import secrets
import string

from django import forms
from django.core.exceptions import ValidationError

from .models import (
    Actividad, Acudiencia, AnioLectivo, Asignatura, Bloque, Clase, Cobro, Colegio, Comunicado, Estudiante,
    Grado, Grupo, Logro, Matricula, Observacion, Pago, Periodo, PlanEstudio, Tarea, Usuario,
)

# Sin caracteres que se confunden al dictarlos o copiarlos a mano (0/O, 1/l).
ALFABETO_CLAVE = "".join(c for c in string.ascii_lowercase + string.digits if c not in "0o1l")


def clave_temporal(largo=8):
    return "".join(secrets.choice(ALFABETO_CLAVE) for _ in range(largo))


class FormularioColegio(forms.ModelForm):
    """Base: recibe el colegio y limita a él todas las listas desplegables."""

    def __init__(self, *args, colegio=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.colegio = colegio
        for campo in self.fields.values():
            if isinstance(campo.widget, forms.DateInput):
                campo.widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
            if isinstance(campo.widget, forms.Textarea):
                campo.widget.attrs.setdefault("rows", 4)
        self.limitar()

    def limitar(self):
        c = self.colegio
        filtros = {
            "grado": Grado.objects.filter(colegio=c),
            "anio": AnioLectivo.objects.filter(colegio=c),
            "asignatura": Asignatura.objects.filter(colegio=c),
            "docente": Usuario.objects.filter(colegio=c, rol="docente", is_active=True),
            "director": Usuario.objects.filter(colegio=c, rol="docente", is_active=True),
            "grupo": Grupo.objects.filter(anio__colegio=c, anio__activo=True).select_related("grado"),
        }
        for nombre, qs in filtros.items():
            if nombre in self.fields:
                self.fields[nombre].queryset = qs

    def save(self, commit=True):
        obj = super().save(commit=False)
        if hasattr(obj, "colegio_id") and obj.colegio_id is None:
            obj.colegio = self.colegio
        if commit:
            obj.save()
            self.save_m2m()
        return obj


FORMATOS_ESCUDO = {"PNG", "JPEG", "WEBP"}


def procesar_escudo(archivo):
    """Valida la imagen y la devuelve como PNG de máximo 400 px.

    Se vuelve a codificar siempre: así se descartan metadatos y cualquier contenido
    que no sea la imagen misma. No se aceptan SVG.
    """
    from io import BytesIO

    from PIL import Image, UnidentifiedImageError

    if archivo.size > 2 * 1024 * 1024:
        raise ValidationError("El escudo supera los 2 MB. Use una imagen más liviana.")
    try:
        imagen = Image.open(archivo)
        if imagen.format not in FORMATOS_ESCUDO:
            raise ValidationError("Use una imagen PNG, JPG o WebP.")
        imagen.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValidationError("No se pudo leer la imagen. Use un archivo PNG, JPG o WebP.")
    imagen = imagen.convert("RGBA")
    imagen.thumbnail((400, 400))
    salida = BytesIO()
    imagen.save(salida, format="PNG", optimize=True)
    return salida.getvalue()


class ColegioForm(forms.ModelForm):
    imagen_escudo = forms.FileField(
        label="Escudo del colegio", required=False,
        help_text="PNG, JPG o WebP, preferiblemente con fondo transparente. Sale en el menú, el ingreso y los informes.",
    )
    quitar_escudo = forms.BooleanField(label="Quitar el escudo actual", required=False)

    class Meta:
        model = Colegio
        fields = [
            "nombre", "lema", "color", "nit", "codigo_dane", "resolucion", "nombre_rector", "ciudad",
            "direccion", "telefono", "correo", "nota_minima", "desde_basico", "desde_alto",
            "desde_superior", "nota_maxima", "tope_recuperacion", "perdidas_para_reprobar",
        ]
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}

    field_order = ["nombre", "lema", "imagen_escudo", "quitar_escudo", "color"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.escudo:
            del self.fields["quitar_escudo"]
        if not self.instance.color:
            self.initial["color"] = "#14523f"

    def clean_imagen_escudo(self):
        archivo = self.cleaned_data.get("imagen_escudo")
        return procesar_escudo(archivo) if archivo else None

    def clean_color(self):
        color = self.cleaned_data.get("color", "").lower()
        return "" if color == "#14523f" else color

    def save(self, commit=True):
        colegio = super().save(commit=False)
        nuevo = self.cleaned_data.get("imagen_escudo")
        if nuevo:
            colegio.escudo, colegio.escudo_tipo = nuevo, "image/png"
            colegio.escudo_version += 1
        elif self.cleaned_data.get("quitar_escudo"):
            colegio.escudo, colegio.escudo_tipo = None, ""
            colegio.escudo_version += 1
        if commit:
            colegio.save()
        return colegio


class AnioForm(FormularioColegio):
    class Meta:
        model = AnioLectivo
        fields = ["nombre", "activo"]


class PeriodoForm(FormularioColegio):
    class Meta:
        model = Periodo
        fields = ["anio", "numero", "nombre", "fecha_inicio", "fecha_fin", "peso", "cerrado"]
        widgets = {"fecha_inicio": forms.DateInput, "fecha_fin": forms.DateInput}


class GradoForm(FormularioColegio):
    class Meta:
        model = Grado
        fields = ["nombre", "abreviatura", "orden", "descriptivo", "pension"]


class GrupoForm(FormularioColegio):
    class Meta:
        model = Grupo
        fields = ["anio", "grado", "nombre", "director"]


class AsignaturaForm(FormularioColegio):
    class Meta:
        model = Asignatura
        fields = ["nombre", "area"]


class ClaseForm(FormularioColegio):
    class Meta:
        model = Clase
        fields = ["grupo", "asignatura", "docente", "horas_semanales"]


class ComunicadoForm(FormularioColegio):
    class Meta:
        model = Comunicado
        fields = ["titulo", "cuerpo", "grupo"]
        widgets = {"cuerpo": forms.Textarea(attrs={"rows": 8})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["grupo"].empty_label = "Todo el colegio"
        self.fields["grupo"].label = "Dirigido a"


class EstudianteForm(FormularioColegio):
    grupo = forms.ModelChoiceField(
        queryset=Grupo.objects.none(), required=False, label="Grupo de este año",
        empty_label="Sin matricular",
    )

    class Meta:
        model = Estudiante
        fields = ["nombres", "apellidos", "tipo_documento", "documento", "fecha_nacimiento", "activo"]
        widgets = {"fecha_nacimiento": forms.DateInput}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            actual = self.instance.matricula_actual()
            if actual:
                self.fields["grupo"].initial = actual.grupo_id

    def clean_documento(self):
        doc = self.cleaned_data["documento"].strip()
        repetido = Estudiante.objects.filter(colegio=self.colegio, documento=doc).exclude(pk=self.instance.pk)
        if repetido.exists():
            raise ValidationError("Ya hay un estudiante con este documento.")
        return doc

    def save(self, commit=True):
        est = super().save(commit=commit)
        grupo = self.cleaned_data.get("grupo")
        if commit:
            matricular(est, grupo)
        return est


def matricular(estudiante, grupo):
    """Deja al estudiante con una sola matrícula activa en el año del grupo."""
    activas = Matricula.objects.filter(estudiante=estudiante, grupo__anio__activo=True, activa=True)
    if grupo is None:
        activas.update(activa=False)
        return None
    activas.exclude(grupo=grupo).update(activa=False)
    matricula, _ = Matricula.objects.update_or_create(
        estudiante=estudiante, grupo=grupo, defaults={"activa": True}
    )
    return matricula


class PersonaForm(FormularioColegio):
    """Docentes, acudientes y directivos. El usuario de ingreso es el documento."""

    rol = None

    class Meta:
        model = Usuario
        fields = ["first_name", "last_name", "documento", "email", "telefono", "is_active"]
        labels = {
            "first_name": "Nombres", "last_name": "Apellidos", "email": "Correo",
            "is_active": "Cuenta activa",
        }
        help_texts = {"is_active": "Desmarque para impedir el ingreso sin borrar el historial."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre in ("first_name", "last_name", "documento"):
            self.fields[nombre].required = True
        self.clave_generada = None

    def clean_documento(self):
        doc = self.cleaned_data["documento"].strip()
        if Usuario.objects.filter(username=doc).exclude(pk=self.instance.pk).exists():
            raise ValidationError("Ya existe una cuenta con este documento.")
        return doc

    def save(self, commit=True):
        usuario = super().save(commit=False)
        nuevo = usuario.pk is None
        usuario.username = self.cleaned_data["documento"]
        usuario.colegio = self.colegio
        if nuevo:
            usuario.rol = self.rol
            self.clave_generada = clave_temporal()
            usuario.set_password(self.clave_generada)
            usuario.debe_cambiar_clave = True
        if commit:
            usuario.save()
        return usuario


class DocenteForm(PersonaForm):
    rol = "docente"


class AcudienteForm(PersonaForm):
    rol = "acudiente"


class DirectivoForm(PersonaForm):
    rol = "directivo"

    class Meta(PersonaForm.Meta):
        fields = ["first_name", "last_name", "documento", "cargo", "email", "telefono", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cargo"].required = True
        self.fields["cargo"].help_text = (
            "Rectoría: todo. Coordinación: lo académico. Secretaría: estudiantes, boletines y pensiones."
        )


class MiCuentaForm(forms.ModelForm):
    class Meta:
        model = Usuario
        fields = ["email", "telefono", "recibir_correos"]
        labels = {"email": "Correo"}
        help_texts = {"email": "Sirve para recuperar la contraseña y recibir avisos del colegio."}


class BloqueForm(FormularioColegio):
    class Meta:
        model = Bloque
        fields = ["nombre", "inicio", "fin", "descanso"]
        widgets = {"inicio": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
                   "fin": forms.TimeInput(attrs={"type": "time"}, format="%H:%M")}


class PlanForm(forms.ModelForm):
    class Meta:
        model = PlanEstudio
        fields = ["asignatura", "horas_semanales"]

    def __init__(self, *args, grado=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.grado = grado
        self.fields["asignatura"].queryset = Asignatura.objects.filter(colegio=grado.colegio).exclude(
            planes__grado=grado
        )


class LogroForm(forms.ModelForm):
    class Meta:
        model = Logro
        fields = ["periodo_numero", "texto"]
        widgets = {"texto": forms.Textarea(attrs={"rows": 3})}


class CobroForm(forms.ModelForm):
    class Meta:
        model = Cobro
        fields = ["concepto", "valor", "vence"]
        widgets = {"vence": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}


class GenerarCobrosForm(forms.Form):
    concepto = forms.CharField(max_length=80, help_text="Por ejemplo: Pensión de marzo")
    vence = forms.DateField(label="Fecha de vencimiento", widget=forms.DateInput(attrs={"type": "date"}))


class PagoForm(forms.ModelForm):
    class Meta:
        model = Pago
        fields = ["valor", "fecha", "medio", "referencia"]
        widgets = {"fecha": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args, saldo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.saldo = saldo
        if saldo is not None and not self.is_bound:
            self.fields["valor"].initial = saldo

    def clean_valor(self):
        valor = self.cleaned_data["valor"]
        if self.saldo is not None and valor > self.saldo:
            raise ValidationError(f"El pago supera el saldo pendiente de este cobro ($ {self.saldo:,.0f}).".replace(",", "."))
        return valor


EXTENSIONES_ENTREGA = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".jpg", ".jpeg", ".png", ".txt", ".zip"}


class EntregaEnLineaForm(forms.Form):
    respuesta = forms.CharField(label="Respuesta", widget=forms.Textarea(attrs={"rows": 5}), required=False)
    enlace = forms.URLField(label="Enlace al trabajo", required=False, help_text="Por ejemplo, un documento de Drive.")
    archivo = forms.FileField(
        label="Archivo", required=False,
        help_text="PDF, Word, Excel, PowerPoint, imagen o ZIP, de máximo 10 MB.",
    )

    def clean_archivo(self):
        archivo = self.cleaned_data.get("archivo")
        if not archivo:
            return archivo
        from pathlib import PurePath

        if PurePath(archivo.name).suffix.lower() not in EXTENSIONES_ENTREGA:
            raise ValidationError("Ese tipo de archivo no se recibe. Use PDF, Word, Excel, PowerPoint, imagen o ZIP.")
        if archivo.size > 10 * 1024 * 1024:
            raise ValidationError("El archivo supera los 10 MB.")
        return archivo

    def clean(self):
        datos = super().clean()
        if not self.errors and not any(datos.get(c) for c in ("respuesta", "enlace", "archivo")):
            raise ValidationError("Escriba la respuesta, pegue un enlace o adjunte un archivo.")
        return datos


class VincularAcudienteForm(forms.Form):
    documento = forms.CharField(label="Documento del acudiente", max_length=30)
    parentesco = forms.ChoiceField(choices=Acudiencia.Parentesco.choices)
    first_name = forms.CharField(label="Nombres", max_length=150, required=False)
    last_name = forms.CharField(label="Apellidos", max_length=150, required=False)
    email = forms.EmailField(label="Correo", required=False)
    telefono = forms.CharField(label="Teléfono", max_length=40, required=False)

    def __init__(self, *args, colegio=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.colegio = colegio
        self.existente = None

    def clean(self):
        datos = super().clean()
        doc = (datos.get("documento") or "").strip()
        if not doc:
            return datos
        datos["documento"] = doc
        usuario = Usuario.objects.filter(username=doc).first()
        if usuario:
            if usuario.colegio_id != self.colegio.id or usuario.rol != "acudiente":
                raise ValidationError("Ese documento pertenece a una cuenta que no es de acudiente.")
            self.existente = usuario
        elif not datos.get("first_name") or not datos.get("last_name"):
            raise ValidationError(
                "No hay un acudiente con ese documento. Escriba nombres y apellidos para crearlo."
            )
        return datos


class ActividadForm(forms.ModelForm):
    class Meta:
        model = Actividad
        fields = ["titulo", "tipo", "fecha", "peso"]
        widgets = {"fecha": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}


class TareaForm(forms.ModelForm):
    class Meta:
        model = Tarea
        fields = ["titulo", "descripcion", "fecha_asignacion", "fecha_entrega", "enlace", "entrega_en_linea"]
        widgets = {
            "fecha_asignacion": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "fecha_entrega": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "descripcion": forms.Textarea(attrs={"rows": 5}),
        }


class ObservacionForm(forms.ModelForm):
    class Meta:
        model = Observacion
        fields = ["tipo", "texto", "fecha", "visible_familia"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "texto": forms.Textarea(attrs={"rows": 3}),
        }


class ImportarForm(forms.Form):
    archivo = forms.FileField(label="Archivo de Excel (.xlsx)")

    def clean_archivo(self):
        archivo = self.cleaned_data["archivo"]
        if not archivo.name.lower().endswith(".xlsx"):
            raise ValidationError("El archivo debe ser de Excel con extensión .xlsx.")
        if archivo.size > 5 * 1024 * 1024:
            raise ValidationError("El archivo supera los 5 MB.")
        return archivo
