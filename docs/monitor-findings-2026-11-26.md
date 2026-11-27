# Theo dõi sản xuất 6 phim Thái từ kho kịch bản — 26/11/2026

Bấm tạo trong app (Phim mới → Từ kho kịch bản → Tạo tự động toàn bộ), 10 cảnh, 9:16, thoại + phụ đề Thái,
bật cổng kiểm duyệt và in phụ đề.

| # | Kịch bản | Engine | Trạng thái |
|---|---|---|---|
| 1 | thai-hai-nguoi-me — แม่สองคน | Flow | xong 10/10 cảnh, 8/10 PASS (sau làm bù) |
| 2 | thai-hop-com-cua-me — ปิ่นโตของแม่ | Flow | xong 10/10 cảnh, 8/10 PASS |
| 3 | thai-ca-truc-dau-tien — แผลเป็นของพี่ | Flow | xong 10/10 cảnh, 8/10 PASS, ≈ 95 phút |
| 4 | thai-quan-mi-dem — ก๋วยเตี๋ยวเที่ยงคืน | Muse | có storyboard, DỪNG: Muse hết phiên (F17) |
| 5 | thai-ong-lao-ban-ve-so — ผ้าเช็ดหน้าผืนนั้น | Muse | có storyboard, DỪNG: Muse hết phiên (F17) |
| 6 | thai-chiec-tuk-tuk-cua-bo — พวงมาลัยของพ่อ | Muse | có storyboard, DỪNG: Muse hết phiên (F17) |

Trước khi chạy: credit Flow 5812; ổ đĩa còn 12 GB (đầy 98%); Muse đã đăng nhập.

## Phát hiện

