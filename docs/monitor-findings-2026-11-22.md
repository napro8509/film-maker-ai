# Theo dõi job 9633c077fc — "ผ้าทอผืนสุดท้าย" (22/11/2026)

Lần chạy đầu tiên sau các bản sửa ngày 21/11 (commit e7b1177). Log đầy đủ có mốc giờ: `docs/monitor-run-9633c077fc.log`.
Dự án: `~/FilmMaker/thai-tam-vai-det-do`. 10 cảnh × 8 giây, 9:16, có thoại tiếng Thái (คำเมือง). Ảnh và video đều làm trên Flow (Veo 3.1 Lite).

## Kết quả

**Job FAILED lúc 01:34:58 và không có final.mp4.** Có 8/10 clip (s01–s03, s05–s09). s04 hết lượt viết lại. s10 làm cả job dừng.

| Bước | Thời gian | Ghi chú |
|---|---|---|
| 1. Storyboard | 23:47:49 → 23:56:54 (9 phút) | Bản nháp xong sau 2 phút; vòng "Sửa 4 cảnh chưa đúng quy tắc I2V" mất 7 phút |
| 2. Review storyboard | 0 giây | Đã kiểm khi lập kế hoạch |
| 3. Ảnh nhân vật | 4 phút | 3 nhân vật Flow, mỗi nhân vật khoảng 75 giây, OK |
| 4. Keyframe | 00:00:58 → 00:41:39 (41 phút) | 10/10: 4 PASS, 6 MINOR 7/10. Ảnh bối cảnh lỗi |
| 5. Review keyframe | 0 giây, không có dòng log | Bỏ qua vì Pre-QC đã chấm PASS/MINOR |
| 6. Video | 00:41:39 → 01:34:58 (53 phút) | 15 lần Flow nhận yêu cầu (5 credit/lần), 8 clip OK, 7 lần lỗi sau khi đã nhận |
| 7–10 | chưa chạy | Không có QC clip, dựng phim, ảnh bìa hay review phim |

Các bản sửa hôm qua hoạt động đúng ở những chỗ đã chạy: OCR không còn đánh FAIL nhầm keyframe, bước giữ nguyên thoại khi lỗi không phải audio đã chạy, và log đã in `video_shot.action` mới. Phần Muse không được dùng trong phim này.

## Vấn đề, xếp theo mức ảnh hưởng

### A. Lỗi làm hỏng cả phim

**A1. Một lỗi ở `gflow image t2i` dừng toàn bộ job** (`pipeline.py:671`, `gflow.py:38`)
- Bản viết lại thứ 2 của s10 thành cảnh quay từ trên xuống, chỉ có bàn tay. Khung hình không còn nhân vật, nên keyframe đi đường `gflow image t2i` (gflow-cli 0.101.2) thay vì giao diện chat Flow (`flow_ui.py`).
- gflow-cli không còn chạy với giao diện Flow hiện tại: `.settings-trigger-button did not become visible` → exit 23 (`EXIT_UI_DRIFT`).
- Exit 23 nằm trong `FATAL_EXIT_CODES`, nên `_on_other_ai` không thử AI còn lại và vòng render dừng hẳn (`report.aborted`). 8 clip đã xong không được dựng.
- Chính lỗi này đã làm hỏng ảnh bối cảnh "Terrace" lúc 00:01:37, nhưng ở đó lỗi được nuốt (chỉ cảnh báo).
- **Cách sửa:**
  1. Exit 23 từ gflow-cli chỉ đánh dấu riêng đường gflow-cli là hỏng trong lần chạy này, giống `_no_flow_chars` ở `pipeline.py:1118`. Ảnh không có nhân vật chuyển sang chat Flow (`flow_ui.py`, không cần ref) hoặc sang Muse.
  2. Ở bước video, một lỗi ảnh/keyframe của một cảnh không bao giờ được dừng cả vòng. Đánh dấu cảnh đó lỗi rồi làm cảnh tiếp theo.
  3. Khi vòng render dừng giữa chừng mà đã có ≥ 70% clip, vẫn chạy bước QC và dựng phim với số clip đang có (đã có cổng "Phim chỉ có X/N cảnh").

**A2. Ảnh bối cảnh lỗi nhưng app không thử cách khác**
- Cả phim không có ảnh tham chiếu bối cảnh hiên, nên không gian có thể lệch giữa các cảnh.
- **Cách sửa:** dùng cùng đường dự phòng như A1 (chat Flow, rồi đến Muse).

