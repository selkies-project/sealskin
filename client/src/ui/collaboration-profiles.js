/**
 * The Collaboration tab of the admin page: named profiles of what the
 * collaboration rooms of users and groups may do.
 *
 * The list shows every profile with what it turns off and the users and
 * groups that name it. The editor is a form of the profile's switches beside
 * a mock of the room's bar, seen as the controller, a collaborator, or a
 * viewer, which leaves out what each switch turns off as it is flipped. A
 * room always keeps one kind of invite, so the last one on cannot be turned
 * off.
 */

import { secureFetch } from '../lib/api.js';
import { confirmDialog } from '../lib/modal.js';
import { escapeHtml, showToast } from '../lib/dom.js';

const $ = (id) => document.getElementById(id);
const FEATURES = [
  'chat', 'webcams', 'microphones', 'app_switcher', 'input_handoff', 'gamepads',
  'participant_invites', 'readonly_invites', 'participants_share',
];
// The label key of each feature, for the list's summary.
const LABELS = {
  chat: 'chat',
  webcams: 'webcams',
  microphones: 'microphones',
  app_switcher: 'appSwitcher',
  input_handoff: 'inputHandoff',
  gamepads: 'gamepads',
  participant_invites: 'participantInvites',
  readonly_invites: 'readonlyInvites',
  participants_share: 'participantsShare',
};
const DEFAULTS = { ...Object.fromEntries(FEATURES.map((feature) => [feature, true])), resolution: 'follow', max_viewers: -1 };

let t;
let deps;
let wired = false;
let profiles = [];
// The profile in the editor, and whose view of the room the mock shows.
let draft = null;
let dirty = false;
let view = 'controller';

const tk = (key, vars) => t(`options.collaboration.${key}`, vars);

// --- The list --------------------------------------------------------------------

async function refresh() {
  try {
    profiles = (await secureFetch('/api/admin/collaboration-profiles', { method: 'GET' })).profiles;
  } catch (error) {
    showToast(error.message, true);
    return;
  }
  deps.setProfiles(profiles.map(({ id, name }) => ({ id, name })));
  renderList();
}

function usersOf(profile) {
  const used = profile.used_by || {};
  return [...(used.groups || []).map((name) => tk('groupNamed', { name })), ...(used.users || [])];
}

function summary(profile) {
  const off = FEATURES.filter((feature) => !profile[feature]).map((feature) => tk(LABELS[feature]));
  if (profile.resolution === 'controller') off.push(tk('resolutionFixedShort'));
  if (profile.max_viewers >= 0) off.push(tk('limitShort', { count: profile.max_viewers }));
  return off.length ? off.join(', ') : tk('nothingOff');
}

