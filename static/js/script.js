let todosLosCorreos = [];
let categoriaActiva = 'principal';
let tipoFeedback    = 'spam';
let intervaloAuto   = null;
let _mapaCorreos    = {};
let _fpmTipo        = 'spam';
let _fpmCorreoId    = null;
let _cargandoFondo  = false;
let _tooltipEl      = null;
let _correosLeidos  = new Set(JSON.parse(localStorage.getItem('ilico_leidos') || '[]'));

document.addEventListener('DOMContentLoaded', () => {
  cargarPerfil();
  cargarStats();
  sincronizarCorreccionesAlServidor();
  cargarChips();
  selTipo('spam');
  prepararLogout();
  iniciarTooltipNivel();
  cargarDesdeCache();
});

function prepararLogout() {
  const btn = document.getElementById('btn-logout');
  if (!btn) return;
  btn.addEventListener('click', e => {
    e.preventDefault();
    detenerAutoRefresh();
    todosLosCorreos = [];
    _mapaCorreos    = {};
    ['badge-principal','badge-archivados'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.textContent = '—';
    });
    window.location.href = '/auth/logout';
  });
}

function detenerAutoRefresh() {
  if (intervaloAuto) {
    clearInterval(intervaloAuto);
    intervaloAuto = null;
  }
}

function dedup(lista) {
  const m = new Map();
  for (const c of lista || []) {
    const id = c && c.id != null ? String(c.id).trim() : '';
    if (id && !m.has(id)) m.set(id, c);
  }
  return [...m.values()];
}

function guardarCorreccionesLocal(spam, ham) {
  localStorage.setItem('ilico_correcciones', JSON.stringify({spam, ham}));
}

