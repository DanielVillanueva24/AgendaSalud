"""Reserva, calendario y ciclo de vida de las citas (RF3, RF4, RF5)."""
from datetime import datetime, timedelta, date

from flask import Blueprint, jsonify, request

from extensions import db
from models import (
    Cita, Paciente, Profesional,
    ESTADOS_CITA, ESTADOS_BLOQUEANTES,
)
from security import login_requerido, rol_requerido, usuario_actual, alcance_profesional_id
from utils import (
    ErrorAPI, body, requerido, parse_dt, parse_fecha,
    parse_int, parse_bool, limpiar,
)
import scheduler
import notifications

bp = Blueprint("citas", __name__, url_prefix="/api/citas")

# Estados que el rol `profesional` puede fijar: solo registro de asistencia
ESTADOS_ASISTENCIA = {"atendida", "no_asistio"}

# Transiciones validas del estado de la cita (RF5)
TRANSICIONES = {
    "pendiente": {"confirmada", "cancelada", "atendida", "no_asistio"},
    "confirmada": {"cancelada", "atendida", "no_asistio", "pendiente"},
    "cancelada": {"pendiente"},
    "atendida": {"no_asistio"},
    "no_asistio": {"atendida"},
}


# --- Listado / calendario -----------------------------------------------------

@bp.get("")
@login_requerido
def listar():
    """
    Citas en un rango. FullCalendar envia ?desde=&hasta= en ISO.
    Filtros: profesional_id, paciente_id, estado (repetible).
    """
    q = Cita.query

    desde = request.args.get("desde")
    hasta = request.args.get("hasta")
    if desde:
        q = q.filter(Cita.fin > parse_dt(desde, "desde"))
    if hasta:
        q = q.filter(Cita.inicio < parse_dt(hasta, "hasta"))

    profesional_id = parse_int(request.args.get("profesional_id"), "profesional_id")
    # Un usuario con rol profesional queda acotado a su propia agenda
    alcance = alcance_profesional_id()
    if alcance:
        profesional_id = alcance
    if profesional_id:
        q = q.filter(Cita.profesional_id == profesional_id)

    paciente_id = parse_int(request.args.get("paciente_id"), "paciente_id")
    if paciente_id:
        q = q.filter(Cita.paciente_id == paciente_id)

    estados = [e for e in request.args.getlist("estado") if e in ESTADOS_CITA]
    if estados:
        q = q.filter(Cita.estado.in_(estados))
    elif not parse_bool(request.args.get("incluir_canceladas"), True):
        q = q.filter(Cita.estado != "cancelada")

    citas = q.order_by(Cita.inicio).limit(1000).all()
    return jsonify({"citas": [c.to_dict() for c in citas], "total": len(citas)})


@bp.get("/version")
@login_requerido
def version():
    """
    Huella barata del estado de la agenda para sincronizar varios navegadores.

    El cliente la consulta cada pocos segundos y solo repinta cuando cambia, de
    modo que una cita creada por recepcion aparece en la pantalla del profesional
    sin recargar. Se combina el numero de citas con la ultima modificacion: el
    contador detecta altas y bajas, la marca de tiempo detecta ediciones.
    """
    q = db.session.query(db.func.count(Cita.id), db.func.max(Cita.actualizada_en))
    alcance = alcance_profesional_id()
    if alcance:
        # Un profesional solo debe repintar cuando cambia SU agenda
        q = q.filter(Cita.profesional_id == alcance)

    total, ultima = q.one()
    return jsonify({"version": f"{total}:{ultima.isoformat() if ultima else '-'}"})


@bp.get("/<int:cid>")
@login_requerido
def detalle(cid):
    return jsonify(_obtener(cid).to_dict())


@bp.get("/agenda-dia")
@login_requerido
def agenda_dia():
    """Vista operativa del dia: lo que recepcion necesita a primera hora."""
    fecha = parse_fecha(request.args.get("fecha"), "fecha") or date.today()
    inicio = datetime.combine(fecha, datetime.min.time())
    fin = inicio + timedelta(days=1)

    q = Cita.query.filter(Cita.inicio >= inicio, Cita.inicio < fin)
    alcance = alcance_profesional_id()
    if alcance:
        q = q.filter(Cita.profesional_id == alcance)

    citas = q.order_by(Cita.inicio).all()
    resumen = {estado: 0 for estado in ESTADOS_CITA}
    for c in citas:
        resumen[c.estado] += 1

    return jsonify({
        "fecha": fecha.isoformat(),
        "citas": [c.to_dict() for c in citas],
        "resumen": resumen,
        "total": len(citas),
        "por_confirmar": resumen["pendiente"],
    })


