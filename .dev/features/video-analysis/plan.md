# Phân tích video (analyse a reference video)

Goal: the user pastes ONE link to a video on the web; Film Maker works out everything about how it is made
(rhythm, cuts, transitions, camera, look, music and sound, on-screen text, characters, settings, story beats)
and turns that into (1) a storyboard for a NEW film in the same style that the pipeline can render, and
(2) an edit-style guide the editor agent follows when it cuts the new film.

## rea-video (https://github.com/JuneYaooo/rea-video)

- Apache-2.0, Python >= 3.10 + ffmpeg, CPU only (onnxruntime), no GPU, no paid key needed. Optional
  OpenAI-compatible VLM (Gemini/Doubao) for better per-shot calls; without it, an agent that can see images
  reviews the uncertain shots (`review` / `apply-review`).
- `rea-video decompose VIDEO -o OUT --lang en` -> `breakdown.json`, a "Reel" package for agents
  (`REEL.md` card + overview, `reel/timeline.md` one line per shot, `reel/sound.md`, `reel/packaging.md`,
  `reel/look.md`, `reel/content.md`), frames, audio stems (voice/music/effects), BPM, sfx moments,
  OCR'd subtitles (SRT/ASS + style), camera move per shot, shot boundaries (95% recall on their eval).
- Its main question is "was this filmed, AI-generated or coded?" (forensics + remake routing). For us the
  useful parts are the measured layers: shots, camera, audio stems/BPM/sfx, captions and their style.
- Downloads models on first run (UVR MDX-Net ~67 MB, BandIt v2 ~149 MB, OCR). Does not download web videos.
- Installed in its own venv outside this repo: `~/FilmMaker/.tools/rea-video/.venv` (never a dependency
  of film-maker-ai). Found via `REA_VIDEO_BIN`, that venv, or `rea-video` on PATH. Optional.

## Pipeline (`filmmaker/video_analysis.py`)

1. **Fetch**: yt-dlp (`YTDLP_BIN`, PATH, or `python -m yt_dlp`) for YouTube/TikTok/Facebook/...; plain
   urllib for a direct .mp4 link; a local file for the CLI. <= 720p, first 10 minutes analysed. yt-dlp also
   brings the title/description and the (auto) subtitles = the dialogue.
2. **Measure (ffmpeg, always)**: probe (size, fps, aspect, duration), shot cuts (`select=gt(scene,x)`),
   black frames / fades (`blackdetect`), loudness curve per second (`ebur128`) -> where the sound rises and
   drops, silences. Shot lengths -> average / median / cuts per minute / pace per section.
3. **rea-video (optional)**: `decompose --lang en`; its Reel text (card, timeline, sound, packaging, look,
   content) goes to Claude; its timeline replaces our shot cuts. If it is missing or fails: log + carry on.
4. **Look (Claude)**: contact sheets (each shot: start/middle/end frames, numbered) + measurements + rea
   text + subtitles/description -> one structured `VideoAnalysis` (schema): shots (size, angle, move,
   transition in, on-screen text), transitions summary, camera, look/grade + English `style_prompt`, sound
   (music mood/genre/tempo, rises/drops with times, sfx, voice), text/subtitle style, characters, settings,
   story beats, hook, and an English `edit_style` guide for the editor. Claude Code subscription
   (`claude -p --tools Read`, like `editor.ai_plan`) or API key (images base64).
5. **Saved** in `<workspace>/.analyses/<id>/`: `analysis.json` (everything), `source.mp4`, `frames/`,
   `rea/`, `report.md` (readable, Vietnamese).

## New film in the same style

`plan_from_analysis(analysis, idea, ...)`: `planner.plan_auto` with the idea + a reference brief (genre,
beats, rhythm, camera, look, sound, characters/settings as archetypes, never copying people or text).
Aspect from the reference, scene count suggested from duration / beats. The result gets
`storyboard.style` (look) and the new field `storyboard.edit_style` (guide). The analysis is copied to
`<project>/reference/`.

Editor: `editor._brief` adds the `edit_style` guide; the system prompt says to follow it (rhythm,
transitions, look, music, sound) within the editing rules. Works for ffmpeg edit and CapCut finishing.

## UI / CLI

- Sidebar: "Phân tích video" button under "Phim mới" -> modal: paste link (+ previous analyses).
  Background job "analyze" with log + progress. When done: report modal (video, summary, rhythm strip of
  shots, transitions, camera, look, sound curve with moments, text style, characters, settings, beats,
  edit guide) + "Tạo phim theo phong cách này" form (optional new idea, scenes, languages) -> plan job ->
  opens the new project.
- API: `POST /api/analyses`, `GET /api/analyses`, `GET /api/analyses/{id}`, `GET .../file/{rel}`,
  `DELETE /api/analyses/{id}`, `POST /api/analyses/{id}/film`.
- CLI: `filmmaker analyze URL_OR_FILE [--no-rea] [--film --idea ... --scenes N -o storyboard.yaml]`.

## Tests (no network)

Measurement parsers on canned ffmpeg output, a real tiny video made with ffmpeg lavfi (cuts + tone) for
shot detection / loudness, fetch with fake yt-dlp, rea timeline parsing, Claude call mocked (fake `claude`
binary returning structured_output), plan_from_analysis with plan_auto mocked, editor brief includes
edit_style, server endpoints (job, list, detail, film, delete, path traversal).
