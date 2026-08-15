"""Modelos SQLAlchemy de AgendaSalud (RF1, RF2, RF3, RF5)."""
from datetime import datetime, date, time

from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db

# --- Vocabularios del dominio -------------------------------------------------

ROLES = ("admin", "recepcion", "profesional")

ESTADOS_CITA = ("pendiente", "confirmada", "cancelada", "atendida", "no_asistio")

# Estados que reservan fisicamente el hueco en la agenda (RF4: deteccion de solapamiento).
# Solo cancelar libera el horario. Una inasistencia NO lo libera: la cita sigue
# ocupando esa casilla en el historico y, si se permitiera agendar encima, el
# calendario acabaria pintando dos citas superpuestas en el mismo hueco.
ESTADOS_OCUPAN_AGENDA = ("pendiente", "confirmada", "atendida", "no_asistio")

# Estados que cuentan como tiempo clinico aprovechado (RF6: indicador de ocupacion).
# Aqui si se excluye `no_asistio`: el hueco estaba reservado, pero no se uso.
ESTADOS_BLOQUEANTES = ("pendiente", "confirmada", "atendida")

# Estados que cuentan como "cita que debio ocurrir" (RF6: tasa de ausentismo)
ESTADOS_CERRADOS = ("atendida", "no_asistio")

DIAS_SEMANA = ("Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo")


def _iso(valor):
    return valor.isoformat() if valor is not None else None


# --- RF1: usuarios y roles ----------------------------------------------------

class Usuario(db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    rol = db.Column(db.String(20), nullable=False, default="recepcion", index=True)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    creado_en = db.Column(db.DateTime, nullable=False, default=datetime.now)
    ultimo_acceso = db.Column(db.DateTime)

    # Si el usuario es un profesional, queda ligado a su ficha profesional
    profesional_id = db.Column(db.Integer, db.ForeignKey("profesionales.id"), index=True)
    profesional = db.relationship("Profesional", back_populates="usuario")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            "id": self.id,
            "nombre": self.nombre,
            "email": self.email,
            "rol": self.rol,
            "activo": self.activo,
            "profesional_id": self.profesional_id,
            "creado_en": _iso(self.creado_en),
            "ultimo_acceso": _iso(self.ultimo_acceso),
        }


# --- RF2: pacientes y profesionales -------------------------------------------

class Paciente(db.Model):
    __tablename__ = "pacientes"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(80), nullable=False)
    apellido = db.Column(db.String(80), nullable=False)
    documento = db.Column(db.String(40), unique=True, index=True)
    telefono = db.Column(db.String(40))
    email = db.Column(db.String(160), index=True)
    fecha_nacimiento = db.Column(db.Date)
    notas = db.Column(db.Text)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    creado_en = db.Column(db.DateTime, nullable=False, default=datetime.now)

    citas = db.relationship("Cita", back_populates="paciente", cascade="all, delete-orphan")

    @property
    def nombre_completo(self):
        return f"{self.nombre} {self.apellido}".strip()

    def estadisticas_asistencia(self):
        """Historial de asistencia del paciente (insumo para RF6 y para el riesgo de ausentismo)."""
        atendidas = sum(1 for c in self.citas if c.estado == "atendida")
        ausencias = sum(1 for c in self.citas if c.estado == "no_asistio")
        cerradas = atendidas + ausencias
        return {
            "atendidas": atendidas,
            "ausencias": ausencias,
            "citas_cerradas": cerradas,
            "tasa_ausentismo": round(ausencias / cerradas * 100, 1) if cerradas else 0.0,
        }

    def to_dict(self, con_estadisticas=False):
        data = {
            "id": self.id,
            "nombre": self.nombre,
            "apellido": self.apellido,
            "nombre_completo": self.nombre_completo,
            "documento": self.documento,
            "telefono": self.telefono,
            "email": self.email,
            "fecha_nacimiento": _iso(self.fecha_nacimiento),
            "notas": self.notas,
            "activo": self.activo,
            "creado_en": _iso(self.creado_en),
        }
        if con_estadisticas:
            data["asistencia"] = self.estadisticas_asistencia()
        return data


class Profesional(db.Model):
    __tablename__ = "profesionales"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(80), nullable=False)
    apellido = db.Column(db.String(80), nullable=False)
    especialidad = db.Column(db.String(120))
    email = db.Column(db.String(160))
    telefono = db.Column(db.String(40))
    duracion_cita_min = db.Column(db.Integer, nullable=False, default=30)
    color = db.Column(db.String(9), nullable=False, default="#2563eb")
    activo = db.Column(db.Boolean, nullable=False, default=True)
    creado_en = db.Column(db.DateTime, nullable=False, default=datetime.now)

    usuario = db.relationship("Usuario", back_populates="profesional", uselist=False)
    horarios = db.relationship(
        "HorarioAtencion", back_populates="profesional",
        cascade="all, delete-orphan", order_by="HorarioAtencion.dia_semana",
    )
    citas = db.relationship("Cita", back_populates="profesional", cascade="all, delete-orphan")

    @property
    def nombre_completo(self):
        return f"{self.nombre} {self.apellido}".strip()

    def to_dict(self, con_horarios=True):
        data = {
            "id": self.id,
            "nombre": self.nombre,
            "apellido": self.apellido,
            "nombre_completo": self.nombre_completo,
            "especialidad": self.especialidad,
            "email": self.email,
            "telefono": self.telefono,
            "duracion_cita_min": self.duracion_cita_min,
            "color": self.color,
            "activo": self.activo,
        }
        if con_horarios:
            data["horarios"] = [h.to_dict() for h in self.horarios]
        return data


