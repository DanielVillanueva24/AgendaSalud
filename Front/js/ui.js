/* =============================================================
   Utilidades de interfaz: avisos, modales, formatos y tablas.
   ============================================================= */

const UI = (() => {

  const ESTADOS = {
    pendiente:  { texto: 'Pendiente' },
    confirmada: { texto: 'Confirmada' },
    atendida:   { texto: 'Atendida' },
    cancelada:  { texto: 'Cancelada' },
    no_asistio: { texto: 'No asistió' },
  };

  const DIAS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'];

  /* --- Escape y formato --------------------------------------------------- */

  function esc(valor) {
    if (valor === null || valor === undefined) return '';
    return String(valor)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  const fmtFecha = new Intl.DateTimeFormat('es', { day: '2-digit', month: 'short', year: 'numeric' });
  const fmtFechaLarga = new Intl.DateTimeFormat('es', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  const fmtHora = new Intl.DateTimeFormat('es', { hour: '2-digit', minute: '2-digit', hour12: false });

  function fecha(iso) { return iso ? fmtFecha.format(new Date(iso)) : '—'; }
  function fechaLarga(iso) { return iso ? fmtFechaLarga.format(new Date(iso)) : '—'; }
  function hora(iso) { return iso ? fmtHora.format(new Date(iso)) : '—'; }
  function fechaHora(iso) { return iso ? `${fecha(iso)} · ${hora(iso)}` : '—'; }

  /** Convierte un Date a 'YYYY-MM-DDTHH:MM' en hora local (para <input datetime-local> y la API). */
  function isoLocal(d) {
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function isoFecha(d) { return isoLocal(d).slice(0, 10); }

  function edad(isoNacimiento) {
    if (!isoNacimiento) return null;
    const n = new Date(isoNacimiento), h = new Date();
    let a = h.getFullYear() - n.getFullYear();
    const m = h.getMonth() - n.getMonth();
    if (m < 0 || (m === 0 && h.getDate() < n.getDate())) a--;
    return a;
  }

  function tag(estado) {
    const info = ESTADOS[estado] || { texto: estado };
    return `<span class="tag tag-${esc(estado)}">${esc(info.texto)}</span>`;
  }

  function porcentaje(v) {
    return (v === null || v === undefined) ? '—' : `${Number(v).toFixed(1).replace('.0', '')}%`;
  }

  /* --- Avisos (toasts) ---------------------------------------------------- */

  function aviso(mensaje, tipo = 'info', ms = 4000) {
    const cont = document.getElementById('avisos');
    const el = document.createElement('div');
    el.className = `aviso ${tipo}`;
    el.innerHTML = `<span>${esc(mensaje)}</span>`;
    cont.appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity .2s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 220);
    }, ms);
  }

  const exito = (m) => aviso(m, 'exito');
  const error = (m) => aviso(m, 'error', 6000);

  /* --- Modales ------------------------------------------------------------ */

  let modalesAbiertos = [];

  /**
   * modal({ titulo, html, ancho, botones:[{texto, clase, id, onClick, cerrar}] })
   * Devuelve { el, cerrar, boton(id) }.
   */
  function modal({ titulo, html, ancho = false, botones = [], alCerrar = null }) {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';

    const pie = botones.length ? `
      <div class="modal-pie">
        ${botones.map((b, i) => `
          <button type="button" class="btn ${b.clase || ''} ${b.izquierda ? 'izquierda' : ''}"
                  data-boton="${i}">${esc(b.texto)}</button>`).join('')}
      </div>` : '';

    overlay.innerHTML = `
      <div class="modal ${ancho ? 'ancho' : ''}" role="dialog" aria-modal="true">
        <div class="modal-cabecera">
          <h3>${esc(titulo)}</h3>
          <button type="button" class="modal-cerrar" aria-label="Cerrar">&times;</button>
        </div>
        <div class="modal-cuerpo">${html}</div>
        ${pie}
      </div>`;

    const api = {
      el: overlay,
      cerrar() {
        overlay.remove();
        modalesAbiertos = modalesAbiertos.filter(m => m !== api);
        document.removeEventListener('keydown', onEsc);
        if (alCerrar) alCerrar();
      },
      $(sel) { return overlay.querySelector(sel); },
      $$(sel) { return Array.from(overlay.querySelectorAll(sel)); },
      cargando(activo, textoBoton) {
        api.$$('.modal-pie .btn').forEach(b => b.disabled = activo);
        const principal = api.$('.modal-pie .btn-primario, .modal-pie .btn-exito, .modal-pie .btn-peligro');
        if (principal && activo) {
          principal.dataset.textoOriginal = principal.dataset.textoOriginal || principal.textContent;
          principal.innerHTML = `<span class="spinner"></span> ${esc(textoBoton || 'Guardando…')}`;
        } else if (principal && principal.dataset.textoOriginal) {
          principal.textContent = principal.dataset.textoOriginal;
        }
      },
      alerta(mensaje, tipo = '') {
        let caja = api.$('.modal-cuerpo .alerta-inline.js-alerta');
        if (!mensaje) { if (caja) caja.remove(); return; }
        if (!caja) {
          caja = document.createElement('div');
          caja.className = 'alerta-inline js-alerta';
          api.$('.modal-cuerpo').prepend(caja);
        }
        caja.className = `alerta-inline js-alerta ${tipo}`;
        caja.textContent = mensaje;
      },
    };

    function onEsc(e) {
      if (e.key === 'Escape' && modalesAbiertos[modalesAbiertos.length - 1] === api) api.cerrar();
    }

    overlay.querySelector('.modal-cerrar').addEventListener('click', () => api.cerrar());
    overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) api.cerrar(); });
    document.addEventListener('keydown', onEsc);

    botones.forEach((b, i) => {
      const btn = overlay.querySelector(`[data-boton="${i}"]`);
      btn.addEventListener('click', async () => {
        if (b.onClick) {
          const resultado = await b.onClick(api);
          if (resultado === false) return;   // el handler cancela el cierre
        }
        if (b.cerrar !== false) api.cerrar();
      });
    });

    document.getElementById('modales').appendChild(overlay);
    modalesAbiertos.push(api);

    const primerCampo = overlay.querySelector(
      'input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea:not([disabled])');
    if (primerCampo) setTimeout(() => primerCampo.focus(), 40);

    return api;
  }

  /** Confirmacion simple. Devuelve una promesa booleana. */
  function confirmar({ titulo = '¿Confirmar?', mensaje, textoOk = 'Confirmar', claseOk = 'btn-primario' }) {
    return new Promise(resolve => {
      let decidido = false;
      modal({
        titulo,
        html: `<p style="margin:0;line-height:1.55">${esc(mensaje)}</p>`,
        botones: [
          { texto: 'Cancelar' },
          { texto: textoOk, clase: claseOk, onClick: () => { decidido = true; resolve(true); } },
        ],
        alCerrar: () => { if (!decidido) resolve(false); },
      });
    });
  }

  /* --- Tablas ------------------------------------------------------------- */

  /** tabla(columnas, filas, opciones) -> HTML. columnas: [{titulo, clase}] */
  function tabla(columnas, filasHtml, { vacio = 'Sin resultados' } = {}) {
    if (!filasHtml || !filasHtml.length) {
      return `<div class="vacio">${esc(vacio)}</div>`;
    }
    return `
      <table class="tabla">
        <thead><tr>${columnas.map(c =>
          `<th class="${c.clase || ''}">${esc(c.titulo)}</th>`).join('')}</tr></thead>
        <tbody>${filasHtml.join('')}</tbody>
      </table>`;
  }

  function cargando(contenedor, texto = 'Cargando…') {
    const el = typeof contenedor === 'string' ? document.getElementById(contenedor) : contenedor;
    if (el) el.innerHTML = `<div class="cargando">${esc(texto)}</div>`;
  }

  function kpi({ etiqueta, valor, detalle, tono = '' }) {
    return `
      <div class="kpi ${tono}">
        <div class="etiqueta">${esc(etiqueta)}</div>
        <div class="valor">${esc(valor)}</div>
        <div class="detalle">${esc(detalle || '')}</div>
      </div>`;
  }

  /* --- Errores de la API -------------------------------------------------- */

  function mostrarError(e, contextoModal) {
    const mensaje = e && e.message ? e.message : 'Ocurrió un error inesperado';
    if (contextoModal) contextoModal.alerta(mensaje);
    else error(mensaje);
    if (!(e instanceof API.ErrorAPI)) console.error(e);
  }

  return {
    ESTADOS, DIAS,
    esc, fecha, fechaLarga, hora, fechaHora, isoLocal, isoFecha, edad, tag, porcentaje,
    aviso, exito, error, mostrarError,
    modal, confirmar, tabla, cargando, kpi,
  };
})();