# --- Motor de optimizacion (RF4) ---------------------------------------------

@bp.get("/sugerencias")
@login_requerido
def sugerencias():
    """
    Mejores huecos segun la heuristica greedy.
    ?profesional_id=&duracion=&desde=&dias=&limite=&fecha=
    Sin profesional_id busca en todos los profesionales activos.
    """
    duracion = parse_int(request.args.get("duracion"), "duracion")
    limite = min(parse_int(request.args.get("limite"), "limite", 5) or 5, 20)
    dias = min(parse_int(request.args.get("dias"), "dias", 14) or 14, 60)
    desde = parse_dt(request.args["desde"], "desde") if request.args.get("desde") else None
    solo_dia = parse_fecha(request.args.get("fecha"), "fecha")

    profesional_id = parse_int(request.args.get("profesional_id"), "profesional_id")
    alcance = alcance_profesional_id()
    if alcance:
        profesional_id = alcance

    if profesional_id:
        profesional = db.session.get(Profesional, profesional_id)
        if profesional is None:
            raise ErrorAPI("Profesional no encontrado", 404)
        slots = scheduler.sugerir_slots(
            profesional, duracion, desde, horizonte_dias=dias,
            limite=limite, solo_dia=solo_dia,
        )
    else:
        activos = Profesional.query.filter_by(activo=True).all()
        if not activos:
            raise ErrorAPI("No hay profesionales activos configurados", 409)
        slots = scheduler.sugerir_multiprofesional(
            activos, duracion, desde, horizonte_dias=dias, limite=limite, solo_dia=solo_dia,
        )

    return jsonify({
        "sugerencias": slots,
        "parametros": {
            "duracion_min": duracion,
            "horizonte_dias": dias,
            "profesional_id": profesional_id,
        },
        "heuristica": "greedy: minimiza espera del paciente y fragmentacion de la agenda",
    })


@bp.get("/verificar")
@login_requerido
def verificar():
    """Comprueba si un intervalo esta libre, antes de confirmar la reserva."""
    profesional_id = parse_int(request.args.get("profesional_id"), "profesional_id")
    # La respuesta incluye los conflictos con nombre de paciente: sin acotar,
    # sondeando horas se puede reconstruir la agenda ajena.
    alcance = alcance_profesional_id()
    if alcance:
        profesional_id = alcance
    if not profesional_id:
        raise ErrorAPI("Falta 'profesional_id'")
    profesional = db.session.get(Profesional, profesional_id)
    if profesional is None:
        raise ErrorAPI("Profesional no encontrado", 404)

    inicio = parse_dt(request.args.get("inicio"), "inicio")
    duracion = parse_int(request.args.get("duracion"), "duracion", profesional.duracion_cita_min)
    fin = parse_dt(request.args["fin"], "fin") if request.args.get("fin") else inicio + timedelta(minutes=duracion)
    excluir = parse_int(request.args.get("excluir_cita_id"), "excluir_cita_id")

    return jsonify(scheduler.verificar_disponibilidad(profesional, inicio, fin, excluir))


# --- Recordatorios de confirmacion (RF5) -------------------------------------
#
# El sistema no envia SMS ni correo: no habia presupuesto para una pasarela y
# quedaba fuera del alcance del MVP. Lo que hace es priorizar a quien hay que
# llamar y dejar registrado el contacto, de modo que el efecto del recordatorio
# sobre el ausentismo si queda medido (ver /api/indicadores/patrones).

