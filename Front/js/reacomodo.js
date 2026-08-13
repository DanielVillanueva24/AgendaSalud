/* =============================================================
   Recompactación de la agenda de un día (RF4).

   El motor greedy no solo elige el hueco de una cita nueva: también
   recupera el tiempo muerto que dejan las cancelaciones y las
   reprogramaciones. Esta vista muestra la propuesta ANTES de aplicarla,
   porque mover la cita de un paciente es una decisión del consultorio,
   no del algoritmo.
   ============================================================= */

const ReacomodoUI = (() => {

  let modal = null;
  let propuesta = null;

  function abrir(fecha) {
    propuesta = null;

    const profesionales = App.estado.profesionales;
    const propio = App.estado.usuario && App.estado.usuario.profesional_id;

    modal = UI.modal({
      titulo: 'Juntar las citas del día',
      ancho: true,
      html: `
        <p class="ayuda" style="margin-top:0">
          Adelanta las citas que aún no han empezado para juntarlas y dejar los ratos
          libres seguidos en vez de sueltos. Las citas ya atendidas, canceladas o en
          curso no se tocan. <strong>Aquí solo ves la propuesta</strong>: no se cambia
          nada hasta que pulses Aplicar.
        </p>

        <div class="filtros" style="margin-bottom:14px">
          <div class="campo">
            <label for="rec-fecha">Día a revisar</label>
            <input type="date" id="rec-fecha" value="${UI.esc(fecha)}">
          </div>
          <div class="campo">
            <label for="rec-profesional">Agenda de</label>
            <select id="rec-profesional" ${propio ? 'disabled' : ''}>
              ${profesionales.map(p => `
                <option value="${p.id}" ${propio === p.id ? 'selected' : ''}>
                  ${UI.esc(p.nombre_completo)}
                </option>`).join('')}
            </select>
          </div>
          <div class="campo">
            <label for="rec-adelanto">Adelantar como mucho</label>
            <select id="rec-adelanto">
              <option value="60">1 hora</option>
              <option value="120" selected>2 horas</option>
              <option value="240">4 horas</option>
              <option value="0">Sin límite</option>
            </select>
            <div class="ayuda">Nadie llegará más de esto antes de su hora.</div>
          </div>
          <label class="check" style="padding-bottom:9px">
            <input type="checkbox" id="rec-solo-pendientes"> No mover las que ya están confirmadas
          </label>
        </div>

        <div id="rec-resultado"></div>`,
      botones: [
        { texto: 'Cerrar sin cambiar nada' },
        {
          texto: 'Aplicar estos cambios',
          clase: 'btn-primario',
          cerrar: false,
          onClick: aplicar,
        },
      ],
    });

    ['rec-fecha', 'rec-profesional', 'rec-adelanto', 'rec-solo-pendientes'].forEach(id =>
      modal.$(`#${id}`).addEventListener('change', calcular));

    calcular();
  }

  function parametros() {
    return {
      fecha: modal.$('#rec-fecha').value,
      profesional_id: modal.$('#rec-profesional').value,
      max_adelanto_min: modal.$('#rec-adelanto').value,
      solo_pendientes: modal.$('#rec-solo-pendientes').checked,
    };
  }

  async function calcular() {
    const caja = modal.$('#rec-resultado');
    const params = parametros();

    if (!params.fecha || !params.profesional_id) {
      caja.innerHTML = '<div class="vacio">Elige una fecha y un profesional.</div>';
      return;
    }

    UI.cargando(caja, 'Calculando…');
    modal.alerta(null);
    botonAplicar().disabled = true;

    try {
      propuesta = await API.citas.reacomodoPrevia(params);
      caja.innerHTML = pintar(propuesta);
      botonAplicar().disabled = !propuesta.aplicable;
    } catch (e) {
      propuesta = null;
      caja.innerHTML = `<div class="vacio">${UI.esc(e.message)}</div>`;
    }
  }

  function botonAplicar() {
    return modal.$('.modal-pie .btn-primario');
  }

  function pintar(p) {
    const mejora = p.mejora;
    const recuperados = mejora.minutos_muertos_recuperados;

    const resumen = `
      <div class="kpis">
        ${UI.kpi({
          etiqueta: 'Rato libre desaprovechado', valor: `${p.antes.minutos_muertos} min`,
          detalle: `Se quedaría en ${p.despues.minutos_muertos} min`,
          tono: p.antes.minutos_muertos ? 'alerta' : 'ok',
        })}
        ${UI.kpi({
          etiqueta: 'Se ganan', valor: `${recuperados} min`,
          detalle: 'Tiempo libre para otra consulta', tono: recuperados > 0 ? 'ok' : '',
        })}
        ${UI.kpi({
          etiqueta: 'Hueco seguido más largo', valor: `${p.despues.hueco_util_max} min`,
          detalle: `Ahora mismo es de ${p.antes.hueco_util_max} min`,
        })}
      </div>`;

    if (!p.movimientos.length) {
      return resumen + `<div class="vacio">${UI.esc(
        p.mensaje || 'Las citas de ese día ya están juntas: no hay nada que mejorar.')}</div>`;
    }

    const avisos = p.movimientos.filter(m => m.requiere_aviso).length;
    const nota = avisos
      ? `<div class="alerta-inline aviso">
           Ojo: ${avisos} de estas ${p.movimientos.length} citas ya estaban confirmadas con
           el paciente. Si aplicas el cambio, hay que llamarles para avisar de la hora nueva.
         </div>`
      : '';

    const tabla = UI.tabla(
      [
        { titulo: 'Paciente' }, { titulo: 'Estado' }, { titulo: 'Hora que tiene ahora' },
        { titulo: 'Pasaría a' }, { titulo: 'Se le adelanta', clase: 'num' },
      ],
      p.movimientos.map(m => `
        <tr>
          <td>
            <strong>${UI.esc(m.paciente)}</strong>
            ${m.telefono ? `<br><small style="color:var(--texto-tenue)">${UI.esc(m.telefono)}</small>` : ''}
          </td>
          <td>${UI.tag(m.estado)}${m.requiere_aviso
            ? ' <small style="color:var(--ambar)">hay que avisar</small>' : ''}</td>
          <td>${UI.hora(m.inicio_actual)}</td>
          <td><strong>${UI.hora(m.inicio_propuesto)}</strong></td>
          <td class="num">${m.minutos_adelanto} min</td>
        </tr>`),
    );

    return `${resumen}${nota}
      <p class="ayuda">Se moverían ${p.movimientos.length} de las ${p.citas_totales} citas del día.
         Las otras ${p.citas_fijas} se quedan como están porque ya empezaron o ya se cerraron.</p>
      ${tabla}`;
  }

  async function aplicar() {
    if (!propuesta || !propuesta.aplicable) return false;

    const avisos = propuesta.movimientos.filter(m => m.requiere_aviso).length;
    const ok = await UI.confirmar({
      titulo: 'Cambiar la hora de estas citas',
      mensaje: `Vas a cambiar la hora de ${propuesta.movimientos.length} cita(s)` +
        (avisos ? `. ${avisos} ya estaban confirmadas, así que tendrás que llamar a esos pacientes.` : '.'),
      textoOk: 'Sí, cambiar las horas',
    });
    if (!ok) return false;

    modal.cargando(true, 'Aplicando…');
    try {
      const r = await API.citas.reacomodoAplicar(parametros());
      UI.exito(`Listo: ${r.aplicados} cita(s) cambiadas de hora. ` +
               `Se ganan ${r.mejora.minutos_muertos_recuperados} min libres.`);
      modal.cerrar();
      App.refrescar();
    } catch (e) {
      modal.cargando(false);
      // La agenda pudo cambiar entre el cálculo y la aplicación: se recalcula.
      UI.mostrarError(e, modal);
      calcular();
    }
    return false;
  }

  return { abrir };
})();
