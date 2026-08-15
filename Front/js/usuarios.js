/* =============================================================
   Administracion de usuarios y roles (RF1). Solo rol admin.
   ============================================================= */

const UsuariosUI = (() => {

  const ROLES = {
    admin: 'Administrador',
    recepcion: 'Recepción',
    profesional: 'Profesional',
  };

  function abrirFormulario(usuario = null) {
    const edicion = Boolean(usuario);

    const m = UI.modal({
      titulo: edicion ? 'Editar usuario' : 'Nuevo usuario',
      html: `
        <form id="form-usuario">
          <div class="campo">
            <label for="usu-nombre">Nombre completo *</label>
            <input type="text" id="usu-nombre" required maxlength="120" value="${UI.esc(usuario?.nombre || '')}">
          </div>
          <div class="campo">
            <label for="usu-email">Correo electrónico *</label>
            <input type="email" id="usu-email" required maxlength="160" value="${UI.esc(usuario?.email || '')}">
            <div class="ayuda">Se usa como nombre de usuario para iniciar sesión.</div>
          </div>
          <div class="fila">
            <div class="campo">
              <label for="usu-rol">Rol *</label>
              <select id="usu-rol" required>
                ${Object.entries(ROLES).map(([v, t]) =>
                  `<option value="${v}" ${usuario?.rol === v ? 'selected' : ''}>${t}</option>`).join('')}
              </select>
            </div>
            <div class="campo">
              <label for="usu-password">${edicion ? 'Nueva contraseña' : 'Contraseña *'}</label>
              <input type="password" id="usu-password" ${edicion ? '' : 'required'} minlength="6"
                     autocomplete="new-password" placeholder="${edicion ? 'Dejar vacío para no cambiarla' : 'Mínimo 6 caracteres'}">
            </div>
          </div>
          <div class="campo" id="campo-profesional" hidden>
            <label for="usu-profesional">Ficha profesional vinculada</label>
            <select id="usu-profesional"><option value="">— Sin vincular —</option></select>
            <div class="ayuda">El usuario solo verá la agenda del profesional vinculado.</div>
          </div>
          ${edicion ? `
            <label class="check">
              <input type="checkbox" id="usu-activo" ${usuario.activo ? 'checked' : ''}> Cuenta activa
            </label>` : ''}
        </form>`,
      botones: [
        { texto: 'Cancelar' },
        { texto: edicion ? 'Guardar cambios' : 'Crear usuario', clase: 'btn-primario', cerrar: false,
          onClick: (mm) => guardar(mm, usuario) },
      ],
    });

    const selProfesional = m.$('#usu-profesional');
    selProfesional.innerHTML = '<option value="">— Sin vincular —</option>' +
      App.estado.profesionales.map(p =>
        `<option value="${p.id}">${UI.esc(p.nombre_completo)}</option>`).join('');
    if (usuario?.profesional_id) selProfesional.value = usuario.profesional_id;

    function alternarVinculo() {
      m.$('#campo-profesional').hidden = m.$('#usu-rol').value !== 'profesional';
    }
    alternarVinculo();
    m.$('#usu-rol').addEventListener('change', alternarVinculo);
    m.$('#form-usuario').addEventListener('submit', (e) => { e.preventDefault(); guardar(m, usuario); });

    return m;
  }

  async function guardar(m, usuario) {
    const rol = m.$('#usu-rol').value;
    const datos = {
      nombre: m.$('#usu-nombre').value.trim(),
      email: m.$('#usu-email').value.trim(),
      rol,
      profesional_id: rol === 'profesional' ? (Number(m.$('#usu-profesional').value) || null) : null,
    };
    if (m.$('#usu-activo')) datos.activo = m.$('#usu-activo').checked;

    const password = m.$('#usu-password').value;
    if (password) datos.password = password;

    if (!datos.nombre || !datos.email) return m.alerta('El nombre y el correo son obligatorios.');
    // Mismo criterio que el backend (usuarios.py _RE_EMAIL): avisar antes de enviar.
    if (!/^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$/.test(datos.email)) {
      return m.alerta('El correo no tiene un formato válido. Ejemplo: nombre@dominio.com');
    }
    if (!usuario && !password) return m.alerta('Indica una contraseña inicial.');
    if (password && password.length < 6) return m.alerta('La contraseña debe tener al menos 6 caracteres.');

    m.alerta(null);
    m.cargando(true);
    try {
      if (usuario) await API.usuarios.actualizar(usuario.id, datos);
      else await API.usuarios.crear(datos);

      UI.exito(usuario ? 'Usuario actualizado.' : 'Usuario creado.');
      m.cerrar();
      App.refrescar();
    } catch (e) {
      m.cargando(false);
      UI.mostrarError(e, m);
      return false;
    }
  }

  function abrirCambioPassword() {
    const m = UI.modal({
      titulo: 'Cambiar mi contraseña',
      html: `
        <form id="form-password">
          <div class="campo">
            <label for="pwd-actual">Contraseña actual</label>
            <input type="password" id="pwd-actual" required autocomplete="current-password">
          </div>
          <div class="campo">
            <label for="pwd-nueva">Nueva contraseña</label>
            <input type="password" id="pwd-nueva" required minlength="6" autocomplete="new-password">
            <div class="ayuda">Mínimo 6 caracteres.</div>
          </div>
          <div class="campo">
            <label for="pwd-repetir">Repetir nueva contraseña</label>
            <input type="password" id="pwd-repetir" required minlength="6" autocomplete="new-password">
          </div>
        </form>`,
      botones: [
        { texto: 'Cancelar' },
        {
          texto: 'Cambiar contraseña', clase: 'btn-primario', cerrar: false,
          onClick: async (mm) => {
            const actual = mm.$('#pwd-actual').value;
            const nueva = mm.$('#pwd-nueva').value;
            if (nueva !== mm.$('#pwd-repetir').value) return mm.alerta('Las contraseñas nuevas no coinciden.');
            if (nueva.length < 6) return mm.alerta('La nueva contraseña debe tener al menos 6 caracteres.');

            mm.cargando(true);
            try {
              await API.auth.cambiarPassword(actual, nueva);
              UI.exito('Contraseña actualizada.');
              mm.cerrar();
            } catch (e) {
              mm.cargando(false);
              UI.mostrarError(e, mm);
              return false;
            }
          },
        },
      ],
    });
    return m;
  }

  const Vista = {
    titulo: 'Usuarios',
    subtitulo: 'Cuentas de acceso y control por rol',

    acciones() {
      return [{ texto: '+ Nuevo usuario', clase: 'btn-primario', onClick: () => abrirFormulario() }];
    },

    montar() {},

    async refrescar() {
      const cont = document.getElementById('usuarios-tabla');
      UI.cargando(cont);
      try {
        const r = await API.usuarios.listar();
        cont.innerHTML = UI.tabla(
          [
            { titulo: 'Usuario' }, { titulo: 'Correo' }, { titulo: 'Rol' },
            { titulo: 'Agenda vinculada' }, { titulo: 'Último acceso' }, { titulo: '', clase: 'num' },
          ],
          r.usuarios.map(u => {
            const prof = u.profesional_id ? App.profesional(u.profesional_id) : null;
            const yo = u.id === App.estado.usuario.id;
            return `
              <tr>
                <td><strong>${UI.esc(u.nombre)}</strong>${yo ? ' <span class="tag tag-neutro">Tú</span>' : ''}
                    ${u.activo ? '' : ' <span class="tag tag-neutro">Inactivo</span>'}</td>
                <td>${UI.esc(u.email)}</td>
                <td><span class="tag tag-rol">${UI.esc(ROLES[u.rol] || u.rol)}</span></td>
                <td>${prof ? UI.esc(prof.nombre_completo) : '—'}</td>
                <td>${u.ultimo_acceso ? UI.fechaHora(u.ultimo_acceso) : '<span style="color:var(--texto-tenue)">Nunca</span>'}</td>
                <td class="acciones">
                  <button class="btn btn-sm" data-editar="${u.id}">Editar</button>
                  ${(u.activo && !yo) ? `<button class="btn btn-sm" data-baja="${u.id}"
                       data-nombre="${UI.esc(u.nombre)}">Desactivar</button>` : ''}
                  ${yo ? '' : `<button class="btn btn-sm btn-peligro" data-borrar="${u.id}"
                       data-nombre="${UI.esc(u.nombre)}">Eliminar</button>`}
                </td>
              </tr>`;
          }),
          { vacio: 'No hay usuarios registrados.' }
        );

        cont.querySelectorAll('[data-editar]').forEach(b => b.addEventListener('click', () =>
          abrirFormulario(r.usuarios.find(u => u.id === Number(b.dataset.editar)))));

        cont.querySelectorAll('[data-baja]').forEach(b => b.addEventListener('click', async () => {
          const ok = await UI.confirmar({
            titulo: 'Desactivar usuario',
            mensaje: `${b.dataset.nombre} no podrá volver a iniciar sesión. Sus registros se conservan.`,
            textoOk: 'Desactivar', claseOk: 'btn-peligro',
          });
          if (!ok) return;
          try {
            await API.usuarios.desactivar(Number(b.dataset.baja));
            UI.exito('Usuario desactivado.');
            Vista.refrescar();
          } catch (e) { UI.mostrarError(e); }
        }));

        cont.querySelectorAll('[data-borrar]').forEach(b => b.addEventListener('click', async () => {
          const ok = await UI.confirmar({
            titulo: 'Eliminar usuario definitivamente',
            mensaje: `Se borrará la cuenta de ${b.dataset.nombre}. Esto no se puede deshacer. ` +
                     'Las citas que registró se conservan, pero dejarán de mostrar quién las creó. ' +
                     'Si solo quieres impedirle el acceso, usa Desactivar.',
            textoOk: 'Eliminar definitivamente', claseOk: 'btn-peligro',
          });
          if (!ok) return;
          try {
            const r = await API.usuarios.eliminar(Number(b.dataset.borrar));
            UI.exito(r.citas_conservadas
              ? `Usuario eliminado. Se conservaron ${r.citas_conservadas} cita(s) que había registrado.`
              : 'Usuario eliminado.');
            Vista.refrescar();
          } catch (e) { UI.mostrarError(e); }
        }));
      } catch (e) {
        cont.innerHTML = `<div class="vacio">${UI.esc(e.message)}</div>`;
      }
    },
  };

  return { abrirFormulario, abrirCambioPassword, Vista };
})();
