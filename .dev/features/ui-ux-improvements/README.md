# Cải thiện UI/UX cho Film Maker — tập hợp task đề xuất

> Mục tiêu: UI/UX hiện tại còn rối và chưa tối ưu. File này là **kết quả nghiên cứu** (đọc toàn bộ
> `filmmaker/web/static/*`, `filmmaker/cli.py`, `filmmaker/web/server.py`, `README.md`) và một **danh
> sách task mới** để bạn xem/review rồi chọn giao cho `/dev-team`. Mỗi task nằm ở một file riêng trong
> thư mục này, tự chứa đủ: vấn đề (kèm bằng chứng `file:line`), mục tiêu, giải pháp đề xuất, tiêu chí
> chấp nhận, file cần sửa, công sức/tác động, rủi ro.
>
> **Chưa động vào code sản phẩm** — đây là đề xuất để review. Sau khi bạn duyệt, mỗi file có thể đưa
> thẳng vào quy trình spec → design → dev.

## Phương pháp nghiên cứu

- Đọc code giao diện web (`app.js` ~3938 dòng, `board.js` ~1222 dòng, `app.css` ~672 dòng, `board.css`,
  `progress.js`, `index.html`) để dựng lại kiến trúc thông tin, luồng người dùng, kho modal, các form.
- Đọc `cli.py` và `web/server.py` (~70 endpoint) để đối chiếu luồng CLI ↔ web, luồng job nền, và các
  điểm lỗi (auth, credit, model ngừng, render bị ngắt).
- Mọi phát hiện đều có trích dẫn `file:line` và đã **đối chiếu lại với code thực tế** ở bản sửa này để
  tránh chẩn đoán sai hoặc đề xuất trùng tính năng đã có.

## Hiện trạng (tóm tắt)

Nền tảng **đã tốt**: có design token (`app.css:4-67`), dark mode, ARIA khá đầy đủ (90+ thuộc tính),
phím tắt (ESC, Ctrl+S), auto-save có debounce, job nền có polling + progress theo từng item. Ngoài ra
nhiều thứ **đã được triển khai** — các task dưới đây **chỉ nhắm phần còn thiếu**, không làm lại:

- Tab theo đúng thứ tự quy trình (`app.js:860-871`); màn rỗng đã có hướng dẫn 4 bước (`app.js:893-905`).
- Nhóm camera đã gom + ẩn 5/9 field trong `<details>` (`app.js:1077-1100`); chế độ cơ bản/nâng cao `isBasic` (`app.js:1196`).
- Final đã có `subTrackSwitch` chọn 1 ngôn ngữ + đánh dấu câu chưa dịch (`app.js:2038-2043`, `2155-2166`).
- Model-change/duration-fix 1 chạm đã có (`app.js:995-1004`, `2820-2929`, `2970-3090`); `prettyError` dịch một phần lỗi (`app.js:1006-1011`).
- `grid3` đã có rule mobile (`app.css:474`, `491`); board đã dùng Pointer Events + `touch-action:none` (`board.js:412-414`, `board.css:57`); focus ring board đã có (`board.css:95`).
- Settings đã dùng `grid2/grid3` cho field ngắn và mật khẩu (`app.js:3819`, `3823`, `3874`, `3877-3880`).

**Không cần đập đi xây lại.** Vấn đề còn lại là **mật độ thông tin, tiết lộ dần, nhất quán, và dẫn dắt
người dùng** ở đúng vài chỗ.

### Những điểm đau chính (đã kiểm chứng trong code)

