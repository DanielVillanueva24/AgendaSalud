/* =============================================================
   Cliente HTTP de la API REST de AgendaSalud.
   Centraliza fetch, manejo de errores y expiracion de sesion.
   ============================================================= */

const API = (() => {
  const BASE = '/api';

  class ErrorAPI extends Error {
    constructor(mensaje, status, datos) {
      super(mensaje);
      this.status = status;
      this.datos = datos || {};
    }
  }

  async function peticion(metodo, ruta, cuerpo, params) {
    let url = BASE + ruta;

    if (params) {
      const qs = new URLSearchParams();
      Object.entries(params).forEach(([clave, valor]) => {
        if (valor === undefined || valor === null || valor === '') return;
        if (Array.isArray(valor)) valor.forEach(v => qs.append(clave, v));
        else qs.append(clave, valor);
      });
      const texto = qs.toString();
      if (texto) url += '?' + texto;
    }

    const opciones = {
      method: metodo,
      headers: { 'Accept': 'application/json' },
      credentials: 'same-origin',
    };
    if (cuerpo !== undefined) {
      opciones.headers['Content-Type'] = 'application/json';
      opciones.body = JSON.stringify(cuerpo);
    }

    let respuesta;
    try {
      respuesta = await fetch(url, opciones);
    } catch (e) {
      throw new ErrorAPI('No se pudo conectar con el servidor. ¿Está corriendo app.py?', 0);
    }

    let datos = null;
    if (respuesta.status !== 204) {
      try { datos = await respuesta.json(); } catch (e) { datos = null; }
    }

    if (!respuesta.ok) {
      // La sesion expiro: se vuelve al login sin perder el mensaje
      if (respuesta.status === 401 && ruta !== '/auth/login' && ruta !== '/auth/sesion') {
        document.dispatchEvent(new CustomEvent('sesion-expirada'));
      }
      const mensaje = (datos && datos.error) || `Error ${respuesta.status}`;
      throw new ErrorAPI(mensaje, respuesta.status, datos);
    }

    // Un cambio hecho desde ESTA pestaña ya repinta por su cuenta: se avisa al
    // sincronizador para que no lo detecte luego y vuelva a repintar sin motivo.
    if (metodo !== 'GET' && ruta.startsWith('/citas') &&
        typeof App !== 'undefined' && App.marcarCambioPropio) {
      App.marcarCambioPropio();
    }

    return datos;
  }

  return {
    ErrorAPI,
    get:  (ruta, params) => peticion('GET', ruta, undefined, params),
    post: (ruta, cuerpo) => peticion('POST', ruta, cuerpo || {}),
    put:  (ruta, cuerpo) => peticion('PUT', ruta, cuerpo || {}),
    del:  (ruta) => peticion('DELETE', ruta),

    // --- Atajos por recurso ---
    auth: {
      login:   (email, password) => peticion('POST', '/auth/login', { email, password }),
      logout:  () => peticion('POST', '/auth/logout', {}),
      sesion:  () => peticion('GET', '/auth/sesion'),
      cambiarPassword: (actual, nueva) =>
        peticion('POST', '/auth/password', { password_actual: actual, password_nueva: nueva }),
    },
    pacientes: {
      listar: (params) => peticion('GET', '/pacientes', undefined, params),
      detalle: (id) => peticion('GET', `/pacientes/${id}`),
      crear: (datos) => peticion('POST', '/pacientes', datos),
      actualizar: (id, datos) => peticion('PUT', `/pacientes/${id}`, datos),
      desactivar: (id) => peticion('DELETE', `/pacientes/${id}`),
    },
    profesionales: {
      listar: (params) => peticion('GET', '/profesionales', undefined, params),
      crear: (datos) => peticion('POST', '/profesionales', datos),
      actualizar: (id, datos) => peticion('PUT', `/profesionales/${id}`, datos),
      desactivar: (id) => peticion('DELETE', `/profesionales/${id}`),
      disponibilidad: (id, params) => peticion('GET', `/profesionales/${id}/disponibilidad`, undefined, params),
    },
    citas: {
      listar: (params) => peticion('GET', '/citas', undefined, params),
      detalle: (id) => peticion('GET', `/citas/${id}`),
      agendaDia: (fecha) => peticion('GET', '/citas/agenda-dia', undefined, { fecha }),
      sugerencias: (params) => peticion('GET', '/citas/sugerencias', undefined, params),
      verificar: (params) => peticion('GET', '/citas/verificar', undefined, params),
      crear: (datos) => peticion('POST', '/citas', datos),
      actualizar: (id, datos) => peticion('PUT', `/citas/${id}`, datos),
      cambiarEstado: (id, estado, motivo) =>
        peticion('PATCH', `/citas/${id}/estado`, { estado, motivo_cancelacion: motivo }),
      eliminar: (id) => peticion('DELETE', `/citas/${id}`),

      // RF5: constancia de los llamados de confirmación al paciente
      porConfirmar: (params) => peticion('GET', '/citas/por-confirmar', undefined, params),
      version: () => peticion('GET', '/citas/version'),
      recordatorio: (id) => peticion('POST', `/citas/${id}/recordatorio`, {}),

      // RF4: recompactación de la agenda de un día
      reacomodoPrevia: (params) => peticion('GET', '/citas/reacomodo', undefined, params),
      reacomodoAplicar: (datos) => peticion('POST', '/citas/reacomodo', datos),
    },
    indicadores: {
      resumen: (params) => peticion('GET', '/indicadores/resumen', undefined, params),
      serie: (params) => peticion('GET', '/indicadores/serie-ausentismo', undefined, params),
      porProfesional: (params) => peticion('GET', '/indicadores/por-profesional', undefined, params),
      patrones: (params) => peticion('GET', '/indicadores/patrones', undefined, params),
      pacientesRiesgo: (params) => peticion('GET', '/indicadores/pacientes-riesgo', undefined, params),
    },
    usuarios: {
      listar: () => peticion('GET', '/usuarios'),
      crear: (datos) => peticion('POST', '/usuarios', datos),
      actualizar: (id, datos) => peticion('PUT', `/usuarios/${id}`, datos),
      desactivar: (id) => peticion('DELETE', `/usuarios/${id}`),
      eliminar: (id) => peticion('DELETE', `/usuarios/${id}`, undefined, { definitivo: 1 }),
    },
  };
})();
