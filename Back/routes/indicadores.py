"""Panel de indicadores: ausentismo y ocupacion de agenda (RF6)."""
from datetime import datetime, timedelta, date

from flask import Blueprint, jsonify, request

from extensions import db
from models import Cita, Paciente, Profesional, DIAS_SEMANA, ESTADOS_CITA, ESTADOS_BLOQUEANTES
from security import login_requerido, alcance_profesional_id
from utils import parse_fecha, parse_int
import scheduler

bp = Blueprint("indicadores", __name__, url_prefix="/api/indicadores")


def _rango():
    """Rango de analisis; por defecto los ultimos 90 dias."""
    hasta = parse_fecha(request.args.get("hasta"), "hasta") or date.today()
    desde = parse_fecha(request.args.get("desde"), "desde") or (hasta - timedelta(days=89))
    if desde > hasta:
        desde, hasta = hasta, desde
    return desde, hasta


def _query_base(desde, hasta):
    q = Cita.query.filter(
        Cita.inicio >= datetime.combine(desde, datetime.min.time()),
        Cita.inicio < datetime.combine(hasta + timedelta(days=1), datetime.min.time()),
    )
    profesional_id = parse_int(request.args.get("profesional_id"), "profesional_id")
    alcance = alcance_profesional_id()
    if alcance:
        profesional_id = alcance
    if profesional_id:
        q = q.filter(Cita.profesional_id == profesional_id)
    return q, profesional_id


def _tasa(parte, total):
    return round(parte / total * 100, 1) if total else 0.0


@bp.get("/resumen")
@login_requerido
def resumen():
    """KPIs principales del rango: ausentismo, ocupacion, confirmacion."""
    desde, hasta = _rango()
    q, profesional_id = _query_base(desde, hasta)
    citas = q.all()

    conteo = {estado: 0 for estado in ESTADOS_CITA}
    for c in citas:
        conteo[c.estado] += 1

    cerradas = conteo["atendida"] + conteo["no_asistio"]
    agendadas = len(citas) - conteo["cancelada"]
    # Se confirmo alguna vez, con independencia del estado final de la cita
    confirmadas_alguna_vez = sum(
        1 for c in citas if c.confirmada_en is not None and c.estado != "cancelada"
    )

    # Ocupacion = minutos efectivamente agendados / capacidad teorica de las agendas
    profesionales = ([db.session.get(Profesional, profesional_id)] if profesional_id
                     else Profesional.query.filter_by(activo=True).all())
    profesionales = [p for p in profesionales if p]
    capacidad = sum(scheduler.minutos_disponibles(p, desde, hasta) for p in profesionales)
    minutos_ocupados = sum(c.duracion_min for c in citas if c.estado in ESTADOS_BLOQUEANTES)
    minutos_perdidos = sum(c.duracion_min for c in citas if c.estado == "no_asistio")

    return jsonify({
        "rango": {"desde": desde.isoformat(), "hasta": hasta.isoformat(),
                  "dias": (hasta - desde).days + 1},
        "total_citas": len(citas),
        "por_estado": conteo,
        "kpis": {
            "tasa_ausentismo": _tasa(conteo["no_asistio"], cerradas),
            "tasa_asistencia": _tasa(conteo["atendida"], cerradas),
            "tasa_cancelacion": _tasa(conteo["cancelada"], len(citas)),
            "tasa_confirmacion": _tasa(confirmadas_alguna_vez, agendadas),
            "ocupacion": _tasa(minutos_ocupados, capacidad),
            "citas_cerradas": cerradas,
            "ausencias": conteo["no_asistio"],
            "horas_perdidas": round(minutos_perdidos / 60, 1),
            "horas_agendadas": round(minutos_ocupados / 60, 1),
            "horas_capacidad": round(capacidad / 60, 1),
        },
    })


@bp.get("/serie-ausentismo")
@login_requerido
def serie_ausentismo():
    """Evolucion temporal del ausentismo (?agrupar=dia|semana|mes)."""
    desde, hasta = _rango()
    q, _ = _query_base(desde, hasta)
    agrupar = request.args.get("agrupar", "semana")

    buckets = {}
    for c in q.filter(Cita.estado.in_(("atendida", "no_asistio"))).all():
        clave = _clave_periodo(c.inicio.date(), agrupar)
        b = buckets.setdefault(clave, {"periodo": clave, "atendidas": 0, "ausencias": 0})
        b["atendidas" if c.estado == "atendida" else "ausencias"] += 1

    serie = []
    for clave in sorted(buckets):
        b = buckets[clave]
        total = b["atendidas"] + b["ausencias"]
        b["total"] = total
        b["tasa_ausentismo"] = _tasa(b["ausencias"], total)
        serie.append(b)

    return jsonify({"agrupar": agrupar, "serie": serie})


@bp.get("/por-profesional")
@login_requerido
def por_profesional():
    """Ausentismo y ocupacion desglosados por profesional."""
    desde, hasta = _rango()
    q, _ = _query_base(desde, hasta)
    citas = q.all()

    datos = {}
    for c in citas:
        d = datos.setdefault(c.profesional_id, {
            "profesional_id": c.profesional_id,
            "profesional": c.profesional.nombre_completo if c.profesional else "—",
            "color": c.profesional.color if c.profesional else "#2563eb",
            "total": 0, "atendidas": 0, "ausencias": 0, "canceladas": 0,
            "minutos_ocupados": 0,
        })
        d["total"] += 1
        if c.estado == "atendida":
            d["atendidas"] += 1
        elif c.estado == "no_asistio":
            d["ausencias"] += 1
        elif c.estado == "cancelada":
            d["canceladas"] += 1
        if c.estado in ESTADOS_BLOQUEANTES:
            d["minutos_ocupados"] += c.duracion_min

    filas = []
    for pid, d in datos.items():
        profesional = db.session.get(Profesional, pid)
        capacidad = scheduler.minutos_disponibles(profesional, desde, hasta) if profesional else 0
        cerradas = d["atendidas"] + d["ausencias"]
        d["tasa_ausentismo"] = _tasa(d["ausencias"], cerradas)
        d["ocupacion"] = _tasa(d["minutos_ocupados"], capacidad)
        d["horas_agendadas"] = round(d["minutos_ocupados"] / 60, 1)
        filas.append(d)

    filas.sort(key=lambda f: f["tasa_ausentismo"], reverse=True)
    return jsonify({"profesionales": filas})


