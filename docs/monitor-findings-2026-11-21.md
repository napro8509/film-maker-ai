# Theo dõi luồng "Tạo tự động toàn bộ" — 21/11/2026

- Job: `13cceddf42` (kind `auto`), bắt đầu khoảng 19:00
- Phim: *เดือนของเต้ (Duean của Tae)*: 10 cảnh × 8 giây, khung 9:16, có thoại, người thật
- Nguồn: kịch bản đã duyệt trong Kho kịch bản, được đưa vào làm "ý tưởng"
- Log thô có dấu thời gian: `docs/monitor-run-13cceddf42.log`

## Dòng thời gian

| Giờ | Bước | Ghi chú |
|---|---|---|
| 19:00 → 19:09 | 1/10 Viết storyboard | khoảng 9 phút, ra 10 cảnh, 5 nhân vật; thư mục `~/FilmMaker/thai-chu-trau-duean` |
| 19:09 | 2/10 Review storyboard | **bị bỏ qua ngay** ("đã kiểm quy tắc khi lập kế hoạch") |
| 19:09 → 19:12:52 | 3/10 Ảnh nhân vật | Muse, 5 ảnh, khoảng 40 giây/ảnh, không lỗi |
| 19:12:52 → 19:49:33 | 4/10 Keyframe | khoảng 37 phút; 4/10 cảnh phải tạo lại vì OCR nhận nhầm chữ |
| 19:49:33 → 20:00:14 | 5/10 Review keyframe | khoảng 11 phút; s09 tạo lại 1 lần, vẫn 5/10 |
| 20:00:14 → 20:58:16 | 6/10 Render video | khoảng 58 phút; 9/10 clip; s07 bị Muse chặn 3 lần nên bỏ; s08, s09 qua sau khi viết lại |
| 20:58:16 → 21:04:16 | 7/10 QC clip | khoảng 6 phút; **4 clip bị loại oan vì OCR** (s02, s03, s06, s10) |
| 21:04:16 → 21:05:37 | 8/10 Dựng phim | khoảng 1 phút; ra `final.mp4` **44 giây, chỉ 5/10 cảnh** |
| 21:05:37 → 21:06:17 | 9/10 Thumbnail | 40 giây |
| 21:06:17 → 21:07:37 | 10/10 Review cả phim | 6/10 REVISE |
| **Tổng** | | **118 phút** cho một phim 44 giây (dự kiến 100 giây) |

## Kết quả

- `~/FilmMaker/thai-chu-trau-duean/output/storyboard/final.mp4`: 44 giây, gồm s01, s04, s05, s08, s09
- Thiếu s07 (Muse chặn); thiếu s02, s03, s06, s10 (cổng QC loại oan vì OCR). Cả 4 clip này **vẫn còn file**, nên có thể ghi đè `qc.json` rồi dựng lại mà không tốn credit.
- 2 cảnh bị AI đổi lời thoại so với kịch bản đã duyệt: s07 ("xin lỗi" thành "tự hào") và s09 (mất ý "ông đã mua trâu").

## Ưu tiên sửa (theo mức tác động)

| # | Vấn đề | Mục | Tác động lần chạy này |
|---|---|---|---|
| 1 | OCR nhận nhầm chữ nhưng vẫn là lỗi cứng ở cả Pre-QC keyframe lẫn QC clip | 14, 38 | **Mất 4/10 cảnh**, khoảng 15 phút tạo lại keyframe |
| 2 | Cổng QC tự loại clip, không dừng lại hỏi khi mất nhiều cảnh hoặc mất cảnh chính; agent dựng phim và agent review cũng không phủ quyết | 38c, 41, 43 | Xuất ra phim mất mạch truyện mà không có cảnh báo chặn |
| 3 | Muse chặn cảnh người lớn chạm vào hoặc trao đồ cho trẻ em; chưa có quy tắc phòng ngừa | 29, 35, 36 | 3 cảnh bị chặn, 7 lần gửi thất bại, mất s07 |
| 4 | `soften_scene` chỉ thêm tính từ hoặc tự đổi thoại đã duyệt | 30, 32, 36 | s09 mất ý chính, s07 đảo nghĩa |
| 5 | Cờ FAIL của Pre-QC không được xoá sau khi agent review đã duyệt, nên keyframe bị tạo lại ở bước render | 23 | s01 tới vòng 4 (8 ảnh), s09 tới vòng 5 |
| 6 | Ảnh nhân vật mang trang phục của cảnh sau; QC so với ảnh nhân vật chứ không so với kịch bản | 9, 15, 20, 40 | Tae đeo khăn sai ở s01–s04 |
| 7 | Storyboard từ kịch bản đã duyệt bị LLM sửa thoại, không có kiểm tra diff | 4, 6, 8 | s04, s05, s09 lệch thoại ngay từ bước 1 |
| 8 | Hiệu năng Muse: mở trình duyệt mới cho mỗi ảnh, `sleep` cố định, nghỉ 55 giây giữa các lần gửi, review JSON lỗi | 13, 17, 19, 34 | Khoảng 25–30 phút chờ không cần thiết |
| 9 | Viết lại prompt không thử lại khi lỗi mạng | 28 | Mất lượt viết lại song song cho s07 |
| 10 | Kho kịch bản viết 8 giây/cảnh trong khi Muse là 10 giây; cảnh 4 nhân vật; log không có giờ; app chạy code cũ | 1, 2, 3, 7, 12 | Nhịp lệch, khó chẩn đoán |

## Ghi nhận (chưa tối ưu / gây kẹt)

### Ghi nhận trước khi chạy

1. **App đang chạy code cũ.** FilmMaker được mở lúc 01:37, còn `gflow.py` và `pipeline.py` được sửa lúc 02:02–02:07 (Muse↔Flow tự đổi AI khi tạo ảnh lỗi). Python chỉ nạp module một lần, nên lần chạy này **không có** cơ chế đổi AI đó. App không báo cho người dùng biết là code trên đĩa đã mới hơn code đang chạy.
   *Đề xuất:* lúc khởi động thì ghi lại mtime/commit; khi file `.py` thay đổi thì hiện banner "Có bản mới, khởi động lại".
