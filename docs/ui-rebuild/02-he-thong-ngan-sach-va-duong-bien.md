# Báo cáo System — đường biên mà thiết kế mới phải nằm gọn bên trong

Ngày 2026-09-24. Đầu vào cho đợt dựng lại toàn bộ UI/UX.

**Ngân sách gốc** (`terraform/dynamodb.tf`, `core/tables.py`):
**14 RCU / 20 WCU đã cấp phát trên tổng 25/25 của toàn tài khoản.
Còn dư: 11 RCU, 5 WCU.** GSI tính tiền riêng nhưng rút từ cùng một quỹ.
**Đọc thì rẻ, ghi thì không.**

Mỗi request có tính phí còn tốn thêm 1 UpdateItem lên `UsageCounters`
(2 WCU) từ middleware `track_usage` trong `main.py` — **không bọc
try/except, nên traffic duy trì trên ~2 req/s toàn sản phẩm biến thành lỗi
500, không phải trang chậm.** **[đã kiểm: `main.py:170-173`, `record_invocation`
gọi trần]**

Trần toàn cục: 1.000.000/30 = **33.333 request/ngày**; mỗi tenant 25% =
8.333.

> Số liệu kích thước item bên dưới là ước lượng từ danh sách thuộc tính.
> RCU của Query = ceil(bytes/4096) × 0,5 (đọc nhất quán cuối cùng).

---

## 1. Ngân sách đọc, theo từng trang

| Trang | Thao tác DynamoDB | Khoá / chỉ mục | Ghi chú |
|---|---|---|---|
| `/dashboard/ui` (Protection) | 2 Query + 1 ghi | `MitigationState(tenant_id)`, `Agents(tenant_id)` | ~20 dòng × 300 B ≈ 1 RCU. `?ip=` thêm một GetItem `Models` **238 KB ≈ 30 RCU, chỉ trên container lạnh** (ModelManager có cache mức lớp) |
| `/dashboard/ui/agents` | 1 Query + 1 ghi | `Agents(tenant_id)` | POST thêm agent: +1 GetItem `Tenants`, +1 PutItem |
| `/dashboard/ui/whitelist` | 1 Query + 1 ghi | `Whitelist(tenant_id)` | |
| `/dashboard/ui/model` | 1 GetItem + 1 ghi | `Models(tenant_id,"production")` | **Không chiếu hẹp — kéo cả blob mô hình 238 KB (~30 RCU) chỉ để in ra một chuỗi version, trên bảng cấp 2 RCU.** Tỷ lệ đọc/giá trị tệ nhất sản phẩm **[đã kiểm]** |
| `/dashboard/ui/history` | 3 Query/Get + 1 ghi | `TenantHistory` sk `between("mit#…")`, `between("agg#…")`, `get("read#")` | 24 giờ ≈ 2 RCU; **7 ngày của tenant bận ≈ 18 RCU trên 2 RCU đã cấp** — chín giây dung lượng của bảng đó cho mỗi lượt xem trang |
| `/admin/ui` (Overview) | 1 **Scan** + 2 GSI Query + 1 GetItem + 1 ghi | `Tenants` scan, `Agents/LastSeenIndex` ×2, `UsageCounters` | **O(số tenant)** |
| `/admin/ui/tenants` | 1 Scan + 1 GSI Query + **N GetItem** + 1 ghi | thêm `Agents(tenant_id)` nếu có `?id=` | **O(n) hai lần**: một scan cộng một GetItem cho mỗi tenant (`_tenants_ctx`). 50 tenant = 52 lượt đi về trong một lần gọi Lambda |
| `/admin/ui/agents` | 2 GSI Query + 1 ghi | `LastSeenIndex(status,last_seen_at)` | Query "stale" chạy **hai lần** khi ở `?status=stale` (một cho badge, một cho bảng). Xuyên tenant, **không phân trang** — cắt cụt ở 1 MB (~4.000 agent) |

Đã là O(n) sẵn: scan bảng `Tenants` bên admin, vòng lặp GetItem usage theo
tenant, và mọi truy vấn `LastSeenIndex` (O(cả đội), không phải O(một
tenant)).

