/* =============================================================
   Vista y formularios de pacientes (RF2).
   ============================================================= */

const PacientesUI = (() => {

  function abrirFormulario(paciente = null, alGuardar = null) {
    const edicion = Boolean(paciente);

    const m = UI.modal({
      titulo: edicion ? 'Editar paciente' : 'Nuevo paciente',
      html: `
        <form id="form-paciente">
          <div class="fila">
            <div class="campo">
              <label for="pac-nombre">Nombre *</label>
              <input type="text" id="pac-nombre" required maxlength="80" value="${UI.esc(paciente?.nombre || '')}">
            </div>
            <div class="campo">
              <label for="pac-apellido">Apellido *</label>
              <input type="text" id="pac-apellido" required maxlength="80" value="${UI.esc(paciente?.apellido || '')}">
            </div>
          </div>
          <div class="fila">
            <div class="campo">
              <label for="pac-documento">Documento de identidad</label>
              <input type="text" id="pac-documento" maxlength="40" value="${UI.esc(paciente?.documento || '')}">
            </div>
            <div class="campo">
              <label for="pac-nacimiento">Fecha de nacimiento</label>
              <input type="date" id="pac-nacimiento" value="${UI.esc(paciente?.fecha_nacimiento || '')}">
            </div>
          </div>
          <div class="fila">
            <div class="campo">
              <label for="pac-telefono">Teléfono</label>
              <input type="tel" id="pac-telefono" maxlength="40" value="${UI.esc(paciente?.telefono || '')}">
              <div class="ayuda">Necesario para los recordatorios de confirmación.</div>
            </div>
            <div class="campo">
              <label for="pac-email">Correo electrónico</label>
              <input type="email" id="pac-email" maxlength="160" value="${UI.esc(paciente?.email || '')}">
            </div>
          </div>
          <div class="campo">
            <label for="pac-notas">Notas</label>
            <textarea id="pac-notas" maxlength="1000">${UI.esc(paciente?.notas || '')}</textarea>
          </div>
          ${edicion ? `
            <label class="check">
              <input type="checkbox" id="pac-activo" ${paciente.activo ? 'checked' : ''}> Paciente activo
            </label>` : ''}
        </form>`,
      botones: [
        { texto: 'Cancelar' },
        { texto: edicion ? 'Guardar cambios' : 'Registrar paciente', clase: 'btn-primario', cerrar: false,
          onClick: (mm) => guardar(mm, paciente, alGuardar) },
      ],
    });

    m.$('#form-paciente').addEventListener('submit', (e) => {
      e.preventDefault();
      guardar(m, paciente, alGuardar);
    });
    return m;
  }

  async function guardar(m, paciente, alGuardar) {
    const datos = {
      nombre: m.$('#pac-nombre').value.trim(),
      apellido: m.$('#pac-apellido').value.trim(),
      documento: m.$('#pac-documento').value.trim(),
      fecha_nacimiento: m.$('#pac-nacimiento').value || null,
      telefono: m.$('#pac-telefono').value.trim(),
      email: m.$('#pac-email').value.trim(),
      notas: m.$('#pac-notas').value.trim(),
    };
    if (m.$('#pac-activo')) datos.activo = m.$('#pac-activo').checked;

    if (!datos.nombre || !datos.apellido) return m.alerta('El nombre y el apellido son obligatorios.');

    m.alerta(null);
    m.cargando(true);
    try {
      const guardado = paciente
        ? await API.pacientes.actualizar(paciente.id, datos)
        : await API.pacientes.crear(datos);

      UI.exito(paciente ? 'Paciente actualizado.' : 'Paciente registrado.');
      m.cerrar();
      await App.cargarPacientes();
      if (alGuardar) alGuardar(guardado);
      else App.refrescar();
    } catch (e) {
      m.cargando(false);
      UI.mostrarError(e, m);
      return false;
    }
  }

  /* --- Ficha con historial ------------------------------------------------- */

  async function abrirFicha(id) {
    let p;
    try { p = await API.pacientes.detalle(id); } catch (e) { return UI.mostrarError(e); }

    const a = p.asistencia;
    const anios = UI.edad(p.fecha_nacimiento);
    const tono = a.tasa_ausentismo >= 20 ? 'alerta' : a.tasa_ausentismo > 0 ? 'aviso' : 'ok';

    UI.modal({
      titulo: p.nombre_completo,
      ancho: true,
      html: `
        <dl class="detalle-lista">
          <dt>Documento</dt><dd>${UI.esc(p.documento || '—')}</dd>
          <dt>Teléfono</dt><dd>${UI.esc(p.telefono || '—')}</dd>
          <dt>Correo</dt><dd>${UI.esc(p.email || '—')}</dd>
          <dt>Nacimiento</dt><dd>${p.fecha_nacimiento ? `${UI.fecha(p.fecha_nacimiento)} (${anios} años)` : '—'}</dd>
          ${p.notas ? `<dt>Notas</dt><dd>${UI.esc(p.notas)}</dd>` : ''}
        </dl>

        <div class="kpis" style="margin:18px 0 14px">
          ${UI.kpi({ etiqueta: 'Citas cerradas', valor: a.citas_cerradas, detalle: 'Atendidas + inasistencias' })}
          ${UI.kpi({ etiqueta: 'Inasistencias', valor: a.ausencias, detalle: 'Sin aviso previo', tono: a.ausencias ? 'alerta' : 'ok' })}
          ${UI.kpi({ etiqueta: 'Tasa de ausentismo', valor: UI.porcentaje(a.tasa_ausentismo), detalle: 'Historial del paciente', tono })}
        </div>

        <h4 style="font-size:13px;margin-bottom:8px">Historial de citas</h4>
        <div class="tabla-scroll" style="max-height:290px;overflow-y:auto;border:1px solid var(--borde);border-radius:var(--radio-sm)">
          ${UI.tabla(
            [{ titulo: 'Fecha' }, { titulo: 'Profesional' }, { titulo: 'Motivo' }, { titulo: 'Estado' }],
            p.citas.map(c => `
              <tr>
                <td>${UI.fecha(c.inicio)} · ${UI.hora(c.inicio)}</td>
                <td>${UI.esc(c.profesional_nombre)}</td>
                <td>${UI.esc(c.motivo || '—')}</td>
                <td>${UI.tag(c.estado)}</td>
              </tr>`),
            { vacio: 'Este paciente aún no tiene citas registradas.' }
          )}
        </div>`,
      botones: [
        { texto: 'Cerrar' },
        ...(App.puede('recepcion') ? [
          { texto: 'Editar datos', onClick: () => abrirFormulario(p) },
          { texto: 'Agendar cita', clase: 'btn-primario', onClick: () => CitasUI.abrirFormulario(null, { paciente_id: p.id }) },
        ] : []),
      ],
    });
  }

  /* --- Vista de listado ---------------------------------------------------- */

  const Vista = {
    titulo: 'Pacientes',
    subtitulo: 'Registro de pacientes del consultorio',

    acciones() {
      return App.puede('recepcion')
        ? [{ texto: '+ Nuevo paciente', clase: 'btn-primario', onClick: () => abrirFormulario() }]
        : [];
    },

    montar() {
      const buscar = document.getElementById('buscar-paciente');
      let temporizador;

      buscar.addEventListener('input', () => {
        clearTimeout(temporizador);
        temporizador = setTimeout(() => Vista.refrescar(), 260);
      });
      document.getElementById('pacientes-estado')
        .addEventListener('change', () => Vista.refrescar());
    },

    async refrescar() {
      const cont = document.getElementById('pacientes-tabla');
      const conteo = document.getElementById('pacientes-conteo');
      // "Solo inactivos" no existe como filtro en la API: se piden todos y se
      // descartan aqui los activos, que son unos pocos cientos como mucho.
      const filtro = document.getElementById('pacientes-estado').value;
      UI.cargando(cont);

      try {
        const r = await API.pacientes.listar({
          q: document.getElementById('buscar-paciente').value,
          incluir_inactivos: filtro === 'activos' ? '' : 1,
          con_estadisticas: 1,
          limite: 300,
        });

        const lista = filtro === 'inactivos' ? r.pacientes.filter(p => !p.activo) : r.pacientes;
        const inactivos = r.pacientes.filter(p => !p.activo).length;

        conteo.textContent = filtro === 'activos'
          ? `${lista.length} paciente(s) activo(s).`
          : `${lista.length} paciente(s) en pantalla · ${inactivos} dado(s) de baja.`;

        const gestiona = App.puede('recepcion');
        cont.innerHTML = UI.tabla(
          [
            { titulo: 'Paciente' }, { titulo: 'Documento' }, { titulo: 'Contacto' },
            { titulo: 'Citas', clase: 'num' }, { titulo: 'Ausentismo', clase: 'num' },
            { titulo: '', clase: 'num' },
          ],
          lista.map(p => {
            const a = p.asistencia;
            const tono = a.tasa_ausentismo >= 20 ? 'tag-no_asistio'
                       : a.tasa_ausentismo > 0 ? 'tag-pendiente' : 'tag-atendida';
            return `
              <tr${p.activo ? '' : ' class="fila-inactiva"'}>
                <td>
                  <a href="#" data-ficha="${p.id}"><strong>${UI.esc(p.nombre_completo)}</strong></a>
                  ${p.activo ? '' : ' <span class="tag tag-neutro">Inactivo</span>'}
                </td>
                <td>${UI.esc(p.documento || '—')}</td>
                <td>${UI.esc(p.telefono || p.email || '—')}</td>
                <td class="num">${a.citas_cerradas}</td>
                <td class="num">${a.citas_cerradas ? `<span class="tag ${tono}">${UI.porcentaje(a.tasa_ausentismo)}</span>` : '—'}</td>
                <td class="acciones">
                  ${gestiona ? `
                    <button class="btn btn-sm" data-editar="${p.id}">Editar</button>
                    ${p.activo
                      ? `<button class="btn btn-sm btn-primario" data-agendar="${p.id}">Agendar</button>
                         <button class="btn btn-sm" data-baja="${p.id}"
                                 data-nombre="${UI.esc(p.nombre_completo)}">Dar de baja</button>`
                      : `<button class="btn btn-sm btn-exito" data-alta="${p.id}">Reactivar</button>`}` : ''}
                </td>
              </tr>`;
          }),
          { vacio: filtro === 'inactivos'
              ? 'No hay pacientes dados de baja.'
              : 'No se encontraron pacientes con esos criterios.' }
        );

        cont.querySelectorAll('[data-ficha]').forEach(a => a.addEventListener('click', (e) => {
          e.preventDefault(); abrirFicha(Number(a.dataset.ficha));
        }));
        cont.querySelectorAll('[data-editar]').forEach(b => b.addEventListener('click', async () => {
          const p = await API.pacientes.detalle(Number(b.dataset.editar));
          abrirFormulario(p);
        }));
        cont.querySelectorAll('[data-agendar]').forEach(b => b.addEventListener('click', () => {
          CitasUI.abrirFormulario(null, { paciente_id: Number(b.dataset.agendar) });
        }));
        cont.querySelectorAll('[data-baja]').forEach(b =>
          b.addEventListener('click', () => darDeBaja(Number(b.dataset.baja), b.dataset.nombre)));
        cont.querySelectorAll('[data-alta]').forEach(b =>
          b.addEventListener('click', () => reactivar(Number(b.dataset.alta))));
      } catch (e) {
        conteo.textContent = '';
        cont.innerHTML = `<div class="vacio">${UI.esc(e.message)}</div>`;
      }
    },
  };

  /* --- Alta y baja logica -------------------------------------------------- */

  async function darDeBaja(id, nombre) {
    const ok = await UI.confirmar({
      titulo: 'Dar de baja al paciente',
      mensaje: `${nombre} dejará de aparecer en el listado y no se le podrán agendar citas nuevas. `
             + 'Su historial se conserva y puedes reactivarlo cuando quieras.',
      textoOk: 'Dar de baja',
      claseOk: 'btn-peligro',
    });
    if (!ok) return;

    try {
      await API.pacientes.desactivar(id);
      UI.exito('Paciente dado de baja.');
      await App.cargarPacientes();
      Vista.refrescar();
    } catch (e) {
      UI.mostrarError(e);
    }
  }

  async function reactivar(id) {
    try {
      await API.pacientes.actualizar(id, { activo: true });
      UI.exito('Paciente reactivado.');
      await App.cargarPacientes();
      Vista.refrescar();
    } catch (e) {
      UI.mostrarError(e);
    }
  }

  return { abrirFormulario, abrirFicha, Vista };
})();