async function sincronizarCorreccionesAlServidor() {
  const raw = localStorage.getItem('ilico_correcciones');
  if (!raw) return;
  try {
    const data = JSON.parse(raw);
    if (!data.spam?.length && !data.ham?.length) return;
    await fetch('/api/correcciones/sincronizar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  } catch {}
}

async function cargarDesdeCache() {
  try {
    const r  = await fetch('/api/correos/cache?categoria=' + categoriaActiva);
    if (r.ok) {
      const d = await r.json();
      if (d.correos && d.correos.length > 0) {
        todosLosCorreos = dedup(d.correos);
        actualizarBadge(categoriaActiva, d.stats);
        renderTabla(todosLosCorreos);
        mostrarHoraActualizacion();
        if (!d.stale) {
          iniciarAutoRefresh();
          return;
        }
      }
    }
  } catch {}
  await cargarCorreos(false);
}

async function cargarCorreos(forzar = false) {
  const btn = document.getElementById('btn-cargar');
  if (btn) { btn.disabled = true; btn.textContent = 'Analizando...'; }

  if (!todosLosCorreos.length) {
    document.getElementById('tabla-contenido').innerHTML =
      '<div class="loading"><div class="spinner"></div>Cargando correos...</div>';
  }

  try {
    const url = '/api/correos?refresh=' + (forzar ? '1' : '0') +
                '&categoria=' + encodeURIComponent(categoriaActiva);
    const r = await fetch(url);

    if (r.status === 401) {
      document.getElementById('tabla-contenido').innerHTML =
        '<div class="empty"><div class="empty-icon">🔒</div><div>Conecta tu Gmail para comenzar</div></div>';
      return;
    }
    if (r.status === 503) {
      document.getElementById('tabla-contenido').innerHTML =
        '<div class="loading"><div class="spinner"></div>El modelo de IA está iniciando, espera un momento...</div>';
      setTimeout(() => cargarCorreos(forzar), 4000);
      return;
    }

    const d = await r.json();
    if (d.correos && d.correos.length > 0) {
      todosLosCorreos = dedup(d.correos);
      actualizarBadge(categoriaActiva, d.stats);
      renderTabla(todosLosCorreos);
      mostrarHoraActualizacion();
    } else if (!d.loading) {
      todosLosCorreos = [];
      mostrarBandejaVacia();
    }

    _cargandoFondo = !!d.loading;
    iniciarAutoRefresh();

  } catch {
    if (!todosLosCorreos.length)
      document.getElementById('tabla-contenido').innerHTML =
        '<div class="empty"><div class="empty-icon">⚠️</div><div>Error al conectar con Gmail</div></div>';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Actualizar'; }
  }
}

function iniciarAutoRefresh() {
  if (intervaloAuto) return;
  const ind = document.getElementById('refresh-indicator');
  if (ind) ind.style.display = 'flex';

  intervaloAuto = setInterval(async () => {
    try {
      const r = await fetch('/api/correos/cache?categoria=' + encodeURIComponent(categoriaActiva));
      if (!r.ok) return;
      const d = await r.json();
      if (!d.correos || !d.correos.length) return;

      _cargandoFondo = !!d.loading;

      if (d.correos && d.correos.length > 0) {
        const prevIds  = new Set(todosLosCorreos.map(c => c.id));
        const llegaron = d.correos.filter(c => !prevIds.has(c.id));
        todosLosCorreos = dedup(d.correos);
        actualizarBadge(categoriaActiva, d.stats);
        renderTabla(todosLosCorreos);
        mostrarHoraActualizacion();
        if (llegaron.length > 0 && !_cargandoFondo)
          mostrarToast(`📬 ${llegaron.length} correo${llegaron.length > 1 ? 's' : ''} nuevo${llegaron.length > 1 ? 's' : ''}.`);
      } else if (d.vacio || (!d.loading && d.correos && d.correos.length === 0)) {
        todosLosCorreos = [];
        mostrarBandejaVacia();
      }
    } catch {}
  }, 5000);
}

async function cambiarCategoria(categoria, btn) {
  detenerAutoRefresh();
  todosLosCorreos = [];
  _mapaCorreos    = {};
  _cargandoFondo  = false;
  categoriaActiva = categoria;

  document.getElementById('tabla-contenido').innerHTML =
    '<div class="loading"><div class="spinner"></div>Cargando correos...</div>';

  document.querySelectorAll('.nav-categoria').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');

  const secBandeja = document.getElementById('sec-bandeja');
  document.querySelectorAll('.section').forEach(s => s.classList.remove('visible'));
  if (secBandeja) secBandeja.classList.add('visible');

  const titulos = {
    principal:  'Bandeja de entrada · Principal',
    archivados: 'Bandeja de entrada · Archivados',
  };
  document.getElementById('topbar-title').textContent = titulos[categoria] || 'Bandeja de entrada';

  try {
    const r = await fetch('/api/correos/cache?categoria=' + encodeURIComponent(categoria));
    if (r.ok) {
      const d = await r.json();
      if (d.correos && d.correos.length > 0 && !d.stale) {
        todosLosCorreos = dedup(d.correos);
        actualizarBadge(categoria, d.stats);
        renderTabla(todosLosCorreos);
        mostrarHoraActualizacion();
        iniciarAutoRefresh();
        return;
      }
      if (d.vacio) {
        mostrarBandejaVacia();
        iniciarAutoRefresh();
        return;
      }
    }
  } catch {}

  await cargarCorreos(false);
}

function mostrarSeccion(id, btn) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('visible'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.nav-categoria').forEach(b => b.classList.remove('active'));

  const sec = document.getElementById('sec-' + id);
  if (sec) sec.classList.add('visible');
  if (btn) btn.classList.add('active');

  const titulos = { clasificar: 'Clasificar texto', aprender: 'Enseñar al sistema' };
  document.getElementById('topbar-title').textContent = titulos[id] || '';

  if (id !== 'clasificar') resetResultBox();
}

function resetResultBox() {
  const box = document.getElementById('result-box');
  if (!box) return;
  box.classList.remove('visible', 'result-loading');
  box.style.background = box.style.borderColor = '';
  [['r-icon',''],['r-clas',''],['r-conf',''],['r-pspam','—'],['r-pham','—']].forEach(([id,txt]) => {
    const el = document.getElementById(id);
    if (el) { el.textContent = txt; el.style.color = ''; }
  });
}

async function cargarPerfil() {
  try {
    const r = await fetch('/api/perfil');
    const d = await r.json();
    const emailEl = document.getElementById('user-email');
    const dotEl   = document.getElementById('user-dot');
    if (d.autenticado) {
      if (emailEl) emailEl.textContent = d.email;
      if (dotEl) {
        dotEl.style.background = 'var(--ham)';
        dotEl.style.boxShadow  = '0 0 8px var(--ham-glow)';
      }
    } else {
      if (emailEl) emailEl.textContent = 'Sin conectar';
    }
  } catch {}
}

async function cargarStats() {
  try {
    const r = await fetch('/api/stats');
    const d = await r.json();
    const sc = document.getElementById('teach-spam-count');
    const hc = document.getElementById('teach-ham-count');
    if (sc) sc.textContent = d.correcciones_spam ?? 0;
    if (hc) hc.textContent = d.correcciones_ham  ?? 0;
  } catch {}
}

function actualizarBadge(categoria, stats) {
  const el = document.getElementById('badge-' + categoria);
  if (el && stats && stats.total !== undefined)
    el.textContent = stats.total;
}

function mostrarHoraActualizacion() {
  const hora = new Date().toLocaleTimeString('es-DO', { hour: '2-digit', minute: '2-digit' });
  let el = document.getElementById('last-updated-bar');
  if (!el) {
    el = document.createElement('div');
    el.id        = 'last-updated-bar';
    el.className = 'last-updated';
    const tw = document.querySelector('.table-wrap');
    const tc = document.getElementById('tabla-contenido');
    if (tw && tc) tw.insertBefore(el, tc);
  }
  el.textContent = `Última actualización: ${hora}`;
}

function calcularNivel(clasificacion, probHam, probSpam) {
  let pct;
  if (clasificacion === 'SPAM')       pct = Math.round(100 - (probSpam || 0));
  else if (clasificacion === 'HAM')   pct = Math.round(probHam || 0);
  else                                pct = Math.round(probHam || 50);
  pct = Math.max(0, Math.min(100, pct));

  if (pct <= 20) return { clave: 'arriesgado',   pct, tooltip: 'Este correo presenta características altamente sospechosas. No interactúes con él.' };
  if (pct <= 40) return { clave: 'cuestionable', pct, tooltip: 'Este correo tiene indicios de no ser legítimo. Revísalo con precaución.' };
  if (pct <= 60) return { clave: 'ambiguo',      pct, tooltip: 'El sistema no puede determinar con certeza si este correo es seguro. Revísalo manualmente.' };
  if (pct <= 80) return { clave: 'fiable',       pct, tooltip: 'Este correo parece legítimo. Verifica el remitente antes de responder.' };
  return           { clave: 'seguro',        pct, tooltip: 'Este correo fue identificado como completamente legítimo y confiable.' };
}

function iniciarTooltipNivel() {
  _tooltipEl = document.createElement('div');
  _tooltipEl.className = 'nivel-tooltip';
  document.body.appendChild(_tooltipEl);

  document.addEventListener('mouseover', e => {
    const wrap = e.target.closest('.nivel-bar-wrap');
    if (!wrap) return;
    const txt = wrap.dataset.tooltip;
    if (!txt) return;
    _tooltipEl.textContent = txt;
    _tooltipEl.classList.add('visible');
  });

  document.addEventListener('mousemove', e => {
    if (!_tooltipEl.classList.contains('visible')) return;
    const x = e.clientX + 16;
    const y = e.clientY - 10;
    const w = _tooltipEl.offsetWidth;
    _tooltipEl.style.left = (x + w > window.innerWidth ? e.clientX - w - 16 : x) + 'px';
    _tooltipEl.style.top  = y + 'px';
  });

  document.addEventListener('mouseout', e => {
    if (e.target.closest('.nivel-bar-wrap')) return;
    _tooltipEl.classList.remove('visible');
  });
}

function mostrarBandejaVacia() {
  const tc  = document.getElementById('tabla-contenido');
  const nom = categoriaActiva === 'archivados' ? 'archivados' : 'en la bandeja principal';
  if (tc) tc.innerHTML = `<div class="empty"><div class="empty-icon">📭</div><div>No hay correos ${nom}.</div></div>`;
  const badge = document.getElementById('badge-' + categoriaActiva);
  if (badge) badge.textContent = '0';
}

function badgeInfo(clas) {
  if (clas === 'SPAM')       return { badge: '<span class="badge badge-spam"><span class="badge-dot"></span>SPAM</span>' };
  if (clas === 'HAM')        return { badge: '<span class="badge badge-ham"><span class="badge-dot"></span>HAM</span>' };
  if (clas === 'SOSPECHOSO') return { badge: '<span class="badge badge-sosp"><span class="badge-dot"></span>DUDA</span>' };
  return { badge: '<span class="badge badge-ind">—</span>' };
}

function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function formatearFechaLocal(fechaStr) {
  if (!fechaStr) return '—';
  try {
    const partes = fechaStr.match(/(\d+)\s+(\w+)\s+(\d+),\s+(\d+):(\d+)/);
    if (!partes) return fechaStr;
    const meses = {ene:0,feb:1,mar:2,abr:3,may:4,jun:5,
                   jul:6,ago:7,sep:8,oct:9,nov:10,dic:11};
    const d = new Date(
      parseInt(partes[3]),
      meses[partes[2].toLowerCase()] ?? 0,
      parseInt(partes[1]),
      parseInt(partes[4]),
      parseInt(partes[5])
    );
    if (isNaN(d.getTime())) return fechaStr;
    const locale = navigator.language || 'es-DO';
    const hora   = d.toLocaleTimeString(locale, {hour:'2-digit', minute:'2-digit', hour12:true});
    const fecha  = d.toLocaleDateString(locale, {day:'numeric', month:'short', year:'numeric'});
    return `${fecha}, ${hora}`;
  } catch {
    return fechaStr;
  }
}

function renderTabla(correos) {
  const tc = document.getElementById('tabla-contenido');
  if (!correos.length) {
    tc.innerHTML = '<div class="empty"><div class="empty-icon">📭</div><div>No hay correos para mostrar.</div></div>';
    return;
  }

  _mapaCorreos = {};
  correos.forEach(c => { _mapaCorreos[c.id] = c; });

  const filas = correos.map(c => {
    const { badge } = badgeInfo(c.clasificacion);
    const niv       = calcularNivel(c.clasificacion, c.prob_ham, c.prob_spam);
    const dotNuevo = _correosLeidos.has(String(c.id)) ? '' : '<span class="dot-nuevo"></span>';
    return `<tr data-id="${esc(c.id)}" onclick="abrirCorreo(this.dataset.id)">
      <td class="td-asunto" title="${esc(c.asunto)}">${dotNuevo}${esc(c.asunto)}</td>
      <td class="td-remite">${esc(c.remite)}</td>
      <td class="td-fecha">${esc(formatearFechaLocal(c.fecha))}</td>
      <td>${badge}</td>
      <td>
        <div class="nivel-wrap">
          <div class="nivel-bar-wrap" data-tooltip="${esc(niv.tooltip)}">
            <div class="nivel-bar">
              <div class="nivel-bar-fill fill-${niv.clave}" style="width:${niv.pct}%"></div>
            </div>
            <span class="nivel-pct">${niv.pct}%</span>
          </div>
        </div>
      </td>
    </tr>`;
  }).join('');

  tc.innerHTML = `<table>
    <thead><tr>
      <th>Asunto</th><th>Remitente</th><th>Fecha</th><th>Clasificación</th><th>Nivel de Confianza</th>
    </tr></thead>
    <tbody>${filas}</tbody>
  </table>`;
}

async function clasificarTexto() {
  const texto = document.getElementById('txt-input').value.trim();
  if (!texto) { mostrarToast('Escribe un mensaje primero.'); return; }
  const box = document.getElementById('result-box');
  box.classList.add('result-loading');
  box.classList.add('visible');
  document.getElementById('r-icon').textContent  = '⏳';
  document.getElementById('r-clas').textContent  = 'Analizando...';
  document.getElementById('r-conf').textContent  = '';
  try {
    const r = await fetch('/api/clasificar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ texto })
    });
    const d = await r.json();
    mostrarResultado(d);
  } catch {
    mostrarToast('Error al clasificar el texto.');
    box.classList.remove('result-loading', 'visible');
  }
}

function mostrarResultado(d) {
  const box    = document.getElementById('result-box');
  const iconos = { SPAM:'🚫', HAM:'✅', SOSPECHOSO:'⚠️', INDETERMINADO:'❓' };
  const colores= { SPAM:'var(--spam)', HAM:'var(--ham)', SOSPECHOSO:'var(--sosp)', INDETERMINADO:'var(--text-2)' };
  document.getElementById('r-icon').textContent  = iconos[d.clasificacion]  || '❓';
  document.getElementById('r-clas').textContent  = d.clasificacion;
  document.getElementById('r-clas').style.color  = colores[d.clasificacion] || '';
  document.getElementById('r-conf').textContent  = `Confianza: ${d.confianza}%${d.ajustado ? ' · ajustado' : ''}`;
  document.getElementById('r-pspam').textContent = (d.prob_spam ?? '—') + '%';
  document.getElementById('r-pham').textContent  = (d.prob_ham  ?? '—') + '%';
  if (d.razon) document.getElementById('r-conf').textContent += ` · ${d.razon}`;
  const col = colores[d.clasificacion];
  box.style.background  = d.clasificacion === 'SPAM' ? 'var(--spam-dim)' : d.clasificacion === 'HAM' ? 'var(--ham-dim)' : d.clasificacion === 'SOSPECHOSO' ? 'var(--sosp-dim)' : 'var(--bg-2)';
  box.style.borderColor = col ? col.replace(')', ',0.3)').replace('var(','rgba(') : 'var(--border)';
  box.classList.remove('result-loading');
  box.classList.add('visible');
}

function selTipo(tipo) {
  tipoFeedback = tipo;
  document.getElementById('btn-spam-tipo').classList.toggle('sel', tipo === 'spam');
  document.getElementById('btn-ham-tipo').classList.toggle('sel',  tipo === 'ham');
}

async function enviarFeedback() {
  const campo   = document.getElementById('fb-palabra');
  const entrada = campo.value.trim();
  if (!entrada) { mostrarToast('Escribe una palabra primero.'); return; }
  const palabras = entrada.split(',').map(p => p.trim()).filter(p => p.length > 2);
  if (!palabras.length) { mostrarToast('Escribe palabras de al menos 3 caracteres.'); return; }
  try {
    const r = await fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ palabras, tipo: tipoFeedback })
    });
    const d = await r.json();
    mostrarToast(d.mensaje || 'Guardado.');
    campo.value = '';
    cargarChips();
    cargarStats();
  } catch {
    mostrarToast('Error al guardar.');
  }
}

