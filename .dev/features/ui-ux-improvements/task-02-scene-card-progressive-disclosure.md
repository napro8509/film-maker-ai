# T2 — Gọn thẻ cảnh bằng tiết lộ dần

**Ưu tiên:** P0 · **Tác động:** Cao · **Công sức:** Trung bình

## Hiện trạng (đã kiểm chứng — một phần đã tiết lộ dần)

`renderScene()` (`app.js:1166-1235`, ~70 dòng, nhiều ternary lồng) dựng mỗi thẻ cảnh. Một số phần **đã**
nhóm/thu gọn, cần ghi nhận để không đề xuất trùng:

- **Camera đã gom nhóm + thu gọn sẵn:** `renderCamera` (`app.js:1077-1100`) nằm trong `.camera-box` có
  tiêu đề "Chỉ đạo hình ảnh" (`app.js:1092-1093`); 4 field chính ở `.grid2`, còn **5/9 field** (lấy nét,
  bố cục, ánh sáng, tốc độ, ghi chú — `CAMERA_MORE`) **đã ẩn trong `<details>`** (`app.js:1095-1099`).
  Mỗi field đều có nhãn qua `field()`.
- **Thoại đã có khối riêng:** `renderDirection` (`app.js:1117-1163`) trong `.camera-box.talk-box`.
- **Chế độ cơ bản vs nâng cao:** khi `isBasic(sb)` (`app.js:1196-1198`) thì cảnh dùng `renderShot` và **ẩn
  luôn** camera + thoại; chỉ chế độ nâng cao mới hiện `renderCamera`/`renderDirection`.
- **Phụ đề:** `subtitleFields(sb, sc, changed)` (gọi ở `app.js:1225`) thêm **3 ô vi/th/en (`grid3`)** vào
  mỗi thẻ cảnh — phần này **chưa** tiết lộ dần (xem thêm T8).
- Media + nút hành động đã tách helper: `renderSceneMedia` (`app.js:1367`), `renderSceneActions` (`app.js:1443`).

Vấn đề còn lại: ở **chế độ nâng cao**, thẻ vẫn rất dài — prompt, cả khối camera, cả khối thoại, bối cảnh,
nhân vật, độ dài/model, **3 ô phụ đề**, và preview prompt đều bung thẳng; không có lớp "chỉ hiện điều quan
trọng". Dự án 20–50 cảnh thành trang rất dài (không virtualize — `renderScenes` `app.js:1023`).

## Mục tiêu

Mỗi thẻ cảnh chỉ phô ra **điều quan trọng nhất** (media preview, prompt chính, độ dài, trạng thái, nút
hành động); phần nâng cao (**toàn khối** camera, thoại, model, bối cảnh, phụ đề) thu gọn và mở khi cần —
mở rộng tinh thần "tiết lộ dần" vốn đã có ở nhóm con camera ra **cả thẻ**.

## Giải pháp đề xuất

1. **Bố cục 2 lớp cho cả thẻ cảnh (ở chế độ nâng cao):**
   - *Lớp luôn hiện:* số cảnh, media preview, prompt chính, chế độ, độ dài, badge trạng thái, nút hành động.
   - *Lớp thu gọn (`<details>` cấp thẻ):* **Camera** (cả `.camera-box`, không chỉ 5 field phụ sẵn có),
     **Thoại** (`renderDirection`), **Nâng cao** (model, bối cảnh), **Phụ đề** (`subtitleFields`, phối hợp T8).
2. **Tóm tắt khi gập:** mỗi section gập hiện chip 1 dòng (vd. camera "Cận cảnh · lia phải", thoại "N câu").
3. **Giữ nguyên chế độ `isBasic(sb)`** (`app.js:1196`): cảnh cơ bản vẫn gọn như hiện tại, chỉ áp tiết lộ dần cho chế độ nâng cao.
4. **Ghi nhớ trạng thái gập theo cảnh** (có thể lưu trong `ui_prefs.py` như layout board) để không bung lại mỗi lần refresh zone.
5. (Tuỳ chọn) chế độ "danh sách gọn" (collapse-all) cho dự án >20 cảnh.

## Tiêu chí chấp nhận

- [ ] Ở chế độ nâng cao, thẻ cảnh mặc định chỉ hiện lớp quan trọng; camera/thoại/nâng cao/phụ đề thu gọn.
- [ ] Section gập có chip tóm tắt phản ánh đúng giá trị bên trong.
- [ ] Chế độ `isBasic(sb)` không đổi hành vi (vẫn ẩn camera/thoại, dùng `renderShot`).
- [ ] Mở/gập một cảnh không ảnh hưởng cảnh khác; trạng thái gập được nhớ qua render lại.
- [ ] Không đổi `name`/binding field → auto-save (`changed()`) và lưu dữ liệu vẫn đúng (regression 3 chế độ keyframe/t2v/i2v).

## File cần sửa

- `filmmaker/web/static/app.js` — thêm lớp gập cấp thẻ trong `renderScene` (`1166-1235`); tóm tắt chip; giữ `renderCamera`/`renderDirection`/`isBasic`/`subtitleFields` nguyên binding.
- `filmmaker/web/static/app.css` — style section gập + chip tóm tắt.
- `filmmaker/web/ui_prefs.py` — (tuỳ chọn) lưu trạng thái gập theo cảnh.

## Rủi ro / lưu ý

- Thẻ cảnh là nơi chỉnh nhiều nhất → không làm chậm auto-save, không đổi tên field. Chỉ đổi cách trình bày.
- Phối hợp với T8 cho phần phụ đề (tránh hai task cùng sửa `subtitleFields` lệch nhau).
