"""Sistema de evaluación: escalas predefinidas, decimales y conversión entre escalas.

El Decreto 1290 de 2009 (art. 5, hoy en el Decreto 1075 de 2015) deja que cada colegio
defina su escala numérica, pero exige expresar su equivalencia con la escala nacional:
Desempeño Superior, Alto, Básico y Bajo. Por eso los cuatro niveles son fijos y lo
que se elige es el rango numérico y dónde empieza cada nivel.
"""
from contextvars import ContextVar
from decimal import ROUND_HALF_UP, Decimal

# Decimales del colegio de la solicitud en curso. Lo fija el control de acceso en cada página.
_decimales = ContextVar("decimales", default=1)

# clave: (nombre, mínima, básico, alto, superior, máxima, decimales)
PREDEFINIDAS = {
    "1-5": ("1,0 a 5,0 (la más usada en Colombia)", "1.0", "3.0", "4.0", "4.6", "5.0", 1),
    "0-5": ("0,0 a 5,0", "0.0", "3.0", "4.0", "4.6", "5.0", 1),
    "1-10": ("1,0 a 10,0", "1.0", "6.0", "8.0", "9.5", "10.0", 1),
    "0-10": ("0,0 a 10,0", "0.0", "6.0", "8.0", "9.5", "10.0", 1),
    "0-100": ("0 a 100", "0", "60", "80", "95", "100", 0),
}
POR_DEFECTO = "1-5"


def usar_decimales(n):
    _decimales.set(1 if n is None else int(n))


def decimales():
    return _decimales.get()


def redondear(valor, n=None):
    if valor is None:
        return None
    n = decimales() if n is None else n
    return Decimal(valor).quantize(Decimal(1).scaleb(-n), rounding=ROUND_HALF_UP)


def clave_de(colegio):
    """La escala predefinida que coincide con la del colegio, o "" si es personalizada."""
    actual = tuple(Decimal(v) for v in colegio.cortes) + (colegio.decimales,)
    for clave, (_, *valores, dec) in PREDEFINIDAS.items():
        if tuple(Decimal(v) for v in valores) + (dec,) == actual:
            return clave
    return ""


def convertir(valor, vieja, nueva, n=1):
    """Pasa una nota de una escala a otra conservando su nivel de desempeño.

    vieja y nueva son [mínima, básico, alto, superior, máxima]. La conversión es proporcional
    dentro de cada nivel: quien estaba en Básico sigue en Básico, y quien estaba justo en el
    límite para aprobar queda justo en el nuevo límite.
    """
    valor = Decimal(valor)
    vieja = [Decimal(v) for v in vieja]
    nueva = [Decimal(v) for v in nueva]
    valor = min(max(valor, vieja[0]), vieja[4])
    for i in range(3, -1, -1):
        if valor >= vieja[i]:
            ancho = vieja[i + 1] - vieja[i]
            fraccion = (valor - vieja[i]) / ancho if ancho else Decimal(0)
            resultado = redondear(nueva[i] + fraccion * (nueva[i + 1] - nueva[i]), n)
            # El redondeo nunca debe subir a alguien de nivel.
            if i < 3 and resultado >= nueva[i + 1] and valor < vieja[i + 1]:
                resultado = nueva[i + 1] - Decimal(1).scaleb(-n)
            return resultado
    return nueva[0]
