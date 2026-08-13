"""
AgendaSalud - punto de entrada de la aplicacion Flask.

Sirve la API REST bajo /api y el frontend estatico de la carpeta Front/,
de modo que todo corre en un unico origen (sin CORS) y basta con abrir
http://127.0.0.1:5000 despues de `python app.py`.
"""
import logging
import os
import sys
from pathlib import Path

# Permite ejecutar `python app.py` desde cualquier carpeta
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import Flask, jsonify, send_from_directory, request  # noqa: E402
from werkzeug.exceptions import HTTPException  # noqa: E402

from config import Config, FRONT_DIR, DB_PATH  # noqa: E402
from extensions import db  # noqa: E402
from utils import ErrorAPI  # noqa: E402


def _configurar_logging(app):
    """Deja ver en consola los avisos de notificaciones (nivel INFO)."""
    if app.config.get("TESTING") or logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def create_app(config_object=Config):
    app = Flask(__name__, static_folder=None)
    app.config.from_object(config_object)
    _configurar_logging(app)

    db.init_app(app)

    import models  # noqa: F401  (registra los modelos en el metadata)
    from routes import registrar_blueprints
    registrar_blueprints(app)

    import notifications
    notifications.init_app(app)

    _registrar_manejadores_error(app)
    _registrar_frontend(app)
    _registrar_cli(app)

    with app.app_context():
        db.create_all()
        _diagnostico_arranque(app)

    return app


def _diagnostico_arranque(app):
    """
    Deja en el log contra que base de datos quedo conectada la aplicacion.

    Sin esto, una plataforma mal configurada arranca sin ninguna queja: si falta
    DATABASE_URL la app cae al SQLite del contenedor, que esta vacio y se pierde
    en cada despliegue. Todo parece correcto hasta que el login responde 401
    porque no hay ni un usuario contra el que validar. Nunca se escribe la
    cadena de conexion completa: lleva la contrasena dentro.
    """
    if app.config.get("TESTING"):
        return

    from models import Cita, Usuario

    try:
        motor = db.engine.name
        nombre = db.engine.url.database
        usuarios = db.session.query(Usuario).count()
        citas = db.session.query(Cita).count()
    except Exception:
        app.logger.exception("No se pudo consultar la base de datos al arrancar")
        return

    app.logger.info("Base de datos: %s (%s) | usuarios: %d | citas: %d",
                    motor, nombre, usuarios, citas)

    if motor == "sqlite" and os.environ.get("RENDER"):
        app.logger.error(
            "DATABASE_URL no esta definida en este servicio: se esta usando un SQLite "
            "dentro del contenedor, que se borra en cada despliegue. Definela en "
            "Render > el Web Service > Environment (no basta con que exista la base de datos)."
        )
    elif usuarios == 0:
        app.logger.warning(
            "La base no tiene ningun usuario: el login respondera 401 con "
            "'Email o contrasena incorrectos'. Falta cargar los datos (migrar.py o seed.py)."
        )


def _registrar_manejadores_error(app):
    @app.errorhandler(ErrorAPI)
    def _error_api(err):
        db.session.rollback()
        return err.respuesta()

    @app.errorhandler(HTTPException)
    def _error_http(err):
        if request.path.startswith("/api/"):
            return jsonify({"error": err.description, "codigo": err.code}), err.code
        return err

    @app.errorhandler(Exception)
    def _error_inesperado(err):
        db.session.rollback()
        app.logger.exception("Error no controlado en %s", request.path)
        if request.path.startswith("/api/"):
            return jsonify({"error": "Error interno del servidor"}), 500
        raise err

    @app.get("/api/salud")
    def salud():
        from models import Cita

        # Solo el motor y el nombre de la base, nunca la cadena de conexion
        # completa: esa lleva usuario y contrasena dentro.
        motor = db.engine.name          # "sqlite" en local, "postgresql" en la nube
        return jsonify({
            "servicio": "AgendaSalud API",
            "estado": "ok",
            "motor": motor,
            "base_datos": DB_PATH.name if motor == "sqlite" else db.engine.url.database,
            "citas": db.session.query(Cita).count(),
            # Solo si hay credenciales de correo cargadas, nunca cuales son:
            # sin esto la unica forma de saberlo es leer los logs del servidor.
            "notificaciones": bool(app.config.get("NOTIFICACIONES_ACTIVAS")),
        })


def _registrar_frontend(app):
    """Sirve Front/ como aplicacion de pagina unica."""

    @app.get("/")
    def index():
        return send_from_directory(FRONT_DIR, "index.html")

    @app.get("/<path:recurso>")
    def estaticos(recurso):
        destino = (FRONT_DIR / recurso)
        if destino.is_file():
            return send_from_directory(FRONT_DIR, recurso)
        # Rutas desconocidas vuelven al shell del SPA
        return send_from_directory(FRONT_DIR, "index.html")


def _registrar_cli(app):
    @app.cli.command("init-db")
    def init_db():
        """Crea las tablas sin cargar datos."""
        db.create_all()
        print(f"Base de datos lista en {DB_PATH}")

    @app.cli.command("seed")
    def seed_cmd():
        """Carga datos de demostracion."""
        from seed import poblar
        poblar()

    @app.cli.command("recordatorios")
    def recordatorios_cmd():
        """Envia ahora los recordatorios de las citas de manana (sin esperar a la tarea diaria)."""
        import notifications
        resumen = notifications.enviar_recordatorios(app)
        print(f"Recordatorios para {resumen['fecha']}:")
        for clave in ("citas", "enviados", "simulados", "errores", "sin_correo", "ya_avisadas"):
            print(f"  {clave:<12}: {resumen[clave]}")
        if resumen["simulados"]:
            print("\n  Modo simulado: define MAIL_USERNAME y MAIL_PASSWORD para enviar de verdad.")


app = create_app()

# La tarea diaria solo debe correr en el proceso que realmente sirve la app:
#   - servidor de desarrollo: el recargador levanta dos procesos y solo el
#     hijo (WERKZEUG_RUN_MAIN) se queda con ella, o cada recordatorio saldria
#     por duplicado;
#   - produccion: gunicorn se anuncia en SERVER_SOFTWARE.
# Importar este modulo desde las pruebas o desde `flask seed` no arranca nada.
_ES_HIJO_DEL_RECARGADOR = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
_BAJO_GUNICORN = os.environ.get("SERVER_SOFTWARE", "").startswith("gunicorn")

if _ES_HIJO_DEL_RECARGADOR or _BAJO_GUNICORN:
    import notifications
    notifications.iniciar_programador(app)


if __name__ == "__main__":
    print("=" * 62)
    print("  AgendaSalud - servidor de desarrollo")
    print(f"  Base de datos : {DB_PATH}")
    print(f"  Frontend      : {FRONT_DIR}")
    print("  URL           : http://127.0.0.1:5000")
    print("=" * 62)
    app.run(host="127.0.0.1", port=5000, debug=True)
