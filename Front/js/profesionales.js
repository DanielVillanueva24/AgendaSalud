/* =============================================================
   Vista de profesionales y editor de la grilla semanal (RF2 / RF4).
   ============================================================= */

const ProfesionalesUI = (() => {

  function filaHorario(h = {}) {
    return `
      <div class="horario-fila" data-horario>
        <select data-dia>
          ${UI.DIAS.map((d, i) =>
            `<option value="${i}" ${h.dia_semana === i ? 'selected' : ''}>${d}</option>`).join('')}
        </select>
        <input type="time" data-inicio step="900" value="${h.hora_inicio || '09:00'}">
        <input type="time" data-fin step="900" value="${h.hora_fin || '13:00'}">
        <button type="button" class="btn btn-sm btn-icono" data-quitar title="Quitar franja">Quitar</button>
      </div>`;
  }

  function abrirFormulario(profesional = null) {
    const edicion = Boolean(profesional);
    const horarios = profesional?.horarios || [];

    const m = UI.modal({
      titulo: edicion ? 'Editar profesional' : 'Nuevo profesional',
      ancho: true,
      html: `
        <form id="form-profesional">
          <div class="fila">
            <div class="campo">
              <label for="pro-nombre">Nombre *</label>
              <input type="text" id="pro-nombre" required maxlength="80" value="${UI.esc(profesional?.nombre || '')}">
            </div>
            <div class="campo">
              <label for="pro-apellido">Apellido *</label>
              <input type="text" id="pro-apellido" required maxlength="80" value="${UI.esc(profesional?.apellido || '')}">
            </div>
          </div>
          <div class="fila">
            <div class="campo">
              <label for="pro-especialidad">Especialidad</label>
              <input type="text" id="pro-especialidad" maxlength="120" value="${UI.esc(profesional?.especialidad || '')}">
            </div>
            <div class="campo">
              <label for="pro-email">Correo electrónico</label>
              <input type="email" id="pro-email" maxlength="160" value="${UI.esc(profesional?.email || '')}">
            </div>
          </div>
          <div class="fila-3">
            <div class="campo">
              <label for="pro-telefono">Teléfono</label>
              <input type="tel" id="pro-telefono" maxlength="40" value="${UI.esc(profesional?.telefono || '')}">
            </div>
            <div class="campo">
              <label for="pro-duracion">Duración estándar (min) *</label>
              <input type="number" id="pro-duracion" required min="5" max="480" step="5"
                     value="${profesional?.duracion_cita_min || 30}">
            </div>
            <div class="campo">
              <label for="pro-color">Color en la agenda</label>
              <input type="color" id="pro-color" value="${UI.esc(profesional?.color || '#2563eb')}">
            </div>
          </div>

          <hr style="border:0;border-top:1px solid var(--borde);margin:18px 0 14px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
            <div>
              <strong style="font-size:13.5px">Horario semanal de atención</strong>
              <div class="ayuda">Define la capacidad de la agenda y los huecos que evalúa el motor.</div>
            </div>
            <button type="button" class="btn btn-sm" id="btn-add-horario">+ Añadir franja</button>
          </div>
          <div id="lista-horarios">
            ${horarios.length ? horarios.map(filaHorario).join('') : filaHorario()}
          </div>

          ${edicion ? `
            <label class="check" style="margin-top:14px">
              <input type="checkbox" id="pro-activo" ${profesional.activo ? 'checked' : ''}> Profesional activo
            </label>` : ''}
        </form>`,
      botones: [
        { texto: 'Cancelar' },
        { texto: edicion ? 'Guardar cambios' : 'Crear profesional', clase: 'btn-primario', cerrar: false,
          onClick: (mm) => guardar(mm, profesional) },
      ],
    });

    const lista = m.$('#lista-horarios');
    m.$('#btn-add-horario').addEventListener('click', () => {
      lista.insertAdjacentHTML('beforeend', filaHorario());
    });
    lista.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-quitar]');
      if (btn) btn.closest('[data-horario]').remove();
    });
    m.$('#form-profesional').addEventListener('submit', (e) => { e.preventDefault(); guardar(m, profesional); });

    return m;
  }

  async function guardar(m, profesional) {
    const horarios = m.$$('[data-horario]').map(fila => ({
      dia_semana: Number(fila.querySelector('[data-dia]').value),
      hora_inicio: fila.querySelector('[data-inicio]').value,
      hora_fin: fila.querySelector('[data-fin]').value,
    })).filter(h => h.hora_inicio && h.hora_fin);

    const datos = {
      nombre: m.$('#pro-nombre').value.trim(),
      apellido: m.$('#pro-apellido').value.trim(),
      especialidad: m.$('#pro-especialidad').value.trim(),
      email: m.$('#pro-email').value.trim(),
      telefono: m.$('#pro-telefono').value.trim(),
      duracion_cita_min: Number(m.$('#pro-duracion').value),
      color: m.$('#pro-color').value,
      horarios,
    };
    if (m.$('#pro-activo')) datos.activo = m.$('#pro-activo').checked;

    if (!datos.nombre || !datos.apellido) return m.alerta('El nombre y el apellido son obligatorios.');
    if (!horarios.length) {
      const ok = await UI.confirmar({
        titulo: 'Sin horario de atención',
        mensaje: 'Sin franjas horarias el motor no podrá sugerir huecos y toda reserva quedará fuera de horario. ¿Continuar igualmente?',
        textoOk: 'Continuar',
      });
      if (!ok) return false;
    }

    m.alerta(null);
    m.cargando(true);
    try {
      if (profesional) await API.profesionales.actualizar(profesional.id, datos);
      else await API.profesionales.crear(datos);

      UI.exito(profesional ? 'Profesional actualizado.' : 'Profesional creado.');
      m.cerrar();
      await App.cargarProfesionales();
      App.refrescar();
    } catch (e) {
      m.cargando(false);
      UI.mostrarError(e, m);
      return false;
    }
  }

  function resumenSemanal(prof) {
    if (!prof.horarios.length) return '<span style="color:var(--texto-tenue)">Sin horario configurado</span>';
    const porDia = {};
    prof.horarios.forEach(h => {
      (porDia[h.dia_semana] = porDia[h.dia_semana] || []).push(`${h.hora_inicio}–${h.hora_fin}`);
    });
    return Object.keys(porDia).sort((a, b) => a - b).map(d =>
      `<div style="font-size:12.5px"><strong>${UI.DIAS[d].slice(0, 3)}</strong> ${UI.esc(porDia[d].join(', '))}</div>`
    ).join('');
  }

  const Vista = {
    titulo: 'Profesionales',
    subtitulo: 'Fichas y horarios que definen la capacidad de la agenda',

    acciones() {
      return App.puede('admin')
        ? [{ texto: '+ Nuevo profesional', clase: 'btn-primario', onClick: () => abrirFormulario() }]
        : [];
    },

    montar() {},

    async refrescar() {
      const cont = document.getElementById('profesionales-tabla');
      UI.cargando(cont);
      try {
        const r = await API.profesionales.listar({ incluir_inactivos: 1 });
        const admin = App.puede('admin');

        cont.innerHTML = UI.tabla(
          [
            { titulo: 'Profesional' }, { titulo: 'Especialidad' }, { titulo: 'Contacto' },
            { titulo: 'Cita', clase: 'num' }, { titulo: 'Horario semanal' }, { titulo: '', clase: 'num' },
          ],
          r.profesionales.map(p => `
            <tr>
              <td>
                <span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:${UI.esc(p.color)};margin-right:7px"></span>
                <strong>${UI.esc(p.nombre_completo)}</strong>
                ${p.activo ? '' : ' <span class="tag tag-neutro">Inactivo</span>'}
              </td>
              <td>${UI.esc(p.especialidad || '—')}</td>
              <td>${UI.esc(p.email || p.telefono || '—')}</td>
              <td class="num">${p.duracion_cita_min} min</td>
              <td>${resumenSemanal(p)}</td>
              <td class="acciones">
                ${admin ? `<button class="btn btn-sm" data-editar="${p.id}">Editar</button>` : ''}
                <button class="btn btn-sm" data-agenda="${p.id}">Ver agenda</button>
              </td>
            </tr>`),
          { vacio: 'Aún no hay profesionales registrados.' }
        );

        cont.querySelectorAll('[data-editar]').forEach(b => b.addEventListener('click', () => {
          // Se usa la respuesta de esta vista (incluye inactivos), no la cache de activos
          abrirFormulario(r.profesionales.find(p => p.id === Number(b.dataset.editar)));
        }));
        cont.querySelectorAll('[data-agenda]').forEach(b => b.addEventListener('click', () => {
          document.getElementById('filtro-profesional').value = b.dataset.agenda;
          App.irA('agenda');
        }));
      } catch (e) {
        cont.innerHTML = `<div class="vacio">${UI.esc(e.message)}</div>`;
      }
    },
  };

  return { abrirFormulario, Vista };
})();
