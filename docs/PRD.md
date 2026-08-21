# PRD (retro) — AI Traffic Shaper: chuyển sang mô hình Free Hybrid

> Tài liệu này được sinh trong **Support mode — Phase 1 (brownfield)**, không đổi
> `current_phase` (vẫn ở Phase 6). Mục đích: chốt lại ý đồ sản phẩm sau khi dự án
> đổi hướng, làm căn cứ để Phase 2 (Architecture) thiết kế lại kiến trúc.
> Nguồn: phỏng vấn trực tiếp với developer, ngày 2026-08-21.

## Bối cảnh — vì sao có bản retro-PRD này

Code hiện tại (README, `services/ai_engine`, `services/worker_orchestrator`,
Terraform/K8s) được xây theo khung cũ: **một tổ chức tự bảo vệ hạ tầng AWS của
chính họ** khỏi DDoS/scraper/botnet, tự vận hành một cụm K8s 1 master + 3
worker trên EC2. Qua phỏng vấn, developer xác nhận **mục tiêu thực sự đã đổi**:
xây một sản phẩm **miễn phí** để người khác tích hợp vào hệ thống của họ —
không phải công cụ nội bộ cho một tổ chức duy nhất.

## Problem & users

Người dùng mục tiêu: các cá nhân/nhóm nhỏ vận hành hệ thống của riêng họ,
muốn có khả năng phát hiện + giảm thiểu traffic bất thường (DDoS, scraper,
botnet) **mà không phải tự xây/tự trả tiền cho một stack ML phức tạp**. Họ
"thuê" sản phẩm này để: cắm một agent nhẹ vào hệ thống của mình, và có một
dịch vụ trung tâm miễn phí lo phần ML/quyết định nặng phía sau.

Người vận hành (nhà phát hành — chính là developer): cần tự host phần backend
trung tâm với **chi phí hạ tầng hướng tới 0đ**, đồng thời không được để bất kỳ
tenant nào nhìn thấy dữ liệu của tenant khác.

## Mô hình phân phối đã chốt

**Hybrid**: agent/sidecar nhẹ do người dùng cài vào hệ thống của họ (gửi
telemetry), backend trung tâm (ML + quyết định mitigation) do nhà phát hành
tự host và chịu chi phí. Đây là quyết định có cân nhắc trade-off — xem
"Assumptions & open questions" về rủi ro chi phí.

**Trạng thái code hiện có:** developer xác nhận **chưa chốt** phần nào của
code cũ (nginx-proxy / ai-engine / worker-orchestrator) có thể tái dùng cho
agent hay cho backend trung tâm — để Phase 2 đánh giá lại, không giả định.
Riêng **toàn bộ hạ tầng Terraform/K8s (1 master + 3 worker EC2) coi như bỏ**,
vì không phù hợp mục tiêu chi phí ≈ 0.

## Success metrics (assumption — cần developer xác nhận số cụ thể)

3 tháng sau khi bản hybrid v1 ra mắt:
- Có ít nhất N agent đang kết nối ổn định tới backend trung tâm (N: **chưa xác nhận**).
- Chi phí hạ tầng backend trung tâm hàng tháng vẫn nằm trong ngưỡng free-tier
  đã chọn (mục tiêu ≈ 0đ, cần Phase 2 định nghĩa ngưỡng cụ thể theo giải pháp
  hạ tầng được chọn).
- Không có sự cố rò rỉ dữ liệu chéo giữa các tenant.

## Components (chốt với developer, không suy diễn)

| Component | In scope? | Build hay reuse | Ghi chú |
|---|---|---|---|
| Agent/sidecar nhẹ (khách tự cài) | Yes | Chưa chốt — có thể dựa ý tưởng nginx-proxy hiện có, Phase 2 đánh giá | Bắt buộc — là điểm tích hợp vào hệ thống khách hàng |
| Backend trung tâm (ML + API) | Yes | Chưa chốt — có thể tái dùng logic ML (IsolationForest, feature registry) hiện có, nhưng kiến trúc multi-tenant + chi phí 0đ cần thiết kế lại | Bắt buộc — nơi chạy detection/mitigation logic dùng chung |
| CLI cài đặt/cấu hình agent | Yes | Build mới | Giảm ma sát onboarding cho người dùng tự cài agent |
| Dashboard người dùng cuối | Yes | Build mới | **Scoped theo tenant** — chỉ thấy dữ liệu của chính họ (traffic bị chặn, whitelist). Không gộp với Control Platform (rủi ro rò rỉ chéo tenant). |
| Control Platform (nhà phát hành) | Yes | Build mới | Quản lý danh sách tenant/agent, sức khỏe hệ thống, và **giám sát usage so với hạn mức free-tier** — bảo vệ mục tiêu chi phí 0đ |
| Public API | Yes | Gắn liền với Backend trung tâm ở trên | Giao thức agent ↔ backend |
| Background workers/jobs | Yes | Chưa chốt — ý tưởng worker-orchestrator hiện có, cần thiết kế lại multi-tenant | Retraining ML, mitigation enforcement |
| Monitoring dashboards nội bộ (Grafana) | Không rõ | N/A | README cũ có Prometheus/Grafana; giữ hay bỏ tùy Phase 2 theo kiến trúc mới |
| Mobile app | No | — | Không được yêu cầu |

