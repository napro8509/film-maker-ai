# Kiểm kê quy tắc làm phim (hiện trạng 18/11/2026)

Danh sách này tổng hợp từ việc đọc code. Số dòng có thể lệch vài dòng.

## Đã gom về `filmmaker/rules.py` (bước 1, không đổi nội dung)

Những thứ sau giờ chỉ được viết **một lần** trong `filmmaker/rules.py`. Các module khác import từ đó, và tên cũ (`storyboard.NO_TEXT`, `i2v_prompt.MAX_WORDS`, `formats.SILENT_RULE`…) vẫn dùng được. Chạy `filmmaker rules` để xem danh mục (chủ đề, lý do, nơi dùng).

- **Giới hạn**: MAX_WORDS, DIALOGUE_MAX, SPEAKER_LOOK_WORDS, MAX_REFS, START_FRAME_CHARS, START_MATCH_MIN (qc dùng chung; `flow_ui.py` giữ bản sao vì chạy độc lập, có test bắt khớp)
- **Câu chữ trên hình**: NO_TEXT, NO_TEXT_IMAGE, NO_TEXT_SHORT
- **Ai nói**: DIALOGUE_RULE (+ bản cũ), DIALOGUE_TURNS, BACKGROUND_NO_WORDS, AUDIO_DIALOGUE_RULE, SILENT_BINDING
- **Im lặng**: NO_DIALOGUE_RULE (+ bản cũ), SILENT_SCENE_RULE, I2V_SILENT_RULE, SILENT_RULE (+ bản cũ), VOICEOVER_RULE
- **Cú máy / âm thanh**: CLOSING, DEFAULT_SOUND
- **Dặn LLM dùng chung**: CHILDREN_GUIDE (planner + storyboard reviewer, trước đây chép 2 lần)

`tests/test_rules.py` bắt: mọi hằng số đều có trong danh mục, không module nào viết lại câu quy tắc thay vì import, và bản sao trong `flow_ui.py` khớp.

**Chưa gom (cần quyết định trước vì sẽ đổi prompt):**
- Các đoạn văn trong prompt LLM dài (planner SYSTEM, basic_planner SYSTEM, reviewer, qc), vì giữa các file chúng **gần giống nhưng không giống hệt** (xem mục 17-18).
- Câu có ghép tên nhân vật ngay trong hàm (`speaker_cues`, `keyframe_staging`, template i2v).
- Câu chèn trong `muse_ui.py` (chạy độc lập).

## 0. Bức tranh chung: một quy tắc đang sống ở tối đa 5 tầng

| Tầng | File | Vai trò |
|---|---|---|
| **A. Prompt lập kịch bản** | `planner.py` SYSTEM (luồng **drama cũ**, đã deprecated), `basic_planner.py` SYSTEM + `formats.py` (luồng **mới**: feature/series/short_film/clip), `video_analysis.py`, `clone.py` | Dặn LLM khi viết storyboard |
| **B. Kiểm & tự sửa sau khi lập** | `basic_planner.py:217-289` (regex, gửi LLM sửa 1 lần), `planner.py:72-122` | Bắt lỗi trong plan rồi sửa |
| **C. Tự chèn vào prompt lúc render** | `storyboard.py` (hằng số + `speaker_cues`, `keyframe_staging`…), `i2v_prompt.py` (template, `veo_safe`, `child_safe`…), `pipeline.py`, `muse_ui.py` | Câu chữ code tự thêm / tự viết lại trước khi gửi Flow/Muse |
| **D. Cảnh báo / validate** | `storyboard.py` `dialogue_warnings`, validators; `i2v_prompt.warnings`, `axis_breaks` | Hiện trong editor và `filmmaker check` |
| **E. Review, QC, soften, thử lại** | `reviewer.py`, `qc.py`, `autopilot.py`, `editor.py`, `flow_ui.py`, `muse_ui.py` | Chấm storyboard/keyframe/clip, làm nhẹ prompt bị chặn, retry/dừng |
| **F. Agent đội làm phim** | `.claude/agents/*.md`, `.claude/commands/make-film.md` | Luồng drama cũ, không có quy tắc lip-sync nào |

