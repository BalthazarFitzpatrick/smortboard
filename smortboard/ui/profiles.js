// credential profiles (shift+p): several claude subscriptions, one active at a time, from
// smortboard/profiles.py. lists each profile's state, pastes a token straight into its mode-600
// file, and lets the operator activate or remove one by hand.
//
// built from the same modal-backdrop / panel-floating pair as preflight.js and inbox.js - nothing
// here needs arrow/enter/escape hijacked from the document, so it stays out of Menu.
//
// relies on globals board.js already defines: api, apiOrError, reenterIfFocusLost.
//
// THE TOKEN NEVER COMES BACK. the add form clears its field on submit whether the paste succeeds
// or not, and nothing here ever writes a token into localStorage or renders one back from the api
// - the response and every row only ever carry name/active/present/limited_until.

const pr = {backdrop: null, panel: null, listEl: null, rows: []};

function hazardPlaceholder(text) {
  const box = document.createElement('div');
  box.className = 'hazard-stripes hazard-placeholder';
  const label = document.createElement('span');
  label.className = 'hazard-label';
  label.textContent = text;
  box.appendChild(label);
  return box;
}

function clearChildren(el) {
  [...el.children].forEach(child => child.remove());
}

function profileStateLabel(row) {
  if (!row.present) return 'no token file';
  if (row.limited_until) {
    const resets = new Date(row.limited_until * 1000);
    if (!Number.isNaN(resets.getTime()) && resets.getTime() > Date.now()) {
      return `rate-limited until ${resets.toLocaleTimeString()}`;
    }
  }
  return 'present';
}

function buildProfileRow(row) {
  const rowEl = document.createElement('div');
  rowEl.className = 'profile-row';
  rowEl.dataset.name = row.name;

  const head = document.createElement('div');
  head.className = 'profile-row-head';

  const name = document.createElement('span');
  name.className = 'profile-name';
  name.textContent = row.name;
  if (row.active) name.classList.add('profile-active');

  const active = document.createElement('span');
  active.className = 'field-label profile-active-label';
  active.textContent = row.active ? 'active' : '';

  const state = document.createElement('span');
  state.className = 'field-label profile-state';
  state.textContent = profileStateLabel(row);

  const status = document.createElement('span');
  status.className = 'profile-status';

  head.append(name, active, state, status);

  const actions = document.createElement('div');
  actions.className = 'profile-actions';

  if (!row.active) {
    const activateBtn = document.createElement('span');
    activateBtn.className = 'profile-activate toggle';
    activateBtn.textContent = 'activate';
    activateBtn.onclick = () => activateProfile(row.name, activateBtn, status);
    actions.appendChild(activateBtn);
  }

  // any profile can be removed now, "default" included - the server refuses only the last
  // remaining one, and removing the active profile switches active away first
  const removeBtn = document.createElement('span');
  removeBtn.className = 'profile-remove toggle';
  removeBtn.textContent = 'remove';
  removeBtn.onclick = () => removeProfile(row.name, removeBtn, status);
  actions.appendChild(removeBtn);

  rowEl.append(head, actions);
  return rowEl;
}

function buildAddForm() {
  const form = document.createElement('div');
  form.className = 'profile-add-row';

  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.className = 'profile-add-name text-field';
  nameInput.placeholder = 'profile name';

  const tokenInput = document.createElement('input');
  tokenInput.type = 'password';
  tokenInput.className = 'profile-add-token text-field';
  tokenInput.placeholder = 'paste a token';

  const submit = document.createElement('span');
  submit.className = 'profile-add-submit toggle';
  submit.textContent = 'add';

  const status = document.createElement('span');
  status.className = 'profile-status';

  const submitAdd = () => addProfile(nameInput, tokenInput, submit, status);
  submit.onclick = submitAdd;
  [nameInput, tokenInput].forEach(input => {
    input.addEventListener('keydown', evt => {
      if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(evt.target); return; }
      if (evt.code !== 'Enter') return;
      evt.preventDefault();
      submitAdd();
    });
  });

  form.append(nameInput, tokenInput, submit, status);
  return form;
}

