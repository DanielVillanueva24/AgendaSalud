"""
Motor de optimizacion de agenda (RF4).

Heuristica greedy con dos objetivos combinados:

  1. Minimizar la espera del paciente  -> se recorren los dias en orden cronologico
     y dentro de cada dia los huecos de mas temprano a mas tarde.
  2. Minimizar la fragmentacion de la agenda -> entre los inicios candidatos de un
     mismo hueco se prefiere el que deja menos tiempo residual inutilizable
     (es decir, el que "pega" la cita nueva contra una cita existente o contra el
     borde de la franja de atencion).

El costo de cada candidato es:

    costo = w_espera * minutos_de_espera + w_frag * minutos_residuales_inutilizables

Se emiten los N candidatos de menor costo. Es greedy porque decide slot a slot con
informacion local (el hueco que esta evaluando) sin reordenar las citas ya agendadas.
"""
from datetime import datetime, timedelta, time

from sqlalchemy import and_, or_

from extensions import db
from models import Cita, Profesional, ESTADOS_BLOQUEANTES

# Pesos de la funcion de costo. La espera pesa poco por minuto para que un hueco
# de hoy siempre gane a uno de manana, pero la fragmentacion desempata dentro del dia.
PESO_ESPERA = 1.0
PESO_FRAGMENTACION = 12.0

GRANULARIDAD_DEFECTO = 15   # minutos entre inicios candidatos
HORIZONTE_DIAS_DEFECTO = 14


# --- Utilidades ---------------------------------------------------------------

def _combinar(dia, hora: time) -> datetime:
    return datetime.combine(dia, hora)


def _solapan(a_ini, a_fin, b_ini, b_fin) -> bool:
    """Dos intervalos [ini, fin) se solapan si cada uno empieza antes de que el otro termine."""
    return a_ini < b_fin and b_ini < a_fin


def citas_bloqueantes(profesional_id, desde: datetime, hasta: datetime, excluir_id=None):
    """Citas que ocupan agenda del profesional dentro del rango."""
    q = Cita.query.filter(
        Cita.profesional_id == profesional_id,
        Cita.estado.in_(ESTADOS_BLOQUEANTES),
        Cita.inicio < hasta,
        Cita.fin > desde,
    )
    if excluir_id:
        q = q.filter(Cita.id != excluir_id)
    return q.order_by(Cita.inicio).all()


def franjas_de_atencion(profesional: Profesional, dia):
    """Franjas horarias (datetime) en que el profesional atiende ese dia, ordenadas y fusionadas."""
    franjas = []
    for h in profesional.horarios:
        if h.dia_semana == dia.weekday():
            franjas.append((_combinar(dia, h.hora_inicio), _combinar(dia, h.hora_fin)))
    franjas.sort()

    fusionadas = []
    for ini, fin in franjas:
        if fusionadas and ini <= fusionadas[-1][1]:
            fusionadas[-1] = (fusionadas[-1][0], max(fusionadas[-1][1], fin))
        else:
            fusionadas.append((ini, fin))
    return fusionadas


def huecos_libres(profesional: Profesional, dia, excluir_cita_id=None):
    """
    Resta las citas bloqueantes a las franjas de atencion.
    Devuelve [(inicio, fin, pegado_izq, pegado_der)] donde los flags indican si el
    borde del hueco toca una cita existente (util para la heuristica de compactacion).
    """
    franjas = franjas_de_atencion(profesional, dia)
    if not franjas:
        return []

    inicio_dia = franjas[0][0]
    fin_dia = franjas[-1][1]
    ocupadas = citas_bloqueantes(profesional.id, inicio_dia, fin_dia, excluir_cita_id)

    huecos = []
    for f_ini, f_fin in franjas:
        cursor = f_ini
        pegado_izq = False  # el borde izquierdo del hueco es una cita (no el inicio de la franja)
        for c in ocupadas:
            if c.fin <= f_ini or c.inicio >= f_fin:
                continue
            if c.inicio > cursor:
                huecos.append((cursor, c.inicio, pegado_izq, True))
            cursor = max(cursor, c.fin)
            pegado_izq = True
        if cursor < f_fin:
            huecos.append((cursor, f_fin, pegado_izq, False))
    return huecos


