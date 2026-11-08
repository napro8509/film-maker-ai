---
description: Chạy cả đội làm phim drama Thái, từ ý tưởng đến final.mp4 đã được review
argument-hint: <slug> [ý tưởng]
---
Bạn là showrunner, điều phối các subagent. Phim nằm ở ~/FilmMaker/<slug>/ với slug là từ đầu tiên của: $ARGUMENTS
Nếu thư mục chưa có brief.md: tạo thư mục (cùng assets/characters/ và reviews/) và ghi phần còn lại của $ARGUMENTS vào brief.md.
Khi gọi subagent, luôn ghi rõ đường dẫn thư mục phim.

Mỗi vòng review sửa tối đa 2 lần; sau đó vẫn chưa đạt thì dừng và hỏi tôi.
Chỉ đọc dòng VERDICT và mục cần sửa của review, không chép cả file vào hội thoại.

1. Kịch bản: screenwriter → script-reviewer. REVISE thì gọi lại screenwriter.
2. Nhân vật: character-designer. Nếu còn nhân vật chưa có ảnh tham chiếu: DỪNG, nhắc tôi tạo ảnh trong giao diện `filmmaker ui`
   (mục Ảnh nhân vật, miễn phí) hoặc đặt ảnh vào assets/characters/, rồi chờ tôi báo xong.
3. Storyboard: director → storyboard-reviewer. REVISE thì gọi lại director.
4. Keyframe (miễn phí): producer chạy keyframes → visual-reviewer chế độ keyframes.
   Cảnh lỗi do prompt: director sửa đúng cảnh đó; lỗi ngẫu nhiên: producer --force <id> --keyframes-only. Review lại.
5. DỪNG. Cho tôi xem danh sách cảnh, số clip sẽ render và đường dẫn thư mục keyframes. Chỉ đi tiếp khi tôi xác nhận render.
6. Video (tốn credit): producer chạy video với câu "người dùng đã xác nhận render" → visual-reviewer chế độ clips.
   Có cảnh cần làm lại: DỪNG, liệt kê cảnh + lý do, hỏi tôi có render lại không (mỗi lần render lại cũng tốn credit).
7. Hoàn thiện: producer chạy stitch và thumbnail.
8. Báo cáo: đường dẫn final.mp4 và ảnh bìa, cảnh nào còn lỗi, các file trong reviews/.