async function addProfile(nameInput, tokenInput, submit, status) {
  const name = nameInput.value.trim();
  const token = tokenInput.value;
  // cleared here, before the request even resolves - a token never survives past this point
  tokenInput.value = '';
  if (!name || !token) {
    status.textContent = 'name and token are both required';
    status.className = 'profile-status profile-error';
    return;
  }
  submit.classList.add('dim');
  status.textContent = 'adding...';
  status.className = 'profile-status';
  const {ok, body} = await apiOrError('/api/profiles', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name, token}),
  });
  submit.classList.remove('dim');
  if (!ok) {
    status.textContent = (body && body.error) || 'could not add that profile';
    status.className = 'profile-status profile-error';
    return;
  }
  nameInput.value = '';
  status.textContent = '';
  loadProfiles();
}

async function activateProfile(name, button, status) {
  button.classList.add('dim');
  status.textContent = 'activating...';
  status.className = 'profile-status';
  const {ok, body} = await apiOrError(`/api/profiles/${encodeURIComponent(name)}/activate`, {method: 'POST'});
  if (!ok) {
    status.textContent = (body && body.error) || 'could not activate';
    status.className = 'profile-status profile-error';
    button.classList.remove('dim');
    return;
  }
  loadProfiles();
}

async function removeProfile(name, button, status) {
  button.classList.add('dim');
  status.textContent = 'removing...';
  status.className = 'profile-status';
  const {ok, body} = await apiOrError(`/api/profiles/${encodeURIComponent(name)}`, {method: 'DELETE'});
  if (!ok) {
    status.textContent = (body && body.error) || 'could not remove';
    status.className = 'profile-status profile-error';
    button.classList.remove('dim');
    return;
  }
  loadProfiles();
}

function renderProfiles() {
  clearChildren(pr.listEl);
  if (!pr.rows.length) {
    pr.listEl.appendChild(hazardPlaceholder('no profiles yet'));
  } else {
    pr.rows.forEach(row => pr.listEl.appendChild(buildProfileRow(row)));
  }
  pr.listEl.appendChild(buildAddForm());
}

async function loadProfiles() {
  clearChildren(pr.listEl);
  pr.listEl.appendChild(hazardPlaceholder('loading...'));
  try {
    pr.rows = await api('/api/profiles');
  } catch (err) {
    clearChildren(pr.listEl);
    pr.listEl.appendChild(hazardPlaceholder(`could not load: ${err.message}`));
    return;
  }
  renderProfiles();
}

function buildProfilesDom() {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop profiles-backdrop';
  const panel = document.createElement('div');
  panel.className = 'panel-floating profiles-panel';

  const title = document.createElement('div');
  title.className = 'popup-title';
  title.textContent = 'credential profiles';
  const rule = document.createElement('div');
  rule.className = 'h-divider';

  const list = document.createElement('div');
  list.className = 'profile-list';

  panel.append(title, rule, list);
  backdrop.appendChild(panel);
  backdrop.addEventListener('mousedown', evt => { if (evt.target === backdrop) closeProfilesPanel(); });

  Object.assign(pr, {backdrop, panel, listEl: list});
  return backdrop;
}

function onProfilesKey(evt) {
  if (evt.code === 'Escape') closeProfilesPanel();
}

function openProfilesPanel() {
  if (!pr.backdrop) buildProfilesDom();
  document.body.appendChild(pr.backdrop);
  document.addEventListener('keydown', onProfilesKey);
  loadProfiles();
  focusPanel(pr.panel);
}

function closeProfilesPanel() {
  if (!pr.backdrop || !pr.backdrop.parentNode) return;
  document.removeEventListener('keydown', onProfilesKey);
  pr.backdrop.remove();
  reenterIfFocusLost();
}

function toggleProfilesPanel() {
  if (pr.backdrop && pr.backdrop.parentNode) closeProfilesPanel();
  else openProfilesPanel();
}
