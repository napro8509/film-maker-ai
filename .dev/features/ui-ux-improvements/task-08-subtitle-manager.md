# T8 — Gọn phần phụ đề ở thẻ cảnh + nối nút Xuất bản (export)

**Ưu tiên:** P2 · **Tác động:** Trung bình · **Công sức:** Trung bình

## Hiện trạng (đã kiểm chứng — tránh nhầm như lần trước)

- **Tab Final KHÔNG có ô nhập phụ đề.** `renderFinal` (`app.js:2082-2166`) chỉ có:
  - `subTrackSwitch()` (`app.js:2038-2043`) — segmented VI/TH/EN để **chọn 1 ngôn ngữ** đang xem.
  - khu "Phụ đề" `narr-zone` (`app.js:2155-2166`) — danh sách **chỉ đọc** theo ngôn ngữ đang chọn, đã
    tự đánh dấu "(chưa dịch — tự dịch khi ghép)" cho câu còn thiếu.
  - trình phát gắn 3 track .srt, hiện đúng track đang chọn (`finalVideo` `app.js:2055-2080`).
  → Nghĩa là "chọn một ngôn ngữ để xem" và "đánh dấu câu chưa dịch" **đã có sẵn** ở Final.
- **Ô nhập phụ đề nằm ở thẻ cảnh, không phải Final.** `subtitleFields(sb, sc, onChange)` (`app.js:173-181`)
  render **3 ô `grid3` (vi/th/en) cho MỖI cảnh**, được gọi trong `renderScene` (`app.js:1225`) và trong
  modal sửa cảnh trên Bảng (`board.js:894`). Đây mới là nơi mật độ cao: dự án 20 cảnh × 3 ô = 60 ô ở tab Cảnh.
- Mô hình lưu: `narration` (track gốc) + `subtitles[code]` cho track khác; thiếu bản dịch thì fallback về
  narration (`subLine`/`setSubLine` `app.js:160-171`), khớp hành vi server (tự dịch khi ghép).
- **`POST /api/projects/{pid}/export`** (`server.py:1327-1345`, kèm `/export/download` `server.py:1347-1360`)
  đóng gói release (mp4 + 3 .srt + metadata) nhưng **chưa được nối vào web UI** — không có nút Xuất bản/đóng
  gói nào trong `app.js` gọi tới endpoint này.
- **Lưu ý hợp đồng API (tránh lỗi 404):** `post_export(pid, zip_archive=False)` mặc định `zip_archive=False`
  → chỉ xuất **thư mục** trên ổ đĩa, **không** tạo file `.zip`. Dù vậy response vẫn luôn trả
  `download_url = /api/projects/{pid}/export/download`. Endpoint tải về (`server.py:1347-1360`) chỉ phục vụ
  file `{slug}_release.zip`; nếu lúc POST không truyền `?zip_archive=true` thì nó **trả HTTP 404**
  ("Chưa có file nén phát hành (.zip)…"). ⇒ Nếu muốn người dùng tải được gói, UI **phải** gọi
  `POST /api/projects/{pid}/export?zip_archive=true` rồi mới dùng `download_url`.

## Vấn đề còn lại (thu hẹp đúng chỗ thiếu)

1. **Thẻ cảnh ở tab Cảnh hiện 3 cột phụ đề cùng lúc** (`subtitleFields` grid3, `app.js:177-181`) → dày và
   khó quét trên dự án nhiều cảnh. Khác với Final, ở đây **chưa** có cơ chế "chỉ sửa 1 ngôn ngữ".
2. **Không có chỉ số tiến độ dịch mỗi ngôn ngữ** ("đã dịch N/∑ cảnh") ở bất kỳ đâu — người dùng phải tự
   dò từng cảnh ở `narr-zone` để biết track nào còn thiếu nhiều.
