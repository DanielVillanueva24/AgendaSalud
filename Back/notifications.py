"""
Notificaciones de AgendaSalud: correos de confirmacion y de recordatorio.

Dos piezas:

  - **Flask-Mail** sobre el SMTP de Gmail, para enviar el correo de
    confirmacion cuando se agenda o se confirma una cita.
  - **APScheduler**, para la tarea diaria que avisa a los pacientes que
    tienen cita al dia siguiente.

Principio de diseno: *la notificacion es accesoria a la agenda*. Si faltan
las credenciales, si Flask-Mail no esta instalado o si Gmail rechaza la
conexion, el sistema lo anota en el log y sigue trabajando. Ninguna cita se
pierde ni ninguna peticion falla por un problema de correo.

Configuracion (variables de entorno, nunca en el codigo):

    MAIL_USERNAME   cuenta de Gmail que envia
    MAIL_PASSWORD   contrasena de aplicacion de 16 caracteres
"""
import atexit
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, time, timedelta

from flask import current_app

logger = logging.getLogger("agendasalud.notificaciones")

_BREVO_URL = "https://api.brevo.com/v3/smtp/email"

# Flask-Mail y APScheduler son opcionales: sin ellos la app arranca igual y
# los correos quedan en el log (modo simulado).
try:
    from flask_mail import Mail, Message
except ImportError:                                  # pragma: no cover
    Mail = Message = None

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:                                  # pragma: no cover
    BackgroundScheduler = None

mail = Mail() if Mail is not None else None
_programador = None

# Resultados posibles de un envio
ENVIADO = "enviado"
SIMULADO = "simulado"      # no hay credenciales: solo se registro en el log
ERROR = "error"

_DIAS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
_MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre")


# --- Registro en la app -------------------------------------------------------

def init_app(app):
    """Conecta Flask-Mail y deja lista la tarea de recordatorios."""
    if mail is not None:
        mail.init_app(app)
    elif app.config.get("NOTIFICACIONES_ACTIVAS") and not app.config.get("BREVO_API_KEY"):
        logger.warning(
            "Hay credenciales de correo pero Flask-Mail no esta instalado; "
            "ejecuta: pip install -r requirements.txt"
        )

    if app.config.get("TESTING"):
        pass                                     # en pruebas se levanta una app por caso
    elif app.config.get("BREVO_API_KEY"):
        logger.info("Notificaciones por correo activas via Brevo (remitente: %s)",
                    app.config.get("MAIL_DEFAULT_SENDER"))
    elif app.config.get("NOTIFICACIONES_ACTIVAS"):
        logger.info("Notificaciones por correo activas via SMTP (%s)", app.config.get("MAIL_USERNAME"))
    else:
        logger.info("Notificaciones en modo simulado: define BREVO_API_KEY (nube) "
                    "o MAIL_USERNAME y MAIL_PASSWORD (local) para enviar")

    # La tarea diaria no se arranca aqui: quien crea la app no sabe todavia si
    # este proceso es el definitivo o el padre del recargador. Lo decide app.py.


# --- Envio --------------------------------------------------------------------

def _enviar(app, destinatario, asunto, cuerpo):
    """Envia un correo y devuelve ENVIADO / SIMULADO / ERROR. Nunca lanza."""
    if not destinatario:
        return ERROR

    # En pruebas y sin credenciales no se toca la red
    if app.config.get("TESTING") or not app.config.get("NOTIFICACIONES_ACTIVAS"):
        logger.info("[correo simulado] para=%s asunto=%s", destinatario, asunto)
        return SIMULADO

    if app.config.get("BREVO_API_KEY"):
        return _enviar_por_brevo(app, destinatario, asunto, cuerpo)
    return _enviar_por_smtp(app, destinatario, asunto, cuerpo)


