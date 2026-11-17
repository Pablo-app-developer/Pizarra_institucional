import secrets
import string

from django import forms
from django.core.exceptions import ValidationError

from .models import (
    Actividad, Acudiencia, AnioLectivo, Asignatura, Clase, Colegio, Comunicado, Estudiante,
    Grado, Grupo, Matricula, Observacion, Periodo, Tarea, Usuario,
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


class ColegioForm(forms.ModelForm):
    class Meta:
        model = Colegio
        fields = [
            "nombre", "lema", "nit", "codigo_dane", "resolucion", "nombre_rector", "ciudad",
            "direccion", "telefono", "correo", "nota_minima", "desde_basico", "desde_alto",
            "desde_superior", "nota_maxima",
        ]


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
        fields = ["nombre", "abreviatura", "orden"]


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
        fields = ["titulo", "descripcion", "fecha_asignacion", "fecha_entrega", "enlace"]
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
