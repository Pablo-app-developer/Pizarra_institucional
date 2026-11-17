# Pizarra

Plataforma académica para colegios privados: notas, tareas, asistencia, observador,
comunicados y boletines en PDF, con acceso para directivos, docentes, acudientes y estudiantes.

Django 5.2, PostgreSQL (SQLite en local), plantillas del servidor y CSS propio. Sin frameworks de frontend.

## Probarla en su computador

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (en Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
python manage.py migrate
python manage.py datos_demo     # colegio de ejemplo con 58 estudiantes
python manage.py runserver
```

Abra http://127.0.0.1:8000. Contraseña de las cuentas de ejemplo: `pizarra2026`

Los usuarios de cada perfil están en la tabla de más abajo.

En Windows, los boletines en PDF necesitan GTK/Pango para WeasyPrint
(ver https://doc.courtbouillon.org/weasyprint/stable/first_steps.html). Con Docker no hace falta instalar nada:

```bash
docker build -t pizarra . && docker run -p 8000:8000 -e DEBUG=1 pizarra
```

## Qué hace cada perfil

| Perfil | Usuario de ejemplo | Acceso |
|---|---|---|
| Rectoría | 1001 | Todo, incluida la identidad del colegio (escudo y color), la escala y el equipo directivo |
| Coordinación | 1002 | Lo académico: docentes, grupos, plan de estudios, horarios, consolidados, boletines, cierre de periodos y de año |
| Secretaría | 1003 | Estudiantes, acudientes, matrícula, importación, boletines, comunicados y pensiones |
| Docente | 2001 | Notas, recuperaciones, tareas, asistencia, observador, logros de sus asignaturas y su horario |
| Docente de preescolar | 2007 | Lo mismo, con valoración descriptiva por dimensiones en lugar de notas |
| Acudiente | 3001 | Notas, tareas (y su envío), horario, asistencia, observaciones, boletines, pagos y avisos de sus hijos |
| Estudiante | (opcional) | Lo mismo que el acudiente, sin el estado de cuenta |
| Administrador de la plataforma | superusuario | `/superadmin/`: crea colegios y sus primeras cuentas |

## Módulos

- **Identidad:** escudo y color institucional en el menú, la pantalla de ingreso, los boletines y los recibos.
- **Plan de estudios y malla:** asignaturas e intensidad por grado, logros por periodo, malla imprimible. Los logros salen en el boletín.
- **Horarios:** jornada con bloques y descansos, generación automática sin cruces de docentes y edición por grupo con validación.
- **Notas:** planilla por actividades con pesos, recuperaciones con tope configurable, consolidado con puestos.
- **Preescolar:** grados con valoración descriptiva; el informe muestra textos en lugar de notas.
- **Tareas:** con registro de entregas o con envío por la plataforma (respuesta, enlace o archivo).
- **Avisos:** nota baja, falta, tarea sin entregar, tarea nueva, comunicado y pago. Quedan en la plataforma y, con correo configurado, llegan también por correo.
- **Pensiones:** cobro masivo por grado, pagos parciales, recibos, cartera y recordatorio a quienes están en mora.
- **Cierre de año:** propuesta de promoción editable y creación del año siguiente con grupos, clases y matrículas.

## Dar de alta un colegio real

```bash
python manage.py crear_colegio "Colegio San Pedro" --documento 63500111 --nombres "Ana" --apellidos "Ruiz"
```

Luego, ya dentro: Configuración (año, periodos, grados, asignaturas), Docentes, Grupos y clases, y por último
Estudiantes > Importar desde Excel.

## Publicarla sin costo (Render + Neon)

La base de datos gratuita de Render se borra a los 30 días, así que la base va en Neon, que no vence.

1. En https://neon.com cree un proyecto gratuito y copie la cadena de conexión (empieza por `postgresql://`).
2. En Render: New > Blueprint y elija este repositorio. Cuando pida `DATABASE_URL`, pegue la cadena de Neon.
3. Con la variable `CARGAR_DEMO=1` el colegio de ejemplo se crea solo en el primer arranque.
   Para un colegio real, quite esa variable y ejecute `python manage.py crear_colegio ...` desde la consola del servicio.
4. Si usa dominio propio, agréguelo en la variable `ALLOWED_HOSTS`.

El plan gratuito de Render apaga el servicio tras 15 minutos sin visitas y tarda cerca de un minuto en despertar.
Sirve para demostraciones; para un colegio en operación use el plan de pago (unos 7 USD al mes).

Variables de entorno: `SECRET_KEY` (obligatoria en producción), `DEBUG=0`, `DATABASE_URL`, `ALLOWED_HOSTS`.

### Correo (recuperar contraseña y avisos)

Sin estas variables la plataforma funciona, pero no envía correos: los avisos quedan solo en la plataforma
y las contraseñas las restablece el colegio.

`EMAIL_HOST`, `EMAIL_PORT` (587), `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` (1), `DEFAULT_FROM_EMAIL`.

### Archivos de las tareas

Los archivos que suben los estudiantes se guardan en la carpeta `media/` del servidor. En el plan gratuito de
Render ese disco se borra en cada despliegue o reinicio: para un colegio en operación use un disco persistente
o un almacenamiento externo. El escudo no tiene ese problema porque se guarda en la base de datos.

## Pruebas

```bash
python manage.py test
```

79 pruebas: cálculo de notas y recuperaciones, permisos por perfil y por colegio, planilla, importación, horarios sin cruces,
cierre de año, avisos, envío de tareas, pensiones, preescolar, escudo y apertura de todas las pantallas.

## Lo que todavía no incluye

- Avisos por WhatsApp automáticos (requieren un proveedor de WhatsApp Business, que tiene costo). Hoy el docente
  tiene un enlace para abrir el chat con el acudiente.
- Pagos en línea: las pensiones registran lo que el colegio recibe; no hay pasarela ni facturación electrónica.
- Almacenamiento externo para los archivos de tareas.
- Restricciones avanzadas en el generador de horarios (docentes de medio tiempo, salones especiales, bloques dobles).
- Promoción con comisiones de evaluación por actas; hoy la decisión se registra estudiante por estudiante.
