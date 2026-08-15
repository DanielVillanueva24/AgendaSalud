"""Extensiones compartidas (evita imports circulares entre app y models)."""
import sqlite3

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def _ajustar_sqlite(conexion, _registro):
    """
    Prepara cada conexion SQLite para que la app aguante varios usuarios a la vez.

    SQLite en su modo por defecto (journal `delete`) toma un bloqueo exclusivo de
    TODA la base mientras escribe: si recepcion guarda una cita justo cuando el
    profesional consulta su agenda, el segundo se queda esperando y a los pocos
    segundos revienta con "database is locked". Es exactamente el sintoma de que
    el sistema se cuelga cuando lo usa mas de una persona.

    - WAL (write-ahead logging): las lecturas ya no esperan a las escrituras, asi
      que consultar la agenda nunca se bloquea contra un guardado en curso.
    - busy_timeout: si dos escrituras coinciden, la segunda reintenta durante 15 s
      en vez de fallar al instante.
    - foreign_keys: SQLite las ignora salvo que se activen por conexion; sin esto
      los ON DELETE CASCADE de los modelos no se aplican.

    En PostgreSQL (la nube) no se toca nada: no es la conexion de SQLite.
    """
    if not isinstance(conexion, sqlite3.Connection):
        return

    cursor = conexion.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()
