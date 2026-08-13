# AgendaSalud

Sistema web para gestionar el agendamiento de citas en consultorios pequeños: reserva en calendario, optimización de huecos, control de estados de cita, notificaciones automáticas por correo y panel de indicadores de ausentismo.

## Stack

- **Backend:** Python 3 + Flask (API REST) + Gunicorn (producción)
- **Base de datos:** PostgreSQL (producción/nube) · SQLite (desarrollo local) mediante SQLAlchemy (ORM)
- **Frontend:** JavaScript + HTML + CSS, con FullCalendar (calendario) y Chart.js (gráficos)
- **Notificaciones:** Flask-Mail sobre Gmail (SMTP) + APScheduler (recordatorios programados)
- **Auth:** sesiones de Flask + hash de contraseñas (Werkzeug)
- **Despliegue:** Render (o Railway)

> Gracias al ORM, cambiar de SQLite a PostgreSQL solo requiere ajustar la cadena de conexión (DATABASE_URL); el resto del código no cambia.

## Variables de entorno

Nunca se escriben en el código ni se suben a GitHub. Se configuran localmente o en el panel de la plataforma de despliegue.

```
DATABASE_URL=postgresql://usuario:clave@host:5432/agendasalud   # en local puede omitirse (usa SQLite)
SECRET_KEY=una-clave-larga-y-aleatoria
MAIL_USERNAME=citas.tuconsultorio@gmail.com
MAIL_PASSWORD=xxxxxxxxxxxxxxxx                                   # contraseña de aplicación de Gmail (16 caracteres)
```

Opcionales: `RECORDATORIO_HORA` / `RECORDATORIO_MINUTO` (hora de la tarea diaria, 18:00 por defecto), `RECORDATORIOS_ACTIVOS=0` (desactiva la tarea) y `MAIL_DEFAULT_SENDER` (remitente si difiere de `MAIL_USERNAME`). Sin ninguna de ellas la aplicación arranca con SQLite y el correo en modo simulado.

## Estructura del proyecto

```
AgendaSalud/
├── Back/
│   ├── app.py            # punto de entrada: crea la app, sirve la API y el frontend
│   ├── config.py         # configuración por entorno (base de datos, correo, sesiones)
│   ├── extensions.py     # instancia compartida de SQLAlchemy
│   ├── models.py         # modelos SQLAlchemy (Usuario, Profesional, Paciente, Cita, Horario)
│   ├── scheduler.py      # algoritmo greedy (sugerir_hueco) y reacomodo de la agenda
│   ├── notifications.py  # envío de correos (Flask-Mail) y recordatorios (APScheduler)
│   ├── security.py       # sesión, decoradores de rol y acotamiento por agenda
│   ├── utils.py          # ErrorAPI y validación de entradas
│   ├── seed.py           # datos de demostración
│   ├── test_agendasalud.py   # 84 pruebas automatizadas
│   ├── routes/           # blueprints: auth, usuarios, pacientes, profesionales, citas, indicadores
│   ├── Procfile          # arranque en producción: gunicorn
│   └── requirements.txt
├── Front/
│   ├── index.html        # aplicación de página única con FullCalendar
│   ├── css/ · js/        # interfaz y cliente de la API
│   └── vendor/           # FullCalendar y Chart.js incluidos (funciona sin internet)
├── .gitignore
└── README.md
```

> Las carpetas se llaman `Back/` y `Front/` (no `backend/` y `frontend/`): es el nombre que quedó en el repositorio.

## Cómo ejecutar (local)

```bash
cd Back
python -m venv venv
source venv/bin/activate      # Windows (Git Bash): source venv/Scripts/activate
pip install -r requirements.txt
python app.py                 # sin DATABASE_URL usa SQLite local
```

Abrir **http://127.0.0.1:5000**. Flask sirve la API y el frontend desde el mismo origen, así que no hay que abrir `index.html` por separado ni configurar CORS.

Comandos auxiliares:

```bash
python seed.py                        # carga datos de demostración
flask --app app init-db               # crea las tablas vacías
flask --app app recordatorios         # envía ahora los recordatorios de mañana
python test_agendasalud.py            # 84 pruebas
```

## Modelo de datos

| Entidad | Campos | Notas |
|---|---|---|
| **Usuario** | id, correo, hash_contrasena, rol | rol: `recepcion` \| `profesional` \| `administrador` |
| **Profesional** | id, nombre, especialidad, duracion_cita_min | duración por defecto de sus consultas (tiempos variables) |
| **Paciente** | id, nombre, telefono, **email** | el email es necesario para las notificaciones |
| **Cita** | id, profesional_id (FK), paciente_id (FK), inicio, duracion_min, estado | estado: `pendiente` \| `confirmada` \| `cancelada` \| `atendida` \| `no_asistio` |
| **Horario** | id, profesional_id (FK), dia, hora_inicio, hora_fin | franjas de atención (base del scheduler) |