---

## 2. Thiết kế được phép coi là miễn phí

- **Sắp xếp bất kỳ danh sách nào theo bất kỳ cột nào.** Mitigation, agent,
  whitelist, tenant, episode lịch sử — tất cả đã nằm trọn trong bộ nhớ sau
  một Query. Lọc, nhóm, đếm phía client cũng vậy.
- **Ngăn chi tiết điều khiển bằng `?ip=` / `?id=`.** Lựa chọn được giải bằng
  các dòng đã fetch (không bao giờ tra khoá lại) — đó đồng thời là bảo đảm
  cô lập tenant. Dán link được, nút Back hoạt động, không tốn thêm lượt đọc
  nào.
- **Phần "tại sao nguồn này".** `MitigationState.features` (7 số thực) được
  ghi lúc ra quyết định, còn `feature_means`/`feature_stds` đi kèm mô hình
  đã cache. Không tốn gì trên container ấm.
- **Mọi con số đếm, badge, số trên nav, và mọi biến thể trạng thái rỗng** suy
  ra từ danh sách đã fetch (`live_count`, `quiet_count`, `active_count`,
  `unread`, `total_blocked`).
- **Toàn bộ biểu đồ.** SVG nội tuyến dựng phía máy chủ (`ui/charts.py`),
  không thư viện, không bước build. Hình học đi bằng thuộc tính trình bày
  SVG; `downsample(limit=240)` chặn trần số phần tử.
- **Mitigation vừa hết hạn.** `query_active` vốn đã fetch cả những dòng đã
  hết hạn nhưng TTL chưa kịp xoá, rồi vứt đi ở phía ứng dụng.
- **Giao diện chọn hàng loạt** trên bất kỳ danh sách nào (checkbox, ô chủ ở
  trạng thái lửng, "3 trên 19").
- **Chủ đề màu, phím tắt, quản lý tiêu điểm** — đều chạy bằng cookie, markup
  và thuộc tính `data-`, không tốn lượt đọc.

---

## 3. Thiết kế **không** được phép giả định