| # | Mức | Vấn đề | Xử lý |
|---|---|---|---|
| F1 | Nghẽn | App chỉ nhận một việc một lúc: bấm tạo phim thứ hai bị từ chối “Đang có việc chạy”, nút “Tạo tự động” trong phim bị khoá. | ĐÃ SỬA: hàng đợi (`Jobs` trong server.py, nút “Hàng đợi: N”), test `tests/test_job_queue.py`. Từ 27/11: mọi loại việc đều xếp hàng và hàng đợi giữ qua lần tắt/mở app (F14, F31). |
| F2 | Sai thông tin | Hộp xác nhận ghi “TỐN CREDIT veo-lite ≈10 credit/clip” cả khi chọn Muse; gói Ultra thực ra 5 credit/clip. | ĐÃ SỬA: chữ theo engine đã chọn. |
| F3 | Nhỏ | `POST /api/env/auth` (nút “Google · kiểm tra”) trả “Could not verify the Flow session” trong khi `gflow credits` ngay trước đó đăng nhập được (5812 credit). | ĐÃ SỬA (27/11): khi bước kiểm tra riêng của gflow-cli lỗi (AssertionError bên trong gflow 0.101), app đọc số credit để xác nhận phiên (`/api/env/auth`); test `tests/test_auth_check_web.py`. |
| F4 | Rủi ro | Muse kiểm tra bằng trình duyệt ẩn (headless) báo “chưa đăng nhập”, mở cửa sổ thật thì vẫn đăng nhập. `filmmaker muse login` tự xác minh bằng headless nên có thể báo lỗi sai; đặt “cửa sổ Flow = headless” có thể làm Muse hỏng. Hiện đang đặt “show” nên không ảnh hưởng. | ĐÃ SỬA phần xác minh sau đăng nhập (dùng cửa sổ thật). |
| F5 | Rủi ro | Ổ đĩa đầy 98% (còn 12 GB). 6 phim cần khoảng 1–2 GB. | ĐÃ SỬA (27/11): app báo “Đĩa còn N GB” trên thanh trên và ghi cảnh báo vào log mỗi việc khi còn dưới 10 GB. Dọn ổ đĩa vẫn là việc của người dùng (27/11 còn 7,7 GB). |
| F6 | Cải tiến | Flow và Muse dùng hai profile trình duyệt riêng nhưng vẫn phải chạy nối tiếp; có thể chạy song song hai làn (cần khoá profile khi một bên mượn bên kia tạo ảnh dự phòng). | CHƯA LÀM (đề xuất thiết kế): cần thanh tiến trình hiển thị hai việc cùng lúc và khoá chéo profile Flow khi phim Muse phải tạo ảnh dự phòng bằng Flow. |
| F7 | Test | Toàn bộ test bản cuối: 1595 đạt, 2 lỗi cũng xảy ra trên code gốc (`test_autopilot_basic_format…` gọi API Anthropic thật với key giả; `test_stitch_translates_and_writes_three_tracks` quá giới hạn chờ 60 giây). | ĐÃ SỬA (27/11): `editor.ai_plan` đổi lỗi API Anthropic (key sai, sập, giới hạn) thành lỗi dự phòng ⇒ dựng theo luật thay vì hỏng phim; test QC không gọi API thật nữa; test phụ đề bỏ OCR. Các hàm chờ job trong test nhận trạng thái “queued” và chờ lâu hơn khi máy bận. |
| F8 | Chậm | Keyframe là khâu lâu nhất trên Flow: mỗi ảnh qua giao diện chat ≈ 2,2 phút, “2 ứng viên song song” thực ra chạy lần lượt (một profile trình duyệt) ⇒ ≈ 5 phút/cảnh, ≈ 50 phút/phim 10 cảnh; cả phim ước ≈ 2 giờ. | ĐÃ SỬA (27/11): khi ảnh đi qua một trình duyệt (Muse, giao diện chat Flow), ứng viên keyframe đầu đạt PASS ≥ 8/10 thì không làm ứng viên còn lại (log ghi “lần lượt”, không còn “song song”); test trong `tests/test_auto_qc_gates.py`. Chưa đo lại trên một phim thật. |
| F9 | Lỗi | Phim 1, s03: Flow tính nhân vật Flow thành nhiều ảnh, từ chối vì “tối đa 3 ảnh tham chiếu” (chưa trừ credit). App hiểu là lỗi nội dung và nhờ AI viết lại prompt, không giải quyết được nguyên nhân. | ĐÃ SỬA: gửi lại một lần chỉ kèm nhân vật chính (`pipeline._render_via_ui`), test `tests/test_ref_limit_retry.py`. Chờ khởi động lại app. |
| F10 | Môi trường | Khoảng 23:52, một iOS Simulator (“iPhone Duo”, iOS 27.2) được boot trên máy (không phải từ việc làm phim này): load lên ~900, swap gần 7 GB, API app trả lời chậm 5–60 giây. | Đã báo người dùng; không tự tắt tiến trình của người khác. |
| F11 | Lỗi | Phim 1, s07 (lúc máy quá tải): Flow trả lời “I will generate 1 video…” nhưng thẻ xin xác nhận không hiện trong 240 giây. App coi là lỗi nội dung, viết lại prompt, mất một trong 2 lượt viết lại. | ĐÃ SỬA trong `flow_ui.py` (có hiệu lực ngay): thành lỗi tạm thời, gửi lại đúng prompt cũ. Test `tests/test_flow_ui_no_offer.py`. |
| F12 | Chất lượng | Phim 1: s07, s08 (đêm mưa, khóc) bị “Audio generation failed”; mỗi lần viết lại làm cảnh bớt cảm xúc (s07 mất chi tiết môi run). | App tự xử lý được, không nghẽn. |
| F13 | Lỗi (mất cảnh) | Phim 1, s09: sau 2 lần “Audio generation failed”, lần thứ 3 Flow từ chối vì “model không hỗ trợ nhân vật Flow”. App tính là bị từ chối nội dung ⇒ bỏ cảnh, dựng phim thiếu s09, lần chạy lại cũng không gửi. | ĐÃ SỬA: gửi lại với ít ảnh nhân vật hơn rồi chỉ ảnh mở đầu; lỗi kiểu này không còn tính là lỗi nội dung. Sẽ chạy lại phim 1 để làm bù s09. |
| F14 | Nghẽn | Hàng đợi mới chỉ nhận “Tạo tự động”; “Render lại” một cảnh, tạo keyframe, ghép phim vẫn bị từ chối khi đang có phim chạy. | ĐÃ SỬA (27/11): mọi việc (render, keyframe, QC, ghép, ảnh, backup, phân tích, crawl…) đều xếp hàng; chỉ từ chối khi đúng việc đó đã chạy/chờ. Nút trên giao diện không còn bị khoá khi đang có việc; chỉ thao tác sửa file ngay của chính phim đang chạy mới chờ. |
| F15 | Chất lượng | Phim 1 xong 115 phút nhưng bản ghép chỉ 7/10 cảnh (s09 mất do F13; s02 sai người nói, s07 lộ mặt bị cổng QC loại), agent chấm cả phim 4/10 REVISE. Cổng QC loại clip nhưng không tự render lại (tuỳ chọn đó không có trên giao diện “Tạo tự động”). | ĐÃ SỬA (27/11): hộp “Tạo tự động” (cả phim mới) có ô “Tự render lại clip bị QC đánh FAIL (tối đa 3 clip)”, mặc định tắt. |
| F16 | Chất lượng | Phim 2, s02: Veo tự vẽ phụ đề Thái vô nghĩa lên hình dù prompt cấm chữ; cổng QC bắt đúng (đã xem khung hình). | Đã render lại (8/10, hết chữ trên hình); cổng QC bắt đúng nên không cần sửa code. |
| F17 | CHẶN | 03:45: phiên Muse hết hạn (trang muse.ai đòi “Log in”; 22:38 còn đăng nhập). Cả 3 phim Muse không chạy được cho tới khi người dùng đăng nhập lại. Không rõ nguyên nhân; nghi vấn chưa kiểm chứng: mỗi lần chạy app nạp lại cookie cũ lưu từ 17/11 (`muse-state.json`). | ĐÃ SỬA nguyên nhân khả dĩ (27/11): cookie `hatch_sess` của Muse sống ~30 ngày và được Muse gia hạn khi dùng; mỗi lần chạy app ghi đè bản cũ lưu lúc đăng nhập lên bản đã gia hạn. Nay chỉ nạp cookie còn thiếu và lưu lại phiên sau mỗi lần Muse trả kết quả; test trong `tests/test_muse_ui.py`. Chưa kiểm chứng qua nhiều ngày. |
| F18 | Nghẽn | App không kiểm tra đăng nhập Muse trước khi xếp phim Muse vào hàng (chỉ phát hiện sau khi viết xong storyboard) và không có nút đăng nhập Muse trong app, phải dùng Terminal. | ĐÃ SỬA (27/11): chip “Muse” trên thanh trên (đỏ khi chưa đăng nhập, bấm để mở cửa sổ đăng nhập ngay trong app), `/api/muse/status`, `/api/muse/login`; phim Muse bị từ chối ngay khi chắc chắn chưa đăng nhập, kèm nút “Đăng nhập Muse”; test `tests/test_muse_login_web.py`. |
| F19 | Giao diện | Nút “Thay ô Hành động bằng prompt AI gợi ý” dán một prompt đầy đủ (máy quay + câu thoại + cấm chữ) vào ô Hành động ⇒ lặp thoại/máy quay nếu bấm. | ĐÃ SỬA (27/11): QC chỉ đề xuất hành động; `qc.action_only` bỏ câu máy quay/thoại/âm thanh/cấm chữ; nút ghi vào ô Hành động của cảnh dạng I2V; test `tests/test_qc_action_only.py`. |
| F20 | Lỗi | Người nói được dàn “quay lưng” vẫn bị ghép câu “turns head toward frame left” ⇒ Veo cho quay mặt ra, hậu kiểm đánh FAIL (phim 1, s07). | ĐÃ SỬA trong `i2v_prompt.py`, test `tests/test_speaker_from_behind.py`. |
| F21 | Giao diện | Phim đã đủ clip: hộp “Tạo tự động” vẫn liệt kê cả quy trình và ghi “≈ 0–0 credit”. | ĐÃ SỬA (27/11): phim đủ clip ghi “không render thêm nên không tốn credit” và chỉ liệt kê việc còn lại. |
| F22 | Lỗi (phim giao) | Phụ đề Thái in lên hình bị cắt chữ ở hai mép: tiếng Thái không có dấu cách nên ffmpeg không xuống dòng. | ĐÃ SỬA: ngắt dòng theo từ vào bản `final.th.burn.srt` trước khi in (`assemble.py`), test `tests/test_subtitle_wrap.py`. Đã xuất lại 3 phim, kiểm tra bằng khung hình. |
| F23 | Lỗi | “Xuất lại theo bản này” ở tab Ghép phim bỏ mất phụ đề: lựa chọn “In phụ đề” lúc Tạo tự động không được lưu cho phim. | ĐÃ SỬA trong `server.py`, có kiểm tra trong `tests/test_autopilot.py`. |
| F24 | CHẶN (đã gỡ) | 27/11 ≈ 12:50, phim 5: trang muse.ai không hiện ô chat ở một lần tải (ảnh nhân vật thứ 3) ⇒ cả phim dừng (exit 23), không có ảnh chụp lỗi. Yêu cầu Muse kế tiếp (phim 6) lại chạy được. | ĐÃ SỬA `muse_ui.py`: tải lại trang một lần rồi mới dừng, lưu ảnh chụp vào `~/.filmmaker/muse-failed`; test `tests/test_muse_open_chat.py`. Đã kích hoạt thật một lần khi bù phim 5: tải lại rồi chạy được. |
| F25 | Ghi nhận | Phim 4: Muse từ chối video s09, s10 (đối thoại trách móc, nước mắt). App tự viết lại prompt và làm tiếp cảnh sau; cả hai qua ở lần sau, giữ nguyên lời thoại. | Không cần sửa. |
| F26 | Lỗi (mất cảnh) | Phim 6: cảnh kết s10 bị cổng QC loại với lý do “dùng nhầm ảnh” (độ khớp khung đầu 0,61) dù clip đúng cảnh, chỉ bị Muse phóng gần hơn (đã xem khung hình). | ĐÃ SỬA `qc.py` (`reframes_start`: clip Muse không xét khớp khung đầu), test `tests/test_muse_reframe.py`. Chạy lại QC: 10/10 PASS. |
| F27 | Chất lượng | Phim 6 s10: lần viết lại prompt thứ 2 bỏ tên nhân vật khỏi phần mô tả ⇒ render không kèm ảnh nhân vật nào (chỉ còn keyframe). | ĐÃ SỬA (27/11): sau khi AI viết lại mà tên nhân vật biến mất, cảnh được ghim `characters` như trước; test trong `tests/test_autopilot.py`. |
| F28 | Giao diện | Phim làm bằng Muse vẫn hiện bảng “gflow chưa tạo được video từ ảnh trên tài khoản Flow này”. | ĐÃ SỬA (27/11): phim toàn Muse không còn hiện bảng này. |
| F29 | Nghẽn nhỏ | Đang chạy “QC lại” của một phim thì không xếp được “Tạo tự động” của chính phim đó vào hàng đợi. | ĐÃ SỬA cùng F14. |
| F30 | Chất lượng (cốt truyện) | Phim 5: ông Lert đổi trang phục giữa phim (bán vé số → chủ tịch mặc áo lụa) nhưng prompt ép “outfit exactly the same” theo ảnh chân dung duy nhất ⇒ s08 vẫn đồ vé số, s09 còn mũ, s10 trượt Pre-QC; s08, s09 vẫn lọt dù sai. | ĐÃ SỬA (27/11): nhân vật có `outfits` (tên → trang phục), cảnh chọn bằng `outfits: {Lert: chairman}`; prompt ghi rõ trang phục của cảnh thay cho trang phục trong mô tả/ảnh chân dung; người viết storyboard được dặn dùng. Test `tests/test_outfits.py`. Phim 5 đã làm trước bản sửa nên không dùng trường này. |
| F31 | Nghẽn | 14:33 app được khởi động lại từ trong app khi phim 5 đang chạy ⇒ mất việc đang chạy và hàng đợi (F14). | ĐÃ SỬA (27/11): việc đang chạy và hàng đợi được ghi vào `<workspace>/.job-queue.json`; mở lại app tự chạy tiếp từ chỗ dở (trừ đăng video và viết kịch bản; render lại có chủ đích không gửi lại lần hai). Cảnh báo khi thoát/khởi động lại đã đổi theo. Test `tests/test_job_queue.py`. |
| F32 | Lỗi (mất cảnh) | Muse từ chối clip khi gửi kèm ảnh nhân vật: phim 5 mất s01, s06, s08, s10 sau 3 lần/cảnh; bỏ ảnh kèm thì s01, s06, s10 qua ngay, s08 qua khi dùng lại prompt gốc. AI viết lại prompt không giải được. | ĐÃ SỬA `pipeline.py`: Muse từ chối ⇒ gửi lại chỉ với ảnh mở đầu trước khi viết lại prompt; test `tests/test_ref_limit_retry.py`. 4 cảnh đã bù bằng cách đó (lệnh `run` một lần), đường tự động trong app chưa gặp thật. |
| F33 | Giao diện | Mở lại app, trang dự án quay “Đang tải…” khoảng 20 giây (35 dự án). Clip Muse vẫn gắn nhãn “Render qua giao diện chat Flow” (cùng nhóm F28). | ĐÃ SỬA (27/11): danh sách dự án được nhớ tới khi file thay đổi và tính sẵn lúc mở app (7 s ⇒ 0,07 s khi đã có); clip Muse gắn nhãn “Render bằng Meta Muse”. |

