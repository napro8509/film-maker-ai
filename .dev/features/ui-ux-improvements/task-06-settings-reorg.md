# T6 — Sắp xếp lại modal Cài đặt

**Ưu tiên:** P1 · **Tác động:** Trung bình · **Công sức:** Thấp–Trung bình (quick win)

## Hiện trạng (đã kiểm chứng — không báo sai bố cục)

`openSettings()` (`app.js:3763-3906`, ~140 dòng) mở một modal `wide` (`app.css:422`) gồm **7 section** nối
tiếp trong một trang cuộn: Google Flow, An toàn khi render, Claude AI, Backup, AI QC, Social (FB/YT), Giao diện.

Lưu ý những điểm **đã** dùng lưới (tránh nói nhầm "xếp dọc một cột"):

- Các field ngắn **đã** bố trí `grid2`/`grid3`: cửa sổ Flow + giới hạn credit (`grid2`, `app.js:3819`),
  nghỉ min/max/retry (`grid3`, `app.js:3823`), Facebook Page ID + Token (`grid2`, `app.js:3874`), và **3
  field mật khẩu YouTube** (Client ID / Secret / Refresh Token) ở `grid3` (`app.js:3877-3880`). Chúng
  **không** xếp dọc một cột trên màn rộng.
- Mỗi `grid2/grid3` đã tự sập 1 cột ở ≤720px (`app.css:491`).

## Vấn đề còn lại (thu hẹp đúng chỗ thiếu)

1. **Một trang cuộn dài, không điều hướng nhóm:** 7 section nằm liền nhau; muốn sửa Theme hay QC phải cuộn
   qua tất cả. Không có sidebar/segmented/tab để nhảy nhóm.
2. **Khối Social khó đọc:** phần Facebook/YouTube bọc trong closure IIFE (`app.js:3854-3883`), trộn input +
   checkbox + help; chưa hiện rõ trạng thái "đã kết nối / chưa" cho từng nền tảng.
3. **Thiếu mô tả ngắn cho vài field** (một số đã có `.help`/`.sub`, một số chưa) → người dùng phải đoán.
4. (Nhỏ) chưa có ô tìm nhanh thiết lập.

## Mục tiêu

Tìm và chỉnh một thiết lập nhanh, không phải cuộn qua mọi thứ — **giữ nguyên** các lưới field đã tốt.

## Giải pháp đề xuất

1. **Điều hướng theo nhóm trong modal:** cột trái là danh mục (Flow · An toàn · Claude · Backup · QC ·
   Social · Giao diện), phải là nội dung nhóm đang chọn — hoặc segmented/tab. Giảm "một trang dài".
2. **Social gọn + trạng thái kết nối:** gom FB/YT thành khối rõ với badge "đã kết nối / chưa" + nút kết
   nối, thay cho closure khó đọc (`app.js:3854-3883`). Giữ nguyên `grid2/grid3` cho các field token.
3. **Mô tả ngắn cho field còn thiếu** (bổ sung `.help`/`.sub`).
4. (Tuỳ chọn) ô **tìm nhanh thiết lập** lọc theo nhãn.

## Tiêu chí chấp nhận

- [ ] Modal Cài đặt chia nhóm điều hướng được (sidebar/segmented/tab); chọn nhóm chỉ hiện field nhóm đó.
- [ ] Khối Facebook/YouTube hiện trạng thái kết nối rõ ràng; vẫn giữ layout `grid2/grid3` cho token.
- [ ] Mọi field vẫn lưu đúng qua `PUT /api/settings` (`server.py:862-902`) — không đổi tên khoá, không mất giá trị (regression).
- [ ] Field mật khẩu vẫn giữ hành vi "đã lưu" (placeholder hint) và cờ xoá key (vd. `clearKey`, `app.js:3838`).

## File cần sửa

- `filmmaker/web/static/app.js` — `openSettings()` (`3763-3906`): thêm điều hướng nhóm, nắn khối Social; giữ nguyên binding/khoá và các `grid2/grid3` sẵn có.
- `filmmaker/web/static/app.css` — layout nhóm (sidebar/segmented) cho modal.

## Rủi ro / lưu ý

- **Không** báo sai rằng field mật khẩu đang xếp dọc 1 cột — chúng đã ở `grid3` (`app.js:3877-3880`).
- **Không đổi tên/khoá payload settings** (map chính xác sang `/api/settings`). Rủi ro chính là regression lưu/đọc toàn bộ field.
