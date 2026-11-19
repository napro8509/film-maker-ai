# Nghiên cứu bổ sung quy tắc prompt (18/11/2026)

Nguồn: hướng dẫn chính thức của Google (Vertex AI, Gemini API, Google Cloud / Developers blog, Flow Help), blog của các công cụ AI video, và các bài tự thử của creator.

## Trạng thái áp dụng (19/11/2026)

Đã áp dụng cả nhóm A, B, C.

- **A, lựa chọn mới trong popup "Quy tắc prompt":**
  - A1, A5: thêm lựa chọn vào radio có sẵn (hướng mặt / dàn cảnh "Gần chính diện", chữ trên hình "Câu khẳng định").
  - A2–A4, A6–A9: checkbox mới, mặc định **tắt**.
- **B, đổi mặc định:** drama im lặng → "chỉ thở" (B1); đánh số ảnh tham chiếu + khoá danh tính (B2); ảnh bối cảnh / keyframe trước xếp cuối (B3); câu mở khi gửi video kèm ảnh tham chiếu (B4).
  - Mỗi mục là một checkbox / lựa chọn, tắt được trong popup.
  - Fingerprint không đổi, nên không cảnh nào phải render lại.
  - Bộ quy tắc cũ nằm ở `tests/legacy_rules.py`; các test golden chạy với bộ này để chứng minh nó vẫn cho ra đúng output cũ.
- **C:**
  - Đã làm:
    - C1: cảnh báo cảnh drama có ≥2 người nói.
    - C2: planner, reviewer và agent director dùng "1 người, 1 câu ≤12 từ".
    - C3: cảnh báo khi tả người nói trùng với người nghe.
    - C4: trường `voice` ở nhân vật, ô "Nhân vật nói" ở người nói của cảnh, planner được dặn điền cả hai.
    - C5: QC được dặn soi kỹ 1/3 dưới mọi khung hình.
    - C6: log cảnh báo khi keyframe có nhiều người hơn mức model giữ giống được.
    - C7: planner cấm người nổi tiếng có thật.
  - Chưa làm:
    - C5 bằng OCR thật: máy chưa có công cụ OCR, nên mới dặn AI QC soi kỹ.
    - C7 phần log `raiFilteredReason`: mã này chỉ có khi gọi API Vertex, app đang chạy qua giao diện Flow.
    - B2 trên đường gflow CLI (`gflow image i2i`): vẫn gửi prompt trơn như cũ (golden argv).

**Cần thử thực tế:** chưa có nguồn nào thử tiếng Thái / tiếng Việt. Nên thử A/B khoảng 10 clip cho B1, A1 và A9 trước khi tin hẳn.

**Độ tin cậy:**
- Phần lớn bằng chứng là hướng dẫn và ý kiến; chỉ có một bài tự thử có số liệu, khoảng 20 clip.
- **Không có nguồn nào thử tiếng Thái hoặc tiếng Việt.**
- Không đọc được Reddit vì công cụ bị chặn.
- Có nhãn **[thử A/B]** là nên thử 10 clip mỗi bản trước khi đổi mặc định.

---

## Thử A/B trên Muse (19/11/2026)

Phim `bua-gio-dinh-menh` (thoại tiếng Thái), 2 cảnh: s02 (hai người, người nói ở góc nghiêng) và s08 (cận một người). Mỗi ô chỉ có **1 clip**, nên đây là tín hiệu ban đầu, chưa phải kết luận. Keyframe giữ nguyên, chỉ đổi prompt video. Chấm bằng AI QC của app (whisper + xem khung hình) và đo giờ thoại.

| Clip | Biến thể | QC | Thoại (giây) | Ghi chú |
|---|---|---|---|---|
| s02 cũ | prompt lúc render phim | MINOR 7 | 1.5–8.4 | áo ต้น lệch màu |
| s02-A | mặc định hiện tại | PASS 8 | 1.7–8.6 | |
| s02-B | hướng mặt "gần chính diện" | **Muse từ chối** | – | Muse không làm được vì keyframe là góc nghiêng; mất 15 phút chờ |
| s02-C | chia nhịp + không tiếng Anh/giọng ngoài/nhạc | PASS 8 | 2.0–8.7 | hai người quay mặt vào nhau rõ hơn A |
| s08 cũ | prompt lúc render phim | PASS 8 | 1.4–9.1 | kính lúc cầm lúc đeo, thêm người lạ |
| s08-A | mặc định hiện tại | PASS 9 | 1.8–9.7 | |
| s08-B | "gần chính diện" | PASS 8 | 1.2–10.0 | mặt quay về ống kính hơn một chút ở cuối |
| s08-C | chia nhịp + 3 câu âm thanh | PASS 9 | 1.5–9.5 | |