Hai luồng chạy song song với quy tắc khác nhau: **drama** (planner.py + `dialogue` list) và **basic** (basic_planner.py + `video_shot` có cấu trúc). Nhiều mâu thuẫn bên dưới là do hai luồng này.

Ký hiệu loại: **[P]** chỉ dặn LLM · **[C]** code tự chèn · **[F]** code tự sửa/viết lại · **[V]** chặn lỗi · **[W]** cảnh báo · **[R]** tiêu chí review/QC · **[S]** dừng.

---

## 1. Thoại & người nói

| Quy tắc | Ở đâu | Loại |
|---|---|---|
| Thoại chỉ nằm trong `dialogue` / `speaker`, không bao giờ trong `prompt`/`image_prompt` | planner:50, director.md:22 | P |
| **Mỗi cảnh chỉ 1 người nói**; đối đáp chia qua nhiều cảnh (shot / reverse-shot) | planner:50, basic_planner:58-61, formats:55-58, reviewer:111-112 | P, R |
| Luồng basic: schema chỉ có 1 ô `speaker`, người khác vào `listeners` | i2v_prompt:472, basic_planner:198-206 | V (cấu trúc) |
| Luồng drama: **không có kiểm tra** khi 1 cảnh có ≥2 người nói | storyboard:995 | (thiếu) |
| Mỗi dòng 1 người, không nói chồng, không ai lặp/nói thay (DIALOGUE_TURNS, DIALOGUE_RULE) | storyboard:41, 341, 988 | C |
| Không lặp thoại trong cảnh / giữa các cảnh / giữa các phần phim dài | planner:51, 113-122, 201; storyboard:973-977, 1023-1029 | P, F, W |
| Độ dài: drama "2-3 câu/8s" **và** "tối đa 2 câu, 6-12 từ"; basic "1 câu ≤12 từ" | planner:50, basic_planner:59, i2v:24, 581 | P, F, W |
| Tối đa 12 câu thoại/cảnh (DIALOGUE_MAX) | storyboard:337, 427 | V |
| Câu thoại không ngoặc kép, không viết tắt (do not) | planner:52, basic_planner:59, i2v:38-83 | P, F |
| Mỗi người có giọng riêng trong `delivery` | planner:50 | P |
| Người nói phải có trong danh sách nhân vật / được chọn vào cảnh | storyboard:1019-1022; reviewer:113-116, 390-440 | W, F |
| Người có mặt nhưng không thoại → "X stays silent" | storyboard:989-992 | C |
| Basic: narration = đúng câu của speaker; không speaker thì xoá narration | storyboard:584-599, basic_planner:180-182, 257-260 | F |
| Basic: người nói cần visual_tag + vị trí frame left/right | i2v:583-615 | W |
| Phim "Không thoại" → bỏ hết thoại | storyboard:961-963, 1030 | F, W |
| QC: đúng câu, đúng ngôn ngữ, mỗi câu 1 lần, 1 người 1 lúc; thoại thừa ở cảnh im lặng là FAIL | qc:76-81, 275-299 | R |

## 2. Khớp môi / ai đang nói