# --- Forma primitiva de la heuristica (evidencia del Entregable 3) ------------

def sugerir_hueco(inicio_jornada, fin_jornada, duracion_min, citas_ocupadas):
    """
    Devuelve el primer hueco valido (greedy) o None si no hay espacio.

    Es la forma minima de la heuristica, la que documenta el informe: recorre las
    citas ocupadas en orden y devuelve el primer instante donde cabe la duracion
    pedida dentro de la jornada.

    - inicio_jornada, fin_jornada: datetime con los limites del dia.
    - duracion_min: duracion de la cita nueva, en minutos.
    - citas_ocupadas: iterable de dicts con las claves 'inicio' y 'fin' (datetime).

    `sugerir_slots()` generaliza esta idea al caso real (varias franjas de
    atencion, varios dias y desempate por fragmentacion); esta version se
    conserva porque es la unidad basica que se prueba y se explica en el informe.
    """
    candidato = inicio_jornada
    duracion = timedelta(minutes=duracion_min)
    ocupadas = sorted(citas_ocupadas, key=lambda c: c["inicio"])

    for c in ocupadas:
        # Cabe entera antes de la siguiente cita ocupada: ese es el hueco.
        if candidato + duracion <= c["inicio"]:
            return candidato
        # Se solapa con ella: el candidato salta al final de esa cita.
        if candidato < c["fin"]:
            candidato = c["fin"]

    # Ultimo tramo: entre la ultima cita y el cierre de la jornada.
    if candidato + duracion <= fin_jornada:
        return candidato
    return None


# --- Verificacion de disponibilidad (usada al crear/mover una cita) -----------

def verificar_disponibilidad(profesional, inicio: datetime, fin: datetime, excluir_cita_id=None):
    """
    Valida una cita concreta contra la agenda.
    Devuelve dict con `disponible`, `conflictos` y `fuera_de_horario`.
    """
    conflictos = [
        c for c in citas_bloqueantes(profesional.id, inicio, fin, excluir_cita_id)
        if _solapan(inicio, fin, c.inicio, c.fin)
    ]

    dentro = any(
        f_ini <= inicio and fin <= f_fin
        for f_ini, f_fin in franjas_de_atencion(profesional, inicio.date())
    )

    return {
        "disponible": not conflictos and dentro,
        "fuera_de_horario": not dentro,
        "conflictos": [c.to_dict() for c in conflictos],
    }


# --- Slots libres de un dia (para pintar el calendario) -----------------------

def slots_del_dia(profesional, dia, duracion_min=None, granularidad=GRANULARIDAD_DEFECTO):
    """Lista de inicios libres donde cabe una cita de `duracion_min` ese dia."""
    duracion = duracion_min or profesional.duracion_cita_min
    paso = timedelta(minutes=granularidad)
    dur = timedelta(minutes=duracion)

    slots = []
    for h_ini, h_fin, _pi, _pd in huecos_libres(profesional, dia):
        cursor = h_ini
        while cursor + dur <= h_fin:
            slots.append({
                "inicio": cursor.isoformat(),
                "fin": (cursor + dur).isoformat(),
            })
            cursor += paso
    return slots


# --- Heuristica greedy de sugerencia -----------------------------------------

def _candidatos_en_hueco(hueco, duracion_min, granularidad, ahora):
    """
    Genera los inicios posibles dentro de un hueco y calcula su fragmentacion residual.

    Fragmentacion = minutos del hueco que quedan a izquierda y/o derecha de la cita
    y que son demasiado cortos para alojar otra cita de la misma duracion.
    """
    h_ini, h_fin, _pegado_izq, _pegado_der = hueco
    dur = timedelta(minutes=duracion_min)
    paso = timedelta(minutes=granularidad)
    total_min = (h_fin - h_ini).total_seconds() / 60

    candidatos = []
    cursor = h_ini
    while cursor + dur <= h_fin:
        if cursor < ahora:
            cursor += paso
            continue

        resto_izq = (cursor - h_ini).total_seconds() / 60
        resto_der = (h_fin - (cursor + dur)).total_seconds() / 60

        # Un resto solo es "desperdicio" si no alcanza para otra cita completa
        fragmentacion = 0.0
        if 0 < resto_izq < duracion_min:
            fragmentacion += resto_izq
        if 0 < resto_der < duracion_min:
            fragmentacion += resto_der

        candidatos.append({
            "inicio": cursor,
            "fin": cursor + dur,
            "fragmentacion": fragmentacion,
            "hueco_min": total_min,
            "pegado": resto_izq == 0 or resto_der == 0,
        })
        cursor += paso
    return candidatos


