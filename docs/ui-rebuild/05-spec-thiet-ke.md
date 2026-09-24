# Spec thiết kế — dựng lại UI/UX từ trang trắng

Ngày 2026-09-24. Nguồn: `01-ba`, `02-he-thong`, `03-tham-my`, và
`04-tong-hop-sau-doi-chat` (bản gộp sau hai vòng đối chất).

Tài liệu này là **thiết kế**, không phải kế hoạch thi công. Nó nói *cái gì*
và *vì sao*. Thứ tự làm và cách chia ticket nằm ở kế hoạch riêng.

> **Phạm vi này quá lớn cho một kế hoạch thi công duy nhất, và điều đó là cố
> ý.** Thiết kế phải mạch lạc xuyên cả ba mặt nên nó nằm trong một tài liệu;
> nhưng bốn phase ở mục 11 là bốn sản phẩm giao được riêng, và **mỗi phase
> lấy một kế hoạch riêng**. Phase 0 không có UI và là điều kiện tiên quyết
> của Phase 1 — đừng gộp chúng.

---

## 0. Phạm vi

**Dựng lại từ trang trắng:** toàn bộ giao diện. Không màn hình nào, không
thanh nav nào, không bố cục nào hiện có được coi là đầu vào.

**Giữ nguyên, vì là vật lý chứ không phải thiết kế:** mô hình dữ liệu và ngân
sách của nó, CSP, hình dạng OAC của CloudFront, phân rã σ, và bộ test hiện có
về cô lập tenant.

**Không làm trong đợt này:**

- Đăng ký tự phục vụ. Không tồn tại đường mã nào, và dựng nó là một dự án
  khác. Trang giới thiệu phải trung thực về điều đó (mục 7).
- Thông báo đẩy dưới mọi hình thức — email, webhook, cảnh báo. Không có mã
  nào, và nó không phải việc của giao diện.
- Bất kỳ thứ gì trong danh sách từ chối ở mục 12.

---

## 1. Nguyên tắc ràng buộc mọi quyết định bên dưới

**1.1 Lời giải thích và cái điều khiển là cùng một vật thể.** Câu *"error
ratio 0,71 — bình thường của bạn là 0,04 ± 0,08 — tức +8,2σ"* và cái điều
khiển nói *"đừng hành động trước 4,5σ"* phải nằm trên **cùng một cây thước,
cùng một đơn vị**. Đây là thứ duy nhất sản phẩm sở hữu mà không ai bắt chước
được: một rule engine giải thích bằng mã luật, mà mã luật không nằm trên
thang đo nào nên không chỉnh được. Nếu bản dựng cuối đặt phần phân rã và phần
tinh chỉnh ở hai nơi, nó đã vứt đi thứ duy nhất sản phẩm có.

**1.2 Một dụng cụ mất tín hiệu không được hiển thị số đọc.** Không phải "hiển
thị số đọc kèm cảnh báo" — mà **xoá số đọc đi**.

**1.3 Thứ phân biệt "yên" với "chết" phải là một phép đo dương.** Màn hình
yên ắng luôn mang một số đếm đang sống và một σ xa nhất quan sát được. Nếu
một trong hai không tính được thì đó không phải màn hình yên ắng, đó là màn
hình chết đang giả dạng.

**1.4 Giao diện chỉ được nói điều đúng.** Không câu chữ nào được phép bị hành
vi thực tế bác bỏ. Có test cưỡng chế, cùng loại với `test_landing_claims.py`,
mở rộng sang câu chữ console.

**1.5 "Đã quyết" và "đang có hiệu lực" là hai trạng thái khác nhau, vĩnh
viễn.** Agent bất đồng bộ theo kiến trúc. Đổi một ngưỡng không rút lại các
luật đã nằm trong nginx của khách. Mọi chỗ hiển thị trạng thái thi hành phải
mang được cả hai, **dựng vào từ đầu chứ không vá sau**.

---

## 2. Phase 0 — đúng đắn và tầng dữ liệu

Không có UI trong phase này. Nó phải xong trước, vì thiếu nó thì các màn hình
bên dưới sẽ hứa những thứ không có.

### 2.1 Nút Allow phải thật sự gỡ chặn

**Lỗi:** `add_whitelist` xoá dòng `MitigationState` nên backend thôi *phục
vụ* quyết định, nhưng `DecisionStore.apply()` chỉ thêm và `sweep_expired()`
chỉ gỡ khi hết hạn cục bộ. Không gì đối chiếu `_active` với danh sách đang
được phục vụ. Luật `deny` trong nginx đứng tới một tiếng, và màn hình nói
*"Any block on it has been lifted"*.