| Quy tắc | Ở đâu | Loại |
|---|---|---|
| Người nói mặt thấy rõ, close-up / MCU | planner:50 | P |
| Người khác môi khép, không há miệng/cười/la trong cảnh có thoại | basic_planner:47, 76-79, 103, 238-240 | P, F |
| `speaker_cues` (drama): "Who is who" + "Only X speaks, turns face toward the camera" + người khác ngậm miệng | storyboard:757-787 | C |
| `keyframe_staging` (drama): người nói đầu tiên mặt to nhất, **nhìn về camera** | storyboard:789-818 | C |
| `first_frame_staging` (basic): người nói **nghiêng 3/4 về phía người kia**, mọi người môi ngậm | i2v:308-318 | C |
| Khoá môi sau câu nói ("closes mouth, maintaining eyeline") | i2v:517, 525 | C |
| SILENT_BINDING cho listener ("lips closed, nodding slightly"; bỏ nodding khi không hợp) | i2v:25, 175-180 | C, F |
| grin/laugh của listener → "smiling with closed lips" | i2v:159-170 | F |
| BACKGROUND_NO_WORDS: hậu cảnh chỉ reo/rì rầm, không nói câu | storyboard:339, 786 | C |
| Audio cảnh có người nói không được có tiếng người khác (cười, thở hắt) | basic_planner:78-79, 104, 241-243; i2v:594 | P, F, W |
| Basic dialogue: "Only one person speaks; everyone else keeps lips closed" | formats:59 | C |
| Review keyframe: người nói là mặt nổi bật nhất, **nhìn máy**; listener to hơn / há miệng → REVISE | reviewer:147-161 | R |
| QC: 6 khung đúng lúc có tiếng; người khác mấp máy là FAIL "sai người nói" | qc:82-91, 240-264 | R |
| Agent .md: **không có quy tắc lip-sync nào** | agents/* | (thiếu) |

## 3. Nhân vật, khuôn mặt, ảnh tham chiếu

| Quy tắc | Ở đâu | Loại |
|---|---|---|
| Ngoại hình cố định, một bộ đồ, đồ đặc trưng; nhân vật dễ phân biệt | planner:39, basic_planner:89-90, character-designer.md:13-21 | P |
| Mô tả không so sánh với người khác; không "identical twins" | basic_planner:35-37, 90, 232-234; i2v:359-367 | P, F |
| Tên ngắn Latin không dấu; tên duy nhất; nhân vật của cảnh phải tồn tại | screenwriter.md:17; storyboard:601-622 | P, V |
| Tool tự chèn "Name: description" theo tên tìm thấy | storyboard:693-712, 955-959 | C |
| Tối đa 3 nhân vật/khung | screenwriter.md:23, director.md:43, storyboard-reviewer.md:13 | P |
| MAX_REFS=3; clip từ ảnh đầu tối đa 2 nhân vật (ưu tiên người nói) | storyboard:24-31, 1063-1090; pipeline:1068-1077 | F |
| Nhãn ảnh tham chiếu "keep X's face, hair and clothes exactly" | pipeline:196-206, muse_ui:269-286 | C |
| Trẻ em: không tuổi cụ thể, không chi tiết cơ thể; girl → schoolgirl (`child_safe`) | planner:53-59, basic_planner:91-93, i2v:370-455, reviewer:123-129 | P, F, R |
| Chân dung: drama toàn thân nhìn camera nền xám; basic cảnh đời thường | storyboard:886-914 | C |
| Không tạo lại chân dung đã dùng trong clip đã render | autopilot:325-337 | F |
| Phân tích/clone: không nêu tên hay nhận diện người thật | video_analysis:115-117, 184; clone:168-170 | P |

## 4. Máy quay, hướng nhìn, trục 180°

| Quy tắc | Ở đâu | Loại |
|---|---|---|
| Drama: camera có shot/angle/**đúng 1 movement**/lens; không lặp trong prompt | planner:32-35, director.md:29-37 | P |
| Đổi cỡ cảnh giữa cảnh liền nhau; cảm xúc → cận; thấp = quyền lực, cao = yếu thế | planner:38, director.md:51-54 | P |
| Trục 180° trong hội thoại | planner:38, basic_planner:71-72, 263-266; i2v:546-567 | P, F, W |
| Basic cảnh thoại: camera của người dùng bị thay bằng khối khoá (85mm CU / 50mm two-shot / OTS) | i2v:214-226 | F |
| EYELINE LOCK: frame left/right, head 3/4 hoặc nghiêng, **không nhìn ống kính** | basic_planner:62-70, i2v:229-251 | P, C |
| `veo_safe`: xoá "camera, lens, viewer, cinematic…", "look at camera" → 3/4 profile | i2v:265-301 | F |
| Không dùng "his left", "eyes" trong cảnh thoại | basic_planner:74-75, 249-250; i2v:304, 610-614 | P, F, W |
| "Single continuous shot, no scene cuts" | i2v:29, 346; reviewer:105 | C, R |
| Khung 9:16: mặt ở giữa-trên, tránh 1/5 dưới và mép phải | planner:37, basic_planner:87-88, reviewer:144-145 | P, R |

## 5. Hành động

