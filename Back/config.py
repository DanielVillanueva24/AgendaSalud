"""Configuracion de la aplicacion AgendaSalud."""
import os
from pathlib import Path

# Raiz del proyecto (carpeta que contiene Back/ y Front/)
BASE_DIR = Path(__file__).resolve().parent.parent
FRONT_DIR = BASE_DIR / "Front"
DB_PATH = BASE_DIR / "agendasalud.db"


def _bandera(nombre, defecto="1"):
    return os.environ.get(nombre, defecto).strip().lower() not in ("0", "false", "no", "")


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


class Config:
    # SECRET_KEY es el nombre que documenta el README; se mantiene el alias
    # AGENDASALUD_SECRET_KEY por compatibilidad con despliegues anteriores.
    SECRET_KEY = (
        os.environ.get("SECRET_KEY")
        or os.environ.get("AGENDASALUD_SECRET_KEY")
        or "dev-agendasalud-cap499-cambiar-en-produccion"
    )

    SQLALCHEMY_DATABASE_URI = uri_base_datos()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Sesiones (RNF1 - seguridad basica)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # 8 horas

    JSON_SORT_KEYS = False

    # Reglas de negocio por defecto
    DURACION_CITA_DEFECTO = 30      # minutos
    GRANULARIDAD_SLOT = 15          # minutos entre inicios candidatos
    ANTICIPACION_MINIMA_MIN = 0     # minutos minimos para agendar a futuro

    # --- Notificaciones por correo (Flask-Mail sobre el SMTP de Gmail) --------
    # MAIL_PASSWORD debe ser una *contrasena de aplicacion* de Gmail (16
    # caracteres, requiere verificacion en dos pasos). Nunca va en el codigo.
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USE_TLS = True
    MAIL_USE_SSL = False
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER") or os.environ.get("MAIL_USERNAME")

    # Sin credenciales la app funciona igual: el correo no se envia, se
    # escribe en el log. La agenda nunca depende de que el SMTP responda.
    NOTIFICACIONES_ACTIVAS = bool(MAIL_USERNAME and MAIL_PASSWORD)

    # --- Recordatorio diario (APScheduler) ------------------------------------
    RECORDATORIOS_ACTIVOS = _bandera("RECORDATORIOS_ACTIVOS")
    RECORDATORIO_HORA = int(os.environ.get("RECORDATORIO_HORA", "18"))
    RECORDATORIO_MINUTO = int(os.environ.get("RECORDATORIO_MINUTO", "0"))