## Nhật ký từng phim

### 1 · แม่สองคน (Flow)
- 22:39 bấm tạo; storyboard đủ 10 cảnh, 3 nhân vật, thoại Thái; review sửa 3 cảnh chưa đúng quy tắc I2V.
- 22:48–22:55 ảnh 3 nhân vật + 4 bối cảnh. 22:57 bắt đầu có keyframe; s01–s03 đạt Pre-QC 7–8/10, xem tay thấy nhân vật đồng nhất.
- 23:36 đủ 10 keyframe, bắt đầu render video (≈ 2 phút/clip, 5 credit). s03 bị Flow từ chối (F9), app xếp lại để làm sau.
- ≈ 00:05–00:35: render xong s01–s08, s10; s09 bị bỏ (F13). 00:42 xong cả quy trình: 114,6 phút, 7/10 cảnh.

### Khởi động lại app và xếp hàng
- 00:42 khởi động lại app (không có việc đang chạy) để nạp các bản sửa; bấm 5 phim còn lại trên giao diện: phim 2 chạy ngay, 4 phim vào hàng đợi — “Hàng đợi: 4”, đúng thứ tự.

### 2 · ปิ่นโตของแม่ (Flow)
- ≈ 00:45 bắt đầu; 10 cảnh, 4 nhân vật. Render 10/10 clip ngay lượt đầu, không lỗi Flow. Hậu kiểm loại s02 (F16); bản ghép 9/10 cảnh, 63 giây.

