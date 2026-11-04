# dashboards/ — JSON export của dashboard

Thư mục này **chưa có file JSON**: định dạng export/import dashboard của ThingsBoard thay đổi giữa các bản 3.x
(cấu trúc `configuration.widgets`, `entityAliases`, `timewindow`, `states`), nên không chế tạo file bằng tay để tránh
import lỗi. Dựng dashboard theo `../SETUP.md` mục 7 (bảng liệt kê từng widget, loại, data key, alias), rồi export
vào đây để tái sử dụng/khôi phục.

## Export

Dashboards → chọn dashboard → nút **Export dashboard** (biểu tượng tải xuống trên thẻ dashboard, hoặc mở dashboard →
menu ⋮ → Export) → lưu:

- `sorter_control.json`   — "Sorter — Điều khiển" (Admin)
- `sorter_monitor.json`   — "Sorter — Giám sát" (Viewer)

Cũng export **widget bundle** "Sorter" (Widgets Library → bundle → Export) thành `sorter_widgets_bundle.json` để
không phải dán lại HTML/JS khi cài máy mới.

## Import (máy mới / cùng phiên bản TB)

1. Widgets Library → **Import widgets bundle** → `sorter_widgets_bundle.json` (làm trước để dashboard tìm thấy widget).
2. Dashboards → **+ → Import dashboard** → chọn file. Khi import, ThingsBoard hỏi **ánh xạ alias** (aliases chứa
   id thiết bị của máy cũ): chọn lại `sorter-pi-01` cho alias `Sorter`, `sorter-view` cho alias `Sorter view`.
3. Gán "Sorter — Giám sát" cho customer Viewer (SETUP.md 5b).

Import giữa các bản khác nhau (ví dụ export từ 3.7 import vào 3.9) thường được, nhưng widget hệ thống có thể đổi
tên/bundle; nếu widget hiện "not found", thay bằng widget tương đương theo bảng SETUP.md 7.2.

## Khi nào nên commit JSON

File export không chứa bí mật (chỉ id entity, cấu hình widget) nên commit được. Trước khi commit, kiểm tra không có
`accessToken` hay `credentialsValue` trong file (`grep -i token *.json`).
