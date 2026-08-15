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

Toda la configuración sensible vive en variables de entorno: **ningún valor real aparece en el código ni en el repositorio**. La lista completa, con descripción y sin valores, está en [`.env.example`](.env.example).

| Variable | Obligatoria | Para qué sirve |
|---|---|---|
| `DATABASE_URL` | En producción | Conexión a PostgreSQL. Si falta, la app usa SQLite local |
| `SECRET_KEY` | En producción | Firma las cookies de sesión |
| `MAIL_USERNAME` | Para enviar correo | Cuenta de Gmail que envía; es también el remitente |
| `MAIL_PASSWORD` | Para enviar correo | Contraseña **de aplicación** de Gmail (16 caracteres) |
| `MAIL_SENDER_NAME` | No | Nombre visible del remitente (`AgendaSalud` por defecto) |
| `RECORDATORIO_HORA` / `RECORDATORIO_MINUTO` | No | Hora de la tarea diaria (18:00 por defecto) |
| `RECORDATORIOS_ACTIVOS=0` | No | Desactiva la tarea programada |

Sin ninguna de ellas la aplicación arranca igual: SQLite local y correo en modo simulado.

**Uso:**

- **Local:** `cp .env.example .env` y rellenar. `.env` está en `.gitignore`.
- **Render:** Dashboard → el servicio → *Environment*.

> **Nunca** se pegan valores reales en el código, en el README, en un commit ni en un chat. La `DATABASE_URL` de Render y la contraseña de aplicación de Gmail son credenciales completas: quien las tenga entra a la base de datos y a la cuenta de correo. Si alguna llega a subirse por error, hay que rotarla (regenerar la contraseña de aplicación en Gmail, recrear la base en Render), no solo borrar el commit.
>
> Si `SECRET_KEY` no está definida en un despliegue con `DATABASE_URL`, la aplicación genera una clave aleatoria en cada arranque y lo avisa en el log: nunca cae en una clave fija publicada en el repositorio.

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
│   ├── migrar.py         # copia los datos de SQLite local a PostgreSQL (nube)
│   ├── test_agendasalud.py   # 84 pruebas automatizadas
│   ├── routes/           # blueprints: auth, usuarios, pacientes, profesionales, citas, indicadores
│   ├── Procfile          # arranque en producción: gunicorn
│   └── requirements.txt
├── Front/
│   ├── index.html        # aplicación de página única con FullCalendar
│   ├── css/ · js/        # interfaz y cliente de la API
│   └── vendor/           # FullCalendar y Chart.js incluidos (funciona sin internet)
├── .env.example          # plantilla de variables de entorno (sin valores)
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
python migrar.py --verificar          # compara la base local con la de la nube
```

La base local es `agendasalud.db` **en la raíz del proyecto** (no dentro de `Back/`): la ruta la define `DB_PATH` en `Back/config.py`.

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

El envío es **Flask-Mail sobre el SMTP de Gmail** (`smtp.gmail.com:587`, TLS), autenticado con las dos únicas variables que hacen falta:

| Variable | Valor |
|---|---|
| `MAIL_USERNAME` | La cuenta de Gmail, p. ej. `agendasaludcitas@gmail.com` |
| `MAIL_PASSWORD` | Su **contraseña de aplicación** de 16 caracteres |

La contraseña de aplicación se genera en *myaccount.google.com → Seguridad → Contraseñas de aplicaciones* y requiere tener activada la verificación en dos pasos. **No es la contraseña normal de la cuenta**: con esa, Gmail responde `535 Username and Password not accepted`.

El remitente es siempre `MAIL_USERNAME` y no se configura aparte, porque Gmail reescribe el `From` a la cuenta con la que te autenticaste; poner otra dirección solo daría la falsa impresión de que el correo sale de otro sitio. Lo único ajustable es el nombre visible, con `MAIL_SENDER_NAME`.

En local basta con escribirlas una vez en un archivo `.env` en la raíz del proyecto (lo excluye `.gitignore`, nunca se sube). La contraseña se puede pegar **tal cual la da Google, con espacios**: se limpian solos, porque con ellos el SMTP responde `535` y parece que la contraseña está mal cuando es la correcta.

```ini
MAIL_USERNAME=agendasaludcitas@gmail.com
MAIL_PASSWORD=abcd efgh ijkl mnop
```

**Probar la configuración** sin esperar a que haya una cita:

```bash
cd Back
python -m flask --app app probar-correo tu.correo@ejemplo.com
```

El comando dice contra qué servidor fue, con qué remitente y cuántos caracteres tiene la contraseña (deben ser 16). Para descartar de una vez si el problema son las credenciales, se pueden pasar sueltas sin tocar nada más:

```bash
python -m flask --app app probar-correo tu.correo@ejemplo.com \
  --usuario agendasaludcitas@gmail.com --password "abcd efgh ijkl mnop"