async function cargarChips() {
  try {
    const r = await fetch('/api/correcciones');
    const d = await r.json();
    renderListaCorrecciones('spam', d.spam || []);
    renderListaCorrecciones('ham',  d.ham  || []);
    const sc = document.getElementById('teach-spam-count');
    const hc = document.getElementById('teach-ham-count');
    if (sc) sc.textContent = (d.spam || []).length;
    if (hc) hc.textContent = (d.ham  || []).length;
    guardarCorreccionesLocal(d.spam || [], d.ham || []);
  } catch {}
}

function renderListaCorrecciones(tipo, palabras) {
  const lista  = document.getElementById('lista-' + tipo);
  const empty  = document.getElementById('empty-' + tipo);
  const count  = document.getElementById('count-' + tipo + '-label');
  if (count) count.textContent = palabras.length;
  if (!palabras.length) {
    lista.innerHTML = '';
    if (empty) { lista.appendChild(empty); empty.style.display = 'block'; }
    return;
  }
  if (empty) empty.style.display = 'none';
  lista.innerHTML = palabras.map(p => crearItemHTML(p, tipo)).join('');
}

function crearItemHTML(palabra, tipo) {
  return `<li class="correccion-item" id="item-${tipo}-${esc(palabra)}">
    <span class="correccion-palabra">${esc(palabra)}</span>
    <div class="correccion-acciones">
      <button class="btn-icon editar"   onclick="iniciarEdicion('${esc(palabra)}','${tipo}')">✏️</button>
      <button class="btn-icon eliminar" onclick="eliminarCorreccion('${esc(palabra)}','${tipo}')">🗑️</button>
    </div>
  </li>`;
}