| Quy tắc | Ở đâu | Loại |
|---|---|---|
| Một hành động chính/cảnh, dựng được trong ≤8s | planner:30, screenwriter.md:19, director.md:20 | P |
| Basic: 1-2 nhịp, không quá 3 hành động liên tiếp; ≥2 "then/finally" → cảnh báo | basic_planner:45-47; i2v:599-601 | P, W |
| **Một người hoạt động/clip**, người kia chỉ phản ứng; không hai người cầm cùng vật | basic_planner:48-51, 229-231; i2v:591-593 | P, F, W |
| Tránh đông người, tay thao tác vật nhỏ, đánh nhau, vũ đạo | screenwriter.md:22, reviewer:107-108 | P, R |
| Video fields chỉ tả chuyển động, không tả lại mặt/áo/nơi | basic_planner:38-39 | P |
| `environment` thường trống, không nói ánh sáng, không "matching the starting frame" | basic_planner:52-54, 235-237; i2v:32, 349 | P, F |

## 6. Cách viết prompt

| Quy tắc | Ở đâu | Loại |
|---|---|---|
| Prompt viết tiếng Anh (trừ lời nói, narration, field `_vi`) | planner:29, basic_planner:27, director.md:42 | P |
| **Tên riêng**: drama gọi tên + mô tả; basic **cấm tên** trong video fields, dùng vị trí + ngoại hình; ≥2 người không dùng he/she | planner:39; basic_planner:40-42, 222-227; i2v:602-619 | P, F, W |
| Không thông số kỹ thuật (16:9, giây, 4k) trong prompt | basic_planner:33-34; i2v:47-78 | P, F |
| Style chung ghi 1 lần trong `style`, tự chèn "Style: …" | planner:46, basic_planner:94; storyboard:710 | P, C |
| Locations khai báo 1 lần, tự chèn "Setting: …" | planner:40, basic_planner:28-31; storyboard:686 | P, C |
| Không tả cái không có (câu phủ định) | basic_planner:56-57, 244; i2v:597 | P, F, W |
| Muse: tiền tố "Make exactly 1 image/video now…" | muse_ui:263-291 | C |
| Sửa theo review: chỉ sửa cảnh/trường bị nêu | basic_planner:106-108, reviewer:133-135, director.md:56 | P, F |

## 7. Chữ trên hình

| Quy tắc | Ở đâu | Loại |
|---|---|---|
| Không caption/phụ đề/biển chữ/watermark trong hình | planner:47, basic_planner:86, director.md:44, reviewer:117 | P, R |
| 4 câu chèn khác nhau: NO_TEXT (drama), NO_TEXT_IMAGE, NO_TEXT_SHORT, Muse "no text, no watermark" | storyboard:33; i2v:26-28; muse_ui:272 | C |
| Không ngoặc kép quanh lời (Veo in chữ) | planner:52, storyboard:967 | P, C |
| Ảnh bìa: chừa vùng tối 1/3 dưới, tiêu đề in sau | storyboard:920-927 | C |
| QC / review keyframe: có chữ là FAIL / REVISE | qc:75, reviewer:142, 171-175 | R |

## 8. Âm thanh & nhạc

| Quy tắc | Ở đâu | Loại |
|---|---|---|
| **Không nhạc trong clip**, nhạc nền thêm một lần lúc dựng | planner:45, basic_planner:55-57, formats:47-49 | P, C |
| 7 câu "im lặng" khác nhau: NO_DIALOGUE_RULE, SILENT_SCENE_RULE, I2V_SILENT_RULE, SILENT_RULE, voiceover rule, SILENT_BINDING, "Everyone keeps lips closed" | storyboard:47-58; formats:49, 71; i2v:25, 488 | C |
| Cảnh im lặng trong phim có thoại → chèn luật im lặng (Veo bịa thoại ~½ số cảnh) | storyboard:52-58, 820-844 | C |
| Basic: "Audio: <âm thật hoặc quiet ambient room tone>" | i2v:31, 345 | C |
| QC: đo dB, whisper + VAD, bỏ transcript bịa | qc:34-37, 161-232 | R |
| Editor: duck nhạc khi có thoại, loudnorm -14 LUFS, ≤12 SFX/phim | editor:172, 251-261, 337-343, 475-496 | F |

## 9. Ngôn ngữ & phụ đề

