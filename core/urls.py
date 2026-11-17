from django.urls import path

from .views import comunes, directivo, docente, familia

urlpatterns = [
    path("", comunes.inicio, name="inicio"),
    path("cuenta/clave/", comunes.cambiar_clave, name="cambiar_clave"),
    path("comunicados/", comunes.comunicados, name="comunicados"),
    path("boletin/<int:matricula_id>/<int:periodo_id>/", comunes.boletin_estudiante, name="boletin_estudiante"),
    path("boletines/grupo/<int:grupo_id>/<int:periodo_id>/", comunes.boletines_grupo, name="boletines_grupo"),

    # Directivos
    path("colegio/", directivo.panel, name="panel"),
    path("colegio/grupos/", directivo.grupos, name="grupos"),
    path("colegio/consolidado/", directivo.consolidado, name="consolidado"),
    path("colegio/boletines/", directivo.boletines, name="boletines"),
    path("colegio/configuracion/", directivo.configuracion, name="configuracion"),
    path("colegio/periodos/<int:pk>/alternar/", directivo.alternar_periodo, name="alternar_periodo"),
    path("colegio/importar/", directivo.importar, name="importar"),
    path("colegio/importar/plantilla/", directivo.plantilla_importacion, name="plantilla_importacion"),
    path("colegio/estudiantes/<int:pk>/", directivo.ficha_estudiante, name="ficha_estudiante"),
    path("colegio/acudiencias/<int:pk>/retirar/", directivo.desvincular_acudiente, name="desvincular_acudiente"),
    path("colegio/usuarios/<int:pk>/clave/", directivo.restablecer_clave, name="restablecer_clave"),
    path("colegio/<slug:entidad>/", directivo.gestion_lista, name="gestion_lista"),
    path("colegio/<slug:entidad>/nuevo/", directivo.gestion_form, name="gestion_nuevo"),
    path("colegio/<slug:entidad>/<int:pk>/editar/", directivo.gestion_form, name="gestion_editar"),
    path("colegio/<slug:entidad>/<int:pk>/eliminar/", directivo.gestion_eliminar, name="gestion_eliminar"),

    # Docentes
    path("clases/", docente.mis_clases, name="mis_clases"),
    path("clases/<int:pk>/notas/", docente.planilla, name="clase_notas"),
    path("clases/<int:pk>/actividades/nueva/", docente.actividad_form, name="actividad_nueva"),
    path("clases/<int:pk>/actividades/<int:actividad_id>/", docente.actividad_form, name="actividad_editar"),
    path("clases/<int:pk>/actividades/<int:actividad_id>/eliminar/", docente.actividad_eliminar, name="actividad_eliminar"),
    path("clases/<int:pk>/tareas/", docente.tareas, name="clase_tareas"),
    path("clases/<int:pk>/tareas/nueva/", docente.tarea_form, name="tarea_nueva"),
    path("clases/<int:pk>/tareas/<int:tarea_id>/", docente.tarea_form, name="tarea_editar"),
    path("clases/<int:pk>/tareas/<int:tarea_id>/eliminar/", docente.tarea_eliminar, name="tarea_eliminar"),
    path("clases/<int:pk>/tareas/<int:tarea_id>/entregas/", docente.tarea_entregas, name="tarea_entregas"),
    path("clases/<int:pk>/asistencia/", docente.asistencia, name="clase_asistencia"),
    path("clases/<int:pk>/estudiantes/", docente.clase_estudiantes, name="clase_estudiantes"),
    path("observador/<int:matricula_id>/", docente.observador, name="observador"),
    path("direccion/<int:pk>/", docente.direccion_grupo, name="direccion_grupo"),

    # Familias
    path("familia/<int:pk>/", familia.resumen, name="familia_resumen"),
    path("familia/<int:pk>/notas/", familia.notas, name="familia_notas"),
    path("familia/<int:pk>/tareas/", familia.tareas, name="familia_tareas"),
    path("familia/<int:pk>/asistencia/", familia.asistencia, name="familia_asistencia"),
    path("familia/<int:pk>/observaciones/", familia.observaciones, name="familia_observaciones"),
    path("familia/<int:pk>/boletines/", familia.boletines, name="familia_boletines"),
]