| Mẫu hình hấp dẫn | Cơ chế hỏng | Con số | Hình dạng rẻ thay thế |
|---|---|---|---|
| Tìm kiếm xuyên tenant | Mọi bảng trừ `Tenants` đều phân mảnh theo `tenant_id`; "xuyên tenant" = Scan, và Scan tính tiền toàn bảng bất kể khớp bao nhiêu. Đồng thời xoá luôn bảo đảm cô lập mà các test đang khẳng định | 100 MB `TelemetryEvents` = ~12.500 RCU = **900 giây toàn bộ ngân sách đọc của cả tài khoản** | Người vận hành chọn tenant trước, rồi đọc bên trong nó |
| Tìm kiếm chuỗi tự do | Không có chỉ mục văn bản. `FilterExpression` áp dụng **sau** khi đọc và tốn RCU y hệt | Phần "lọc theo IP" của lịch sử đã làm đúng thế này rồi — `list_history` đọc cả cửa sổ 7 ngày rồi vứt bớt dòng trong Python. Bộ lọc chỉ là trang trí | Chỉ `begins_with` trên khoá sắp xếp. `mit#<hour>#<ip>` hỗ trợ tiền tố **giờ**, không hỗ trợ tiền tố IP |
| Tra từng dòng trong danh sách | N lượt đi về trong một Lambda 30 giây; RCU dồn cục lên bảng cấp 1 RCU | `_tenants_ctx` hôm nay: 50 tenant = 50 GetItem ≈ 500 ms, dồn ~25 RCU | `BatchGetItem`, 100 khoá mỗi lần, cùng RCU, 1 lượt đi về (`get_buckets_batch` đã là mẫu sẵn có) |
| Tự làm mới / polling | Cache bị tắt trên mọi đường không tĩnh, nên mỗi nhịp là một lần gọi Lambda **và** một lượt ghi có tính phí. Chạm trần thì telemetry bị từ chối trên toàn nền tảng | Một tab 5 giây = 17.280 req/ngày = **52% toàn bộ ngân sách ngày của tài khoản**; 10 giây = 26%; 60 giây = 4,3%. **Mười người vận hành để 5 giây = 5,2 lần trần → bảo vệ tắt cho mọi tenant** | Làm mới thủ công, hoặc ≥60 giây và chỉ trên đúng trang cần |
| Cuộn vô tận | `_query_all_pages` nuốt `LastEvaluatedKey`, còn `query_by_tenant` không phân trang gì cả. Không đường đọc nào phơi ra con trỏ | Trang thứ N đọc lại cả phân mảnh → O(n²) | Cửa sổ cố định do người dùng chọn (tab 24 giờ / 7 ngày) |
| Cập nhật thời gian thực | Lambda Function URL không có WebSocket. SSE giữ một lần gọi sống suốt đời kết nối, tính vào 400.000 GB-s/tháng ở mức 512 MB | 800.000 Lambda-giây/tháng tổng cộng = **9,3 ngày thời gian kết nối cho toàn sản phẩm**. Một người vận hành, 8 giờ/ngày × 20 ngày = **72% toàn bộ hạn mức tính toán tháng** | Trạng thái dựng phía máy chủ + một nút làm mới tường minh |
| Xuất CSV lịch sử | 30 ngày là 30 lần lượt đọc 7 ngày; API chặn ở `MAX_HISTORY_DAYS = 7`; phản hồi Function URL đệm 6 MB; CloudFront timeout gốc 30 giây | 30 ngày ≈ 75 RCU trong một request trên 2 RCU đã cấp; tín dụng dồn là 300 giây × 2 = 600 RCU, nên sống sót ~8 cú bấm/ngày | Xuất đúng cửa sổ đang hiển thị, ≤7 ngày |
| Thêm GSI mới | GSI nhân bản **mọi lượt ghi** của bảng gốc vào cùng quỹ. **Cấp thiếu cho nó thì lượt ghi của bảng gốc bị nghẽn — mà đó là đường ingest, tức là sản phẩm ngừng ra quyết định** | Dư 5 WCU. GSI cho `Agents` = 1 WCU (được). `TenantHistory` = 2. `MitigationState` = 3. **`TelemetryEvents` = 5, tức toàn bộ phần còn lại** | Tiền tố khoá sắp xếp trong thiết kế bảng đơn `TenantHistory` sẵn có |

---

## 3b. Hình dạng CloudFront — đọc trước khi vẽ bất kỳ form nào

Origin Access Control của CloudFront ký SigV4 mọi request tới Lambda
(`signing_behavior = "always"`). SigV4 phủ cả header lẫn **hash của thân
request** — nhưng CloudFront không đọc thân, nên **phía trình duyệt phải tự
cung cấp `x-amz-content-sha256`**, là SHA-256 dạng hex của đúng chuỗi byte
trên đường truyền. Thiếu nó: **403 ngay tại biên, ứng dụng không bao giờ
thấy request.**

Trình duyệt không thể thêm header vào một `<form method="post">` thuần. Do
đó:

1. **Form có thân không thể submit theo cách gốc trong sản phẩm này.** Chỉ
   tồn tại hai hình dạng.
2. **POST không thân (rẻ, mặc định).** Giá trị đi trong query string — hook
   `htmx:configRequest` trong `ui-status.js` dời chúng sang đó cho mọi phần
   tử đánh dấu `data-params-in-url`. Không hash, không mã hoá phía client,
   chạy được dưới `script-src 'self'`. **Mọi thao tác thay đổi trạng thái
   trong portal hôm nay đều dùng dạng này**: suspend, reactivate, thêm/xoá
   whitelist, cho qua hàng loạt, tạo tenant, thêm agent, đổi chủ đề.
3. **POST có hash (đắt, đúng một nơi gọi).** `signed-post.js` +
   `crypto.subtle`. Dành riêng cho đăng nhập, vì mật khẩu hay token nằm
   trong URL sẽ rơi vào log truy cập CloudFront, header `Referer` và lịch sử
   trình duyệt. `crypto.subtle` chỉ tồn tại trên nguồn an toàn.

