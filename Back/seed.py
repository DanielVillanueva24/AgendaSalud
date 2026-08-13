"""
Carga de datos de demostracion para AgendaSalud.

Genera un juego minimo y legible: 3 profesionales con agenda semanal, 10 pacientes
y 10 citas variadas que cubren los cinco estados y reparten pasado, hoy y futuro
entre los tres profesionales. La idea es poder revisar la informacion de un
vistazo, no saturar las tablas.

Uso:  python seed.py            (recrea la base desde cero)
      python seed.py --mantener (solo agrega si la base esta vacia)
"""
import random
import sys
from datetime import datetime, timedelta, time, date

from app import create_app
from extensions import db
from models import Usuario, Paciente, Profesional, HorarioAtencion, Cita

SEMILLA = 2026

PROFESIONALES = [
    {
        "nombre": "Laura", "apellido": "Mendoza", "especialidad": "Medicina General",
        "email": "laura.mendoza@agendasalud.local", "telefono": "555-0101",
        "duracion_cita_min": 30, "color": "#2563eb",
        "horarios": [(d, time(9, 0), time(13, 0)) for d in range(5)]
                    + [(d, time(15, 0), time(19, 0)) for d in range(5)],
    },
    {
        "nombre": "Carlos", "apellido": "Rivas", "especialidad": "Odontologia",
        "email": "carlos.rivas@agendasalud.local", "telefono": "555-0102",
        "duracion_cita_min": 45, "color": "#0d9488",
        "horarios": [(d, time(8, 0), time(14, 0)) for d in range(5)]
                    + [(5, time(9, 0), time(13, 0))],
    },
    {
        "nombre": "Ana", "apellido": "Torres", "especialidad": "Psicologia Clinica",
        "email": "ana.torres@agendasalud.local", "telefono": "555-0103",
        "duracion_cita_min": 60, "color": "#7c3aed",
        "horarios": [(d, time(10, 0), time(14, 0)) for d in (0, 2, 4)]
                    + [(d, time(16, 0), time(20, 0)) for d in (1, 3)],
    },
]

USUARIOS = [
    {"nombre": "Administrador del Sistema", "email": "admin@agendasalud.local",
     "password": "admin123", "rol": "admin"},
    {"nombre": "Marta Nunez", "email": "recepcion@agendasalud.local",
     "password": "recepcion123", "rol": "recepcion"},
]

NOMBRES = [
    ("Sofia", "Ramirez"), ("Miguel", "Castillo"), ("Valentina", "Ortiz"),
    ("Javier", "Fuentes"), ("Camila", "Herrera"), ("Diego", "Salazar"),
    ("Lucia", "Paredes"), ("Andres", "Villalba"), ("Martina", "Cardenas"),
    ("Tomas", "Aguilar"),
]

# Las 10 citas de demostracion, una por fila:
#   (dias respecto de hoy, indice de profesional, indice de paciente, estado, motivo)
# Cubren los cinco estados del ciclo de vida y reparten pasado / hoy / futuro
# entre los tres profesionales, para que toda la interfaz tenga algo que mostrar
# sin llenar las tablas de ruido.
PLAN_CITAS = [
    (-14, 0, 0, "atendida",   "Control de rutina"),
    (-10, 1, 1, "no_asistio", "Limpieza dental"),
    (-7,  2, 2, "atendida",   "Sesion de terapia"),
    (-4,  0, 3, "cancelada",  "Renovacion de receta"),
    (-2,  1, 4, "atendida",   "Chequeo preventivo"),
    (0,   0, 5, "confirmada", "Primera consulta"),
    (0,   1, 6, "pendiente",  "Dolor persistente"),
    (2,   2, 7, "confirmada", "Seguimiento de tratamiento"),
    (4,   0, 8, "pendiente",  "Resultados de examenes"),
    (7,   2, 9, "pendiente",  "Sesion de terapia"),
]

MOTIVOS_CANCELACION = [
    "El paciente reprogramo", "Imprevisto laboral", "Motivos de salud",
    "Cancelada por el consultorio",
]


def poblar(recrear=True):
    app = create_app()
    with app.app_context():
        if recrear:
            db.drop_all()
            db.create_all()
        elif Usuario.query.first():
            print("La base ya tiene datos. Usa `python seed.py` sin --mantener para recrearla.")
            return

        rnd = random.Random(SEMILLA)

        profesionales = _crear_profesionales()
        pacientes = _crear_pacientes(rnd)
        usuarios = _crear_usuarios(profesionales)
        db.session.commit()

        recepcion = next(u for u in usuarios if u.rol == "recepcion")
        total = _crear_citas(rnd, profesionales, pacientes, recepcion)
        db.session.commit()

        _resumen(total)


def _crear_profesionales():
    creados = []
    for ficha in PROFESIONALES:
        horarios = ficha.pop("horarios")
        profesional = Profesional(**ficha)
        for dia, inicio, fin in horarios:
            profesional.horarios.append(
                HorarioAtencion(dia_semana=dia, hora_inicio=inicio, hora_fin=fin)
            )
        db.session.add(profesional)
        creados.append(profesional)
        ficha["horarios"] = horarios  # deja el diccionario reutilizable
    db.session.flush()
    return creados


