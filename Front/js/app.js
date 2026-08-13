/* =============================================================
   Arranque, sesion y navegacion entre vistas.
   ============================================================= */

const App = (() => {

  const estado = {
    usuario: null,
    profesionales: [],
    pacientes: [],
    vista: null,
  };

  const VISTAS = {
    hoy: HoyUI.Vista,
    agenda: CalendarioUI.Vista,
    pacientes: PacientesUI.Vista,
    profesionales: ProfesionalesUI.Vista,
    indicadores: IndicadoresUI.Vista,
    usuarios: UsuariosUI.Vista,
  };

  const montadas = new Set();

  /* --- Permisos ------------------------------------------------------------ */

  /** puede('recepcion','profesional') -> true si el usuario tiene alguno de esos roles (admin siempre). */
  function puede(...roles) {
    if (!estado.usuario) return false;
    if (estado.usuario.rol === 'admin') return true;
    return roles.includes(estado.usuario.rol);
  }

  // El profesional solo registra asistencia: confirmar, cancelar y reactivar
  // son de recepcion. Espeja la regla del backend (citas.py ESTADOS_ASISTENCIA).
  const ESTADOS_ASISTENCIA = ['atendida', 'no_asistio'];

  /** puedeFijarEstado('confirmada') -> false para un profesional. */
  function puedeFijarEstado(nuevoEstado) {
    if (!puede('recepcion', 'profesional')) return false;
    if (estado.usuario.rol === 'profesional') return ESTADOS_ASISTENCIA.includes(nuevoEstado);
    return true;
  }

  function profesional(id) {
    return estado.profesionales.find(p => p.id === Number(id)) || null;
  }

  /* --- Catalogos en cache -------------------------------------------------- */

  async function cargarProfesionales() {
    const r = await API.profesionales.listar();
    estado.profesionales = r.profesionales;

    // Un profesional solo tiene su propia agenda: el selector no ofrece ninguna
    // eleccion, asi que se oculta en vez de mostrar un desplegable de un solo item.
    const soloPropia = estado.usuario && estado.usuario.rol === 'profesional';

    ['filtro-profesional', 'ind-profesional'].forEach(id => {
      const sel = document.getElementById(id);
      if (!sel) return;
      const campo = sel.closest('.campo');
      if (campo) campo.hidden = soloPropia;

      const previo = sel.value;
      sel.innerHTML = (soloPropia ? '' : '<option value="">Todos</option>') +
        estado.profesionales.map(p =>
          `<option value="${p.id}">${UI.esc(p.nombre_completo)}</option>`).join('');
      if (previo) sel.value = previo;
    });
  }

  async function cargarPacientes() {
    const r = await API.pacientes.listar({ limite: 500 });
    estado.pacientes = r.pacientes;
  }

  /* --- Navegacion ---------------------------------------------------------- */

  function irA(nombre) {
    const vista = VISTAS[nombre];
    if (!vista) return;

    estado.vista = nombre;
    localStorage.setItem('agendasalud.vista', nombre);

    document.querySelectorAll('.nav-item').forEach(b =>
      b.classList.toggle('activo', b.dataset.vista === nombre));
    document.querySelectorAll('.vista').forEach(s =>
      s.hidden = s.id !== `vista-${nombre}`);

    document.getElementById('vista-titulo').textContent = vista.titulo;
    document.getElementById('vista-subtitulo').textContent = vista.subtitulo || '';

    const acciones = document.getElementById('vista-acciones');
    acciones.innerHTML = '';
    (vista.acciones ? vista.acciones() : []).forEach(a => {
      const btn = document.createElement('button');
      btn.className = `btn ${a.clase || ''}`;
      btn.textContent = a.texto;
      btn.addEventListener('click', a.onClick);
      acciones.appendChild(btn);
    });

    if (!montadas.has(nombre)) {
      montadas.add(nombre);
      if (vista.montar) vista.montar();
    }
    vista.refrescar();
  }

  /** Refresca la vista activa (tras crear/editar algo). */
  function refrescar() {
    const vista = VISTAS[estado.vista];
    if (vista) vista.refrescar();
  }

  /** Igual que refrescar(), pero sin re-renderizar el calendario si es la vista activa. */
  function refrescarSilencioso() {
    if (estado.vista !== 'agenda') refrescar();
  }

  /* --- Sincronizacion entre navegadores ------------------------------------ */
  //
  // Sin websockets: el README pide no meter frameworks pesados, asi que en vez
  // de una conexion permanente se consulta una huella muy barata de la agenda
  // (un COUNT y un MAX) y solo se repinta cuando otro usuario la ha cambiado.
  // Asi una cita creada por recepcion aparece sola en la pantalla del profesional.

  const INTERVALO_SINCRONIA = 4000;
  let versionAgenda = null;
  let temporizadorSincronia = null;

  async function comprobarCambios() {
    // Sin sesion no hay nada que mirar; con la pestaña oculta no se gasta red
    if (!estado.usuario || document.hidden) return;
    try {
      const { version } = await API.citas.version();
      if (versionAgenda === null) {
        versionAgenda = version;          // primera lectura: solo se toma la referencia
      } else if (version !== versionAgenda) {
        versionAgenda = version;
        refrescar();                      // aqui si interesa repintar la agenda
      }
    } catch (e) {
      // Servidor caido o sesion expirada: se reintenta en el siguiente ciclo
    }
  }

  function iniciarSincronia() {
    detenerSincronia();
    versionAgenda = null;
    temporizadorSincronia = setInterval(comprobarCambios, INTERVALO_SINCRONIA);
    // Al volver a la pestaña se comprueba en el acto, sin esperar al ciclo
    document.addEventListener('visibilitychange', alVolverAlaPestana);
  }

  function detenerSincronia() {
    clearInterval(temporizadorSincronia);
    temporizadorSincronia = null;
    document.removeEventListener('visibilitychange', alVolverAlaPestana);
  }

  function alVolverAlaPestana() {
    if (!document.hidden) comprobarCambios();
  }

  /** Tras un cambio propio: evita que el sondeo lo detecte y repinte otra vez. */
  function marcarCambioPropio() {
    versionAgenda = null;
  }

  /* --- Sesion -------------------------------------------------------------- */

  function pintarUsuario() {
    const u = estado.usuario;
    const iniciales = u.nombre.split(/\s+/).slice(0, 2).map(p => p[0]).join('').toUpperCase();
    document.getElementById('usuario-avatar').textContent = iniciales;
    document.getElementById('usuario-nombre').textContent = u.nombre;
    document.getElementById('usuario-rol').textContent =
      ({ admin: 'Administrador', recepcion: 'Recepción', profesional: 'Profesional' })[u.rol] || u.rol;

    // Oculta las secciones que el rol no puede usar
    document.querySelectorAll('.nav-item[data-rol]').forEach(b => {
      b.hidden = !puede(b.dataset.rol);
    });
  }

  async function entrar(usuario) {
    estado.usuario = usuario;
    document.getElementById('pantalla-login').hidden = true;
    document.getElementById('app').hidden = false;
    pintarUsuario();

    try {
      await Promise.all([cargarProfesionales(), cargarPacientes()]);
    } catch (e) {
      UI.mostrarError(e);
    }

    const guardada = localStorage.getItem('agendasalud.vista');
    const inicial = (guardada && VISTAS[guardada] && !esVistaProhibida(guardada)) ? guardada : 'hoy';
    irA(inicial);

    iniciarSincronia();
  }

  function esVistaProhibida(nombre) {
    const boton = document.querySelector(`.nav-item[data-vista="${nombre}"]`);
    return Boolean(boton && boton.dataset.rol && !puede(boton.dataset.rol));
  }

  function salir(mensaje) {
    detenerSincronia();
    estado.usuario = null;
    estado.profesionales = [];
    estado.pacientes = [];
    montadas.clear();
    document.getElementById('modales').innerHTML = '';
    document.getElementById('app').hidden = true;
    document.getElementById('pantalla-login').hidden = false;
    document.getElementById('login-password').value = '';
    if (mensaje) UI.aviso(mensaje, 'info');
  }

  /* --- Login --------------------------------------------------------------- */

  function prepararLogin() {
    const form = document.getElementById('form-login');
    const cajaError = document.getElementById('login-error');
    const boton = document.getElementById('btn-login');

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      cajaError.hidden = true;
      boton.disabled = true;
      boton.innerHTML = '<span class="spinner"></span> Entrando…';

      try {
        const r = await API.auth.login(
          document.getElementById('login-email').value.trim(),
          document.getElementById('login-password').value,
        );
        await entrar(r.usuario);
      } catch (err) {
        cajaError.textContent = err.message;
        cajaError.hidden = false;
      } finally {
        boton.disabled = false;
        boton.textContent = 'Iniciar sesión';
      }
    });

    document.querySelectorAll('.credencial').forEach(btn => {
      btn.addEventListener('click', () => {
        document.getElementById('login-email').value = btn.dataset.email;
        document.getElementById('login-password').value = btn.dataset.password;
        form.requestSubmit();
      });
    });
  }

  /* --- Arranque ------------------------------------------------------------ */

  async function iniciar() {
    prepararLogin();

    document.getElementById('nav').addEventListener('click', (e) => {
      const btn = e.target.closest('.nav-item');
      if (btn) irA(btn.dataset.vista);
    });

    document.getElementById('btn-logout').addEventListener('click', async () => {
      try { await API.auth.logout(); } catch (e) { /* la sesion se limpia igualmente */ }
      salir('Sesión cerrada.');
    });

    document.getElementById('usuario-avatar').addEventListener('click', () =>
      UsuariosUI.abrirCambioPassword());
    document.getElementById('usuario-avatar').title = 'Cambiar mi contraseña';
    document.getElementById('usuario-avatar').style.cursor = 'pointer';

    document.addEventListener('sesion-expirada', () =>
      salir('Tu sesión expiró. Vuelve a iniciar sesión.'));

    try {
      const r = await API.auth.sesion();
      if (r.autenticado) return entrar(r.usuario);
    } catch (e) {
      UI.error(e.message);
    }
    document.getElementById('pantalla-login').hidden = false;
  }

  document.addEventListener('DOMContentLoaded', iniciar);

  return {
    estado, puede, puedeFijarEstado, profesional, irA, refrescar, refrescarSilencioso,
    marcarCambioPropio, cargarProfesionales, cargarPacientes,
  };
})();
