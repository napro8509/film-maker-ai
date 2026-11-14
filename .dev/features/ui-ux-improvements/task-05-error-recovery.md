# T5 — Hoàn thiện khôi phục lỗi (những chỗ còn thiếu, không làm lại thứ đã có)

**Ưu tiên:** P1 · **Tác động:** Trung bình–Cao · **Công sức:** Thấp–Trung bình

## Hiện trạng (đã kiểm chứng — nhiều phần đã làm rồi)

Không đề xuất làm lại các luồng **đã triển khai**:

- **Sửa model đã ngừng — 1 chạm đã có:** banner `renderModelWarning` (`app.js:995-1004`) có nút gọi thẳng
  `openModelChange()`; `openModelChange` (`app.js:2820-2929`) có xem trước + áp dụng (`model-change/preview`
  → `model-change`); `openBlockedModelChange` (`app.js:2970`) và `openDurationFix` (`app.js:2978-3090`) đã
  nối lỗi "model ngừng" và "thời lượng không hợp lệ" thành nút sửa.
- **Lỗi validate ĐÃ được dịch một phần:** `prettyError` (`app.js:1006-1011`) chuyển một số lỗi Pydantic sang
  tiếng Việt theo cảnh (vd. "Cảnh s01: không có nhân vật…", "chưa điền", "kiểu Ảnh → Video cần chọn ảnh").
- Lỗi i2v có banner riêng với hành động (`renderErrors` `app.js:964-993`).

## Vấn đề còn lại (thu hẹp đúng chỗ thiếu)

1. **`prettyError` chưa phủ hết:** nhiều message Pydantic khác (vd. `model: value is not a valid
   enumeration member`, lỗi độ dài ngôn ngữ) vẫn lọt ra dạng thô. Cần map thêm và **gắn link cuộn tới đúng
   cảnh/field**, điều `prettyError` hiện chưa làm (chỉ đổi chữ).
2. **Mâu thuẫn `maxlength` ô ngôn ngữ:** `langPicker` input đặt `maxlength: 80` (`app.js:197`) nhưng
   `cleanLang` loại mọi tên **> 40 ký tự** (`app.js:184-190`, trùng rule backend). Người dùng gõ 41–80 ký
   tự rồi mới bị báo lỗi khi lưu. Nên hạ `maxlength` về 40 + chữ gợi ý để chặn từ đầu (quick win).
3. **Cảnh bị ngắt chưa gom một chỗ:** các cảnh `submitting/stale/invalid/unverified` hiện mỗi thẻ tự xử lý
   rải rác (`renderSceneMedia` `app.js:1367-1393`, `renderSceneActions` `app.js:1443`); đã có endpoint
   `adopt-clip` (`server.py:1753-1786`) và dữ liệu `unverified_clip`/`adopt` từ `/estimate`
   (`server.py:1745,1743`) nhưng **chưa có một khu "Khôi phục cảnh bị ngắt"** tập trung với 3 nút rõ ràng.
4. **Autopilot chưa surface điều kiện lỗi trước khi chạy:** `openAutopilot` (`app.js:2462-2484`) post thẳng
   tới `/autopilot` (`app.js:2479-2481`) mà không nhắc trước nếu dự án đang có model ngừng / cảnh lỗi —
   banner cảnh báo chỉ hiện ở ngoài, không trong modal Autopilot.

## Mục tiêu

Lấp đúng 4 khoảng trống trên — không dựng lại luồng model-change/duration-fix đã có.

## Giải pháp đề xuất

1. **Mở rộng `prettyError`** (`app.js:1006-1011`): thêm map cho lỗi enum model, lỗi độ dài, lỗi field còn
   lại; mỗi dòng lỗi gắn link cuộn tới `#scene-<i>` tương ứng.
2. **Hạ `maxlength` ô ngôn ngữ về 40** (`app.js:197`) + chữ gợi ý "tối đa 40 ký tự" — khớp `cleanLang`/backend.
3. **Khu "Khôi phục cảnh bị ngắt":** gom cảnh `submitting/stale/invalid/unverified` vào một thẻ, mỗi cảnh 3
   nút: **Kiểm tra trên Flow** · **Dùng clip này** (`adopt-clip`, dùng `adopt_lines`/`adopt` từ `/estimate`) ·
   **Render lại**. Tái dùng dữ liệu `/estimate` thay vì thêm backend.
4. **Pre-check trong Autopilot:** trong `openAutopilot` (`app.js:2462-2484`), nếu `S.cur.model_warning` hoặc
   `S.cur.errors` có nội dung thì hiện cảnh báo + nút "Sửa model trước" (gọi `openModelChange()` đã có) ngay
   trong modal trước khi bắt đầu.

## Tiêu chí chấp nhận

- [ ] `prettyError` phủ thêm lỗi enum model/độ dài; mỗi dòng lỗi click cuộn tới đúng cảnh.
- [ ] Ô ngôn ngữ `maxlength=40` + gợi ý; không còn báo "quá 40 ký tự" bất ngờ khi lưu.
- [ ] Có khu "Khôi phục cảnh bị ngắt" với 3 nút hoạt động thật (adopt/kiểm tra/render lại), dùng dữ liệu `/estimate` + `adopt-clip` sẵn có.
- [ ] Modal Autopilot cảnh báo + cho sửa model khi dự án có `model_warning`/`errors`.
- [ ] **Không** dựng lại `openModelChange`/`openDurationFix` đã có; không đổi hành vi render/credit.

## File cần sửa

- `filmmaker/web/static/app.js` — `prettyError` (`1006-1011`); `langPicker` maxlength (`197`); khu khôi phục (quanh `renderScenes` `1023`); pre-check `openAutopilot` (`2462-2484`).
- `filmmaker/web/static/app.css` — style khu "Khôi phục cảnh bị ngắt".
- (Không cần sửa backend: dùng `/estimate`, `adopt-clip`, `model-change` sẵn có.)

## Rủi ro / lưu ý

- `adopt-clip`/`model-change` tác động thật lên state/file → giữ confirm rõ và test regression.
- Giữ nguyên an toàn credit khi render lại cảnh bị ngắt (không tự gửi khi chưa xác nhận).
