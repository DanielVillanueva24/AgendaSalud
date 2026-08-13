"""Control de acceso por sesion y rol (RNF1)."""
from functools import wraps

from flask import session, jsonify, g

from extensions import db
from models import Usuario
from utils import ErrorAPI


def usuario_actual():
    """Usuario de la sesion activa, o None. Se cachea por request en `g`."""
    if "usuario" in g:
        return g.usuario

    uid = session.get("usuario_id")
    g.usuario = db_usuario(uid) if uid else None
    return g.usuario


def db_usuario(uid):
    u = db.session.get(Usuario, uid)
    return u if (u and u.activo) else None


def login_requerido(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if usuario_actual() is None:
            session.pop("usuario_id", None)
            return jsonify({"error": "No autenticado"}), 401
        return fn(*args, **kwargs)
    return wrapper


def rol_requerido(*roles):
    """Restringe el endpoint a los roles indicados. El rol `admin` siempre pasa."""
    def decorador(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            u = usuario_actual()
            if u is None:
                return jsonify({"error": "No autenticado"}), 401
            if u.rol != "admin" and u.rol not in roles:
                return jsonify({
                    "error": "No tienes permisos para esta accion",
                    "rol_requerido": list(roles),
                }), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorador


def es_admin():
    u = usuario_actual()
    return bool(u and u.rol == "admin")


def alcance_profesional_id():
    """
    Un usuario con rol `profesional` solo ve su propia agenda.
    Devuelve el id al que hay que acotar las consultas, o None si ve todo.
    """
    u = usuario_actual()
    if u and u.rol == "profesional":
        if not u.profesional_id:
            # Sin ficha vinculada no hay agenda propia que acotar. Devolver None
            # aqui significaria "ve todo", justo lo contrario de lo que el rol
            # pretende, asi que se falla cerrado.
            raise ErrorAPI(
                "Tu usuario tiene rol profesional pero no esta vinculado a una ficha "
                "profesional. Pide a un administrador que complete la vinculacion.",
                403,
            )
        return u.profesional_id
    return None
