"""
Pruebas automatizadas de AgendaSalud (RNF5).

Cubren el motor de optimizacion (RF4), las reglas de la agenda (RF3),
el ciclo de estados de la cita (RF5), el control de acceso por rol (RF1/RNF1)
y el calculo de los indicadores (RF6).

Ejecutar:  python test_agendasalud.py
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, time, date

from config import Config
from app import create_app
from extensions import db
from models import Usuario, Paciente, Profesional, HorarioAtencion, Cita
import scheduler


def proximo_lunes(desde=None):
    """Fecha del proximo lunes futuro (evita chocar con la hora actual)."""
    base = (desde or date.today()) + timedelta(days=1)
    while base.weekday() != 0:
        base += timedelta(days=1)
    return base


class BaseTest(unittest.TestCase):
    """Levanta la app con una base SQLite temporal y datos minimos."""

    def setUp(self):
        self.fd, self.ruta_db = tempfile.mkstemp(suffix=".db")

        class TestConfig(Config):
            TESTING = True
            SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.ruta_db}"
            SECRET_KEY = "test"

        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.cliente = self.app.test_client()
        self._datos_base()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()          # Windows bloquea el .db si el pool sigue abierto
        self.ctx.pop()
        os.close(self.fd)
        try:
            os.unlink(self.ruta_db)
        except OSError:
            pass

    def _datos_base(self):
        self.profesional = Profesional(
            nombre="Laura", apellido="Mendoza", especialidad="General",
            duracion_cita_min=30,
        )
        # Lunes a viernes de 09:00 a 12:00
        for dia in range(5):
            self.profesional.horarios.append(
                HorarioAtencion(dia_semana=dia, hora_inicio=time(9, 0), hora_fin=time(12, 0))
            )
        db.session.add(self.profesional)

        self.paciente = Paciente(nombre="Sofia", apellido="Ramirez", documento="D1")
        self.otro_paciente = Paciente(nombre="Miguel", apellido="Castillo", documento="D2")
        db.session.add_all([self.paciente, self.otro_paciente])

        self.admin = Usuario(nombre="Admin", email="admin@test.local", rol="admin")
        self.admin.set_password("admin123")
        self.recepcion = Usuario(nombre="Recep", email="recep@test.local", rol="recepcion")
        self.recepcion.set_password("recep123")
        db.session.add_all([self.admin, self.recepcion])
        db.session.commit()

        self.lunes = proximo_lunes()

    # --- utilidades ---

    def login(self, email="recep@test.local", password="recep123"):
        return self.cliente.post("/api/auth/login", json={"email": email, "password": password})

    def crear_cita(self, hora=9, minuto=0, duracion=30, estado=None, dia=None, paciente=None):
        inicio = datetime.combine(dia or self.lunes, time(hora, minuto))
        cita = Cita(
            paciente_id=(paciente or self.paciente).id,
            profesional_id=self.profesional.id,
            inicio=inicio,
            fin=inicio + timedelta(minutes=duracion),
            estado=estado or "confirmada",
        )
        db.session.add(cita)
        db.session.commit()
        return cita


class TestMotorOptimizacion(BaseTest):
    """RF4 - heuristica greedy."""

    def test_agenda_vacia_genera_hueco_completo(self):
        huecos = scheduler.huecos_libres(self.profesional, self.lunes)
        self.assertEqual(len(huecos), 1)
        inicio, fin, _, _ = huecos[0]
        self.assertEqual(inicio.hour, 9)
        self.assertEqual(fin.hour, 12)

    def test_las_citas_parten_el_hueco(self):
        self.crear_cita(hora=10, minuto=0, duracion=30)
        huecos = scheduler.huecos_libres(self.profesional, self.lunes)
        self.assertEqual(
            [(i.strftime("%H:%M"), f.strftime("%H:%M")) for i, f, _, _ in huecos],
            [("09:00", "10:00"), ("10:30", "12:00")],
        )

    def test_las_canceladas_no_ocupan_agenda(self):
        self.crear_cita(hora=10, estado="cancelada")
        huecos = scheduler.huecos_libres(self.profesional, self.lunes)
        self.assertEqual(len(huecos), 1, "una cita cancelada debe liberar el hueco")

    def test_una_inasistencia_sigue_ocupando_su_hueco(self):
        """
        Solo cancelar libera el horario. Si una inasistencia lo liberase, se
        podria agendar encima y el calendario pintaria dos citas superpuestas
        en la misma casilla.
        """
        self.crear_cita(hora=10, minuto=0, duracion=30, estado="no_asistio")
        huecos = scheduler.huecos_libres(self.profesional, self.lunes)
        self.assertEqual(
            [(i.strftime("%H:%M"), f.strftime("%H:%M")) for i, f, _, _ in huecos],
            [("09:00", "10:00"), ("10:30", "12:00")],
        )

        inicio = datetime.combine(self.lunes, time(10, 0))
        r = scheduler.verificar_disponibilidad(self.profesional, inicio, inicio + timedelta(minutes=30))
        self.assertFalse(r["disponible"])
        self.assertEqual(len(r["conflictos"]), 1)

    def test_deteccion_de_solapamiento(self):
        self.crear_cita(hora=10, minuto=0, duracion=30)
        inicio = datetime.combine(self.lunes, time(10, 15))
        r = scheduler.verificar_disponibilidad(self.profesional, inicio, inicio + timedelta(minutes=30))
        self.assertFalse(r["disponible"])
        self.assertEqual(len(r["conflictos"]), 1)

    def test_intervalos_contiguos_no_solapan(self):
        self.crear_cita(hora=10, minuto=0, duracion=30)
        inicio = datetime.combine(self.lunes, time(10, 30))
        r = scheduler.verificar_disponibilidad(self.profesional, inicio, inicio + timedelta(minutes=30))
        self.assertTrue(r["disponible"], "una cita que empieza cuando termina otra debe caber")

    def test_detecta_fuera_de_horario(self):
        inicio = datetime.combine(self.lunes, time(20, 0))
        r = scheduler.verificar_disponibilidad(self.profesional, inicio, inicio + timedelta(minutes=30))
        self.assertTrue(r["fuera_de_horario"])
        self.assertFalse(r["disponible"])

    def test_greedy_prefiere_el_hueco_mas_temprano(self):
        slots = scheduler.sugerir_slots(self.profesional, 30, limite=1)
        self.assertTrue(slots)
        self.assertEqual(datetime.fromisoformat(slots[0]["inicio"]).time(), time(9, 0))

    def test_greedy_compacta_en_lugar_de_fragmentar(self):
        """
        Con 09:00-09:30 y 10:00-12:00 ocupados queda un hueco de 30 min (09:30-10:00).
        Para una cita de 30 min el motor debe usarlo entero y no dejar residuo.
        """
        self.crear_cita(hora=9, minuto=0, duracion=30)
        self.crear_cita(hora=10, minuto=0, duracion=120, paciente=self.otro_paciente)

        # Se acota al dia bajo prueba: si no, el motor propondria (con razon) un
        # dia anterior con la agenda vacia y no se estaria midiendo la compactacion.
        slots = scheduler.sugerir_slots(self.profesional, 30, limite=3, solo_dia=self.lunes)
        self.assertTrue(slots)
        mejor = slots[0]
        self.assertEqual(datetime.fromisoformat(mejor["inicio"]).time(), time(9, 30))
        self.assertEqual(mejor["fragmentacion_min"], 0)
        self.assertTrue(mejor["compacta"])

    def test_greedy_evita_dejar_restos_inutilizables(self):
        """
        Hueco de 09:00 a 12:00 y cita de 60 min: iniciar a las 09:15 dejaria
        15 min muertos al principio. El motor debe elegir el inicio pegado al borde.
        """
        slots = scheduler.sugerir_slots(self.profesional, 60, limite=5)
        self.assertEqual(slots[0]["fragmentacion_min"], 0)
        self.assertEqual(datetime.fromisoformat(slots[0]["inicio"]).time(), time(9, 0))

    def test_no_sugiere_horarios_pasados(self):
        ayer = date.today() - timedelta(days=7)
        slots = scheduler.sugerir_slots(self.profesional, 30, limite=10, solo_dia=ayer)
        self.assertEqual(slots, [], "no deben sugerirse huecos en el pasado")

    def test_capacidad_semanal(self):
        # 5 dias x 3 horas = 900 minutos
        minutos = scheduler.minutos_disponibles(self.profesional, self.lunes, self.lunes + timedelta(days=6))
        self.assertEqual(minutos, 900)


class TestApiCitas(BaseTest):
    """RF3 y RF5 - reserva y ciclo de estados."""

    def setUp(self):
        super().setUp()
        self.login()

    def _payload(self, hora=9, minuto=0, duracion=30):
        return {
            "paciente_id": self.paciente.id,
            "profesional_id": self.profesional.id,
            "inicio": datetime.combine(self.lunes, time(hora, minuto)).isoformat(),
            "duracion_min": duracion,
            "motivo": "Control",
        }

    def test_crear_cita(self):
        r = self.cliente.post("/api/citas", json=self._payload())
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["estado"], "pendiente")

    def test_rechaza_solapamiento(self):
        self.cliente.post("/api/citas", json=self._payload())
        r = self.cliente.post("/api/citas", json=self._payload(hora=9, minuto=15))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["tipo"], "solapamiento")

    def test_rechaza_agendar_sobre_una_inasistencia(self):
        """
        Recorrido completo del fallo real: se agenda, el paciente no aparece, se
        registra la inasistencia y recepcion intenta meter a otro en ese hueco.
        Antes se aceptaba y las dos citas quedaban dibujadas una encima de otra.
        """
        creada = self.cliente.post("/api/citas", json=self._payload()).get_json()
        self.cliente.patch(f"/api/citas/{creada['id']}/estado", json={"estado": "no_asistio"})

        r = self.cliente.post("/api/citas", json={
            **self._payload(), "paciente_id": self.otro_paciente.id,
        })
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["tipo"], "solapamiento")

    def test_cancelar_si_libera_el_hueco(self):
        """El contrapunto del test anterior: cancelar sigue devolviendo el horario."""
        creada = self.cliente.post("/api/citas", json=self._payload()).get_json()
        self.cliente.patch(f"/api/citas/{creada['id']}/estado", json={"estado": "cancelada"})

        r = self.cliente.post("/api/citas", json={
            **self._payload(), "paciente_id": self.otro_paciente.id,
        })
        self.assertEqual(r.status_code, 201)

    def test_rechaza_fuera_de_horario_pero_permite_forzar(self):
        r = self.cliente.post("/api/citas", json=self._payload(hora=20))
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.get_json()["puede_forzar"])

        forzada = {**self._payload(hora=20), "forzar": True}
        self.assertEqual(self.cliente.post("/api/citas", json=forzada).status_code, 201)

    def test_paciente_no_puede_estar_en_dos_citas_a_la_vez(self):
        otro = Profesional(nombre="Carlos", apellido="Rivas", duracion_cita_min=30)
        otro.horarios.append(HorarioAtencion(dia_semana=0, hora_inicio=time(9, 0), hora_fin=time(12, 0)))
        db.session.add(otro)
        db.session.commit()

        self.cliente.post("/api/citas", json=self._payload())
        r = self.cliente.post("/api/citas", json={**self._payload(), "profesional_id": otro.id})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["tipo"], "solapamiento_paciente")

    def test_flujo_de_estados_completo(self):
        cid = self.cliente.post("/api/citas", json=self._payload()).get_json()["id"]

        r = self.cliente.post(f"/api/citas/{cid}/estado", json={"estado": "confirmada"})
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.get_json()["confirmada_en"])

        r = self.cliente.post(f"/api/citas/{cid}/estado", json={"estado": "atendida"})
        self.assertEqual(r.get_json()["estado"], "atendida")
        self.assertIsNotNone(r.get_json()["cerrada_en"])

    def test_transicion_invalida(self):
        cid = self.cliente.post("/api/citas", json=self._payload()).get_json()["id"]
        self.cliente.post(f"/api/citas/{cid}/estado", json={"estado": "cancelada"})
        r = self.cliente.post(f"/api/citas/{cid}/estado", json={"estado": "atendida"})
        self.assertEqual(r.status_code, 409, "de cancelada no se puede pasar directo a atendida")

    def test_estado_invalido(self):
        cid = self.cliente.post("/api/citas", json=self._payload()).get_json()["id"]
        r = self.cliente.post(f"/api/citas/{cid}/estado", json={"estado": "inventado"})
        self.assertEqual(r.status_code, 400)

    def test_cancelar_libera_el_hueco(self):
        cid = self.cliente.post("/api/citas", json=self._payload()).get_json()["id"]
        self.cliente.post(f"/api/citas/{cid}/estado", json={"estado": "cancelada"})
        r = self.cliente.post("/api/citas", json={**self._payload(), "paciente_id": self.otro_paciente.id})
        self.assertEqual(r.status_code, 201)

    def test_no_se_elimina_una_cita_cerrada(self):
        cid = self.cliente.post("/api/citas", json=self._payload()).get_json()["id"]
        self.cliente.post(f"/api/citas/{cid}/estado", json={"estado": "atendida"})
        self.assertEqual(self.cliente.delete(f"/api/citas/{cid}").status_code, 409)

    def test_reprogramar_valida_solapamiento(self):
        primera = self.cliente.post("/api/citas", json=self._payload(hora=9)).get_json()
        segunda = self.cliente.post(
            "/api/citas", json={**self._payload(hora=10), "paciente_id": self.otro_paciente.id}
        ).get_json()

        r = self.cliente.put(f"/api/citas/{segunda['id']}", json={"inicio": primera["inicio"]})
        self.assertEqual(r.status_code, 409)

    def test_sugerencias_devuelven_ranking(self):
        r = self.cliente.get(f"/api/citas/sugerencias?profesional_id={self.profesional.id}&limite=3")
        self.assertEqual(r.status_code, 200)
        sugerencias = r.get_json()["sugerencias"]
        self.assertTrue(sugerencias)
        self.assertEqual([s["ranking"] for s in sugerencias], list(range(1, len(sugerencias) + 1)))
        self.assertEqual(sugerencias, sorted(sugerencias, key=lambda s: s["costo"]))

    def test_sugerencias_acotadas_a_un_dia(self):
        dia = (self.lunes + timedelta(days=2)).isoformat()
        r = self.cliente.get(
            f"/api/citas/sugerencias?profesional_id={self.profesional.id}&duracion=30&fecha={dia}"
        )
        self.assertEqual(r.status_code, 200)
        fechas = {s["inicio"][:10] for s in r.get_json()["sugerencias"]}
        self.assertEqual(fechas, {dia})

    def test_sugerencias_acotadas_a_un_dia_sin_elegir_profesional(self):
        """
        Sin profesional se usa sugerir_multiprofesional, que no propagaba
        `solo_dia`: la fecha pedida se ignoraba en silencio.
        """
        otro = Profesional(nombre="Carlos", apellido="Rivas", duracion_cita_min=30)
        for d in range(5):
            otro.horarios.append(
                HorarioAtencion(dia_semana=d, hora_inicio=time(9, 0), hora_fin=time(12, 0))
            )
        db.session.add(otro)
        db.session.commit()

        dia = (self.lunes + timedelta(days=2)).isoformat()
        r = self.cliente.get(f"/api/citas/sugerencias?duracion=30&fecha={dia}")
        self.assertEqual(r.status_code, 200)
        sugerencias = r.get_json()["sugerencias"]
        self.assertTrue(sugerencias)
        self.assertEqual({s["inicio"][:10] for s in sugerencias}, {dia})


class TestSeguridad(BaseTest):
    """RF1 y RNF1 - autenticacion y control de acceso."""

    def test_endpoints_protegidos(self):
        for ruta in ("/api/citas", "/api/pacientes", "/api/indicadores/resumen", "/api/usuarios"):
            self.assertEqual(self.cliente.get(ruta).status_code, 401, ruta)

    def test_password_no_se_guarda_en_claro(self):
        self.assertNotIn("admin123", self.admin.password_hash)
        self.assertTrue(self.admin.check_password("admin123"))
        self.assertFalse(self.admin.check_password("otra"))

    def test_login_incorrecto(self):
        r = self.login(password="incorrecta")
        self.assertEqual(r.status_code, 401)

    def test_usuario_desactivado_no_entra(self):
        self.recepcion.activo = False
        db.session.commit()
        self.assertEqual(self.login().status_code, 403)

    def test_recepcion_no_administra_usuarios(self):
        self.login()
        self.assertEqual(self.cliente.get("/api/usuarios").status_code, 403)
        self.assertEqual(
            self.cliente.post("/api/profesionales", json={"nombre": "X", "apellido": "Y"}).status_code, 403
        )

    def test_admin_administra_usuarios(self):
        self.login("admin@test.local", "admin123")
        self.assertEqual(self.cliente.get("/api/usuarios").status_code, 200)

    def test_no_se_puede_quedar_sin_admin(self):
        self.login("admin@test.local", "admin123")
        r = self.cliente.put(f"/api/usuarios/{self.admin.id}", json={"rol": "recepcion"})
        self.assertEqual(r.status_code, 409)

    def test_profesional_solo_ve_su_agenda(self):
        otro = Profesional(nombre="Carlos", apellido="Rivas")
        db.session.add(otro)
        db.session.commit()

        usuario = Usuario(nombre="Laura", email="laura@test.local", rol="profesional",
                          profesional_id=self.profesional.id)
        usuario.set_password("prof123")
        db.session.add(usuario)
        self.crear_cita(hora=9)
        cita_ajena = Cita(
            paciente_id=self.otro_paciente.id, profesional_id=otro.id,
            inicio=datetime.combine(self.lunes, time(9, 0)),
            fin=datetime.combine(self.lunes, time(9, 30)), estado="confirmada",
        )
        db.session.add(cita_ajena)
        db.session.commit()

        self.login("laura@test.local", "prof123")
        citas = self.cliente.get("/api/citas").get_json()["citas"]
        self.assertTrue(citas)
        self.assertTrue(all(c["profesional_id"] == self.profesional.id for c in citas))
        self.assertEqual(self.cliente.get(f"/api/citas/{cita_ajena.id}").status_code, 403)

    def test_logout_cierra_la_sesion(self):
        self.login()
        self.cliente.post("/api/auth/logout")
        self.assertEqual(self.cliente.get("/api/citas").status_code, 401)


class TestAlcancePorRol(BaseTest):
    """
    RNF1 - el rol `profesional` queda acotado a su propia agenda.

    `rol_requerido` decide QUE acciones permite un rol; el acotamiento por
    agenda lo hace `alcance_profesional_id()`. Estas pruebas cubren los puntos
    donde ese acotamiento no se aplicaba y se escapaban datos de otras agendas.
    """

    def setUp(self):
        super().setUp()
        self.otro = Profesional(nombre="Carlos", apellido="Rivas", duracion_cita_min=30)
        for dia in range(5):
            self.otro.horarios.append(
                HorarioAtencion(dia_semana=dia, hora_inicio=time(9, 0), hora_fin=time(12, 0))
            )
        db.session.add(self.otro)

        self.usuario_prof = Usuario(
            nombre="Laura", email="laura@test.local", rol="profesional",
            profesional_id=self.profesional.id,
        )
        self.usuario_prof.set_password("prof123")
        db.session.add(self.usuario_prof)
        db.session.commit()

    def login_profesional(self):
        return self.login("laura@test.local", "prof123")

    def cita_ajena(self, hora=9, estado="confirmada", dia=None, paciente=None):
        inicio = datetime.combine(dia or self.lunes, time(hora, 0))
        cita = Cita(
            paciente_id=(paciente or self.otro_paciente).id,
            profesional_id=self.otro.id,
            inicio=inicio, fin=inicio + timedelta(minutes=30), estado=estado,
        )
        db.session.add(cita)
        db.session.commit()
        return cita

    def test_el_profesional_no_agenda_citas(self):
        """README: agendar es de recepcion; el profesional solo ve las suyas."""
        self.login_profesional()
        r = self.cliente.post("/api/citas", json={
            "paciente_id": self.paciente.id,
            "profesional_id": self.profesional.id,
            "inicio": datetime.combine(self.lunes, time(10, 0)).isoformat(),
        })
        self.assertEqual(r.status_code, 403)

    def test_el_profesional_no_reprograma_citas(self):
        cita_id = self.crear_cita(hora=9).id
        self.login_profesional()
        r = self.cliente.put(f"/api/citas/{cita_id}", json={"profesional_id": self.otro.id})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(db.session.get(Cita, cita_id).profesional_id, self.profesional.id)

    def test_el_profesional_solo_registra_asistencia(self):
        """README: sus estados son atendida / no asistio, no confirmar ni cancelar."""
        cita_id = self.crear_cita(hora=9, estado="pendiente").id
        self.login_profesional()

        for prohibido in ("confirmada", "cancelada"):
            r = self.cliente.patch(f"/api/citas/{cita_id}/estado", json={"estado": prohibido})
            self.assertEqual(r.status_code, 403, prohibido)

        r = self.cliente.patch(f"/api/citas/{cita_id}/estado", json={"estado": "atendida"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["estado"], "atendida")

    def test_el_profesional_no_crea_ni_edita_pacientes(self):
        """README: pacientes en solo lectura para el profesional."""
        self.login_profesional()
        self.assertEqual(
            self.cliente.post("/api/pacientes", json={"nombre": "X", "apellido": "Y"}).status_code, 403
        )
        self.assertEqual(
            self.cliente.put(f"/api/pacientes/{self.paciente.id}", json={"nombre": "Z"}).status_code, 403
        )
        # Consultar si puede
        self.assertEqual(self.cliente.get("/api/pacientes").status_code, 200)

    def test_el_profesional_solo_ve_su_propio_perfil(self):
        """README: fila Profesionales -> 'Solo su perfil'."""
        self.login_profesional()
        listado = self.cliente.get("/api/profesionales").get_json()["profesionales"]
        self.assertEqual([p["id"] for p in listado], [self.profesional.id])
        self.assertEqual(self.cliente.get(f"/api/profesionales/{self.otro.id}").status_code, 403)
        self.assertEqual(self.cliente.get(f"/api/profesionales/{self.profesional.id}").status_code, 200)

    def test_verificar_no_revela_la_agenda_ajena(self):
        ajena = self.cita_ajena(hora=9)
        self.login_profesional()
        r = self.cliente.get("/api/citas/verificar", query_string={
            "profesional_id": self.otro.id,
            "inicio": ajena.inicio.isoformat(),
            "duracion": 30,
        })
        self.assertEqual(r.status_code, 200)
        # Se evalua su propia agenda (libre a esa hora), no la del otro
        self.assertEqual(r.get_json()["conflictos"], [])

    def test_pacientes_riesgo_solo_incluye_pacientes_propios(self):
        # otro_paciente falta 3 veces, pero solo con el otro profesional
        for i in range(3):
            self.cita_ajena(estado="no_asistio", dia=self.lunes - timedelta(days=7 + i))

        self.login_profesional()
        riesgo = self.cliente.get("/api/indicadores/pacientes-riesgo").get_json()["pacientes"]
        self.assertNotIn(self.otro_paciente.id, [p["paciente_id"] for p in riesgo])

    def test_profesional_sin_ficha_vinculada_no_ve_nada(self):
        """Fallar cerrado: sin ficha no hay agenda que acotar, asi que se deniega."""
        suelto = Usuario(nombre="Suelto", email="suelto@test.local", rol="profesional")
        suelto.set_password("suelto123")
        db.session.add(suelto)
        db.session.commit()

        self.login("suelto@test.local", "suelto123")
        self.assertEqual(self.cliente.get("/api/citas").status_code, 403)

    def test_no_se_crea_un_profesional_sin_ficha(self):
        self.login("admin@test.local", "admin123")
        r = self.cliente.post("/api/usuarios", json={
            "nombre": "Nuevo", "email": "nuevo@test.local",
            "password": "clave123", "rol": "profesional",
        })
        self.assertEqual(r.status_code, 400)

    def test_cambiar_el_rol_a_profesional_exige_ficha(self):
        self.login("admin@test.local", "admin123")
        r = self.cliente.put(f"/api/usuarios/{self.recepcion.id}", json={"rol": "profesional"})
        self.assertEqual(r.status_code, 400)

    def test_dejar_de_ser_profesional_suelta_la_ficha(self):
        self.login("admin@test.local", "admin123")
        r = self.cliente.put(f"/api/usuarios/{self.usuario_prof.id}", json={"rol": "recepcion"})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.get_json()["profesional_id"])


class TestHistoricoInmutable(BaseTest):
    """Las citas de dias ya pasados se consultan pero no se modifican."""

    def setUp(self):
        super().setUp()
        self.ayer = date.today() - timedelta(days=1)
        self.pasada = self.crear_cita(hora=9, dia=self.ayer, estado="pendiente")
        self.login()

    def test_no_se_reprograma_una_cita_pasada(self):
        r = self.cliente.put(f"/api/citas/{self.pasada.id}",
                             json={"inicio": datetime.combine(self.lunes, time(9, 0)).isoformat()})
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.get_json()["solo_lectura"])

    def test_no_se_cambia_el_estado_de_una_cita_pasada(self):
        r = self.cliente.patch(f"/api/citas/{self.pasada.id}/estado", json={"estado": "confirmada"})
        self.assertEqual(r.status_code, 409)

    def test_no_se_elimina_ni_se_recuerda_una_cita_pasada(self):
        self.assertEqual(self.cliente.delete(f"/api/citas/{self.pasada.id}").status_code, 409)
        self.assertEqual(
            self.cliente.post(f"/api/citas/{self.pasada.id}/recordatorio").status_code, 409
        )

    def test_no_se_agenda_en_una_fecha_pasada(self):
        r = self.cliente.post("/api/citas", json={
            "paciente_id": self.paciente.id, "profesional_id": self.profesional.id,
            "inicio": datetime.combine(self.ayer, time(10, 0)).isoformat(),
        })
        self.assertEqual(r.status_code, 409)

    def test_la_cita_pasada_si_se_consulta_y_se_marca_no_editable(self):
        r = self.cliente.get(f"/api/citas/{self.pasada.id}")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["editable"])

    def test_el_dia_en_curso_sigue_abierto(self):
        """Hoy no es historico: hay que poder cerrar la jornada."""
        de_hoy = self.crear_cita(hora=23, minuto=30, dia=date.today(), estado="pendiente")
        r = self.cliente.patch(f"/api/citas/{de_hoy.id}/estado", json={"estado": "atendida"})
        self.assertEqual(r.status_code, 200)


class TestCuentasDeUsuario(BaseTest):
    """RF1 - formato de correo y borrado definitivo."""

    def setUp(self):
        super().setUp()
        self.login("admin@test.local", "admin123")

    def _crear(self, email):
        return self.cliente.post("/api/usuarios", json={
            "nombre": "Prueba", "email": email, "password": "clave123", "rol": "recepcion",
        })

    def test_rechaza_correos_mal_formados(self):
        for malo in ("sin-arroba", "dos@@arrobas.com", "sin@dominio", "con espacio@x.com", "@x.com"):
            self.assertEqual(self._crear(malo).status_code, 400, malo)

    def test_acepta_un_correo_valido(self):
        self.assertEqual(self._crear("nuevo.usuario@dominio.com").status_code, 201)

    def test_eliminar_borra_la_fila_y_conserva_las_citas(self):
        uid = self._crear("borrable@dominio.com").get_json()["id"]
        cita = self.crear_cita(hora=9)
        cita.creada_por_id = uid
        db.session.commit()

        r = self.cliente.delete(f"/api/usuarios/{uid}", query_string={"definitivo": 1})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["eliminado"])
        self.assertIsNone(db.session.get(Usuario, uid))
        # La cita sobrevive, solo pierde la referencia al autor
        self.assertIsNotNone(db.session.get(Cita, cita.id))
        self.assertIsNone(db.session.get(Cita, cita.id).creada_por_id)

    def test_sin_definitivo_solo_desactiva(self):
        uid = self._crear("desactivable@dominio.com").get_json()["id"]
        r = self.cliente.delete(f"/api/usuarios/{uid}")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["eliminado"])
        self.assertFalse(db.session.get(Usuario, uid).activo)

    def test_no_puedo_eliminarme_a_mi_mismo(self):
        r = self.cliente.delete(f"/api/usuarios/{self.admin.id}", query_string={"definitivo": 1})
        self.assertEqual(r.status_code, 409)


class TestIndicadores(BaseTest):
    """RF6 - calculo de ausentismo y ocupacion."""

    def setUp(self):
        super().setUp()
        self.lunes_pasado = date.today() - timedelta(days=date.today().weekday() + 7)
        self.login()

    def test_tasa_de_ausentismo(self):
        # 3 atendidas + 1 inasistencia => 25 %
        for i, estado in enumerate(["atendida", "atendida", "atendida", "no_asistio"]):
            self.crear_cita(hora=9, minuto=0, duracion=30, estado=estado,
                            dia=self.lunes_pasado + timedelta(days=i))

        k = self.cliente.get("/api/indicadores/resumen").get_json()["kpis"]
        self.assertEqual(k["citas_cerradas"], 4)
        self.assertEqual(k["ausencias"], 1)
        self.assertEqual(k["tasa_ausentismo"], 25.0)
        self.assertEqual(k["horas_perdidas"], 0.5)

    def test_las_canceladas_no_cuentan_como_ausentismo(self):
        self.crear_cita(hora=9, estado="atendida", dia=self.lunes_pasado)
        self.crear_cita(hora=10, estado="cancelada", dia=self.lunes_pasado)

        datos = self.cliente.get("/api/indicadores/resumen").get_json()
        self.assertEqual(datos["kpis"]["tasa_ausentismo"], 0.0)
        self.assertEqual(datos["kpis"]["tasa_cancelacion"], 50.0)

    def test_ocupacion_sobre_capacidad_real(self):
        # Un solo dia de rango: capacidad 180 min, ocupados 90 => 50 %
        dia = self.lunes_pasado
        self.crear_cita(hora=9, duracion=60, estado="atendida", dia=dia)
        self.crear_cita(hora=10, duracion=30, estado="atendida", dia=dia,
                        paciente=self.otro_paciente)

        r = self.cliente.get(f"/api/indicadores/resumen?desde={dia}&hasta={dia}").get_json()
        self.assertEqual(r["kpis"]["horas_capacidad"], 3.0)
        self.assertEqual(r["kpis"]["ocupacion"], 50.0)

    def test_efecto_de_la_confirmacion(self):
        confirmada = self.crear_cita(hora=9, estado="atendida", dia=self.lunes_pasado)
        confirmada.confirmada_en = confirmada.inicio - timedelta(days=1)
        self.crear_cita(hora=10, estado="no_asistio", dia=self.lunes_pasado,
                        paciente=self.otro_paciente)
        db.session.commit()

        efecto = self.cliente.get("/api/indicadores/patrones").get_json()["efecto_confirmacion"]
        self.assertEqual(efecto["confirmadas"]["tasa_ausentismo"], 0.0)
        self.assertEqual(efecto["sin_confirmar"]["tasa_ausentismo"], 100.0)
        self.assertEqual(efecto["reduccion_pp"], 100.0)

    def test_pacientes_en_riesgo(self):
        self.crear_cita(hora=9, estado="no_asistio", dia=self.lunes_pasado)
        self.crear_cita(hora=10, estado="atendida", dia=self.lunes_pasado)

        r = self.cliente.get("/api/indicadores/pacientes-riesgo").get_json()["pacientes"]
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["paciente_id"], self.paciente.id)
        self.assertEqual(r[0]["tasa_ausentismo"], 50.0)


class TestValidaciones(BaseTest):
    """Validacion de entradas en la capa de API."""

    def setUp(self):
        super().setUp()
        self.login("admin@test.local", "admin123")

    def test_documento_duplicado(self):
        r = self.cliente.post("/api/pacientes", json={"nombre": "A", "apellido": "B", "documento": "D1"})
        self.assertEqual(r.status_code, 409)

    def test_campos_obligatorios(self):
        r = self.cliente.post("/api/pacientes", json={"nombre": "Solo nombre"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("apellido", r.get_json()["campos"])

    def test_fecha_invalida(self):
        r = self.cliente.post("/api/citas", json={
            "paciente_id": self.paciente.id,
            "profesional_id": self.profesional.id,
            "inicio": "no-es-una-fecha",
        })
        self.assertEqual(r.status_code, 400)

    def test_horario_con_franjas_solapadas(self):
        r = self.cliente.put(f"/api/profesionales/{self.profesional.id}/horarios", json={
            "horarios": [
                {"dia_semana": 0, "hora_inicio": "09:00", "hora_fin": "12:00"},
                {"dia_semana": 0, "hora_inicio": "11:00", "hora_fin": "14:00"},
            ]
        })
        self.assertEqual(r.status_code, 400)

    def test_horario_invertido(self):
        r = self.cliente.put(f"/api/profesionales/{self.profesional.id}/horarios", json={
            "horarios": [{"dia_semana": 0, "hora_inicio": "15:00", "hora_fin": "09:00"}]
        })
        self.assertEqual(r.status_code, 400)

    def test_recurso_inexistente(self):
        self.assertEqual(self.cliente.get("/api/pacientes/9999").status_code, 404)
        self.assertEqual(self.cliente.get("/api/citas/9999").status_code, 404)


class TestReacomodoAgenda(BaseTest):
    """
    RF4 - recompactacion de la agenda de un dia.

    El motor no solo elige el hueco de una cita nueva: tambien recompacta un dia
    que quedo fragmentado por cancelaciones y reprogramaciones.
    """

    def setUp(self):
        super().setUp()
        self.login()

    def _dia_fragmentado(self):
        """
        Franja 09:00-12:00 con dos citas mal colocadas:
        09:20-09:50 y 10:10-10:40. Deja 20 + 20 min inutilizables (< 30).
        """
        a = self.crear_cita(hora=9, minuto=20, duracion=30)
        b = self.crear_cita(hora=10, minuto=10, duracion=30, paciente=self.otro_paciente)
        return a, b

    def _previa(self, **params):
        consulta = {"profesional_id": self.profesional.id, "fecha": self.lunes.isoformat()}
        consulta.update(params)
        cadena = "&".join(f"{k}={v}" for k, v in consulta.items())
        return self.cliente.get(f"/api/citas/reacomodo?{cadena}")

    def test_detecta_los_minutos_muertos(self):
        self._dia_fragmentado()
        datos = self._previa().get_json()

        self.assertEqual(datos["antes"]["minutos_muertos"], 40)
        self.assertEqual(datos["despues"]["minutos_muertos"], 0)
        self.assertEqual(datos["mejora"]["minutos_muertos_recuperados"], 40)
        self.assertTrue(datos["aplicable"])

    def test_la_previa_no_toca_la_base_de_datos(self):
        a, _ = self._dia_fragmentado()
        inicio_original = a.inicio
        self._previa()
        db.session.refresh(a)
        self.assertEqual(a.inicio, inicio_original, "la vista previa no debe mover nada")

    def test_aplicar_compacta_las_citas(self):
        a, b = self._dia_fragmentado()
        r = self.cliente.post("/api/citas/reacomodo", json={
            "profesional_id": self.profesional.id, "fecha": self.lunes.isoformat(),
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["aplicados"], 2)

        db.session.refresh(a)
        db.session.refresh(b)
        self.assertEqual(a.inicio.time(), time(9, 0))
        self.assertEqual(b.inicio.time(), time(9, 30))

    def test_nunca_retrasa_una_cita(self):
        # Una agenda ya compacta no debe generar ningun movimiento
        self.crear_cita(hora=9, minuto=0, duracion=30)
        self.crear_cita(hora=9, minuto=30, duracion=30, paciente=self.otro_paciente)

        datos = self._previa().get_json()
        self.assertEqual(datos["movimientos"], [])
        self.assertFalse(datos["aplicable"])

    def test_el_tope_de_adelanto_limita_el_movimiento(self):
        self.crear_cita(hora=11, minuto=0, duracion=30)
        # Sin tope la cita se iria a las 09:00; con 30 min de tope, solo a las 10:30
        datos = self._previa(max_adelanto_min=30).get_json()
        movimiento = datos["movimientos"][0]
        self.assertEqual(datetime.fromisoformat(movimiento["inicio_propuesto"]).time(), time(10, 30))
        self.assertEqual(movimiento["minutos_adelanto"], 30)

    def test_solo_pendientes_respeta_las_confirmadas(self):
        self.crear_cita(hora=11, minuto=0, duracion=30, estado="confirmada")

        completo = self._previa().get_json()
        self.assertEqual(len(completo["movimientos"]), 1)
        self.assertTrue(completo["movimientos"][0]["requiere_aviso"],
                        "mover una cita confirmada obliga a avisar al paciente")

        restringido = self._previa(solo_pendientes="true").get_json()
        self.assertEqual(restringido["movimientos"], [])

    def test_no_invade_otra_cita_del_mismo_paciente(self):
        otro = Profesional(nombre="Carlos", apellido="Rivas", duracion_cita_min=30)
        otro.horarios.append(HorarioAtencion(dia_semana=0, hora_inicio=time(9, 0), hora_fin=time(12, 0)))
        db.session.add(otro)
        db.session.commit()

        # El paciente ya esta con otro profesional de 09:00 a 09:30
        db.session.add(Cita(
            paciente_id=self.paciente.id, profesional_id=otro.id,
            inicio=datetime.combine(self.lunes, time(9, 0)),
            fin=datetime.combine(self.lunes, time(9, 30)), estado="confirmada",
        ))
        db.session.commit()
        self.crear_cita(hora=11, minuto=0, duracion=30)

        datos = self._previa().get_json()
        propuesto = datetime.fromisoformat(datos["movimientos"][0]["inicio_propuesto"])
        self.assertGreaterEqual(propuesto.time(), time(9, 30),
                                "un paciente no puede estar en dos consultas a la vez")

    def test_no_propone_un_reacomodo_que_empeora_el_dia(self):
        """
        Compactar a la izquierda es voraz: con el tope de adelanto, la cita queda
        a media franja y parte el hueco grande en uno inutilizable.

        Franja 09:00-12:00, cita 11:20-11:50 (10 min muertos al final). Adelantarla
        el maximo permitido (2 h) la deja a las 09:20 y crea 20 min muertos al
        principio: el motor debe descartar la propuesta en lugar de ofrecerla.
        """
        self.crear_cita(hora=11, minuto=20, duracion=30)

        datos = self._previa().get_json()
        self.assertFalse(datos["aplicable"])
        self.assertEqual(datos["movimientos"], [])
        self.assertIn("tiempo muerto", datos["mensaje"])
        self.assertEqual(datos["antes"], datos["despues"])

    def test_dia_sin_citas(self):
        datos = self._previa().get_json()
        self.assertFalse(datos["aplicable"])
        self.assertIn("No hay citas", datos["mensaje"])


class TestRecordatorios(BaseTest):
    """RF5 - registro de los contactos de confirmacion previos a la cita."""

    def setUp(self):
        super().setUp()
        self.login()

    def test_registrar_recordatorio_incrementa_el_contador(self):
        cita = self.crear_cita(hora=9, estado="pendiente")
        r = self.cliente.post(f"/api/citas/{cita.id}/recordatorio")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["recordatorios_enviados"], 1)
        self.assertIsNotNone(r.get_json()["ultimo_recordatorio_en"])

        r = self.cliente.post(f"/api/citas/{cita.id}/recordatorio")
        self.assertEqual(r.get_json()["recordatorios_enviados"], 2)

    def test_no_se_recuerda_una_cita_cerrada(self):
        cita = self.crear_cita(hora=9, estado="atendida")
        self.assertEqual(self.cliente.post(f"/api/citas/{cita.id}/recordatorio").status_code, 409)

    def test_lista_por_confirmar_prioriza_por_riesgo(self):
        # Paciente con historial de inasistencia -> debe encabezar la lista de llamadas
        self.crear_cita(hora=9, estado="no_asistio", dia=date.today() - timedelta(days=14))
        self.crear_cita(hora=9, minuto=0, estado="pendiente")
        self.crear_cita(hora=10, minuto=0, estado="pendiente", paciente=self.otro_paciente)

        datos = self.cliente.get("/api/citas/por-confirmar?dias=30").get_json()
        self.assertEqual(datos["total"], 2)
        self.assertEqual(datos["citas"][0]["paciente_id"], self.paciente.id)
        self.assertEqual(datos["citas"][0]["riesgo"], "alto")
        self.assertEqual(datos["sin_contactar"], 2)


class TestPlanPruebasEntregable3(BaseTest):
    """
    Plan de pruebas funcionales documentado en el Entregable 3 (seccion 3.5).

    Cada prueba reproduce una fila de la tabla del informe, con su mismo
    identificador, para que la evidencia escrita sea verificable sobre el codigo.
    """

    def setUp(self):
        super().setUp()
        self.login()

    def test_P1_crear_cita_en_hueco_libre(self):
        """P1 | prof=1, pac=1, 09:00 -> cita creada en estado 'pendiente'."""
        r = self.cliente.post("/api/citas", json={
            "paciente_id": self.paciente.id,
            "profesional_id": self.profesional.id,
            "inicio": datetime.combine(self.lunes, time(9, 0)).isoformat(),
            "duracion_min": 30,
        })
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["estado"], "pendiente")

    def test_P2_evitar_solapamiento(self):
        """P2 | ocupadas 09:00-09:30 y 10:00-10:30, dur=30 -> el hueco sugerido es 09:30."""
        jornada_ini = datetime.combine(self.lunes, time(9, 0))
        jornada_fin = datetime.combine(self.lunes, time(13, 0))
        ocupadas = [
            {"inicio": jornada_ini, "fin": jornada_ini + timedelta(minutes=30)},
            {"inicio": datetime.combine(self.lunes, time(10, 0)),
             "fin": datetime.combine(self.lunes, time(10, 30))},
        ]
        hueco = scheduler.sugerir_hueco(jornada_ini, jornada_fin, 30, ocupadas)
        self.assertEqual(hueco.time(), time(9, 30))

    def test_P2b_jornada_llena_no_devuelve_hueco(self):
        """Complemento de P2: sin espacio, la heuristica devuelve None."""
        ini = datetime.combine(self.lunes, time(9, 0))
        fin = datetime.combine(self.lunes, time(10, 0))
        ocupadas = [{"inicio": ini, "fin": fin}]
        self.assertIsNone(scheduler.sugerir_hueco(ini, fin, 30, ocupadas))

    def test_P3_confirmar_cita(self):
        """P3 | PATCH estado='confirmada' -> estado actualizado."""
        cita = self.crear_cita(hora=9, estado="pendiente")
        r = self.cliente.patch(f"/api/citas/{cita.id}/estado", json={"estado": "confirmada"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["estado"], "confirmada")
        self.assertIsNotNone(r.get_json()["confirmada_en"])

    def test_P4_indicador_de_ausentismo(self):
        """P4 | 1 cita 'no_asistio' sobre 1 cerrada -> tasa = 100 %."""
        ayer = date.today() - timedelta(days=1)
        self.crear_cita(hora=9, estado="no_asistio", dia=ayer)

        kpis = self.cliente.get("/api/indicadores/resumen").get_json()["kpis"]
        self.assertEqual(kpis["citas_cerradas"], 1)
        self.assertEqual(kpis["ausencias"], 1)
        self.assertEqual(kpis["tasa_ausentismo"], 100.0)

    def test_P5_rechazar_estado_invalido(self):
        """P5 | PATCH estado='xyz' -> error 400."""
        cita = self.crear_cita(hora=9, estado="pendiente")
        r = self.cliente.patch(f"/api/citas/{cita.id}/estado", json={"estado": "xyz"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("estados_validos", r.get_json())


if __name__ == "__main__":
    unittest.main(verbosity=2)