2. **`~/Library/Logs/FilmMaker.log` không có dấu thời gian.** Trong log có 25 dòng `[restart] mở lại bằng…` liên tiếp nhau mà không biết xảy ra lúc nào hay vì sao. Không thể dựa vào đó để chẩn đoán vòng lặp restart.
3. **Log của job không có dấu thời gian.** API `/api/jobs/{id}` trả về `lines` dạng chuỗi trần, nên không đo được mỗi bước mất bao lâu nếu không có công cụ ngoài đi poll.
4. **Bước 1 đổi kịch bản đã duyệt sang storyboard lại bằng một lần gọi LLM tự do.** Log ghi "Claude đang viết kịch bản 10 cảnh", trong khi thực chất là chuyển đổi. Có nguy cơ LLM sửa lời thoại hoặc số cảnh dù prompt đã dặn "giữ nguyên từng chữ", và sau bước này không có kiểm tra tự động nào đối chiếu thoại giữa script và storyboard.
5. **Job chưa có `project` cho tới khi bước 1 xong.** Trong lúc viết storyboard, giao diện không có thư mục nào để mở; nếu bước này lỗi thì không còn lại file nào để xem.

### Bước 1–2: storyboard

6. **LLM tự sửa lời thoại dù prompt dặn giữ nguyên từng chữ.** Ở s09, kịch bản gốc là `ลุงซื้อแล้ว แต่ลุงเลี้ยงบ่ไหวดอก` / `ฝากหลานเลี้ยงให้ลุงแหน่เด้อ`; storyboard lại thành `ลุงซื้อแล้ว แต่เลี้ยงบ่ไหว ฝากหลานเลี้ยงแหน่เด้อ` (mất "ลุง", "ดอก", "ให้ลุง"). s04 và s05 thì gộp hai câu, hai nhịp nói thành một chuỗi (s05 vốn có hai nhịp hành động khác nhau). Nhiều khả năng do quy tắc "một câu ≤ 12 từ" trong `planner.py:50` mâu thuẫn với kịch bản đã duyệt.
   *Đề xuất:* khi nguồn là kịch bản từ Kho, **parse thoại theo quy tắc** từ các dòng `- Tên: "…"` rồi gán thẳng vào storyboard, không để LLM viết lại; sau bước 1 thêm bước kiểm tra diff thoại giữa script và storyboard, lệch thì báo hoặc chặn.
7. **Thời lượng lệch: kịch bản 8 giây/cảnh, Muse chỉ hỗ trợ 10 giây** (`models.yaml: durations: [10]`). Storyboard tự nâng lên 10 giây, tổng 100 giây thay vì 80 giây; nhịp diễn trong kịch bản viết cho 8 giây nên dễ có 2 giây thừa ở mỗi cảnh. Kho kịch bản nên biết engine đích (hoặc giả định 10 giây khi engine là Muse).
8. **Bước 2 "Agent review storyboard" bị bỏ qua hoàn toàn** với luồng I2V ("đã kiểm quy tắc khi lập kế hoạch"). Vì vậy lỗi số 6 lọt qua. Tối thiểu nên có một bước kiểm tra rẻ, chạy bằng quy tắc: thoại khớp script, tên nhân vật, số cảnh.
9. **Prompt ảnh nhân vật mang thông tin theo cảnh.** Prompt portrait của Tae có câu "From the dusk scene onward a red-and-white checked cloth rests on his shoulder; in the final scene that cloth is tied around his waist". Ảnh tham chiếu chỉ cần trang phục gốc; câu theo thời gian này dễ làm model vẽ thêm khăn vào ảnh nhân vật, rồi khăn lan sang các cảnh đầu (s01–s04).
   **ĐÃ XÁC NHẬN:** `assets/characters/tae.png` có khăn ca rô đỏ trắng buộc ở hông (trang phục của cảnh cuối), và ảnh lại là một cảnh dọn bàn ăn lúc hoàng hôn chứ không phải ảnh tham chiếu nền trung tính. Ảnh này sẽ được dùng làm tham chiếu cho mọi keyframe có Tae.
   *Đề xuất:* prompt portrait chỉ lấy trang phục gốc; chuyển các thay đổi trang phục theo cảnh sang `image_prompt` của cảnh; bắt buộc nền trung tính, đứng toàn thân, không có đạo cụ.
11. **Không có bước review ảnh nhân vật trước khi dùng làm tham chiếu.** Bước 3 xong là sang bước 4 ngay; ảnh nhân vật chỉ được xem cùng keyframe ở bước 5, lúc đó đã tạo xong 10 keyframe dựa trên ảnh tham chiếu sai. Nên có một bước kiểm tra nhanh portrait (trang phục khớp mô tả gốc, không có đạo cụ của cảnh) trước bước 4.
10. **Thư mục phim dùng lại thư mục Kho cũ** (`thai-chu-trau-duean`, tạo ngày 19/11, có sẵn `script.md`/`brief.md`). `storyboard.yaml` và `ui.json` bị ghi đè mà không có bản sao lưu; nếu thư mục đó đã có storyboard từ lần chạy trước thì sẽ mất.

### Bước 4: keyframe

12. **Cảnh 4 nhân vật chỉ được cảnh báo, không được xử lý.** s01 cần ảnh tham chiếu của Tae, Sak, Mee, Duean cộng thêm ảnh bối cảnh, tức 5 ảnh. Log hiện "⚠ 4 nhân vật… nên bớt người hoặc tách cảnh" nhưng pipeline vẫn chạy tiếp. Duean là trâu, không cần giữ khuôn mặt người, nhưng vẫn được tính vào giới hạn 3 người.
    *Đề xuất:* (a) không tính nhân vật động vật hay đồ vật vào giới hạn khuôn mặt; (b) cảnh báo này nên có ngay ở bước lập storyboard hoặc ở Kho kịch bản, không đợi tới bước keyframe; (c) với tham chiếu người, ưu tiên nhân vật đang nói hoặc đứng tiền cảnh.