| Quy tắc | Ở đâu | Loại |
|---|---|---|
| DIALOGUE_RULE "Spoken language: X. Each line said only by its character" (drama) | storyboard:39-42, 870-883 | C |
| Basic: ngôn ngữ nằm trong động từ nói "speaking in Thai…" | i2v:503-504 | C |
| Luôn 3 track phụ đề vi/th/en; thiếu thì tự dịch | planner:48, 590-614; storyboard:81-83, 1190-1215 | P, F, W |
| Tự thêm dấu tiếng Việt khi ≥50% câu thiếu dấu | planner:383-480 | F |
| "Không phụ đề" → xoá narration/subtitles | planner:357-360; storyboard:580 | F |
| Phụ đề chỉ hiện trong cảnh, tránh chuyển cảnh | editor:404-415 | F |

## 10. Thời lượng

| Quy tắc | Ở đâu | Loại |
|---|---|---|
| Clip 4-8s, làm tròn lên thời lượng hợp lệ | planner:28, 366; basic_planner:348; autopilot:270 | P, F |
| Mặc định 6-10 cảnh × 8s | screenwriter.md:15, make-film.md:31 | P |
| Narration / caption ≤12 từ | basic_planner:83, formats:70-77, i2v:587 | P, F, W |
| Phim dài: phần ≤15 cảnh, tổng ≤80 | planner:160-161 | V |
| Remake: không dài hơn video gốc, cảnh có thể 2s (cắt lúc dựng) | video_analysis:181-183, 1180 | P, F |
| Editor: giữ ≥~2.5s (prompt) nhưng code MIN_KEEP=1.0 | editor:54-55, 548 | P, F |

## 11. Liên tục giữa các cảnh

| Quy tắc | Ở đâu | Loại |
|---|---|---|
| Cảnh bắt đầu từ trạng thái cuối cảnh trước; nhảy thời gian → `continue_previous: false` | planner:42-43, director.md:55 | P |
| Ảnh tham chiếu ô 1 = keyframe cảnh trước hoặc ảnh bối cảnh | storyboard:1092-1103; pipeline:200 | C |
| Ảnh bối cảnh trống người cho mỗi location | storyboard:675-684; autopilot:382-424 | C, F |
| Review keyframe: lỗi liên tục **luôn REVISE** | reviewer:146, 161-165 | R |

## 12. Chính sách, trẻ em, người thật, soften

| Quy tắc | Ở đâu | Loại |
|---|---|---|
| Flow từ chối: xúc phạm, đe doạ, la hét, đánh nhau, máu, vũ khí, trẻ em gặp nạn → diễn nhẹ | planner:60-66, basic_planner:97-98, reviewer:118-122 | P, R |
| Trẻ em: danh sách cảnh cấm (tắm, giường cận, khóc to…) | planner:53-59, reviewer:123-129 (gần nguyên văn), basic_planner:91-93 (rút gọn) | P, R |
| Người thật: không tên thật, cấp bậc, "true story" | reviewer:130-132, 208-214 (**planner không có**) | R, F |
| Soften theo loại lỗi (blocked, prominent, harmful, audio failed, policy+trẻ em) | reviewer:189-234; autopilot:583-623 | F |
| Re-stage khi bị chặn lặp lại (đổi góc, ít người) — chỉ chạy qua web | reviewer:226-234; server:2077 | F |

## 13. Hook & cấu trúc truyện

| Quy tắc | Ở đâu | Loại |
|---|---|---|
| s01 vào xung đột trong 2-3s đầu; stakes ~giây 7; twist giữa; payoff cuối | planner:36, formats:41-42, reviewer:104, screenwriter.md:16 | P, R |
| Format: feature 3 hồi, series kết treo, short_film trọn vẹn, clip 1 tình huống + twist | formats:18-44 | P |
| `content_direction` mỗi cảnh (mục đích, cảm xúc, cách diễn) | planner:49, director.md:21 | P, C |

## 14. Dựng phim

| Quy tắc | Ở đâu | Loại |
|---|---|---|
| Cut là chính; crossfade/fadeblack/fadewhite theo ý nghĩa; cả phim 2-3 kiểu | editor:553-563, editor.md:17-19 | P |
| Không cắt vào thoại hay hành động chính (code chỉ đảm bảo khi có screen_time) | editor:547, 278-331 | P, F |
| Trim khung đen/đứng, giới hạn trim, fade in/out | editor:54-55, 243-334, 374 | F |
| Không AI → crossfade 0.5s mọi chỗ | editor:264-275 | F |

