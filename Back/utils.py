"""Helpers de parseo y validacion para la capa de API."""
from datetime import datetime, date, time

from flask import request, jsonify


class ErrorAPI(Exception):
    """Error de negocio/validacion con codigo HTTP. Lo captura el handler global."""

    def __init__(self, mensaje, status=400, detalles=None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.status = status
        self.detalles = detalles or {}

    def respuesta(self):
        cuerpo = {"error": self.mensaje}
        if self.detalles:
            cuerpo.update(self.detalles)
        return jsonify(cuerpo), self.status


def body():
    """Cuerpo JSON de la peticion (dict) o error 400."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ErrorAPI("Se esperaba un cuerpo JSON valido")
    return data


def requerido(data, *campos):
    faltantes = [c for c in campos if data.get(c) in (None, "")]
    if faltantes:
        raise ErrorAPI(
            "Faltan campos obligatorios: " + ", ".join(faltantes),
            detalles={"campos": faltantes},
        )


def parse_dt(valor, campo="fecha"):
    """Acepta ISO 8601 con o sin zona; se normaliza a datetime naive local."""
    if isinstance(valor, datetime):
        return valor.replace(tzinfo=None)
    if not valor:
        raise ErrorAPI(f"El campo '{campo}' es obligatorio")
    texto = str(valor).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(texto)
    except ValueError:
        raise ErrorAPI(f"Formato de fecha/hora invalido en '{campo}': {valor}")
    return dt.replace(tzinfo=None)


def parse_fecha(valor, campo="fecha"):
    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor).strip()[:10])
    except ValueError:
        raise ErrorAPI(f"Formato de fecha invalido en '{campo}': {valor}")


def parse_hora(valor, campo="hora"):
    if isinstance(valor, time):
        return valor
    try:
        partes = str(valor).strip().split(":")
        return time(int(partes[0]), int(partes[1]) if len(partes) > 1 else 0)
    except (ValueError, IndexError):
        raise ErrorAPI(f"Formato de hora invalido en '{campo}': {valor} (se espera HH:MM)")


def parse_bool(valor, defecto=False):
    if valor is None:
        return defecto
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ("1", "true", "si", "sí", "yes", "on")


def parse_int(valor, campo="valor", defecto=None):
    if valor in (None, ""):
        return defecto
    try:
        return int(valor)
    except (TypeError, ValueError):
        raise ErrorAPI(f"Se esperaba un numero entero en '{campo}': {valor}")


def limpiar(valor):
    """Normaliza texto de formularios: strip y cadena vacia -> None."""
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None
