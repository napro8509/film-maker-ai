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
| **Storyboard từ 1 câu ý tưởng** | `filmmaker plan "..."` dùng Claude viết danh sách cảnh, prompt tiếng Anh chuẩn cho Veo, phụ đề tiếng Việt. |
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

# hoặc B. Tự viết từ file mẫu
filmmaker template cafe.yaml

# Xem trước các lệnh gflow sẽ chạy (không tốn credit)
filmmaker run cafe.yaml --dry-run

# Tạo clip + ghép phim
filmmaker make cafe.yaml --music nhac.mp3 --burn-subtitles

# Kiểm tra công cụ + cảnh dùng model sắp ngừng/đã ngừng
filmmaker check cafe.yaml            # [--out DIR], mặc định output/<tên storyboard>

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
├── final.mp4
└── final.srt
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
    image_prompt: "Close-up of Lan at a street cafe at dawn"
    prompt: "Lan lifts the glass and smiles, slow dolly-in"
    duration: 8
    narration: "Hà Nội, 5 giờ sáng."   # phụ đề

  - id: s02
    mode: t2v             # thẳng từ chữ
    prompt: "Macro shot of condensed milk swirling into coffee"

  - id: s03
    mode: i2v             # animate ảnh của bạn (đường dẫn tính từ thư mục storyboard)
    image: assets/photo.png
    end_image: assets/photo_end.png    # tùy chọn
    prompt: "Camera pulls back to reveal the street"
```

Mẹo: viết `prompt` bằng tiếng Anh, mỗi cảnh một hành động + một chuyển động máy quay.

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
| `run` / `make` | `--dry-run`, `--keyframes-only`, `--only s02`, `--force s02`, `--retry-unknown`, `--retries 2`, `--pause 8-20`, `--fail-fast`, `--profile <tài khoản>`, `--out <thư mục>` |
| `stitch` / `make` | `--music file.mp3`, `--music-volume 0.25`, `--burn-subtitles`, `--allow-missing`, `-o phim.mp4` |

Biến môi trường: `GFLOW_BIN` (đường dẫn lệnh gflow khác), `ANTHROPIC_API_KEY` (cho `plan`).
Các biến của gflow như `GFLOW_CLI_OUTPUT_DIR`, `GFLOW_CLI_FLOW_HOST` vẫn dùng bình thường.

## Lưu ý

- gflow-cli là công cụ **không chính thức** (reverse-engineered), điều khiển trình duyệt trên tài khoản Google của bạn.
  Việc tự động hoá chịu điều khoản của Google; đọc [DISCLAIMER](https://github.com/ffroliva/gflow-cli/blob/main/DISCLAIMER.md)
  và [ACCOUNT_SAFETY](https://github.com/ffroliva/gflow-cli/blob/main/docs/ACCOUNT_SAFETY.md) của gflow-cli.
- Chỉ render **video** tốn credit; tạo ảnh keyframe và ghép phim thì miễn phí. Luôn chạy `--dry-run` trước.
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
