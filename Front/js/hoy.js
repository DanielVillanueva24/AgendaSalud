/* =============================================================
   Vista operativa del día: confirmar citas y registrar asistencia
   con un clic (RF5, el flujo diario de recepción).
   ============================================================= */

const HoyUI = (() => {

  const Vista = {
    titulo: 'Agenda del día',
    subtitulo: 'Confirma, atiende o registra inasistencias',

    acciones() {
      // Optimizar reprograma citas en bloque y agendar crea: ambas de recepcion.
      if (!App.puede('recepcion')) return [];
      return [
        {
          texto: 'Juntar citas del día',
          onClick: () => ReacomodoUI.abrir(
            document.getElementById('hoy-fecha').value || UI.isoFecha(new Date())),
        },
        { texto: '+ Nueva cita', clase: 'btn-primario', onClick: () => CitasUI.abrirFormulario() },
      ];
    },

    montar() {
      const input = document.getElementById('hoy-fecha');
      input.value = UI.isoFecha(new Date());
      input.addEventListener('change', () => Vista.refrescar());
    },

    async refrescar() {
      const fecha = document.getElementById('hoy-fecha').value || UI.isoFecha(new Date());
      const cont = document.getElementById('hoy-tabla');
      const kpis = document.getElementById('hoy-kpis');
      UI.cargando(cont);

      try {
        const r = await API.citas.agendaDia(fecha);
        const res = r.resumen;
        const activas = res.pendiente + res.confirmada + res.atendida + res.no_asistio;

        // Un día ya pasado es histórico: ni se confirma ni se registra asistencia,
        // así que "Por confirmar" deja de tener sentido y desaparece.
        const pasado = fecha < UI.isoFecha(new Date());

        kpis.innerHTML = [
          UI.kpi({ etiqueta: 'Citas del día', valor: activas, detalle: `${res.cancelada} canceladas` }),
          pasado
            ? UI.kpi({ etiqueta: 'Se quedaron sin confirmar', valor: res.pendiente,
                       detalle: 'Nunca llegaron a confirmarse' })
            : UI.kpi({ etiqueta: 'Por confirmar', valor: res.pendiente,
                       detalle: 'Llamar al paciente', tono: res.pendiente ? 'aviso' : 'ok' }),
          UI.kpi({ etiqueta: 'Confirmadas', valor: res.confirmada,
                   detalle: pasado ? 'Confirmaron su asistencia' : 'Listas para atender' }),
          UI.kpi({ etiqueta: 'Atendidas', valor: res.atendida, detalle: 'Consultas cerradas', tono: 'ok' }),
          UI.kpi({ etiqueta: 'No asistieron', valor: res.no_asistio,
                   detalle: 'Ausentismo del día', tono: res.no_asistio ? 'alerta' : 'ok' }),
        ].join('');

        const gestiona = !pasado && App.puede('recepcion', 'profesional');
        const ahora = new Date();

        cont.innerHTML = UI.tabla(
          [
            { titulo: 'Hora' }, { titulo: 'Paciente' }, { titulo: 'Profesional' },
            { titulo: 'Motivo' }, { titulo: 'Estado' }, { titulo: 'Acciones', clase: 'num' },
          ],
          r.citas.map(c => {
            const pasada = new Date(c.fin) < ahora;
            return `
              <tr${pasada && c.estado === 'confirmada' ? ' style="background:var(--ambar-claro)"' : ''}>
                <td><strong>${UI.hora(c.inicio)}</strong><br><small style="color:var(--texto-tenue)">${c.duracion_min} min</small></td>
                <td>
                  <a href="#" data-ficha="${c.paciente_id}">${UI.esc(c.paciente_nombre)}</a>
                  ${c.paciente_telefono ? `<br><small style="color:var(--texto-tenue)">${UI.esc(c.paciente_telefono)}</small>` : ''}
                </td>
                <td>${UI.esc(c.profesional_nombre)}</td>
                <td>${UI.esc(c.motivo || '—')}</td>
                <td>
                  ${UI.tag(c.estado)}
                  ${c.recordatorios_enviados
                    ? `<br><small style="color:var(--texto-tenue)" title="Llamadas de confirmación registradas">${c.recordatorios_enviados} llamada(s)</small>`
                    : ''}
                </td>
                <td class="acciones">
                  ${gestiona ? acciones(c) : ''}
                  <button class="btn btn-sm" data-detalle="${c.id}">Ver</button>
                </td>
              </tr>`;
          }),
          { vacio: `Sin citas agendadas para el ${UI.fecha(fecha)}.` }
        );

        enlazar(cont);
      } catch (e) {
        cont.innerHTML = `<div class="vacio">${UI.esc(e.message)}</div>`;
      }
    },
  };

  function acciones(c) {
    if (c.estado === 'pendiente') {
      // El recordatorio deja constancia del llamado aunque el paciente no confirme:
      // es lo que permite medir después el efecto de la confirmación sobre el ausentismo.
      return `
        <button class="btn btn-sm" data-recordar="${c.id}"
                title="Registrar que se llamó al paciente">Registrar llamada</button>
        ${App.puedeFijarEstado('confirmada')
          ? `<button class="btn btn-sm btn-primario" data-estado="confirmada" data-id="${c.id}">Confirmar</button>`
          : ''}`;
    }
    if (c.estado === 'confirmada') {
      return `
        <button class="btn btn-sm btn-exito" data-estado="atendida" data-id="${c.id}">Atendida</button>
        <button class="btn btn-sm" data-estado="no_asistio" data-id="${c.id}"
                data-paciente="${UI.esc(c.paciente_nombre)}">No asistió</button>`;
    }
    return '';
  }

  function enlazar(cont) {
    cont.querySelectorAll('[data-detalle]').forEach(b =>
      b.addEventListener('click', () => CitasUI.abrirDetalle(Number(b.dataset.detalle))));

    cont.querySelectorAll('[data-ficha]').forEach(a =>
      a.addEventListener('click', (e) => {
        e.preventDefault();
        PacientesUI.abrirFicha(Number(a.dataset.ficha));
      }));

    cont.querySelectorAll('[data-recordar]').forEach(b =>
      b.addEventListener('click', async () => {
        b.disabled = true;
        try {
          const cita = await API.citas.recordatorio(Number(b.dataset.recordar));
          UI.exito(`Llamado registrado (${cita.recordatorios_enviados} en total).`);
          Vista.refrescar();
        } catch (e) {
          b.disabled = false;
          UI.mostrarError(e);
        }
      }));

    cont.querySelectorAll('[data-estado]').forEach(b =>
      b.addEventListener('click', async () => {
        const estado = b.dataset.estado;

        if (estado === 'no_asistio') {
          const ok = await UI.confirmar({
            titulo: 'Registrar inasistencia',
            mensaje: `¿Confirmas que ${b.dataset.paciente} no asistió? El registro alimenta el indicador de ausentismo.`,
            textoOk: 'Sí, no asistió',
            claseOk: 'btn-peligro',
          });
          if (!ok) return;
        }

        b.disabled = true;
        try {
          await API.citas.cambiarEstado(Number(b.dataset.id), estado);
          UI.exito(`Cita marcada como ${UI.ESTADOS[estado].texto.toLowerCase()}.`);
          Vista.refrescar();
        } catch (e) {
          b.disabled = false;
          UI.mostrarError(e);
        }
      }));
  }

  return { Vista };
})();