## User stories

### US-1: Phát hiện bất thường bằng ML [Done — mô hình cũ]
As a hệ thống, I want phát hiện traffic bất thường theo hành vi (7 feature/IP,
IsolationForest, retrain hàng ngày), so that phân biệt được traffic độc hại
với traffic bình thường mà không cần label thủ công.

**Acceptance criteria:**
- [x] Model retrain hàng ngày, validate trước khi promote (đã có, Phase 3)
- [x] Shadow mode 24h trước khi bật mitigation (đã có)
- [ ] Đánh giá lại: logic này có tái dùng được cho backend multi-tenant không (Phase 2)

### US-2: Giảm thiểu traffic độc hại (Tier 1 rate-limit + Tier 2 block) [Done — mô hình cũ]
As a hệ thống, I want tự động rate-limit hoặc block IP bất thường với TTL
tự hết hạn, so that giảm tải mà không cần can thiệp thủ công.

**Acceptance criteria:**
- [x] Tier 1 (nginx rate-limit động) hoạt động thật, có test (Phase 3, Stage 2)
- [x] Tier 2 (hard block) hoạt động thật, có test
- [ ] Đánh giá lại: cơ chế này chạy trong agent hay trong backend trung tâm (Phase 2)

### US-3: Agent nhẹ tích hợp vào hệ thống khách hàng [Must]
As a người vận hành hệ thống nhỏ, I want cài một agent/sidecar nhẹ vào hệ
thống của tôi, so that traffic của tôi được bảo vệ mà không phải tự vận hành
một stack ML riêng.

**Acceptance criteria:**
- [ ] Agent cài được vào ít nhất 1 kiểu hệ thống đích phổ biến (ví dụ: đứng
      trước một web server) mà không cần đổi cấu trúc hạ tầng hiện có của khách
- [ ] Agent gửi được telemetry tới backend trung tâm và nhận lại quyết định
      mitigation
- [ ] Agent hoạt động độc lập nếu backend trung tâm tạm thời không phản hồi
      (fail-open hay fail-closed — **assumption, cần xác nhận**)

### US-4: Backend trung tâm multi-tenant [Must]
As a nhà phát hành, I want một backend dùng chung xử lý ML detection +
quyết định mitigation cho nhiều tenant, so that mỗi khách hàng không phải tự
vận hành hạ tầng ML.

**Acceptance criteria:**
- [ ] Dữ liệu/telemetry của các tenant được cô lập với nhau (không rò rỉ chéo)
- [ ] Backend chạy được trong ngân sách hạ tầng mục tiêu ≈ 0đ (ngưỡng cụ thể:
      Phase 2 định nghĩa theo giải pháp hạ tầng)
- [ ] Có cơ chế giới hạn/điều tiết khi tổng tải vượt ngưỡng free-tier, tránh
      phát sinh chi phí ngoài kế hoạch

### US-5: CLI cài đặt & cấu hình agent [Must]
As a người dùng mới, I want một lệnh CLI để cài, đăng ký và cấu hình agent
với backend trung tâm, so that onboarding nhanh, không cần đọc tài liệu dài.

**Acceptance criteria:**
- [ ] Một lệnh cài + một lệnh đăng ký kết nối agent với backend
- [ ] CLI báo được trạng thái kết nối (thành công/lỗi) rõ ràng

### US-6: Dashboard cho người dùng cuối [Should]
As a khách hàng, I want xem traffic bị chặn, cấu hình whitelist, lịch sử
mitigation của riêng hệ thống tôi, so that tôi tin tưởng và kiểm soát được
sản phẩm đang bảo vệ hệ thống của mình.

