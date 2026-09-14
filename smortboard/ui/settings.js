// settings panel (o): mission control preferences, opened from the top-right button or the o key.
//
// built from the same modal-backdrop / panel-floating pair as preflight.js and inbox.js - nothing
// here needs arrow/enter/escape hijacked from the document, so it stays out of Menu.
//
// scope toggles, snapshot options and the rest arrive as later cards, each pushing a
// {label, node} entry here.
//
// relies on globals board.js already defines: reenterIfFocusLost, api, apiOrError.

const st = {backdrop: null, panel: null, listEl: null};

// auto_switch_profiles: unset/"on" rotates credentials on USAGE_LIMIT (smortboard/profiles.py);
// "off" parks the board until the reset instead, the pre-profiles behaviour. the toggle reads its
// state from GET /api/settings on open (buildAutoSwitchToggle -> loadAutoSwitchToggle), same as
// every board setting - there is no client-side default, an unset key just renders as checked.
function buildAutoSwitchToggle() {
  const wrap = document.createElement('label');
  wrap.className = 'settings-toggle-row';
  const box = document.createElement('input');
  box.type = 'checkbox';
  box.className = 'settings-auto-switch-checkbox';
  box.checked = true;
  const text = document.createElement('span');
  text.textContent = 'switch credential profiles automatically on a usage limit';
  wrap.append(box, text);

  box.addEventListener('change', async () => {
    box.disabled = true;
    const {ok} = await apiOrError('/api/settings', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({auto_switch_profiles: box.checked ? null : 'off'}),
    });
    box.disabled = false;
    if (!ok) box.checked = !box.checked; // revert on a failed save
  });

  loadAutoSwitchToggle(box);
  return wrap;
}

async function loadAutoSwitchToggle(box) {
  try {
    const settings = await api('/api/settings');
    box.checked = settings.auto_switch_profiles !== 'off';
  } catch {
    // leave the default (checked) - a failed load is not worth blocking the panel on
  }
}

const SETTINGS_SECTIONS = [
  {label: 'credential profiles', node: buildAutoSwitchToggle()},
];

function settingsHazardPlaceholder(text) {
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

function buildSettingsSection(section) {
  const box = document.createElement('div');
  box.className = 'settings-section';
  const label = document.createElement('div');
  label.className = 'field-label';
  label.textContent = section.label;
  box.append(label, section.node);
  return box;
}

function renderSettings() {
  clearChildren(st.listEl);
  if (!SETTINGS_SECTIONS.length) {
    st.listEl.appendChild(settingsHazardPlaceholder('no preferences yet'));
    return;
  }
  SETTINGS_SECTIONS.forEach(section => st.listEl.appendChild(buildSettingsSection(section)));
}

function buildSettingsDom() {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop settings-backdrop';
  const panel = document.createElement('div');
  panel.className = 'panel-floating settings-panel';

  const title = document.createElement('div');
  title.className = 'popup-title';
  title.textContent = 'settings';
  const rule = document.createElement('div');
  rule.className = 'h-divider';

  const list = document.createElement('div');
  list.className = 'settings-list';

  panel.append(title, rule, list);
  backdrop.appendChild(panel);
  backdrop.addEventListener('mousedown', evt => { if (evt.target === backdrop) closeSettingsPanel(); });

  Object.assign(st, {backdrop, panel, listEl: list});
  return backdrop;
}

function onSettingsKey(evt) {
  if (evt.code === 'Escape') closeSettingsPanel();
}

function openSettingsPanel() {
  if (!st.backdrop) buildSettingsDom();
  document.body.appendChild(st.backdrop);
  document.addEventListener('keydown', onSettingsKey);
  renderSettings();
}

function closeSettingsPanel() {
  if (!st.backdrop || !st.backdrop.parentNode) return;
  document.removeEventListener('keydown', onSettingsKey);
  st.backdrop.remove();
  reenterIfFocusLost();
}

function toggleSettingsPanel() {
  if (st.backdrop && st.backdrop.parentNode) closeSettingsPanel();
  else openSettingsPanel();
}

// ---- top-right button, fixed to the viewport --------------------------------------------------
// NOT appended inside #board-bar: board.js's renderBoardBar() does `bar.innerHTML = ''` on every
// board list load, which would wipe out a child living there - the same gotcha the attention
// indicator (inbox.js) already ran into. fixed to the viewport instead, at the board bar's corner.

function buildSettingsButton() {
  const btn = document.createElement('div');
  btn.className = 'toggle settings-button';
  btn.textContent = 'settings';
  btn.title = 'settings (o)';
  btn.onclick = () => toggleSettingsPanel();
  document.body.appendChild(btn);
  return btn;
}

buildSettingsButton();