## 15. Thử lại, dừng, credit

| Quy tắc | Ở đâu | Loại |
|---|---|---|
| Chỉ render tốn credit khi người dùng xác nhận; không bao giờ `--retry-unknown` | producer.md:18-26, make-film.md:14, 51-58 | P |
| Mã fatal {8,23,36-39,12,14} dừng cả lượt | gflow:34, pipeline:594, autopilot:183 | S |
| Retry tạm thời ≤2 lần, chờ 30·n s; ảnh 2 lần | pipeline:468-480, autopilot:48, 362-437 | F |
| Bị chặn → soften 1 lần rồi gửi lại | autopilot:583-623 | F |
| QC chỉ gắn cờ, không bao giờ tự render lại | qc:11, autopilot:626 | R |
| Khớp khung đầu <0.75 → FAIL (trừ R2V) — khai báo 2 nơi | flow_ui:873, qc:40 | R, S |
| Muse: chỉ nhận clip giữa bong bóng của mình; từ chối → 41, lỗi → 42 | muse_ui:204-335, 425-485 | R, S |
| Keyframe review 2 vòng, vòng cuối chấp nhận mọi verdict | autopilot:59, 454-482 | F |

## 16. Ngưỡng duyệt & quy trình agent

| Quy tắc | Ở đâu | Loại |
|---|---|---|
| Kịch bản PASS khi mọi điểm ≥7 | script-reviewer.md:18 | R |
| Hình ảnh PASS khi giống nhân vật ≥7, "không lỗi nặng" (chưa định nghĩa) | visual-reviewer.md:35 | R |
| Storyboard-reviewer: không có ngưỡng điểm | storyboard-reviewer.md | (thiếu) |
| Mỗi công đoạn sửa tối đa 2 vòng rồi hỏi | make-film.md:39 | P |

---

## 17. Trùng lặp lớn nhất (cùng một ý, viết ở nhiều chỗ)

1. **Không chữ trên hình**: ~12 chỗ, 4 câu chèn khác nhau.
2. **Im lặng / không thoại**: 7 câu chèn khác nhau.
3. **Một người nói / cảnh**: planner, basic_planner, formats, reviewer, qc, storyboard (3 câu chèn chồng nhau trong cùng một prompt drama: DIALOGUE_RULE + DIALOGUE_TURNS + BACKGROUND_NO_WORDS).
4. **Luật trẻ em**: planner:53-59 ≈ reviewer:123-129 (chép gần nguyên văn), bản rút gọn ở basic_planner, thêm `child_safe` và 2 hàm chân dung trẻ em khác nhau.
5. **Nội dung Flow từ chối**: planner, basic_planner, reviewer (2 lần), agent .md.
6. **Không nhạc**: 6 chỗ.
7. **Ngưỡng 0.75 + miễn trừ R2V**: flow_ui và qc.
8. **Giới hạn ảnh tham chiếu**: 5 chỗ (MAX_REFS, START_FRAME_CHARS, ảnh bìa, `_cap_cli_refs`, `ref_caps`).

## 18. Mâu thuẫn cần quyết