**Acceptance criteria:**
- [ ] Dashboard chỉ hiển thị dữ liệu của tenant đang đăng nhập (không thấy tenant khác)
- [ ] Xem được lịch sử mitigation gần đây và chỉnh whitelist

### US-7: Control Platform cho nhà phát hành [Should]
As a nhà phát hành, I want thấy toàn cảnh các tenant/agent đang kết nối, sức
khỏe hệ thống, và usage so với hạn mức free-tier, so that tôi phát hiện sớm
rủi ro phát sinh chi phí hoặc lạm dụng trước khi nó xảy ra.

**Acceptance criteria:**
- [ ] Danh sách tenant/agent + trạng thái kết nối, cập nhật gần thời gian thực
- [ ] Cảnh báo khi usage tổng thể tiệm cận ngưỡng free-tier

### US-8: Rà soát lỗ hổng bảo mật của dự án [Should]
As a nhà phát hành, I want một lượt rà soát bảo mật cho hướng đi mới (đa
tenant, agent public-facing), so that tôi biết rủi ro trước khi mời người
dùng thật kết nối vào backend của mình.

**Acceptance criteria:**
- [ ] Có báo cáo lỗ hổng/rủi ro cụ thể cho kiến trúc hybrid mới (thực hiện
      qua `/sdlc use 4 ...` sau khi Phase 2 chốt kiến trúc — chưa làm ở bước này)

### US-9: Xác minh triển khai thực tế [Could]
As a nhà phát hành, I want xác minh rằng backend trung tâm thực sự chạy ổn
định trên hạ tầng free-tier đã chọn, so that tuyên bố "đã deploy, ổn định"
là sự thật chứ không phải giả định.

**Acceptance criteria:**
- [ ] Có bằng chứng chạy thật (không phải test local) trên hạ tầng đích

## Out of scope (v1)

- Mobile app.
- Gộp chung Dashboard người dùng cuối với Control Platform (đã quyết định
  tách riêng — xem Components).
- Mô hình "bạn host chung miễn phí cho mọi tenant không giới hạn" hay mô hình
  "chỉ tự host OSS" — đã chọn hybrid, hai mô hình kia không theo đuổi ở v1.
- Toàn bộ hạ tầng Terraform/K8s (1 master + 3 worker EC2) của mô hình cũ.

## Constraints

- **Chi phí hạ tầng backend trung tâm: mục tiêu ≈ 0đ/tháng.** Đây là ràng
  buộc cứng nhất của dự án — Phase 2 phải chọn kiến trúc/nhà cung cấp cụ thể
  đáp ứng được (vd: giới hạn quy mô free-tier, serverless, v.v.), không thể
  vừa multi-tenant thật vừa 0đ vô điều kiện ở quy mô lớn.
- Deadline: chưa nêu.
- Ngân sách ngoài hạ tầng: chưa nêu.
- Tech bắt buộc: chưa nêu ngoài việc tận dụng lại phần ML hiện có nếu hợp lý.
- Compliance/data rules: chưa nêu — cần xác nhận nếu backend xử lý dữ liệu
  traffic thật của bên thứ ba (có thể chứa PII, vd IP người dùng cuối của khách hàng).

## Assumptions & open questions

- **Ngưỡng "chi phí ≈ 0" chưa được định lượng cụ thể** (bao nhiêu tenant/traffic
  là còn trong ngân sách?) — Phase 2 cần chốt cùng developer trước khi thiết
  kế hạ tầng, vì đây là ràng buộc kiến trúc quan trọng nhất.
- Việc tái sử dụng code ML/mitigation hiện có cho kiến trúc multi-tenant mới:
  **chưa chốt**, để Phase 2 đánh giá từng phần thay vì giả định giữ hay bỏ.
  Chỉ có hạ tầng Terraform/K8s là đã được xác nhận bỏ.
- Agent fail-open hay fail-closed khi mất kết nối backend: chưa hỏi, cần Phase 2 xác nhận.
- Compliance/PII khi backend của bên thứ ba xử lý traffic log chứa IP của
  người dùng cuối các tenant: chưa hỏi, nên xác nhận trước khi thiết kế schema dữ liệu.
- Yêu cầu rà soát lỗ hổng bảo mật (US-8) của developer được ghi nhận nhưng
  **chưa thực hiện** trong lượt này — thuộc phạm vi Phase 4, nên chạy sau khi
  Phase 2 chốt kiến trúc mới (rà soát trên kiến trúc cũ sẽ lỗi thời ngay).
