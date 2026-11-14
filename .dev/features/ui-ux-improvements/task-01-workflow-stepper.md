# T1 — Dẫn dắt luồng làm phim (workflow stepper + "việc tiếp theo")

**Ưu tiên:** P0 · **Tác động:** Cao · **Công sức:** Trung bình

## Hiện trạng (đã kiểm chứng)

- 5 tab ngang: *Phong cách & nhân vật · Bảng · Cảnh · Ghép phim & ảnh bìa · YAML* (`renderMain`
  `app.js:860-871`). Thứ tự tab **đã** theo quy trình (có comment "Tabs follow the workflow"), nhưng các
  tab **đứng ngang hàng**, không thể hiện mình đang ở đâu trong hành trình tuyến tính (phong cách & nhân
  vật → dựng cảnh → keyframe miễn phí → render tốn credit → ghép/phụ đề → xuất bản).
- `renderHeader` (`app.js:910-962`) có thanh tiến độ + vài nút (Autopilot/Keyframes/Render/Stitch) nhưng
  **không gắn nút vào trạng thái dự án** để gợi ý bước kế.
- Màn rỗng (`renderEmpty` `app.js:893-905`) **đã** có hướng dẫn 4 bước — nhưng chỉ hiện khi **chưa chọn dự
  án**; khi đã mở một dự án nửa chừng thì không còn chỉ dẫn "việc tiếp theo".
- `POST /api/projects/{pid}/estimate` (`server.py:1727-1752`) trả **danh sách cảnh sẽ render / bị chặn /
  adopt / keyframe mới / done** — nhưng **chưa cộng sẵn số credit ước tính trong response này**. (Lưu ý:
  dữ liệu giá credit theo clip/giây **có tồn tại** trong codebase — `filmmaker/models.yaml`,
  `model_meta.py` `credits()`/`price_unit()`, trả qua `GET /api/models` (`server.py:826-845`), và
  `record_spend(meta.credits(...))` ở `server.py:1970` trừ credit theo từng cảnh; chỉ một vài model mới để
  `price: null`. Phạm vi T1 cố ý **không** tự tính credit mà chỉ dùng **số cảnh** từ `/estimate`.)
  Số dư credit lấy từ cache `GET /api/credits` (`server.py:801-803`).

Hệ quả: với dự án đang làm dở, người dùng mới không rõ "đang ở bước nào" và "việc tiếp theo là gì"; dễ bỏ
sót bước (quên keyframe trước render, quên nhạc/phụ đề trước khi ghép).

## Mục tiêu

Cho người dùng (nhất là khi đang ở một dự án nửa chừng) luôn thấy **mình đang ở bước nào** và **việc tiếp
theo là gì**, mà không ép buộc thứ tự.

## Giải pháp đề xuất

1. **Thanh bước (stepper)** mảnh dưới header, 5 mốc: *Phong cách & nhân vật · Cảnh · Keyframe · Render ·
   Hoàn thiện*. Trạng thái mỗi mốc suy ra từ dữ liệu đã có (`S.cur`: số cảnh, số cảnh có keyframe/clip, đã
   ghép chưa) — tái dùng dữ liệu `renderHeader` (`app.js:910-962`) và `/estimate`.
   - Trạng thái: `chưa bắt đầu` / `đang làm` / `xong` / `cảnh báo` (vd. có cảnh dùng model ngừng — từ `S.cur.model_warning`).
   - Click một mốc = chuyển tới tab tương ứng.
2. **Thẻ "Việc tiếp theo"** ở đầu tab hiện tại: một câu + một nút chính, sinh theo trạng thái:
   - Chưa có cảnh → "Thêm cảnh đầu tiên" (→ Cảnh).
   - Có cảnh, chưa keyframe → "Tạo keyframe (miễn phí) cho **N** cảnh" (N = `new_keyframes` từ `/estimate`).
   - Keyframe xong → "Render **N** cảnh" (N = `renders` từ `/estimate`). **Không hứa số credit** (endpoint
     không trả); nếu muốn nhắc tiền, chỉ hiện **số dư cache** (`GET /api/credits`) kèm lời "sẽ tốn credit".
   - Render xong hết → "Ghép phim & thêm phụ đề" (→ Ghép phim).
3. Giữ Autopilot như lối tắt "làm hết", đặt cạnh stepper để rõ nó bỏ qua các bước thủ công.

## Tiêu chí chấp nhận

- [ ] Có stepper 5 bước hiển thị trạng thái đúng theo dữ liệu dự án (test dự án rỗng, nửa chừng, đã ghép).
- [ ] Thẻ "Việc tiếp theo" đổi nội dung + nút theo trạng thái; nút dẫn tới đúng tab/hành động.
- [ ] Nút trong thẻ next-action hiển thị **số cảnh** lấy từ `/estimate` (`renders`/`new_keyframes`) — **không** bịa số credit.
- [ ] (Nếu hiện tiền) chỉ dùng **số dư cache** `GET /api/credits`, không gọi luồng credit chậm mỗi lần render.
- [ ] Stepper dùng được trên viewport hẹp; không ép thứ tự (vẫn click tự do mọi tab).

## File cần sửa

- `filmmaker/web/static/app.js` — thêm `renderStepper()` + `renderNextAction()`, gọi trong `renderMain`/`renderHeader` (`854-962`); đọc `/estimate` (số cảnh) và `/api/credits` (số dư).
- `filmmaker/web/static/app.css` — style stepper + next-action card (token sẵn có).
- (Không cần endpoint mới.)

## Rủi ro / lưu ý

- `/estimate` **không** có credit → tuyệt đối không hiển thị "~X credit" như thể lấy từ đó.
- Suy ra trạng thái bước phải khớp logic hiện có (`pipe.is_done`/`done` từ `/estimate`); tránh đếm sai.
- Đừng biến stepper thành wizard khoá bước — "gợi ý, không ép".
