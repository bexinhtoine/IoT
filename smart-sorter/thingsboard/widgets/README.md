# Widget tùy chỉnh cho dashboard Sorter (T-06)

Hai widget tùy chỉnh, mỗi widget gồm 3 phần HTML / CSS / JavaScript dán vào Widget Library của ThingsBoard 3.x.

| Widget | Loại widget | File | Dùng ở |
|---|---|---|---|
| Defect image card — thẻ ảnh hàng lỗi mới nhất | **Latest values** | `defect_image_card.{html,css,js}` | Điều khiển (alias Sorter) và Giám sát (alias Sorter view) |
| Sorter control panel — khối điều khiển RPC | **Control widget** | `sorter_control_panel.{html,css,js}` | Chỉ dashboard Điều khiển |

## Cách dán vào Widget Library

1. Menu trái **Widgets Library** (3.5+: *Widgets library → Widget types*; 3.3/3.4: *Widgets Library → + → Create new widgets bundle* "Sorter" rồi vào bundle).
2. **+ Add widget → Create new widget type** → chọn loại ở bảng trên.
3. Tab **Resources / HTML / CSS / JavaScript**: dán lần lượt nội dung file `.html`, `.css`, `.js`.
   (Tên tab có thể là *HTML*, *CSS*, *JavaScript* nằm trong cùng một trang editor tùy phiên bản.)
4. Với **Latest values** (defect image card): trong *Widget Editor* phần **Datasource** mẫu thêm keys
   `defect_image, ai_label, ai_conf, pkg_id, reason` để xem trước; bấm **Run** để kiểm tra, rồi **Save** với tên
   `Defect image card`.
5. Với **Control widget**: Save với tên `Sorter control panel`. Trong Settings của widget không cần đổi gì;
   timeout 10 s được truyền thẳng trong JS (`RPC_TIMEOUT_MS`).
6. Trên dashboard: **Edit → Add widget → (bundle Sorter) → chọn widget** → Datasource: alias tương ứng
   (SETUP.md mục 7 liệt kê alias). Với control widget, *Target device* = alias **Sorter**.

## Ghi chú API cần đối chiếu theo phiên bản

- `self.ctx.controlApi.sendTwoWayCommand(method, params, timeout)` — tên và thứ tự đối số ổn định từ 3.0;
  3.4+ thêm `persistent, persistentPollingInterval, retries, additionalInfo, requestUUID` (tùy chọn). Trả về
  rxjs `Observable`, dùng `.subscribe({next, error})`.
- `self.ctx.currentUser` (AuthUser, có `email`) — tồn tại trong WidgetContext từ 3.x; JS có 2 fallback
  (`userService.getAuthUser()` và giải mã `jwt_token` trong localStorage) nếu phiên bản khác.
- `self.ctx.dialogs.confirm(title, message, cancelText, okText)` → `Observable<boolean>`; fallback `window.confirm`.
- `self.ctx.data[i].dataKey.name` / `self.ctx.data[i].data` — cấu trúc dữ liệu widget, ổn định trong 3.x.
- Lỗi RPC về `error:` của subscribe với `status` 504/408 (thiết bị không trả lời trong timeout), 403 (user không có
  quyền trên thiết bị — trường hợp Viewer), 400 (payload sai).

## Lưu ý bảo mật

- `requestedBy` do widget điền từ email người đăng nhập **chỉ để hiển thị** trong `rpc_audit`; ai cũng có thể giả
  giá trị này qua REST. Nguồn tin cậy là **Audit Logs** của ThingsBoard (menu Audit Logs, lọc RPC call).
- Widget không bao giờ dùng `innerHTML` với dữ liệu từ thiết bị (ảnh base64 gán vào `img.src`, text gán
  `textContent`) để tránh XSS từ telemetry.
- Nút DỪNG KHẨN CẤP không bị khóa khi lệnh khác đang chờ và không hỏi xác nhận (T-06). Phía Pi, `emergencyStop`
  được xử lý trước mọi thứ và không tính rate limit (S-05).