**Hệ quả mà người thiết kế phải chịu:**

- **Bất cứ thứ gì người dùng gõ vào form đều kết thúc trong một URL**, trừ
  credential. Không ô văn bản tự do dài quá vài trăm ký tự; không ô "ghi
  chú"; không dán được đoạn log. Thao tác hàng loạt bị chặn trần
  (`BULK_ALLOW_LIMIT = 50` ≈ 1,1 KB query string).
- **Không thể tuần tự hoá lại thân để băm.** htmx mã hoá dấu cách thành
  `%20`, `URLSearchParams` thành `+`; chuỗi lý do đầu tiên có dấu cách sẽ
  403 trong vô hình.
- **Không có `Authorization: Bearer`.** OAC thay thế header đó. Phiên đi
  trong cookie `id_token` (httpOnly) hoặc header `X-Id-Token`.
- **CSP là `default-src 'self'; script-src 'self'; style-src 'self'`, không
  `unsafe-inline`.** Không font CDN, không icon CDN, không analytics, không
  `<script>` nội tuyến, **và không thuộc tính `style=""`** — chiều rộng do
  dữ liệu quyết định phải là class CSS hoặc thuộc tính trình bày SVG (`x`,
  `width`, `points`, `d`), thứ CSP không quản. `img-src 'self' data:`.
- **Chỉ `/ui/static/*` được cache** (max-age 300). Mọi điểm ảnh khác đều là
  một lần gọi Lambda.

---

## 4. Dữ liệu đã trả lời được mà không màn hình nào hiển thị

Đây là phần mặt bằng miễn phí. Người ghi → bảng → trường, tất cả hiện chỉ
ghi mà không ai đọc:

- **Lưu lượng theo giờ.** `record_traffic` (`api/routes/agent.py`, chạy trên
  mọi batch) → `TenantHistory` `agg#<hour>`: `requests`, `batches`,
  `tier1_decisions`, `tier2_decisions`, TTL 30 ngày. `list_series` đã phân
  tích cả bốn thành `HourlyPoint` và trang lịch sử **chỉ render `batches`,
  và chỉ để làm dấu chỗ trống**. Một biểu đồ lưu lượng thật, một lớp phủ số
  quyết định mỗi giờ, và xu hướng 7 ngày — **tất cả đã được fetch rồi vứt
  đi. Không tốn thêm lượt đọc nào.**
- **Số request theo ngày của từng tenant, cho mọi ngày kể từ khi chạy.**
  `record_tenant_ingest` (`core/usage.py`) → `UsageCounters`
  `YYYY-MM-DD#tenant#<id>`. **Bảng đó không có TTL**, nên lịch sử tích luỹ.
  Chỉ *hôm nay* được đọc. Xu hướng theo tenant, so tuần, "ai đang đốt ngân
  sách" — đều sẵn có.
- **Hồ sơ lưu lượng 25 giờ của mọi IP.** `add_aggregate` →
  `TelemetryEvents`: `request_count`, `error_count`, `post_count`,
  `total_bytes`, `total_time`, `distinct_uri_count`, `distinct_ua_count`
  theo từng lô 5 giây, truy vấn được bằng chính khoá phân mảnh của bảng gốc
  (`query_buckets_for_ip`, không GSI, không scan). **Không trang nào đọc.**
  Đây chính là bằng chứng đằng sau mọi cú chặn.
- **Trạng thái loại trừ khỏi huấn luyện.** `flagged` được ghi bởi
  `mark_flagged` (mọi quyết định bất thường) và `flag_all_for_ip` (route
  `/admin/v1/tenants/{id}/training-exclude/{ip}`, **hoàn toàn không có giao
  diện**). Không gì cho thấy mô hình đang bỏ qua những IP nào.
- **Mô hình staging.** `retrain_tenant` ghi một dòng `Models` ở
  `stage_version="staging"` mỗi đêm **kể cả khi bị từ chối thăng hạng** — cố
  ý, "vì nó là bằng chứng". `model_status` chỉ đọc `"production"`. Câu "mô
  hình đêm qua đã huấn luyện xong và không được thăng hạng" nằm trong bảng
  và vô hình. (*Lý do* chỉ được log, không được lưu.)
