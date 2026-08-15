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

    MAIL_USERNAME   cuenta de Gmail que envia; es tambien el remitente
    MAIL_PASSWORD   contrasena de aplicacion de 16 caracteres (no la normal)

Para comprobar que la configuracion funciona sin esperar a que haya una cita:

    flask --app app probar-correo destino@ejemplo.com
"""
import atexit
import logging
import smtplib
import threading
from datetime import date, datetime, time, timedelta

from flask import current_app

logger = logging.getLogger("agendasalud.notificaciones")

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
    elif app.config.get("NOTIFICACIONES_ACTIVAS"):
        logger.warning(
            "Hay credenciales de correo pero Flask-Mail no esta instalado; "
            "ejecuta: pip install -r requirements.txt"
        )

    if app.config.get("TESTING"):
        pass                                     # en pruebas se levanta una app por caso
    elif app.config.get("NOTIFICACIONES_ACTIVAS"):
        logger.info("Notificaciones por correo activas via SMTP de %s (remitente: %s)",
                    app.config.get("MAIL_SERVER"), app.config.get("MAIL_USERNAME"))
    else:
        logger.info("Notificaciones en modo simulado: define MAIL_USERNAME y "
                    "MAIL_PASSWORD (contrasena de aplicacion de Gmail) para enviar")

    # La tarea diaria no se arranca aqui: quien crea la app no sabe todavia si
    # este proceso es el definitivo o el padre del recargador. Lo decide app.py.


# --- Envio --------------------------------------------------------------------

def _enviar(app, destinatario, asunto, cuerpo):
    """
    Envia un correo por el SMTP de Gmail y devuelve ENVIADO / SIMULADO / ERROR.

    Nunca lanza: un fallo de correo no puede tumbar la peticion que agendo la
    cita. El motivo del fallo queda siempre en el log.
    """
    if not destinatario:
        return ERROR

    # En pruebas y sin credenciales no se toca la red
    if app.config.get("TESTING") or not app.config.get("NOTIFICACIONES_ACTIVAS"):
        logger.info("[correo simulado] para=%s asunto=%s", destinatario, asunto)
        return SIMULADO

    if mail is None:
        logger.info("[correo simulado] Flask-Mail no esta instalado; para=%s", destinatario)
        return SIMULADO

    # El From es la cuenta autenticada (ver config.py): Gmail lo reescribiria
    # de todos modos. Solo el nombre visible es configurable.
    remitente = (app.config.get("MAIL_SENDER_NAME", "AgendaSalud"),
                 app.config["MAIL_USERNAME"])

    try:
        with app.app_context():
            mail.send(Message(
                subject=asunto,
                recipients=[destinatario],
                body=cuerpo,
                sender=remitente,
            ))
        logger.info("Correo enviado a %s (%s)", destinatario, asunto)
        return ENVIADO

    # Ojo al orden: smtplib.SMTPException hereda de OSError, asi que si el
    # `except OSError` fuera antes se tragaria los rechazos de Gmail y los
    # explicaria como si fueran un puerto bloqueado.
    except smtplib.SMTPAuthenticationError as err:
        logger.error(
            "Gmail rechazo las credenciales de %s (%s). MAIL_PASSWORD tiene que ser "
            "una contrasena de aplicacion de 16 caracteres, no la contrasena normal, "
            "y la cuenta necesita la verificacion en dos pasos activada.",
            app.config.get("MAIL_USERNAME"), err,
        )
        return ERROR
    except smtplib.SMTPException as err:
        # Destinatario invalido, limite de envio diario, mensaje rechazado...
        logger.error("Gmail rechazo el correo a %s: %s", destinatario, err)
        return ERROR
    except OSError as err:
        # Aqui ya solo quedan los fallos de socket: "Network is unreachable" o un
        # timeout al abrir la conexion. La maquina no deja salir trafico SMTP; no
        # es un problema de credenciales y no se arregla con configuracion, porque
        # el paquete no llega ni a salir. Es tipico de los planes gratuitos de
        # PaaS, que bloquean los puertos 25, 465 y 587.
        logger.error(
            "No se pudo abrir la conexion SMTP con %s:%s (%s). Si ocurre en la nube, "
            "el proveedor esta bloqueando el puerto de salida; en un plan gratuito "
            "no hay forma de enviar por SMTP.",
            app.config.get("MAIL_SERVER"), app.config.get("MAIL_PORT"), err,
        )
        return ERROR
    except Exception:
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


_PRUEBA = """Esto es un correo de prueba de AgendaSalud.

Si lo estas leyendo, la cuenta de Gmail y la contrasena de aplicacion estan
bien configuradas y los pacientes recibiran sus confirmaciones y recordatorios.

    Servidor  : {servidor}:{puerto}
    Remitente : {remitente}
    Enviado   : {momento}

--
AgendaSalud
Este es un mensaje automatico, no es necesario responderlo.
"""


def enviar_prueba(destinatario, app=None):
    """
    Manda un correo de prueba a la direccion indicada.

    Existe para poder verificar las credenciales sin depender de que haya una
    cita con un paciente con correo. Envia en primer plano (no en un hilo) para
    que el resultado sea el de verdad y no un "enviado" optimista, y devuelve un
    diccionario con el diagnostico en vez de solo el estado: cuando falla, lo
    util es saber con que cuenta y contra que servidor se intento.

    Uso desde la linea de comandos:

        flask --app app probar-correo destino@ejemplo.com
    """
    app = app or current_app._get_current_object()
    destinatario = (destinatario or "").strip()

    diagnostico = {
        "destinatario": destinatario,
        "servidor": app.config.get("MAIL_SERVER"),
        "puerto": app.config.get("MAIL_PORT"),
        "remitente": app.config.get("MAIL_USERNAME"),
        "notificaciones_activas": bool(app.config.get("NOTIFICACIONES_ACTIVAS")),
        "flask_mail_instalado": mail is not None,
    }

    if not destinatario or "@" not in destinatario:
        diagnostico["estado"] = ERROR
        diagnostico["detalle"] = "Indica una direccion de correo valida."
        return diagnostico

    diagnostico["estado"] = _enviar(
        app, destinatario,
        "Prueba de configuracion de AgendaSalud",
        _PRUEBA.format(
            servidor=app.config.get("MAIL_SERVER"),
            puerto=app.config.get("MAIL_PORT"),
            remitente=app.config.get("MAIL_USERNAME") or "(sin configurar)",
            momento=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        ),
    )

    if diagnostico["estado"] == SIMULADO:
        diagnostico["detalle"] = (
            "No se envio nada: faltan MAIL_USERNAME y MAIL_PASSWORD "
            "(o Flask-Mail no esta instalado). El correo solo se escribio en el log."
        )
    elif diagnostico["estado"] == ERROR:
        diagnostico["detalle"] = "El envio fallo. El motivo exacto esta en el log del servidor."
    else:
        diagnostico["detalle"] = "Correo entregado al servidor de Gmail."
    return diagnostico


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