class HorarioAtencion(db.Model):
    """Franja semanal recurrente en la que el profesional atiende (base del motor RF4)."""
    __tablename__ = "horarios_atencion"

    id = db.Column(db.Integer, primary_key=True)
    profesional_id = db.Column(
        db.Integer, db.ForeignKey("profesionales.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    dia_semana = db.Column(db.Integer, nullable=False)  # 0 = lunes ... 6 = domingo
    hora_inicio = db.Column(db.Time, nullable=False)
    hora_fin = db.Column(db.Time, nullable=False)

    profesional = db.relationship("Profesional", back_populates="horarios")

    __table_args__ = (
        db.Index("ix_horario_prof_dia", "profesional_id", "dia_semana"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "profesional_id": self.profesional_id,
            "dia_semana": self.dia_semana,
            "dia_nombre": DIAS_SEMANA[self.dia_semana],
            "hora_inicio": self.hora_inicio.strftime("%H:%M"),
            "hora_fin": self.hora_fin.strftime("%H:%M"),
        }


# --- RF3 / RF5: citas ---------------------------------------------------------

class Cita(db.Model):
    __tablename__ = "citas"

    id = db.Column(db.Integer, primary_key=True)
    paciente_id = db.Column(db.Integer, db.ForeignKey("pacientes.id", ondelete="CASCADE"), nullable=False, index=True)
    profesional_id = db.Column(db.Integer, db.ForeignKey("profesionales.id", ondelete="CASCADE"), nullable=False, index=True)

    inicio = db.Column(db.DateTime, nullable=False, index=True)
    fin = db.Column(db.DateTime, nullable=False)

    estado = db.Column(db.String(20), nullable=False, default="pendiente", index=True)
    motivo = db.Column(db.String(200))
    notas = db.Column(db.Text)

    # Trazabilidad del ciclo de vida (RF5 / RF6)
    creada_en = db.Column(db.DateTime, nullable=False, default=datetime.now)
    actualizada_en = db.Column(db.DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
    confirmada_en = db.Column(db.DateTime)
    cerrada_en = db.Column(db.DateTime)          # momento en que se marco atendida / no_asistio
    motivo_cancelacion = db.Column(db.String(200))
    creada_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    sugerida_por_motor = db.Column(db.Boolean, nullable=False, default=False)

    # Recordatorios de confirmacion (RF5): registro de los contactos hechos al
    # paciente antes de la cita, para poder medir su efecto sobre el ausentismo.
    recordatorios_enviados = db.Column(db.Integer, nullable=False, default=0)
    ultimo_recordatorio_en = db.Column(db.DateTime)

    paciente = db.relationship("Paciente", back_populates="citas")
    profesional = db.relationship("Profesional", back_populates="citas")
    creada_por = db.relationship("Usuario", foreign_keys=[creada_por_id])

    __table_args__ = (
        # RNF3: la consulta mas frecuente es "agenda de un profesional en un rango"
        db.Index("ix_cita_prof_inicio", "profesional_id", "inicio"),
        db.Index("ix_cita_estado_inicio", "estado", "inicio"),
    )

    @property
    def duracion_min(self):
        return int((self.fin - self.inicio).total_seconds() // 60)

    @property
    def bloquea_agenda(self):
        return self.estado in ESTADOS_OCUPAN_AGENDA

    def to_dict(self):
        return {
            "id": self.id,
            "paciente_id": self.paciente_id,
            "paciente_nombre": self.paciente.nombre_completo if self.paciente else None,
            "paciente_telefono": self.paciente.telefono if self.paciente else None,
            "profesional_id": self.profesional_id,
            "profesional_nombre": self.profesional.nombre_completo if self.profesional else None,
            "profesional_color": self.profesional.color if self.profesional else "#2563eb",
            "inicio": _iso(self.inicio),
            "fin": _iso(self.fin),
            "duracion_min": self.duracion_min,
            "estado": self.estado,
            "motivo": self.motivo,
            "notas": self.notas,
            "motivo_cancelacion": self.motivo_cancelacion,
            "sugerida_por_motor": self.sugerida_por_motor,
            "recordatorios_enviados": self.recordatorios_enviados,
            "ultimo_recordatorio_en": _iso(self.ultimo_recordatorio_en),
            "confirmada_en": _iso(self.confirmada_en),
            "cerrada_en": _iso(self.cerrada_en),
            "creada_en": _iso(self.creada_en),
            "creada_por": self.creada_por.nombre if self.creada_por else None,
            # Las citas de dias ya pasados son historico: se consultan, no se tocan
            "editable": self.es_editable,
        }

    @property
    def es_editable(self):
        """Solo el dia en curso y el futuro admiten cambios (ver routes/citas.py)."""
        return self.inicio.date() >= date.today()