**Sửa:** agent đối chiếu với **tập đang có hiệu lực**, và tập đó phải được
gửi kèm.

> **Không đối chiếu với `decisions[]` của phản hồi telemetry.** Bản đầu của
> spec này nói thế và nó sai nguy hiểm: `decisions[]` chỉ chứa các IP **có
> traffic trong lô đó** (`for ip in touched_ips`). Một IP đang bị chặn thì
> ngừng gửi traffic — đó chính là điều bị chặn nghĩa là gì. Đối chiếu với nó
> sẽ **gỡ chặn mọi kẻ tấn công vài giây sau khi chặn**, tệ hơn hẳn lỗi đang
> muốn sửa.

`TelemetryResponse` thêm **`active_ips: list[str]`** — toàn bộ IP đang có
hiệu lực cho tenant này, lấy từ `MitigationStateTable.query_active`, chính là
thứ route `/agent/v1/decisions` vốn đã trả về và agent chưa bao giờ gọi.

`DecisionStore.reconcile(active_ips, adapters)` gỡ mọi IP trong `_active`
không còn trong tập đó, y như nó gỡ những IP hết hạn.

Giá: **+1 Query mỗi lô** (~1 RCU cho ~20 dòng, ở 0,39 lô/giây toàn tài khoản
là ~0,39 RCU duy trì), và ~800 B thêm vào phản hồi cho 50 địa chỉ. **Không
thêm một request nào** — đó là lý do chọn cách này thay vì để agent poll
`/agent/v1/decisions`: poll 60 giây với 4 agent đã ăn 69% hạn mức ngày của
một tenant.

Gửi danh sách IP chứ không gửi cả `MitigationState`: một tenant 50 mitigation
mà gửi đủ object kèm mảng bảy đặc trưng là ~10KB mỗi 5 giây, tức ~172MB/ngày
egress cho một việc mà một danh sách chuỗi làm được.

**Ràng buộc: chỉ đối chiếu khi lô được chấp nhận.** Một phản hồi lỗi hoặc một
đợt backend chết **không được** hiểu là "mọi thứ đã được gỡ" — đó sẽ là gỡ
toàn bộ bảo vệ đúng lúc tấn công. Trong `runner._apply`, `result` là None khi
`BackendError`, nên điều kiện đã đúng sẵn; phải có test giữ nó.

**Trong lúc chưa sửa,** giao diện chỉ được nói:
> "203.0.113.7 đã vào danh sách cho phép. Chúng tôi đã ngừng ban hành lệnh
> chặn này. Agent của bạn gỡ luật đang có khi hết bộ đếm — còn 42 phút."

Không tông xanh báo thành công. Trạng thái **đang chờ**, kèm đồng hồ. Hộp
xác nhận cũng không được hứa tức thì.

### 2.2 `last_features` trên episode lịch sử

Thêm 7 số vào `record_decision`. Item từ ~184 B lên ~284 B: **vẫn một đơn vị
ghi, vẫn 1 WCU, 0 thao tác phát sinh**.

Kèm **id phiên bản của bộ thống kê đã đo chúng**. Không có nó, cột "bình
thường của bạn" đọc từ mô hình hiện tại còn con số σ bị đóng băng từ lúc ra
quyết định — sau một đêm huấn luyện lại, hai thứ đó mô tả hai mô hình khác
nhau, im lặng, ngay trên màn hình là toàn bộ khác biệt của sản phẩm.

**Luật cho dòng episode:** bản ghi số học kích thước cố định, chặn ở 1 KB,
**không bao giờ có trường văn bản độ dài thay đổi**. Dư địa 740 B (~61 đặc
trưng số nữa), nhưng một trường tự do 740 B sẽ nhân đôi chi phí ghi của lượt
ghi lịch sử nóng nhất sản phẩm.

### 2.3 Mười ba khoang vùng sát ngưỡng

Gộp vào **chính lệnh `ADD` của `agg#<hour>`** trong `record_traffic` — vốn đã
chạy vô điều kiện, mỗi lô một lần. **0 thao tác, 0 WCU.**

12 khoang 0,25σ từ 3,0 đến 6,0, cộng một khoang tràn ≥6,0 khớp
`SIGMA_CEILING = 6.0`. Tên `n300`…`n600`, 8 B mỗi khoang. Item 119 B → 223 B.

Tích luỹ **ở backend, trong vòng lặp quyết định** — agent không tính z.

Sàn đặt ở 3,0 vì lý do sản phẩm: ADR-006 đo 0,82% dương tính giả ở 3,5σ và
tỷ lệ dốc lên rất nhanh bên dưới, nên điều khiển không nên mời người ta đặt
một cái cổng mà nó không dám khuyến nghị.