@bp.get("/patrones")
@login_requerido
def patrones():
    """
    Donde se concentra el ausentismo: por dia de la semana y por franja horaria.
    Sirve para justificar politicas de recordatorio y sobreagenda.
    """
    desde, hasta = _rango()
    q, _ = _query_base(desde, hasta)
    cerradas = q.filter(Cita.estado.in_(("atendida", "no_asistio"))).all()

    por_dia = [{"dia": DIAS_SEMANA[i], "indice": i, "atendidas": 0, "ausencias": 0} for i in range(7)]
    por_hora = {}

    for c in cerradas:
        fila = por_dia[c.inicio.weekday()]
        fila["atendidas" if c.estado == "atendida" else "ausencias"] += 1

        franja = por_hora.setdefault(c.inicio.hour, {
            "hora": f"{c.inicio.hour:02d}:00", "indice": c.inicio.hour,
            "atendidas": 0, "ausencias": 0,
        })
        franja["atendidas" if c.estado == "atendida" else "ausencias"] += 1

    for fila in por_dia:
        fila["tasa_ausentismo"] = _tasa(fila["ausencias"], fila["atendidas"] + fila["ausencias"])
    franjas = sorted(por_hora.values(), key=lambda f: f["indice"])
    for fila in franjas:
        fila["tasa_ausentismo"] = _tasa(fila["ausencias"], fila["atendidas"] + fila["ausencias"])

    # Efecto de la confirmacion previa sobre el ausentismo (evidencia para RF5)
    confirmadas = [c for c in cerradas if c.confirmada_en is not None]
    sin_confirmar = [c for c in cerradas if c.confirmada_en is None]
    efecto = {
        "confirmadas": {
            "total": len(confirmadas),
            "ausencias": sum(1 for c in confirmadas if c.estado == "no_asistio"),
        },
        "sin_confirmar": {
            "total": len(sin_confirmar),
            "ausencias": sum(1 for c in sin_confirmar if c.estado == "no_asistio"),
        },
    }
    for grupo in efecto.values():
        grupo["tasa_ausentismo"] = _tasa(grupo["ausencias"], grupo["total"])
    efecto["reduccion_pp"] = round(
        efecto["sin_confirmar"]["tasa_ausentismo"] - efecto["confirmadas"]["tasa_ausentismo"], 1
    )

    # Efecto del recordatorio telefonico registrado por recepcion
    con_recordatorio = [c for c in cerradas if (c.recordatorios_enviados or 0) > 0]
    sin_recordatorio = [c for c in cerradas if not c.recordatorios_enviados]
    efecto_recordatorio = {
        "con_recordatorio": {
            "total": len(con_recordatorio),
            "ausencias": sum(1 for c in con_recordatorio if c.estado == "no_asistio"),
        },
        "sin_recordatorio": {
            "total": len(sin_recordatorio),
            "ausencias": sum(1 for c in sin_recordatorio if c.estado == "no_asistio"),
        },
    }
    for grupo in efecto_recordatorio.values():
        grupo["tasa_ausentismo"] = _tasa(grupo["ausencias"], grupo["total"])
    efecto_recordatorio["reduccion_pp"] = round(
        efecto_recordatorio["sin_recordatorio"]["tasa_ausentismo"]
        - efecto_recordatorio["con_recordatorio"]["tasa_ausentismo"], 1
    )

    return jsonify({
        "por_dia": por_dia,
        "por_hora": franjas,
        "efecto_confirmacion": efecto,
        "efecto_recordatorio": efecto_recordatorio,
    })


@bp.get("/pacientes-riesgo")
@login_requerido
def pacientes_riesgo():
    """Pacientes con mayor historial de inasistencia (candidatos a recordatorio reforzado)."""
    limite = min(parse_int(request.args.get("limite"), "limite", 10) or 10, 50)
    minimo = parse_int(request.args.get("minimo_citas"), "minimo_citas", 2) or 2

    # La lista lleva telefono e historial: un profesional solo ve a los pacientes
    # que ha atendido, no el padron completo.
    q = Paciente.query.filter_by(activo=True)
    alcance = alcance_profesional_id()
    if alcance:
        q = q.filter(Paciente.citas.any(Cita.profesional_id == alcance))

    filas = []
    for paciente in q.all():
        stats = paciente.estadisticas_asistencia()
        if stats["citas_cerradas"] >= minimo and stats["ausencias"] > 0:
            filas.append({
                "paciente_id": paciente.id,
                "paciente": paciente.nombre_completo,
                "telefono": paciente.telefono,
                **stats,
            })

    filas.sort(key=lambda f: (f["tasa_ausentismo"], f["ausencias"]), reverse=True)
    return jsonify({"pacientes": filas[:limite], "minimo_citas": minimo})


def _clave_periodo(dia: date, agrupar: str) -> str:
    if agrupar == "dia":
        return dia.isoformat()
    if agrupar == "mes":
        return dia.strftime("%Y-%m")
    anio, semana, _ = dia.isocalendar()
    return f"{anio}-S{semana:02d}"