function iniciarEdicion(palabra, tipo) {
  const item = document.getElementById('item-' + tipo + '-' + palabra);
  if (!item) return;
  item.innerHTML = `
    <input class="correccion-input" id="input-edicion-${tipo}-${esc(palabra)}" value="${esc(palabra)}"
           onkeydown="if(event.key==='Enter')guardarEdicion('${esc(palabra)}','${tipo}');if(event.key==='Escape')cancelarEdicion('${esc(palabra)}','${tipo}')">
    <div class="correccion-acciones">
      <button class="btn-icon guardar"  onclick="guardarEdicion('${esc(palabra)}','${tipo}')">✅</button>
      <button class="btn-icon cancelar" onclick="cancelarEdicion('${esc(palabra)}','${tipo}')">✕</button>
    </div>`;
  document.getElementById('input-edicion-' + tipo + '-' + palabra)?.focus();
}

function cancelarEdicion(palabra, tipo) {
  const item = document.getElementById('item-' + tipo + '-' + palabra);
  if (item) item.outerHTML = crearItemHTML(palabra, tipo);
}

async function guardarEdicion(anterior, tipo) {
  const input = document.getElementById('input-edicion-' + tipo + '-' + anterior);
  if (!input) return;
  const nueva = input.value.toLowerCase().trim();
  if (!nueva || nueva === anterior) { cancelarEdicion(anterior, tipo); return; }
  try {
    const r = await fetch('/api/correcciones/editar', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ palabra_anterior: anterior, palabra_nueva: nueva, tipo })
    });
    const d = await r.json();
    mostrarToast(d.mensaje);
    cargarChips(); cargarStats();
  } catch { mostrarToast('Error.'); cancelarEdicion(anterior, tipo); }
}

