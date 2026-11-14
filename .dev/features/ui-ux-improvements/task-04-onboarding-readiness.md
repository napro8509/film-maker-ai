# T4 — Onboarding có hướng dẫn + kiểm tra sẵn sàng trước khi render

**Ưu tiên:** P1 · **Tác động:** Cao · **Công sức:** Trung bình

## Hiện trạng (đã kiểm chứng — chú ý hợp đồng API)

Người dùng mới phải vượt nhiều rào; UI chưa gộp thành một checklist "sẵn sàng hay chưa":

- Cài đặt 2 pha + Playwright/Chromium (~200MB) dễ bỏ sót (README).
- `filmmaker check` (CLI) báo thiếu gflow/đăng nhập/ffmpeg; web có chip `#envchips` từ `/api/env`
  (`server.py:741-763`) nhưng **không gộp** thành một checklist có nút sửa tại chỗ.
- **Credit:** có 2 endpoint khác nhau — `GET /api/credits` (`server.py:801-803`) trả **số dư cache tức
  thì**; `POST /api/env/credits` (`server.py:787-799`) hỏi Flow **thật nhưng chậm** (mở trình duyệt, ~30–60s)
  và cập nhật cache. Hiện UI không chủ động kiểm tra trước render → render với số dư 0 thì **render giữa
  chừng mới lỗi**.
- **`/estimate` chưa cộng sẵn số credit ước tính** (`server.py:1727-1752`, chỉ trả danh sách cảnh). Dữ liệu
  giá credit **có tồn tại** trong codebase (`filmmaker/models.yaml`, `model_meta.py` `credits()`/`price_unit()`,
  `GET /api/models` `server.py:826-845`, `record_spend` `server.py:1970`), nhưng tính theo từng cảnh phụ
  thuộc model/giây/số clip và một vài model mới để `price: null`, nên **`/estimate` hiện không tự quy ra
  "cần ~Y credit"**. Phạm vi T4 cố ý **không** tự tính tổng credit — chỉ dùng **số cảnh** từ `/estimate` và
  **số dư cache** từ `/api/credits` để cảnh báo "số dư thấp/0 trước khi render".
- **Model QC (whisper):** `POST /api/qc/model` (`server.py:2184-2195`) tải model, nhưng **yêu cầu đã cài
  `whisper-cli` trước**; thiếu binary thì trả **HTTP 400** "chạy `brew install whisper-cpp`"
  (`server.py:2187-2188`). Nên **không auto-download vượt qua được bước brew** — phải cài binary trước.

## Mục tiêu

(1) Một checklist onboarding gộp mọi điều kiện với nút sửa tại chỗ. (2) Cảnh báo "có thể không đủ điều
kiện" **trước** khi tốn tiền, bằng số dư **cache** + kiểm tra auth — **không** bịa con số credit cần.

## Giải pháp đề xuất

1. **Thẻ "Sẵn sàng làm phim"** (hiện khi có mục chưa đạt), gộp từ `/api/env` + auth:
   - gflow cài chưa · Đăng nhập Google chưa (nút gọi `/api/env/login` `server.py:778-785`) · ffmpeg/ffprobe ·
     Claude (API key hoặc Claude Code) · (tuỳ chọn) model QC.
   - Mỗi mục: ✓/✗ + 1 nút hành động hoặc link. Đạt hết thì thẻ tự ẩn.
2. **Pre-flight trước render (nhẹ, dùng cache):** khi bấm Render/Autopilot:
   - Lấy **số dư cache** `GET /api/credits` (tức thì). Nếu số dư = 0 / rất thấp / chưa có → modal cảnh báo
     "Số dư credit đang là X (số dư gần nhất). Render sẽ tốn credit." với nút *Kiểm tra số dư ngay* (gọi
     `POST /api/env/credits` kèm loading), *Vẫn render*, *Huỷ*. **Không** so với con số "cần Y" (không có dữ liệu).
   - Nếu **chưa đăng nhập** → chặn sớm, mở luồng đăng nhập thay vì để job chạy rồi fail.
3. **Model QC đúng thực tế:** nếu thiếu `whisper-cli`, hiện **bước cài `whisper-cpp`** (lệnh brew, hoặc
   link) rồi mới cho bấm "Tải model" (`/api/qc/model`); không hứa tự tải nếu thiếu binary (sẽ 400).

## Tiêu chí chấp nhận

- [ ] Thẻ sẵn sàng hiện đúng khi thiếu điều kiện, tự ẩn khi đủ; mỗi mục có nút/giải pháp tại chỗ.
- [ ] Pre-flight dùng **số dư cache** `GET /api/credits`; cảnh báo khi số dư 0/thấp/chưa có, cho *Kiểm tra ngay* (`/api/env/credits`)/ *Vẫn render* / *Huỷ*. Không hiển thị "cần ~Y credit".
- [ ] Bấm Render khi chưa đăng nhập → chặn trước, mở luồng đăng nhập (không để job fail giữa chừng).
- [ ] QC thiếu `whisper-cli` → hướng dẫn cài `whisper-cpp` trước, rồi mới tải model; không rơi vào lỗi 400 khó hiểu.
- [ ] Không thay đổi logic trừ credit; chỉ **đọc** số dư cache/auth để cảnh báo.

## File cần sửa

- `filmmaker/web/static/app.js` — thẻ readiness; bước pre-flight (số dư cache) trong `runAction`/`openAutopilot` (`app.js:2462-2484`, quanh `renderHeader` `910`).
- `filmmaker/web/static/app.css` — style checklist.
- (Không cần endpoint mới: dùng `/api/env`, `/api/credits`, `/api/env/credits`, `/api/env/login` sẵn có.)

## Rủi ro / lưu ý

- `POST /api/env/credits` chậm (~30–60s, mở trình duyệt) → **mặc định dùng cache**, chỉ gọi khi người dùng bấm *Kiểm tra ngay*, có loading + đường "bỏ qua".
- **Không** bịa số credit cần từ `/estimate`; **không** hứa auto-download model QC khi thiếu `whisper-cli`.
- Không tự render/trừ credit; chỉ cảnh báo.
