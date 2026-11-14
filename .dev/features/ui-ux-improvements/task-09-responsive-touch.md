# T9 — Responsive & cảm ứng (mobile, board, modal co giãn)

**Ưu tiên:** P2 · **Tác động:** Trung bình · **Công sức:** Trung bình

## Hiện trạng (đã kiểm chứng — tránh khẳng định sai như lần trước)

Nền responsive **đã khá tốt**, không thiếu như bản nháp trước nói:

- **`grid3` ĐÃ có rule mobile:** `.grid3` → 2 cột ở ≤900px (`app.css:474`), và `.grid2, .grid3, .char-row`
  → 1 cột ở ≤720px (`app.css:491`). Không tràn ngang như đã nói nhầm.
- **Board ĐÃ dùng Pointer Events cho pan và kéo thẻ:** `vp.addEventListener("pointerdown/move/up", …)`
  (`board.js:412-414`) + `touch-action: none` trên viewport (`board.css:57`) + `setPointerCapture`
  khi kéo thẻ (`board.js:1058-1080`). Pan và kéo sắp xếp thẻ **chạm được** trên tablet/điện thoại.
- **Zoom board bằng `wheel`** (`board.js:416`) — có trên trackpad/chuột, nhưng **chưa có pinch 2 ngón**.
- **Modal có co:** `.modal` rộng `min(560px, 100%)`, `.modal.wide` `min(760px, 100%)` (`app.css:421-422`)
  → tự rút về 100% ở màn hẹp; field bên trong dùng `grid2/grid3` đã sập 1 cột ở ≤720px.
- **Sidebar đã thành drawer** ở ≤720px (`app.css:479-489`), có scrim.
- Board **đã** gợi ý lối thay thế cho thao tác kéo mũi tên trên cảm ứng: banner "Trên màn hình cảm ứng
  hãy sửa nhân vật của cảnh ở tab Cảnh" (`board.js:500-503`).

## Vấn đề còn lại (thu hẹp đúng chỗ thiếu)

1. **Kéo tạo mũi tên liên kết nhân vật vẫn cần chuột/trackpad** (`board.js:500-503` nêu rõ), chưa có cách
   làm hẳn bằng chạm **ngay trên Bảng** — hiện chỉ chỉ sang tab Cảnh. Thiếu lối "chọn thẻ → nút Liên kết với…".
2. **Chưa có pinch-to-zoom** trên board (chỉ có `wheel`); trên thiết bị cảm ứng thuần (không trackpad)
   khó phóng to/thu nhỏ sơ đồ.
3. **Mục tiêu chạm** của một số nút icon nhỏ (`.btn.icon.sm`, ví dụ công cụ thoại `app.js:1147-1148`) có
   thể dưới ~40px trên mobile → khó bấm chính xác.
4. Cần rà lại các lưới **không dùng class `grid2/grid3`** (ví dụ `.row` với `flexWrap:nowrap` ở Final
   `app.js:2123`) xem có tràn ở màn hẹp không.

## Mục tiêu

Giữ các màn chính dùng tốt ở màn hẹp (đã gần đạt) và **lấp đúng 3 khoảng trống cảm ứng còn lại**: liên
kết nhân vật không cần kéo trên Bảng, pinch-zoom, và kích thước chạm.

## Giải pháp đề xuất

1. **Liên kết nhân vật không cần kéo (trên Bảng):** chọn một thẻ cảnh/nhân vật → nút "Liên kết với…" mở
   danh sách để nối, thay cho việc bắt buộc kéo mũi tên. Bổ sung cho (không thay) banner gợi ý `board.js:500-503`.
2. **Pinch-to-zoom:** thêm xử lý 2 pointer trong các handler pointer sẵn có của board (`board.js:412-414`)
   để phóng to/thu nhỏ bằng hai ngón, dùng lại hàm set view của `wheel` (`bOnWheel`).
3. **Kích thước chạm ≥40px** cho nút hành động chính trên mobile (media query tăng padding/min-size cho
   `.btn` ở ≤720px), ưu tiên nút icon trong thẻ cảnh và jobbar.
4. **Rà lưới flex thủ công** còn lại (vd. `app.js:2123`) cho sập/ wrap ở ≤720px.

## Tiêu chí chấp nhận

- [ ] Trên Bảng, liên kết một nhân vật vào cảnh làm được **không cần kéo** (chọn + nút Liên kết với…).
- [ ] Board phóng to/thu nhỏ được bằng pinch 2 ngón trên thiết bị cảm ứng.
- [ ] Nút hành động chính đạt kích thước chạm tối thiểu (~40px) ở ≤720px.
- [ ] Rà soát xác nhận không còn lưới nào tràn ngang ở ≤720px (gồm các `.row` flex thủ công), test trình duyệt thu nhỏ.

## File cần sửa

- `filmmaker/web/static/board.js` — pinch-zoom trong handler pointer (`412-414`); lối "Liên kết với…" không cần kéo.
- `filmmaker/web/static/app.css`, `board.css` — kích thước chạm ≥40px ở mobile; rà lưới flex thủ công.

## Rủi ro / lưu ý

- **Không** khẳng định lại rằng grid3 thiếu rule mobile hay board không dùng pointer events — đều đã có.
- Board là thành phần phức tạp (layout engine, SVG, pan/zoom) → thêm pinch dễ hồi quy pan hiện tại; tách nhỏ và test trên desktop.
- App chính là desktop/native macOS; mobile là "dùng được" chứ không cần hoàn hảo.
