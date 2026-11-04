/* T-06 — Defect image card: widget tùy chỉnh ThingsBoard (Widget type: Latest values).
 *
 * Cách dán: Widget Library → Add widget → Create new widget type → Latest values →
 *   tab HTML   : defect_image_card.html
 *   tab CSS    : (CSS bên dưới, phần "CSS" ở cuối file này hoặc defect_image_card.css nếu tách)
 *   tab JavaScript: nội dung file này
 * Data keys trong widget (datasource = alias "Sorter" hoặc "Sorter view"):
 *   defect_image (string base64 JPEG), ai_label, ai_conf, pkg_id, reason, defect_label (tùy chọn — nếu Pi gửi)
 *
 * API đối chiếu theo phiên bản ThingsBoard 3.x:
 *   self.ctx.data[i].dataKey.name  — tên key;  self.ctx.data[i].data = [[ts, value], ...] (latest values: 1 phần tử)
 *   self.ctx.$container            — jQuery wrapper của DOM widget; self.ctx.$scope — scope Angular
 *   self.onDataUpdated / self.onResize / self.onDestroy — vòng đời widget
 */
self.onInit = function () {
  var root = self.ctx.$container[0];
  self.ctx.$dc = {
    img: root.querySelector('#dc-img'),
    empty: root.querySelector('#dc-empty'),
    time: root.querySelector('#dc-time'),
    pkg: root.querySelector('#dc-pkg'),
    label: root.querySelector('#dc-label'),
    defect: root.querySelector('#dc-defect'),
    conf: root.querySelector('#dc-conf'),
    reason: root.querySelector('#dc-reason')
  };
  self.ctx.$dc.img.style.display = 'none';
  self.ctx.$dc.lastTs = 0;
};

/* Lấy giá trị mới nhất + ts của một data key; trả null nếu không có */
function latest(ctx, keyName) {
  for (var i = 0; i < ctx.data.length; i++) {
    var d = ctx.data[i];
    if (d.dataKey && d.dataKey.name === keyName && d.data && d.data.length) {
      var last = d.data[d.data.length - 1];
      return { ts: last[0], value: last[1] };
    }
  }
  return null;
}

function fmtTime(ts) {
  if (!ts) { return '—'; }
  var d = new Date(ts);
  var p = function (n) { return (n < 10 ? '0' : '') + n; };
  return p(d.getDate()) + '/' + p(d.getMonth() + 1) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}

self.onDataUpdated = function () {
  var el = self.ctx.$dc;
  if (!el) { return; }
  var img = latest(self.ctx, 'defect_image');
  if (img && typeof img.value === 'string' && img.value.length > 0) {
    var b64 = img.value;
    // Chấp nhận cả chuỗi đã có tiền tố data: lẫn chuỗi base64 thuần; không bao giờ chèn chuỗi vào innerHTML
    var src = b64.indexOf('data:') === 0 ? b64 : 'data:image/jpeg;base64,' + b64;
    if (img.ts !== el.lastTs) {
      el.img.src = src;
      el.lastTs = img.ts;
    }
    el.img.style.display = 'block';
    el.empty.style.display = 'none';
    el.time.textContent = fmtTime(img.ts);
  } else {
    el.img.style.display = 'none';
    el.empty.style.display = 'flex';
    el.time.textContent = '—';
  }
  var setText = function (node, v, suffix) {
    node.textContent = (v === null || v === undefined || v === '') ? '—' : String(v) + (suffix || '');
  };
  var pkg = latest(self.ctx, 'pkg_id');
  var label = latest(self.ctx, 'ai_label');
  var defect = latest(self.ctx, 'defect_label');
  var conf = latest(self.ctx, 'ai_conf');
  var reason = latest(self.ctx, 'reason');
  setText(el.pkg, pkg && pkg.value);
  setText(el.label, label && label.value);
  setText(el.defect, defect && defect.value);
  if (conf && conf.value !== null && conf.value !== undefined && conf.value !== '') {
    var c = Number(conf.value);
    el.conf.textContent = isNaN(c) ? String(conf.value) : (c <= 1 ? (c * 100).toFixed(1) : c.toFixed(1)) + ' %';
  } else {
    el.conf.textContent = '—';
  }
  setText(el.reason, reason && reason.value);
  // Màu nhãn: DEFECT đỏ, UNCERTAIN cam, OK xanh
  var lv = label && String(label.value || '').toUpperCase();
  el.label.className = 'v ' + (lv === 'DEFECT' ? 'bad' : lv === 'UNCERTAIN' ? 'warn' : lv === 'OK' ? 'good' : '');
  self.ctx.detectChanges();
};

self.onResize = function () {};

self.onDestroy = function () {
  self.ctx.$dc = null;
};