@bp.get("/por-confirmar")
@login_requerido
def por_confirmar():
    """
    Citas proximas todavia sin confirmar, ordenadas por prioridad de llamada.

    La prioridad combina el historial de inasistencia del paciente con la
    cercania de la cita: primero quien mas falta y quien antes se atiende.
    """
    dias = min(parse_int(request.args.get("dias"), "dias", 7) or 7, 60)
    ahora = datetime.now()
    limite = ahora + timedelta(days=dias)

    q = Cita.query.filter(
        Cita.estado == "pendiente",
        Cita.inicio >= ahora,
        Cita.inicio <= limite,
    )
    alcance = alcance_profesional_id()
    if alcance:
        q = q.filter(Cita.profesional_id == alcance)

    filas = []
    for cita in q.order_by(Cita.inicio).all():
        asistencia = cita.paciente.estadisticas_asistencia() if cita.paciente else {}
        tasa = asistencia.get("tasa_ausentismo", 0.0)
        horas_faltantes = (cita.inicio - ahora).total_seconds() / 3600

        # Riesgo alto: historial de faltas o cita inminente sin confirmar
        if tasa >= 20 or (horas_faltantes <= 24 and tasa > 0):
            riesgo = "alto"
        elif tasa > 0 or horas_faltantes <= 48:
            riesgo = "medio"
        else:
            riesgo = "bajo"

        filas.append({
            **cita.to_dict(),
            "asistencia": asistencia,
            "riesgo": riesgo,
            "horas_faltantes": round(horas_faltantes, 1),
            "prioridad": round(tasa * 2 + max(0, 72 - horas_faltantes), 2),
        })

    filas.sort(key=lambda f: -f["prioridad"])
    resumen = {nivel: sum(1 for f in filas if f["riesgo"] == nivel) for nivel in ("alto", "medio", "bajo")}

    return jsonify({
        "citas": filas,
        "total": len(filas),
        "dias": dias,
        "resumen_riesgo": resumen,
        "sin_contactar": sum(1 for f in filas if not f["recordatorios_enviados"]),
    })


@bp.post("/<int:cid>/recordatorio")
@rol_requerido("recepcion", "profesional")
def registrar_recordatorio(cid):
    """
    Deja constancia de que se contacto al paciente para confirmar la cita.

    Si el paciente tiene correo registrado, ademas se le envia el recordatorio;
    el resultado del envio viaja en la clave `correo` de la respuesta.
    """
    cita = _obtener(cid)
    _verificar_editable(cita)

    if cita.estado in ("cancelada", "atendida", "no_asistio"):
        raise ErrorAPI("La cita ya está cerrada; no procede un recordatorio.", 409)

    correo = notifications.enviar_recordatorio(cita)

    # El contador registra el contacto con el paciente (llamada o correo), asi
    # que sube tambien cuando la recepcion solo llamo por telefono.
    cita.recordatorios_enviados = (cita.recordatorios_enviados or 0) + 1
    cita.ultimo_recordatorio_en = datetime.now()
    db.session.commit()

    datos = cita.to_dict()
    datos["correo"] = correo
    return jsonify(datos)


# --- Reacomodo de la agenda de un dia (RF4) ----------------------------------

@bp.get("/reacomodo")
@login_requerido
def reacomodo_previa():
    """
    Vista previa del reacomodo: no modifica nada.
    ?profesional_id=&fecha=&max_adelanto_min=&solo_pendientes=
    """
    profesional, dia = _profesional_y_dia()
    return jsonify(scheduler.proponer_reacomodo(profesional, dia, **_opciones(request.args)))


@bp.post("/reacomodo")
@rol_requerido("recepcion")          # reprograma en bloque: mismo criterio que PUT /citas
def reacomodo_aplicar():
    """
    Aplica el reacomodo propuesto para ese dia.

    La propuesta se recalcula en el servidor (no se confia en la que vio el
    navegador) y, tras mover las citas, se verifica la integridad de la agenda
    antes de confirmar la transaccion.
    """
    datos = body()
    profesional, dia = _profesional_y_dia(desde_body=True)
    propuesta = scheduler.proponer_reacomodo(profesional, dia, **_opciones(datos))

    if not propuesta["movimientos"]:
        return jsonify({**propuesta, "aplicados": 0})

    for mov in propuesta["movimientos"]:
        cita = db.session.get(Cita, mov["cita_id"])
        if cita is None:
            raise ErrorAPI("La agenda cambió mientras se calculaba el reacomodo. Vuelve a intentarlo.", 409)
        cita.inicio = parse_dt(mov["inicio_propuesto"], "inicio_propuesto")
        cita.fin = parse_dt(mov["fin_propuesto"], "fin_propuesto")

    db.session.flush()

    problema = scheduler.verificar_integridad_dia(profesional, dia)
    if problema:
        db.session.rollback()
        raise ErrorAPI(f"El reacomodo se descartó por seguridad: {problema}", 409)

    db.session.commit()
    return jsonify({**propuesta, "aplicados": len(propuesta["movimientos"])})