| # | Mâu thuẫn | Bên A | Bên B |
|---|---|---|---|
| 1 | **Người nói nhìn camera hay không** | Drama: "turns the face toward the camera" (storyboard:779, 800), review keyframe đòi nhìn máy (reviewer:147-152) | Basic: cấm nhìn ống kính, xoá cả chữ "camera" (i2v:229-301) |
| 2 | **Tên riêng trong prompt video** | Drama: gọi tên + mô tả (planner:39) | Basic: cấm tên (basic_planner:40-42); clone dùng tên cast mới |
| 3 | **Số câu/từ mỗi cảnh** | planner:50 "2-3 câu/8s" | Cùng dòng: "tối đa 2 câu, 6-12 từ"; basic "1 câu ≤12 từ"; reviewer:110 "2-3" |
| 4 | **Cười trong cảnh im lặng** | SILENT_SCENE_RULE cho phép cười, ngân nga | I2V_SILENT_RULE chỉ thở/khóc nhẹ; i2v đổi laugh → cười mím; i2v:594 cảnh báo tiếng cười |
| 5 | **Câu phủ định** | Basic cấm "no music / no dialogue" | NO_TEXT, NO_DIALOGUE_RULE, SILENT_SCENE_RULE, NO_TEXT_SHORT, clone "No speech, no subtitles" đều phủ định |
| 6 | **Thông số kỹ thuật** | i2v xoá giây/tỉ lệ khỏi prompt | Muse thêm "aspect ratio X, N seconds long" |
| 7 | **`environment` có nói ánh sáng không** | Schema basic_planner:127 "lighting continuity" | SYSTEM basic_planner:52-54 + code cấm |
| 8 | **Biểu cảm mạnh vs môi khép** | Planner khuyến khích phản ứng mạnh, hook "striking face reaction" | Cảnh thoại: mọi người môi khép, không cười/há miệng |
| 9 | **Delivery ví dụ** | planner:50 "whispering, holding back tears", hook "provocative line" | Soften cấm khóc to, gay gắt (reviewer:203, planner:60-66) |
| 10 | **Editor trim** | Prompt "giữ ≥2.5s", fade hẹp, "cut là xương sống" | Code MIN_KEEP=1.0, fade rộng hơn, auto_plan crossfade mọi chỗ |
| 11 | **Thiếu máy quay** | i2v:575 cảnh báo thiếu camera | Cảnh thoại basic bỏ camera của người dùng (i2v:214-226) |
| 12 | **`veo_safe` phạm vi** | Comment i2v:9-12 nói chỉ cho cảnh thoại | Thực tế chạy cả cảnh im lặng |

## 19. Lỗ hổng (quy tắc có ý định nhưng không được áp)

- Drama: **không cảnh báo khi một cảnh có ≥2 người nói**.
- Prompt basic cảnh im lặng đi đường `_silent_shot`, **bỏ qua `for_flow`** → không có câu không-chữ, không qua `child_safe` (storyboard:850-851).
- Code basic chỉ kiểm "≥2 người hoạt động" và từ cấm "camera" ở một loại cảnh (thoại / không thoại), trong khi luật áp cho mọi clip.
- Muse: prompt ghi "exact first frame" nhưng không kiểm khung đầu; QC lại miễn trừ mọi cảnh có ref.
- Muse từ chối/lỗi không bao giờ được soften, chỉ gửi lại y nguyên.
- Cảnh bị chặn mà soften thất bại có thể bị gửi lại y nguyên (tốn credit) ở bước "interrupted" (autopilot:557-565, 609-611).
- `rerender_budget` khai báo ở CLI/server nhưng không dùng; `review_clips` (reviewer:167-175) là code chết.
- Keyframe review: "lỗi liên tục luôn REVISE" nhưng vòng cuối chấp nhận mọi verdict, ảnh làm lại không ai xem lại.
- Planner không có luật người thật / "prominent people" dù reviewer có.
- Storyboard reviewer không kiểm nhạc trong prompt, ngoặc kép, gọi người theo cái máy thấy.
- Toàn bộ agent .md mô tả luồng drama cũ, không có lip-sync, không ghi chú deprecated.

## 20. Gợi ý hướng quy hoạch (để bàn)

1. **Một nguồn quy tắc duy nhất**: `filmmaker/rules.py` đã có (bước 1: giới hạn, câu chèn, đoạn trẻ em). Bước tiếp: chuyển dần các đoạn trong prompt LLM vào đây sau khi chốt các mâu thuẫn ở mục 18, rồi để planner, reviewer, qc ghép prompt từ cùng các đoạn đó.
2. **Chốt một luồng**: drama đã deprecated. Hoặc gỡ hẳn, hoặc cho drama dùng cùng câu chèn với basic. Phần lớn mâu thuẫn (#1, #2, #4, #5) biến mất khi chỉ còn một luồng.
3. **Gộp câu chèn**: 1 câu không-chữ, 1 câu im lặng, 1 câu "chỉ X nói"; thống nhất kiểu khẳng định (không phủ định).
4. **Một bảng số liệu**: 12 từ, 2 nhân vật ref, 0.75, 8s, 2 vòng… để ở một chỗ.
5. **Agent .md**: tham chiếu tới nguồn quy tắc thay vì chép lại, hoặc đánh dấu deprecated.