Rút ra:
- Cả 5 clip đúng người nói, đúng lời tiếng Thái, không phụ đề, không tiếng Anh. Bộ mặc định mới không làm gì xấu đi so với clip cũ.
- **"Chia nhịp theo giây" không có tác dụng trên Muse**: đặt nói 0–7s rồi im 7–10s, nhưng thoại vẫn kéo tới 8.7–9.5s như khi không chia. Giữ tắt.
- **"Gần chính diện" nguy hiểm khi keyframe là góc nghiêng**: Muse từ chối hẳn ở s02; ở s08 chỉ khác chút ít. Chỉ nên bật khi dàn cảnh ảnh cũng là "gần chính diện" (để keyframe khớp).
- 3 câu âm thanh mới (không tiếng Anh, không giọng ngoài khung, không nhạc dưới thoại) vô hại; với Muse thì không thấy khác biệt vì clip A cũng đã sạch.
- Đã sửa: bản chia nhịp của cảnh một người không còn ghi "the others react silently".
- Mỗi clip Muse mất 65–92 giây. Muse hiện vẫn còn lượt (không phải đợi tới 24/11).

## 1. Những gì đang làm và được nguồn xác nhận

| Quy tắc hiện có | Nguồn xác nhận |
|---|---|
| Thoại dạng `… says: câu`, **không ngoặc kép** (ngoặc kép làm Veo in chữ) | Vertex AI Best practices |
| Ảnh → video: chỉ tả chuyển động, không tả lại mặt/áo/nền | Vertex AI Best practices |
| Gọi người bằng đặc điểm nhìn thấy ("the man in the red hat says:") | Vertex prompt guide, Replicate |
| Một người nói mỗi clip (khớp môi ~80% một người, <40% nhiều người) | eastondev (tự thử ~20 clip), Magic Hour, veo3ai |
| ≤12 từ / câu; thoại phải vừa 8 giây | Flow tips (blog.google), Replicate |
| Một cảnh mỗi clip, không cắt (`CLOSING`) | Vertex AI Best practices |
| Tả im lặng bằng âm thanh thật, không viết "no …" (`DEFAULT_SOUND`, `SILENT_RULE`, `I2V_SILENT_RULE`) | Veo 3.1 guide (Google Cloud), Vertex prompt guide |
| Người nghe môi khép, cười mím (`SILENT_BINDING`, `closed_smile`) | oakgen, dramapixel |
| Không nhạc trong clip, nhạc thêm lúc dựng | Magic Hour |
| `(no subtitles…)` sau câu thoại (`NO_TEXT_SHORT`) | Replicate (mẹo cộng đồng) |
| Tối đa 3 ảnh tham chiếu (`MAX_REFS`) | Flow Help (ingredients ≤3), Gemini docs (2.5 Flash ≤3) |
| Nhãn vai trò từng ảnh tham chiếu ("the first is X, keep X's face…") | Nano Banana Pro tips (blog.google), Google AI guide |
| Prompt ảnh dạng câu văn kể cảnh + thuật ngữ máy ảnh | Developers Blog "How to prompt Gemini 2.5 Flash Image" |
| Hook bằng khuôn mặt / câu sốc trong 3 giây đầu | invideo, insmind |

## 2. Phân xử 3 mâu thuẫn còn mở

