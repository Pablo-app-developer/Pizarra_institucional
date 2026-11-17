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

## Publicarla en Render

1. Suba este código a un repositorio de GitHub.
2. En Render: New > Blueprint y elija el repositorio. `render.yaml` crea la base PostgreSQL y el servicio web.
3. Agregue su dominio en la variable `ALLOWED_HOSTS` si usa uno propio.
4. En la consola del servicio: `python manage.py crear_colegio ...`

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
