"""Configuracion de la aplicacion AgendaSalud.

Todos los datos sensibles (cadena de conexion, clave de firma, credenciales de
correo) se leen de variables de entorno. Ninguno se escribe en el codigo ni se
versiona: ver `.env.example` para la lista completa.
"""
import logging
import os
import secrets
from pathlib import Path

# Raiz del proyecto (carpeta que contiene Back/ y Front/)
BASE_DIR = Path(__file__).resolve().parent.parent
FRONT_DIR = BASE_DIR / "Front"
DB_PATH = BASE_DIR / "agendasalud.db"


def _cargar_env():
    """
    Lee el archivo .env de la raiz, si existe, antes que nada mas.

    En local es la forma comoda de tener las credenciales: se escriben una vez
    en .env (que .gitignore excluye) y valen para el servidor, para las pruebas
    y para los comandos de consola, sin tener que exportar variables en cada
    terminal ni recordar la sintaxis de PowerShell. En la nube no hay .env y
    mandan las variables del propio servicio.

    Lo que ya venga en el entorno tiene prioridad: `override=False` evita que un
    .env olvidado en el disco pise la configuracion real del despliegue.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:                       # pragma: no cover
        return                                # sin el paquete, solo variables de entorno

    for ruta in (BASE_DIR / ".env", Path(__file__).resolve().parent / ".env"):
        if ruta.is_file():
            load_dotenv(ruta, override=False)


_cargar_env()


def _bandera(nombre, defecto="1"):
    return os.environ.get(nombre, defecto).strip().lower() not in ("0", "false", "no", "")


def opciones_motor():
    """
    Ajustes del pool de conexiones, distintos segun el motor.

    Con SQLite (local) las conexiones se comparten entre los hilos del servidor:
    hay que desactivar `check_same_thread`, que por defecto hace que una conexion
    solo sirva al hilo que la abrio, y dar margen para esperar a que se libere un
    bloqueo de escritura en vez de fallar en el acto. Las PRAGMA que completan
    esto (WAL y busy_timeout) se aplican en extensions.py.

    Con PostgreSQL (nube) se reciclan las conexiones antes de que el servidor las
    corte por inactividad.
    """
    opciones = {"pool_pre_ping": True}
    if uri_base_datos().startswith("sqlite"):
        opciones["connect_args"] = {"check_same_thread": False, "timeout": 15}
    else:
        opciones["pool_recycle"] = 280
        opciones["pool_size"] = 5
        opciones["max_overflow"] = 5
    return opciones


def uri_base_datos():
    """
    Cadena de conexion: PostgreSQL si hay DATABASE_URL, SQLite en local si no.

    Render y Heroku entregan la URL con el esquema antiguo `postgres://`, que
    SQLAlchemy 2 ya no reconoce; hay que reescribirlo a `postgresql://`.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return f"sqlite:///{DB_PATH.as_posix()}"
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def clave_secreta():
    """
    Clave con la que se firman las cookies de sesion.

    En un despliegue real tiene que venir del entorno. Si falta, se genera una
    aleatoria en cada arranque en vez de recurrir a un valor fijo escrito en el
    repositorio: quien lea el codigo publico no puede falsificar sesiones. El
    precio es que reiniciar el servicio cierra las sesiones abiertas, que es
    justamente la senal de que falta definir SECRET_KEY en la plataforma.

    En local (sin DATABASE_URL) se usa una clave de desarrollo estable para no
    tener que volver a entrar despues de cada recarga del servidor.
    """
    # AGENDASALUD_SECRET_KEY es un alias antiguo; se mantiene por compatibilidad
    clave = os.environ.get("SECRET_KEY") or os.environ.get("AGENDASALUD_SECRET_KEY")
    if clave:
        return clave

    if os.environ.get("DATABASE_URL", "").strip():
        logging.getLogger("agendasalud").warning(
            "SECRET_KEY no esta definida: se usa una clave temporal distinta en cada "
            "arranque. Definela en las variables de entorno del servicio."
        )
        return secrets.token_hex(32)

    return "dev-local-agendasalud"


class Config:
    SECRET_KEY = clave_secreta()

    SQLALCHEMY_DATABASE_URI = uri_base_datos()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = opciones_motor()

    # Sesiones (RNF1 - seguridad basica)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # 8 horas

    JSON_SORT_KEYS = False

    # Reglas de negocio por defecto
    DURACION_CITA_DEFECTO = 30      # minutos
    GRANULARIDAD_SLOT = 15          # minutos entre inicios candidatos
    ANTICIPACION_MINIMA_MIN = 0     # minutos minimos para agendar a futuro

    # --- Notificaciones por correo -------------------------------------------
    # Unica via de envio: SMTP de Gmail con Flask-Mail.
    #
    # MAIL_USERNAME es la cuenta de Gmail que envia (p. ej.
    # agendasaludcitas@gmail.com) y MAIL_PASSWORD su *contrasena de aplicacion*
    # de 16 caracteres, no la contrasena normal: requiere tener activada la
    # verificacion en dos pasos en la cuenta. Ninguna de las dos va en el codigo.
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USE_TLS = True
    MAIL_USE_SSL = False
    MAIL_USERNAME = (os.environ.get("MAIL_USERNAME") or "").strip() or None

    # Google muestra la contrasena de aplicacion en cuatro bloques separados por
    # espacios ("abcd efgh ijkl mnop") y al copiarla se pegan tal cual, pero el
    # SMTP la quiere de corrido: con espacios responde 535 y parece que la
    # contrasena esta mal cuando en realidad es la correcta. Se limpian aqui
    # todos los espacios (normales y no separables, que es lo que copia el
    # navegador) en vez de exigir que el usuario acierte con el formato.
    MAIL_PASSWORD = "".join((os.environ.get("MAIL_PASSWORD") or "").split()) or None

    # El remitente es siempre la cuenta autenticada. No se deja configurar
    # aparte porque Gmail reescribe el From al usuario con el que se autentico:
    # poner otra direccion no la cambiaria, solo daria la falsa impresion de que
    # el correo sale de otro sitio.
    MAIL_DEFAULT_SENDER = MAIL_USERNAME

    # Nombre visible junto a la direccion en la bandeja del paciente
    MAIL_SENDER_NAME = os.environ.get("MAIL_SENDER_NAME", "AgendaSalud")

    # Sin credenciales la app funciona igual: el correo no se envia, se escribe
    # en el log. La agenda nunca depende de que el correo responda.
    NOTIFICACIONES_ACTIVAS = bool(MAIL_USERNAME and MAIL_PASSWORD)

    # --- Recordatorio diario (APScheduler) ------------------------------------
    RECORDATORIOS_ACTIVOS = _bandera("RECORDATORIOS_ACTIVOS")
    RECORDATORIO_HORA = int(os.environ.get("RECORDATORIO_HORA", "18"))
    RECORDATORIO_MINUTO = int(os.environ.get("RECORDATORIO_MINUTO", "0"))