| Mâu thuẫn | Kết luận từ nghiên cứu |
|---|---|
| **Người nói nhìn camera hay nghiêng 3/4** | **Gộp cả hai.** Khớp môi cần mặt to, gần chính diện, thấy rõ miệng (Magic Hour, Higgsfield). Cảm giác phim cần mắt không nhìn thẳng ống kính. Đề xuất lựa chọn mới: người nói ở gần nhất, mặt xoay nhẹ 3/4 về phía ống kính, mắt nhìn người nghe ngay sát cạnh ống kính; người nghe quay lưng (qua vai) hoặc nghiêng và nhòe. Không nguồn chính thức nào nói về lỗi "nhìn chằm vào ống kính": eyeline guide là của riêng mình. |
| **Cảnh im lặng có được cười không** | **Mặc định "chỉ thở" cho cả drama.** Miệng mở và hai người đối mặt kéo Veo bịa thoại (dramapixel, oakgen, log của chính mình). Chỉ cho cười khi hành động cần. |
| **Mỗi clip bao nhiêu câu** | **1 người, 1 câu, ≤12 từ**; chấp nhận 2 câu nếu cùng một người. Bỏ "2-3 câu / 8 giây" trong planner (lý do: <40% khi nhiều người nói, ~30% với ngôn ngữ không phải Anh). |

## 3. Đề xuất

### Nhóm A: thêm lựa chọn vào popup (không đổi mặc định, an toàn)

| # | Quy tắc | Bộ | Câu đề xuất | Nguồn |
|---|---|---|---|---|
| A1 | Hướng mặt **"gần chính diện, mắt nhìn người nghe sát ống kính"** (lựa chọn thứ 3 cho *Dàn cảnh khung đầu* và *Hướng mặt người nói*) | Ảnh + Video | "{speaker} is nearest the camera with the largest face, turned slightly three-quarter toward the lens, mouth clearly visible, eyes on {listener} just beside the lens; {listener} seen from behind the shoulder or in soft profile, lips closed." | Magic Hour, Higgsfield, tổng hợp |
| A2 | **Không nói tiếng Anh** | Video | Câu ngôn ngữ thêm "No English is spoken." và drama ghi ngôn ngữ ngay trong câu: "Lan says in Thai: …" | Gemini API: chỉ tiếng Anh được hỗ trợ đầy đủ |
| A3 | **Không lời ngoài khung** | Video | "No 'mhm', hums or off-screen voices." (thêm vào câu im lặng / cảnh thoại) | dramapixel |
| A4 | **Không nhạc dưới lời thoại** (mặc định tắt) | Video | "Audio: {ambient} under the dialogue; no background music." | Magic Hour, mẹo cộng đồng |
| A5 | Chữ trên ảnh: lựa chọn **"khẳng định"** | Ảnh | "Clean film frame: every sign, screen and surface is blank or unreadable." | Google: tả điều muốn có thay vì điều cấm |
| A6 | **Mở prompt ảnh bằng sản phẩm cần ra** (mặc định tắt) | Ảnh | "Create a photorealistic cinematic film still: …" | Nano Banana guide (Google Cloud), Runware |
| A7 | **Mỗi nhân vật xuất hiện một lần, đúng số người** | Ảnh | "Show exactly {k} people; each character appears only once." | Google AI Nano Banana Pro guide |
| A8 | Chân dung **"từ gối trở lên, mặt to"** thay cho toàn thân | Ảnh | "Medium-full shot from the knees up, face large and sharp, front view, neutral expression, plain light grey background, soft even light." | Gemini docs, Muse guide (chữ không giữ được mặt) |
| A9 | **[thử A/B] Nhịp theo mốc giây** thay cho khoá môi | Video | "[00:00-00:05] {speaker} says: {line}. [00:05-00:08] {speaker} stops talking, lips closed; {listener} reacts silently." | Veo 3.1 guide (timestamp) |

### Nhóm B: đổi mặc định (đổi prompt gửi đi, nên thử A/B trước)

| # | Thay đổi | Nguồn |
|---|---|---|
| B1 | Drama: cảnh im lặng mặc định "chỉ thở" thay vì "cho phép cười" | §2 |
| B2 | Nhãn ảnh tham chiếu đánh số và khoá danh tính: "Image {n} is {name}: keep {name}'s facial features, hairstyle and outfit exactly the same as Image {n}." | Google AI guide, Nano Banana Pro tips |
| B3 | Đặt ảnh có đúng tỉ lệ ở **cuối** danh sách tham chiếu và ghi tỉ lệ trong prompt ảnh (ảnh ra lấy tỉ lệ của ảnh cuối) | Gemini API docs, Developers Blog |
| B4 | Ảnh → video có ingredients: mở bằng "Using the provided images for {the girl in the yellow shirt}, {the kitchen}, …" | Veo 3.1 guide, Flow Help |