```

> ⚠️ Los planes gratuitos de Render (y de la mayoría de PaaS) **bloquean las conexiones salientes a los puertos de SMTP**. Si es el caso, el envío falla con `OSError: [Errno 101] Network is unreachable` al abrir el socket, aunque las credenciales sean correctas: el paquete no llega a salir de la máquina y no se arregla con configuración. En local funciona sin problema. Para la nube haría falta un plan de pago que permita SMTP saliente, o volver a una API de correo sobre HTTPS.

- `GET /api/salud` indica en `notificaciones` si el correo está configurado.
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

## Migrar los datos locales a la nube

La base PostgreSQL de Render nace vacía. `Back/migrar.py` copia a la nube lo que ya hay en la base SQLite local (profesionales, usuarios, pacientes, horarios y citas).

- **No duplica:** cada fila se identifica por una clave natural (email del usuario, documento del paciente, la terna profesional + paciente + hora de inicio de la cita), no por su `id`. Se puede ejecutar las veces que haga falta; lo que ya está, se salta.
- **No copia los `id`:** PostgreSQL asigna los suyos y el script traduce las claves foráneas sobre la marcha, así que las secuencias de la base destino quedan sanas.
- **Todo o nada:** la carga corre dentro de una única transacción.

```bash
cd Back
pip install psycopg2-binary                  # conector de PostgreSQL, si falta

export DATABASE_URL="<External Database URL de Render>"   # PowerShell: $env:DATABASE_URL="..."

python migrar.py --verificar                 # 1. recuento a ambos lados
python migrar.py --simular                   # 2. ensayo, no escribe nada
python migrar.py                             # 3. migración real
```

Opciones: `--sqlite RUTA` (otra base de origen) y `--destino URL` (destino sin usar `DATABASE_URL`).

Hay que usar la **External** Database URL de Render (la Internal solo funciona dentro de su red). El script imprime la URL con la contraseña censurada, para que se pueda pegar la salida en un informe sin filtrarla.

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

## Seguridad

- **Sin secretos en el repositorio.** Credenciales, cadenas de conexión y claves se leen del entorno; el repositorio solo guarda la plantilla `.env.example` con los nombres. `.gitignore` bloquea `.env`, `*.db`, `*.pem` y `*.key`.
- **Contraseñas hasheadas** con Werkzeug (`generate_password_hash`); nunca se guardan ni se devuelven en claro.
- **Sesiones** con cookie `HttpOnly` y `SameSite=Lax`, caducidad de 8 horas y clave de firma tomada del entorno.
- **Control de acceso por rol** en cada endpoint (`Back/security.py`), con principio de menor privilegio.
- **La base local `agendasalud.db` no se versiona:** contiene datos de pacientes y hashes de contraseñas.

## Convención de commits

Commits pequeños y con mensajes claros, p. ej.:
`git commit -m "Agrega notificaciones por correo"` · `git commit -m "Migra configuración a PostgreSQL"`
