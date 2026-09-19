// credential profiles (shift+p): one active credential per lab, from
// smortboard/profiles.py. lists each profile's state, pastes a token straight into its mode-600
// file, and lets the operator activate or remove one by hand.
//
// built from the same modal-backdrop / panel-floating pair as preflight.js and inbox.js; only the
// two credential choices use Menu through smortui's standard listMenu helper
//
// relies on board globals api, apiOrError and reenterIfFocusLost, plus smortui's listMenu helper
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
  rowEl.dataset.lab = row.lab || 'anthropic';

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
  state.textContent = `${row.kind || 'oauth'} - ${profileStateLabel(row)}`;

  const status = document.createElement('span');
  status.className = 'profile-status';

  head.append(name, active, state, status);

  const actions = document.createElement('div');
  actions.className = 'profile-actions';

  if (!row.active) {
    const activateBtn = document.createElement('span');
    activateBtn.className = 'profile-activate toggle';
    activateBtn.textContent = 'activate';
    activateBtn.onclick = () => activateProfile(row.name, activateBtn, status, row.lab || 'anthropic');
    actions.appendChild(activateBtn);
  }

  // any profile can be removed now, "default" included - the server refuses only the last
  // remaining one, and removing the active profile switches active away first
  const removeBtn = document.createElement('span');
  removeBtn.className = 'profile-remove toggle';
  removeBtn.textContent = 'remove';
  removeBtn.onclick = () => removeProfile(row.name, removeBtn, status, row.lab || 'anthropic');
  actions.appendChild(removeBtn);

  rowEl.append(head, actions);
  return rowEl;
}

function buildAddForm() {
  const form = document.createElement('div');
  form.className = 'profile-add-row';

  let lab = 'anthropic';
  let kind = 'oauth';
  const labChoices = [
    {id: 'anthropic', label: 'anthropic'},
    {id: 'openai', label: 'openai'},
  ];
  const openaiKindChoices = [
    {id: 'auth_json', label: 'ChatGPT login JSON'},
    {id: 'api_key', label: 'API key'},
  ];

  const labButton = document.createElement('button');
  labButton.type = 'button';
  labButton.className = 'profile-add-lab profile-add-choice toggle';
  labButton.setAttribute('aria-label', 'lab');
  labButton.setAttribute('aria-haspopup', 'menu');
  labButton.textContent = lab;

  const kindButton = document.createElement('button');
  kindButton.type = 'button';
  kindButton.className = 'profile-add-kind profile-add-choice toggle';
  kindButton.setAttribute('aria-label', 'credential kind');
  kindButton.setAttribute('aria-haspopup', 'menu');
  kindButton.textContent = openaiKindChoices[0].label;

  labButton.onclick = () => listMenu('choose lab', labChoices.map(choice => ({
    ...choice, on: choice.id === lab,
  })), choice => {
    lab = choice.id;
    labButton.textContent = choice.label;
    if (lab === 'openai') {
      kind = openaiKindChoices[0].id;
      kindButton.textContent = openaiKindChoices[0].label;
      form.insertBefore(kindButton, nameInput);
    } else {
      kind = 'oauth';
      kindButton.remove();
    }
  }).openAt(labButton);

  kindButton.onclick = () => listMenu('credential kind', openaiKindChoices.map(choice => ({
    ...choice, on: choice.id === kind,
  })), choice => {
    kind = choice.id;
    kindButton.textContent = choice.label;
  }).openAt(kindButton);

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

  const submitAdd = () => addProfile(nameInput, tokenInput, submit, status, lab, kind);
  submit.onclick = submitAdd;
  [nameInput, tokenInput].forEach(input => {
    input.addEventListener('keydown', evt => {
      if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(evt.target); return; }
      if (evt.code !== 'Enter') return;
      evt.preventDefault();
      submitAdd();
    });
  });

  form.append(labButton, nameInput, tokenInput, submit, status);
  return form;
}

async function addProfile(nameInput, tokenInput, submit, status, lab = 'anthropic', kind = 'oauth') {
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
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({lab, kind, name, token}),
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

async function activateProfile(name, button, status, lab = 'anthropic') {
  button.classList.add('dim');
  status.textContent = 'activating...';
  status.className = 'profile-status';
  const {ok, body} = await apiOrError(`/api/profiles/${encodeURIComponent(lab)}/${encodeURIComponent(name)}/activate`, {method: 'POST'});
  if (!ok) {
    status.textContent = (body && body.error) || 'could not activate';
    status.className = 'profile-status profile-error';
    button.classList.remove('dim');
    return;
  }
  loadProfiles();
}

async function removeProfile(name, button, status, lab = 'anthropic') {
  button.classList.add('dim');
  status.textContent = 'removing...';
  status.className = 'profile-status';
  const {ok, body} = await apiOrError(`/api/profiles/${encodeURIComponent(lab)}/${encodeURIComponent(name)}`, {method: 'DELETE'});
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
    const labs = [...new Set(pr.rows.map(row => row.lab || 'anthropic'))];
    labs.forEach(lab => {
      const heading = document.createElement('div');
      heading.className = 'field-label';
      heading.textContent = lab;
      pr.listEl.appendChild(heading);
      pr.rows.filter(row => (row.lab || 'anthropic') === lab)
        .forEach(row => pr.listEl.appendChild(buildProfileRow(row)));
    });
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