def _enviar_por_brevo(app, destinatario, asunto, cuerpo):
    """
    Envio por la API HTTPS de Brevo.

    Es la via que funciona en la nube: los planes gratuitos suelen bloquear las
    conexiones salientes a los puertos de SMTP, y el 443 no. Se usa urllib de la
    biblioteca estandar para no anadir dependencias.
    """
    remitente = app.config.get("MAIL_DEFAULT_SENDER") or app.config.get("MAIL_USERNAME")
    if not remitente:
        logger.error("BREVO_API_KEY definida pero falta MAIL_DEFAULT_SENDER (remitente)")
        return ERROR

    carga = json.dumps({
        "sender": {"email": remitente, "name": app.config.get("MAIL_SENDER_NAME", "AgendaSalud")},
        "to": [{"email": destinatario}],
        "subject": asunto,
        "textContent": cuerpo,
    }).encode("utf-8")

    peticion = urllib.request.Request(
        _BREVO_URL,
        data=carga,
        method="POST",
        headers={
            "api-key": app.config["BREVO_API_KEY"],
            "content-type": "application/json",
            "accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(peticion, timeout=20) as respuesta:
            respuesta.read()
        logger.info("Correo enviado a %s via Brevo (%s)", destinatario, asunto)
        return ENVIADO
    except urllib.error.HTTPError as err:
        # Brevo explica el motivo en el cuerpo: remitente sin verificar, clave
        # invalida, cuota agotada... Sin esto solo se veria "HTTP Error 400".
        try:
            detalle = err.read().decode("utf-8", "replace")[:400]
        except Exception:
            detalle = ""
        logger.error("Brevo rechazo el correo a %s: HTTP %s %s",
                     destinatario, err.code, detalle)
        return ERROR
    except Exception:
        logger.exception("No se pudo enviar el correo a %s via Brevo", destinatario)
        return ERROR


def _enviar_por_smtp(app, destinatario, asunto, cuerpo):
    """Envio por SMTP con Flask-Mail. Funciona en local; en la nube suele estar bloqueado."""
    if mail is None:
        logger.info("[correo simulado] Flask-Mail no esta instalado; para=%s", destinatario)
        return SIMULADO

    try:
        with app.app_context():
            mensaje = Message(
                subject=asunto,
                recipients=[destinatario],
                body=cuerpo,
                sender=app.config.get("MAIL_DEFAULT_SENDER") or app.config.get("MAIL_USERNAME"),
            )
            mail.send(mensaje)
        logger.info("Correo enviado a %s (%s)", destinatario, asunto)
        return ENVIADO
    except OSError as err:
        # "Network is unreachable" al abrir el socket: el servidor no deja salir
        # trafico SMTP. No es un problema de credenciales y no se arregla con
        # configuracion; hay que enviar por HTTPS (BREVO_API_KEY).
        logger.error(
            "No se pudo abrir la conexion SMTP con %s:%s (%s). Si esto ocurre en la "
            "nube, el proveedor bloquea el puerto: define BREVO_API_KEY para enviar por HTTPS.",
            app.config.get("MAIL_SERVER"), app.config.get("MAIL_PORT"), err,
        )
        return ERROR
    except Exception:
        # Un fallo de SMTP no puede tumbar la peticion que agendo la cita
        logger.exception("No se pudo enviar el correo a %s", destinatario)
        return ERROR


def _enviar_en_segundo_plano(app, destinatario, asunto, cuerpo):
    """
    Envia sin bloquear la respuesta HTTP.

    Al hilo solo se le pasan cadenas ya extraidas: nunca objetos del ORM, que
    quedarian ligados a la sesion de otro hilo.
    """
    if app.config.get("TESTING") or not app.config.get("NOTIFICACIONES_ACTIVAS"):
        return _enviar(app, destinatario, asunto, cuerpo)

    threading.Thread(
        target=_enviar, args=(app, destinatario, asunto, cuerpo), daemon=True,
    ).start()
    return ENVIADO


# --- Redaccion de los mensajes ------------------------------------------------

def _fecha_larga(momento):
    """'lunes 18 de agosto de 2026' sin depender del locale del sistema."""
    return (f"{_DIAS[momento.weekday()]} {momento.day} de "
            f"{_MESES[momento.month - 1]} de {momento.year}")


def _datos_cita(cita):
    paciente = cita.paciente
    profesional = cita.profesional
    especialidad = (profesional.especialidad or "").strip() if profesional else ""
    return {
        "email": (paciente.email or "").strip() if paciente else "",
        "paciente": paciente.nombre_completo if paciente else "",
        "profesional": profesional.nombre_completo if profesional else "",
        "especialidad": f" ({especialidad})" if especialidad else "",
        "fecha": _fecha_larga(cita.inicio),
        "hora": cita.inicio.strftime("%H:%M"),
        "duracion": cita.duracion_min,
        "motivo": cita.motivo or "consulta general",
    }


_CONFIRMACION = """Hola {paciente}:

Tu cita quedo agendada.

    Profesional : {profesional}{especialidad}
    Fecha       : {fecha}
    Hora        : {hora} ({duracion} minutos)
    Motivo      : {motivo}

Te pedimos llegar unos minutos antes. Si no puedes asistir, avisanos con
anticipacion para ofrecer el espacio a otro paciente.

--
AgendaSalud
Este es un mensaje automatico, no es necesario responderlo.
"""

_RECORDATORIO = """Hola {paciente}:

Te recordamos que manana tienes una cita.

    Profesional : {profesional}{especialidad}
    Fecha       : {fecha}
    Hora        : {hora} ({duracion} minutos)
    Motivo      : {motivo}

Si no puedes asistir, avisanos hoy mismo para ofrecer el espacio a otro
paciente.

--
AgendaSalud
Este es un mensaje automatico, no es necesario responderlo.
"""


# --- API publica --------------------------------------------------------------

def enviar_confirmacion(cita, app=None):
    """Correo de confirmacion: al crear la cita o al pasarla a `confirmada`."""
    app = app or current_app._get_current_object()
    datos = _datos_cita(cita)
    if not datos["email"]:
        logger.info("Cita %s: el paciente no tiene correo registrado", cita.id)
        return ERROR
    return _enviar_en_segundo_plano(
        app, datos["email"],
        f"Cita agendada: {datos['fecha']} a las {datos['hora']}",
        _CONFIRMACION.format(**datos),
    )


def enviar_recordatorio(cita, app=None, en_segundo_plano=True):
    """Correo de recordatorio de una cita concreta."""
    app = app or current_app._get_current_object()
    datos = _datos_cita(cita)
    if not datos["email"]:
        return ERROR
    asunto = f"Recordatorio de tu cita: {datos['fecha']} a las {datos['hora']}"
    cuerpo = _RECORDATORIO.format(**datos)
    if en_segundo_plano:
        return _enviar_en_segundo_plano(app, datos["email"], asunto, cuerpo)
    return _enviar(app, datos["email"], asunto, cuerpo)


# --- Tarea diaria de recordatorios (APScheduler) ------------------------------

def citas_a_recordar(dia=None):
    """
    Citas vigentes del dia indicado (por defecto, manana).

    El orden no es cronologico sino por riesgo: el README pide usar el
    historial de ausentismo del paciente para priorizar los recordatorios, asi
    que quien mas ha faltado se contacta primero. El orden por hora se
    conserva dentro de cada nivel de riesgo (`sort` es estable).
    """
    from models import Cita

    dia = dia or date.today() + timedelta(days=1)
    citas = (
        Cita.query
        .filter(
            Cita.inicio >= datetime.combine(dia, time.min),
            Cita.inicio <= datetime.combine(dia, time.max),
            Cita.estado.in_(("pendiente", "confirmada")),
        )
        .order_by(Cita.inicio)
        .all()
    )
    citas.sort(
        key=lambda c: c.paciente.estadisticas_asistencia()["tasa_ausentismo"] if c.paciente else 0,
        reverse=True,
    )
    return citas


def enviar_recordatorios(app=None, dia=None):
    """
    Tarea diaria: avisa a los pacientes con cita manana.

    Devuelve un resumen con el detalle de lo ocurrido. Solo se apunta el
    recordatorio en la cita cuando el correo salio de verdad, para no inflar
    el indicador de contactos con envios simulados.
    """
    app = app or current_app._get_current_object()

    with app.app_context():
        from extensions import db

        objetivo = dia or date.today() + timedelta(days=1)
        resumen = {"fecha": objetivo.isoformat(), "citas": 0, "enviados": 0,
                   "simulados": 0, "errores": 0, "sin_correo": 0, "ya_avisadas": 0}
        hoy = date.today()

        for cita in citas_a_recordar(objetivo):
            resumen["citas"] += 1

            # Si la tarea corre dos veces el mismo dia, no se duplica el aviso
            if cita.ultimo_recordatorio_en and cita.ultimo_recordatorio_en.date() == hoy:
                resumen["ya_avisadas"] += 1
                continue

            datos = _datos_cita(cita)
            if not datos["email"]:
                resumen["sin_correo"] += 1
                continue

            estado = enviar_recordatorio(cita, app=app, en_segundo_plano=False)
            if estado == ENVIADO:
                resumen["enviados"] += 1
                cita.recordatorios_enviados = (cita.recordatorios_enviados or 0) + 1
                cita.ultimo_recordatorio_en = datetime.now()
            elif estado == SIMULADO:
                resumen["simulados"] += 1
            else:
                resumen["errores"] += 1

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("No se pudo registrar el envio de recordatorios")
        finally:
            db.session.remove()

    logger.info("Recordatorios para %(fecha)s: %(enviados)s enviados, "
                "%(simulados)s simulados, %(errores)s con error, "
                "%(sin_correo)s sin correo", resumen)
    return resumen


def iniciar_programador(app):
    """Programa la tarea diaria. Devuelve el programador, o None si no aplica."""
    global _programador

    if app.config.get("TESTING") or not app.config.get("RECORDATORIOS_ACTIVOS"):
        return None
    if BackgroundScheduler is None:
        logger.warning("APScheduler no esta instalado: no habra recordatorios automaticos")
        return None
    if _programador is not None:
        return _programador

    hora = app.config.get("RECORDATORIO_HORA", 18)
    minuto = app.config.get("RECORDATORIO_MINUTO", 0)

    _programador = BackgroundScheduler(daemon=True)
    _programador.add_job(
        func=lambda: enviar_recordatorios(app),
        trigger="cron", hour=hora, minute=minuto,
        id="recordatorios_diarios", replace_existing=True,
    )
    _programador.start()
    atexit.register(lambda: _programador.shutdown(wait=False))
    logger.info("Recordatorios diarios programados a las %02d:%02d", hora, minuto)
    return _programador
