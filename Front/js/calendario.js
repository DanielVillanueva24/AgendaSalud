/* =============================================================
   Vista de agenda con FullCalendar (RF3).
   Incluye arrastrar-y-soltar para reprogramar contra la API.
   ============================================================= */

const CalendarioUI = (() => {

  let calendario = null;

  function construir() {
    const el = document.getElementById('calendario');

    // En movil las 7 columnas de la semana no dejan ancho legible para una cita:
    // se arranca en lista, que es la vista util en pantalla estrecha.
    const movil = window.matchMedia('(max-width: 560px)').matches;

    calendario = new FullCalendar.Calendar(el, {
      locale: 'es',
      initialView: movil ? 'listWeek' : 'timeGridWeek',
      height: 'auto',
      nowIndicator: true,
      weekNumbers: false,
      firstDay: 1,
      slotMinTime: '07:00:00',
      slotMaxTime: '21:00:00',
      slotDuration: '00:30:00',
      slotLabelFormat: { hour: '2-digit', minute: '2-digit', hour12: false },
      eventTimeFormat: { hour: '2-digit', minute: '2-digit', hour12: false },
      allDaySlot: false,
      expandRows: true,
      headerToolbar: movil
        ? { left: 'prev,next hoy', center: '', right: 'listWeek,timeGridDay,dayGridMonth' }
        : { left: 'prev,next hoy', center: 'title', right: 'dayGridMonth,timeGridWeek,timeGridDay,listWeek' },
      customButtons: {
        hoy: { text: 'Ir a hoy', click: () => calendario.today() },
      },
      buttonText: { month: 'Mes', week: 'Semana', day: 'Día', list: 'Lista' },
      views: {
        // En el mes cada dia es una celda baja: se listan pocas y el resto va al "+N mas"
        dayGridMonth: { dayMaxEvents: 3 },
        listWeek: { buttonText: 'Lista' },
      },
      businessHours: false,
      editable: App.puede('recepcion'),
      selectable: App.puede('recepcion'),
      selectMirror: true,
      eventDurationEditable: true,

      // Un mismo profesional ya no puede tener dos citas a la misma hora (lo
      // impide la API), pero con el filtro en "Todos" siguen coincidiendo citas
      // de profesionales distintos. Por defecto FullCalendar las encabalga al
      // 50 %, y entonces parece que una cita esta tapando a otra: con
      // slotEventOverlap en false se reparten el ancho de la columna y cada una
      // se lee entera.
      slotEventOverlap: false,
      eventMaxStack: 4,
      moreLinkText: (n) => `+${n} más`,

      // Al arrastrar: no se puede soltar una cita encima de otra del MISMO
      // profesional. La API lo rechazaria igualmente, pero asi el arrastre se
      // frena en el sitio en vez de ir y volver con un error.
      eventOverlap: (fija, movida) => {
        const a = fija.extendedProps;
        const b = movida && movida.extendedProps;
        if (!a || !b) return true;
        return a.profesional_id !== b.profesional_id;
      },

      events: cargarEventos,

      eventClick: (info) => {
        info.jsEvent.preventDefault();
        CitasUI.abrirDetalle(Number(info.event.id));
      },

      select: (info) => {
        if (!App.puede('recepcion')) return;
        const duracionMin = Math.round((info.end - info.start) / 60000);
        CitasUI.abrirFormulario(null, {
          inicio: info.start,
          profesional_id: document.getElementById('filtro-profesional').value || undefined,
          duracion_min: duracionMin >= 10 ? duracionMin : undefined,
        });
        calendario.unselect();
      },

      eventDrop: (info) => reprogramar(info),
      eventResize: (info) => reprogramar(info),

      eventDidMount: (info) => {
        // El evento espejo de `selectMirror` se monta sin extendedProps. Como
        // esto corre dentro del componentDidMount de FullCalendar, cualquier
        // excepcion aqui aborta el render y deja la barra de botones muerta:
        // el tooltip degrada, nunca lanza.
        const c = info.event.extendedProps;
        if (!c || !c.estado) return;
        const estado = UI.ESTADOS[c.estado];
        info.el.title =
          `${c.paciente_nombre}\n${c.profesional_nombre}\n${estado ? estado.texto : c.estado}` +
          (c.motivo ? `\n${c.motivo}` : '');
      },
    });

    calendario.render();
  }

  /* --- Carga de eventos ---------------------------------------------------- */

  async function cargarEventos(info, exito, fallo) {
    const filtroEstado = document.getElementById('filtro-estado').value;
    const params = {
      desde: UI.isoLocal(info.start),
      hasta: UI.isoLocal(info.end),
      profesional_id: document.getElementById('filtro-profesional').value || undefined,
    };
    if (filtroEstado === 'activas') params.incluir_canceladas = 'false';
    else if (filtroEstado) params.estado = filtroEstado;

    try {
      const r = await API.citas.listar(params);
      exito(r.citas.map(c => ({
        id: String(c.id),
        title: c.paciente_nombre,
        start: c.inicio,
        end: c.fin,
        classNames: [`ev-${c.estado}`],
        borderColor: c.profesional_color,
        editable: c.estado !== 'atendida' && c.estado !== 'no_asistio',
        extendedProps: c,
      })));
    } catch (e) {
      fallo(e);
      UI.mostrarError(e);
    }
  }

  /* --- Reprogramacion por arrastre ---------------------------------------- */

  async function reprogramar(info) {
    const cita = info.event.extendedProps;
    const nuevoInicio = info.event.start;
    const duracion = Math.round((info.event.end - nuevoInicio) / 60000);

    try {
      await API.citas.actualizar(Number(info.event.id), {
        inicio: UI.isoLocal(nuevoInicio),
        duracion_min: duracion,
      });
      UI.exito(`Cita de ${cita.paciente_nombre} reprogramada al ${UI.fecha(nuevoInicio)} a las ${UI.hora(nuevoInicio)}.`);
      App.refrescarSilencioso();
    } catch (e) {
      // Fuera de horario: se ofrece mantener el cambio como excepcion
      if (e.status === 409 && e.datos.puede_forzar) {
        const ok = await UI.confirmar({
          titulo: 'Fuera del horario de atención',
          mensaje: `${e.message}. ¿Mantener el cambio como excepción?`,
          textoOk: 'Mantener',
        });
        if (ok) {
          try {
            await API.citas.actualizar(Number(info.event.id), {
              inicio: UI.isoLocal(nuevoInicio), duracion_min: duracion, forzar: true,
            });
            UI.exito('Cita reprogramada como excepción.');
            return App.refrescarSilencioso();
          } catch (e2) { UI.mostrarError(e2); }
        }
      } else {
        UI.mostrarError(e);
      }
      info.revert();
    }
  }

  /* --- Vista --------------------------------------------------------------- */

  const Vista = {
    titulo: 'Agenda',
    get subtitulo() {
      return App.puede('recepcion')
        ? 'Calendario de citas — arrastra una cita para reprogramarla'
        : 'Calendario de citas — consulta el detalle para registrar la asistencia';
    },

    acciones() {
      return App.puede('recepcion')
        ? [{ texto: '+ Nueva cita', clase: 'btn-primario', onClick: () => CitasUI.abrirFormulario() }]
        : [];
    },

    montar() {
      document.getElementById('filtro-profesional').addEventListener('change', alCambiarProfesional);
      document.getElementById('filtro-estado').addEventListener('change', alCambiarEstado);
    },

    /**
     * Al cerrar sesion. El calendario fija sus permisos (arrastrar, seleccionar)
     * en el momento de construirse, asi que hay que tirarlo: si no, quien entre
     * despues sigue viendo el calendario del usuario anterior, con sus permisos
     * y sus citas todavia pintadas. Los listeners de los filtros se quedan: son
     * de elementos permanentes de la pagina y montar() no vuelve a correr.
     */
    desmontar() {
      if (calendario) {
        calendario.destroy();
        calendario = null;
      }
      document.getElementById('calendario').innerHTML = '';
      document.getElementById('filtro-profesional').value = '';
      document.getElementById('filtro-estado').value = '';
    },

    refrescar() {
      if (!calendario) {
        construir();
        aplicarHorarioVisible();
      } else {
        calendario.refetchEvents();
      }
      // El contenedor estaba oculto al construirse: recalcula tamaños
      setTimeout(() => calendario && calendario.updateSize(), 30);
    },
  };

  function alCambiarProfesional() {
    aplicarHorarioVisible();
    Vista.refrescar();
  }

  function alCambiarEstado() {
    Vista.refrescar();
  }

  /** Ajusta el rango horario visible y las horas laborables al profesional filtrado. */
  function aplicarHorarioVisible() {
    if (!calendario) return;
    const id = Number(document.getElementById('filtro-profesional').value);
    const profesionales = id ? [App.profesional(id)].filter(Boolean) : App.estado.profesionales;
    const horarios = profesionales.flatMap(p => p.horarios || []);

    if (!horarios.length) {
      calendario.setOption('businessHours', false);
      return;
    }

    const aMinutos = (hhmm) => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5));
    const min = Math.min(...horarios.map(h => aMinutos(h.hora_inicio)));
    const max = Math.max(...horarios.map(h => aMinutos(h.hora_fin)));
    const aTexto = (m) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}:00`;

    calendario.setOption('slotMinTime', aTexto(Math.max(0, min - 60)));
    calendario.setOption('slotMaxTime', aTexto(Math.min(24 * 60, max + 60)));
    calendario.setOption('businessHours', horarios.map(h => ({
      // FullCalendar usa 0 = domingo; el backend usa 0 = lunes
      daysOfWeek: [(h.dia_semana + 1) % 7],
      startTime: h.hora_inicio,
      endTime: h.hora_fin,
    })));
  }

  return { Vista };
})();