3. **Không có nút Xuất bản/đóng gói trong web UI**: `/api/projects/{pid}/export` chỉ gọi được bằng tay;
   người dùng web không tải được gói release (mp4 + srt + metadata) trong một thao tác.

## Mục tiêu

Giảm mật độ phụ đề ở thẻ cảnh, cho người dùng thấy ngay track nào còn thiếu, và đưa khả năng đóng gói
release (`export`) đã có ở backend lên web UI.

## Giải pháp đề xuất

1. **Chọn ngôn ngữ đang sửa ở thẻ cảnh (đồng bộ với `subTrack()` của Final):** mặc định `subtitleFields`
   (`app.js:173-181`) chỉ hiện **một ô** theo `subTrack()` thay vì cả 3; thêm segmented nhỏ (tái dùng
   `subTrackSwitch`) để đổi track. Giữ nguyên `setSubLine`/mô hình `narration`+`subtitles[code]`. Có nút
   "Hiện cả 3" cho ai muốn đối chiếu.
2. **Chỉ số "đã dịch N/∑"** cho vi/th/en: tính client-side từ `scenes[].narration`/`subtitles[code]`
   (đã có `subLine` để biết câu nào là bản dịch thật vs fallback). Hiện cạnh `subTrackSwitch` ở Final
   (`app.js:2104`, `2158`) và đầu tab Cảnh.
3. **Nối nút "Xuất bản / đóng gói"** trong khu phim hoàn chỉnh của Final (`app.js:2100-2113`): gọi
   `POST /api/projects/{pid}/export?zip_archive=true` (bắt buộc có `zip_archive=true` để backend tạo file
   `.zip`) rồi hiện link `download_url` trả về — lúc này `/export/download` mới trả được file thay vì 404.
   Trước khi gói, nếu một track chưa dịch đủ thì hiện cảnh báo nhẹ (dựa chỉ số ở mục 2) — bản dịch thiếu
   vẫn được server tự dịch khi ghép, nên đây là cảnh báo chứ không chặn.

## Tiêu chí chấp nhận

- [ ] Ở thẻ cảnh, mặc định chỉ hiện ô phụ đề của ngôn ngữ đang chọn; đổi ngôn ngữ không mất nội dung đã nhập các track khác.
- [ ] Có chỉ số "đã dịch N/∑" cho từng ngôn ngữ ở Final (và tab Cảnh); đồng bộ với đánh dấu "(chưa dịch)" sẵn có ở `narr-zone`.
- [ ] Final có nút gọi `POST /api/projects/{pid}/export?zip_archive=true` và hiện link tải gói hoạt động (tải được `.zip`, không 404); cảnh báo nếu còn track thiếu nhiều.
- [ ] Lưu phụ đề vẫn đúng mô hình `narration`/`subtitles[code]` (`app.js:160-181`) — không mất dữ liệu, không đổi payload.

## File cần sửa

- `filmmaker/web/static/app.js` — `subtitleFields` (`173-181`) thêm chế độ 1-track; chỉ số dịch; nút export trong `renderFinal` (`2082-2166`).
- `filmmaker/web/static/board.js` — `subtitleFields` trong modal cảnh (`894`) hưởng cùng chế độ 1-track.
- `filmmaker/web/static/app.css` — layout chỉ số + nút export.
- (Không cần sửa backend: `POST /api/projects/{pid}/export` đã có — `server.py:1327-1345`. UI nhớ truyền `?zip_archive=true` để `/export/download` trả được file `.zip`, tránh HTTP 404 — `server.py:1347-1360`.)

## Rủi ro / lưu ý

- **Không** đề xuất lại "chọn 1 ngôn ngữ để xem" hay "đánh dấu câu chưa dịch" ở Final — đã có (`subTrackSwitch`, `narr-zone`).
- Mô hình dữ liệu phụ đề nhạy (narration vs subtitles[code]) → chỉ đổi cách trình bày/sửa, giữ nguyên cách lưu.
