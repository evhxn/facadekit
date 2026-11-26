<?php
/*
 * Settings and status page, shown inside FPP's UI (menu.inc). Uses FPP's Bootstrap 5 classes
 * only (no colours of its own, so light and dark themes both work) and talks to ajax.php.
 * Play and Stop for published shows call FPP's own playlist API.
 */
require_once __DIR__ . '/lib/common.php';
$fkAjax = 'plugin.php?plugin=' . rawurlencode(FK_REPO) . '&page=ajax.php&nopage=1';
?>
<div id="fk-plugin" class="container-fluid px-0" data-ajax="<?php echo htmlspecialchars($fkAjax, ENT_QUOTES); ?>">
  <h2 class="h4">FacadeKit Projectors <small class="text-body-secondary">v<?php echo htmlspecialchars(FK_PLUGIN_VERSION); ?></small></h2>
  <p class="text-body-secondary">Switches PJLink projectors on and off for your shows and lists the shows FacadeKit sent to this player.
    Nothing happens until you add a projector; automatic power and the fault guard stay off until you turn them on.</p>
  <div id="fk-alert" class="alert d-none" role="alert"></div>

  <section class="border rounded p-3 mb-3">
    <div class="d-flex flex-wrap gap-2 align-items-center mb-2">
      <h3 class="h5 mb-0 me-auto">Status</h3>
      <button type="button" class="btn btn-outline-secondary" id="fk-refresh">Refresh</button>
      <button type="button" class="btn btn-success" id="fk-on">All on</button>
      <button type="button" class="btn btn-outline-danger" id="fk-off">All off</button>
    </div>
    <div id="fk-health" class="mb-2"></div>
    <div id="fk-player" class="mb-2 text-body-secondary"></div>
    <div class="table-responsive">
      <table class="table table-sm align-middle">
        <thead><tr><th>Projector</th><th>Power</th><th>Input</th><th>Hours</th><th>Model</th><th>Problems</th><th>Temperature and recovery</th><th>Checked</th></tr></thead>
        <tbody id="fk-status-rows"><tr><td colspan="8" class="text-body-secondary">Loading…</td></tr></tbody>
      </table>
    </div>
  </section>

  <section class="border rounded p-3 mb-3">
    <h3 class="h5">Projectors</h3>
    <p class="text-body-secondary small mb-2">Address and port of each projector's PJLink service (port 4352). The password is the projector's own
      PJLink password; leave it empty when PJLink authentication is off. Timings only apply while a projector itself reports warming up or
      cooling down: laser projectors such as the Panasonic PT-RZ570 normally need none.</p>
    <div class="table-responsive">
      <table class="table table-sm align-middle">
        <thead><tr><th>Name</th><th>Address</th><th>Port</th><th>Password</th><th>Input</th><th title="Longest wait for the projector to report on">Ready wait (s)</th><th title="Longest wait while it reports cooling before switching on">Cooling wait (s)</th><th title="Delay before the automatic power-off">Off delay (s)</th><th></th></tr></thead>
        <tbody id="fk-projector-rows"></tbody>
      </table>
    </div>
    <button type="button" class="btn btn-outline-primary" id="fk-add">Add projector</button>
  </section>

  <section class="border rounded p-3 mb-3">
    <h3 class="h5">Automatic power and fault guard</h3>
    <div class="form-check form-switch"><input class="form-check-input" type="checkbox" id="fk-auto_on"><label class="form-check-label" for="fk-auto_on">Switch projectors on when a show starts</label></div>
    <div class="form-check form-switch"><input class="form-check-input" type="checkbox" id="fk-auto_off"><label class="form-check-label" for="fk-auto_off">Switch projectors off when a show stops (after each projector's off delay)</label></div>
    <div class="my-2">
      <label class="form-label" for="fk-auto_scope">Which playlists count as shows</label>
      <select class="form-select w-auto mw-100" id="fk-auto_scope">
        <option value="facadekit">Only shows FacadeKit published (listed below)</option>
        <option value="all">Every playlist (also xLights and other shows)</option>
      </select>
    </div>
    <div class="form-check form-switch"><input class="form-check-input" type="checkbox" id="fk-guard_enabled"><label class="form-check-label" for="fk-guard_enabled">Watch the projectors during a show and report faults</label></div>
    <div class="form-check form-switch"><input class="form-check-input" type="checkbox" id="fk-guard_stop_playback"><label class="form-check-label" for="fk-guard_stop_playback">Stop the playlist when a projector reports a fault</label></div>
    <div class="form-check form-switch"><input class="form-check-input" type="checkbox" id="fk-recovery_enabled"><label class="form-check-label" for="fk-recovery_enabled">After a projector temperature error, retry once when it has cooled down and is healthy (the plugin then watches the projectors during a show)</label></div>
    <div class="row g-2 my-1">
      <div class="col-auto"><label class="form-label" for="fk-recovery_cooldown_s">Minimum cooldown (s)</label><input class="form-control" type="number" min="30" max="3600" id="fk-recovery_cooldown_s"></div>
      <div class="col-auto"><label class="form-label" for="fk-recovery_healthy_reads">Healthy readings in a row</label><input class="form-control" type="number" min="2" max="10" id="fk-recovery_healthy_reads"></div>
    </div>
    <p class="small text-body-secondary">Recovery never power-cycles a projector that is still on. It sends no power-on while a projector reports a temperature error, keeps the other projector's show running (unless "Stop the playlist" above is on, which stops the show first and nothing is retried), tries at most once per show, and never restarts a playlist. Switching a projector off, stopping or replacing the show, or turning this off cancels a queued retry.</p>
    <div class="row g-2 my-1">
      <div class="col-auto"><label class="form-label" for="fk-guard_interval_s">Check every (s)</label><input class="form-control" type="number" min="5" max="600" id="fk-guard_interval_s"></div>
      <div class="col-auto"><label class="form-label" for="fk-connect_timeout_s">Connect timeout (s)</label><input class="form-control" type="number" min="1" max="30" id="fk-connect_timeout_s"></div>
      <div class="col-auto"><label class="form-label" for="fk-io_timeout_s">Answer timeout (s)</label><input class="form-control" type="number" min="1" max="60" id="fk-io_timeout_s"></div>
    </div>
    <p class="small text-body-secondary">For a show that needs the picture from the first second, put the command <em>FacadeKit Projectors On</em> with <em>Wait until ready</em> in the playlist's lead-in instead.</p>
    <button type="button" class="btn btn-primary" id="fk-save-settings">Save options</button>
  </section>

  <section class="border rounded p-3 mb-3">
    <h3 class="h5">FacadeKit</h3>
    <div class="row g-2 align-items-end mb-2">
      <div class="col-12 col-md-6"><label class="form-label" for="fk-facadekit_url">FacadeKit address (the computer running FacadeKit)</label><input class="form-control" type="url" id="fk-facadekit_url" placeholder="http://192.168.1.20:8137"></div>
      <div class="col-auto"><button type="button" class="btn btn-outline-secondary" id="fk-check">Check</button></div>
      <div class="col-auto"><a class="btn btn-outline-secondary d-none" id="fk-open" target="_blank" rel="noopener">Open FacadeKit</a></div>
    </div>
    <div id="fk-check-result" class="small mb-2"></div>
    <div id="fk-token-state" class="mb-2"></div>
    <div class="d-flex flex-wrap gap-2">
      <button type="button" class="btn btn-outline-primary" id="fk-token-create">Create pairing token</button>
      <button type="button" class="btn btn-outline-danger" id="fk-token-revoke">Revoke token</button>
    </div>
    <div id="fk-token-show" class="alert alert-info mt-2 d-none"></div>
  </section>

  <section class="border rounded p-3 mb-3">
    <h3 class="h5">Shows published by FacadeKit</h3>
    <div class="table-responsive">
      <table class="table table-sm align-middle">
        <thead><tr><th>Show</th><th>Project</th><th>Published</th><th>Calibration age</th><th>Video / playlist</th><th></th></tr></thead>
        <tbody id="fk-show-rows"></tbody>
      </table>
    </div>
  </section>

  <section class="border rounded p-3 mb-3">
    <div class="d-flex flex-wrap gap-2 align-items-center mb-2">
      <h3 class="h5 mb-0 me-auto">Log</h3>
      <button type="button" class="btn btn-outline-secondary" id="fk-log-refresh">Refresh log</button>
    </div>
    <p class="small text-body-secondary">Passwords and tokens are removed before lines are written and again before they are shown. FPP rotates this log itself.</p>
    <pre id="fk-log" class="border rounded p-2 small bg-body-tertiary" style="max-height: 24rem; overflow: auto; white-space: pre-wrap;"></pre>
  </section>
</div>

<script>
(function () {
  'use strict';
  var root = document.getElementById('fk-plugin');
  var AJAX = root.getAttribute('data-ajax');
  var SETTINGS = ['auto_on', 'auto_off', 'auto_scope', 'guard_enabled', 'guard_stop_playback',
    'guard_interval_s', 'recovery_enabled', 'recovery_cooldown_s', 'recovery_healthy_reads', 'connect_timeout_s', 'io_timeout_s', 'facadekit_url'];
  var state = { projectors: [], settings: {}, timing: {} };

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'class') { node.className = attrs[k]; } else { node.setAttribute(k, attrs[k]); }
    });
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  function alertMsg(text, kind) {
    var box = document.getElementById('fk-alert');
    box.className = 'alert alert-' + (kind || 'danger');
    box.textContent = text;
    if (!text) { box.className = 'alert d-none'; }
  }

  function call(action, extra) {
    var body = Object.assign({ action: action }, extra || {});
    return fetch(AJAX, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-FK-Request': '1' },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: 'HTTP ' + r.status }; });
    }).then(function (data) {
      if (!data.ok && data.error) { throw new Error(data.error); }
      return data;
    });
  }

  function powerLabel(p) {
    var map = { on: 'On', standby: 'Off (standby)', warmup: 'Warming up', cooling: 'Cooling down', busy: 'Busy', unknown: 'Unknown' };
    return map[p] || p || 'Unknown';
  }

  function renderStatus(data) {
    var rows = document.getElementById('fk-status-rows');
    rows.textContent = '';
    if (!data.projectors.length) {
      var tr = el('tr'); tr.appendChild(el('td', { colspan: '8', class: 'text-body-secondary' }, 'No projectors yet. Add one below.'));
      rows.appendChild(tr);
    }
    data.projectors.forEach(function (s) {
      var tr = el('tr');
      tr.appendChild(el('td', {}, s.name));
      var badge = s.ok ? (s.power === 'on' ? 'text-bg-success' : 'text-bg-secondary') : 'text-bg-danger';
      var td = el('td'); td.appendChild(el('span', { class: 'badge ' + badge }, s.ok ? powerLabel(s.power) : 'No answer')); tr.appendChild(td);
      tr.appendChild(el('td', {}, s.input || ''));
      tr.appendChild(el('td', {}, (s.lamps || []).map(function (l) { return l.hours + ' h'; }).join(', ')));
      tr.appendChild(el('td', {}, [s.manufacturer, s.model].filter(Boolean).join(' ')));
      var problems = [];
      if (s.errors) { Object.keys(s.errors).forEach(function (k) { if (s.errors[k] !== 'ok') { problems.push(k + ': ' + s.errors[k]); } }); }
      if (s.error) { problems.push(s.error); }
      tr.appendChild(el('td', { class: problems.length ? 'text-danger' : '' }, problems.join('; ')));
      var th = s.thermal || {};
      var rec = th.recovery || {};
      var tcell = el('td', { class: 'small' });
      var levelTxt = { ok: 'Temperature OK', warning: 'Temperature WARNING', error: 'Temperature ERROR', unknown: 'Temperature not read yet' }[th.level || 'unknown'];
      tcell.appendChild(el('div', { class: th.level === 'error' ? 'text-danger fw-bold' : (th.level === 'warning' ? 'text-warning-emphasis fw-bold' : '') }, levelTxt));
      if (th.last_valid_check_at) { tcell.appendChild(el('div', { class: 'text-body-secondary' }, 'Last good reading ' + new Date(th.last_valid_check_at).toLocaleTimeString())); }
      if (th.recovery_enabled && rec.state && rec.state !== 'idle') {
        tcell.appendChild(el('div', { class: rec.state === 'attention' ? 'text-danger fw-bold' : '' }, 'Recovery: ' + rec.label + (rec.reason ? ' - ' + rec.reason : '')));
        if (rec.cooldown_ends_at) { tcell.appendChild(el('div', {}, 'No retry before ' + new Date(rec.cooldown_ends_at).toLocaleTimeString())); }
        if (rec.wants_on === false) { tcell.appendChild(el('div', {}, 'Switched off by the operator for this show')); }
      }
      if (rec.last_outcome) { tcell.appendChild(el('div', { class: 'text-body-secondary' }, 'Last outcome: ' + rec.last_outcome)); }
      tr.appendChild(tcell);
      tr.appendChild(el('td', { class: 'small text-body-secondary' }, s.checked_at ? new Date(s.checked_at).toLocaleTimeString() : ''));
      rows.appendChild(tr);
    });
    var health = document.getElementById('fk-health');
    health.textContent = '';
    if (data.health && data.health.healthy === false) {
      var box = el('div', { class: 'alert alert-danger d-flex flex-wrap gap-2 align-items-center mb-0' });
      box.appendChild(el('span', { class: 'me-auto' }, 'Fault: ' + (data.health.reason || 'unknown')));
      var clear = el('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary' }, 'Clear');
      clear.addEventListener('click', function () { call('clear_health').then(function () { refresh(false); }); });
      box.appendChild(clear);
      health.appendChild(box);
    }
    var player = document.getElementById('fk-player');
    player.textContent = data.player ? ('FPP: ' + (data.player.status_name || '?') + (data.player.current_playlist ? ', playing ' + data.player.current_playlist : '')) : 'FPP status not available';
  }

  function refresh(live) {
    return call('status', { live: live }).then(renderStatus).catch(function (e) { alertMsg(e.message); });
  }

  function numberCell(value, key) {
    var range = state.timing[key] || [0, 0, 900];
    var input = el('input', { class: 'form-control form-control-sm', type: 'number', min: String(range[1]), max: String(range[2]), 'data-key': key, style: 'max-width: 6rem' });
    input.value = value === undefined ? range[0] : value;
    var td = el('td'); td.appendChild(input); return td;
  }

  function textCell(value, key, placeholder) {
    var input = el('input', { class: 'form-control form-control-sm', type: 'text', 'data-key': key, placeholder: placeholder || '' });
    input.value = value || '';
    var td = el('td'); td.appendChild(input); return td;
  }

  function projectorRow(p) {
    var tr = el('tr');
    tr.appendChild(textCell(p.name, 'name', 'Left'));
    tr.appendChild(textCell(p.host, 'host', '192.168.50.10'));
    var port = numberCell(p.port || 4352, 'port'); port.firstChild.min = '1'; port.firstChild.max = '65535'; tr.appendChild(port);
    var pw = el('td');
    var pwInput = el('input', { class: 'form-control form-control-sm', type: 'password', autocomplete: 'new-password', 'data-key': 'password',
      placeholder: p.has_password ? 'unchanged (set)' : 'none' });
    pw.appendChild(pwInput);
    if (p.has_password) {
      var clearLabel = el('label', { class: 'small d-block' });
      var clearBox = el('input', { type: 'checkbox', class: 'form-check-input me-1', 'data-key': 'password_clear' });
      clearLabel.appendChild(clearBox); clearLabel.appendChild(document.createTextNode('remove'));
      pw.appendChild(clearLabel);
    }
    tr.appendChild(pw);
    tr.appendChild(textCell(p.input, 'input', '31'));
    tr.appendChild(numberCell(p.ready_timeout_s, 'ready_timeout_s'));
    tr.appendChild(numberCell(p.cooldown_timeout_s, 'cooldown_timeout_s'));
    tr.appendChild(numberCell(p.off_delay_s, 'off_delay_s'));
    var actions = el('td', { class: 'text-nowrap' });
    var save = el('button', { type: 'button', class: 'btn btn-sm btn-primary me-1' }, 'Save');
    var test = el('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary me-1' }, 'Test');
    var del = el('button', { type: 'button', class: 'btn btn-sm btn-outline-danger' }, 'Remove');
    save.addEventListener('click', function () {
      var data = { id: p.id || '' };
      tr.querySelectorAll('[data-key]').forEach(function (input) {
        var key = input.getAttribute('data-key');
        if (key === 'password' || key === 'password_clear') { return; }
        data[key] = input.type === 'number' ? Number(input.value) : input.value;
      });
      var clear = tr.querySelector('[data-key="password_clear"]');
      if (clear && clear.checked) { data.password_action = 'clear'; }
      else if (pwInput.value !== '') { data.password_action = 'set'; data.password = pwInput.value; }
      else { data.password_action = 'keep'; }
      call('save_projector', { projector: data }).then(function () {
        pwInput.value = '';
        alertMsg('Saved ' + data.name + '.', 'success');
        load();
      }).catch(function (e) { alertMsg(e.message); });
    });
    test.addEventListener('click', function () {
      if (!p.id) { alertMsg('Save the projector first.'); return; }
      test.disabled = true;
      call('test_projector', { id: p.id }).then(function (d) {
        var s = d.snapshot;
        alertMsg(s.ok ? (p.name + ': ' + powerLabel(s.power) + (s.model ? ' (' + [s.manufacturer, s.model].filter(Boolean).join(' ') + ')' : '') + (s.error ? '; ' + s.error : ''))
          : (p.name + ': ' + s.error), s.ok ? 'success' : 'danger');
        refresh(false);
      }).catch(function (e) { alertMsg(e.message); }).finally(function () { test.disabled = false; });
    });
    del.addEventListener('click', function () {
      if (!p.id) { tr.remove(); return; }
      if (!window.confirm('Remove ' + p.name + ' from the plugin?')) { return; }
      call('delete_projector', { id: p.id }).then(load).catch(function (e) { alertMsg(e.message); });
    });
    actions.appendChild(save); actions.appendChild(test); actions.appendChild(del);
    tr.appendChild(actions);
    return tr;
  }

  function renderProjectors() {
    var rows = document.getElementById('fk-projector-rows');
    rows.textContent = '';
    state.projectors.forEach(function (p) { rows.appendChild(projectorRow(p)); });
  }

  function renderSettings() {
    SETTINGS.forEach(function (key) {
      var input = document.getElementById('fk-' + key);
      if (!input) { return; }
      if (input.type === 'checkbox') { input.checked = !!state.settings[key]; } else { input.value = state.settings[key]; }
    });
    var open = document.getElementById('fk-open');
    if (state.settings.facadekit_url) { open.href = state.settings.facadekit_url; open.classList.remove('d-none'); } else { open.classList.add('d-none'); }
  }

  function renderToken(token) {
    var box = document.getElementById('fk-token-state');
    box.textContent = token.paired
      ? 'Paired: a token exists (created ' + new Date(token.created_at).toLocaleString() + '). FacadeKit uses it for status, power and the show list.'
      : 'Not paired: the plugin\'s API answers nobody until you create a token and paste it into FacadeKit (Send to Falcon Player > Projectors).';
  }

  function ageText(days) {
    if (days === null || days === undefined) { return 'unknown'; }
    if (days < 1) { return 'today'; }
    return Math.round(days) + ' days';
  }

  function renderShows(shows) {
    var rows = document.getElementById('fk-show-rows');
    rows.textContent = '';
    if (!shows.length) {
      var tr = el('tr'); tr.appendChild(el('td', { colspan: '6', class: 'text-body-secondary' }, 'No shows yet. Send one from FacadeKit.'));
      rows.appendChild(tr);
    }
    shows.forEach(function (s) {
      var tr = el('tr');
      tr.appendChild(el('td', {}, s.name));
      tr.appendChild(el('td', {}, s.project || ''));
      tr.appendChild(el('td', { class: 'small' }, s.published_at ? new Date(s.published_at).toLocaleString() : ''));
      tr.appendChild(el('td', {}, ageText(s.calibration_age_days)));
      tr.appendChild(el('td', { class: 'small' }, s.video + ' / ' + s.playlist));
      var td = el('td', { class: 'text-nowrap' });
      var play = el('button', { type: 'button', class: 'btn btn-sm btn-success me-1' }, 'Play');
      var stop = el('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary me-1' }, 'Stop');
      var forget = el('button', { type: 'button', class: 'btn btn-sm btn-outline-danger' }, 'Forget');
      play.addEventListener('click', function () {
        fetch('api/playlist/' + encodeURIComponent(s.playlist) + '/start', { method: 'POST', credentials: 'same-origin' })
          .then(function () { setTimeout(function () { refresh(false); }, 1000); });
      });
      stop.addEventListener('click', function () {
        fetch('api/playlists/stop', { method: 'POST', credentials: 'same-origin' }).then(function () { refresh(false); });
      });
      forget.addEventListener('click', function () {
        if (!window.confirm('Remove "' + s.name + '" from this list? The playlist and video stay in FPP.')) { return; }
        call('show_delete', { playlist: s.playlist }).then(loadShows);
      });
      td.appendChild(play); td.appendChild(stop); td.appendChild(forget);
      tr.appendChild(td);
      rows.appendChild(tr);
    });
  }

  function loadShows() { return call('shows').then(function (d) { renderShows(d.shows); }).catch(function (e) { alertMsg(e.message); }); }
  function loadLog() {
    return call('log').then(function (d) {
      var pre = document.getElementById('fk-log');
      pre.textContent = d.lines.length ? d.lines.join('\n') : '(empty)';
      pre.scrollTop = pre.scrollHeight;
    }).catch(function (e) { alertMsg(e.message); });
  }

  function load() {
    return call('get').then(function (d) {
      state.projectors = d.projectors; state.settings = d.settings; state.timing = d.timing;
      renderProjectors(); renderSettings(); renderToken(d.token);
    }).catch(function (e) { alertMsg(e.message); });
  }

  document.getElementById('fk-add').addEventListener('click', function () {
    document.getElementById('fk-projector-rows').appendChild(projectorRow({ port: 4352 }));
  });
  document.getElementById('fk-refresh').addEventListener('click', function () { refresh(true); });
  ['on', 'off'].forEach(function (action) {
    document.getElementById('fk-' + action).addEventListener('click', function () {
      call('power', { power: action, wait: true }).then(function (d) {
        var bad = d.results.filter(function (r) { return !r.ok; });
        alertMsg(bad.length ? bad.map(function (r) { return r.name + ': ' + r.message; }).join('; ')
          : 'All projectors: ' + action, bad.length ? 'danger' : 'success');
        refresh(false); loadLog();
      }).catch(function (e) { alertMsg(e.message); });
    });
  });
  document.getElementById('fk-save-settings').addEventListener('click', function () {
    var out = {};
    SETTINGS.forEach(function (key) {
      var input = document.getElementById('fk-' + key);
      out[key] = input.type === 'checkbox' ? input.checked : (input.type === 'number' ? Number(input.value) : input.value);
    });
    call('save_settings', { settings: out }).then(function (d) {
      state.settings = d.settings; renderSettings(); alertMsg('Options saved.', 'success');
    }).catch(function (e) { alertMsg(e.message); });
  });
  document.getElementById('fk-check').addEventListener('click', function () {
    var box = document.getElementById('fk-check-result');
    box.textContent = 'Checking…';
    call('check_facadekit').then(function (d) { box.textContent = d.message; box.className = 'small mb-2 ' + (d.ok ? 'text-success' : 'text-danger'); })
      .catch(function (e) { box.textContent = e.message; });
  });
  document.getElementById('fk-token-create').addEventListener('click', function () {
    if (!window.confirm('Create a new pairing token? The old one stops working.')) { return; }
    call('token_create').then(function (d) {
      var box = document.getElementById('fk-token-show');
      box.textContent = '';
      box.appendChild(el('div', {}, 'Copy this token into FacadeKit now. It is not shown again:'));
      box.appendChild(el('code', { class: 'user-select-all text-break' }, d.token));
      box.classList.remove('d-none');
      load();
    }).catch(function (e) { alertMsg(e.message); });
  });
  document.getElementById('fk-token-revoke').addEventListener('click', function () {
    if (!window.confirm('Revoke the token? FacadeKit loses access until you pair again.')) { return; }
    call('token_revoke').then(function () { document.getElementById('fk-token-show').classList.add('d-none'); load(); });
  });
  document.getElementById('fk-log-refresh').addEventListener('click', loadLog);

  load().then(function () { return refresh(false); }).then(loadShows).then(loadLog);
})();
</script>
