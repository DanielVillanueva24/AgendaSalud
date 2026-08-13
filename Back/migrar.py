"""
migrar.py - Copia los datos de la base SQLite local a PostgreSQL (Render).

Lee las cinco tablas del proyecto (profesionales, usuarios, pacientes,
horarios_atencion y citas) de la base local y las inserta en la base de la nube
sin duplicar lo que ya exista: cada fila se identifica por una clave natural
(el email del usuario, el documento del paciente, el trio profesional+paciente+
inicio de la cita...), no por su id.

Los ids NO se copian. PostgreSQL asigna los suyos y el script va traduciendo las
claves foraneas sobre la marcha, de modo que las secuencias de la base destino
quedan sanas y se puede volver a ejecutar cuantas veces haga falta.

Uso:
    # 1) Ver que hay a cada lado, sin escribir nada
    python migrar.py --verificar

    # 2) Ensayo: dice exactamente que insertaria, sin tocar la nube
    python migrar.py --simular

    # 3) Migracion de verdad
    python migrar.py

La URL de destino se toma de la variable de entorno DATABASE_URL (la *External
Database URL* de Render) o del parametro --destino. Nunca se escribe en este
archivo: esa cadena lleva la contrasena de la base dentro.
"""
import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, insert, select
from sqlalchemy.exc import OperationalError

# Permite ejecutar `python migrar.py` desde cualquier carpeta
sys.path.insert(0, str(Path(__file__).resolve().parent))

import models  # noqa: F401,E402  (registra las tablas en el metadata)
from extensions import db  # noqa: E402

METADATA = db.metadata

# Ruta por defecto de la base local: la misma que usa config.py (raiz del proyecto)
RAIZ = Path(__file__).resolve().parent.parent
SQLITE_POR_DEFECTO = RAIZ / "agendasalud.db"

# Orden de copiado: cada tabla se migra despues de aquellas a las que apunta
ORDEN = ["profesionales", "usuarios", "pacientes", "horarios_atencion", "citas"]

# Claves foraneas que hay que traducir del id de origen al id de destino
REMAPEOS = {
    "usuarios": {"profesional_id": "profesionales"},
    "horarios_atencion": {"profesional_id": "profesionales"},
    "citas": {
        "paciente_id": "pacientes",
        "profesional_id": "profesionales",
        "creada_por_id": "usuarios",
    },
}

# Claves foraneas que pueden quedar vacias sin invalidar la fila
OPCIONALES = {("usuarios", "profesional_id"), ("citas", "creada_por_id")}


# --- Identidad de las filas ---------------------------------------------------

def _txt(valor):
    """Normaliza texto para comparar (los emails y documentos no distinguen mayusculas)."""
    return valor.strip().lower() if isinstance(valor, str) else valor


def clave_natural(tabla, fila):
    """
    Huella que identifica a una fila con independencia de su id.

    Para las tablas hijas (horarios y citas) se usan las claves foraneas *ya
    traducidas* al destino, asi que la comparacion es valida en ambos lados.
    """
    if tabla == "usuarios":
        return ("usuario", _txt(fila["email"]))

    if tabla == "profesionales":
        return ("profesional", _txt(fila["nombre"]), _txt(fila["apellido"]), _txt(fila["email"]))

    if tabla == "pacientes":
        if fila.get("documento"):
            return ("paciente-doc", _txt(fila["documento"]))
        return ("paciente", _txt(fila["nombre"]), _txt(fila["apellido"]), _txt(fila["email"]))

    if tabla == "horarios_atencion":
        return ("horario", fila["profesional_id"], fila["dia_semana"],
                fila["hora_inicio"], fila["hora_fin"])

    if tabla == "citas":
        # Un profesional no puede tener dos citas que empiecen a la misma hora
        return ("cita", fila["profesional_id"], fila["paciente_id"], fila["inicio"])

    raise ValueError(f"No hay clave natural definida para la tabla {tabla}")


# --- Conexiones ---------------------------------------------------------------