def _crear_pacientes(rnd):
    pacientes = []
    hoy = date.today()
    for i, (nombre, apellido) in enumerate(NOMBRES, start=1):
        edad = rnd.randint(18, 78)
        paciente = Paciente(
            nombre=nombre,
            apellido=apellido,
            documento=f"DOC{1000 + i}",
            telefono=f"555-{rnd.randint(1000, 9999)}",
            email=f"{nombre.lower()}.{apellido.lower()}@correo.local",
            fecha_nacimiento=hoy - timedelta(days=edad * 365 + rnd.randint(0, 364)),
            activo=True,
        )
        db.session.add(paciente)
        pacientes.append(paciente)
    db.session.flush()
    return pacientes


def _crear_usuarios(profesionales):
    usuarios = []
    for datos in USUARIOS:
        usuario = Usuario(nombre=datos["nombre"], email=datos["email"], rol=datos["rol"])
        usuario.set_password(datos["password"])
        db.session.add(usuario)
        usuarios.append(usuario)

    # Un usuario por cada profesional, para que vea su propia agenda
    for profesional in profesionales:
        usuario = Usuario(
            nombre=profesional.nombre_completo,
            email=profesional.email,
            rol="profesional",
            profesional_id=profesional.id,
        )
        usuario.set_password("profesional123")
        db.session.add(usuario)
        usuarios.append(usuario)

    db.session.flush()
    return usuarios


def _slots_del_dia(profesional, dia):
    """Slots consecutivos completos segun la duracion estandar del profesional."""
    dur = timedelta(minutes=profesional.duracion_cita_min)
    slots = []
    for h in profesional.horarios:
        if h.dia_semana != dia.weekday():
            continue
        cursor = datetime.combine(dia, h.hora_inicio)
        limite = datetime.combine(dia, h.hora_fin)
        while cursor + dur <= limite:
            slots.append((cursor, cursor + dur))
            cursor += dur
    return sorted(slots)


def _crear_citas(rnd, profesionales, pacientes, autor):
    """
    Crea las 10 citas de PLAN_CITAS. Cada una se ancla al primer dia laborable
    del profesional a partir del desfase pedido, de modo que caiga siempre dentro
    de su horario de atencion sea cual sea el dia en que se siembre la base.
    """
    hoy = date.today()
    ocupados = set()                       # (profesional_id, inicio) ya usados
    total = {"historicas": 0, "futuras": 0}

    for offset, prof_idx, pac_idx, estado, motivo in PLAN_CITAS:
        profesional = profesionales[prof_idx]
        slot = _primer_slot_libre(profesional, hoy + timedelta(days=offset), ocupados)
        if slot is None:
            continue

        inicio, fin = slot
        ocupados.add((profesional.id, inicio))

        cita = Cita(
            paciente_id=pacientes[pac_idx].id,
            profesional_id=profesional.id,
            inicio=inicio,
            fin=fin,
            estado=estado,
            motivo=motivo,
            creada_por_id=autor.id,
            creada_en=inicio - timedelta(days=rnd.randint(2, 15), hours=rnd.randint(0, 8)),
            sugerida_por_motor=rnd.random() < 0.4,
        )
        _marcar_ciclo_de_vida(rnd, cita, inicio)

        db.session.add(cita)
        total["historicas" if inicio.date() < hoy else "futuras"] += 1

    return total


def _primer_slot_libre(profesional, objetivo, ocupados):
    """Primer hueco del profesional entre `objetivo` y los 6 dias siguientes."""
    for salto in range(7):
        for inicio, fin in _slots_del_dia(profesional, objetivo + timedelta(days=salto)):
            if (profesional.id, inicio) not in ocupados:
                return inicio, fin
    return None


def _marcar_ciclo_de_vida(rnd, cita, inicio):
    """
    Rellena las marcas de tiempo coherentes con el estado, que son las que
    alimentan los indicadores: confirmacion previa, cierre y recordatorios.
    """
    if cita.estado in ("confirmada", "atendida", "no_asistio"):
        cita.recordatorios_enviados = 1
        cita.ultimo_recordatorio_en = inicio - timedelta(hours=rnd.randint(24, 72))
        cita.confirmada_en = inicio - timedelta(hours=rnd.randint(12, 48))

    if cita.estado in ("atendida", "no_asistio"):
        cita.cerrada_en = cita.fin
    elif cita.estado == "cancelada":
        cita.motivo_cancelacion = rnd.choice(MOTIVOS_CANCELACION)
        cita.cerrada_en = inicio - timedelta(hours=rnd.randint(2, 48))


def _resumen(total):
    print("=" * 62)
    print("  Datos de demostracion cargados")
    print("=" * 62)
    print(f"  Profesionales : {Profesional.query.count()}")
    print(f"  Pacientes     : {Paciente.query.count()}")
    print(f"  Usuarios      : {Usuario.query.count()}")
    print(f"  Citas         : {Cita.query.count()} "
          f"({total['historicas']} historicas / {total['futuras']} futuras)")
    print("-" * 62)
    print("  Credenciales de acceso:")
    print("    admin@agendasalud.local        / admin123        (administrador)")
    print("    recepcion@agendasalud.local    / recepcion123    (recepcion)")
    print("    laura.mendoza@agendasalud.local / profesional123 (profesional)")
    print("=" * 62)


if __name__ == "__main__":
    poblar(recrear="--mantener" not in sys.argv)
