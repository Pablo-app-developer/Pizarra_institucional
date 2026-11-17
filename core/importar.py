"""Importación masiva de estudiantes y acudientes desde Excel."""
from io import BytesIO

from django.db import transaction
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from .forms import clave_temporal, matricular
from .models import Acudiencia, Estudiante, Grupo, Usuario

COLUMNAS = [
    "tipo_documento", "documento", "nombres", "apellidos", "grupo",
    "acudiente_documento", "acudiente_nombres", "acudiente_apellidos",
    "acudiente_parentesco", "acudiente_telefono", "acudiente_correo",
]
OBLIGATORIAS = ["documento", "nombres", "apellidos"]


def plantilla_xlsx():
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Estudiantes"
    hoja.append(COLUMNAS)
    for celda in hoja[1]:
        celda.font = Font(bold=True)
    hoja.append([
        "TI", "1098765432", "María José", "Rojas Pinto", "6°A",
        "63512345", "Claudia", "Pinto Gómez", "madre", "3001234567", "claudia@ejemplo.com",
    ])
    for letra, ancho in zip("ABCDEFGHIJK", [16, 16, 22, 24, 10, 22, 22, 24, 22, 20, 28]):
        hoja.column_dimensions[letra].width = ancho
    salida = BytesIO()
    libro.save(salida)
    return salida.getvalue()


def _texto(valor):
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)  # Excel entrega los documentos como 1098765432.0
    return str(valor).strip()


def importar_estudiantes(archivo, colegio):
    """Devuelve dict con creados, actualizados, cuentas nuevas y errores por fila.

    Cada fila se procesa en su propia transacción: una fila con error no
    impide que las demás se carguen.
    """
    resultado = {"creados": 0, "actualizados": 0, "cuentas": [], "errores": []}
    try:
        hoja = load_workbook(archivo, read_only=True, data_only=True).active
    except Exception:
        resultado["errores"].append((0, "No se pudo leer el archivo. Verifique que sea un Excel .xlsx válido."))
        return resultado

    filas = hoja.iter_rows(values_only=True)
    encabezado = [_texto(c).lower() for c in next(filas, [])]
    faltan = [c for c in OBLIGATORIAS if c not in encabezado]
    if faltan:
        resultado["errores"].append((1, f"Faltan columnas obligatorias: {', '.join(faltan)}. Use la plantilla."))
        return resultado

    grupos = {
        str(g).lower().replace("º", "°"): g
        for g in Grupo.objects.filter(anio__colegio=colegio, anio__activo=True).select_related("grado")
    }
    tipos = {t.value for t in Estudiante.TipoDocumento}
    parentescos = {p.value for p in Acudiencia.Parentesco}

    for numero, valores in enumerate(filas, start=2):
        fila = {col: _texto(valores[i]) if i < len(valores) else "" for i, col in enumerate(encabezado)}
        if not any(fila.values()):
            continue
        vacios = [c for c in OBLIGATORIAS if not fila.get(c)]
        if vacios:
            resultado["errores"].append((numero, f"Falta {', '.join(vacios)}."))
            continue
        grupo = None
        if fila.get("grupo"):
            grupo = grupos.get(fila["grupo"].lower().replace("º", "°").replace(" ", ""))
            if grupo is None:
                resultado["errores"].append((numero, f"El grupo «{fila['grupo']}» no existe en el año activo."))
                continue
        cuenta_nueva = None
        try:
            with transaction.atomic():
                tipo = fila.get("tipo_documento", "").upper()
                est, creado = Estudiante.objects.update_or_create(
                    colegio=colegio, documento=fila["documento"],
                    defaults={
                        "nombres": fila["nombres"], "apellidos": fila["apellidos"],
                        "tipo_documento": tipo if tipo in tipos else "TI", "activo": True,
                    },
                )
                if grupo:
                    matricular(est, grupo)
                doc_ac = fila.get("acudiente_documento")
                if doc_ac:
                    acudiente = Usuario.objects.filter(username=doc_ac).first()
                    if acudiente and (acudiente.colegio_id != colegio.id or acudiente.rol != "acudiente"):
                        raise ValueError(f"El documento {doc_ac} ya pertenece a otra cuenta que no es de acudiente.")
                    if acudiente is None:
                        if not fila.get("acudiente_nombres") or not fila.get("acudiente_apellidos"):
                            raise ValueError("Faltan nombres o apellidos del acudiente.")
                        clave = clave_temporal()
                        acudiente = Usuario(
                            username=doc_ac, documento=doc_ac, colegio=colegio, rol="acudiente",
                            first_name=fila["acudiente_nombres"], last_name=fila["acudiente_apellidos"],
                            telefono=fila.get("acudiente_telefono", ""), email=fila.get("acudiente_correo", ""),
                            debe_cambiar_clave=True,
                        )
                        acudiente.set_password(clave)
                        acudiente.save()
                        cuenta_nueva = {
                            "nombre": acudiente.nombre_completo, "usuario": doc_ac, "clave": clave,
                            "estudiante": est.nombre_natural,
                        }
                    parentesco = fila.get("acudiente_parentesco", "").lower()
                    Acudiencia.objects.update_or_create(
                        estudiante=est, acudiente=acudiente,
                        defaults={"parentesco": parentesco if parentesco in parentescos else "otro"},
                    )
            # Solo se informa la cuenta cuando la fila completa quedó guardada.
            if cuenta_nueva:
                resultado["cuentas"].append(cuenta_nueva)
            resultado["creados" if creado else "actualizados"] += 1
        except ValueError as error:
            resultado["errores"].append((numero, str(error)))
        except Exception:
            resultado["errores"].append((numero, "No se pudo guardar esta fila. Revise que los datos estén completos."))
    return resultado
