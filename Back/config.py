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
    # Hay dos vias de envio y se elige la primera disponible:
    #
    #   1. Brevo sobre HTTPS (BREVO_API_KEY). Es la que funciona en la nube:
    #      muchos planes gratuitos bloquean las conexiones salientes a los
    #      puertos de SMTP, y entonces Gmail falla con "Network is unreachable"
    #      sin que las credenciales tengan nada que ver.
    #   2. SMTP de Gmail (MAIL_USERNAME + MAIL_PASSWORD), util en local.
    #
    # MAIL_PASSWORD debe ser una *contrasena de aplicacion* de Gmail (16
    # caracteres, requiere verificacion en dos pasos). Nunca va en el codigo.
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USE_TLS = True
    MAIL_USE_SSL = False
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER") or os.environ.get("MAIL_USERNAME")

    # Remitente que ve el paciente. Con Brevo tiene que ser una direccion
    # verificada en la cuenta (Senders & IP > Senders).
    MAIL_SENDER_NAME = os.environ.get("MAIL_SENDER_NAME", "AgendaSalud")
    BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "").strip()

    # Sin credenciales la app funciona igual: el correo no se envia, se
    # escribe en el log. La agenda nunca depende de que el correo responda.
    NOTIFICACIONES_ACTIVAS = bool(
        (BREVO_API_KEY and MAIL_DEFAULT_SENDER) or (MAIL_USERNAME and MAIL_PASSWORD)
    )

    # --- Recordatorio diario (APScheduler) ------------------------------------
    RECORDATORIOS_ACTIVOS = _bandera("RECORDATORIOS_ACTIVOS")
    RECORDATORIO_HORA = int(os.environ.get("RECORDATORIO_HORA", "18"))
    RECORDATORIO_MINUTO = int(os.environ.get("RECORDATORIO_MINUTO", "0"))
