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
pip install -e '.[ai]'                 # bỏ [ai] nếu không dùng `plan`

# 3. kiểm tra mọi thứ
filmmaker check
```

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

# Xem tiến độ / làm lại một cảnh / ghép lại
filmmaker status cafe.yaml
filmmaker run cafe.yaml --force s03
filmmaker stitch cafe.yaml --music nhac.mp3
```

Kết quả nằm ở `output/<tên storyboard>/`:

```
output/cafe/
├── state.json        # tiến độ, project_id, media_id từng cảnh
├── keyframes/s01.png
├── clips/s01.mp4 ...
├── final.mp4
└── final.srt
```

## Định dạng storyboard

```yaml
title: "Ly cà phê đầu tiên"
aspect: "16:9"            # hoặc 9:16 cho TikTok/Shorts
model: veo-fast           # veo-lite | veo-fast | veo-quality | omni-flash | ... (gflow models)
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

## Các tuỳ chọn hay dùng

| Lệnh | Tuỳ chọn |
|---|---|
| `run` / `make` | `--dry-run`, `--only s02`, `--force s02`, `--retry-unknown`, `--retries 2`, `--pause 8-20`, `--fail-fast`, `--profile <tài khoản>`, `--out <thư mục>` |
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