### B. Lượt viết lại bị dùng sai chỗ

**B1. Lỗi tạm thời của Flow bị coi là bị từ chối nội dung** (s01, s02, s07)
- Flow trả về "Something went wrong. Please try again." kèm nút *Try again*. App ghi "thường do nội dung cảnh" và viết lại prompt.
- Lần gửi lại s01 qua ngay. s02 tốn thêm khoảng 4 phút tạo lại 2 keyframe. s07 bị viết lại thành "ngồi thẳng, cười", mất trạng thái yếu ớt trong kịch bản.
- **Cách sửa:** trong `flow_ui.py`, tách "Something went wrong / Try again" khi không có "Audio generation failed" hay lời từ chối chính sách thành một mã lỗi riêng (lỗi tạm thời). Pipeline gửi lại đúng prompt cũ (tối đa 2 lần, cách nhau 30–60 giây) trước khi tính là một lượt viết lại.

**B2. Chờ 4 phút mới phát hiện lỗi** (s01 từ 00:42:09 đến 00:46:11, s07 tương tự)
- Thông báo "error" đã hiện ngay trong khung chat, nhưng app chờ tới hết hạn "chờ Flow xác nhận".
- **Cách sửa:** trong vòng chờ xác nhận, kiểm tra thông báo lỗi ở mỗi lần quét và thoát ngay khi thấy.

**B3. Cảnh không thoại bị "Audio generation failed" và bản viết lại không động tới âm thanh** (s04 ×3, s10 ×2)
- 5/5 lỗi audio rơi vào cảnh không thoại, hoặc cảnh mà Veo phải tạo giọng คำเมือง (s07). Mọi cảnh có thoại tiếng Thái phổ thông đều qua. Phim trước (Muse) không có lỗi audio nào.
- `SOFTEN_SYSTEM` chỉ bảo AI sửa thoại, giọng và hành động; nó không nhắc trường `video_shot.audio` ("gentle slow breathing", "one slow calm breath"…). Vì vậy bản viết lại chỉ đổi vài chữ trong hành động.
- Kịch bản đã định s04 và s10 dùng nhạc ru hậu kỳ, nên tiếng gốc của hai cảnh này đằng nào cũng bị thay.
- **Cách sửa:**
  1. Lần lỗi audio đầu tiên ở cảnh không thoại: không viết lại hành động. Chỉ rút `audio` về "quiet room tone" và bỏ tiếng thở, tiếng khóc, tiếng người.
  2. Lần thứ hai: thử AI video còn lại (Muse) hoặc model Veo khác cho riêng cảnh đó.
  3. Thêm vào `SOFTEN_SYSTEM`: "with no dialogue, the `audio` field is the usual trigger: keep it to plain ambience".

### C. Câu chuyện bị đổi mà không hỏi

**C1. Câu thoại cao trào s07 bị viết lại**
- Kịch bản: `นกน้อย... ภู่เจ้ามาหาอุ๊ยแหมเน้อ` (giọng คำเมือง, "sáng mai lại đến với bà nhé"; câu đầu tiên bà Kham nói trong phim).
- Bản đã render: `นกน้อย... ดีใจที่ที่หนูมาหาอุ๊ยนะ`. Câu này mất giọng địa phương, mất ý "sáng mai", và còn lỗi lặp chữ "ที่ที่".
- Lý do: lỗi audio cho phép đổi thoại (`keeps_dialogue` = False) mà không có bước kiểm tra nào.
- **Cách sửa:**
  1. Nếu bản viết lại đổi câu thoại đã duyệt, chạy so sánh với `script.final.md` và ghi rõ trong log: "⚠ s07 đổi thoại: A → B".
  2. Ở luồng tự động, chỉ cho đổi thoại ở lượt cuối, và cảnh đó được đánh dấu "cần người duyệt" trên UI.
  3. Trước khi đổi thoại, thử giữ nguyên câu nhưng đổi cách nói (`voice_style` "frail, slow" → "soft, calm") vì giọng "yếu ớt" có thể là thứ bị chặn.

**C2. Viết lại làm mất điểm cốt truyện** (s04: Jaa tự tỉnh kéo khăn thay vì bà Kham đắp khăn cho Jaa đang ngủ; s10: chuyển sang cận bàn tay, không thấy mặt Jaa)
- **Cách sửa:** đưa "Hành động chính" của cảnh trong kịch bản vào prompt viết lại như ràng buộc cứng ("must keep: …"). Sau đó cho một bước kiểm nhanh (cùng lần gọi) trả về `story_point_kept: bool`; nếu false thì không dùng bản đó.