async function eliminarCorreccion(palabra, tipo) {
  if (!confirm(`¿Eliminar "${palabra}" de ${tipo.toUpperCase()}?`)) return;
  try {
    const r = await fetch('/api/correcciones/eliminar', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ palabra, tipo })
    });
    const d = await r.json();
    mostrarToast(d.mensaje);
    cargarChips(); cargarStats();
  } catch { mostrarToast('Error.'); }
}

async function abrirCorreo(id) {
  _correosLeidos.add(String(id));
  localStorage.setItem('ilico_leidos', JSON.stringify([..._correosLeidos]));
  const cache  = _mapaCorreos[id] || {};
  const overlay = document.getElementById('modal-overlay');
  overlay.classList.add('open');
  document.body.style.overflow = 'hidden';

  document.getElementById('modal-asunto').textContent = cache.asunto || '(sin asunto)';
  document.getElementById('modal-remite').textContent = cache.remite || '—';
  document.getElementById('modal-fecha').textContent  = cache.fecha  || '—';
  document.getElementById('modal-body').innerHTML =
    '<div class="modal-loading"><div class="spinner"></div>Cargando contenido...</div>';

  const { badge } = badgeInfo(cache.clasificacion);
  document.getElementById('modal-badge').innerHTML   = badge;
  document.getElementById('modal-razon').textContent = cache.razon || '';

  try {
    const r = await fetch('/api/correo/' + encodeURIComponent(id));
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();

    document.getElementById('modal-asunto').textContent = d.asunto || cache.asunto;
    document.getElementById('modal-remite').textContent = d.remite || cache.remite;
    document.getElementById('modal-fecha').textContent  = d.fecha  || cache.fecha;

    if (d.clasificacion) {
      const { badge: b } = badgeInfo(d.clasificacion);
      document.getElementById('modal-badge').innerHTML   = b;
      document.getElementById('modal-razon').textContent = d.razon || '';
    }

    const body = document.getElementById('modal-body');
    if (d.html_cuerpo && d.html_cuerpo.trim()) {
      body.innerHTML = '<div class="modal-cuerpo-html"><iframe id="email-frame" sandbox="allow-same-origin" srcdoc=""></iframe></div>';
      const frame    = document.getElementById('email-frame');
      frame.srcdoc   = d.html_cuerpo;
      frame.onload   = () => {
        try {
          const h = frame.contentDocument.body.scrollHeight;
          frame.style.height = Math.min(h + 20, 600) + 'px';
        } catch {}
      };
    } else if (d.cuerpo && d.cuerpo.trim()) {
      body.innerHTML = `<div class="modal-cuerpo-text">${esc(d.cuerpo)}</div>`;
    } else {
      body.innerHTML = '<div class="empty"><div class="empty-icon">📭</div><div>Sin contenido de texto.</div></div>';
    }
  } catch {
    document.getElementById('modal-body').innerHTML =
      '<div class="empty"><div class="empty-icon">⚠️</div><div>No se pudo cargar el correo.</div></div>';
  }

  if (cache.clasificacion && cache.clasificacion !== 'INDETERMINADO') {
    setTimeout(() => abrirFPM(id, cache.clasificacion, cache.texto_clasificar), 500);
  }
}