`ADD` tạo thuộc tính vắng mặt, nên **chỉ phát ra khoang mà lô này chạm tới**.
Đổi lại, khoang vắng mặt đọc về là **vắng mặt chứ không phải 0**, nên biểu đồ
phải tự điền 0, đúng như `query_series(fill=True)` đã làm.

### 2.4 Ngưỡng theo tenant

Giá trị sống trên `Tenants` (đã đọc trên mọi request agent qua
`assert_tenant_active`). Huấn luyện đêm chép sang item `Models` `production`;
`ScoreStats` thêm `tier1_z`/`tier2_z`. `classify(score, stats)` vốn đã nhận
`stats`, và item đó đã nằm trong `ModelManager._cache`.

**0 đọc, 0 RCU, 0 WCU trên đường chấm điểm nóng.**

Lưu *chỉ* trên `Models` là sai: `save_model` viết lại item đó mỗi đêm và sẽ
âm thầm hoàn nguyên giá trị người vận hành đặt.

**Độ trễ lan truyền là nghĩa vụ của giao diện, không phải lỗi.** `_cache`
sống theo vòng đời container ấm nên thay đổi lan ra trong vài phút. Màn hình
**phải nói ra điều đó** ngay tại chỗ đặt cổng.

### 2.5 Bốn bản ghi nhật ký

Tiền tố khoá sắp xếp thứ tư `set#<epoch10>` trên `TenantHistory`. Một PutItem
mỗi lần đổi, ~200 B, **1 WCU**, TTL 30 ngày, không bảng mới, không GSI, đọc
qua đúng đường `query`/`between` đã có.

Bốn hành động, và chỉ bốn: **đổi ngưỡng** (ai, khi nào, cũ, mới), **thêm/xoá
whitelist** (ai, khi nào, lý do), **đình chỉ/kích hoạt tenant** (ai, khi
nào), **phát/thu hồi khoá agent** (ai, khi nào).

Không hoàn tác — cả bốn đều đã có hành động nghịch đảo.

Người thực hiện lấy từ JWT đã xác minh. `dashboard_auth` hiện chỉ trả
`tenant_id`, **phải mở rộng để trả claims**. Sửa `added_by` của whitelist
trong cùng lượt.

### 2.6 Hai lượt đọc cho câu "tôi có đang được bảo vệ không"

`UsageCounters`: hạn mức tenant (`YYYY-MM-DD#tenant#<id>`) và **trần toàn
cục** (dòng chỉ có ngày), gộp thành **một BatchGetItem**, ~1 RCU.

Trần toàn cục là cái quan trọng: `enforce_usage_ceiling` từ chối ingest trên
**toàn nền tảng**, nên một tenant còn trong hạn mức riêng vẫn có thể đang
không được bảo vệ.

**Nâng `UsageCounters` từ 1 lên 2 RCU.**

Cộng với +0,5 RCU mà byte khoang thêm vào cửa sổ 24 giờ (mục 2.3), đó là
**toàn bộ** phần xin thêm dung lượng của cả đợt. Mọi thứ khác là thêm byte
vào những lượt ghi vốn đã xảy ra.

### 2.7 Chiếu hẹp lệnh đọc `Models`

`get_metadata()` với `ProjectionExpression` loại trừ `model_blob` (mẫu đã có
trong `registry.model_exists`). **~30 RCU → ~0,5 RCU.**

### 2.8 `keys.js` tra `#palette` lười

Hiện bắt một lần lúc nạp (`keys.js:17`). Swap khôi phục lịch sử thay con của
`body` → tham chiếu thành rác → **Ctrl+K im lặng chết sau một cú Back**.

Một dòng, ~20 byte. **Phải xong trước khi chạm vào cấu hình htmx.**

### 2.9 Cấu hình htmx

`historyEnabled: true` + `historyCacheSize: 0`.

Hai cờ độc lập nhau. Cờ hai bảo đảm không byte markup nào của tenant vào
localStorage — hàm lưu cache `return` trước mọi lượt ghi và xoá luôn cache
bản cũ để lại:

```js
historyCacheSize<=0){localStorage.removeItem("htmx-history-cache");return
```

**Sửa ADR-007**, đừng bỏ: nó biện minh cho `historyEnabled:false` bằng chính
mối lo localStorage mà cờ hai giải quyết trực tiếp.

**Xác nhận trên trình duyệt thật lúc rollout**, theo đúng chuẩn ADR-007 tự
đặt: đọc bytes là cần, không đủ.