def sugerir_slots(profesional, duracion_min=None, desde=None, horizonte_dias=HORIZONTE_DIAS_DEFECTO,
                  limite=5, granularidad=GRANULARIDAD_DEFECTO, solo_dia=None):
    """
    Devuelve los `limite` mejores huecos segun la heuristica greedy.

    `solo_dia` (date) restringe la busqueda a una fecha concreta.
    """
    duracion = duracion_min or profesional.duracion_cita_min
    ahora = datetime.now()
    desde = desde or ahora
    if desde < ahora:
        desde = ahora

    if solo_dia:
        dias = [solo_dia]
    else:
        dias = [desde.date() + timedelta(days=i) for i in range(horizonte_dias)]

    evaluados = []
    for dia in dias:
        for hueco in huecos_libres(profesional, dia):
            limite_inferior = max(desde, datetime.combine(dia, time.min))
            for cand in _candidatos_en_hueco(hueco, duracion, granularidad, limite_inferior):
                espera_min = (cand["inicio"] - desde).total_seconds() / 60
                costo = PESO_ESPERA * espera_min + PESO_FRAGMENTACION * cand["fragmentacion"]
                evaluados.append({
                    "inicio": cand["inicio"].isoformat(),
                    "fin": cand["fin"].isoformat(),
                    "profesional_id": profesional.id,
                    "profesional_nombre": profesional.nombre_completo,
                    "duracion_min": duracion,
                    "espera_min": round(espera_min),
                    "fragmentacion_min": round(cand["fragmentacion"]),
                    "compacta": cand["pegado"],
                    "costo": round(costo, 2),
                })

    # Greedy: se toman los de menor costo; a igual costo, el mas temprano.
    evaluados.sort(key=lambda s: (s["costo"], s["inicio"]))
    mejores = evaluados[:limite]
    for i, s in enumerate(mejores):
        s["ranking"] = i + 1
        s["motivo"] = _explicar(s)
    return mejores


def _explicar(slot):
    """Texto corto que justifica la sugerencia (transparencia del motor para el usuario)."""
    partes = []
    if slot["espera_min"] < 60:
        partes.append("disponible de inmediato")
    elif slot["espera_min"] < 60 * 24:
        partes.append(f"en aprox. {round(slot['espera_min'] / 60)} h")
    else:
        partes.append(f"en {round(slot['espera_min'] / 60 / 24)} dia(s)")

    if slot["fragmentacion_min"] == 0:
        partes.append("no deja huecos muertos")
    else:
        partes.append(f"deja {slot['fragmentacion_min']} min sin uso")

    if slot["compacta"]:
        partes.append("compacta la agenda")
    return "; ".join(partes)


def sugerir_multiprofesional(profesionales, duracion_min=None, desde=None,
                             horizonte_dias=HORIZONTE_DIAS_DEFECTO, limite=5, solo_dia=None):
    """
    Aplica la heuristica sobre varios profesionales y mezcla los resultados por costo.

    `solo_dia` (date) restringe la busqueda a una fecha concreta, igual que en
    `sugerir_slots`: sin propagarlo, pedir un dia sin elegir profesional lo ignoraba.
    """
    todos = []
    for p in profesionales:
        todos.extend(sugerir_slots(p, duracion_min, desde, horizonte_dias,
                                   limite=limite, solo_dia=solo_dia))
    todos.sort(key=lambda s: (s["costo"], s["inicio"]))
    mejores = todos[:limite]
    for i, s in enumerate(mejores):
        s["ranking"] = i + 1
    return mejores


