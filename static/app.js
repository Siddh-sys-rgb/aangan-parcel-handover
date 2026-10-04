'use strict';
const $ = (selector) => document.querySelector(selector);
const state = { user: null, csrf: '', parcels: [], residents: [], filter: 'all', query: '', selected: null };
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = (at) => new Date(at * 1000).toLocaleString('en-IN', {day:'numeric', month:'short', hour:'2-digit', minute:'2-digit'});
async function api(path, options = {}) {
  const headers = {'X-CSRF-Token': state.csrf, ...(options.body ? {'Content-Type':'application/json'} : {}), ...options.headers};
  const response = await fetch(path, {...options, headers});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
  return data;
}
function announce(message, error = false) {
  $('#notice').textContent = message; $('#notice').classList.toggle('error', error); $('#notice').hidden = !message;
}
async function refresh() {
  state.parcels = (await api('/api/parcels')).parcels;
  if (state.user.role === 'receptionist') state.residents = (await api('/api/residents')).residents;
  render();
}
function render() {
  const signedIn = Boolean(state.user);
  $('#login-panel').hidden = signedIn; $('#desk-panel').hidden = !signedIn; $('#account').hidden = !signedIn;
  if (!signedIn) return;
  $('#account-name').textContent = `${state.user.name} · ${state.user.unit}`;
  $('#new-arrival').hidden = state.user.role !== 'receptionist';
  $('#desk-title').textContent = state.user.role === 'receptionist' ? 'A place for every arrival.' : 'A little delivery, just for you.';
  $('#desk-subtitle').textContent = state.user.role === 'receptionist' ? 'Good handovers start with a little order.' : `Your parcels for ${state.user.unit}. Bring your pickup code to reception.`;
  $('#waiting-count').textContent = state.parcels.filter(p => p.status === 'waiting').length;
  $('#expired-count').textContent = state.parcels.filter(p => p.code_state === 'expired' || p.code_state === 'locked').length;
  $('#collected-count').textContent = state.parcels.filter(p => p.status === 'collected').length;
  $('#total-count').textContent = `(${state.parcels.length})`;
  const parcels = state.parcels.filter(p => (state.filter === 'all' || p.status === state.filter) && `${p.sender} ${p.description} ${p.resident_name} ${p.unit}`.toLowerCase().includes(state.query));
  $('#parcel-grid').innerHTML = parcels.map(p => {
    const label = p.status === 'collected' ? 'Collected' : p.code_state === 'active' ? 'Waiting for pickup' : p.code_state === 'locked' ? 'Code locked' : 'New code needed';
    const badge = p.status === 'collected' ? 'collected' : p.code_state !== 'active' ? 'warning' : '';
    return `<article class="parcel-card"><div class="parcel-top"><span class="parcel-number">ARRIVAL / ${String(p.id).padStart(3,'0')}</span><span class="badge ${badge}">${label}</span></div><h3>${escape(p.sender)}</h3><p class="parcel-description">${escape(p.description)}</p><div class="resident-line"><span class="unit">${escape(p.unit)}</span><div><strong>${escape(p.resident_name)}</strong><small>RESIDENT / PRERNA RESIDENCY</small></div></div><div class="parcel-bottom"><span>${date(p.arrived_at)}</span><button data-parcel="${p.id}" type="button">${p.status === 'collected' ? 'View handover' : 'Open parcel'} ↗</button></div></article>`;
  }).join('') || '<div class="empty"><strong>A clear desk.</strong>No parcels match this view. Try another filter or record a new arrival.</div>';
  document.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('selected', b.dataset.filter === state.filter));
}
function showIssued(data) {
  $('#issued-code').textContent = data.pickup_code;
  $('#code-description').textContent = `Parcel ${String(data.parcel.id).padStart(3,'0')} · valid for 24 hours. Reissuing immediately invalidates any previous code.`;
  $('#code-notice').hidden = false;
  $('#code-notice').scrollIntoView({behavior:'smooth',block:'center'});
}
async function openParcel(id) {
  const parcel = state.parcels.find(p => p.id === id);
  if (!parcel) return;
  state.selected = parcel;
  const {events} = await api(`/api/parcels/${id}/events`);
  $('#parcel-detail').innerHTML = `<div class="dialog-heading"><div><p class="eyebrow">ARRIVAL / ${String(id).padStart(3,'0')}</p><h2>${escape(parcel.sender)}</h2></div><button class="close" type="button" data-close="parcel-dialog" aria-label="Close parcel details">×</button></div><p>${escape(parcel.description)}</p><div class="detail-meta"><div><span>RESIDENT / FLAT</span><strong>${escape(parcel.resident_name)} · ${escape(parcel.unit)}</strong></div><div><span>PICKUP CODE</span><strong>${escape(parcel.code_state.toUpperCase())} · ${date(parcel.code_expires_at)}</strong></div></div>${parcel.status === 'waiting' && parcel.code_state === 'active' ? `<form id="pickup-form" class="pickup-form"><p>Verify the private code before handing over this parcel. A successful handover consumes the code.</p><div class="row"><label>Six-digit pickup code<input id="pickup-code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="off" required placeholder="••••••"></label><button class="primary" type="submit">Verify & hand over ↗</button></div></form>` : `<p>${parcel.status === 'collected' ? `Collected on ${date(parcel.collected_at)}. This code cannot be used again.` : 'Ask reception to reissue a fresh code before collection.'}</p>`}<div class="detail-actions"><span class="badge">REVISION ${parcel.revision}</span>${state.user.role === 'receptionist' && parcel.status === 'waiting' ? '<button id="reissue" class="quiet" type="button">Issue a new pickup code</button>' : ''}</div><p id="detail-error" class="form-error" role="alert"></p><h3 class="history-title">The handover trail</h3><ol class="timeline">${events.map(e => `<li><strong>${escape(e.actor)} · ${escape(e.kind.replaceAll('_',' '))}</strong><small>${date(e.created_at)}</small><p>${escape(e.detail)}</p></li>`).join('')}</ol>`;
  $('#parcel-dialog').showModal();
  let retryKey = null;
  $('#pickup-code')?.addEventListener('input', () => { retryKey = null; });
  $('#pickup-form')?.addEventListener('submit', async (event) => {
    event.preventDefault(); const button = event.submitter; button.disabled = true;
    retryKey = retryKey || crypto.randomUUID();
    try {
      const data = await api(`/api/parcels/${id}/handover`, {method:'POST', body:JSON.stringify({revision:parcel.revision, pickup_code:$('#pickup-code').value, retry_key:retryKey})});
      $('#parcel-dialog').close(); await refresh(); announce(`Parcel ${id} handed over. ${data.replayed ? 'Previous confirmation restored.' : 'Single-use code consumed and handover recorded.'}`);
    } catch (error) { $('#detail-error').textContent = error.message; }
    finally { button.disabled = false; }
  });
  $('#reissue')?.addEventListener('click', async (event) => {
    event.target.disabled = true;
    try { const data = await api(`/api/parcels/${id}/reissue`, {method:'POST',body:JSON.stringify({revision:parcel.revision})}); $('#parcel-dialog').close(); await refresh(); showIssued(data); announce('New code issued. The previous code is now invalid.'); }
    catch(error) { $('#detail-error').textContent = error.message; event.target.disabled = false; }
  });
}
$('#login-form').addEventListener('submit', async (event) => {
  event.preventDefault(); event.submitter.disabled = true; $('#login-error').textContent = '';
  try {
    const data = await api('/api/login', {method:'POST',body:JSON.stringify({email:$('#login-email').value,password:$('#login-password').value})});
    state.user = data.user; state.csrf = data.csrf_token; await refresh(); announce('Signed in. This demo uses fictional accounts and never sends SMS.');
  } catch(error) { $('#login-error').textContent = error.message; }
  finally { event.submitter.disabled = false; }
});
$('#logout').addEventListener('click', async () => {
  try { const data = await api('/api/logout',{method:'POST',body:'{}'}); state.csrf = data.csrf_token; state.user = null; state.parcels = []; $('#code-notice').hidden = true; announce(''); render(); }
  catch(error) { announce(error.message,true); }
});
document.querySelectorAll('[data-demo]').forEach(button => button.addEventListener('click', () => {
  const email = button.dataset.demo === 'reception' ? 'reception@aangan.demo' : `${button.dataset.demo}@aangan.demo`;
  $('#login-email').value = email; $('#login-password').value = button.dataset.demo === 'reception' ? 'Lobby@2026' : 'Home@2026'; $('#login-error').textContent = '';
}));
$('#new-arrival').addEventListener('click', () => {
  $('#arrival-resident').innerHTML = state.residents.map(r => `<option value="${r.id}">${escape(r.unit)} · ${escape(r.name)}</option>`).join('');
  $('#arrival-form').reset(); $('#arrival-error').textContent = ''; $('#arrival-dialog').showModal();
});
$('#arrival-form').addEventListener('submit', async (event) => {
  event.preventDefault(); event.submitter.disabled = true;
  try { const data = await api('/api/parcels',{method:'POST',body:JSON.stringify({resident_id:Number($('#arrival-resident').value),sender:$('#arrival-sender').value,description:$('#arrival-description').value})}); $('#arrival-dialog').close(); await refresh(); showIssued(data); announce('Arrival recorded. Share the pickup code privately with the resident.'); }
  catch(error) { $('#arrival-error').textContent = error.message; }
  finally { event.submitter.disabled = false; }
});
$('#parcel-grid').addEventListener('click', async (event) => { const button=event.target.closest('[data-parcel]'); if(button) { try {await openParcel(Number(button.dataset.parcel));}catch(error){announce(error.message,true);} } });
document.addEventListener('click', event => { const close=event.target.closest('[data-close]'); if(close) $(`#${close.dataset.close}`).close(); });
document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => {state.filter=button.dataset.filter;render();}));
$('#search').addEventListener('input', event => {state.query=event.target.value.trim().toLowerCase();render();});
$('#dismiss-code').addEventListener('click', () => {$('#code-notice').hidden=true;$('#issued-code').textContent='';});
(async () => {try {const data=await api('/api/session');state.user=data.user;state.csrf=data.csrf_token;if(state.user)await refresh();else render();}catch(error){announce(error.message,true);}})();
