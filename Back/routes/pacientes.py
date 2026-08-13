"""CRUD de pacientes (RF2)."""
from flask import Blueprint, jsonify, request

from extensions import db
from models import Paciente, Cita
from security import login_requerido, rol_requerido
from utils import ErrorAPI, body, requerido, parse_fecha, parse_bool, parse_int, limpiar

bp = Blueprint("pacientes", __name__, url_prefix="/api/pacientes")


@bp.get("")
@login_requerido
def listar():
    q = Paciente.query

    buscar = limpiar(request.args.get("q"))
    if buscar:
        patron = f"%{buscar.lower()}%"
        q = q.filter(db.or_(
            db.func.lower(Paciente.nombre).like(patron),
            db.func.lower(Paciente.apellido).like(patron),
            db.func.lower(Paciente.documento).like(patron),
            db.func.lower(Paciente.email).like(patron),
            Paciente.telefono.like(patron),
        ))

    if not parse_bool(request.args.get("incluir_inactivos")):
        q = q.filter(Paciente.activo.is_(True))

    limite = parse_int(request.args.get("limite"), "limite", 200)
    pacientes = q.order_by(Paciente.apellido, Paciente.nombre).limit(limite).all()

    con_stats = parse_bool(request.args.get("con_estadisticas"))
    return jsonify({
        "pacientes": [p.to_dict(con_estadisticas=con_stats) for p in pacientes],
        "total": q.count(),
    })


@bp.get("/<int:pid>")
@login_requerido
def detalle(pid):
    paciente = _obtener(pid)
    data = paciente.to_dict(con_estadisticas=True)
    citas = (Cita.query.filter_by(paciente_id=pid)
             .order_by(Cita.inicio.desc()).limit(50).all())
    data["citas"] = [c.to_dict() for c in citas]
    return jsonify(data)


@bp.post("")
@rol_requerido("recepcion")          # README: el profesional solo consulta pacientes
def crear():
    data = body()
    requerido(data, "nombre", "apellido")

    documento = limpiar(data.get("documento"))
    if documento and Paciente.query.filter_by(documento=documento).first():
        raise ErrorAPI(f"Ya existe un paciente con el documento {documento}", 409)

    paciente = Paciente(
        nombre=limpiar(data["nombre"]),
        apellido=limpiar(data["apellido"]),
        documento=documento,
        telefono=limpiar(data.get("telefono")),
        email=limpiar(data.get("email")),
        fecha_nacimiento=parse_fecha(data.get("fecha_nacimiento"), "fecha_nacimiento"),
        notas=limpiar(data.get("notas")),
        activo=parse_bool(data.get("activo"), True),
    )
    db.session.add(paciente)
    db.session.commit()
    return jsonify(paciente.to_dict()), 201


@bp.put("/<int:pid>")
@rol_requerido("recepcion")          # README: el profesional solo consulta pacientes
def actualizar(pid):
    paciente = _obtener(pid)
    data = body()

    if "documento" in data:
        documento = limpiar(data["documento"])
        duplicado = Paciente.query.filter(
            Paciente.documento == documento, Paciente.id != pid
        ).first() if documento else None
        if duplicado:
            raise ErrorAPI(f"Ya existe otro paciente con el documento {documento}", 409)
        paciente.documento = documento

    for campo in ("nombre", "apellido", "telefono", "email", "notas"):
        if campo in data:
            setattr(paciente, campo, limpiar(data[campo]))
    if "fecha_nacimiento" in data:
        paciente.fecha_nacimiento = parse_fecha(data["fecha_nacimiento"], "fecha_nacimiento")
    if "activo" in data:
        paciente.activo = parse_bool(data["activo"], True)

    if not paciente.nombre or not paciente.apellido:
        raise ErrorAPI("Nombre y apellido no pueden quedar vacios")

    db.session.commit()
    return jsonify(paciente.to_dict())


@bp.delete("/<int:pid>")
@rol_requerido("recepcion")
def desactivar(pid):
    """Baja logica: conservamos el historial de citas para los indicadores (RF6)."""
    paciente = _obtener(pid)
    paciente.activo = False
    db.session.commit()
    return jsonify({"ok": True, "paciente": paciente.to_dict()})


def _obtener(pid) -> Paciente:
    paciente = db.session.get(Paciente, pid)
    if paciente is None:
        raise ErrorAPI("Paciente no encontrado", 404)
    return paciente