### D. Tốc độ

**D1. Keyframe khoảng 4 phút mỗi cảnh** (41 phút cho 10 cảnh)
- Log ghi "Sinh song song 2 keyframe ứng viên" nhưng hai lần chạy `flow_ui.py` nối tiếp nhau (khóa `_ui_browser_lock`), mỗi lần khoảng 80 giây, cộng 35–45 giây VLM chọn ảnh.
- **Cách sửa (chọn một):**
  - xin 2 ảnh trong một lần gửi chat Flow (cần kiểm xem chat Flow có nhận yêu cầu "2 images" không);
  - chỉ sinh ứng viên thứ hai khi ứng viên đầu < PASS 8 (tiết kiệm khoảng 15 phút ở phim này, vì 4/10 cảnh đạt PASS ngay);
  - sửa câu log thành "lần lượt" cho đúng.

**D2. Storyboard: vòng sửa quy tắc I2V mất 7 phút** (bản nháp 2 phút)
- **Cách sửa:** sửa các cảnh lỗi bằng code khi được (các lỗi I2V đã được `i2v_prompt.warnings` liệt kê). Chỉ gọi AI cho cảnh còn lỗi sau khi sửa bằng code, và gọi song song từng cảnh.

**D3. Bản viết lại đổi `image_prompt` thì tạo lại cả 2 keyframe** (s02: +4 phút, dù lỗi chỉ là tạm thời)
- Sẽ hết khi có B1. Ngoài ra: khi viết lại vì lỗi video, chỉ đổi `image_prompt` nếu lỗi nói rõ khung đầu là vấn đề.

### E. Nhỏ

- **E1.** Bước 5 kết thúc mà không ghi dòng nào. Nên in "10/10 keyframe đã qua Pre-QC (6 MINOR), không cần review lại".
- **E2.** Cảnh MINOR 7 và MINOR 6 (s04 làm lại, chỉ có 1 ứng viên vì ứng viên kia lỗi) được dùng mà không ai xem lại. Gợi ý: review riêng các cảnh < 8 điểm, và thử lại ứng viên lỗi một lần.
- **E3.** Giao diện ghi "Flow không tạo được video (thường do nội dung cảnh)" cho mọi lỗi. Nên ghi đúng loại lỗi: tạm thời, audio, chính sách.
- **E4.** Có thể đã mất 7 × 5 = 35 credit cho các lần lỗi sau khi Flow nhận (Flow có hoàn credit hay không thì chưa kiểm).
- **E5.** Bộ theo dõi của chính tôi bị câm 30 phút đầu vì `cut` giữ đệm đầu ra (không phải lỗi app).

## Ưu tiên sửa

| # | Việc | Mục | Tác động |
|---|---|---|---|
| 1 | Exit 23 của gflow-cli không dừng job; ảnh không nhân vật đi chat Flow hoặc Muse | A1, A2 | Phim này đã có thể ra final.mp4 |
| 2 | Lỗi tạm thời "Something went wrong" → gửi lại nguyên prompt; thoát chờ ngay khi thấy lỗi | B1, B2, D3 | Giữ lượt viết lại, nhanh hơn khoảng 15 phút |
| 3 | Lỗi audio ở cảnh không thoại: rút `audio` trước, sau đó đổi AI | B3 | s04 và s10 có thể qua |
| 4 | Giữ thoại và điểm cốt truyện khi viết lại; báo khi thoại bị đổi | C1, C2 | Phim đúng kịch bản đã duyệt |
| 5 | Dừng giữa chừng mà đã có ≥ 70% clip vẫn dựng phim | A1.3 | Luôn có sản phẩm để xem |
| 6 | Keyframe: chỉ sinh ứng viên 2 khi cần; storyboard sửa I2V bằng code | D1, D2 | Nhanh hơn khoảng 20 phút |
| 7 | Log bước 5, review cảnh MINOR thấp, thông báo lỗi đúng loại | E1–E3 | Dễ theo dõi |

## Làm tiếp phim này

Có thể chạy lại "Tạo tự động": state.json giữ 8 clip đã xong, nên chỉ cần làm s04 và s10. Nhưng chừng nào chưa sửa mục 1, s10 sẽ lại đi đường `gflow image t2i` và dừng job lần nữa. Trước khi dựng, nên sửa tay câu thoại s07 về bản gốc và render lại s07.

## Đã sửa (22/11): mục ưu tiên 1–5