### Nhóm C: ngoài popup (kiểm tra, planner, QC, dữ liệu)

| # | Việc | Nguồn |
|---|---|---|
| C1 | **Cảnh báo cảnh drama có ≥2 người nói** (lỗ hổng đã biết) | eastondev, Magic Hour |
| C2 | Planner: bỏ "2-3 câu / 8 giây", chỉ còn 1 người, ≤12 từ | §2 |
| C3 | Cảnh báo khi **đặc điểm người nói trùng với người nghe** (mô tả giống nhau thì tráo thoại) | Replicate |
| C4 | **Giọng cố định cho từng nhân vật**: trường `voice` mới, chèn nguyên văn vào mọi clip ("{Name}'s voice: warm mid-pitch female, native Bangkok Thai accent, slow pace.") | Vertex Best practices, veo3ai, Kling/LTX gắn giọng vào nhân vật |
| C5 | QC: dò chữ ở 1/3 dưới khung (phụ đề Veo tự in) | MIT Technology Review, veo3ai |
| C6 | Giới hạn ảnh tham chiếu theo từng model: Nano Banana 2 tối đa 4 nhân vật, Pro tối đa 5 | Gemini API docs |
| C7 | Planner/reviewer: cấm tên hoặc ngoại hình người nổi tiếng có thật; log mã chặn `raiFilteredReason` | Vertex Responsible AI |

### Không nên làm

- **Chuyển prompt sang JSON**: chỉ một bài tự báo cải thiện mà không công bố cách làm; Morph Studio phản bác. Giữ văn xuôi có nhãn (`Audio:`).
- **Bỏ quy tắc trẻ em**: Google không công bố cách lách nào; bộ lọc phụ thuộc cài đặt tài khoản. Giữ `CHILDREN_GUIDE` / `child_safe` như cách giảm rủi ro rút từ kinh nghiệm.

## Nguồn chính

- Vertex AI Veo best practices: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/best-practice
- Vertex AI Veo prompt guide: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/video-gen-prompt-guide
- Ultimate prompting guide for Veo 3.1: https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1
- DeepMind Veo prompt guide: https://deepmind.google/models/veo/prompt-guide/
- Flow Help (ingredients): https://support.google.com/flow/answer/16353334
- Flow video tips: https://blog.google/innovation-and-ai/products/flow-video-tips/
- How to prompt Gemini 2.5 Flash Image: https://developers.googleblog.com/en/how-to-prompt-gemini-2-5-flash-image-generation-for-the-best-results/
- Ultimate Nano Banana prompting guide: https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-nano-banana
- Nano Banana Pro tips: https://blog.google/products/gemini/prompting-tips-nano-banana-pro/
- Nano Banana Pro prompting guide (Google AI): https://dev.to/googleai/nano-banana-pro-prompting-guide-strategies-1h9n
- Gemini image responsible AI: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/gemini-image-responsible-ai
- Replicate, prompting Veo 3: https://replicate.com/blog/using-and-prompting-veo-3
- Magic Hour, speaking in Veo 3: https://magichour.ai/blog/how-to-prompt-for-speaking-in-veo-3
- eastondev, Veo 3 audio tests: https://eastondev.com/blog/en/posts/ai/20251207-veo3-audio-generation-guide/
- dramapixel, unwanted dialogue: https://dramapixel.ai/blogs/stop-veo-3-unwanted-dialogues
- oakgen, wrong character speaking: https://oakgen.ai/blog/ai-video-wrong-character-speaking
- veo3ai, two-character dialogue: https://www.veo3ai.io/blog/veo-3-two-character-dialogue-guide-2026
- MIT Technology Review, Veo 3 subtitles: https://www.technologyreview.com/2025/07/15/1120156/googles-generative-video-model-veo-3-has-a-subtitles-problem/
- Kling 3.0 lip-sync: https://kling.ai/blog/kling-video-3-omni-native-lip-sync-audio-guide
- Hedra speaker selection: https://hedra.com/docs/pages/studio/content-creation/speaker-selection
- Muse prompting (Runware): https://runware.ai/docs/models/meta-muse-image/guides/prompting.md
- invideo, AI micro-drama: https://invideo.io/blog/how-to-make-ai-micro-drama/