### 3 · แผลเป็นของพี่ (Flow)
- ≈ 02:09–03:44; 10 cảnh, 5 nhân vật. Không lỗi Flow, không cảnh nào bị QC loại; bản ghép 10/10 cảnh, 73 giây; agent chấm 8/10 PASS.

### 4 · ก๋วยเตี๋ยวเที่ยงคืน (Muse)
- 03:45 storyboard 10 cảnh, 3 nhân vật (video + ảnh `muse`, 10 giây/cảnh); dừng ở ảnh nhân vật: chưa đăng nhập Muse (exit 8). Chưa tốn gì.

### 5 · ผ้าเช็ดหน้าผืนนั้น (Muse)
- Storyboard 10 cảnh, 4 nhân vật; dừng ở ảnh nhân vật (exit 8). Hàng đợi tự chuyển sang phim 6, không bị kẹt.

### 6 · พวงมาลัยของพ่อ (Muse)
- Storyboard 10 cảnh, 4 nhân vật; dừng ở ảnh nhân vật (exit 8).

### Đợt Muse (27/11, sau khi người dùng đăng nhập lại)
- ≈ 11:45 bấm “Tạo tự động” cho phim 4, 5, 6 trên giao diện (không tốn credit Flow).
- Phim 4 xong trong 61,7 phút: 10/10 cảnh, không cảnh nào bị QC loại, 92 s, 8/10 PASS (F25); phụ đề Thái không bị cắt (đã xem khung hình).
- Phim 5 dừng ở ảnh nhân vật (F24); hàng đợi tự chuyển sang phim 6. Phim 5 đã xếp lại sau phim 6.
- Phim 6 xong trong 64,3 phút: s01, s07, s09, s10 bị Muse từ chối lần đầu (s10 hai lần), app tự viết lại và qua hết; bản đầu 9/10 cảnh, 7/10 (F26).
- Khởi động lại app (không có việc đang chạy) để nạp F24, F26; QC lại phim 6: 10/10 PASS; xếp ghép lại sau phim 5.
- Phim 6 ghép lại (4,8 phút): 10/10 cảnh, 90 s, 8/10 PASS.
- Phim 5: bị gián đoạn lúc 14:33 (F31); dừng thêm một lần để sửa F30, khởi động lại app, tạo lại keyframe s08–s10.