# --- Reacomodo de la agenda de un dia (RF4) ----------------------------------
#
# El motor no solo elige el hueco de una cita nueva: tambien puede recompactar
# la agenda de un dia que ya quedo fragmentada por cancelaciones y reprogramaciones.
# Se usa un greedy de primer ajuste (first-fit) que conserva el orden cronologico
# de las citas y las adelanta lo maximo posible dentro de las franjas de atencion.

# Estados de una cita que permiten moverla al reacomodar
ESTADOS_MOVIBLES = ("pendiente", "confirmada")

# Cuanto se puede adelantar una cita respecto de su hora original. Es un limite de
# negocio, no tecnico: el paciente ya organizo su dia en torno a la hora pactada,
# asi que adelantarlo demasiado convierte una mejora de agenda en una molestia.
MAX_ADELANTO_MIN = 120


def metricas_dia(profesional, dia, ocupadas=None):
    """
    Calidad de la agenda de un dia.

    - minutos_muertos: tiempo libre repartido en huecos demasiado cortos para
      alojar otra cita; es tiempo clinico que no se puede vender.
    - hueco_util_max: bloque libre contiguo mas grande (capacidad real restante).
    - fragmentacion: porcentaje del tiempo libre que es tiempo muerto.
    """
    duracion = profesional.duracion_cita_min
    franjas = franjas_de_atencion(profesional, dia)
    if ocupadas is None:
        ocupadas = [(c.inicio, c.fin) for c in _citas_del_dia(profesional, dia)]
    ocupadas = sorted(ocupadas)

    capacidad = sum((f - i).total_seconds() / 60 for i, f in franjas)
    usados = 0
    libres = []

    for f_ini, f_fin in franjas:
        cursor = f_ini
        for o_ini, o_fin in ocupadas:
            if o_fin <= f_ini or o_ini >= f_fin:
                continue
            if o_ini > cursor:
                libres.append((o_ini - cursor).total_seconds() / 60)
            usados += (min(o_fin, f_fin) - max(o_ini, f_ini)).total_seconds() / 60
            cursor = max(cursor, o_fin)
        if cursor < f_fin:
            libres.append((f_fin - cursor).total_seconds() / 60)

    muertos = sum(h for h in libres if 0 < h < duracion)
    libre_total = sum(libres)

    return {
        "capacidad_min": int(capacidad),
        "ocupado_min": int(usados),
        "libre_min": int(libre_total),
        "minutos_muertos": int(muertos),
        "hueco_util_max": int(max(libres)) if libres else 0,
        "huecos": len(libres),
        "citas": len(ocupadas),
        "ocupacion": round(usados / capacidad * 100, 1) if capacidad else 0.0,
        "fragmentacion": round(muertos / libre_total * 100, 1) if libre_total else 0.0,
    }


def _citas_del_dia(profesional, dia):
    inicio = datetime.combine(dia, time.min)
    return Cita.query.filter(
        Cita.profesional_id == profesional.id,
        Cita.estado.in_(ESTADOS_BLOQUEANTES),
        Cita.inicio >= inicio,
        Cita.inicio < inicio + timedelta(days=1),
    ).order_by(Cita.inicio).all()


def _compromisos_paciente(paciente_ids, dia, excluir_ids):
    """
    Citas de esos pacientes ese dia con OTROS profesionales.
    Al mover una cita hay que respetarlas: un paciente no se clona.
    """
    if not paciente_ids:
        return {}
    inicio = datetime.combine(dia, time.min)
    q = Cita.query.filter(
        Cita.paciente_id.in_(list(paciente_ids)),
        Cita.estado.in_(ESTADOS_BLOQUEANTES),
        Cita.inicio >= inicio,
        Cita.inicio < inicio + timedelta(days=1),
    )
    if excluir_ids:
        q = q.filter(~Cita.id.in_(list(excluir_ids)))

    mapa = {}
    for c in q.all():
        mapa.setdefault(c.paciente_id, []).append((c.inicio, c.fin))
    return mapa