- **Siêu dữ liệu mô hình có sẵn mà không render:** `contamination`,
  `feature_means`, `feature_stds`, `features`, `stage` trên `Models`.
  `feature_means`/`feature_stds` đang dùng cho ngăn chi tiết trực tiếp, nhưng
  không gì cho tenant thấy "đây là hình dạng bình thường của bạn trên cả bảy
  trục".
- **`Tenants.contact_email`** — do `create_tenant` ghi, vắng mặt trong schema
  `Tenant`, không bao giờ đọc lại. Người vận hành không biết phải email cho
  ai về cái tenant sắp bị đình chỉ.
- **`added_by` của whitelist không được ghi** dù `docs/schema.md` khẳng định
  có. Không gì trả lời được "ai đã cho IP này vào".
- **`Agents.registered_at`** có render trong hai ngăn chi tiết nhưng không có
  trong bảng nào; `api_key_hash` thì đúng là không bao giờ hiển thị.
- **`UsageCounters.estimated_gb_seconds`** là trường hợp ngược lại: có render
  trên trang Overview thành ô "GB-seconds", nhưng `record_invocation` luôn
  được gọi với giá trị mặc định 0.0, nên **nó hiển thị 0.00 vĩnh viễn**.
  **[đã kiểm: `core/usage.py:28`, `main.py:172`]** Và
  `dynamodb_consumed_rcu`/`dynamodb_consumed_wcu` được đọc vào `UsageReport`
  mà **không có người ghi nào cả**.

---

## 5. Những cú thắng rẻ, xếp hạng

1. **Chiếu hẹp lệnh đọc `Models`.** Thêm `get_metadata()` với
   `ProjectionExpression` loại trừ `model_blob` (mẫu đã có sẵn trong
   `registry.model_exists`). **~30 RCU → ~0,5 RCU mỗi lượt xem trang model**,
   0 WCU. Mở khoá: một trang model mở thoải mái được, cộng `contamination`,
   baseline theo từng đặc trưng, và một GetItem chiếu hẹp thứ hai (+0,5 RCU)
   cho mô hình staging.
2. **Vẽ ra chuỗi số liệu đã fetch sẵn.** Chỉ là macro biểu đồ và template.
   **0 RCU, 0 WCU.** Mở khoá lưu lượng, số quyết định và xu hướng trên trang
   lịch sử — **mức lợi thấy được trên một đơn vị công sức lớn nhất trong danh
   sách này**.
3. **Ghi `last_features` trong `record_decision`.** Cùng một UpdateItem,
   thêm ~70 byte trên một item còn xa mới tới 1 KB, nên vẫn là 1 WCU.
   **0 thao tác phát sinh.** Mở khoá ngăn "tại sao" cho **30 ngày** lịch sử;
   hôm nay bằng chứng đó chết theo TTL 300 giây/1 giờ của `MitigationState`
   và không dựng lại được. Nhân tiện thêm `promoted`/`promotion_reason` vào
   PutItem của `save_model` staging (cũng 0 lượt ghi phát sinh).
4. **`UsageCountersTable.get_many()` bằng BatchGetItem.** Thay N GetItem
   trong `_tenants_ctx` bằng 1–2 lệnh gọi, cùng RCU, và làm cho đường
   sparkline 7 ngày theo tenant trở nên kham được (7 × N khoá, lô 100). Sửa
   luôn vấn đề O(n) lượt đi về của trang đó và cho cái cần gạt duy nhất của
   người vận hành một chỗ để nhắm.
5. **Truy vấn telemetry theo IP có chặn biên.** Thêm biên `since_ts` trên
   khoá sắp xếp vào `query_buckets_for_ip` và dùng nó trên mitigation đang
   chọn. **15 phút ≈ 4,5 RCU, 1 giờ ≈ 18 RCU**, chỉ khi được yêu cầu, 0 WCU.
   Mở khoá ngăn bằng chứng: tốc độ request, tỷ lệ lỗi, độ tản URL và
   user-agent đằng sau cú chặn — và phơi ra `flagged`. **Bắt buộc phải chặn
   biên**: bản không chặn đọc cả phân mảnh (tới 18.000 item ≈ 330 RCU).