### Làm bù phim 1
- ≈ 04:05 bấm “Render video” (chỉ s09), rồi render lại s02, s07 và “Tạo tự động” để QC + ghép lại.
- s09 render bù đạt 8/10; s02 render lại đạt 8/10. Khởi động lại app lần 2 (nạp F20) rồi render lại s07.
- s07 đạt 9/10 (đã xem khung hình: quay lưng suốt cảnh). Phim 2: s02 render lại đạt 8/10, hết chữ trên hình. Xếp “Tạo tự động” cho phim 1 và 2 để ghép lại.
- Ghép lại phim 1 qua hàng đợi (2,3 phút): 10/10 cảnh, 71 giây, 8/10 PASS (lượt đầu 7/10 cảnh, 4/10).

## Kết quả (27/11, ≈ 16:10) — đủ 6/6 phim

| Phim | Engine | Cảnh | Điểm cả phim | Độ dài | File |
|---|---|---|---|---|---|
| แม่สองคน | Flow | 10/10 | 8/10 PASS | 71 s | `~/FilmMaker/thai-hai-nguoi-me/output/storyboard/final.mp4` |
| ปิ่นโตของแม่ | Flow | 10/10 | 8/10 PASS | 72 s | `~/FilmMaker/thai-hop-com-cua-me/output/storyboard/final.mp4` |
| แผลเป็นของพี่ | Flow | 10/10 | 8/10 PASS | 73 s | `~/FilmMaker/thai-ca-truc-dau-tien/output/storyboard/final.mp4` |
| ก๋วยเตี๋ยวเที่ยงคืน | Muse | 10/10 | 8/10 PASS | 92 s | `~/FilmMaker/thai-quan-mi-dem/output/storyboard/final.mp4` |
| ผ้าเช็ดหน้าผืนนั้น | Muse | 10/10 | 7/10 PASS | 91 s | `~/FilmMaker/thai-ong-lao-ban-ve-so/output/storyboard/final.mp4` |
| พวงมาลัยของพ่อ | Muse | 10/10 | 8/10 PASS | 90 s | `~/FilmMaker/thai-chiec-tuk-tuk-cua-bo/output/storyboard/final.mp4` |

Điểm 3 phim Flow chấm trước khi xuất lại để sửa phụ đề (F22); hình và tiếng không đổi.

Phim 5, điểm còn thiếu theo agent chấm (đã xem khung hình): s10 ra cận mặt ông Lert thay vì cận hai bàn tay nhận khăn và thẻ; ảnh bìa vẫn vẽ ông Lert đội mũ, đeo khay vé số trong văn phòng.

## Việc tiếp theo
1. Commit các bản sửa (chưa commit).
2. F6: chạy song song một phim Flow và một phim Muse (đề xuất thiết kế, chưa làm).
3. Theo dõi trên phim thật: F8 (ứng viên keyframe dừng sớm), F17 (phiên Muse giữ được qua nhiều ngày), F32 (Muse từ chối ⇒ gửi lại không kèm ảnh).
4. Không cần sửa: F10 (môi trường máy), F12, F16, F25 (app đã tự xử lý).