def _profesional_y_dia(desde_body=False):
    datos = body() if desde_body else request.args
    profesional_id = parse_int(datos.get("profesional_id"), "profesional_id")

    alcance = alcance_profesional_id()
    if alcance:
        profesional_id = alcance
    if not profesional_id:
        raise ErrorAPI("Indica el profesional cuya agenda quieres reacomodar")

    profesional = db.session.get(Profesional, profesional_id)
    if profesional is None:
        raise ErrorAPI("Profesional no encontrado", 404)

    dia = parse_fecha(datos.get("fecha"), "fecha")
    if dia is None:
        raise ErrorAPI("Indica la fecha a reacomodar")
    return profesional, dia


def _opciones(datos):
    """
    Restricciones de negocio del reacomodo. La vista previa y la aplicacion
    tienen que usar las mismas: si no, el usuario aprobaria una propuesta y se
    ejecutaria otra distinta.
    """
    adelanto = parse_int(datos.get("max_adelanto_min"), "max_adelanto_min",
                         scheduler.MAX_ADELANTO_MIN)
    if adelanto is not None and adelanto < 0:
        raise ErrorAPI("El adelanto maximo no puede ser negativo")
    return {
        # 0 = sin limite de adelanto
        "max_adelanto_min": adelanto,
        "solo_pendientes": parse_bool(datos.get("solo_pendientes")),
    }


# --- Alta / modificacion ------------------------------------------------------

@bp.post("")
@rol_requerido("recepcion")          # README: el profesional solo ve su agenda, no agenda
def crear():
    data = body()
    requerido(data, "paciente_id", "profesional_id", "inicio")

    paciente = db.session.get(Paciente, parse_int(data["paciente_id"], "paciente_id"))
    if paciente is None:
        raise ErrorAPI("Paciente no encontrado", 404)
    if not paciente.activo:
        raise ErrorAPI("El paciente esta inactivo", 409)

    profesional_id = parse_int(data["profesional_id"], "profesional_id")
    # Un profesional solo agenda en su propia agenda, mande lo que mande el body
    alcance = alcance_profesional_id()
    if alcance:
        profesional_id = alcance

    profesional = db.session.get(Profesional, profesional_id)
    if profesional is None:
        raise ErrorAPI("Profesional no encontrado", 404)
    if not profesional.activo:
        raise ErrorAPI("El profesional esta inactivo", 409)

    inicio = parse_dt(data["inicio"], "inicio")
    if inicio.date() < date.today():
        raise ErrorAPI("No se pueden agendar citas en una fecha ya pasada", 409)

    fin = _calcular_fin(data, inicio, profesional)
    forzar = parse_bool(data.get("forzar"))

    _validar_intervalo(profesional, paciente, inicio, fin, forzar=forzar)

    cita = Cita(
        paciente_id=paciente.id,
        profesional_id=profesional.id,
        inicio=inicio,
        fin=fin,
        estado=data.get("estado") if data.get("estado") in ESTADOS_CITA else "pendiente",
        motivo=limpiar(data.get("motivo")),
        notas=limpiar(data.get("notas")),
        creada_por_id=usuario_actual().id,
        sugerida_por_motor=parse_bool(data.get("sugerida_por_motor")),
    )
    if cita.estado == "confirmada":
        cita.confirmada_en = datetime.now()

    db.session.add(cita)
    db.session.commit()

    # El correo sale despues del commit y en segundo plano: si el SMTP falla o
    # tarda, la cita ya esta guardada y la respuesta no se retrasa.
    notifications.enviar_confirmacion(cita)

    return jsonify(cita.to_dict()), 201