**Cả năm nằm gọn trong 11 RCU dư và cộng thêm 0 WCU.**

---

## 6. Danh sách nói không — ghi thẳng vào spec

1. **Không tìm kiếm xuyên tenant, không "tìm IP này ở mọi nơi".** Chỉ Scan
   làm được, và nó phá bảo đảm cô lập.
2. **Không tìm chuỗi tự do, không bộ lọc "chứa" phía máy chủ.** Filter của
   DynamoDB tính tiền như đọc đủ. Chỉ khoá chính xác hoặc tiền tố khoá sắp
   xếp.
3. **Không tự làm mới dưới 60 giây, và không bật polling mặc định.** Một tab
   5 giây là 52% trần request ngày của tài khoản; chạm trần thì telemetry bị
   từ chối cho mọi tenant — **một cái dashboard sẽ tự tắt bảo vệ**.
4. **Không có gì thời gian thực.** Không WebSocket (Function URL không hỗ
   trợ), không SSE (toàn sản phẩm chỉ có 9,3 ngày thời gian kết nối mỗi
   tháng), không giao diện lạc quan ngụ ý có kênh đẩy.
5. **Không cuộn vô tận, không phân trang bằng con trỏ.** Không đường đọc nào
   phơi con trỏ, và thêm nó nghĩa là đi lại đường ống `_query_all_pages` trên
   mọi bảng.
6. **Không GSI mới nếu chưa phân bổ WCU tường minh từ 5 phần còn lại. Không
   bao giờ trên `TelemetryEvents`.** Một GSI cấp thiếu sẽ nghẽn lượt ghi của
   bảng gốc — tức là ngừng ra quyết định mitigation đúng vào lúc traffic gây
   ra nó.
7. **Không cửa sổ lịch sử quá 7 ngày, không xuất dữ liệu không chặn biên,
   không sinh báo cáo PDF.** Máy chủ cưỡng chế ở `MAX_HISTORY_DAYS`; dữ liệu
   30 ngày dù sao cũng không tồn tại quá TTL.
8. **Không ô nhập nào dài, tự do hoặc bí mật**, ngoài form đăng nhập. Nó đi
   vào URL, vào log truy cập CloudFront, vào `Referer` và lịch sử trình
   duyệt.
9. **Không tài nguyên bên thứ ba dưới bất kỳ hình thức nào** — font, icon,
   thư viện biểu đồ, analytics, báo lỗi. Người thiết kế đòi một webfont là
   đang đòi sửa CSP và thêm một file tự host.
10. **Không vẽ ngăn "tại sao" cho một episode lịch sử trước khi mục 5.3
    ship.** Các đặc trưng biện minh cho quyết định quá khứ đã bị TTL xoá;
    đừng vẽ một màn hình hứa thứ không có.
11. **Không thao tác hàng loạt quá 50 dòng**, và không thao tác hàng loạt nào
    ngụ ý xác nhận từng dòng hay kết quả chi tiết từng dòng — 50 dòng đã là
    100 lượt ghi tuần tự lên các bảng cấp 1 và 3 WCU, sống bằng tín dụng dồn.
12. **Không có dấu vết kiểm toán cho người vận hành, không có hoàn tác.**
    Không bảng nào ghi ai đã làm gì; `added_by` trên whitelist còn không được
    ghi. Thêm được và rẻ, nhưng đó là thay đổi tầng dữ liệu, không phải thứ
    một màn hình được phép giả định là có.

---

### Tệp định nghĩa các ràng buộc trên

`services/backend/core/tables.py`, `terraform/dynamodb.tf`,
`terraform/cloudfront.tf`, `services/backend/core/usage.py`,
`services/backend/main.py`, `services/backend/ui/static/ui-status.js`,
`services/backend/ui/static/signed-post.js`, `services/backend/ui/charts.py`,
`docs/adr/005-cloudfront-oac.md`, `docs/adr/007-portal-redesign.md`.
