/* =============================================================
   Citas: formulario de reserva con motor de sugerencias (RF3/RF4)
   y ficha de detalle con control de estados (RF5).
   ============================================================= */

const CitasUI = (() => {

  /* --- Formulario de alta / edicion --------------------------------------- */

  /**
   * abrirFormulario(cita, prefijo)
   *
   * `prefijo.paciente_id` llega desde la vista de Pacientes ("Agendar" en la
   * fila o en la ficha). En ese caso el paciente ya esta elegido y el formulario
   * lo deja fijo: si se pudiera cambiar, es facilisimo abrir la ficha de un
   * paciente y acabar creandole la cita a otro sin darse cuenta.
   */
  function abrirFormulario(cita = null, prefijo = {}) {
    const edicion = Boolean(cita);
    const inicio = cita ? new Date(cita.inicio)
                        : (prefijo.inicio ? new Date(prefijo.inicio) : proximaHoraRedonda());
    const profesionalId = cita ? cita.profesional_id : (prefijo.profesional_id || '');
    const duracion = cita ? cita.duracion_min : (prefijo.duracion_min || null);

    const pacienteFijo = !edicion && Boolean(prefijo.paciente_id);
    const fijado = pacienteFijo
      ? App.estado.pacientes.find(p => p.id === Number(prefijo.paciente_id))
      : null;

    const m = UI.modal({
      titulo: edicion ? `Editar cita #${cita.id}` : 'Nueva cita',
      ancho: true,
      html: `
        <form id="form-cita">
          <div class="fila">
            <div class="campo">
              <label for="cita-paciente">Paciente *</label>
              ${pacienteFijo ? '' : `
                <input type="search" id="cita-filtro-paciente" placeholder="Filtrar por nombre o documento…" style="margin-bottom:6px">`}
              <select id="cita-paciente" required size="1" ${pacienteFijo ? 'disabled' : ''}></select>
              <div class="ayuda">${pacienteFijo
                ? `Cita para ${UI.esc(fijado ? fijado.nombre_completo : 'el paciente seleccionado')}. `
                  + 'Para agendar a otro, cierra y elígelo en el listado de pacientes.'
                : '<a href="#" id="cita-nuevo-paciente">+ Registrar un paciente nuevo</a>'}</div>
            </div>
            <div class="campo">
              <label for="cita-profesional">Profesional *</label>
              <select id="cita-profesional" required></select>
              <div class="ayuda" id="cita-info-profesional"></div>
            </div>
          </div>

          <div class="fila-3">
            <div class="campo">
              <label for="cita-fecha">Fecha *</label>
              <input type="date" id="cita-fecha" required value="${UI.isoFecha(inicio)}">
            </div>
            <div class="campo">
              <label for="cita-hora">Hora *</label>
              <input type="time" id="cita-hora" required step="300" value="${UI.isoLocal(inicio).slice(11, 16)}">
            </div>
            <div class="campo">
              <label for="cita-duracion">Duración (min) *</label>
              <input type="number" id="cita-duracion" required min="5" max="480" step="5">
            </div>
          </div>

          <div id="cita-estado-slot"></div>

          <div class="campo">
            <label for="cita-motivo">Motivo de la consulta</label>
            <input type="text" id="cita-motivo" maxlength="200" value="${UI.esc(cita?.motivo || '')}">
          </div>
          <div class="campo">
            <label for="cita-notas">Notas internas</label>
            <textarea id="cita-notas" maxlength="1000">${UI.esc(cita?.notas || '')}</textarea>
          </div>

          <div class="panel" style="margin:0;box-shadow:none;background:var(--superficie-alt)">
            <div class="panel-cabecera" style="padding:11px 14px">
              <div>
                <h3 style="font-size:13px">Horarios libres que sugiere el sistema</h3>
                <p>Busca el hueco que menos hace esperar al paciente y que menos parte la agenda.</p>
              </div>
              <div class="filtros" style="align-items:flex-end">
                <div class="campo" style="margin:0;min-width:150px">
                  <label for="sug-fecha">Buscar solo el día</label>
                  <input type="date" id="sug-fecha" min="${UI.isoFecha(new Date())}">
                  <div class="ayuda">Déjalo vacío para buscar en las próximas 3 semanas.</div>
                </div>
                <button type="button" class="btn btn-sm" id="btn-sugerir">Sugerir horarios</button>
              </div>
            </div>
            <div class="panel-cuerpo" id="cita-sugerencias" hidden></div>
          </div>
        </form>`,
      botones: [
        { texto: 'Cancelar' },
        {
          texto: edicion ? 'Guardar cambios' : 'Agendar cita',
          clase: 'btn-primario',
          cerrar: false,
          onClick: (modal) => guardar(modal, cita),
        },
      ],
    });

    // --- Poblado de selects
    const selPaciente = m.$('#cita-paciente');
    const selProfesional = m.$('#cita-profesional');

    function pintarPacientes(filtro = '') {
      // Con el paciente fijado el desplegable solo contiene a ese paciente: no
      // hay nada que elegir ni forma de cambiarlo desde aqui.
      if (pacienteFijo) {
        const nombre = fijado ? fijado.nombre_completo : `Paciente #${prefijo.paciente_id}`;
        selPaciente.innerHTML = `<option value="${prefijo.paciente_id}">${UI.esc(nombre)}</option>`;
        selPaciente.value = String(prefijo.paciente_id);
        return;
      }

      const texto = filtro.trim().toLowerCase();
      const lista = App.estado.pacientes.filter(p => {
        if (!texto) return true;
        return [p.nombre_completo, p.documento, p.telefono, p.email]
          .some(v => (v || '').toLowerCase().includes(texto));
      });
      const seleccion = selPaciente.value;
      selPaciente.innerHTML = '<option value="">— Selecciona un paciente —</option>' +
        lista.map(p => `<option value="${p.id}">${UI.esc(p.nombre_completo)}${
          p.documento ? ` · ${UI.esc(p.documento)}` : ''}</option>`).join('');
      if (seleccion && lista.some(p => String(p.id) === seleccion)) selPaciente.value = seleccion;
    }

    selProfesional.innerHTML = '<option value="">— Selecciona —</option>' +
      App.estado.profesionales.map(p =>
        `<option value="${p.id}" data-duracion="${p.duracion_cita_min}">${UI.esc(p.nombre_completo)}${
          p.especialidad ? ` · ${UI.esc(p.especialidad)}` : ''}</option>`).join('');

    pintarPacientes();
    if (cita) selPaciente.value = cita.paciente_id;
    else if (prefijo.paciente_id) selPaciente.value = prefijo.paciente_id;
    if (profesionalId) selProfesional.value = profesionalId;

    function sincronizarDuracion() {
      const opcion = selProfesional.selectedOptions[0];
      const porDefecto = opcion ? Number(opcion.dataset.duracion || 30) : 30;
      m.$('#cita-duracion').value = duracion || porDefecto;
      const prof = App.profesional(Number(selProfesional.value));
      m.$('#cita-info-profesional').textContent = prof
        ? `Duración estándar: ${prof.duracion_cita_min} min · ${resumenHorarios(prof)}`
        : '';
    }
    sincronizarDuracion();

    // Con el paciente fijado estos dos controles ni siquiera se pintan
    if (!pacienteFijo) {
      m.$('#cita-filtro-paciente').addEventListener('input', (e) => pintarPacientes(e.target.value));
      m.$('#cita-nuevo-paciente').addEventListener('click', (e) => {
        e.preventDefault();
        PacientesUI.abrirFormulario(null, async (nuevo) => {
          await App.cargarPacientes();
          pintarPacientes();
          selPaciente.value = nuevo.id;
        });
      });
    }

    selProfesional.addEventListener('change', () => {
      if (!duracion) m.$('#cita-duracion').value = '';
      sincronizarDuracion();
      verificarSlot();
    });
    ['#cita-fecha', '#cita-hora', '#cita-duracion'].forEach(sel =>
      m.$(sel).addEventListener('change', verificarSlot));

    m.$('#btn-sugerir').addEventListener('click', () => sugerir(m));
    // Cambiar el día rehace la búsqueda sin tener que volver a pulsar el botón,
    // pero solo si ya hay sugerencias en pantalla (si no, sería una llamada a ciegas).
    m.$('#sug-fecha').addEventListener('change', () => {
      if (!m.$('#cita-sugerencias').hidden) sugerir(m);
    });
    m.$('#form-cita').addEventListener('submit', (e) => { e.preventDefault(); guardar(m, cita); });

    /* Comprueba en vivo si el hueco elegido esta libre */
    let verificando = null;
    async function verificarSlot() {
      const datos = leerFormulario(m);
      const caja = m.$('#cita-estado-slot');
      if (!datos.profesional_id || !datos.inicio || !datos.duracion_min) { caja.innerHTML = ''; return; }

      const token = Symbol();
      verificando = token;
      try {
        const r = await API.citas.verificar({
          profesional_id: datos.profesional_id,
          inicio: datos.inicio,
          duracion: datos.duracion_min,
          excluir_cita_id: cita ? cita.id : undefined,
        });
        if (verificando !== token) return;

        if (r.disponible) {
          caja.innerHTML = `<div class="alerta-inline info">El horario está libre y dentro de la agenda de atención.</div>`;
        } else if (r.conflictos.length) {
          const c = r.conflictos[0];
          caja.innerHTML = `<div class="alerta-inline">Ocupado: ${UI.esc(c.paciente_nombre)} de ${UI.hora(c.inicio)} a ${UI.hora(c.fin)}. Elige otro hueco.</div>`;
        } else {
          caja.innerHTML = `<div class="alerta-inline aviso">Fuera del horario de atención del profesional. Podrás agendar igualmente confirmando la excepción.</div>`;
        }
      } catch (e) { /* verificacion silenciosa */ }
    }
    verificarSlot();

    return m;
  }

  /* --- Sugerencias del motor ---------------------------------------------- */

  async function sugerir(m) {
    const caja = m.$('#cita-sugerencias');
    const datos = leerFormulario(m);
    const dia = m.$('#sug-fecha').value;      // vacío = buscar en todo el horizonte
    caja.hidden = false;
    caja.innerHTML = '<div class="cargando">Calculando los mejores huecos…</div>';

    try {
      const r = await API.citas.sugerencias({
        profesional_id: datos.profesional_id || undefined,
        duracion: datos.duracion_min || undefined,
        limite: 5,
        dias: 21,
        fecha: dia || undefined,
      });

      if (!r.sugerencias.length) {
        caja.innerHTML = `<div class="alerta-inline aviso">${dia
          ? `No queda ningún hueco libre el ${UI.fecha(dia)}${
              datos.duracion_min ? ` para una cita de ${datos.duracion_min} min` : ''
            }. Prueba con otro día o deja la fecha vacía.`
          : 'No hay huecos disponibles en las próximas 3 semanas con esos parámetros.'}</div>`;
        return;
      }

      caja.innerHTML = `
        <div class="sugerencias">
          ${r.sugerencias.map(s => `
            <button type="button" class="sugerencia ${s.ranking === 1 ? 'mejor' : ''}"
                    data-inicio="${s.inicio}" data-profesional="${s.profesional_id}" data-duracion="${s.duracion_min}">
              <span class="rank">${s.ranking}</span>
              <span class="info">
                <strong>${UI.fechaLarga(s.inicio)} · ${UI.hora(s.inicio)} – ${UI.hora(s.fin)}</strong>
                <small>${UI.esc(s.profesional_nombre)} · ${UI.esc(s.motivo)}</small>
              </span>
            </button>`).join('')}
        </div>`;

      caja.querySelectorAll('.sugerencia').forEach(btn => {
        btn.addEventListener('click', () => {
          const inicio = new Date(btn.dataset.inicio);
          m.$('#cita-fecha').value = UI.isoFecha(inicio);
          m.$('#cita-hora').value = UI.isoLocal(inicio).slice(11, 16);
          m.$('#cita-profesional').value = btn.dataset.profesional;
          m.$('#cita-duracion').value = btn.dataset.duracion;
          m.el.dataset.sugerida = '1';
          caja.querySelectorAll('.sugerencia').forEach(b => b.style.outline = '');
          btn.style.outline = '2px solid var(--azul)';
          m.$('#cita-estado-slot').innerHTML =
            '<div class="alerta-inline info">Horario tomado de la sugerencia del motor de optimización.</div>';
        });
      });
    } catch (e) {
      caja.innerHTML = `<div class="alerta-inline">${UI.esc(e.message)}</div>`;
    }
  }

  /* --- Guardado ------------------------------------------------------------ */

  function leerFormulario(m) {
    const fecha = m.$('#cita-fecha').value;
    const hora = m.$('#cita-hora').value;
    return {
      paciente_id: Number(m.$('#cita-paciente').value) || null,
      profesional_id: Number(m.$('#cita-profesional').value) || null,
      inicio: (fecha && hora) ? `${fecha}T${hora}:00` : null,
      duracion_min: Number(m.$('#cita-duracion').value) || null,
      motivo: m.$('#cita-motivo').value.trim(),
      notas: m.$('#cita-notas').value.trim(),
      sugerida_por_motor: m.el.dataset.sugerida === '1',
    };
  }

  async function guardar(m, cita, forzar = false) {
    const datos = leerFormulario(m);
    if (!datos.paciente_id) return m.alerta('Selecciona un paciente.');
    if (!datos.profesional_id) return m.alerta('Selecciona un profesional.');
    if (!datos.inicio) return m.alerta('Indica la fecha y la hora.');
    if (!datos.duracion_min) return m.alerta('Indica la duración de la cita.');

    m.alerta(null);
    m.cargando(true);
    try {
      const cuerpo = { ...datos, forzar };
      const guardada = cita
        ? await API.citas.actualizar(cita.id, cuerpo)
        : await API.citas.crear(cuerpo);

      UI.exito(cita ? 'Cita actualizada.' : `Cita agendada para el ${UI.fecha(guardada.inicio)} a las ${UI.hora(guardada.inicio)}.`);
      m.cerrar();
      App.refrescar();
    } catch (e) {
      m.cargando(false);
      // Fuera de horario: se ofrece agendar como excepcion
      if (e.status === 409 && e.datos.puede_forzar) {
        const ok = await UI.confirmar({
          titulo: 'Fuera del horario de atención',
          mensaje: `${e.message}. ¿Deseas agendarla igualmente como excepción?`,
          textoOk: 'Agendar igualmente',
        });
        if (ok) return guardar(m, cita, true);
        return false;
      }
      UI.mostrarError(e, m);
      return false;
    }
  }

  /* --- Ficha de detalle y control de estados (RF5) ------------------------- */

  async function abrirDetalle(id) {
    let cita;
    try {
      cita = await API.citas.detalle(id);
    } catch (e) { return UI.mostrarError(e); }

    // Las citas de días ya pasados son histórico: se consultan, no se tocan.
    const editable = cita.editable !== false;
    // Reprogramar y borrar son de recepcion; el profesional solo marca asistencia.
    const puedeEditar = editable && App.puede('recepcion');
    const transiciones = !editable ? [] : ({
      pendiente:  [['confirmada', 'Confirmar', 'btn-primario'], ['atendida', 'Marcar atendida', 'btn-exito'], ['no_asistio', 'No asistió', ''], ['cancelada', 'Cancelar cita', 'btn-peligro']],
      confirmada: [['atendida', 'Marcar atendida', 'btn-exito'], ['no_asistio', 'No asistió', ''], ['cancelada', 'Cancelar cita', 'btn-peligro']],
      cancelada:  [['pendiente', 'Reactivar', 'btn-primario']],
      atendida:   [['no_asistio', 'Corregir: no asistió', '']],
      no_asistio: [['atendida', 'Corregir: sí asistió', 'btn-exito']],
    }[cita.estado] || []).filter(([e]) => App.puedeFijarEstado(e));

    const m = UI.modal({
      titulo: `Cita del ${UI.fecha(cita.inicio)}`,
      html: `
        ${editable ? '' : `<div class="alerta-inline info" style="margin-top:0">
          Esta cita ya pasó. Se muestra como consulta del histórico y no puede modificarse.
        </div>`}
        <dl class="detalle-lista">
          <dt>Estado</dt><dd>${UI.tag(cita.estado)}</dd>
          <dt>Paciente</dt><dd>${UI.esc(cita.paciente_nombre)}${cita.paciente_telefono ? ` · ${UI.esc(cita.paciente_telefono)}` : ''}</dd>
          <dt>Profesional</dt><dd>${UI.esc(cita.profesional_nombre)}</dd>
          <dt>Horario</dt><dd>${UI.fechaLarga(cita.inicio)}<br>${UI.hora(cita.inicio)} – ${UI.hora(cita.fin)} (${cita.duracion_min} min)</dd>
          <dt>Motivo</dt><dd>${UI.esc(cita.motivo || '—')}</dd>
          ${cita.notas ? `<dt>Notas</dt><dd>${UI.esc(cita.notas)}</dd>` : ''}
          ${cita.motivo_cancelacion ? `<dt>Cancelación</dt><dd>${UI.esc(cita.motivo_cancelacion)}</dd>` : ''}
          <dt>Confirmada</dt><dd>${cita.confirmada_en ? UI.fechaHora(cita.confirmada_en) : '<span style="color:var(--texto-tenue)">Sin confirmar</span>'}</dd>
          ${cita.sugerida_por_motor ? '<dt>Origen</dt><dd>Horario sugerido por el sistema</dd>' : ''}
          <dt>Registrada</dt><dd>${UI.fechaHora(cita.creada_en)}${
            cita.creada_por ? ` · por ${UI.esc(cita.creada_por)}` : ''}</dd>
        </dl>
        ${transiciones.length ? `
          <hr style="border:0;border-top:1px solid var(--borde);margin:18px 0 14px">
          <label style="font-size:12.5px;font-weight:600;color:#334155">Cambiar estado</label>
          <div class="acciones-estado">
            ${transiciones.map(([estado, texto, clase]) =>
              `<button type="button" class="btn btn-sm ${clase}" data-estado="${estado}">${texto}</button>`).join('')}
          </div>` : ''}`,
      botones: puedeEditar ? [
        { texto: 'Eliminar', clase: 'btn-peligro izquierda', cerrar: false, onClick: (mm) => eliminar(mm, cita) },
        { texto: 'Cerrar' },
        { texto: 'Editar', clase: 'btn-primario', onClick: () => abrirFormulario(cita) },
      ] : [{ texto: 'Cerrar' }],
    });

    m.$$('[data-estado]').forEach(btn => {
      btn.addEventListener('click', () => cambiarEstado(m, cita, btn.dataset.estado));
    });
  }

  async function cambiarEstado(m, cita, estado) {
    let motivo = null;

    if (estado === 'cancelada') {
      motivo = await pedirMotivoCancelacion();
      if (motivo === null) return;
    }
    if (estado === 'no_asistio') {
      const ok = await UI.confirmar({
        titulo: 'Registrar inasistencia',
        mensaje: `¿Confirmas que ${cita.paciente_nombre} no asistió a la cita? Este registro alimenta el indicador de ausentismo.`,
        textoOk: 'Sí, no asistió',
        claseOk: 'btn-peligro',
      });
      if (!ok) return;
    }

    m.cargando(true, 'Actualizando…');
    try {
      await API.citas.cambiarEstado(cita.id, estado, motivo);
      UI.exito(`Cita marcada como ${UI.ESTADOS[estado].texto.toLowerCase()}.`);
      m.cerrar();
      App.refrescar();
    } catch (e) {
      m.cargando(false);
      UI.mostrarError(e, m);
    }
  }

  function pedirMotivoCancelacion() {
    return new Promise(resolve => {
      let enviado = false;
      UI.modal({
        titulo: 'Cancelar cita',
        html: `
          <div class="campo">
            <label for="motivo-cancel">Motivo de la cancelación</label>
            <input type="text" id="motivo-cancel" maxlength="200" placeholder="Ej.: el paciente reprogramó">
            <div class="ayuda">Opcional, pero útil para analizar las causas de cancelación.</div>
          </div>`,
        botones: [
          { texto: 'Volver' },
          {
            texto: 'Cancelar la cita', clase: 'btn-peligro',
            onClick: (mm) => { enviado = true; resolve(mm.$('#motivo-cancel').value.trim() || null); },
          },
        ],
        alCerrar: () => { if (!enviado) resolve(null); },
      });
    });
  }

  async function eliminar(m, cita) {
    const ok = await UI.confirmar({
      titulo: 'Eliminar cita',
      mensaje: 'La cita se borrará definitivamente. Si solo quieres anularla conservando el registro, usa "Cancelar cita".',
      textoOk: 'Eliminar',
      claseOk: 'btn-peligro',
    });
    if (!ok) return false;

    try {
      await API.citas.eliminar(cita.id);
      UI.exito('Cita eliminada.');
      m.cerrar();
      App.refrescar();
    } catch (e) {
      UI.mostrarError(e, m);
      return false;
    }
  }

  /* --- Helpers ------------------------------------------------------------- */

  function proximaHoraRedonda() {
    const d = new Date();
    d.setMinutes(d.getMinutes() + 30 - (d.getMinutes() % 30), 0, 0);
    return d;
  }

  function resumenHorarios(prof) {
    if (!prof.horarios || !prof.horarios.length) return 'Sin horario configurado';
    const dias = [...new Set(prof.horarios.map(h => UI.DIAS[h.dia_semana].slice(0, 3)))];
    return `Atiende ${dias.join(', ')}`;
  }

  return { abrirFormulario, abrirDetalle };
})();