@bp.put("/<int:cid>")
@rol_requerido("recepcion")          # README: reprogramar es de recepcion y admin
def actualizar(cid):
    """Reprogramar o editar datos de la cita (drag & drop del calendario incluido)."""
    cita = _obtener(cid)
    _verificar_editable(cita)
    data = body()

    if "profesional_id" in data:
        profesional = db.session.get(Profesional, parse_int(data["profesional_id"], "profesional_id"))
        if profesional is None:
            raise ErrorAPI("Profesional no encontrado", 404)
        # _obtener() ya comprobo que la cita sea suya; sin esto un profesional
        # podria sacarsela del alcance reasignandola a otro.
        alcance = alcance_profesional_id()
        if alcance and profesional.id != alcance:
            raise ErrorAPI("No puedes reasignar la cita a otro profesional", 403)
        if not profesional.activo:
            raise ErrorAPI("El profesional esta inactivo", 409)
        cita.profesional_id = profesional.id
    else:
        profesional = cita.profesional

    if "paciente_id" in data:
        paciente = db.session.get(Paciente, parse_int(data["paciente_id"], "paciente_id"))
        if paciente is None:
            raise ErrorAPI("Paciente no encontrado", 404)
        cita.paciente_id = paciente.id

    reprogramada = False
    if "inicio" in data:
        nuevo_inicio = parse_dt(data["inicio"], "inicio")
        nuevo_fin = _calcular_fin(data, nuevo_inicio, profesional, duracion_actual=cita.duracion_min)
        reprogramada = (nuevo_inicio != cita.inicio or nuevo_fin != cita.fin)
        cita.inicio, cita.fin = nuevo_inicio, nuevo_fin
    elif "duracion_min" in data:
        cita.fin = cita.inicio + timedelta(minutes=parse_int(data["duracion_min"], "duracion_min"))
        reprogramada = True

    if reprogramada and cita.estado in ESTADOS_BLOQUEANTES:
        _validar_intervalo(
            profesional, cita.paciente, cita.inicio, cita.fin,
            forzar=parse_bool(data.get("forzar")), excluir_cita_id=cita.id,
        )

    for campo in ("motivo", "notas"):
        if campo in data:
            setattr(cita, campo, limpiar(data[campo]))

    if "estado" in data:
        _cambiar_estado(cita, data["estado"], limpiar(data.get("motivo_cancelacion")))

    db.session.commit()
    return jsonify(cita.to_dict())


@bp.route("/<int:cid>/estado", methods=["POST", "PATCH"])
@rol_requerido("recepcion", "profesional")
def cambiar_estado(cid):
    """
    RF5: confirmar, cancelar o registrar asistencia.

    Se acepta PATCH ademas de POST porque es el verbo semanticamente correcto
    para una modificacion parcial y es el que documenta el informe.
    """
    cita = _obtener(cid)
    _verificar_editable(cita)
    data = body()
    requerido(data, "estado")
    anterior = cita.estado
    _cambiar_estado(cita, data["estado"], limpiar(data.get("motivo_cancelacion")))
    db.session.commit()

    if cita.estado == "confirmada" and anterior != "confirmada":
        notifications.enviar_confirmacion(cita)

    return jsonify(cita.to_dict())


@bp.delete("/<int:cid>")
@rol_requerido("recepcion")
def eliminar(cid):
    """
    Borrado fisico. Solo se permite para citas futuras aun sin cerrar: las citas
    cerradas alimentan los indicadores de ausentismo y no deben desaparecer.
    """
    cita = _obtener(cid)
    _verificar_editable(cita)
    if cita.estado in ("atendida", "no_asistio"):
        raise ErrorAPI(
            "No se puede eliminar una cita ya cerrada; cancelala si necesitas anularla.", 409
        )
    db.session.delete(cita)
    db.session.commit()
    return jsonify({"ok": True})


# --- Helpers internos ---------------------------------------------------------

def _calcular_fin(data, inicio, profesional, duracion_actual=None):
    if data.get("fin"):
        fin = parse_dt(data["fin"], "fin")
    else:
        duracion = parse_int(data.get("duracion_min"), "duracion_min",
                             duracion_actual or profesional.duracion_cita_min)
        fin = inicio + timedelta(minutes=duracion)
    if fin <= inicio:
        raise ErrorAPI("La hora de fin debe ser posterior a la de inicio")
    if (fin - inicio) > timedelta(hours=8):
        raise ErrorAPI("La duracion de una cita no puede superar las 8 horas")
    return fin


