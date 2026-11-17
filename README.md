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
python manage.py datos_demo     # colegio de ejemplo con 48 estudiantes
python manage.py runserver
```

Abra http://127.0.0.1:8000. Contraseña de las cuentas de ejemplo: `pizarra2026`

| Rol | Usuario |
|---|---|
| Rectoría | 1001 |
| Docente (Matemáticas, director de 6°A) | 2001 |
| Acudiente con dos hijos | 3001 |

En Windows, los boletines en PDF necesitan GTK/Pango para WeasyPrint
(ver https://doc.courtbouillon.org/weasyprint/stable/first_steps.html). Con Docker no hace falta instalar nada:

```bash
docker build -t pizarra . && docker run -p 8000:8000 -e DEBUG=1 pizarra
```

## Qué hace cada rol

**Directivos**: panel del colegio, estudiantes (uno a uno o por Excel), docentes, acudientes, grupos y clases,
consolidado de notas con puestos, boletines en PDF por estudiante o por grupo, comunicados, escala de valoración,
cierre de periodos.

**Docentes**: planilla de notas tipo hoja de cálculo con actividades y pesos, tareas con registro de entregas,
asistencia por clase, observador del estudiante, y observaciones de boletín si dirigen grupo.

**Acudientes y estudiantes**: resumen con alertas, notas por actividad, tareas pendientes y entregadas,
asistencia, observaciones y boletines de los periodos cerrados. Un acudiente con varios hijos los ve a todos.

## Reglas que conviene conocer

- El usuario de ingreso es el número de documento. Las cuentas nuevas reciben una contraseña temporal
  que se muestra una sola vez y debe cambiarse en el primer ingreso.
- Definitiva de una asignatura: promedio ponderado por el peso de cada actividad. Si todas pesan 0, promedio simple.
  Mientras falten actividades por calificar, se calcula sobre lo ya calificado.
- Nota final del año: promedio de los periodos según su peso.
- Cerrar un periodo bloquea los cambios de notas y publica el boletín a las familias. Se puede reabrir.
- Cada colegio solo ve su información; un mismo servidor atiende varios colegios.

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

## Pruebas

```bash
python manage.py test
```

34 pruebas: cálculo de notas, permisos por rol y por colegio, planilla, importación y apertura de todas las pantallas.

## Lo que todavía no incluye

- Recuperar contraseña por correo (hoy la restablece el colegio) y avisos por correo o WhatsApp.
- Entrega de tareas con archivos por parte del estudiante: el docente registra quién entregó.
- Recuperaciones o nivelaciones, logros descriptivos por asignatura y promoción de año automática.
- Horarios, pagos de pensión y matrícula en línea.