## Endpoints de la API

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/citas` | Crea una cita (envía correo de confirmación) |
| PATCH | `/api/citas/<id>/estado` | Cambia el estado de una cita |
| GET | `/api/citas` | Lista citas (para el calendario) |
| GET | `/api/indicadores/ausentismo` | Total, no_asistio y tasa de ausentismo (%) |

La API real es más amplia: incluye el CRUD de pacientes, profesionales, usuarios y horarios, el login por sesión (`/api/auth/*`), las sugerencias del motor (`/api/citas/sugerencias`), el reacomodo del día (`/api/citas/reacomodo`), los recordatorios (`/api/citas/<id>/recordatorio`, `/api/citas/por-confirmar`) y varios indicadores (`/api/indicadores/resumen`, `serie-ausentismo`, `por-profesional`, `patrones`, `pacientes-riesgo`).

**Estado:** todo lo anterior está implementado. El correo de confirmación sale al crear y al confirmar una cita; el recordatorio, desde la tarea diaria o a mano desde la ficha de la cita.

## Algoritmo de optimización (evolución solicitada)

Versión actual: `sugerir_hueco()` recorre las franjas del profesional y asigna el primer hueco válido que no se solape, minimizando el tiempo muerto.

Evolución pedida en la retroalimentación:
- **Tiempos variables:** que la duración dependa del profesional y del tipo de consulta (campo `duracion_cita_min` por profesional), en lugar de un valor fijo.
- **Historial del paciente:** usar el porcentaje de ausentismo de cada paciente para priorizar recordatorios y, opcionalmente, aplicar sobre-agenda controlada en horarios con pacientes de alto riesgo de inasistencia.

## Notificaciones automáticas (Gmail)

- Se usa **Flask-Mail** sobre el SMTP de Gmail (`smtp.gmail.com`, puerto 587, TLS).
- Autenticación con una **contraseña de aplicación** de Gmail (requiere verificación en dos pasos), guardada en `MAIL_PASSWORD`.
- **Correo de confirmación:** al crear o confirmar una cita.
- **Recordatorio automático:** una tarea diaria con **APScheduler** (18:00 por defecto, ajustable con `RECORDATORIO_HORA`) busca las citas del día siguiente y envía un correo a cada paciente. Se puede lanzar a mano con `flask --app app recordatorios`.
- **Prioridad por historial:** los recordatorios se recorren de mayor a menor tasa de ausentismo del paciente, de modo que quien más ha faltado se contacta primero.
- **Modo simulado:** sin `MAIL_USERNAME` / `MAIL_PASSWORD` la aplicación funciona igual y los correos se escriben en el log en vez de enviarse. Un fallo de SMTP nunca tumba la petición que agendó la cita ni se pierde la cita.
- Futuro: añadir canales de WhatsApp (Twilio) y Telegram.

## Despliegue en la nube (Render)

1. Subir el proyecto a GitHub.
2. En Render: **New → Web Service** y conectar el repositorio.
3. Build y arranque. El backend vive en `Back/`, no en la raíz, así que hay dos formas válidas:

   | | Root Directory | Build Command | Start Command |
   |---|---|---|---|
   | **A (recomendada)** | `Back` | `pip install -r requirements.txt` | `gunicorn --bind 0.0.0.0:$PORT app:app` |
   | **B (sin tocar Root Directory)** | *(vacío)* | `pip install -r requirements.txt` | `gunicorn --chdir Back --bind 0.0.0.0:$PORT app:app` |

   La opción B funciona porque en la raíz hay un `requirements.txt` que reenvía a `Back/requirements.txt` y un `Procfile` que entra en `Back/`.

   El `--bind` importa: por defecto gunicorn escucha en `127.0.0.1:8000`, donde la plataforma no puede alcanzarlo, y el deploy se queda colgado en el health check.
4. **New → PostgreSQL** para crear la base gratuita; copiar su `DATABASE_URL`.
5. En el Web Service, agregar las variables de entorno (`DATABASE_URL`, `SECRET_KEY`, `MAIL_USERNAME`, `MAIL_PASSWORD`).
6. Nota de código: si `DATABASE_URL` empieza con `postgres://`, reemplazarlo por `postgresql://` para SQLAlchemy.

Render entrega una URL con HTTPS automático. Los datos persisten en PostgreSQL (no en el contenedor).

## Roles y permisos

El campo `rol` de `Usuario` define el acceso (principio de menor privilegio):

| Función | Recepción | Profesional | Administrador |
|---|---|---|---|
| Uso principal | Operar la agenda día a día | Ver su propia agenda | Controlar todo el sistema |
| Pacientes | Crear y editar | Consultar | Acceso total |
| Citas | Agendar/reprogramar/cancelar (todas) | Ver solo las suyas | Acceso total |
| Estados / asistencia | Confirmada, atendida, no asistió | Atendida / no asistió | Acceso total |
| Profesionales | Solo consultar | Solo su perfil | Crear, editar, eliminar |
| Indicadores | Ocupación de la agenda | Sus propias métricas | Global |
| Usuarios / configuración | Sin acceso | Sin acceso | Gestiona usuarios y roles |

## Convención de commits

Commits pequeños y con mensajes claros, p. ej.:
`git commit -m "Agrega notificaciones por correo"` · `git commit -m "Migra configuración a PostgreSQL"`
