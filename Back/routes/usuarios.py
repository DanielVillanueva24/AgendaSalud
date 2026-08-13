"""Administracion de usuarios y roles (RF1). Solo accesible por admin."""
import re

from flask import Blueprint, jsonify, request

from extensions import db
from models import Usuario, Profesional, Cita, ROLES
from security import rol_requerido, usuario_actual
from utils import ErrorAPI, body, requerido, parse_bool, parse_int, limpiar

bp = Blueprint("usuarios", __name__, url_prefix="/api/usuarios")

VINCULO_REQUERIDO = (
    "Un usuario con rol 'profesional' debe vincularse a una ficha profesional: "
    "sin ella no se puede acotar su agenda"
)


@bp.get("")
@rol_requerido("admin")
def listar():
    q = Usuario.query
    if not parse_bool(request.args.get("incluir_inactivos"), True):
        q = q.filter(Usuario.activo.is_(True))
    usuarios = q.order_by(Usuario.nombre).all()
    return jsonify({"usuarios": [u.to_dict() for u in usuarios], "roles": list(ROLES)})


@bp.post("")
@rol_requerido("admin")
def crear():
    data = body()
    requerido(data, "nombre", "email", "password", "rol")

    email = _validar_email(data["email"])
    if Usuario.query.filter(db.func.lower(Usuario.email) == email).first():
        raise ErrorAPI(f"Ya existe un usuario con el email {email}", 409)

    rol = _validar_rol(data["rol"])
    if len(data["password"]) < 6:
        raise ErrorAPI("La contrasena debe tener al menos 6 caracteres")

    usuario = Usuario(
        nombre=limpiar(data["nombre"]),
        email=email,
        rol=rol,
        activo=parse_bool(data.get("activo"), True),
        profesional_id=_validar_profesional(data.get("profesional_id"), rol),
    )
    usuario.set_password(data["password"])

    db.session.add(usuario)
    db.session.commit()
    return jsonify(usuario.to_dict()), 201


@bp.put("/<int:uid>")
@rol_requerido("admin")
def actualizar(uid):
    usuario = _obtener(uid)
    data = body()

    if "email" in data:
        email = _validar_email(data["email"])
        duplicado = Usuario.query.filter(
            db.func.lower(Usuario.email) == email, Usuario.id != uid
        ).first()
        if duplicado:
            raise ErrorAPI(f"Ya existe otro usuario con el email {email}", 409)
        usuario.email = email

    if "nombre" in data:
        usuario.nombre = limpiar(data["nombre"])
        if not usuario.nombre:
            raise ErrorAPI("El nombre no puede quedar vacio")

    if "rol" in data:
        nuevo_rol = _validar_rol(data["rol"])
        if usuario.id == usuario_actual().id and nuevo_rol != "admin":
            raise ErrorAPI("No puedes quitarte a ti mismo el rol de administrador", 409)
        usuario.rol = nuevo_rol

    if "profesional_id" in data:
        usuario.profesional_id = _validar_profesional(
            data["profesional_id"], usuario.rol, excluir_usuario_id=usuario.id
        )
    elif usuario.rol != "profesional":
        # Al dejar de ser profesional se suelta la ficha: un vinculo huerfano
        # acotaria consultas de un rol que ya no debe estar acotado.
        usuario.profesional_id = None

    # Invariante del que depende alcance_profesional_id(): rol profesional <=> ficha.
    # Cubre el caso de cambiar el rol a profesional sin mandar profesional_id.
    if usuario.rol == "profesional" and not usuario.profesional_id:
        raise ErrorAPI(VINCULO_REQUERIDO)

    if "activo" in data:
        activo = parse_bool(data["activo"], True)
        if not activo and usuario.id == usuario_actual().id:
            raise ErrorAPI("No puedes desactivar tu propia cuenta", 409)
        usuario.activo = activo

    if data.get("password"):
        if len(data["password"]) < 6:
            raise ErrorAPI("La contrasena debe tener al menos 6 caracteres")
        usuario.set_password(data["password"])

    _asegurar_admin_restante(usuario)
    db.session.commit()
    return jsonify(usuario.to_dict())


@bp.delete("/<int:uid>")
@rol_requerido("admin")
def desactivar(uid):
    """
    Por defecto da de baja logica y conserva el historial.
    Con ?definitivo=1 borra la fila de verdad.
    """
    usuario = _obtener(uid)
    if usuario.id == usuario_actual().id:
        raise ErrorAPI("No puedes dar de baja tu propia cuenta", 409)

    _asegurar_admin_restante(usuario)

    if not parse_bool(request.args.get("definitivo")):
        usuario.activo = False
        db.session.commit()
        return jsonify({"ok": True, "eliminado": False, "usuario": usuario.to_dict()})

    # Las citas que creo se conservan (alimentan los indicadores); solo se suelta
    # la referencia al autor para no dejar una clave foranea colgando.
    citas_creadas = Cita.query.filter_by(creada_por_id=usuario.id).update(
        {"creada_por_id": None}, synchronize_session=False
    )
    db.session.delete(usuario)
    db.session.commit()
    return jsonify({"ok": True, "eliminado": True, "citas_conservadas": citas_creadas})


def _validar_rol(rol):
    rol = limpiar(rol)
    if rol not in ROLES:
        raise ErrorAPI(f"Rol invalido: {rol}", 400, {"roles_validos": list(ROLES)})
    return rol


# Formato de correo: una arroba, dominio con punto y sin espacios ni arrobas extra.
# Deliberadamente laxo (no se valida el RFC 5322 completo), solo descarta erratas.
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


def _validar_email(valor):
    email = (limpiar(valor) or "").lower()
    if not email:
        raise ErrorAPI("El correo electronico es obligatorio")
    if not _RE_EMAIL.match(email):
        raise ErrorAPI(f"El correo '{valor}' no tiene un formato valido (ejemplo: nombre@dominio.com)")
    return email


def _validar_profesional(profesional_id, rol, excluir_usuario_id=None):
    pid = parse_int(profesional_id, "profesional_id")
    if pid is None:
        if rol == "profesional":
            raise ErrorAPI(VINCULO_REQUERIDO)
        return None
    if rol != "profesional":
        raise ErrorAPI("Solo un usuario con rol 'profesional' puede vincularse a una ficha profesional")
    if db.session.get(Profesional, pid) is None:
        raise ErrorAPI("Profesional no encontrado", 404)

    ocupado = Usuario.query.filter(Usuario.profesional_id == pid)
    if excluir_usuario_id:
        ocupado = ocupado.filter(Usuario.id != excluir_usuario_id)
    if ocupado.first():
        raise ErrorAPI("Ese profesional ya tiene un usuario vinculado", 409)
    return pid


def _asegurar_admin_restante(usuario):
    """Evita dejar el sistema sin ningun administrador activo."""
    if usuario.rol == "admin" and usuario.activo:
        return
    quedan = Usuario.query.filter(
        Usuario.rol == "admin", Usuario.activo.is_(True), Usuario.id != usuario.id
    ).count()
    if quedan == 0:
        raise ErrorAPI("Debe quedar al menos un administrador activo en el sistema", 409)


def _obtener(uid) -> Usuario:
    usuario = db.session.get(Usuario, uid)
    if usuario is None:
        raise ErrorAPI("Usuario no encontrado", 404)
    return usuario
