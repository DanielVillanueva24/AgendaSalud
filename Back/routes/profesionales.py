"""CRUD de profesionales y sus horarios de atencion (RF2, base de RF4)."""
from datetime import date, timedelta

from flask import Blueprint, jsonify, request

from extensions import db
from models import Profesional, HorarioAtencion, DIAS_SEMANA
from security import login_requerido, rol_requerido, alcance_profesional_id
from utils import ErrorAPI, body, requerido, parse_bool, parse_int, parse_hora, parse_fecha, limpiar
import scheduler

bp = Blueprint("profesionales", __name__, url_prefix="/api/profesionales")


@bp.get("")
@login_requerido
def listar():
    q = Profesional.query
    if not parse_bool(request.args.get("incluir_inactivos")):
        q = q.filter(Profesional.activo.is_(True))
    # README: el profesional solo ve su propio perfil
    alcance = alcance_profesional_id()
    if alcance:
        q = q.filter(Profesional.id == alcance)
    profesionales = q.order_by(Profesional.apellido, Profesional.nombre).all()
    return jsonify({"profesionales": [p.to_dict() for p in profesionales]})


@bp.get("/<int:pid>")
@login_requerido
def detalle(pid):
    _verificar_alcance(pid)
    return jsonify(_obtener(pid).to_dict())


@bp.post("")
@rol_requerido("admin")
def crear():
    data = body()
    requerido(data, "nombre", "apellido")

    profesional = Profesional(
        nombre=limpiar(data["nombre"]),
        apellido=limpiar(data["apellido"]),
        especialidad=limpiar(data.get("especialidad")),
        email=limpiar(data.get("email")),
        telefono=limpiar(data.get("telefono")),
        duracion_cita_min=parse_int(data.get("duracion_cita_min"), "duracion_cita_min", 30),
        color=limpiar(data.get("color")) or "#2563eb",
        activo=parse_bool(data.get("activo"), True),
    )
    if profesional.duracion_cita_min <= 0:
        raise ErrorAPI("La duracion de la cita debe ser mayor a 0 minutos")

    db.session.add(profesional)
    db.session.flush()
    _reemplazar_horarios(profesional, data.get("horarios"))
    db.session.commit()
    return jsonify(profesional.to_dict()), 201


@bp.put("/<int:pid>")
@rol_requerido("admin")
def actualizar(pid):
    profesional = _obtener(pid)
    data = body()

    for campo in ("nombre", "apellido", "especialidad", "email", "telefono", "color"):
        if campo in data:
            setattr(profesional, campo, limpiar(data[campo]))
    if "duracion_cita_min" in data:
        duracion = parse_int(data["duracion_cita_min"], "duracion_cita_min", 30)
        if duracion <= 0:
            raise ErrorAPI("La duracion de la cita debe ser mayor a 0 minutos")
        profesional.duracion_cita_min = duracion
    if "activo" in data:
        profesional.activo = parse_bool(data["activo"], True)
    if "horarios" in data:
        _reemplazar_horarios(profesional, data["horarios"])

    if not profesional.nombre or not profesional.apellido:
        raise ErrorAPI("Nombre y apellido no pueden quedar vacios")
    profesional.color = profesional.color or "#2563eb"

    db.session.commit()
    return jsonify(profesional.to_dict())


@bp.delete("/<int:pid>")
@rol_requerido("admin")
def desactivar(pid):
    profesional = _obtener(pid)
    profesional.activo = False
    db.session.commit()
    return jsonify({"ok": True, "profesional": profesional.to_dict()})


# --- Horarios y disponibilidad -----------------------------------------------

@bp.get("/<int:pid>/horarios")
@login_requerido
def horarios(pid):
    _verificar_alcance(pid)
    return jsonify({"horarios": [h.to_dict() for h in _obtener(pid).horarios]})


@bp.put("/<int:pid>/horarios")
@rol_requerido("admin")
def guardar_horarios(pid):
    profesional = _obtener(pid)
    data = body()
    _reemplazar_horarios(profesional, data.get("horarios", []))
    db.session.commit()
    return jsonify({"horarios": [h.to_dict() for h in profesional.horarios]})


@bp.get("/<int:pid>/disponibilidad")
@login_requerido
def disponibilidad(pid):
    """Slots libres de un dia concreto (?fecha=YYYY-MM-DD&duracion=30)."""
    _verificar_alcance(pid)
    profesional = _obtener(pid)
    fecha = parse_fecha(request.args.get("fecha"), "fecha") or date.today()
    duracion = parse_int(request.args.get("duracion"), "duracion", profesional.duracion_cita_min)

    return jsonify({
        "fecha": fecha.isoformat(),
        "dia": DIAS_SEMANA[fecha.weekday()],
        "duracion_min": duracion,
        "franjas": [
            {"inicio": i.isoformat(), "fin": f.isoformat()}
            for i, f in scheduler.franjas_de_atencion(profesional, fecha)
        ],
        "huecos": [
            {"inicio": i.isoformat(), "fin": f.isoformat(),
             "minutos": int((f - i).total_seconds() // 60)}
            for i, f, _pi, _pd in scheduler.huecos_libres(profesional, fecha)
        ],
        "slots": scheduler.slots_del_dia(profesional, fecha, duracion),
    })


def _reemplazar_horarios(profesional: Profesional, horarios):
    """Sustituye por completo la grilla semanal del profesional."""
    if horarios is None:
        return
    if not isinstance(horarios, list):
        raise ErrorAPI("'horarios' debe ser una lista")

    nuevos = []
    for i, h in enumerate(horarios):
        if not isinstance(h, dict):
            raise ErrorAPI(f"Horario #{i + 1} invalido")
        dia = parse_int(h.get("dia_semana"), "dia_semana")
        if dia is None or not 0 <= dia <= 6:
            raise ErrorAPI(f"Horario #{i + 1}: 'dia_semana' debe estar entre 0 (lunes) y 6 (domingo)")
        inicio = parse_hora(h.get("hora_inicio"), "hora_inicio")
        fin = parse_hora(h.get("hora_fin"), "hora_fin")
        if inicio >= fin:
            raise ErrorAPI(
                f"Horario del {DIAS_SEMANA[dia]}: la hora de inicio debe ser anterior a la de fin"
            )
        nuevos.append((dia, inicio, fin))

    # Rechaza franjas solapadas dentro del mismo dia
    for dia in range(7):
        del_dia = sorted([(i, f) for d, i, f in nuevos if d == dia])
        for anterior, siguiente in zip(del_dia, del_dia[1:]):
            if siguiente[0] < anterior[1]:
                raise ErrorAPI(f"Las franjas del {DIAS_SEMANA[dia]} se solapan entre si")

    profesional.horarios.clear()
    db.session.flush()
    for dia, inicio, fin in nuevos:
        profesional.horarios.append(
            HorarioAtencion(dia_semana=dia, hora_inicio=inicio, hora_fin=fin)
        )


def _obtener(pid) -> Profesional:
    profesional = db.session.get(Profesional, pid)
    if profesional is None:
        raise ErrorAPI("Profesional no encontrado", 404)
    return profesional


def _verificar_alcance(pid):
    """README: un usuario con rol profesional solo accede a su propia ficha."""
    alcance = alcance_profesional_id()
    if alcance and pid != alcance:
        raise ErrorAPI("Solo puedes consultar tu propio perfil profesional", 403)