| # | Điểm đau (đã thu hẹp về phần còn thiếu) | Bằng chứng |
|---|----------|-----------|
| 1 | Dự án nửa chừng không có "đang ở bước nào / việc tiếp theo"; `/estimate` **không** trả credit | `app.js:860-871`, `910-962`; `server.py:1727-1752` |
| 2 | Chế độ nâng cao: cả khối camera/thoại/model/**phụ đề** bung thẳng, chưa gập cấp thẻ | `app.js:1166-1235`, phụ đề `1225` |
| 3 | Danh sách dự án không có tìm/lọc/sắp xếp; `error` chỉ có khi YAML hỏng, `done==scenes` lọt dự án rỗng | `app.js:825-852`, `server.py:534-556`, `906-916` |
| 4 | Không gộp checklist sẵn sàng; không pre-flight credit (dùng cache `/api/credits`); QC cần `whisper-cli` trước | `server.py:741-763`, `801-803`, `2184-2195` |
| 5 | `prettyError` chưa phủ hết; cảnh bị ngắt chưa gom một chỗ; Autopilot chưa surface lỗi trước | `app.js:1006-1011`, `1443`, `2462-2484` |
| 6 | Cài đặt: 7 section một trang cuộn dài, Social lồng closure khó đọc (field đã dùng grid) | `app.js:3763-3906`, Social `3854-3883` |
| 7 | Phản hồi tiến độ rời: jobbar + overlay riêng; hàng chờ Flow không đếm ngược | `app.js:3291-3354`, `progress.js`, README:467-472 |
| 8 | Thẻ cảnh hiện 3 ô phụ đề cùng lúc; chưa có chỉ số "đã dịch N/∑"; `/export` chưa nối web UI | `app.js:173-181`, `1225`; `server.py:1327-1346` |
| 9 | Board: liên kết nhân vật phải kéo (có lối thay thế ở tab Cảnh), chưa pinch-zoom; kích thước chạm | `board.js:412-414`, `500-503` |
| 10 | Nút chính lệch quy ước; trạng thái chủ yếu bằng màu; alt ảnh **cảnh** sơ sài | `app.js:2516`, `app.css:608`, ảnh cảnh `app.js:1383-1386` |

## Backlog đề xuất (đã chấm ưu tiên)

Chấm theo **Tác động** (giá trị cho người dùng) × **Công sức** (ngược). Ưu tiên = Tác động cao / Công sức thấp trước.

| Task | Tên | Tác động | Công sức | Ưu tiên | File |
|------|-----|:---:|:---:|:---:|------|
| T1 | Dẫn dắt luồng làm phim (workflow stepper + "việc tiếp theo") | Cao | TB | **P0** | [task-01](task-01-workflow-stepper.md) |
| T2 | Gọn thẻ cảnh bằng tiết lộ dần (gập cấp thẻ cho camera/thoại/model/phụ đề) | Cao | TB | **P0** | [task-02](task-02-scene-card-progressive-disclosure.md) |
| T3 | Tìm kiếm / lọc / sắp xếp danh sách dự án | TB-Cao | Thấp | **P0** | [task-03](task-03-project-list-search.md) |
| T4 | Onboarding có hướng dẫn + pre-flight credit (số dư cache) | Cao | TB | **P1** | [task-04](task-04-onboarding-readiness.md) |
| T5 | Hoàn thiện khôi phục lỗi (phần còn thiếu, không làm lại model-change đã có) | TB-Cao | Thấp-TB | **P1** | [task-05](task-05-error-recovery.md) |
| T6 | Sắp xếp lại modal Cài đặt (điều hướng nhóm, Social rõ trạng thái) | TB | Thấp-TB | **P1** | [task-06](task-06-settings-reorg.md) |
| T7 | Phản hồi tiến độ: đếm ngược hàng chờ + gộp jobbar/overlay | TB | TB | **P2** | [task-07](task-07-progress-feedback.md) |
| T8 | Gọn phụ đề ở thẻ cảnh + chỉ số dịch + nối nút export | TB | TB | **P2** | [task-08](task-08-subtitle-manager.md) |
| T9 | Responsive & cảm ứng (liên kết không kéo, pinch-zoom, kích thước chạm) | TB | TB-Cao | **P2** | [task-09](task-09-responsive-touch.md) |
| T10 | Chuẩn hoá hệ thiết kế & khả dụng (nút, trạng thái, a11y) | TB | Thấp-TB | **P2** | [task-10](task-10-design-system-a11y.md) |

### Lộ trình gợi ý

- **Đợt 1 (quick wins, 1 sprint):** T3, T6, một phần T10 (chuẩn hoá nút + trạng thái). Rủi ro thấp,
  thấy kết quả ngay.
- **Đợt 2 (lõi trải nghiệm):** T1, T2, T5. Đây là phần làm UI "bớt rối" nhất.
- **Đợt 3 (hoàn thiện):** T4, T7, T8, T9 và phần còn lại của T10.

### "Quick wins" có thể làm ngay (gợi ý, chờ bạn duyệt)

- Hạ `maxlength` ô ngôn ngữ về 40 cho khớp `cleanLang`/backend (hiện đặt `maxlength:80` ở `app.js:197` nhưng loại >40 ký tự ở `app.js:184-190`).
- Ô tìm kiếm ở sidebar (T3) — chỉ lọc client-side danh sách đã có.
- Alt có nghĩa cho **ảnh cảnh** (keyframe/ảnh bắt đầu, `app.js:1383-1386`) và `aria-label` cho `div.char-img` nhân vật/bối cảnh (`app.js:1826`, `1935`).
- Nối nút gọi `POST /api/projects/{pid}/export?zip_archive=true` (`server.py:1327-1345`) vào tab Final (T8) — nhớ `zip_archive=true` để `/export/download` trả được `.zip`, tránh HTTP 404.

> Mỗi task có tiêu chí chấp nhận đo được để QA kiểm chứng. Các task **đọc** API sẵn có (`/estimate`,
> `/api/credits`, `/export`, `adopt-clip`, `model-change`) — đã ghi rõ endpoint nào trả gì để không giả
> định sai (vd. `/estimate` **không** trả số credit; `/api/projects` chỉ gắn `error` khi YAML hỏng).