def proponer_reacomodo(profesional, dia, ahora=None, max_adelanto_min=MAX_ADELANTO_MIN,
                       solo_pendientes=False):
    """
    Propone una nueva distribucion de las citas movibles del dia.

    Greedy de primer ajuste: recorre las citas en su orden cronologico actual y
    coloca cada una en el primer instante libre posible dentro de las franjas de
    atencion, sin invadir las citas fijas ni los compromisos del paciente.

    Son FIJAS las citas ya atendidas, las que ya empezaron y las canceladas.
    Son MOVIBLES las pendientes o confirmadas que todavia no han comenzado.

    Restricciones de negocio (no son tecnicas, son del dominio):

    - `max_adelanto_min` limita cuanto se puede adelantar una cita. Una agenda
      matematicamente optima que adelante a un paciente tres horas es inutil:
      el paciente ya organizo su dia en torno a esa hora.
    - `solo_pendientes` excluye las citas ya confirmadas, que son las que mas
      molestia causa mover porque el paciente ya dijo que si a esa hora.

    Devuelve la propuesta sin tocar la base de datos (vista previa).
    """
    ahora = ahora or datetime.now()
    franjas = franjas_de_atencion(profesional, dia)
    citas = _citas_del_dia(profesional, dia)

    if not franjas:
        return _propuesta_vacia(profesional, dia, "El profesional no atiende ese dia.")
    if not citas:
        return _propuesta_vacia(profesional, dia, "No hay citas agendadas ese dia.")

    estados_movibles = ("pendiente",) if solo_pendientes else ESTADOS_MOVIBLES

    fijas, movibles = [], []
    for c in citas:
        if c.estado in estados_movibles and c.inicio > ahora:
            movibles.append(c)
        else:
            fijas.append(c)

    antes = metricas_dia(profesional, dia, [(c.inicio, c.fin) for c in citas])

    if not movibles:
        return _propuesta_vacia(
            profesional, dia,
            "No hay citas movibles: todas ya empezaron o estan cerradas.", antes,
        )

    compromisos = _compromisos_paciente(
        {c.paciente_id for c in movibles}, dia, {c.id for c in movibles}
    )

    # Obstaculos iniciales: las citas que no se pueden mover
    ocupadas = sorted((c.inicio, c.fin) for c in fijas)
    movimientos = []
    posiciones = []

    tope = timedelta(minutes=max_adelanto_min) if max_adelanto_min else None

    for cita in sorted(movibles, key=lambda c: c.inicio):
        duracion = timedelta(minutes=cita.duracion_min)
        bloqueos = sorted(ocupadas + compromisos.get(cita.paciente_id, []))

        # No se busca antes del tope de adelanto permitido para esta cita
        limite_inferior = max(ahora, cita.inicio - tope) if tope else ahora
        destino = _primer_hueco(franjas, bloqueos, duracion, limite_inferior)

        # Si no cabe en ningun sitio, o si quedaria mas tarde que ahora,
        # se respeta su posicion original: el reacomodo nunca retrasa a nadie.
        if destino is None or destino >= cita.inicio:
            destino = cita.inicio

        ocupadas = sorted(ocupadas + [(destino, destino + duracion)])
        posiciones.append((destino, destino + duracion))

        if destino != cita.inicio:
            movimientos.append({
                "cita_id": cita.id,
                "paciente": cita.paciente.nombre_completo if cita.paciente else "—",
                "telefono": cita.paciente.telefono if cita.paciente else None,
                "estado": cita.estado,
                "duracion_min": cita.duracion_min,
                "inicio_actual": cita.inicio.isoformat(),
                "inicio_propuesto": destino.isoformat(),
                "fin_propuesto": (destino + duracion).isoformat(),
                "minutos_adelanto": int((cita.inicio - destino).total_seconds() // 60),
                # Una cita ya confirmada exige volver a llamar al paciente
                "requiere_aviso": cita.estado == "confirmada",
            })

    despues = metricas_dia(
        profesional, dia, [(c.inicio, c.fin) for c in fijas] + posiciones
    )

    # Red de seguridad de la heuristica: compactar a la izquierda es voraz y con
    # citas de distinta duracion puede fragmentar mas de lo que repara (deja
    # residuos cortos entre cita y cita). Si la propuesta no mejora el tiempo
    # muerto, no se ofrece: molestar a los pacientes solo se justifica si el
    # consultorio gana algo con ello.
    if despues["minutos_muertos"] > antes["minutos_muertos"]:
        return _propuesta_vacia(
            profesional, dia,
            "Reacomodar este dia dejaria mas tiempo muerto del que hay ahora "
            f"({despues['minutos_muertos']} min frente a {antes['minutos_muertos']} min). "
            "Se mantiene la distribucion actual.",
            antes,
        )

    return {
        "fecha": dia.isoformat(),
        "profesional_id": profesional.id,
        "profesional": profesional.nombre_completo,
        "citas_totales": len(citas),
        "citas_fijas": len(fijas),
        "citas_movibles": len(movibles),
        "movimientos": movimientos,
        "antes": antes,
        "despues": despues,
        "mejora": {
            "minutos_muertos_recuperados": antes["minutos_muertos"] - despues["minutos_muertos"],
            "hueco_util_ganado": despues["hueco_util_max"] - antes["hueco_util_max"],
            "fragmentacion_reducida_pp": round(antes["fragmentacion"] - despues["fragmentacion"], 1),
        },
        "aplicable": bool(movimientos),
        "mensaje": None if movimientos else "La agenda de ese dia ya esta compactada.",
    }


def _primer_hueco(franjas, bloqueos, duracion, ahora):
    """Primer instante libre donde cabe `duracion`, recorriendo las franjas en orden."""
    for f_ini, f_fin in franjas:
        cursor = max(f_ini, ahora)
        # Alinea al minuto siguiente para no proponer horas con segundos
        if cursor.second or cursor.microsecond:
            cursor = cursor.replace(second=0, microsecond=0) + timedelta(minutes=1)

        while cursor + duracion <= f_fin:
            choque = next(
                (b for b in bloqueos if _solapan(cursor, cursor + duracion, b[0], b[1])),
                None,
            )
            if choque is None:
                return cursor
            cursor = choque[1]      # salta hasta el final del obstaculo
    return None


def _propuesta_vacia(profesional, dia, mensaje, antes=None):
    vacio = antes or metricas_dia(profesional, dia)
    return {
        "fecha": dia.isoformat(),
        "profesional_id": profesional.id,
        "profesional": profesional.nombre_completo,
        "citas_totales": vacio["citas"],
        "citas_fijas": vacio["citas"],
        "citas_movibles": 0,
        "movimientos": [],
        "antes": vacio,
        "despues": vacio,
        "mejora": {"minutos_muertos_recuperados": 0, "hueco_util_ganado": 0,
                   "fragmentacion_reducida_pp": 0.0},
        "aplicable": False,
        "mensaje": mensaje,
    }


def verificar_integridad_dia(profesional, dia):
    """
    Comprueba que tras aplicar un reacomodo no quedaron solapamientos.
    Se usa como red de seguridad antes de confirmar la transaccion.
    """
    citas = _citas_del_dia(profesional, dia)
    for anterior, siguiente in zip(citas, citas[1:]):
        if _solapan(anterior.inicio, anterior.fin, siguiente.inicio, siguiente.fin):
            return f"Las citas #{anterior.id} y #{siguiente.id} quedarian solapadas"

    for c in citas:
        choque = Cita.query.filter(
            Cita.paciente_id == c.paciente_id,
            Cita.id != c.id,
            Cita.estado.in_(ESTADOS_BLOQUEANTES),
            Cita.inicio < c.fin,
            Cita.fin > c.inicio,
        ).first()
        if choque:
            return f"El paciente de la cita #{c.id} quedaria con dos citas a la vez"
    return None


# --- Ocupacion (insumo del panel RF6) ----------------------------------------

def minutos_disponibles(profesional, desde_dia, hasta_dia):
    """Capacidad teorica del profesional (suma de sus franjas) en el rango de dias."""
    total = 0
    dia = desde_dia
    while dia <= hasta_dia:
        for f_ini, f_fin in franjas_de_atencion(profesional, dia):
            total += (f_fin - f_ini).total_seconds() / 60
        dia += timedelta(days=1)
    return int(total)
