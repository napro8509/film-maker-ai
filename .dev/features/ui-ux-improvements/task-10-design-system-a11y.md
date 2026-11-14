# T10 — Chuẩn hoá hệ thiết kế & khả dụng (nút, trạng thái, a11y)

**Ưu tiên:** P2 · **Tác động:** Trung bình (lan toả) · **Công sức:** Thấp–Trung bình

## Hiện trạng (đã kiểm chứng — tránh báo "thiếu" thứ đã có)

Nền design system tốt (token `app.css:4-67`, ARIA 90+). Một số thứ bản nháp trước báo thiếu nhưng **đã có**:

- **Focus ring thẻ board ĐÃ có:** `.board-card:focus-visible { box-shadow: var(--focus); }` (`board.css:95`),
  thẻ có `tabindex="0"` + `role/aria-label` (`board.js:649`), điều hướng phím ở `bOnKey` (`board.js:1158-1170`).
- **Nhóm camera ĐÃ có nhãn nhìn thấy:** mỗi field camera bọc trong `field(label, …)` (`app.js:1086-1099`),
  cả cụm nằm trong `.camera-box` có tiêu đề "Chỉ đạo hình ảnh" (`app.js:1092-1093`). Thiếu là **ngữ nghĩa
  nhóm** (`<fieldset>/<legend>`), không phải thiếu nhãn.
- **Ảnh nhân vật/bối cảnh KHÔNG phải `<img>` ở `app.js:1383-1386`.** Hai dòng đó là `<img>` **keyframe của
  cảnh** (`alt="keyframe"`, `app.js:1383`) và **ảnh bắt đầu i2v** (`alt="ảnh bắt đầu"`, `app.js:1386`) —
  thuộc media của cảnh. Ảnh chân dung nhân vật và bối cảnh dùng `div.char-img` với `background-image`
  (`app.js:1826` cho nhân vật, `app.js:1935` cho bối cảnh) — **không có thuộc tính `alt`** vì là ảnh nền CSS.

## Vấn đề còn lại (thu hẹp đúng chỗ thiếu)

- **Vị trí/kiểu nút chính chưa đồng nhất:** khi ở `.card-head`, khi inline trong `.row`; biến thể
  icon+text / chỉ icon / chỉ text dùng lẫn (so `renderStory` `app.js:1592+` với các khu khác). Người dùng phải "học lại" mỗi khu vực.
- **Trạng thái/điểm QC chủ yếu bằng màu:** badge điểm review `score ok/mid/bad` dựa màu là chính
  (`renderAutoReview` `app.js:2516`, `app.css:608`) → khó cho người mù màu; nên kèm icon/chữ.
- **Ảnh cảnh (keyframe/ảnh bắt đầu) có `alt` sơ sài:** `alt="keyframe"`, `alt="ảnh bắt đầu"`
  (`app.js:1383-1386`) — nên mô tả theo cảnh (vd. "Keyframe cảnh s01"). Ảnh nhân vật/bối cảnh là
  `background-image` (`app.js:1826`, `1935`) → cân nhắc thêm `role="img"` + `aria-label` mô tả.
- **Thiếu `<fieldset>/<legend>`** cho nhóm camera (`app.js:1092-1099`) và `langPicker` (`app.js:192-211`)
  tuy đã có nhãn từng field nhưng chưa nhóm ngữ nghĩa.
- **Chưa có skip-to-content** ở đầu trang (`index.html`).

## Mục tiêu

Một "bảng chữ cái" UI nhất quán (nút, badge trạng thái, khoảng cách) và lấp các khoảng trống a11y **thực
sự còn thiếu** (không phụ thuộc màu, alt/aria có nghĩa, nhóm ngữ nghĩa, skip link).

## Giải pháp đề xuất

1. **Quy ước dùng nút + helper thống nhất:** vị trí nút chính (vd. luôn phải trong `card-head`), biến thể
   icon/text, màu danger; rà các nơi lệch và nắn lại.
2. **Trạng thái không chỉ bằng màu:** mọi badge điểm/trạng thái kèm **icon + chữ** (ok/mid/bad), không chỉ
   màu (`app.css:608`, `app.js:2516`).
3. **Alt/aria có nghĩa:** `alt` ảnh cảnh theo mã cảnh (`app.js:1383-1386`); thêm `role="img"`+`aria-label`
   cho `div.char-img` nhân vật/bối cảnh (`app.js:1826`, `1935`).
4. **Nhóm ngữ nghĩa:** bọc nhóm camera (`app.js:1092-1099`) và langPicker (`app.js:192-211`) bằng
   `<fieldset>/<legend>` (giữ nhãn từng field sẵn có).
5. **Skip-to-content** ở `index.html`; giữ nguyên focus ring board (đã có `board.css:95`) — chỉ bổ sung nơi còn thiếu.
6. (Tuỳ chọn) trang "style guide" nội bộ ghi lại các lớp/biến thể để dev sau đồng bộ.

## Tiêu chí chấp nhận

- [ ] Nút chính nhất quán vị trí/biến thể across Story/Scenes/Final (rà danh sách nút).
- [ ] Mọi badge trạng thái/điểm có icon+chữ, không chỉ dựa màu (kiểm bằng mô phỏng mù màu).
- [ ] `alt` ảnh cảnh mô tả theo cảnh; `div.char-img` nhân vật/bối cảnh có `role="img"`+`aria-label`.
- [ ] Nhóm camera và langPicker có `<fieldset>/<legend>`.
- [ ] Có skip-to-content; focus ring board vẫn rõ (không hồi quy `board.css:95`).
- [ ] Không thay đổi hành vi dữ liệu; thuần trình bày + thuộc tính a11y.

## File cần sửa

- `filmmaker/web/static/app.css` — badge trạng thái (icon+chữ), skip link.
- `filmmaker/web/static/app.js` — nắn nút; alt ảnh cảnh (`1383-1386`); aria cho char-img (`1826`, `1935`); fieldset camera (`1092-1099`)/langPicker (`192-211`).
- `filmmaker/web/static/index.html` — skip-to-content.

## Rủi ro / lưu ý

- **Không** báo thiếu focus ring board hay thiếu nhãn camera — đều đã có (`board.css:95`, `app.js:1086-1099`).
- Thay đổi rải rác, rủi ro thấp nhưng dễ sót → làm kèm checklist rà toàn bộ nút/badge/ảnh.