13. **Từ khi có ảnh tới lúc ghi file mất khoảng 20 giây** (ví dụ 19:10:12 "có ảnh" → 19:10:32 "OK"), và giữa hai ảnh lại chờ thêm khoảng 20 giây. Nếu đó là `sleep` cố định trong `muse_ui.py` thì 15 ảnh của một phim mất khoảng 10 phút chỉ để chờ (cần kiểm tra lại, vì job log được poll 20 giây một lần nên số đo có thể bị làm tròn).
    **Đã kiểm tra lại code:** mỗi ảnh chạy `muse_ui.py` trong một subprocess mới, mở trình duyệt mới. Riêng các `time.sleep` cố định đã cộng lại khoảng 20 giây/ảnh: 3–4 giây khởi động, chờ `settle`, `3 + số file` sau khi upload, 5 giây sau khi gửi, 8 giây chờ ảnh progressive, mỗi vòng poll 5 giây (`muse_ui.py:117,153,186,446,463,469,498`).
    *Đề xuất:* giữ một phiên trình duyệt Muse cho cả batch, và thay `sleep` cố định bằng việc chờ đến khi phần tử hoặc thuộc tính `src` ổn định.
14. **[ƯU TIÊN CAO] OCR nhận nhầm chữ nên loại oan keyframe tốt, tốn thêm một vòng tạo ảnh và lặp lại lỗi cũ.** Ở s01, cả 2 ứng viên bị FAIL 3/10 vì "dính chữ trên hình (Hi |) iil | Milt | \ ——emay Sas Bt fia $ ro…)". Tôi đã mở cả 2 ảnh: **không có chữ nào**, đó là vân gỗ nhà sàn, vải ca rô và rơm. Đây cũng là lỗi đã gặp ở s01/s08 của phim trước (`thai-tham-ruong-cua-bo`) mà chưa được sửa.
    Nguyên nhân nằm ở `qc.py:286-304`: `tesseract --psm 6` (giả định một khối chữ đồng nhất) chạy trên ảnh tự nhiên luôn sinh ra rác; ngưỡng "≥2 từ dài từ 3 ký tự, hoặc 1 từ dài từ 5 ký tự" quá thấp; và `qc.py:342/676` coi mọi kết quả OCR là lỗi cứng, ghi đè cả nhận định của VLM (VLM thực ra đã đánh giá ứng viên #1 là tốt nhất).
    *Đề xuất:* dùng `--psm 11/12` (sparse text) kèm lọc `confidence ≥ 80` (qua `tesseract … tsv`); chỉ tính là chữ khi là từ có trong từ điển hoặc chữ Thái hợp lệ, hoặc khi VLM cũng xác nhận có chữ; OCR chỉ nên là gợi ý cho VLM, không phải lỗi cứng.
15. **Pre-QC không bắt được lỗi trang phục.** Cả 2 ứng viên s01 đều vẽ Tae **có khăn ca rô đỏ buộc ở hông** (do lỗi ở mục 9), trong khi kịch bản chỉ cho Tae có khăn từ s05 trở đi. Pre-QC chỉ nêu "môi hé" và "chữ", không so trang phục với mô tả của từng cảnh. Đốm trắng hình lưỡi liềm trên trán trâu, một chi tiết quan trọng, cũng gần như không nhìn thấy ở cả 2 ảnh.
16. **Sau 2 vòng FAIL, keyframe vẫn được nhận và log không nói vì sao.** Ở s01, vòng 2 cũng FAIL 3/10 (log không ghi lý do, nhiều khả năng lại là OCR nhận nhầm), nhưng ảnh vẫn được ghi thành `s01.png` và dùng luôn làm ảnh nối cho s02, nên lỗi khăn ở mục 15 lan sang các cảnh sau. Cũng vì vậy, 2 vòng Pre-QC không làm thay đổi kết quả mà chỉ tốn khoảng 5 phút.
    *Đề xuất:* (a) log ghi rõ lý do của vòng 2; (b) khi FAIL chỉ do OCR thì bỏ qua OCR và lấy kết quả VLM; (c) khi FAIL vì lý do khác thì đánh dấu cảnh cần xem lại trên giao diện, thay vì im lặng dùng ảnh.
17. **"Sinh song song 2 ứng viên" thực ra chạy tuần tự.** Với Muse, 2 ảnh được gửi lần lượt (19:16:52 rồi 19:17:52), mỗi ảnh khoảng 40–60 giây. Mỗi cảnh hiện mất khoảng 3 phút nếu đạt ngay, khoảng 6 phút nếu phải tạo lại. Với 10 cảnh, riêng bước keyframe mất khoảng 30–60 phút.
    *Bổ sung mục 14:* s03 cũng bị loại vì OCR rác ("lilo ltabal \ eair = a= os…"); tôi đã mở ảnh, không có chữ nào. Tới giờ 2/3 cảnh phải tạo lại chỉ vì OCR.
18. **Ứng viên vòng 2 ghi đè lên file của vòng 1** (`candidates/sXX_c1.png`, `_c2.png`), nên sau khi chạy không còn cách nào xem lại ảnh vòng 1 bị loại vì sao. Nên đặt tên `sXX_r1_c1.png`, `sXX_r2_c1.png`.
    *Bổ sung mục 14:* ở s04, **chính VLM viết "Cả hai sạch và đúng nhân vật"**, vậy mà OCR ("te enone Y= on, Lae Nai nm") vẫn hạ cả 2 ứng viên xuống FAIL 3/10. Đây là bằng chứng rõ nhất rằng OCR đang ghi đè lên nhận định của VLM. Tới giờ 3/4 cảnh phải tạo lại vì OCR.
    *Thống kê mục 14 (tới s09):* 4 trên 9 cảnh (s01, s03, s04, s09) phải tạo lại vì OCR, mỗi lần mất khoảng 2,5 phút, tổng cộng khoảng 10 phút lãng phí. Các cảnh ban đêm, tối (s05–s08) đều qua ngay vòng 1; OCR chỉ báo nhầm ở cảnh ban ngày, nhiều vân gỗ và chi tiết. Một keyframe mất khoảng 2 phút 40 giây nếu qua ngay, khoảng 5 phút nếu phải tạo lại.

### Bước 5: Agent review keyframe (bắt đầu 19:49:33)

19. **Claude review không trả được JSON đúng định dạng dù chỉ với 2 ảnh.** Lỗi lặp lại nhiều lần mất 2 phút (19:49:33 → 19:51:33) rồi mới chia còn 1 ảnh mỗi lượt, trong khi `REVIEW_BATCH = 5`. Gần như cố định mất 2 phút cho mỗi lần review.
    *Đề xuất:* dùng structured output hoặc tool-use với schema; hoặc mặc định 1 ảnh mỗi lượt và chạy song song; lưu nguyên văn câu trả lời lỗi để biết vì sao parse hỏng.
20. **Agent review so ảnh với ảnh nhân vật, không so với kịch bản, nên lỗi trong ảnh nhân vật được coi là đúng.** Review s01 cho 8/10 và viết "trang phục các nhân vật khớp chân dung", trong khi Tae đeo khăn ở hông là sai kịch bản (mục 9, 15). Đến s09 (tạo lại), Pre-QC lại chê "khăn buộc ở thắt lưng thay vì vắt trên vai như mô tả". Hai lớp QC mâu thuẫn nhau, còn bản thân ảnh nhân vật thì cứ kéo model về phía khăn ở hông, nên s09 dễ rơi vào vòng tạo lại không bao giờ sửa được.
21. **Agent review chỉ xem 2/10 keyframe (s01 và s09, hai cảnh FAIL Pre-QC).** 8 keyframe còn lại chỉ dựa vào Pre-QC; với các cảnh đã PASS như vậy là hợp lý, nhưng log không nói rõ là chỉ chọn cảnh FAIL.
22. **Tạo lại theo góp ý thì bỏ ảnh nối và dựng lại từ đầu với 4 nhân vật + bối cảnh** ("đã nối 2 cảnh liền… tạo lại từ ảnh nhân vật + bối cảnh"). Cảnh tạo lại vì thế có nguy cơ lệch ánh sáng và vị trí so với s08 và s10.
23. **[ƯU TIÊN CAO] Cờ FAIL của Pre-QC không bị xoá sau khi agent review đã duyệt, nên keyframe bị tạo lại lần nữa ở bước render.** Lúc 20:00:14, khi vào bước 6: "Keyframe sẵn có của s01 bị đánh dấu FAIL — tạo lại ứng viên mới". Thực ra s01 FAIL chỉ do OCR nhận nhầm (mục 14), và agent review ở bước 5 đã cho 8/10. Kết quả là s01 bị tạo keyframe tới vòng thứ 3 (6 ảnh), keyframe vừa được review duyệt bị thay bằng ảnh **chưa ai review**, và phải chờ thêm khoảng 2,5–5 phút trước khi render. s09 nhiều khả năng cũng bị như vậy.
    *Đề xuất:* agent review PASS/MINOR phải ghi đè cờ Pre-QC (lưu `verdict_source` trong state); bước 6 không nên tự chạy Pre-QC lại với keyframe đã qua bước 5.
24. **s09 vẫn 5/10 sau 2 vòng review mà phim vẫn đi tiếp, không báo người dùng.** Vấn đề mặt Mee cúi xuống, có nguy cơ Veo gán lời thoại sai người, vẫn còn nguyên. Hết vòng review lẽ ra phải dừng lại hoặc đánh dấu cảnh để người dùng chọn.
25. **Thời gian bước 4+5:** keyframe mất khoảng 37 phút (19:12 → 19:49), review keyframe khoảng 11 phút (19:49 → 20:00). Riêng tạo lại vì OCR và review lỗi JSON đã tốn khoảng 15 phút.
    *Bổ sung mục 23:* lúc 20:02:54, s01 FAIL lần thứ 3, sang vòng 4 (tổng 8 ảnh). **VLM ghi rõ "không có chữ/logo"** ngay trong cùng một dòng với kết luận OCR "dính chữ". Nếu vòng này cũng FAIL thì ảnh vẫn bị đẩy đi render, như vậy các vòng tạo lại không có điều kiện dừng nào có ý nghĩa.

### Bước 6: Render video (bắt đầu 20:00:14)

26. **Render trên Muse nhanh và ổn định: khoảng 2 phút 20 giây mỗi clip** (s01–s06 xong từ 20:06 đến 20:17, không lỗi). Thời gian của cả phim chủ yếu nằm ở keyframe và QC, không phải ở render.
27. **Muse từ chối s07 (Sak quỳ ôm Tae, xin lỗi) lúc 20:19:35.** Đây là cùng kiểu cảnh với s07 bị từ chối 3 lần ở phim trước: bố ôm con, cảm xúc mạnh. **Chính Muse đã đề xuất một phương án thay thế cụ thể** ("Sak kneeling beside Tae with his hand on Tae's shoulder rather than embracing him, saying the same apology line"), nhưng app chỉ ghi nhận là lỗi.
    *Đề xuất:* (a) khi Muse tự gợi ý cách làm khác, đưa nguyên câu gợi ý vào `soften_scene()` làm chỉ dẫn ưu tiên, hoặc trả lời "yes" ngay trong cuộc chat Muse để nó tự tạo; (b) ở bước lập storyboard hoặc Kho kịch bản, đánh dấu sẵn các hành động hay bị chặn (ôm, chạm người trẻ em, khóc) và viết lại trước khi tốn một lần render.
    *Ghi chú mục 27:* toàn văn lời nhắn của Muse đã nằm trong `error` truyền vào `soften_scene()` (`autopilot.py:716`), nên AI viết lại vẫn có thể đọc được gợi ý đó. Tuy vậy, prompt của `soften_scene` không yêu cầu ưu tiên làm theo gợi ý của Muse.
28. **Lần viết lại s07 chạy nền chết ngay vì lỗi mạng tạm thời và không được thử lại.** Lúc 20:19:55: "Claude Code không trả lời được: API Error: The socket connection was closed unexpectedly… để nguyên". Lần lỗi này không bị tính vào `MAX_SOFTEN` (hist chưa được ghi), nên `_soften_blocked()` sau vòng render vẫn có thể thử lại. Nhưng lợi thế chạy song song trong lúc render các cảnh khác đã mất, và người dùng thấy dòng "để nguyên" sẽ hiểu là app đã bỏ cuộc.
    *Đề xuất:* bọc `soften_scene` (và mọi lần gọi `claude -p`) trong retry có backoff, khoảng 2–3 lần, khi lỗi là socket, timeout hay 5xx; đổi câu log thành "sẽ thử lại sau vòng render".
29. **[ƯU TIÊN CAO] Có thể đoán trước kiểu cảnh Muse từ chối: người lớn chạm vào hoặc trao đồ tận tay cho trẻ em.** 3/3 cảnh bị từ chối đều cùng kiểu: s07 (bố ôm con), s08 (trao dây), s09 (ông lão đặt dây vào tay cậu bé). Lần nào Muse cũng gợi ý cách thay thế giống nhau: **giữ khoảng cách, không chạm, không trao đồ tận tay**. Trong khi đó các cảnh trẻ em đứng một mình hoặc với con trâu (s04, s05, s06, s10…) đều qua.
    *Đề xuất:* thêm quy tắc "child-contact" vào `rules.py`, áp dụng ở bước storyboard và Kho kịch bản khi engine là Muse: không mô tả người lớn ôm, chạm hay đặt đồ vào tay trẻ em; thay bằng đứng cạnh, chìa đồ ra ở tầm tay, hoặc tách thành 2 shot. Mỗi lần bị từ chối tốn khoảng 2 phút render cộng một vòng viết lại; phim này có 3/10 cảnh, nên làm trước sẽ tiết kiệm đáng kể.
    s09 còn bị thêm một lượt tạo lại keyframe vô ích (mục 23) ngay trước khi bị từ chối: 2,5 phút tạo ảnh cho một clip không thành.
30. **[ƯU TIÊN CAO] Lần viết lại s09 chỉ thêm tính từ, giữ nguyên đúng hành động đã bị chặn.** `video_shot.action` mới là "the stooped old man **gently lays the rope end into the boy's open cupped hands** with a warm, kindly expression…". Bản mới chỉ thêm "gently/warm/kindly", hành động trao dây vào tay trẻ em vẫn còn nguyên, và gợi ý của Muse ("Mee standing beside Duean speaking to Tae from a little distance, rather than handing the rope over") bị bỏ qua. Ngoài ra `prompt` vẫn là bản cũ trong khi `video_shot` đã đổi, nên 2 trường lệch nhau; log in ra "prompt mới:" rỗng vì `fix.prompt` rỗng.
    *Đề xuất:* (a) prompt của `soften_scene` phải bắt AI **thay đổi hành động hoặc bố cục**, không chỉ đổi giọng văn, và khi lời Muse có cụm "I can make a different version… instead —" thì trích phương án đó ra, coi như chỉ dẫn bắt buộc; (b) kiểm tra hậu kỳ: nếu động từ chính hoặc đối tượng chính không đổi thì yêu cầu viết lại; (c) đồng bộ `prompt` với `video_shot.action`; (d) log nên in trường thực sự đã thay đổi.
31. **s08 và s09 bị Muse từ chối chỉ khoảng 40 giây sau khi gửi.** Nghĩa là Muse kiểm duyệt keyframe cộng prompt trước khi render, vì vậy một bước "tiền kiểm" bằng quy tắc (mục 29) sẽ chặn được các lần gửi vô ích này.
32. **[ƯU TIÊN CAO] Lần viết lại thứ 2 tự đổi lời thoại đã duyệt và làm mất nút thắt của truyện.** s09 render được (20:45), nhưng thoại đã đổi từ "ลุงซื้อแล้ว แต่ลุงเลี้ยงบ่ไหวดอก / ฝากหลานเลี้ยงให้ลุงแหน่เด้อ" (*Bác mua rồi nhưng bác nuôi không nổi, nhờ cháu nuôi giùm*) thành "เดือนขออยู่กับหลานเนาะ ฝากหลานเลี้ยงให้ดีเด้อ" (*Duean xin ở với cháu, nuôi cho tốt nhé*). Ý "ông đã mua trâu rồi gửi lại", chính là cao trào của phim, đã biến mất; hành động trao dây cũng bị bỏ. Thoại không hề là nguyên nhân bị chặn, nên không có lý do gì để đổi. Người dùng cũng không được hỏi hay báo.
    *Đề xuất:* `soften_scene` mặc định **không được đổi `dialogue`/`narration`** khi phim lấy từ kịch bản đã duyệt (chỉ đổi hình ảnh, hành động, máy quay); nếu buộc phải đổi thoại thì phải đánh dấu cảnh và báo người dùng duyệt.
33. **Log nhắc "Flow chỉ nhận 3 ảnh" trong khi engine là Muse** ("bỏ Mee: Flow chỉ nhận 3 ảnh, ưu tiên người nói / tương tác"). Thông báo dùng chung cho cả hai nên dễ hiểu nhầm. Nên kiểm tra lại xem giới hạn 3 ảnh tham chiếu có đúng với Muse không; nếu Muse nhận nhiều hơn thì đang bỏ phí ảnh tham chiếu.
34. **Nghỉ cố định 55 giây giữa các lần gửi Muse** ("nghỉ 55s giữa các lần gửi"). Với khoảng 13 lần gửi video, chỉ riêng thời gian nghỉ đã khoảng 12 phút. Nên chỉ nghỉ khi Muse báo rate limit, hoặc dùng khoảng thời gian đó để tạo keyframe cho cảnh tiếp theo.
35. **Ngay cả gợi ý của chính Muse cũng bị Muse từ chối.** Lần viết lại thứ nhất của s07 làm đúng theo gợi ý trước đó của Muse (tay đặt lên vai thay vì ôm), nhưng lúc 20:53 vẫn bị chặn; giờ Muse gợi ý "tay buông thõng, không chạm". Điều này củng cố mục 29: **bất kỳ cử chỉ chạm nào giữa người lớn và trẻ em đều bị chặn**, nên quy tắc phải là "không chạm", chứ không phải "chạm nhẹ hơn".
36. **s07 bị bỏ khỏi phim sau 3 lần bị từ chối (20:58:16), dù bản cuối đã bỏ hẳn cử chỉ chạm.** Bản thứ 3 chỉ là hai bố con đứng cạnh nhau, tay buông thõng, góc nhìn từ phía sau, và Pre-QC chấm PASS 8/10, nhưng vẫn bị chặn. Bản này cũng **lại đổi thoại** từ "พ่อขอโทษเด้อลูก" (*Bố xin lỗi con*) sang "พ่อภูมิใจในตัวเจ้าเด้อลูก" (*Bố tự hào về con*), nghĩa là ý nghĩa của cảnh bị đảo ngược (xem mục 32).
    *Giả thuyết (chưa kiểm chứng):* cả 3 lần, keyframe đều được **nối từ keyframe s06** (đêm, cậu bé buồn hoặc khóc) và luôn có ảnh tham chiếu Tae, nên cảm xúc "trẻ em đau buồn" của ảnh gốc vẫn còn. Gợi ý cuối của Muse đổi hẳn bối cảnh sang "đi cạnh nhau ra ruộng lúc bình minh". Lần viết lại cuối nên **ngắt chuỗi nối keyframe** và đổi ánh sáng hoặc bối cảnh.
    *Đề xuất về luồng:* đây là lần thứ 2 liên tiếp cảnh "bố xin lỗi con" (s07) bị bỏ (phim trước cũng vậy, phải làm tay tới vòng 4). Khi một cảnh có thoại quan trọng bị bỏ, phim không nên tự dựng tiếp mà nên **dừng lại hỏi người dùng** (bỏ cảnh, sửa tay, hay thử phương án mà Muse gợi ý), hoặc ít nhất đánh dấu rõ trên giao diện. Có thể thêm một lượt thứ 3 chạy đúng nguyên văn phương án Muse gợi ý.
37. **Tổng kết bước 6:** khoảng 58 phút (20:00 → 20:58). Trong đó render thật 9 clip khoảng 21 phút; phần còn lại dành cho tạo lại keyframe vì OCR hoặc cờ FAIL cũ (s01, s09), 3 cảnh bị chặn với 6 lần gửi thất bại, viết lại prompt, và nghỉ 55 giây giữa các lần gửi. Kết quả 9/10 cảnh, có 2 cảnh bị đổi thoại (s07 bị bỏ, s09 đã đổi).

### Bước 7: AI QC clip (20:58 → 21:04)

38. **[NGHIÊM TRỌNG NHẤT] Cổng QC loại oan 4/9 clip vì OCR nhận nhầm chữ, phim chỉ còn 5/10 cảnh.** s02, s03, s06 và s10 đều FAIL 4/10 với lý do duy nhất là "OpenMontage phát hiện chữ/phụ đề dính trên khung hình", kèm chuỗi rác ("I Hs ti . sah SLL |", "oe |\| arse amt…"). **Tôi đã trích đúng các khung hình bị báo (s02 @8.8s, s06 @1.2s): không có chữ nào.** Đó là vân gỗ, vải ca rô, khăn kẻ và bóng tối. Sau đó "🚫 Cổng QC loại bỏ 4 clip lỗi khỏi bản ghép cuối", nên phim dựng từ s01, s04, s05, s08, s09: mất cảnh mở đầu cảm xúc (s02, s03), mất cảnh Tae đưa dây cho bố (s06, câu thoại chính trong brief "ขายเถอะพ่อ… ให้แม่หายนะ") và mất cảnh kết (s10). Cộng thêm s07 đã bị bỏ, **câu chuyện không còn đọc được**.
    Đây cùng một lỗi gốc với mục 14 (`qc.py:286-304`), áp cho 4 frame của mỗi clip, nên xác suất báo nhầm còn cao hơn ở keyframe. Phim trước cũng bị đúng lỗi này ở s01/s08, và đã phải sửa tay `qc.json`.
    *Đề xuất (làm ngay):*
    (a) OCR **không bao giờ** được là lý do duy nhất để FAIL; chỉ hạ điểm hoặc đánh dấu "cần xem lại", và nhờ VLM xác nhận lại đúng frame đó;
    (b) dùng `--psm 11` kèm lọc độ tin cậy, và yêu cầu chữ phải lặp lại ở ít nhất 2 frame liên tiếp, vì phụ đề in cứng thì đứng yên còn rác OCR thì ngẫu nhiên;
    (c) cổng QC **không được tự loại** khi việc loại làm mất hơn khoảng 20% số cảnh hoặc mất cảnh có thoại chính; khi đó dừng lại hỏi người dùng.
39. **STT tự bịa lời thoại kiểu "quảng cáo kênh" bằng tiếng Việt** ở s10 (Tae nói thì thầm tiếng Thái). Đây là lỗi bịa chữ quen thuộc của Whisper khi âm thanh nhỏ ("hãy đăng ký kênh…"). QC đã nhận ra nhưng vẫn tính vào điểm. Nên chỉ định `language="th"` cho Whisper, và lọc danh sách câu bịa quen thuộc của Whisper.
40. **Đã xác nhận lỗi khăn ở mục 9/15 lan sang clip:** ở s02 (buổi sáng), Tae vẫn đeo khăn ca rô đỏ ở hông. QC clip không nhắc tới, vì cũng so với ảnh nhân vật.

### Bước 8–9: dựng phim, thumbnail

41. **Agent dựng phim lặng lẽ dựng từ 5 cảnh còn lại, không cảnh báo truyện bị đứt.** Bước dựng nhanh (khoảng 1 phút), ra phim 44 giây, mỗi clip 10 giây bị cắt còn khoảng 8,8 giây. Agent có đọc kịch bản và viết ghi chú dựng rất kỹ (fade-to-black, crossfade), nhưng không nhắc rằng thiếu s02, s03, s06, s07, s10, tức mất 5 trên 10 câu thoại, trong đó có câu chính của brief. Agent dựng nên có quyền "phủ quyết": khi thiếu cảnh có thoại chính hoặc thiếu cảnh kết thì báo REVISE hoặc dừng, không xuất bản phim.
42. **Thumbnail tả cảnh s10 (sương sớm trên ruộng lúa), là cảnh đã bị loại khỏi phim,** và tả Tae "với khăn đỏ trắng". Prompt thumbnail nên lấy từ những cảnh thực sự có trong `final.mp4`.

### Bước 10: Agent review cả phim

43. **Agent review cả phim không biết các cảnh đã bị cổng QC loại, nên đánh giá sai.** Agent viết "Câu chuyện cảm động và dễ theo dõi", lại nhận xét về chuyển cảnh "s06 (bé đưa dây mời cha bán) sang s08" trong khi s06 không có trong phim, và chỉ ghi chung chung "s02, s03, s06, s10 không thấy trong sheet mẫu, cần xác nhận đã render". Nguyên nhân là agent nhận kịch bản cùng khung hình nhưng không nhận `result.qc_failed`. Agent cũng lấy lời thoại đã bị đổi ("cha nói tự hào về con") làm chuẩn, và còn đề xuất render s07 theo đúng bản đã đổi đó. Điểm 6/10 vì vậy quá cao so với một phim chỉ còn 5/10 cảnh.
    *Đề xuất:* truyền vào review danh sách cảnh thực sự có trong `final.mp4`, các cảnh bị loại kèm lý do, và thoại gốc của kịch bản; thiếu từ 30% số cảnh trở lên thì tự động FAIL.
44. **Thông báo cuối "🎬 Xong toàn bộ" không nói rõ phim thiếu một nửa.** `result` có `missing` và `qc_failed`, nhưng dòng log cuối chỉ ghi điểm 6/10. Nên in to: "Phim chỉ có 5/10 cảnh, thiếu: …, xem lý do và bấm nút Dựng lại khi bỏ qua QC".

## Đã sửa (21/11, sau lần chạy): mục 14, 38, 44 và một phần mục 2/3 của bảng ưu tiên

- **`qc.ocr_frame`**: chỉ đọc 40% phía dưới khung hình (vùng phụ đề), phóng to lên ≥1080 px, tách chữ sáng bằng 2 ngưỡng luma (150 và 200), đọc bằng Tesseract `--psm 6` dạng TSV và chỉ giữ từ có độ tin cậy ≥ 80.
  - Không báo nhầm trên 66 ảnh của phim này (36 frame clip, 10 keyframe, 20 ứng viên) và 40 frame của phim `thai-tham-ruong-cua-bo`; trước đây 8 cảnh bị báo nhầm.
  - Bắt được 3/3 phụ đề tiếng Anh/Việt in thử ở vùng dưới, kể cả chữ vàng cỡ nhỏ.
  - Không đọc chữ in phía trên khung hình (đã có VLM kiểm), và không đọc tiếng Thái vì máy chưa cài gói `tha` của Tesseract.
- **OCR không còn tự gây FAIL.**
  - Keyframe: OCR → MINOR nếu không có VLM; có VLM thì VLM quyết định (gợi ý OCR được gửi kèm, ghi rõ là "noisy").
  - Clip: OCR → `minor_issues`; `text_errors` chỉ đúng khi VLM xác nhận.
  - Giao diện: huy hiệu "△ nghi có chữ" thay cho "✘ lỗi chữ".
- **`QC_VERSION = 3`**: các kết luận cũ (keyframe hoặc clip) không đạt mà có trích lỗi OCR cũ sẽ được chấm lại; các kết luận khác giữ nguyên.
- **Cổng QC lúc dựng phim** (`filter_clips_for_assembly`):
  - Clip hỏng kỹ thuật (file lỗi, khung hình đen, mất tiếng, chữ đã được VLM xác nhận) luôn bị loại.
  - Clip FAIL do AI chấm nội dung chỉ bị loại khi tổng số cảnh mất ≤ `max(1, 25%)`; quá mức đó thì giữ lại hết và log "GIỮ LẠI…".
- **Cuối autopilot** in dòng "⚠ Phim chỉ có X/N cảnh — thiếu: …" và ghi `result["qc_excluded"]`.
- Chạy thử trên bản sao của phim này: 4 clip từng bị loại (s02, s03, s06, s10) được đánh dấu chấm lại, và cổng giữ đủ **9/9 clip**.
- Test: thêm `tests/test_qc_ocr_gate.py` (14 test); cả bộ test có 1508 test qua. Test `test_autopilot_basic_format_runs_vimax_and_openmontage_and_gates_bad_clip` vốn đã lỗi từ trước (gọi Anthropic API thật bằng key không hợp lệ trong settings).

## Đã sửa tiếp: mục 2–6 của bảng ưu tiên

- **Mục 2 (không dừng khi mất nhiều cảnh):** review cả phim được báo đúng những cảnh có trong `final.mp4`, những cảnh bị cổng QC loại (kèm lý do) và những cảnh không có clip. Thiếu ≥ 30% số cảnh thì luôn REVISE, điểm tối đa 4, kèm dòng "Phim thiếu X/N cảnh…" (`FILM_MISSING_REVISE`). Cuối luồng in "⚠ Phim chỉ có X/N cảnh".
- **Mục 3 (người lớn chạm vào trẻ em):** `rules.CHILD_CONTACT_RULE` được đưa vào `planner.SYSTEM`, `basic_planner.SYSTEM`, `reviewer.STORYBOARD_SYSTEM` và `SOFTEN_SYSTEM`: không ôm, chạm hay trao đồ tận tay trẻ em; thay bằng đứng cạnh, chìa đồ ra, đặt đồ xuống.
- **Mục 4 (bước viết lại prompt):**
  - `soften_scene` giữ nguyên thoại (dialogue, narration, `video_shot.speaker.line`) bất kể AI trả về gì. Chỉ được đổi thoại khi lỗi là "Audio generation failed".
  - Prompt yêu cầu đổi hành động hoặc dàn cảnh, không chỉ thêm tính từ.
  - Phương án Muse tự gợi ý được trích ra (`muse_suggestion`, đúng với cả 4 lời từ chối thật) và đưa cho AI làm nền.
  - Log "prompt mới" in ra `video_shot.action`.
- **Mục 5 (cờ FAIL Pre-QC):** khi agent review duyệt, `qc.accept_keyframe` ghi đè cờ FAIL, nên bước render không tạo lại ảnh nữa. Keyframe hết vòng sửa thì được đánh MINOR "dùng tạm", kèm log cảnh báo.
- **Mục 6 (ảnh nhân vật mang trang phục cảnh sau):**
  - `i2v_prompt.base_look` bỏ khỏi prompt portrait các câu nói về cảnh sau. Thử trên 101 nhân vật cũ: 4 câu bị bỏ, cả 4 đều đúng.
  - `rules.OUTFIT_RULE` được đưa vào planner.
  - Review keyframe và Pre-QC so trang phục với chính cảnh đó, không so với ảnh portrait.
- Test: thêm `tests/test_monitor_fixes.py` (10 test); cập nhật 2 test trong `tests/test_autopilot.py` theo hành vi mới. Cả bộ có 1518 test qua; chỉ còn lỗi sẵn ở `test_autopilot_basic_format_…`, vì test này gọi API thật bằng key không hợp lệ.
- Chưa thử trên phim thật: cần khởi động lại app và chạy lại.

## Đã sửa: hiệu năng Muse (mục 13, 17, 34)

- **Các `sleep` cố định trong `muse_ui.py`** được thay bằng chờ có điều kiện, giới hạn trên đúng bằng thời gian chờ cũ:

  | Chỗ chờ | Trước | Sau |
  |---|---|---|
  | Sau khi mở trang | 3 giây | 0 (đã có bước chờ khung "Today") |
  | Sau khi upload | 3 + số ảnh | Chờ đủ số ảnh xem trước (đo được 0,5 giây thay vì 4 giây) |
  | Sau khi gửi | 5 giây | Chờ tin nhắn vào khung chat, tối đa 5 giây |
  | Ảnh hiện dần | 8 giây | Đọc `src` 2 lần giống nhau và ảnh đã tải xong, tối đa 8 giây |
  | Vòng kiểm tra | 5 giây | 2 giây (`POLL`) |

- **Phiên trình duyệt dùng chung:**
  - Thêm `muse_ui.py serve` và `gflow.MuseSession`, nhận yêu cầu dạng JSON từng dòng.
  - Phiên tự đóng sau 120 giây rảnh và khi autopilot kết thúc. Lỗi khởi động thì tự quay về cách mở trình duyệt riêng cho mỗi yêu cầu.
  - Tắt bằng `FILMMAKER_MUSE_SESSION=0`; khi có `MUSE_UI_CMD` (dùng cho test hoặc driver riêng) thì không dùng phiên chung.
- **Khoảng nghỉ giữa các lần gửi video Muse:**
  - Bỏ khoảng nghỉ cố định 45–75 giây sau mỗi clip.
  - `Gflow.muse_video` giờ giữ khoảng cách 45–75 giây giữa hai lần *bắt đầu gửi*. Clip mất khoảng 80 giây nên thường không phải chờ thêm.
  - Sau khi Muse báo lỗi, lần sau chờ 60, rồi 120, tối đa 180 giây; gửi thành công thì về lại bình thường.
- **Đo thật** với `--dry-run`: chỉ mở trang, upload và gõ prompt, không gửi.
  - Phần trước khi gửi: bản cũ khoảng 11,5 giây mỗi lần; bản mới chạy riêng khoảng 8–9 giây; bản mới trong phiên chung khoảng 6,4 giây.
  - Phần sau khi gửi (bubble, ảnh hiện dần, vòng kiểm tra) chưa đo được nếu không gửi thật; ước tính tiết kiệm thêm khoảng 8 giây mỗi ảnh.
  - Ước tính cho phim 10 cảnh: khoảng 6–8 phút ở phần ảnh, cộng khoảng 10 phút khoảng nghỉ video.
- Test: thêm `tests/test_muse_speed.py` (15 test). Cả bộ có 1532 test qua; ngoài lỗi sẵn do API key, còn `test_stitch_translates_and_writes_three_tracks` lỗi chập chờn khi chạy song song (chạy riêng qua 3/3 lần).