`hx-boost` trở nên hoạt động được như tác dụng phụ. Nó opt-in theo phần tử
nên không gì đổi — nhưng dùng nó phải là **quyết định tường minh**.

---

## 3. Nguyên thuỷ chung — cái trục σ

Một component, dùng ở mọi nơi trên mặt khách hàng.

### 3.1 Cấu tạo: hai lớp

**Lớp A — SVG.** `preserveAspectRatio="none"`, `viewBox="0 0 600 H"`, 100 đơn
vị mỗi sigma. Chỉ chứa hình học **không có chữ**: ba dải băng, hai đường
cổng, các vạch nguồn, các thanh mật độ. Kéo giãn vô hại vì không có chữ.

**Lớp B — HTML.** Cùng hộp, `padding-inline: 0`. Toàn bộ chữ: nhãn dải, nhãn
vạch σ, số đọc cổng. `display: grid; grid-template-columns: repeat(6, 1fr)`.
**Nhãn nằm ở σ nguyên nên không cần vị trí tính toán** — chúng là grid item
với `grid-column`.

Hai lớp là grid item cùng một ô (`grid-area: 1/1`), `inline-size: 100%`.
**Padding trên bất kỳ lớp nào cũng phá ánh xạ, nên không có padding.**

Kết quả: **xoá 223 luật CSS liệt kê (~9KB) mà không thêm luật thay thế nào**,
và vạch nguồn giữ vị trí chính xác thay vì bị làm tròn.

### 3.2 Nội dung: mật độ dưới cổng, danh tính trên cổng

**Dưới cổng** không ai hành động lên từng nguồn, nên vẽ **mật độ 13 thanh**
từ các khoang. Chi phí byte là **hằng số theo quy mô tenant**.

**Trên cổng** vẽ từng vạch có danh tính, bấm được. Dân số ở đây bị chặn sẵn —
đó là danh sách mitigation.

Không cần `downsample` cho trục. *(Nhưng đừng xoá hàm `downsample` mà chưa rà
hết nơi gọi — nó có `test_charts.py` phủ.)*

### 3.3 Ba trạng thái, và chúng là trạng thái của **vùng vẽ**

| | Vùng vẽ | Câu |
|---|---|---|
| Có tín hiệu | dải tô đậm, có vạch | "142 nguồn, 3 agent đang báo, lúc 14:02" |
| Mất tín hiệu | dải rút về đường viền mảnh, **không vạch nào** | "Không agent nào báo từ 09:14. **web-01 đã ngừng.** Không có gì đang được đo — trục này hiển thị dữ liệu cuối cùng chúng tôi có, không phải traffic hiện tại." |
| Chạm hạn mức | dải vẫn tô, trục đóng dấu thời gian và đông cứng | "Chạm hạn mức 14:02 UTC. Đo lại lúc nửa đêm UTC, còn 6 giờ. **Agent của bạn vẫn chạy; khởi động lại không giúp gì.**" |
| Ngày đầu | dải vẽ, **hai cổng vẽ ở trạng thái chưa nạp đạn** | "Chưa có mô hình. Chúng tôi đang đo và chưa thi hành gì. Cổng bật sau lần huấn luyện đầu, thường sau một đêm." |

Ba trạng thái này hôm nay bị gộp làm một hoặc bị nói sai — trạng thái hạn mức
đang được hiển thị như trạng thái agent chết.

**Test:** không phần tử vạch nào render khi `agent_health()` trả
`never`/`degraded`.

### 3.4 Trục thời gian — cùng cây thước, xoay đi

Mỗi giờ một hàng, vị trí dọc hàng là σ, **hai cổng là hai đường dọc xuyên qua
mọi hàng** — người vận hành nhìn thấy đúng nghĩa đen cái cổng cắt qua lịch sử.

Cùng một lượt Query đã đọc cho dải phản hồi (+0,5 RCU từ byte khoang).

**Chỉ phát ra ô có dữ liệu.** Thưa 20–40 ô ≈ 1,4–2,8KB; dày đặc cả 312 ô ≈
21,8KB. Đây là **yêu cầu, không phải tối ưu hoá**.

Thay thế hoàn toàn biểu đồ độ lệch 240 điểm và `sigma_strip`.

---

## 4. Console khách hàng

Điều hướng là **độ phóng**, không phải trang. URL thật, swap htmx, trục không
nhúc nhích.

### 4.1 Zoom 0 — cái Cổng · `/dashboard/ui`

