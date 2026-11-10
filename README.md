# film-maker-ai

Công cụ dòng lệnh **`filmmaker`** tự động hoá việc làm video trên **Google Flow (Veo)** thông qua
[gflow-cli](https://github.com/ffroliva/gflow-cli).

```
ý tưởng ──plan──▶ storyboard.yaml ──run──▶ keyframe + clip trên Flow ──stitch──▶ final.mp4
         (Claude)     (bạn sửa)          (gflow, chạy lại được)        (ffmpeg, nhạc + phụ đề)
```

## Tool này làm thêm gì so với gflow-cli?

| | |
|---|---|
| **Storyboard từ 1 câu ý tưởng** | `filmmaker plan "..."` dùng Claude viết danh sách cảnh, prompt tiếng Anh chuẩn cho Veo, phụ đề tiếng Việt. Chọn riêng tiếng nhân vật nói (`--dialogue-lang`) và tiếng phụ đề (`--lang`). |
| **Nhân vật đồng nhất** | Khai báo `characters` một lần; mô tả được tự chèn vào mọi cảnh có tên nhân vật. Chế độ `keyframe` tạo ảnh mở đầu (miễn phí) rồi mới animate. |
| **Chạy lại không mất tiền** | Tiến độ lưu ở `state.json`. Cảnh đã xong được bỏ qua; sửa prompt cảnh nào thì chỉ cảnh đó được tạo lại. |
| **An toàn credit** | Cảnh được đánh dấu *trước* khi gửi lệnh render tốn tiền. Nếu bị ngắt giữa chừng, lần sau tool **không** tự gửi lại (tránh trừ credit 2 lần) cho tới khi bạn cho phép. Hết credit / mất đăng nhập / Flow đổi giao diện → dừng ngay. |
| **Thử lại thông minh** | Chỉ thử lại lỗi mà gflow báo là tạm thời (timeout, WAF 403...). Nghỉ ngẫu nhiên giữa các lần gửi. |
| **Ra phim hoàn chỉnh** | Ghép clip theo thứ tự, chuẩn hoá khung hình, thêm nhạc nền, tạo file `.srt` hoặc in phụ đề lên hình. |

## Cài đặt

Yêu cầu: Python 3.10+, `ffmpeg`, Google Chrome, tài khoản Google có quyền dùng Flow (Google AI Pro/Ultra).

```bash
# 1. gflow-cli + trình duyệt cho nó
uv tool install gflow-cli
uv tool run --from gflow-cli playwright install chromium
gflow auth login --browser chrome      # đăng nhập Google một lần

# 2. filmmaker
git clone https://github.com/napro8509/film-maker-ai && cd film-maker-ai
pip install -e '.[ai,ui]'              # [ai] cho `plan`, [ui] cho giao diện web

# 3. kiểm tra mọi thứ
filmmaker check
```

## App macOS (khuyên dùng)

```bash
scripts/build_mac_app.sh          # tạo ~/Applications/Film Maker.app
```

Mở "Film Maker" từ Launchpad/Spotlight hoặc kéo vào Dock. App là cửa sổ native (WKWebView),
không cần mở trình duyệt hay bật server bằng tay — phần xử lý chạy ngầm bên trong app và tắt khi đóng cửa sổ.
Tải file dùng hộp thoại Lưu của macOS; thông báo khi render xong hiện ở Notification Center.
Log ở `~/Library/Logs/FilmMaker.log`. Chạy lại script nếu di chuyển thư mục dự án hoặc tạo lại `.venv`.
Không cần build app: `filmmaker app`.

## Giao diện web

```bash
filmmaker ui                 # mở http://127.0.0.1:8765 trong trình duyệt
filmmaker ui --workspace ~/Phim --port 9000
```

Trên macOS có thể double-click `scripts/FilmMaker.command` (kéo vào Dock để mở nhanh).
Các dự án được lưu ở `~/FilmMaker/<tên-phim>/` (storyboard.yaml, assets/, output/), nên vẫn dùng chung được với các lệnh CLI.

Giao diện có:

- **Dự án**: tạo phim từ ý tưởng (Claude viết storyboard), từ mẫu, trống, hoặc nhập file `.yaml`; nhân bản; xoá (chuyển vào `.trash`, khôi phục được).
- **Sửa từng cảnh**: đổi kiểu cảnh, prompt, độ dài, model, phụ đề; thêm/xoá/nhân bản/đổi thứ tự; tải ảnh lên cho kiểu Ảnh → Video; xem prompt thực tế gửi lên Flow. Tự động lưu.
- **Nhân vật & phong cách**: mô tả nhân vật một lần, tự thêm vào mọi cảnh có tên nhân vật đó.
- **Ảnh nhân vật (tham chiếu)**: tạo ảnh nhân vật trên Flow (miễn phí) hoặc tải ảnh lên. Ảnh được gửi kèm (`--ref`) khi tạo keyframe (`gflow image i2i`) và video Chữ → Video (`gflow video r2v`) của mọi cảnh có tên nhân vật, tối đa 3 nhân vật mỗi cảnh, để giữ khuôn mặt đồng nhất. Nếu tài khoản Flow chưa hỗ trợ thì tự quay về cách cũ (không tốn credit). Đổi ảnh nhân vật sẽ đánh dấu các cảnh liên quan cần tạo lại.
- **Tab "Bảng"**: xem cả phim như một bảng card: nhân vật → keyframe → video của từng cảnh. Kéo nối mũi tên để chọn nhân vật xuất hiện trong cảnh; tạo ảnh nhân vật và keyframe ngay trên card (miễn phí); render video có hộp xác nhận credit. Xem mục "Nhân vật trong từng cảnh".
- **Tạo keyframe trước (miễn phí)**: xem ảnh mở đầu từng cảnh, sửa đến khi ưng rồi mới render video. Render lại video vẫn giữ ảnh đã duyệt.
- **Render an toàn**: hộp xác nhận liệt kê đúng các clip sẽ tốn credit; cảnh bị ngắt giữa chừng không bị gửi lại nếu bạn chưa xác nhận; nút Dừng (cảnh đang render được làm nốt).
- **Model sắp/đã ngừng**: banner đầu dự án báo cảnh nào dùng model sắp ngừng; dropdown "Model video" gắn nhãn "sắp ngừng dd/mm" hoặc "đã ngừng"; nút "Đổi model cho các cảnh chưa render" (xem mục "Model video và ngày ngừng").
- **Log trực tiếp** và trạng thái từng cảnh (xong / đã sửa / bị ngắt / lỗi); thông báo trên máy khi xong.
- **Phim hoàn chỉnh**: xem nối tiếp các clip kèm phụ đề, chọn nhạc nền + âm lượng, in phụ đề lên hình, ghép, tải MP4 / .srt, mở trong Finder.
- **Cài đặt**: tài khoản gflow, thời gian nghỉ giữa các lần gửi, số lần thử lại, cách gọi Claude, Anthropic API key (lưu ở `~/.config/filmmaker/settings.json`), chế độ sáng/tối; kiểm tra đăng nhập Google, xem credit.

### Viết storyboard bằng gói Claude (Pro/Max), không cần API key

Gói Claude Pro/Max không dùng được cho API key (API tính phí riêng), nhưng dùng được qua Claude Code.
Nếu máy có lệnh `claude` đã đăng nhập (`claude` rồi gõ `/login`), `filmmaker plan` và nút "Từ ý tưởng"
sẽ tự gọi qua Claude Code. Muốn chọn rõ: `filmmaker plan "..." --backend claude_code` hoặc `--backend api`.
Khi gọi Claude Code, `ANTHROPIC_API_KEY` được gỡ khỏi môi trường để không bị tính phí API.

Server chỉ nghe trên `127.0.0.1` và mỗi lần mở có một mã riêng, nên trang web khác không gọi được vào để render.

## Dùng nhanh

```bash
# A. Có ý tưởng -> để Claude viết storyboard (cần ANTHROPIC_API_KEY)
filmmaker plan "Một ngày của cô gái bán cà phê ở Hà Nội" -n 6 --lang Vietnamese -o cafe.yaml
#    nhân vật nói tiếng Việt, phụ đề tiếng Anh (--dialogue-lang none = không thoại)
filmmaker plan "..." --dialogue-lang Vietnamese --lang English -o cafe.yaml

# hoặc B. Tự viết từ file mẫu
filmmaker template cafe.yaml

# Xem trước các lệnh gflow sẽ chạy (không tốn credit)
filmmaker run cafe.yaml --dry-run

# Tạo clip + ghép phim
filmmaker make cafe.yaml --music nhac.mp3 --burn-subtitles

# Kiểm tra công cụ + cảnh dùng model sắp ngừng/đã ngừng + cảnh có độ dài model không nhận
filmmaker check cafe.yaml            # [--out DIR], mặc định output/<tên storyboard>

# Làm tròn độ dài model không nhận (vd veo-lite chỉ nhận 4, 6, 8 giây); không cờ thì chỉ liệt kê
filmmaker round-durations cafe.yaml --up     # hoặc --down; [--out DIR]

# Xem tiến độ / làm lại một cảnh / ghép lại
filmmaker status cafe.yaml
filmmaker run cafe.yaml --force s03
filmmaker stitch cafe.yaml --music nhac.mp3

# Tiến độ dạng JSON cho script/agent (chỉ đọc, không tốn credit)
filmmaker status cafe.yaml --json | jq '.summary'
```

`status --json` in đúng một object JSON ra stdout: `title`, `storyboard`, `out_dir`, `project_id` (`null` nếu chưa có),
`summary` (đếm `total`, `done`, `changed`, `interrupted`, `failed`, `todo`) và `scenes` theo thứ tự storyboard.
Mỗi cảnh có `id`, `mode`, `status` (một trong 5 mã trên), `prompt` và `error` đầy đủ (không cắt), `video`/`keyframe`
là đường dẫn tuyệt đối nếu file đang có trên đĩa, ngược lại `null`. Dùng chung được với `--out`. Storyboard lỗi thì
thoát với mã ≠ 0 và stdout rỗng. Đầu ra chữ khi không có `--json` giữ nguyên.

Kết quả nằm ở `output/<tên storyboard>/`:

```
output/cafe/
├── state.json        # tiến độ, project_id, media_id từng cảnh
├── keyframes/s01.png
├── clips/s01.mp4 ...
├── final.mp4         # có sẵn 3 track phụ đề vi / th / en (bật/tắt trong trình phát)
├── final.srt         # phụ đề ngôn ngữ gốc (như trước)
└── final.vi.srt, final.th.srt, final.en.srt
```

## Backup lên Google Drive

Clip Veo tốn credit, nên nên có bản sao trên Drive (gói Google AI Pro/Ultra có sẵn nhiều dung lượng).
Tool dùng [rclone](https://rclone.org) để gửi file lên.

```bash
brew install rclone
rclone config        # n (new remote) → tên: gdrive → chọn "Google Drive" → đăng nhập Google trong trình duyệt
```

```bash
filmmaker backup cafe.yaml            # gửi lên gdrive:FilmMaker/cafe/
filmmaker backup --list               # các dự án đã backup
filmmaker restore cafe --to ~/Phim    # tải về máy khác / sau khi mất máy
filmmaker run cafe.yaml --backup      # tự backup sau mỗi clip render xong
```

- Chỉ gửi file mới hoặc đã đổi, **không bao giờ xoá gì trên Drive**. Clip bị render lại thì bản cũ được chuyển vào
  `gdrive:FilmMaker/.versions/<dự án>/<thời điểm>/`.
- Dự án của giao diện (`~/FilmMaker/<phim>/`) được backup cả thư mục (storyboard, ảnh, nhạc, `output/`).
  Storyboard dùng bằng CLI thì backup file storyboard, ảnh mà nó dùng và thư mục `output/<tên>/` của nó.
- Khi khôi phục, đường dẫn trong `state.json` được sửa theo chỗ mới, nên các cảnh đã xong **không bị render lại**.
- Trong giao diện: **Cài đặt → Backup lên Google Drive** (đổi nơi lưu, bật "Tự backup"), menu **⋯ → Backup lên Drive**,
  và **Phim mới → Từ Drive** để khôi phục. Tự backup chạy ngầm; nếu lỗi (mất mạng, hết hạn đăng nhập) chỉ báo trong log,
  việc render vẫn tiếp tục.
- Đổi nơi lưu: `--remote gdrive:ThuMucKhac` hoặc trong Cài đặt (`backup_remote` trong `~/.config/filmmaker/settings.json`).
  Phiên Drive hết hạn thì chạy `rclone config reconnect gdrive:`.

## Định dạng storyboard

```yaml
title: "Ly cà phê đầu tiên"
aspect: "16:9"            # hoặc 9:16 cho TikTok/Shorts
model: veo-lite           # veo-lite | veo-lite-lp | omni-1.2-flash | ... (gflow models; bỏ trống = Flow tự chọn)
image_model: nano2
style: "cinematic, warm morning light, 35mm film grain"   # nối vào mọi prompt
# project_id: "..."       # dùng lại project Flow có sẵn

characters:
  - name: Lan
    description: "Vietnamese woman, late 20s, shoulder-length black hair, cream linen shirt"

scenes:
  - id: s01
    mode: keyframe        # ảnh mở đầu (Imagen, miễn phí) -> animate (Veo)
    image_prompt: "Lan at a street cafe at dawn"
    prompt: "Lan lifts the glass and smiles"
    camera:               # chỉ đạo hình ảnh (tuỳ chọn, tiếng Anh) - xem mục dưới
      shot: close-up
      angle: eye-level angle
      movement: slow dolly-in
      lens: 85mm portrait lens
    duration: 8
    narration: "Hà Nội, 5 giờ sáng."   # phụ đề (ngôn ngữ gốc)
    subtitles:                          # tuỳ chọn: bản Việt / Thái / Anh; thiếu thì Claude tự dịch khi ghép
      th: "ฮานอย ตีห้า"
      en: "Hanoi, 5 a.m."

  - id: s02
    mode: t2v             # thẳng từ chữ
    prompt: "Macro shot of condensed milk swirling into coffee"

  - id: s03
    mode: i2v             # animate ảnh của bạn (đường dẫn tính từ thư mục storyboard)
    image: assets/photo.png
    end_image: assets/photo_end.png    # tùy chọn
    prompt: "Camera pulls back to reveal the street"
```

Mẹo: viết `prompt` bằng tiếng Anh, mỗi cảnh một hành động; góc máy và chuyển động đặt trong `camera`.

### Nhân vật trong từng cảnh (`characters`)

Mặc định tool dò tên nhân vật trong prompt của cảnh. Muốn chỉ định rõ, thêm `characters` (tuỳ chọn) vào cảnh:

```yaml
characters:
  - {name: Lan, description: "..."}
  - {name: Minh, description: "..."}

scenes:
  - id: s01
    prompt: "Lan and Minh share coffee"
    characters: [Lan, Minh]   # chỉ đúng hai người này
  - id: s02
    prompt: "Empty street at dawn"
    characters: []            # cảnh không có nhân vật
  - id: s03
    prompt: "Lan walks home"  # bỏ trống characters: dò tên như cũ
```

- Bỏ trống: dò tên trong prompt như cũ. Có `characters`: dùng đúng danh sách đó cho phần mô tả và ảnh tham chiếu (tối đa 3 ảnh mỗi cảnh); `[]` = không nhân vật.
- Tên không phân biệt hoa thường, phải có trong `characters` cấp trên và không trùng nhau, nếu không storyboard bị báo lỗi.
- Tab "Bảng" ghi trường này khi bạn kéo/xoá mũi tên nhân vật → cảnh.

### Ngôn ngữ thoại và phụ đề

```yaml
dialogue_language: "Vietnamese"   # nhân vật nói tiếng gì; "none" = không thoại; bỏ trống = Veo tự đoán
subtitle_language: "English"      # ngôn ngữ phụ đề lúc viết storyboard (chỉ để tham khảo)
```

- `dialogue_language`: tool thêm một câu cố định ("All spoken dialogue is in Vietnamese; …") vào **mọi prompt video**
  gửi Flow, nên mọi cảnh nói cùng một thứ tiếng. Câu này không vào prompt ảnh và không đổi fingerprint: đổi giữa
  chừng thì cảnh đã có clip vẫn giữ tiếng cũ (muốn đổi thì render lại cảnh đó, tốn credit).
- `subtitle_language`: chỉ để tham khảo, không gửi Flow. Phụ đề lấy từ `narration`.
- Phim luôn có **3 bộ phụ đề: Tiếng Việt, Tiếng Thái, Tiếng Anh**. `narration` là bản gốc; bản dịch nằm trong
  `subtitles` của từng cảnh (Claude viết sẵn khi lập storyboard). Khi ghép / dựng, câu nào còn thiếu bản dịch được
  Claude tự dịch và lưu lại vào storyboard.yaml (không có Claude thì track đó dùng tạm câu gốc). MP4 xuất ra có 3
  track phụ đề chuyển qua lại được, kèm `final.vi.srt` / `final.th.srt` / `final.en.srt`. In phụ đề lên hình thì
  chọn track bằng `--burn-lang vi|th|en` (CLI) hoặc ô "Track in lên hình" (app); tab Ghép phim có nút VI / TH / EN.
- Mỗi giá trị tối đa 40 ký tự, một dòng: không xuống dòng, không ký tự ẩn (rộng bằng không, điều khiển chiều chữ...);
  khoảng trắng thừa được gộp. Vi phạm thì storyboard báo lỗi (tab YAML trả 400, CLI thoát mã ≠ 0 trước khi gọi Claude).
- Luôn đặt giá trị trong ngoặc kép: YAML đọc `no`, `off`, `yes` không có ngoặc thành true/false và storyboard sẽ báo lỗi.
- `none` (không phân biệt hoa thường) chỉ dùng cho `dialogue_language`; ô phụ đề trong popup và `--lang` cần một
  ngôn ngữ cụ thể, popup không nhận "none".
- Đổi `dialogue_language` ở card Thông tin phim chỉ lưu storyboard và hiện cảnh báo nếu đã có cảnh có clip; app không
  tự render lại gì. Cảnh nào muốn đổi tiếng thì bấm Render lại thủ công.
- `filmmaker plan --dialogue-lang Thai --lang Vietnamese` ghi hai trường này; popup "Phim mới" trong app có hai ô
  "Nhân vật nói" và "Ngôn ngữ phụ đề", và nhớ lựa chọn gần nhất.

### Chỉ đạo hình ảnh (`camera`)

Mỗi cảnh có thể có khối `camera` mô tả cách quay. Mọi trường đều tuỳ chọn, viết bằng thuật ngữ điện ảnh tiếng Anh:

| Trường | Ý nghĩa | Ví dụ |
|---|---|---|
| `shot` | cỡ cảnh | `establishing shot`, `wide shot`, `medium shot`, `close-up`, `extreme close-up`, `over-the-shoulder shot`, `point-of-view shot` |
| `angle` | góc máy | `eye-level angle`, `low angle`, `high angle`, `bird's-eye view, top-down`, `dutch angle, tilted horizon` |
| `movement` | chuyển động máy (một cái) | `static locked-off camera`, `slow dolly-in`, `tracking shot following the subject`, `pan left to right`, `orbit around the subject`, `crane up`, `handheld camera, subtle shake` |
| `lens` | ống kính | `24mm wide-angle lens`, `50mm lens`, `85mm portrait lens`, `macro lens`, `anamorphic lens, oval bokeh, horizontal flares` |
| `focus` | lấy nét, độ sâu | `shallow depth of field, soft bokeh background`, `deep focus, everything sharp`, `rack focus from foreground to background` |
| `composition` | bố cục | `rule of thirds`, `centered symmetrical composition`, `foreground framing through a doorway or objects`, `silhouette against the light` |
| `lighting` | ánh sáng riêng của cảnh | `golden hour backlight`, `low-key lighting, deep shadows`, `rim light outlining the subject` |
| `speed` | tốc độ | `slow motion`, `time-lapse`, `speed ramp` |
| `notes` | ghi chú đạo diễn khác | `camera stays behind her shoulder the whole time` |

Tool tự ghép vào prompt gửi lên Flow (xem "Prompt thực tế gửi lên Flow" ở mỗi cảnh):
- Cảnh `keyframe`: cỡ cảnh, góc, ống kính, nét, bố cục, ánh sáng vào **ảnh mở đầu**; chuyển động, tốc độ, nét vào **video**.
- Cảnh `t2v` (và khi tự chuyển sang Chữ → Video): tất cả vào prompt video. Cảnh `i2v`: chuyển động, tốc độ, nét.
- Đổi `camera` thì cảnh đó cần render lại. Cảnh không có `camera` giữ nguyên prompt như trước.
- Giao diện: mục **Chỉ đạo hình ảnh** dưới ô Hành động của mỗi cảnh, có danh sách gợi ý (nhãn tiếng Việt) nhưng gõ tự do được.
  "Viết kịch bản bằng AI" và agent đạo diễn (`/make-film`) tự điền mục này theo ngữ pháp dựng phim
  (mở bằng toàn cảnh, đổi cỡ cảnh giữa các cảnh liền nhau, quy tắc 180° khi hội thoại...).

### Model video và ngày ngừng

Dự án mới dùng `veo-lite` (Veo 3.1 Lite). Google ngừng `veo-fast`, `veo-quality` và `omni-flash` từ **05/12/2026**.
Ngày ngừng, độ dài cho phép và giá credit theo gói nằm trong `filmmaker/models.yaml`. Muốn sửa mà không đổi code,
tạo file ghi đè `~/.config/filmmaker/models.yaml` (cùng thư mục với `settings.json`, hoặc đặt `$FILMMAKER_MODELS`),
chỉ ghi các trường cần đổi; sửa là có hiệu lực ngay:

```yaml
models:
  veo-fast: {retires: 2026-12-20}   # Google lùi ngày ngừng
```

- `filmmaker check cafe.yaml` liệt kê cảnh dùng model sắp/đã ngừng theo nhóm chưa có clip / bị ngắt / đã có clip;
  thoát mã 1 nếu có cảnh chưa có clip dùng model đã ngừng.
- `run`/`make`: trước ngày ngừng chỉ cảnh báo; từ ngày ngừng, cảnh sắp gửi lên Flow dùng model đã ngừng bị chặn
  (không gửi gì, thoát mã ≠ 0). `--dry-run` và `--keyframes-only` chỉ cảnh báo. Không có cờ bỏ qua chặn:
  đổi model trong giao diện hoặc sửa `model:` trong storyboard.
- Giao diện: banner ở đầu dự án (và nút cùng tên ở tab Cốt truyện) có nút **Đổi model cho các cảnh chưa render**.
  Hộp thoại xem trước cho biết bao nhiêu cảnh sẽ đổi sang model mới (mặc định `veo-lite`), ước tính credit khi render,
  và cảnh có độ dài model mới không nhận (chọn "Làm tròn lên"/"Làm tròn xuống"). Chỉ cảnh **chưa có clip** dùng model
  sắp/đã ngừng bị đổi; cảnh đã có clip, cảnh bị ngắt và cảnh cố ý ghim model còn hoạt động giữ nguyên (clip đã trả tiền
  không bị đánh dấu render lại). Nút chỉ ghi storyboard, không render, không tốn credit; bấm Render sau đó như thường lệ.
  Nút khoá khi đang có job chạy.
- Từ ngày ngừng, bấm Render mà có cảnh sắp gửi dùng model đã ngừng thì giao diện không gửi gì mà mở thẳng hộp thoại đổi
  model (nút Render/"Render lại"/"Gửi lại" trên một cảnh thì mở bản đổi model cho riêng cảnh đó); gọi thẳng API `/run` trả 409.
  Cảnh bị ngắt vẫn cần kiểm tra trên Flow rồi mới "Gửi lại", đổi model không xoá trạng thái bị ngắt.
- Thử trước hành vi "đã ngừng": trong file ghi đè đặt `retires` của model đó là một ngày đã qua, xoá dòng đó để quay lại.

#### Độ dài cảnh model không nhận

Model có khai báo `durations` (vd `veo-lite: [4, 6, 8]`) chỉ nhận các độ dài đó; gửi cảnh `duration: 5` thì Flow từ
chối và cảnh bị lỗi. `duration` để trống (mặc định của Flow) và model không khai báo `durations` thì không bị kiểm.

- `filmmaker check cafe.yaml` liệt kê cảnh chưa có clip (chưa render, bị ngắt) có độ dài sai, dạng
  `s03: 5 giây — veo-lite chỉ nhận 4, 6, 8 (lên 6 / xuống 4)`, và thoát mã 1.
- `run`/`make`: có cảnh sắp gửi bị sai độ dài thì không gửi gì (thoát mã 1). `--dry-run` và `--keyframes-only` chỉ
  cảnh báo. Chỉ xét cảnh lượt này sẽ gửi: cảnh đã có clip (trừ khi `--force`) và cảnh bị ngắt "BỎ QUA" không chặn.
- `filmmaker round-durations cafe.yaml [--out DIR] [--up | --down]`: không cờ thì chỉ liệt kê (thoát mã 1 nếu có cảnh
  sai); `--up`/`--down` ghi lại `duration:` của các cảnh chưa có clip về độ dài hợp lệ gần nhất (kẹp ở hai đầu danh
  sách), in `s03: 5 → 6 giây`. Không gọi gflow, không tốn credit. Ghi lại storyboard nên comment và kiểu trích dẫn trong YAML bị mất
  (giá trị các khoá khác giữ nguyên, chỉ `duration` đổi); nút "Áp dụng" trong giao diện cũng vậy.
  Cảnh **bị ngắt** không bao giờ tự làm tròn (có thể Flow vẫn đang render): kiểm tra Flow, tải clip về hoặc sửa tay;
  còn cảnh như vậy thì lệnh thoát mã 1. Cảnh **đã có clip** cũng không bị sửa; muốn render lại thì sửa tay `duration:`.
- Giao diện: bấm Render mà có cảnh sai độ dài thì hiện hộp "Độ dài không hợp lệ" (làm tròn lên/xuống) trước hộp credit.

#### Flow báo xếp hàng

Sau khi đồng ý trả credit, nếu Flow trả lời "đang xếp hàng (đông người dùng)" thì app không coi là lỗi mà chờ tiếp
trong thời gian cho phép (log in số phút còn lại). Hết giờ mà chưa có video, cảnh ở trạng thái bị ngắt kèm cách xử lý:
mở Flow, tải video về `clips/<id>.mp4`, rồi "Dùng clip này" (hoặc chạy lại; app tự nhận clip, không tốn credit).
App không tự gửi lại cảnh đã trả credit. Dòng lệnh trong log giờ có prompt (rút gọn), model và độ dài đã gửi.

## Đội AI làm phim tự động (Claude Code)

Mở Claude Code trong thư mục repo này rồi gõ:

```
/make-film ca-phe-sang Một ngày của cô gái bán cà phê ở Hà Nội
/make-film ca-phe-sang --auto Một ngày ...    # không dừng hỏi trước khi render video tốn credit
```

Phim nằm ở `~/FilmMaker/<slug>/` (mở được trong `filmmaker ui`). Sửa `brief.md` để đổi thể loại, khán giả,
khung hình, ngôn ngữ thoại/phụ đề, số cảnh, nhạc nền. Chạy lại lệnh với cùng slug để làm tiếp từ công đoạn còn dở.

| Công đoạn | Agent tạo | Agent review | Tốn credit |
|---|---|---|---|
| Kịch bản `script.md` | screenwriter | script-reviewer | không |
| Nhân vật + ảnh tham chiếu | character-designer, producer | visual-reviewer (characters) | không |
| Storyboard `storyboard.yaml` | director | storyboard-reviewer | không |
| Keyframe | producer | visual-reviewer (keyframes) | không |
| Video | producer | visual-reviewer (clips) | **có** |
| Ghép phim + ảnh bìa | producer | visual-reviewer (final) | không |

Mỗi review ghi vào `reviews/` và kết thúc bằng `VERDICT: PASS` / `REVISE`; mỗi công đoạn sửa tối đa 2 vòng rồi hỏi bạn.
Không có `--auto` thì đội luôn dừng hỏi trước khi render video. Với `--auto`, mỗi clip được render lại tối đa 1 lần;
hết credit, mất đăng nhập hoặc cảnh bị ngắt khi render thì vẫn dừng hỏi. Định nghĩa agent ở `.claude/agents/`, lệnh ở `.claude/commands/make-film.md`.

Tạo ảnh nhân vật bằng CLI: `filmmaker characters storyboard.yaml [--name Lan] [--force]` (miễn phí, tự ghi `image` vào storyboard).

## Các tuỳ chọn hay dùng

| Lệnh | Tuỳ chọn |
|---|---|
| `run` / `make` | `--dry-run`, `--keyframes-only`, `--only s02`, `--force s02`, `--retry-unknown`, `--adopt s02`, `--retries 2`, `--pause 8-20`, `--fail-fast`, `--profile <tài khoản>`, `--out <thư mục>` |
| `round-durations` | `--up`, `--down`, `--out <thư mục>` |
| `stitch` / `make` | `--music file.mp3`, `--music-volume 0.25`, `--burn-subtitles`, `--allow-missing`, `-o phim.mp4` |

Biến môi trường: `GFLOW_BIN` (đường dẫn lệnh gflow khác), `ANTHROPIC_API_KEY` (cho `plan`).
Các biến của gflow như `GFLOW_CLI_OUTPUT_DIR`, `GFLOW_CLI_FLOW_HOST` vẫn dùng bình thường.

## Lưu ý

- gflow-cli là công cụ **không chính thức** (reverse-engineered), điều khiển trình duyệt trên tài khoản Google của bạn.
  Việc tự động hoá chịu điều khoản của Google; đọc [DISCLAIMER](https://github.com/ffroliva/gflow-cli/blob/main/DISCLAIMER.md)
  và [ACCOUNT_SAFETY](https://github.com/ffroliva/gflow-cli/blob/main/docs/ACCOUNT_SAFETY.md) của gflow-cli.
- Chỉ render **video** tốn credit; tạo ảnh keyframe và ghép phim thì miễn phí. Luôn chạy `--dry-run` trước.
- Cảnh bị ngắt khi render có thể đã bị Flow trừ credit. Nếu clip đã có trên Flow, tải về và lưu thành
  `clips/<id>.mp4`: lần chạy sau tool dùng clip đó thay vì gửi lại, nhưng chỉ khi file được tải **sau** lần gửi bị ngắt
  và `ffprobe` (đi kèm ffmpeg) đọc được trọn. Không có ffprobe thì tool không tự nhận clip nào.
  Trên web, thẻ cảnh báo tình trạng clip: `ok` (sẽ được dùng), `stale` (file cũ hơn lần gửi, không dùng),
  `invalid` (đọc không trọn, có thể tải dở), `unverified` (cảnh bị ngắt từ bản cũ, chưa rõ mới hay cũ),
  `unchecked` (chưa cài ffprobe). Clip copy từ nơi khác còn giữ ngày cũ (`stale`) mà chắc là bản mới thì chạy
  `touch clips/<id>.mp4`.
- Cảnh bị ngắt từ bản cũ của tool (chưa ghi lúc gửi) không được tự nhận: dùng `--adopt <id>` (hoặc nút "Dùng clip này"
  trên web). `--adopt <id> --force <id>` bị cấm vì sẽ gửi lại ngay sau khi nhận clip. Clip không dùng được thì cảnh đó
  không bị gửi trong lần chạy này, kể cả khi có `--retry-unknown`.
- `--force <id>` / `--retry-unknown` vẫn gửi lại được, kèm dòng cảnh báo credit. Trên web, kể cả nút "Gửi lại" trên
  thẻ cảnh cũng mở hộp xác nhận và phải tick "Tôi đã kiểm tra, gửi lại các cảnh này"; ô tick chỉ áp dụng cho
  đúng các cảnh được liệt kê trong hộp.
- Flow thay đổi giao diện thường xuyên. Nếu gặp lỗi exit 23 (selector drift) hoặc 36, hãy `gflow update`.

## Phát triển

```bash
pip install -e '.[dev]'
pytest            # dùng gflow giả (tests/fake_gflow.py), không cần tài khoản, cần ffmpeg
```

## Cài môi trường local bằng một lệnh

Script sẽ clone code vào `~/Desktop/Projects/film-maker-ai`, tạo `.venv`, cài `filmmaker`, gflow-cli và Chromium, rồi chạy test.
Đổi thư mục bằng biến `TARGET`.

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/napro8509/film-maker-ai/claude/relaxed-bohr-q25gvh/scripts/setup_local.sh | bash
```

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/napro8509/film-maker-ai/claude/relaxed-bohr-q25gvh/scripts/setup_local.ps1 -OutFile setup_local.ps1; powershell -ExecutionPolicy Bypass -File setup_local.ps1
```

Repo private thì `curl` không tải được script: tải `scripts/setup_local.sh` (hoặc `.ps1`) từ GitHub về rồi chạy file đó.
