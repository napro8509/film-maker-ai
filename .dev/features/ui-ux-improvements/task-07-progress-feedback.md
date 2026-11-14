# T7 — Phản hồi tiến độ: đếm ngược hàng chờ + gộp jobbar/overlay

**Ưu tiên:** P2 · **Tác động:** Trung bình · **Công sức:** Trung bình

## Vấn đề

Tiến độ job hiện ra ở **hai nơi tách rời**: thanh `jobbar` cố định dưới đáy (`renderJobbar` `app.js:3291-3354`)
và các overlay `.prog-ov` gắn lên từng ảnh/thẻ media qua `progress.js` (`progOn/progRunning/progQueued`).
Hai hệ (log-line cũ parse scene bằng regex `app.js:1045`, và `progress` object mới) chạy song song, dễ
lệch nhau. Khi Flow **xếp hàng** (đợi tối đa 3 phút để bắt đầu — README:467-472) UI chỉ ghi log "đang xếp
hàng", **không đếm ngược**, nên người dùng không biết đang kẹt hay vẫn chờ.

## Mục tiêu

Một nguồn sự thật cho tiến độ, và trạng thái hàng chờ có thời gian cụ thể để người dùng yên tâm.

## Giải pháp đề xuất

1. **Hợp nhất mô hình tiến độ:** chuẩn hoá về `progress` object có cấu trúc (`char:/kf:/vid:` theo item —
   đã có ở server `job.progress`), để jobbar và overlay đọc cùng nguồn; loại dần việc parse log bằng regex (`app.js:1045`).
2. **Hàng chờ có đếm ngược:** khi một cảnh ở trạng thái "xếp hàng", hiện "Đang chờ Flow bắt đầu… còn ~1m45s"
   (dựa mốc thời gian gửi + ngưỡng 3 phút). Khi vượt ngưỡng, đổi sang "Lâu hơn thường lệ — có thể thử lại".
3. **Jobbar rõ ngữ cảnh:** hiện bước hiện tại (vd. "Render cảnh 3/8"), nút Dừng (`/api/jobs/{jid}/stop`
   `server.py:2543-2551`) kèm lời nhắc "sẽ dừng sau khi xong cảnh đang chạy để tránh trừ credit 2 lần".
4. **Đồng bộ overlay ↔ jobbar:** cùng %/trạng thái; tránh cảnh "xong" ở overlay mà jobbar còn "đang chạy".

## Tiêu chí chấp nhận

- [ ] Jobbar và overlay lấy trạng thái từ cùng `progress` object; không còn lệch trạng thái giữa hai nơi.
- [ ] Cảnh đang xếp hàng hiện đếm ngược; vượt ngưỡng đổi thông điệp phù hợp.
- [ ] Jobbar hiện bước hiện tại dạng "x/y" và nút Dừng kèm giải thích.
- [ ] Test với job render nhiều cảnh (mock) cho thấy tiến độ tăng đúng và kết thúc đúng trạng thái.

## File cần sửa

- `filmmaker/web/static/progress.js` + `app.js` (`renderJobbar` `3291-3354`, polling `app.js:395`) — hợp nhất nguồn tiến độ.
- (Nếu cần) `filmmaker/web/server.py` job progress (`145-231`) để bổ sung mốc thời gian hàng chờ cho mỗi item.

## Rủi ro / lưu ý

- Chạm vào polling/tiến độ dễ gây nhấp nháy UI — cần cập nhật theo zone (`app.js:314-331`) thay vì render lại cả trang.
- Không đổi logic dừng job an toàn (đợi xong cảnh hiện tại).