Màn hình mặc định. Trả lời **việc số 1** ("có gì hỏng không, tại tôi hay tại
các anh") trong 10 giây, **trên một trang**, gồm cả trường hợp agent chết và
trường hợp chạm hạn mức.

Trên màn hình: trục ở mức toàn đội · dải can thiệp · **điều khiển cổng
tier-1, và chỉ tier-1** · danh sách nguồn trên cổng, mỗi dòng bấm được để vào
zoom 1 · thao tác cho qua, đơn lẻ và hàng loạt.

**Cổng tier-2 không đặt ở đây.** Nó cần cửa sổ 7 ngày để có bằng chứng (mục
5.2), nên nó sống ở màn hình lịch sử (mục 4.4). Đặt nó ở zoom 0 nghĩa là đưa
người ta một cái điều khiển mà đồ thị bên dưới nó trống trơn.

**Thao tác hàng loạt phải nêu tên cái gì thất bại.** Chọn 50, 3 cái sai định
dạng, trả về hai con số nguyên là đúng cái bị 1.4 cấm: trong lúc sự cố, *ba
cái nào vẫn còn bị chặn* là thứ duy nhất người ta cần.

Đọc: `MitigationState(tenant_id)` · `Agents(tenant_id)` · `TenantHistory`
24 giờ · BatchGetItem hạn mức. Không toả nhánh theo dòng.

### 4.2 Zoom 1 — một nguồn · `?ip=`

Trả lời **việc số 2**, thứ khiến sản phẩm tồn tại.

Trục giữ nguyên, nguồn được chọn sáng lên. Bên dưới: **bảng phân rã bảy đặc
trưng** so với baseline của chính tenant, sắp theo khoảng cách tuyệt đối giảm
dần, đánh dấu những đặc trưng thực sự dẫn dắt.

Thao tác kháng nghị đặt **ngay cạnh dòng bằng chứng mà nó bác bỏ** — "cho IP
này qua vì tỷ lệ POST giải thích được" nằm cạnh dòng POST ratio, không nằm ở
góc màn hình.

Mỗi quyết định là một permalink: mục đích tồn tại của nó là được dán vào một
ticket.

Với episode lịch sử, phần này chỉ mở được **sau khi 2.2 ship**. Trước đó
**không vẽ màn hình hứa nó**.

### 4.3 Zoom 2 — một đặc trưng · `?ip=&feature=`

Phân bố của đặc trưng đó cho tenant này, vị trí của nguồn đang chọn trên đó,
và lịch sử của nguồn trong chiều đó.

Đây là chỗ đặt **ngưỡng riêng theo từng đặc trưng**, nếu và khi nó được xây.
Phiên bản đầu chỉ đọc.

### 4.4 Lịch sử · `?days=1|7`

Không phải một zoom thứ tư — **cùng một trục, xoay đi** (mục 3.4). Mỗi giờ
một hàng, hai cổng là hai đường dọc xuyên mọi hàng.

Đây là chỗ ở của **episode**, và của **cổng tier-2** (mục 5.2 — nó cần cửa sổ
7 ngày để có bằng chứng).

Lọc theo nguồn phải **thực sự lọc**. Hôm nay `dashboard_status.html` trỏ tới
`/dashboard/ui/history?ip=…` còn `history_page` không có tham số `ip`, nên
người dùng rơi vào lịch sử không lọc và bị hiển thị sai đối tượng, trong im
lặng. Route JSON thì có tham số đó.

Phần "tại sao" của một episode chỉ mở được **sau khi 2.2 ship**. Trước đó
**không vẽ affordance hứa nó** — một nút bị vô hiệu hoá còn tệ hơn không có
nút.

### 4.5 Danh sách cho phép · `/dashboard/ui/allowed`

> Đổi tên từ `/dashboard/ui/whitelist`. Chỉ tồn tại một đường; cập nhật mọi
> link trỏ tới nó thay vì để hai URL cùng sống.

Không phải một zoom — một **sổ đăng ký**. Mỗi mục: IP, ai thêm, khi nào, lý
do.

Phải mang cảnh báo mà hôm nay không chỗ nào nói: **cho một IP qua là loại nó
khỏi baseline của chính bạn, vĩnh viễn.** Một khách hàng cho qua từng IP của
CDN trong lúc sự cố đang xoá dần nguồn traffic lớn nhất của mình khỏi
baseline, điều đó **làm phồng σ của mọi thứ còn lại** và gây ra lần chặn nhầm
tiếp theo. Màn hình phải hiển thị tác động tích luỹ, không chỉ danh sách.

### 4.6 Agents · `/dashboard/ui/agents`

**Vòng cài đặt khép lại tới phép đo đầu tiên.** Phát agent → chạy lệnh → nhìn
trục chuyển từ "đang chờ phép đo đầu tiên" sang một số đếm nguồn sống.
**Thành công là khi sigma đầu tiên xuất hiện, không phải khi tài khoản tồn
tại.**

Credential hiện một lần, và màn hình phải chịu được việc bị rời đi: có bước
xác nhận đã chạy được, không phải một chuỗi ký tự mà một cú bấm là mất.

Mỗi agent phải phân biệt được **"đang gửi telemetry"** với **"đang ghi được
luật"**. Một agent không tìm thấy bộ thi hành nào hiện vẫn báo cáo vui vẻ và
bảo vệ số không, console hiển thị "Reporting" màu xanh.

### 4.7 Mô hình · `/dashboard/ui/model`

Rẻ để mở sau 2.7. Hiển thị: baseline bảy đặc trưng (*"đây là hình dạng bình
thường của bạn"*), trạng thái huấn luyện, và **mô hình staging** — hôm nay nó
được ghi mỗi đêm kể cả khi bị từ chối thăng hạng, cố ý, "vì nó là bằng
chứng", và không màn hình nào đọc.

### 4.8 Việc số 4 — nói thẳng là chưa trả lời được

*"Tuần này các anh làm được gì cho tôi"* không tính được: không gì ghi lại số
request **thực sự bị từ chối**. Agent thi hành cục bộ và không báo lại.

Được phép hiển thị **số quyết định**, và **phải gắn nhãn đúng như thế** —
không được để nó đọc ra thành số request bị chặn. Đó là hai đại lượng khác
nhau và gộp chúng là vi phạm 1.4.

---

## 5. Cái cổng

### 5.1 Hình dạng điều khiển

Cổng có **đúng 13 vị trí hợp lệ**. Toàn bộ đường cong phản hồi dựng sẵn từ
máy chủ và hiện hết cùng lúc:

```
3.00σ   ████████████████████████  2.847 nguồn
3.25σ   ██████████████            1.204
3.50σ   ███████                     412
3.75σ   ███                         118
4.00σ   ██                           47      ← cổng hiện tại
4.25σ   █                            19
```

Kéo cho xem một giá trị mỗi lần; đường cong cho xem cả mười ba. **Không kéo
thả** — không phải vì đắt, mà vì kém thông tin hơn.

Đặt cổng: **13 hàng, mỗi hàng một POST không thân**
(`action="/…/gate?sigma=4.25"`), đúng mẫu ba form của nút đổi chủ đề. Cộng
một ô `<input type="number">` trong ngăn chi tiết cho trường hợp cần chính
xác. **0 byte JS.**

Vị trí hiện tại mang `aria-current`, **không phải chỉ bằng màu**. **Không
`text-transform: uppercase` nhãn σ** — CSS viết hoa chữ Hy Lạp, đó là lỗi đã
ship "4Σ SLOWED" ra màn hình sự cố.

### 5.2 Cửa sổ thời gian

**Cổng tier-1: 24 giờ.** **Cổng tier-2: bắt buộc 7 ngày.** ADR-006 đo 0,00%
dương tính giả ở z < −5,0, nên các khoang 5,0–6,0 rỗng gần như mọi ngày và
đường cong **không có bằng chứng ở chỗ đặt vạch 5σ**.

7 ngày ≈ 20 RCU mỗi lượt xem, đặt sau tab `?days=7` đã có, **không bật trên
mọi trang**.

### 5.3 Ràng buộc trung thực

Dải phản hồi **được phép nói con số và tỷ lệ, và không bao giờ được có link
"cho tôi xem chúng"** cho vùng dưới ngưỡng — phía sau không lưu IP nào. Một
nút bị vô hiệu hoá còn tệ hơn không có nút.

Cổng phải hiển thị **cả hai trạng thái** của nguyên tắc 1.5: đã ban hành, và
đang có hiệu lực tại agent. Cộng độ trễ lan truyền từ 2.4.

---

## 6. Console vận hành

**Trục σ không xuất hiện ở đây**, và đó là lý do kỹ thuật: z được chuẩn hoá
theo phân phối huấn luyện của riêng từng tenant, nên **không tồn tại σ xuyên
tenant nào có nghĩa**. Vẽ nó ra sẽ cần N truy vấn trên một trang vốn đã là
Scan + 2 GSI + N GetItem.

Mặt này **thừa hưởng hệ chữ và hệ màu, và không thừa hưởng gì khác**. Nó dùng
lại *component* đo lường, thay đại lượng bên trong: đại lượng của nó là đồng
hồ hạn mức.

Hai việc, và chỉ hai: **"tôi sắp bị tính tiền chưa"** và **"tenant nào gây
ra, có đình chỉ không"**.

Bắt buộc sửa: số request của tenant **phải có mẫu số** — hôm nay in một con
số trần, trái đúng quy tắc do `presenters.py` tự đặt ra. Cộng xu hướng 7 ngày
để cái cần gạt duy nhất của người vận hành có chỗ nhắm (`BatchGetItem`, xem
`02-he-thong` mục 5.4).

Và `estimated_gb_seconds` **hoặc được ghi thật, hoặc bị gỡ khỏi màn hình**.
Hôm nay nó hiển thị 0.00 vĩnh viễn vì `record_invocation` luôn được gọi với
giá trị mặc định. Một ô đo hiển thị hằng số 0 là vi phạm 1.4.

Trang Agents bên vận hành là một bộ lọc trên danh sách, không phải một công
việc — gộp vào trang tenant.

---

## 7. Trang giới thiệu

**Việc của nó:** thuyết phục một người lạ rằng sản phẩm này đo site của chính
họ chứ không phải site của người khác, và đưa cho họ **đúng một hành động
tiếp theo mà thực sự tồn tại**.

Vì không có đăng ký tự phục vụ, hành động đó là **xin cấp quyền**, chuyển tới
người vận hành, và trang **nói thẳng là chỉ theo lời mời, một người vận
hành**. Gắn nút dẫn tới trang đăng ký không tồn tại thì tệ hơn thừa nhận ràng
buộc — trang này vốn đã tiêu uy tín của mình vào việc thừa nhận ràng buộc, và
đó là tài sản của nó.

Mục "những gì nó chưa làm được" **giữ lại**. Một bản viết lại lặng lẽ bỏ nó
đi sẽ biến trang này thành trang của mọi hãng bảo mật khác.

Trang đọc **không DynamoDB**, giữ nguyên tính chất đó — nó là thứ cho phép
trang nằm sau cache và không bị tính phí.

---

## 8. Hợp đồng khả năng tiếp cận

**Vị trí trên một trục là thứ trình đọc màn hình không đọc được.** Nên mọi
biểu đồ mang **một bảng song song**, và bảng đó **bắt buộc, không tuỳ chọn**.

**Bảng song song là bản tóm tắt, không phải ma trận.** Trục thời gian không
dựng bảng 24×13 = 312 ô — không ai muốn nghe 312 con số. Dựng **24 hàng × 3
cột: giờ, số nguồn trên cổng, σ đỉnh**. Nó trả lời đúng câu hỏi mà biểu đồ
tồn tại để trả lời.

Mọi điều khiển tới được bằng bàn phím. Trạng thái **không bao giờ chỉ bằng
màu**. Tương phản đo trên **từng mặt nền mà token đó thực sự đáp xuống**,
không đo một lần trên nền trắng.

Mọi vùng cuộn có `tabindex="0"` và `role="region"` — vùng chỉ vào được bằng
con trỏ là vùng không vào được bằng bàn phím.

---

## 9. Bố cục file và ngân sách

```
tokens.css    bảng màu (gồm ramp tối và .on-ink), thang chữ, khoảng cách   mọi mặt
app.css       phần tử cơ bản + MỌI class mà macro dùng chung phát ra        mọi mặt
console.css   chỉ khung console                                            chỉ console
```

Macro dùng chung chuyển vào `templates/shared/` để phép quét là một **thư
mục** chứ không phải danh sách tên file duy trì bằng tay.

**Lật `test_shared_chart_styles.py` từ yêu cầu thành điều cấm:** *không
selector nào trong `console.css` được phép khớp một class do bất cứ thứ gì
dưới `templates/shared/` phát ra.* Bài kiểm hiện tại hỏi "nó có trong
`app.css` không?" — câu đó cho phép một bản sao rồi trôi dạt.

```
CSS    −9KB (223 luật liệt kê)  +2KB (trục)  −4KB (khung sidebar)
       = net −11KB, còn ~69KB trên trần 80KB
JS     +20 byte (sửa keys.js). Trần 20KB tự viết, 5 file.
File   7 mục trong _ASSETS, không đổi
Markup ~5,5KB điển hình / 11,5KB xấu nhất mỗi trang
       ĐỘC LẬP VỚI QUY MÔ TENANT
DDB    +1 RCU UsageCounters, +0,5 RCU cửa sổ 24 giờ
```

Trần JS 20KB không phải vì kích thước truyền tải — gói Lambda ở 197MB/250MB
và frontend chiếm 0,04%. Nó là **sức người đọc soát**: 20KB là lượng JS một
người thực sự đọc hết được từng dòng, và đó là biện pháp an toàn duy nhất của
frontend này.

Mọi asset mới có mục `_ASSETS` **trong cùng commit**. Đã ship hai lần
referenced-but-not-served, và triệu chứng là trang render hoàn hảo còn tính
năng chết câm.

---

## 10. Hợp đồng kiểm thử

Mỗi mục dưới đây là một test phải tồn tại, đặt tên theo lỗi nó chặn.

1. Không phần tử vạch nào render khi `agent_health()` trả `never`/`degraded`.
2. Màn hình yên ắng luôn mang một số đếm sống và một σ xa nhất; thiếu một
   trong hai thì không phải màn hình yên ắng.
3. Ba trạng thái — chưa từng kết nối, agent im, chạm hạn mức — render ra ba
   câu khác nhau, và câu hạn mức nêu giờ reset UTC.
4. Không câu chữ nào trên console bị hành vi bác bỏ. Mở rộng
   `test_landing_claims.py` sang console: nút Allow gỡ được luật ở agent
   (kiểm đầu-cuối), và console cảnh báo ở 80% hạn mức đúng như trang giới
   thiệu tuyên bố.
5. Mọi thao tác gỡ thi hành đổi được thứ gì đó trên **chính màn hình người
   dùng đang đứng**, trong một thao tác, **có nêu tên địa chỉ bị ảnh hưởng**
   — không có kết quả dạng số nguyên trần.
6. Không selector `console.css` nào khớp class do `templates/shared/` phát
   ra.
7. Mọi bảng console nằm trong vùng cuộn có `tabindex="0"` và `role="region"`.
8. Không luật CSS nào viết hoa nội dung có thể chứa σ.
9. Mọi phím tắt được in ra màn hình đều có ràng buộc trong markup.
10. Mọi asset được template tham chiếu đều có trong `_ASSETS`, và ngược lại.
11. Mọi script tự viết parse được (`node --check`), strict, bọc IIFE.
12. Tương phản đo trên từng mặt nền mà token đáp xuống.
13. Dòng episode không bao giờ mang trường văn bản độ dài thay đổi.
14. Bốn hành động ở 2.5 sinh ra bản ghi trả lời được ai/khi nào; ba hành động
    không nằm trong bốn đó thì không sinh bản ghi nào.

---

## 11. Thứ tự

**Phase 0** (mục 2) — không UI. 2.1 và 2.2 độc lập, chạy song song được. 2.8
phải xong trước 2.9.

**Phase 1** — trục (mục 3) và console khách hàng (mục 4). Không bắt đầu
trước khi 2.2 ship, nếu không zoom 1 sẽ hứa một thứ TTL đã xoá.

**Phase 2** — console vận hành (mục 6).

**Phase 3** — trang giới thiệu (mục 7).

**Ship riêng, sau cùng:** bất kỳ thay đổi nào lên cache behavior của
`/ui/static/*`. Đó là bước duy nhất mà Lambda alias không rollback được.

---

## 12. Từ chối tường minh

Ghi ra đây để không phải phát hiện lại lúc thi công.

1. Không tìm kiếm xuyên tenant, không "tìm IP này ở mọi nơi" — chỉ Scan làm
   được, và nó phá bảo đảm cô lập.
2. Không tìm chuỗi tự do, không lọc "chứa" phía máy chủ.
3. Không tự làm mới dưới 60 giây, không polling mặc định. **Một tab 5 giây là
   52% trần request ngày của cả tài khoản**; chạm trần thì telemetry bị từ
   chối cho mọi tenant — một cái dashboard sẽ tự tắt bảo vệ.
4. Không có gì thời gian thực. Function URL không có WebSocket; SSE thì toàn
   sản phẩm chỉ có 9,3 ngày thời gian kết nối mỗi tháng.
5. Không cuộn vô tận, không phân trang bằng con trỏ.
6. Không GSI mới nếu chưa phân bổ WCU tường minh từ 5 phần còn lại. **Không
   bao giờ trên `TelemetryEvents`.**
7. Không cửa sổ lịch sử quá 7 ngày, không xuất dữ liệu không chặn biên.
8. Không ô nhập dài, tự do hoặc bí mật ngoài form đăng nhập — nó đi vào URL,
   vào log CloudFront, vào `Referer`, vào lịch sử trình duyệt.
9. **Không tài nguyên bên thứ ba dưới mọi hình thức** — font, icon, thư viện
   biểu đồ, analytics, báo lỗi.
10. Không thao tác hàng loạt quá 50 dòng.
11. Không kéo thả.
12. Không miễn trừ `HX-Request` khỏi metering để làm đẹp con số — chúng là
    những lần gọi Lambda thật.