function renderList() {
  const tbody = document.querySelector('#cp-table tbody');
  if (!profiles.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="bp-empty">${escapeHtml(tk('none'))}</td></tr>`;
    return;
  }
  tbody.innerHTML = profiles.map((profile) => {
    const used = usersOf(profile);
    return `
      <tr data-id="${escapeHtml(profile.id)}">
        <td>${escapeHtml(profile.name)}</td>
        <td>${escapeHtml(summary(profile))}</td>
        <td>${used.length ? escapeHtml(used.join(', ')) : `<span class="bp-muted">${escapeHtml(tk('unused'))}</span>`}</td>
        <td class="actions-cell">
          <button class="warning" data-act="edit">${escapeHtml(t('common.edit'))}</button>
          <button class="secondary" data-act="copy">${escapeHtml(tk('duplicate'))}</button>
          <button class="danger" data-act="delete" ${used.length ? `disabled title="${escapeHtml(tk('inUse', { names: used.join(', ') }))}"` : ''}>${used.length ? '<i class="fas fa-lock"></i> ' : ''}${escapeHtml(t('common.delete'))}</button>
        </td>
      </tr>`;
  }).join('');
}

async function onListClick(event) {
  const button = event.target.closest('button[data-act]');
  if (!button) return;
  const profile = profiles.find((item) => item.id === button.closest('tr').dataset.id);
  if (!profile) return;
  if (button.dataset.act === 'edit') {
    openEditor(profile);
  } else if (button.dataset.act === 'copy') {
    openEditor({ ...profile, id: '', name: tk('copyOf', { name: profile.name }) }, true);
  } else if (button.dataset.act === 'delete') {
    const sure = await confirmDialog(t, {
      title: tk('deleteTitle'), message: tk('confirmDelete', { name: profile.name }), confirm: t('common.delete'), danger: true,
    });
    if (!sure) return;
    try {
      await secureFetch(`/api/admin/collaboration-profiles/${profile.id}`, { method: 'DELETE' });
    } catch (error) {
      showToast(error.message, true);
    }
    await refresh();
  }
}

// --- The editor ------------------------------------------------------------------

function openEditor(profile, changed = false) {
  draft = { ...DEFAULTS, ...profile, id: profile.id || '' };
  dirty = changed;
  $('cp-list').style.display = 'none';
  $('cp-editor').style.display = '';
  $('cp-editor-title').textContent = draft.id ? tk('editProfile', { name: draft.name }) : tk('newProfile');
  $('cp-name').value = draft.name || '';
  FEATURES.forEach((feature) => { $(`cp-${feature}`).checked = Boolean(draft[feature]); });
  $('cp-resolution').value = draft.resolution;
  $('cp-max_viewers').value = draft.max_viewers >= 0 ? draft.max_viewers : '';
  paint();
}

async function closeEditor() {
  if (dirty) {
    const sure = await confirmDialog(t, { title: tk('discardTitle'), message: tk('confirmDiscard'), confirm: tk('discard'), danger: true });
    if (!sure) return;
  }
  draft = null;
  $('cp-editor').style.display = 'none';
  $('cp-list').style.display = '';
  await refresh();
}

function readForm() {
  FEATURES.forEach((feature) => { draft[feature] = $(`cp-${feature}`).checked; });
  draft.resolution = $('cp-resolution').value;
  const limit = parseInt($('cp-max_viewers').value, 10);
  draft.max_viewers = Number.isFinite(limit) && limit >= 0 ? limit : -1;
}

async function save() {
  readForm();
  const body = { ...draft, name: $('cp-name').value.trim() };
  delete body.id;
  delete body.used_by;
  if (!body.name) {
    showToast(tk('nameRequired'), true);
    $('cp-name').focus();
    return;
  }
  try {
    await secureFetch(draft.id ? `/api/admin/collaboration-profiles/${draft.id}` : '/api/admin/collaboration-profiles', {
      method: draft.id ? 'PUT' : 'POST', body: JSON.stringify(body),
    });
  } catch (error) {
    showToast(error.message, true);
    return;
  }
  showToast(tk('saved', { name: body.name }));
  dirty = false;
  await closeEditor();
}

/** Keep the form consistent and paint the mock from it. */
function paint() {
  // The last kind of invite left on stays on.
  $('cp-participant_invites').disabled = !draft.readonly_invites;
  $('cp-readonly_invites').disabled = !draft.participant_invites;
  $('cp-participants_share').disabled = !(draft.participant_invites && draft.readonly_invites);

  const on = (need) => (need === 'follow' ? draft.resolution === 'follow' : Boolean(draft[need]));
  const mock = $('cp-mock');
  mock.querySelectorAll('[data-needs], [data-roles]').forEach((node) => {
    const needs = (node.dataset.needs || '').split(' ').filter(Boolean);
    const roles = (node.dataset.roles || '').split(' ').filter(Boolean);
    node.hidden = !needs.every(on) || (roles.length > 0 && !roles.includes(view));
  });
  mock.querySelector('[data-show-when="controller-resolution"]').hidden = draft.resolution !== 'controller';

  const participantLink = view === 'controller' && draft.participant_invites;
  const readonlyLink = draft.readonly_invites && (view === 'controller' || (view === 'participant' && draft.participants_share));
  $('cp-mock-participant-link').hidden = !participantLink;
  $('cp-mock-readonly-link').hidden = !readonlyLink;
  $('cp-mock-invite').hidden = !participantLink && !readonlyLink;
  $('cp-mock-limit').textContent = draft.max_viewers >= 0 ? tk('previewLimit', { count: draft.max_viewers }) : '';
  document.querySelectorAll('#cp-views button').forEach((button) => {
    button.classList.toggle('active', button.dataset.view === view);
  });
}

function wire() {
  wired = true;
  $('cp-new').addEventListener('click', () => openEditor({}));
  document.querySelector('#cp-table tbody').addEventListener('click', onListClick);
  $('cp-cancel').addEventListener('click', closeEditor);
  $('cp-save').addEventListener('click', save);
  $('cp-form').addEventListener('submit', (event) => event.preventDefault());
  $('cp-form').addEventListener('input', () => {
    if (!draft) return;
    dirty = true;
    readForm();
    paint();
  });
  $('cp-views').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-view]');
    if (!button || !draft) return;
    view = button.dataset.view;
    paint();
  });
}

/**
 * Show the tab.
 *
 * @param {{t: function, setProfiles: function}} options The translator, and what takes the
 *   profiles' names for the user and group forms.
 */
export async function openCollaborationProfiles(options) {
  t = options.t;
  deps = options;
  if (!wired) wire();
  if (!draft) await refresh();
}
