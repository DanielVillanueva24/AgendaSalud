"""Blueprints de la API REST de AgendaSalud."""
from routes.auth import bp as auth_bp
from routes.pacientes import bp as pacientes_bp
from routes.profesionales import bp as profesionales_bp
from routes.citas import bp as citas_bp
from routes.indicadores import bp as indicadores_bp
from routes.usuarios import bp as usuarios_bp

BLUEPRINTS = (
    auth_bp,
    usuarios_bp,
    pacientes_bp,
    profesionales_bp,
    citas_bp,
    indicadores_bp,
)


def registrar_blueprints(app):
    for bp in BLUEPRINTS:
        app.register_blueprint(bp)
