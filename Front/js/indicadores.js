/* =============================================================
   Panel de indicadores con Chart.js (RF6).
   ============================================================= */

const IndicadoresUI = (() => {

  const graficos = {};

  const COLORES = {
    azul: '#2563eb', verde: '#059669', rojo: '#dc2626',
    ambar: '#d97706', gris: '#94a3b8', violeta: '#7c3aed',
  };

  Chart.defaults.font.family = '"Segoe UI", system-ui, sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = '#64748b';
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.boxWidth = 8;

  function pintar(id, config) {
    if (graficos[id]) graficos[id].destroy();
    graficos[id] = new Chart(document.getElementById(id), config);
  }

  function parametros() {
    return {
      desde: document.getElementById('ind-desde').value || undefined,
      hasta: document.getElementById('ind-hasta').value || undefined,
      profesional_id: document.getElementById('ind-profesional').value || undefined,
    };
  }

  /* --- Bloques ------------------------------------------------------------- */

  function pintarKpis(r) {
    const k = r.kpis;
    const tonoAusentismo = k.tasa_ausentismo >= 20 ? 'alerta' : k.tasa_ausentismo >= 10 ? 'aviso' : 'ok';
    const tonoOcupacion = k.ocupacion >= 75 ? 'ok' : k.ocupacion >= 50 ? 'aviso' : 'alerta';

    document.getElementById('ind-kpis').innerHTML = [
      UI.kpi({
        etiqueta: 'Tasa de ausentismo', valor: UI.porcentaje(k.tasa_ausentismo),
        detalle: `${k.ausencias} de ${k.citas_cerradas} citas cerradas`, tono: tonoAusentismo,
      }),
      UI.kpi({
        etiqueta: 'Ocupación de agenda', valor: UI.porcentaje(k.ocupacion),
        detalle: `${k.horas_agendadas} h de ${k.horas_capacidad} h disponibles`, tono: tonoOcupacion,
      }),
      UI.kpi({
        etiqueta: 'Tasa de confirmación', valor: UI.porcentaje(k.tasa_confirmacion),
        detalle: 'Citas confirmadas antes de la consulta',
      }),
      UI.kpi({
        etiqueta: 'Horas perdidas', valor: `${k.horas_perdidas} h`,
        detalle: 'Tiempo clínico desaprovechado por inasistencias', tono: k.horas_perdidas > 0 ? 'alerta' : 'ok',
      }),
      UI.kpi({
        etiqueta: 'Cancelaciones', valor: UI.porcentaje(k.tasa_cancelacion),
        detalle: `${r.por_estado.cancelada} de ${r.total_citas} citas`,
      }),
    ].join('');

    pintar('g-estados', {
      type: 'doughnut',
      data: {
        labels: ['Atendidas', 'No asistió', 'Canceladas', 'Confirmadas', 'Pendientes'],
        datasets: [{
          data: [r.por_estado.atendida, r.por_estado.no_asistio, r.por_estado.cancelada,
                 r.por_estado.confirmada, r.por_estado.pendiente],
          backgroundColor: [COLORES.verde, COLORES.rojo, COLORES.gris, COLORES.azul, COLORES.ambar],
          borderWidth: 2, borderColor: '#fff',
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: '58%',
        plugins: { legend: { position: 'right' } },
      },
    });
  }

  function pintarSerie(r) {
    pintar('g-serie', {
      type: 'line',
      data: {
        labels: r.serie.map(p => p.periodo),
        datasets: [
          {
            label: 'Tasa de ausentismo (%)',
            data: r.serie.map(p => p.tasa_ausentismo),
            borderColor: COLORES.rojo, backgroundColor: 'rgba(220,38,38,.10)',
            fill: true, tension: .3, pointRadius: 3, yAxisID: 'y',
          },
          {
            label: 'Citas cerradas',
            data: r.serie.map(p => p.total),
            borderColor: COLORES.azul, backgroundColor: 'transparent',
            borderDash: [5, 4], tension: .3, pointRadius: 2, yAxisID: 'y1',
          },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          y: { position: 'left', beginAtZero: true, title: { display: true, text: '% ausentismo' } },
          y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false },
                title: { display: true, text: 'Citas' } },
        },
      },
    });
  }

  function pintarPatrones(r) {
    pintar('g-dias', {
      type: 'bar',
      data: {
        labels: r.por_dia.map(d => d.dia.slice(0, 3)),
        datasets: [
          { label: 'Atendidas', data: r.por_dia.map(d => d.atendidas), backgroundColor: COLORES.verde, stack: 'c' },
          { label: 'No asistió', data: r.por_dia.map(d => d.ausencias), backgroundColor: COLORES.rojo, stack: 'c' },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } },
        plugins: {
          tooltip: {
            callbacks: {
              afterBody: (items) => {
                const fila = r.por_dia[items[0].dataIndex];
                return `Ausentismo: ${fila.tasa_ausentismo}%`;
              },
            },
          },
        },
      },
    });

    pintar('g-horas', {
      type: 'bar',
      data: {
        labels: r.por_hora.map(h => h.hora),
        datasets: [{
          label: 'Tasa de ausentismo (%)',
          data: r.por_hora.map(h => h.tasa_ausentismo),
          backgroundColor: r.por_hora.map(h =>
            h.tasa_ausentismo >= 20 ? COLORES.rojo : h.tasa_ausentismo >= 10 ? COLORES.ambar : COLORES.verde),
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, title: { display: true, text: '% ausentismo' } } },
      },
    });

    const e = r.efecto_confirmacion;
    const caja = document.getElementById('ind-efecto');
    if (e.confirmadas.total + e.sin_confirmar.total > 0) {
      caja.innerHTML = `
        <div class="alerta-inline ${e.reduccion_pp > 0 ? 'info' : 'aviso'}">
          <strong>Efecto de la confirmación previa (RF5):</strong>
          las citas confirmadas registran un <strong>${e.confirmadas.tasa_ausentismo}%</strong> de ausentismo
          (${e.confirmadas.ausencias}/${e.confirmadas.total}) frente al
          <strong>${e.sin_confirmar.tasa_ausentismo}%</strong> de las no confirmadas
          (${e.sin_confirmar.ausencias}/${e.sin_confirmar.total}):
          una diferencia de <strong>${e.reduccion_pp} puntos porcentuales</strong>.
        </div>`;
    } else {
      caja.innerHTML = '';
    }
  }

  function pintarTablaProfesionales(r) {
    const cont = document.getElementById('ind-profesionales');
    // Comparar profesionales no aplica a quien solo ve su propia agenda:
    // la tabla degeneraria en una unica fila.
    const panel = cont.closest('.panel');
    if (panel) panel.hidden = App.estado.usuario.rol === 'profesional';

    cont.innerHTML = UI.tabla(
      [
        { titulo: 'Profesional' }, { titulo: 'Citas', clase: 'num' },
        { titulo: 'Atendidas', clase: 'num' }, { titulo: 'Inasistencias', clase: 'num' },
        { titulo: 'Canceladas', clase: 'num' }, { titulo: 'Ausentismo', clase: 'num' },
        { titulo: 'Ocupación', clase: 'num' }, { titulo: 'Horas', clase: 'num' },
      ],
      r.profesionales.map(p => {
        const tono = p.tasa_ausentismo >= 20 ? 'tag-no_asistio'
                   : p.tasa_ausentismo >= 10 ? 'tag-pendiente' : 'tag-atendida';
        return `
          <tr>
            <td>
              <span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:${UI.esc(p.color)};margin-right:7px"></span>
              ${UI.esc(p.profesional)}
            </td>
            <td class="num">${p.total}</td>
            <td class="num">${p.atendidas}</td>
            <td class="num">${p.ausencias}</td>
            <td class="num">${p.canceladas}</td>
            <td class="num"><span class="tag ${tono}">${UI.porcentaje(p.tasa_ausentismo)}</span></td>
            <td class="num">${UI.porcentaje(p.ocupacion)}</td>
            <td class="num">${p.horas_agendadas} h</td>
          </tr>`;
      }),
      { vacio: 'No hay citas en el rango seleccionado.', icono: '📊' }
    );
  }

  function pintarRiesgo(r) {
    const cont = document.getElementById('ind-riesgo');
    cont.innerHTML = UI.tabla(
      [
        { titulo: 'Paciente' }, { titulo: 'Teléfono' },
        { titulo: 'Citas cerradas', clase: 'num' }, { titulo: 'Inasistencias', clase: 'num' },
        { titulo: 'Tasa', clase: 'num' }, { titulo: '', clase: 'num' },
      ],
      r.pacientes.map(p => `
        <tr>
          <td><a href="#" data-ficha="${p.paciente_id}"><strong>${UI.esc(p.paciente)}</strong></a></td>
          <td>${UI.esc(p.telefono || '—')}</td>
          <td class="num">${p.citas_cerradas}</td>
          <td class="num">${p.ausencias}</td>
          <td class="num"><span class="tag ${p.tasa_ausentismo >= 20 ? 'tag-no_asistio' : 'tag-pendiente'}">${UI.porcentaje(p.tasa_ausentismo)}</span></td>
          <td class="acciones"><button class="btn btn-sm" data-ficha-btn="${p.paciente_id}">Ver ficha</button></td>
        </tr>`),
      { vacio: 'Ningún paciente supera el umbral de inasistencias. 🎉', icono: '✅' }
    );

    cont.querySelectorAll('[data-ficha]').forEach(a => a.addEventListener('click', (e) => {
      e.preventDefault(); PacientesUI.abrirFicha(Number(a.dataset.ficha));
    }));
    cont.querySelectorAll('[data-ficha-btn]').forEach(b => b.addEventListener('click', () =>
      PacientesUI.abrirFicha(Number(b.dataset.fichaBtn))));
  }

  /* --- Vista --------------------------------------------------------------- */

  const Vista = {
    titulo: 'Indicadores',
    subtitulo: 'Ausentismo y ocupación de la agenda',

    acciones() {
      return [{ texto: '🖨 Imprimir', onClick: () => window.print() }];
    },

    montar() {
      const hasta = new Date();
      const desde = new Date();
      desde.setDate(desde.getDate() - 89);
      document.getElementById('ind-desde').value = UI.isoFecha(desde);
      document.getElementById('ind-hasta').value = UI.isoFecha(hasta);

      document.getElementById('btn-aplicar-ind').addEventListener('click', () => Vista.refrescar());
      document.getElementById('ind-agrupar').addEventListener('change', () => Vista.refrescar());
    },

    async refrescar() {
      const p = parametros();
      try {
        const [resumen, serie, porProf, patrones, riesgo] = await Promise.all([
          API.indicadores.resumen(p),
          API.indicadores.serie({ ...p, agrupar: document.getElementById('ind-agrupar').value }),
          API.indicadores.porProfesional(p),
          API.indicadores.patrones(p),
          API.indicadores.pacientesRiesgo({ limite: 10 }),
        ]);

        pintarKpis(resumen);
        pintarSerie(serie);
        pintarPatrones(patrones);
        pintarTablaProfesionales(porProf);
        pintarRiesgo(riesgo);

        document.getElementById('vista-subtitulo').textContent =
          `Del ${UI.fecha(resumen.rango.desde)} al ${UI.fecha(resumen.rango.hasta)} · ${resumen.total_citas} citas analizadas`;
      } catch (e) {
        UI.mostrarError(e);
      }
    },
  };

  return { Vista };
})();
