"""Autenticacion por sesion (RF1 / RNF1)."""
from datetime import datetime

from flask import Blueprint, jsonify, session

from extensions import db
from models import Usuario
from security import login_requerido, usuario_actual
from utils import body, requerido, limpiar

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@bp.post("/login")
def login():
    data = body()
    requerido(data, "email", "password")

    email = limpiar(data["email"]).lower()
    usuario = Usuario.query.filter(db.func.lower(Usuario.email) == email).first()

    # Mensaje generico: no revela si el email existe.
    if usuario is None or not usuario.check_password(data["password"]):
        return jsonify({"error": "Email o contrasena incorrectos"}), 401
    if not usuario.activo:
        return jsonify({"error": "La cuenta esta desactivada. Contacta al administrador."}), 403

    session.clear()
    session["usuario_id"] = usuario.id
    session.permanent = True

    usuario.ultimo_acceso = datetime.now()
    db.session.commit()

    return jsonify({"usuario": _perfil(usuario)})


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.get("/sesion")
def sesion():
    """Permite al frontend saber si hay sesion activa al cargar la pagina."""
    usuario = usuario_actual()
    if usuario is None:
        return jsonify({"autenticado": False}), 200
    return jsonify({"autenticado": True, "usuario": _perfil(usuario)})


@bp.post("/password")
@login_requerido
def cambiar_password():
    data = body()
    requerido(data, "password_actual", "password_nueva")

    usuario = usuario_actual()
    if not usuario.check_password(data["password_actual"]):
        return jsonify({"error": "La contrasena actual no es correcta"}), 400
    if len(data["password_nueva"]) < 6:
        return jsonify({"error": "La contrasena nueva debe tener al menos 6 caracteres"}), 400

    usuario.set_password(data["password_nueva"])
    db.session.commit()
    return jsonify({"ok": True})


def _perfil(usuario: Usuario):
    data = usuario.to_dict()
    if usuario.profesional:
        data["profesional"] = usuario.profesional.to_dict(con_horarios=False)
    return data
