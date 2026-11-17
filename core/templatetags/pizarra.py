from django import template
from django.utils.formats import number_format
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

ICONOS = {
    "inicio": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20h14V9.5"/>',
    "personas": '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c.6-3.6 3-5.5 6-5.5s5.4 1.9 6 5.5"/><path d="M16 5.2a3 3 0 0 1 0 5.6M18 14.8c1.7.7 2.7 2.4 3 5.2"/>',
    "docente": '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4M7 9h6M7 12h4"/>',
    "familia": '<circle cx="8" cy="7" r="3"/><circle cx="17" cy="10" r="2.2"/><path d="M2.5 20c.5-4 2.7-6 5.5-6s5 2 5.5 6M14 20c.3-2.6 1.4-4 3-4s2.7 1.4 3 4"/>',
    "grupos": '<rect x="3" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5"/>',
    "notas": '<path d="M4 4h16v16H4z"/><path d="M4 9.5h16M4 15h16M10 4v16"/>',
    "boletin": '<path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4M9 12h7M9 16h7"/>',
    "comunicado": '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 9h8M8 12h5"/>',
    "ajustes": '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
    "salir": '<path d="M10 4H5v16h5M14 8l4 4-4 4M18 12H9"/>',
    "llave": '<circle cx="8" cy="14" r="4"/><path d="m11 11 9-8M16 6l3 3"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "plan": '<path d="M5 4h11l3 3v13H5z"/><path d="M8 9h8M8 13h8M8 17h5"/>',
    "horario": '<rect x="3" y="5" width="18" height="16" rx="1.5"/><path d="M3 10h18M8 3v4M16 3v4M9 10v11M15 10v11M3 15.5h18"/>',
    "pension": '<rect x="3" y="6" width="18" height="12" rx="1.5"/><circle cx="12" cy="12" r="2.6"/><path d="M6.5 9.5v5M17.5 9.5v5"/>',
    "aviso": '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5h4"/>',
}


@register.simple_tag
def icono(nombre):
    trazos = ICONOS.get(nombre, "")
    return mark_safe(
        '<svg class="icono" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" '
        f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{trazos}</svg>'
    )


@register.filter
def nota(valor):
    """4.5 -> «4,5»; None -> raya."""
    if valor is None or valor == "":
        return "—"
    return number_format(valor, decimal_pos=1)


@register.simple_tag(takes_context=True)
def desempeno(context, valor, con_texto=True):
    """Nota con su nivel de desempeño según la escala del colegio."""
    colegio = context.get("colegio")
    if valor is None or colegio is None:
        return mark_safe('<span class="nota nota--vacia">Sin nota</span>')
    clave, etiqueta = colegio.desempeno(valor)
    if con_texto:
        return format_html(
            '<span class="nota nota--{}"><b>{}</b> {}</span>', clave, number_format(valor, decimal_pos=1), etiqueta
        )
    return format_html(
        '<span class="nota nota--{}" title="{}"><b>{}</b></span>', clave, etiqueta, number_format(valor, decimal_pos=1)
    )


@register.filter
def clave(diccionario, llave):
    try:
        return diccionario.get(llave)
    except AttributeError:
        return None


@register.filter
def es_casilla(campo):
    return campo.field.widget.__class__.__name__ == "CheckboxInput"