function cerrarModal(e) {
  if (e.target === document.getElementById('modal-overlay')) cerrarModalBtn();
}

function cerrarModalBtn() {
  document.getElementById('modal-overlay').classList.remove('open');
  document.body.style.overflow = '';
  document.getElementById('modal-body').innerHTML = '';
  cerrarFPM();
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') cerrarModalBtn();
});

function abrirFPM(id, clasificacion, textoClasificar) {
  _fpmCorreoId        = id;
  _fpmTipo            = clasificacion === 'SPAM' ? 'spam' : 'ham';
  window._fpmTexto    = textoClasificar || '';

  document.getElementById('fpm-correccion').classList.remove('visible');
  document.getElementById('fpm-ok').classList.remove('visible');
  document.getElementById('fpm-pregunta').style.display = 'block';
  document.querySelector('.fpm-btn-row').style.display  = 'flex';
  document.getElementById('fpm-palabra').value = '';

  const label = clasificacion === 'SPAM' ? 'SPAM 🚫' : clasificacion === 'HAM' ? 'HAM ✅' : 'SOSPECHOSO ⚠️';
  document.getElementById('fpm-pregunta').textContent =
    `Ilico clasificó este correo como ${label}. ¿Es correcto?`;

  document.getElementById('fpm').classList.add('open');
}

