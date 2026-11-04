/* T-04 / T-06 — Sorter control panel: gửi two-way RPC đúng whitelist 4.4 tới sorter-pi-01.
 *
 * Cách dán: Widget Library → Add widget → Create new widget type → Control widget →
 *   HTML: sorter_control_panel.html, CSS: sorter_control_panel.css, JavaScript: file này.
 *   Settings → đặt "RPC timeout" không dùng; timeout truyền trực tiếp 10000 ms ở dưới (T-04).
 * Trên dashboard: datasource = alias "Sorter" (sorter-pi-01). Dashboard Giám sát (Viewer) không có widget này,
 * và Viewer cũng không có quyền với sorter-pi-01 nên dù dán widget cũng không gửi RPC được (T-08).
 *
 * API đối chiếu theo phiên bản ThingsBoard 3.x:
 *   self.ctx.controlApi.sendTwoWayCommand(method, params, timeout, persistent?, retries?, ...)
 *       -> Observable (rxjs); subscribe({ next, error }). Ở 3.4+ đối số thêm (persistent, ...) là tùy chọn.
 *   self.ctx.currentUser            -> AuthUser {email, firstName, lastName, authority, ...}. Nếu phiên bản không có,
 *       fallback self.ctx.$injector.get(self.ctx.servicesMap.get('userService')).getAuthUser() hoặc localStorage jwt.
 *   self.ctx.dialogs.confirm(title, message, cancelText, okText) -> Observable<boolean>; fallback window.confirm.
 * Người gửi (requestedBy) chỉ để hiển thị trong rpc_audit; nguồn tin cậy là Audit Logs của ThingsBoard (S-05).
 */
var RPC_TIMEOUT_MS = 10000; // T-04

self.onInit = function () {
  var root = self.ctx.$container[0];
  var q = function (id) { return root.querySelector('#' + id); };
  var st = {
    result: q('scp-result'), title: q('scp-result-title'), body: q('scp-result-body'),
    speed: q('scp-speed'), user: q('scp-user'), busy: false,
    buttons: root.querySelectorAll('button')
  };
  self.ctx.$scp = st;
  st.userEmail = currentUserEmail();
  st.user.textContent = st.userEmail ? 'requestedBy: ' + st.userEmail : '';

  // DỪNG KHẨN CẤP: gửi ngay, không hỏi, không bị khóa bởi lệnh khác đang chờ
  q('scp-estop').addEventListener('click', function () { send('emergencyStop', {}, { force: true }); });
  q('scp-start').addEventListener('click', function () {
    var v = parseInt(st.speed.value, 10);
    if (isNaN(v) || v < 30 || v > 100) { show('start', { ok: false, error: 'BAD_PARAMS', detail: 'speed phải trong 30–100' }, 'err'); return; }
    send('start', { speed: v });
  });
  q('scp-stop').addEventListener('click', function () { send('stop', {}); });
  q('scp-reset-estop').addEventListener('click', function () {
    confirmDialog('Reset E-stop', 'Đã kiểm tra khu vực băng chuyền an toàn? Lệnh resetEstop đưa ESP32 về IDLE.', function () { send('resetEstop', {}); });
  });
  q('scp-reset-counters').addEventListener('click', function () { send('resetCounters', {}); });
  q('scp-reset-codes').addEventListener('click', function () {
    confirmDialog('Reset mã đã dùng', 'Xóa toàn bộ bảng used_codes (chỉ cho demo, cần device.demo_mode = true). Tiếp tục?',
      function () { send('resetUsedCodes', { confirm: 'RESET' }); });
  });
  q('scp-ota').addEventListener('click', function () { send('otaCheck', {}); });
  q('scp-status').addEventListener('click', function () { send('getStatus', {}); });
};

/* Email người đang đăng nhập để điền requestedBy (chỉ hiển thị) */
function currentUserEmail() {
  try {
    if (self.ctx.currentUser && self.ctx.currentUser.email) { return self.ctx.currentUser.email; }
  } catch (e) { /* bỏ qua */ }
  try {
    var svc = self.ctx.$injector.get(self.ctx.servicesMap.get('userService'));
    var u = svc && svc.getAuthUser ? svc.getAuthUser() : null;
    if (u && u.email) { return u.email; }
  } catch (e) { /* phiên bản không có servicesMap */ }
  try {
    var jwt = window.localStorage.getItem('jwt_token');
    if (jwt) {
      var payload = JSON.parse(atob(jwt.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
      if (payload && payload.sub) { return payload.sub; }
    }
  } catch (e) { /* bỏ qua */ }
  return 'dashboard';
}

function confirmDialog(title, message, onOk) {
  try {
    if (self.ctx.dialogs && self.ctx.dialogs.confirm) {
      self.ctx.dialogs.confirm(title, message, 'Hủy', 'Đồng ý').subscribe(function (ok) { if (ok) { onOk(); } });
      return;
    }
  } catch (e) { /* fallback */ }
  if (window.confirm(title + '\n\n' + message)) { onOk(); }
}

function setBusy(busy) {
  var st = self.ctx.$scp;
  st.busy = busy;
  for (var i = 0; i < st.buttons.length; i++) {
    var b = st.buttons[i];
    if (b.id === 'scp-estop') { continue; } // E-stop không bao giờ bị khóa
    b.disabled = busy;
  }
}

function show(method, data, cls) {
  var st = self.ctx.$scp;
  if (!st) { return; }
  var now = new Date();
  var p = function (n) { return (n < 10 ? '0' : '') + n; };
  st.title.textContent = method + ' — ' + p(now.getHours()) + ':' + p(now.getMinutes()) + ':' + p(now.getSeconds());
  st.body.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
  st.result.className = 'scp-result ' + cls;
  try { self.ctx.detectChanges(); } catch (e) { /* bỏ qua */ }
}

/* Gửi two-way RPC; opts.force = không chờ lệnh trước (E-stop) */
function send(method, params, opts) {
  var st = self.ctx.$scp;
  opts = opts || {};
  if (st.busy && !opts.force) { return; }
  var p = Object.assign({}, params || {}, { requestedBy: st.userEmail });
  show(method, 'Đang gửi ' + JSON.stringify(p) + ' (timeout ' + RPC_TIMEOUT_MS / 1000 + ' s)…', 'pending');
  if (!opts.force) { setBusy(true); }
  var done = function () { if (!opts.force) { setBusy(false); } };
  var obs;
  try {
    obs = self.ctx.controlApi.sendTwoWayCommand(method, p, RPC_TIMEOUT_MS);
  } catch (e) {
    done(); show(method, { ok: false, error: 'WIDGET_ERROR', detail: String(e) }, 'err'); return;
  }
  obs.subscribe({
    next: function (resp) {
      done();
      var ok = resp && resp.ok === true;
      show(method, resp, ok ? 'ok' : 'err');
    },
    error: function (err) {
      done();
      var msg = (err && (err.message || err.statusText || err.error)) || 'không có phản hồi';
      var status = err && err.status;
      // 504/408: thiết bị không trả lời trong 10 s; 403: không có quyền (Viewer); 400: payload sai
      show(method, { ok: false, error: status === 504 || status === 408 ? 'DEVICE_TIMEOUT' : status === 403 ? 'FORBIDDEN' : 'RPC_ERROR',
                     status: status, detail: msg }, 'err');
    }
  });
}

self.onDataUpdated = function () {};
self.onResize = function () {};
self.onDestroy = function () { self.ctx.$scp = null; };