def _validar_intervalo(profesional, paciente, inicio, fin, forzar=False, excluir_cita_id=None):
    """Aplica las reglas de agenda. `forzar` permite saltar el horario de atencion, nunca el solapamiento."""
    resultado = scheduler.verificar_disponibilidad(profesional, inicio, fin, excluir_cita_id)

    if resultado["conflictos"]:
        conflicto = resultado["conflictos"][0]
        raise ErrorAPI(
            f"{profesional.nombre_completo} ya tiene una cita de "
            f"{conflicto['inicio'][11:16]} a {conflicto['fin'][11:16]}",
            409,
            {"conflictos": resultado["conflictos"], "tipo": "solapamiento"},
        )

    if resultado["fuera_de_horario"] and not forzar:
        raise ErrorAPI(
            f"El horario esta fuera de la agenda de atencion de {profesional.nombre_completo}",
            409,
            {"tipo": "fuera_de_horario", "puede_forzar": True},
        )

    # El paciente tampoco puede estar en dos consultas a la vez
    choque_paciente = Cita.query.filter(
        Cita.paciente_id == paciente.id,
        Cita.estado.in_(ESTADOS_BLOQUEANTES),
        Cita.inicio < fin,
        Cita.fin > inicio,
    )
    if excluir_cita_id:
        choque_paciente = choque_paciente.filter(Cita.id != excluir_cita_id)
    if choque_paciente.first():
        raise ErrorAPI(
            f"{paciente.nombre_completo} ya tiene otra cita en ese horario",
            409, {"tipo": "solapamiento_paciente"},
        )


def _verificar_editable(cita):
    """
    Una cita de un dia ya pasado es historico y no admite cambios de ningun tipo.
    El dia en curso sigue abierto para poder cerrar la jornada (marcar asistencia).
    """
    if not cita.es_editable:
        raise ErrorAPI(
            "Esta cita es de una fecha pasada: el historico no se modifica", 409,
            {"solo_lectura": True},
        )


def _cambiar_estado(cita, nuevo, motivo_cancelacion=None):
    if nuevo not in ESTADOS_CITA:
        raise ErrorAPI(
            f"Estado invalido: {nuevo}", 400, {"estados_validos": list(ESTADOS_CITA)}
        )

    # README: el profesional solo registra asistencia; confirmar y cancelar
    # son de recepcion. El admin pasa siempre.
    u = usuario_actual()
    if u and u.rol == "profesional" and nuevo not in ESTADOS_ASISTENCIA:
        raise ErrorAPI(
            "Un profesional solo puede registrar asistencia (atendida o no asistio)", 403,
            {"estados_permitidos": sorted(ESTADOS_ASISTENCIA)},
        )

    if nuevo == cita.estado:
        return

    if nuevo not in TRANSICIONES.get(cita.estado, set()):
        raise ErrorAPI(
            f"No se puede pasar de '{cita.estado}' a '{nuevo}'", 409,
            {"transiciones_validas": sorted(TRANSICIONES.get(cita.estado, set()))},
        )

    # Reactivar una cita cancelada exige que el hueco siga libre
    if cita.estado == "cancelada" and nuevo in ESTADOS_BLOQUEANTES:
        _validar_intervalo(cita.profesional, cita.paciente, cita.inicio, cita.fin,
                           forzar=True, excluir_cita_id=cita.id)

    cita.estado = nuevo
    ahora = datetime.now()

    if nuevo == "confirmada":
        cita.confirmada_en = ahora
        cita.cerrada_en = None
        cita.motivo_cancelacion = None
    elif nuevo == "cancelada":
        cita.motivo_cancelacion = motivo_cancelacion
        cita.cerrada_en = ahora
    elif nuevo in ("atendida", "no_asistio"):
        cita.cerrada_en = ahora
    elif nuevo == "pendiente":
        cita.confirmada_en = None
        cita.cerrada_en = None
        cita.motivo_cancelacion = None


def _obtener(cid) -> Cita:
    cita = db.session.get(Cita, cid)
    if cita is None:
        raise ErrorAPI("Cita no encontrada", 404)
    alcance = alcance_profesional_id()
    if alcance and cita.profesional_id != alcance:
        raise ErrorAPI("No tienes acceso a esta cita", 403)
    return cita