function cerrarFPM() {
  document.getElementById('fpm').classList.remove('open');
  _fpmCorreoId = null;
}

function fpmRespuesta(correcto) {
  if (correcto) {
    document.getElementById('fpm-pregunta').style.display = 'none';
    document.querySelector('.fpm-btn-row').style.display  = 'none';
    const ok = document.getElementById('fpm-ok');
    ok.textContent = '✅ Clasificación confirmada. ¡Gracias!';
    ok.classList.add('visible');
    setTimeout(cerrarFPM, 1800);
  } else {
    document.getElementById('fpm-correccion').classList.add('visible');
    fpmSelTipo(_fpmTipo === 'spam' ? 'ham' : 'spam');
  }
}

function fpmSelTipo(tipo) {
  _fpmTipo = tipo;
  document.getElementById('fpm-tipo-spam').classList.toggle('sel', tipo === 'spam');
  document.getElementById('fpm-tipo-ham').classList.toggle('sel',  tipo === 'ham');
}

async function fpmGuardar() {
  const campo   = document.getElementById('fpm-palabra');
  const entrada = campo.value.trim();
  if (!entrada) { mostrarToast('Escribe al menos una palabra clave.'); return; }
  const palabras = entrada.split(',').map(p => p.trim()).filter(p => p.length > 2);
  if (!palabras.length) { mostrarToast('Palabras demasiado cortas.'); return; }

  try {
    const r = await fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        palabras,
        tipo:             _fpmTipo,
        texto_clasificar: window._fpmTexto || '',
        correo_id:        _fpmCorreoId,
      })
    });
    const d = await r.json();
    document.getElementById('fpm-correccion').classList.remove('visible');
    document.getElementById('fpm-pregunta').style.display = 'none';
    document.querySelector('.fpm-btn-row').style.display  = 'none';
    const ok = document.getElementById('fpm-ok');
    ok.textContent = `✅ Modelo actualizado con: ${palabras.join(', ')}`;
    ok.classList.add('visible');
    cargarChips();
    cargarStats();
    setTimeout(cerrarFPM, 2500);
  } catch {
    mostrarToast('Error al guardar la corrección.');
  }
}

function mostrarToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3200);
}
