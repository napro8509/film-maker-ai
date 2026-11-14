# T3 — Tìm kiếm / lọc / sắp xếp danh sách dự án

**Ưu tiên:** P0 · **Tác động:** Trung bình–Cao · **Công sức:** Thấp (quick win)

## Hiện trạng (đã kiểm chứng — chú ý hợp đồng API)

`renderSidebar()` (`app.js:825-852`) đổ **toàn bộ** danh sách dự án, **không có** ô tìm kiếm/lọc/sắp xếp.

Dữ liệu từ `GET /api/projects` (`list_projects` `server.py:906-916`, qua `summary()` `server.py:534-556`).
Mỗi dự án **bình thường** trả: `id, title, aspect, scenes, done, has_final, thumb, updated`. Hai điểm quan
trọng khi thiết kế bộ lọc:

- **Trường `error` CHỈ xuất hiện khi đọc dự án hỏng** (vd. YAML sai cú pháp) — nhánh `except` của
  `list_projects` (`server.py:913-915`). Với dự án mà `summary()` chạy được, **mọi lỗi validate storyboard
  bị nuốt** (`summary()` bọc `load_storyboard`/`is_done` trong `try/except: pass`, `server.py:538-543`) →
  trả `done=0` nhưng **không có `error`**. Vậy chip lọc "Có lỗi" thuần client dựa trên `error` **chỉ bắt
  được dự án YAML hỏng**, bỏ sót dự án lỗi validate/render.
- `done == scenes` **không** đủ để nói "Hoàn thành": dự án rỗng có `scenes=0, done=0` → `0==0` lọt nhầm vào
  "Hoàn thành". Phải thêm điều kiện `scenes > 0`.

## Mục tiêu

Tìm nhanh một dự án và sắp xếp danh sách theo nhu cầu, hoàn toàn ở client, **mà không hứa những bộ lọc API
không đỡ được**.

## Giải pháp đề xuất

1. **Ô tìm kiếm** đầu sidebar: lọc theo tiêu đề (không phân biệt hoa thường, bỏ dấu tiếng Việt). Hiện số kết quả.
2. **Sắp xếp**: Mới cập nhật (mặc định, theo `updated`) · Tên A→Z · Tiến độ (`done/scenes`). Lưu lựa chọn vào localStorage.
3. **Lọc nhanh (chip):** Tất cả · Đang làm (`scenes > 0 && done < scenes`) · Hoàn thành (`scenes > 0 && done == scenes`) ·
   **YAML lỗi** (`error` có giá trị — đặt đúng tên "YAML lỗi", **không** gọi chung là "Có lỗi" vì API chỉ
   đánh dấu dự án YAML hỏng). (Tuỳ chọn mở rộng sau: nếu muốn chip "Có lỗi" thật, cần backend surface lỗi
   validate trong `summary()` — ghi rõ là việc backend, ngoài phạm vi quick win này.)
4. **Empty state** khi không khớp: "Không có dự án nào khớp 'abc'" + nút xoá lọc.
5. Giữ nguyên chế độ rail thu gọn — khi thu gọn, ẩn ô tìm kiếm.

## Tiêu chí chấp nhận

- [ ] Gõ vào ô tìm kiếm lọc danh sách tức thời (bỏ dấu tiếng Việt vẫn khớp).
- [ ] 3 kiểu sắp xếp hoạt động đúng; lựa chọn được nhớ qua lần mở lại.
- [ ] Chip "Đang làm"/"Hoàn thành" dùng điều kiện có `scenes > 0` (dự án rỗng không rơi vào "Hoàn thành").
- [ ] Chip lỗi đặt tên "YAML lỗi" theo đúng dữ liệu API trả (`error` chỉ có khi đọc hỏng); không khẳng định bắt được mọi lỗi.
- [ ] Có empty state khi không khớp; nút xoá lọc đưa về "Tất cả". Không gọi thêm API (lọc/sắp xếp client-side).

## File cần sửa

- `filmmaker/web/static/app.js` — `renderSidebar()` (`825-852`): thêm thanh tìm/lọc/sắp xếp + state lọc.
- `filmmaker/web/static/app.css` — style ô tìm kiếm + chip lọc.
- (Tuỳ chọn, ngoài quick win) `filmmaker/web/server.py` — nếu muốn chip "Có lỗi" thật thì `summary()` cần trả cờ lỗi validate.

## Rủi ro / lưu ý

- Thuần client, rủi ro thấp; chỉ cần không phá chế độ drawer/rail hiện có.
- **Không** hứa chip "Có lỗi" bắt mọi dự án lỗi — API hiện chỉ đánh dấu dự án YAML hỏng.