def normalizar_url(url):
    """Render y Heroku entregan `postgres://`, que SQLAlchemy 2 ya no reconoce."""
    url = (url or "").strip()
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def url_destino(argumento):
    url = normalizar_url(argumento or os.environ.get("DATABASE_URL"))
    if not url:
        salir(
            "Falta la URL de destino.\n"
            "  Define DATABASE_URL con la External Database URL de Render, o pasa\n"
            "  --destino postgresql://usuario:clave@host/base"
        )
    if url.startswith("sqlite"):
        salir("La URL de destino apunta a SQLite. Se esperaba una base PostgreSQL.")

    # Render publica dos URLs. La *Internal* (host sin dominio, del estilo
    # `dpg-xxxx-a`) solo resuelve dentro de su red privada: es la que va en el
    # Web Service, pero desde un equipo de casa no se puede alcanzar. Para
    # migrar hace falta la *External*, que termina en `-postgres.render.com`.
    host = url.split("@", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    if host.startswith("dpg-") and "." not in host:
        salir(
            "Esa es la Internal Database URL de Render y solo funciona dentro de su red.\n"
            "  Desde tu equipo hay que usar la External Database URL: mismo usuario y\n"
            "  contrasena, pero el host termina en `-postgres.render.com`.\n"
            "  Render > la base de datos > Connections > External Database URL."
        )
    return url


def ruta_sqlite(argumento):
    ruta = Path(argumento).expanduser().resolve() if argumento else SQLITE_POR_DEFECTO
    if not ruta.is_file():
        salir(f"No encuentro la base local en {ruta}\n  Usa --sqlite RUTA para indicarla.")
    return ruta


def censurar(url):
    """Oculta la contrasena para poder imprimir la URL sin exponerla."""
    if "@" not in url:
        return url
    credenciales, servidor = url.rsplit("@", 1)
    if ":" in credenciales:
        credenciales = credenciales.rsplit(":", 1)[0] + ":***"
    return f"{credenciales}@{servidor}"


def salir(mensaje):
    print(f"\nERROR: {mensaje}\n")
    sys.exit(1)


# --- Migracion ----------------------------------------------------------------

def migrar_tabla(nombre, con_origen, con_destino, mapas, simular):
    """Copia una tabla y devuelve el recuento (insertadas, omitidas, descartadas)."""
    tabla = METADATA.tables[nombre]
    columnas = [c.name for c in tabla.columns if c.name != "id"]
    remap = REMAPEOS.get(nombre, {})
    mapa = mapas.setdefault(nombre, {})

    # Indice de lo que ya vive en el destino, por clave natural
    existentes = {}
    for fila in con_destino.execute(select(tabla)).mappings():
        existentes[clave_natural(nombre, fila)] = fila["id"]

    insertadas = omitidas = descartadas = 0
    id_simulado = -1

    for origen in con_origen.execute(select(tabla).order_by(tabla.c.id)).mappings():
        datos = {columna: origen[columna] for columna in columnas}

        # Traduce las claves foraneas al id que tienen en el destino
        huerfana = False
        for columna, referida in remap.items():
            if datos.get(columna) is None:
                continue
            traducido = mapas.get(referida, {}).get(datos[columna])
            if traducido is None:
                if (nombre, columna) in OPCIONALES:
                    traducido = None          # se pierde el dato, no la fila
                else:
                    huerfana = True
                    break
            datos[columna] = traducido

        if huerfana:
            print(f"    ! fila {nombre}#{origen['id']} descartada: apunta a un registro inexistente")
            descartadas += 1
            continue

        clave = clave_natural(nombre, datos)
        if clave in existentes:
            mapa[origen["id"]] = existentes[clave]
            omitidas += 1
            continue

        if simular:
            nuevo_id = id_simulado
            id_simulado -= 1
        else:
            resultado = con_destino.execute(insert(tabla).values(**datos))
            nuevo_id = resultado.inserted_primary_key[0]

        existentes[clave] = nuevo_id
        mapa[origen["id"]] = nuevo_id
        insertadas += 1

    return insertadas, omitidas, descartadas


def contar(conexion, nombre):
    tabla = METADATA.tables[nombre]
    return conexion.execute(select(func.count()).select_from(tabla)).scalar_one()


def tabla_de_recuentos(con_origen, con_destino, titulo_destino="Destino"):
    print(f"  {'Tabla':<20}{'Local':>8}{titulo_destino:>12}")
    print(f"  {'-' * 40}")
    for nombre in ORDEN:
        try:
            en_destino = contar(con_destino, nombre)
        except Exception:
            en_destino = "-"
        print(f"  {nombre:<20}{contar(con_origen, nombre):>8}{en_destino:>12}")


def migrar(ruta_origen, url_dest, simular=False):
    motor_origen = create_engine(f"sqlite:///{ruta_origen.as_posix()}")
    motor_destino = create_engine(url_dest, pool_pre_ping=True)

    print("=" * 62)
    print("  AgendaSalud - migracion SQLite -> PostgreSQL")
    print("=" * 62)
    print(f"  Origen  : {ruta_origen}")
    print(f"  Destino : {censurar(url_dest)}")
    if simular:
        print("  Modo    : SIMULACION (no se escribe nada)")
    print("-" * 62)

    with motor_origen.connect() as con_origen, motor_destino.connect() as con_destino:
        # Todo ocurre en una sola transaccion: o se migra entero, o no se migra nada
        transaccion = con_destino.begin()
        try:
            # Crea las tablas que falten en la nube; no toca las que ya existan
            METADATA.create_all(con_destino)

            mapas = {}
            for nombre in ORDEN:
                insertadas, omitidas, descartadas = migrar_tabla(
                    nombre, con_origen, con_destino, mapas, simular
                )
                detalle = f"{insertadas:>4} nuevas   {omitidas:>4} ya existian"
                if descartadas:
                    detalle += f"   {descartadas} descartadas"
                print(f"  {nombre:<20} {detalle}")
        except Exception:
            transaccion.rollback()
            raise

        if simular:
            transaccion.rollback()
        else:
            transaccion.commit()

    print("-" * 62)
    if simular:
        print("  Simulacion terminada: la base de la nube quedo intacta.")
        print("  Vuelve a ejecutar sin --simular para migrar de verdad.")
    else:
        with motor_origen.connect() as con_origen, motor_destino.connect() as con_destino:
            tabla_de_recuentos(con_origen, con_destino, "Nube")
        print("-" * 62)
        print("  Migracion completada.")
    print("=" * 62)


def verificar(ruta_origen, url_dest):
    motor_origen = create_engine(f"sqlite:///{ruta_origen.as_posix()}")
    motor_destino = create_engine(url_dest, pool_pre_ping=True)

    print("=" * 62)
    print("  AgendaSalud - recuento de registros")
    print("=" * 62)
    print(f"  Origen  : {ruta_origen}")
    print(f"  Destino : {censurar(url_dest)}")
    print("-" * 62)
    with motor_origen.connect() as con_origen, motor_destino.connect() as con_destino:
        tabla_de_recuentos(con_origen, con_destino, "Nube")

        citas = METADATA.tables["citas"]
        print("-" * 62)
        print("  Citas por estado en la nube:")
        filas = con_destino.execute(
            select(citas.c.estado, func.count())
            .group_by(citas.c.estado)
            .order_by(citas.c.estado)
        ).all()
        for estado, cantidad in filas or [("(sin citas)", 0)]:
            print(f"    {estado:<14}: {cantidad}")

        # Con que cuentas se puede entrar a la app desplegada. Si esta lista sale
        # vacia, el login respondera 401 por mucho que la conexion funcione.
        usuarios = METADATA.tables["usuarios"]
        print("-" * 62)
        print("  Cuentas de acceso en la nube:")
        cuentas = con_destino.execute(
            select(usuarios.c.email, usuarios.c.rol, usuarios.c.activo).order_by(usuarios.c.email)
        ).all()
        if not cuentas:
            print("    (ninguna) -> el login va a devolver 401. Falta migrar.")
        for email, rol, activo in cuentas:
            estado = "activa" if activo else "DESACTIVADA"
            print(f"    {email:<36} {rol:<12} {estado}")
    print("=" * 62)


def main():
    parser = argparse.ArgumentParser(
        description="Copia los datos de la base SQLite local a PostgreSQL sin duplicar."
    )
    parser.add_argument("--sqlite", help=f"Ruta de la base local (por defecto: {SQLITE_POR_DEFECTO})")
    parser.add_argument("--destino", help="URL de PostgreSQL (por defecto: variable DATABASE_URL)")
    parser.add_argument("--simular", action="store_true", help="Ensayo: no escribe en el destino")
    parser.add_argument("--verificar", action="store_true", help="Solo muestra los recuentos de ambas bases")
    args = parser.parse_args()

    origen = ruta_sqlite(args.sqlite)
    destino = url_destino(args.destino)

    try:
        if args.verificar:
            verificar(origen, destino)
        else:
            migrar(origen, destino, simular=args.simular)
    except ImportError as err:
        if "psycopg2" in str(err):
            salir("Falta el conector de PostgreSQL. Instalalo con:\n  pip install psycopg2-binary")
        raise
    except OperationalError as err:
        detalle = str(err.orig).strip() if err.orig else str(err)
        salir(
            f"No se pudo conectar con la base de destino:\n  {detalle}\n\n"
            "  Revisa que sea la External Database URL (no la Internal), que la base\n"
            "  siga viva en Render y, si el fallo menciona SSL, agrega `?sslmode=require`\n"
            "  al final de la URL."
        )


if __name__ == "__main__":
    main()