| # | Sửa ở đâu | Hành vi mới |
|---|---|---|
| 1 | `gflow.py` `Gflow.text_to_image`, `flow_ui.py` `run_i2i` / `build_image_prompt`, `pipeline.py` (khóa trình duyệt dùng chung `Gflow.ui_lock`) | `gflow image t2i` báo exit 23 thì ghi một dòng "⇄ … từ giờ ảnh chỉ từ mô tả chữ được tạo qua giao diện chat của Flow", và mọi ảnh không có nhân vật (bối cảnh, cảnh chỉ có tay…) đi chat Flow trong phần còn lại của lần chạy. Lệnh `flow_ui.py i2i` nhận yêu cầu không có `--ref`. Dry-run thật trên project của phim này: mở trang, gõ prompt OK (chưa gửi thật). |
| 2 | `flow_ui.py` `EXIT_FLOW_GLITCH = 43`, `is_glitch`; `autopilot.py` `_Softener.on_failed` / `requeue`, `_refusal_reason` | "Something went wrong" (không kèm audio hay chính sách) không còn bị coi là bị từ chối nội dung: gửi lại đúng prompt cũ sau 45 giây, tối đa 2 lần, không viết lại và không tạo lại keyframe. Lúc chờ đề nghị của Flow, thấy thông báo lỗi là thoát ngay thay vì chờ 240 giây. Lỗi cũ đã lưu (exit 41 với "Something went wrong") khi chạy lại cũng không bị viết lại. |
| 3 | `reviewer.py` `is_silent`, `quiet_audio_fix`, `SOFTEN_SYSTEM`; `autopilot.py` `_soften_one` | Cảnh không thoại bị "Audio generation failed": lần 1 chỉ đổi `video_shot.audio` thành "quiet ambient room tone only" bằng code, giữ nguyên hình và hành động (không gọi AI). Lần 2: AI được bảo rằng không ai nói, phần `audio` là thứ hay bị chặn. Hướng dẫn viết lại có thêm luật về `audio` (không tiếng thở, thở dài, nức nở, giọng thêm). |
| 4 | `reviewer.py` `keeps_dialogue(error, attempt)`, `soften_scene(script_scene=…)`, `SoftenFix.story_point_kept`, `spoken_words`; `autopilot.py` `_script_scene`, `dialogue_changed` | Lỗi audio lần 1 ở cảnh có thoại: giữ nguyên từng chữ, chỉ đổi cách nói (`voice_style` không "frail/weak/trembling"), âm thanh và hành động. Chỉ từ lần 2 mới được đổi chữ, và phải giữ nghĩa, giọng địa phương, tiểu từ. Mỗi lần viết lại có kèm đoạn của cảnh trong `script.final.md` để giữ điểm cốt truyện; AI trả `story_point_kept: false` thì bản đó bị bỏ. Thoại bị đổi được ghi "⚠ ĐỔI THOẠI ĐÃ DUYỆT: «cũ» → «mới»", lưu vào `autopilot.json` (`dialogue_changed`), nhắc ở cuối log và báo cho agent review cả phim. |
| 5 | `autopilot.py` `MIN_ASSEMBLE_SHARE = 0.7`, `_video_run`, `render_aborted` | Render dừng vì lỗi nghiêm trọng mà đã có ≥ 70% clip thì app không báo lỗi job nữa: ghi "⚠ Render video dừng giữa chừng (…) nhưng đã có X/N clip: dựng phim từ các cảnh đang có", bỏ qua các lượt render sau và chạy tiếp QC, dựng phim, ảnh bìa, review. Dưới 70% thì vẫn dừng như cũ. |

Test: `tests/test_monitor_fixes_1122.py` (17), thêm 3 test trang Flow giả trong `tests/test_flow_ui_queue.py`, sửa 2 test kiểm hành vi cũ (`test_soften_prompt_mentions_audio_only_for_audio_failures`, `test_soften_after_audio_failure_keeps_words_first_then_may_change_them`). Cả bộ: 1552 qua, chỉ còn `test_autopilot_basic_format_runs_vimax_and_openmontage_and_gates_bad_clip` lỗi sẵn (gọi API Anthropic thật, key không hợp lệ); `test_stitch_translates_and_writes_three_tracks` thỉnh thoảng lỗi khi chạy song song và qua khi chạy riêng.

Chưa làm: đổi cảnh không thoại sang Muse ở lần lỗi audio thứ 2 (mục B3.2), và mục 6–7 của bảng ưu tiên. Chưa thử với lần gửi thật.
