# Tổng hợp sau đối chất — bản thống nhất của bốn phía

Ngày 2026-09-24. Gộp từ bốn báo cáo độc lập
(`01-ba`, `02-he-thong`, `03-tham-my`, và phần FE) sau **hai vòng đối chất
chéo**. Đây là bản dùng để viết spec.

**Cách đọc.** Mục 1 là kết luận đã thống nhất, **đã cập nhật theo vòng hai** —
nếu chỉ đọc một mục thì đọc mục này. Mục 2 ghi lại ai bác ai và ai nhượng bộ,
đọc nếu muốn biết kết luận nào đã bị thử lửa. Mục 3 là cái giá: **3.0–3.5 là
kết luận hiện hành**, và **`3-cũ` là bản đã bị thay thế, giữ lại chỉ để đối
chiếu — đừng thi công từ nó**. Mục 4 là thứ phải làm trước khi mở một file
CSS. Mục 5 là những gì còn treo.

> **Cảnh báo cho người viết spec.** Tài liệu này có hai thế hệ nội dung. Bất
> cứ chỗ nào mục 1 và mục `3-cũ` nói khác nhau, **mục 1 và 3.0–3.5 là đúng**.
> Bản trước đã từng mâu thuẫn với chính nó ở phần điều hướng; chỗ đó đã sửa,
> nhưng nguyên tắc ưu tiên này vẫn phải giữ.

---

## 1. Đã thống nhất

### 1.1 Xương sống

**SET POINT.** Trục σ là vật thể chính và có mặt trên **các trang của khách
hàng** — **không** có mặt trên console vận hành (lý do ở 2.6, và nó là lý do
kỹ thuật chứ không phải khẩu vị).

**Trục tự báo nguồn tin của chính nó.** Không thêm ô trạng thái, không thêm
banner. Một đồng hồ mất tín hiệu thì không được phép hiển thị một số đọc.
Ba trạng thái, và chúng là trạng thái **của vùng vẽ**:

| Trạng thái | Vùng vẽ | Câu |
|---|---|---|
| Có tín hiệu | dải tô đậm, có vạch nguồn | "142 nguồn, 3 agent đang báo, lúc 14:02" |
| Mất tín hiệu | dải rút về đường viền mảnh, **không vạch nào** | "Không agent nào báo từ 09:14. Không có gì đang được đo." |
| Chạm hạn mức | dải vẫn tô, trục đóng dấu thời gian và đông cứng | "Chạm hạn mức 14:02 UTC. Đo lại lúc nửa đêm UTC, còn 6 giờ. Agent của bạn vẫn chạy; khởi động lại không giúp gì." |

Phép thử phân biệt nó với một cái ô đã bị dời chỗ: **ở trạng thái mất tín
hiệu, trên màn hình không còn số đọc nào cả.** Kiểm thử được: khẳng định
không phần tử vạch nào render khi `agent_health()` trả `never`/`degraded`.

**Luật màn hình yên ắng (BA):** *màn hình yên ắng luôn mang một số đếm đang
sống và một giá trị σ xa nhất quan sát được. Nếu một trong hai không tính
được thì đó không phải màn hình yên ắng.* Thứ phân biệt "yên" với "chết"
phải là **một phép đo dương**, không phải một sự vắng mặt được tạo kiểu.

Câu được chấp nhận cho màn hình yên:
> "3 agent đang báo, gần nhất 12 giây trước. 1.847 nguồn được đo trong một
> giờ qua. Xa bình thường nhất đạt 1,8σ. Cổng của bạn đặt ở 4,0σ."

### 1.2 Trục được dựng bằng gì

**Hai lớp, chia theo một nguyên tắc thật: vị trí tuỳ ý đi vào SVG, vị trí số
nguyên đi vào HTML.**

- **Lớp A — SVG**, `preserveAspectRatio="none"`, `viewBox="0 0 600 H"`, 100
  đơn vị cho mỗi sigma. Chỉ chứa hình học **không có chữ**: ba dải băng, hai
  đường cổng, và mọi vạch nguồn (`<line x1=… x2=…>`, vị trí chính xác, không
  làm tròn). Kéo giãn lớp này vô hại vì trong nó không có chữ nào.
- **Lớp B — HTML**, cùng hộp, `padding-inline: 0`. Toàn bộ chữ: nhãn dải,
  nhãn vạch σ, số đọc của cổng. Định vị bằng
  `display: grid; grid-template-columns: repeat(6, 1fr)`. **Nhãn nằm ở các
  σ nguyên nên không cần vị trí tính toán nào cả** — chúng là grid item với
  `grid-column`.

Hai lớp là grid item trong cùng một ô (`grid-area: 1/1`), `inline-size: 100%`,
padding ngang bằng 0 — nên sigma *s* rơi đúng `s/6 × 100%` ở cả hai lớp, tại
mọi bề rộng. **Padding trên bất kỳ lớp nào cũng phá ánh xạ, nên không có
padding.**

Đây là chỗ giải được mâu thuẫn chứ không né nó: `sigma_strip` hiện dùng
flexbox **vì nhãn của nó nằm trong vùng bị kéo giãn**. Ở đây nhãn rời khỏi
SVG, nên phản đối đó không còn áp dụng. SVG chỉ giữ thứ được lợi khi bị kéo.

### 1.3 Cái cổng là một điều khiển như thế nào

**Nút tăng/giảm theo nấc, hai bên một số đọc sống; ô nhập số ở ngăn chi tiết
cho trường hợp cần chính xác. Không kéo thả.**

| Cách | Phán quyết | JS | Bàn phím | Ở 390px |
|---|---|---|---|---|
| Kéo thả thật | **bác** | ~2KB, **cộng thêm** đường bàn phím vẫn còn nợ | không, về bản chất | thù địch: 65px/σ, ngón tay che mất giá trị |
| Bấm vào một dải | được | 0 | miễn phí (`<button>`) | tốt, nhưng chỉ ra giá trị theo khoang |
| **Tăng/giảm theo nấc** | **khuyến nghị** | **0** | **miễn phí** | **tốt: đích 44px, giá trị không nằm dưới ngón tay** |
| `<input type="number">` | được | 0 | miễn phí | tốt, giá trị chính xác, bàn phím số gốc |
| `<input type="range">` | **bác** | 0 để dùng, nhưng phản chiếu vị trí lên trục cần inline style — **CSP chặn** | miễn phí | thù địch ở 65px/σ |

Mọi phương án 0-JS đều là **POST không thân**, đúng mẫu mà nút đổi chủ đề đã
dùng. Không `hx-on:`, không eval, không cần `x-amz-content-sha256`.

**Trục vẫn hiển thị cổng ở đúng vị trí, dựng từ máy chủ. Người dùng đọc vị
trí và gõ giá trị.**

Lý do cắt kéo thả, phát biểu lại sau vòng hai: **không phải vì nó đắt, mà vì
nó kém thông tin hơn.** Cổng có đúng 13 vị trí hợp lệ, nên toàn bộ đường cong
phản hồi dựng sẵn được một lần. Kéo cho xem một giá trị mỗi lần; đường cong
cho xem cả mười ba cùng lúc. FE: *"không có trường hợp nào phản hồi liên tục
thắng ở đây"* — phản hồi liên tục chỉ đáng khi biến liên tục **và** việc tính
đáp ứng cho mọi giá trị là đắt. Ở đây không điều nào đúng.

**Hai điều kiện bắt buộc lên 13 hàng chọn cổng:**

- Mỗi hàng là **một POST không thân** (`action="/…/gate?sigma=4.25"`), đúng
  mẫu ba form của nút đổi chủ đề. Không thân, không cần
  `x-amz-content-sha256`, không đụng `signed-post.js`.
- Vị trí hiện tại mang `aria-current`, **không phải chỉ bằng màu**. Và
  **không được `text-transform: uppercase` nhãn σ** — đó chính là lỗi đã ship
  "4Σ SLOWED" ra màn hình sự cố, `test_rendered_wording.py` giữ ranh giới
  này.

### 1.4 Điều hướng

**Phóng to là swap htmx, và URL vẫn đúng.** Trục **không nhúc nhích** — lời
hứa ban đầu của hướng SET POINT được giữ nguyên.

Điều này đòi đổi cấu hình: `historyEnabled: true` + `historyCacheSize: 0`.
Hai cờ này **độc lập nhau** trong htmx. Cờ thứ nhất bật lại `hx-push-url` nên
link dán đi tái hiện đúng khung nhìn; cờ thứ hai bảo đảm **không byte markup
nào của tenant được ghi vào localStorage** — hàm lưu cache short-circuit và
`return` trước mọi lượt ghi, và còn chủ động xoá cache do bản cũ để lại.

Đó chính là tính chất an toàn mà `historyEnabled:false` được chọn để mua, đạt
được bằng một cờ chính xác hơn. Xem 3.1 cho năm ràng buộc đi kèm — đặc biệt
**`keys.js` phải sửa trước khi đổi cờ**, và **cấu hình phải xác nhận trên
trình duyệt thật**.

URL giữ nguyên hình dạng `/dashboard/ui?zoom=fleet|tenant|source&ip=…` và vẫn
dán được vào ticket.

> Bản trước vòng hai kết luận ngược lại — rằng phóng to phải là tải trang
> thật. Kết luận đó dựa trên giả định `historyEnabled:false` là bắt buộc, và
> giả định đó sai.

### 1.5 Dữ liệu

| Việc | Cơ chế | Giá |
|---|---|---|
| Ngưỡng theo tenant | Giá trị sống trên `Tenants` (đã đọc trên mọi request agent qua `assert_tenant_active`). Huấn luyện đêm chép sang item `Models` `production`; `ScoreStats` thêm `tier1_z`/`tier2_z`. `classify(score, stats)` vốn đã nhận `stats`, và item đó đã nằm trong `ModelManager._cache` | **0 đọc, 0 RCU, 0 WCU trên đường nóng** |
| Xem trước cổng hai chiều | **13 khoang** gộp vào **chính lệnh ADD `agg#<hour>` đã có** trong `record_traffic` — vốn đã chạy vô điều kiện, mỗi lô một lần. 12 khoang 0,25σ từ 3,0 đến 6,0, cộng một khoang tràn ≥6,0 khớp với `SIGMA_CEILING` | **0 thao tác, 0 WCU.** Item hiện ~119 B, dư 905 B; 13 khoang tốn 104 B → ~223 B, vẫn một đơn vị ghi |
| "Tại sao" cho lịch sử | Thêm `last_features` (7 số) vào `record_decision` | Item từ ~184 B lên ~284 B — **vẫn một đơn vị, vẫn 1 WCU, 0 thao tác phát sinh** |
| "Tôi có đang được bảo vệ không" | 2 GetItem trên `UsageCounters` (hạn mức tenant + trần toàn cục) gộp thành **một BatchGetItem** | ~1 RCU. **Cần nâng `UsageCounters` từ 1 lên 2 RCU** |
| Nhật ký 4 hành động | Tiền tố khoá sắp xếp thứ tư `set#<epoch10>` trên `TenantHistory`, một PutItem mỗi lần đổi, ~200 B, TTL 30 ngày | **1 WCU mỗi lần đổi**, ~0,0001 WCU duy trì |

**Tổng cộng xin thêm dung lượng: +1 RCU trên `UsageCounters`, cộng +0,5 RCU
cho cửa sổ 24 giờ** (byte của các khoang làm 24 hàng `agg#` vượt từ một đơn
vị đọc lên hai). Mọi thứ khác là thêm byte vào những lượt ghi vốn đã xảy ra.

**Và một ràng buộc về cửa sổ thời gian, bắt buộc phải đọc cùng bảng trên:**
24 giờ **không đủ** để cổng 5σ trung thực. ADR-006 đo được 0,00% dương tính
giả ở z < −5,0, nên các khoang 5,0–6,0 rỗng gần như mọi ngày và đường cong
phản hồi **không có bằng chứng nào ở chỗ đặt vạch 5σ**. Cổng tier-1 dùng 24
giờ; **cổng tier-2 bắt buộc 7 ngày**, đặt sau tab `?days=7` đã tồn tại, không
bật trên mọi trang. Chi tiết và chi phí ở 3.4.

Ràng buộc trung thực đi kèm: dải phản hồi của cổng **được phép nói con số và
tỷ lệ, và không bao giờ được phép có link "cho tôi xem chúng"** cho vùng dưới
ngưỡng, vì phía sau không lưu IP nào. Một nút bị vô hiệu hoá còn tệ hơn
không có nút.

### 1.6 Bốn hành động cần nhật ký, và chỉ bốn

Phép thử: *"liệu sau này có người tranh cãi về việc này, với tiền bạc, trách
nhiệm hoặc an ninh đi kèm không?"*

1. **Đổi ngưỡng** — cơ chế trung tâm của SET POINT. Đổi mức thi hành cho mọi
   traffic tương lai trên một site đang chạy. Ghi: ai, khi nào, giá trị cũ,
   giá trị mới. Không thương lượng.
2. **Thêm/xoá whitelist** — tác dụng kép và một phần không hoàn tác được: vừa
   miễn trừ mitigation **vừa vĩnh viễn loại nguồn đó khỏi baseline**. Ghi: ai,
   khi nào, lý do.
3. **Đình chỉ / kích hoạt lại tenant** — làm gãy một khách hàng đang trả tiền
   và thu hồi credential. Ghi: ai, khi nào.
4. **Phát/thu hồi khoá agent** — cấp phát credential. Ghi: ai, khi nào.

**Không cần:** lượt xem trang, đánh dấu đã đọc, chủ đề màu, bộ lọc, lựa
chọn. Cho qua hàng loạt không phải một sự kiện riêng — ghi các dòng whitelist
cấu thành nó.

**Và không có hoàn tác.** Cả bốn đều đã có hành động nghịch đảo.

Ghi chú thi công: người thực hiện lấy từ JWT đã xác minh (`sub`/email), mà
`dashboard_auth` hiện chỉ trả `tenant_id` — **phải mở rộng để trả về claims**.
Đó là thay đổi chữ ký hàm, không phải chi phí dung lượng. Sửa `added_by` của
whitelist trong cùng một lượt.

### 1.7 Bố cục file

Ba file CSS, chia theo **ai nạp**, không chia theo **là cái gì**:

```
tokens.css    bảng màu (gồm cả ramp tối và .on-ink), thang chữ, khoảng cách   mọi mặt
app.css       phần tử cơ bản + MỌI class mà macro dùng chung phát ra          mọi mặt
console.css   chỉ khung console (nav, khung, đồ đạc của bảng)                 chỉ console
```

Và để lỗi cũ **không lặp lại được về mặt cấu trúc**: dời macro dùng chung vào
`templates/shared/` để phép quét là một **thư mục** chứ không phải danh sách
tên file duy trì bằng tay, rồi **lật `test_shared_chart_styles.py` từ một yêu
cầu thành một điều cấm** — *không selector nào trong `console.css` được phép
khớp một class do bất cứ thứ gì dưới `templates/shared/` phát ra.* Bài kiểm
hiện tại hỏi "nó có trong `app.css` không?", câu đó cho phép một bản sao rồi
trôi dạt. Điều cấm thì không cho phép gì cả.

### 1.8 Trang giới thiệu

Việc của nó, phát biểu lại:

> **Thuyết phục một người lạ rằng sản phẩm này đo site của chính họ chứ không
> phải site của người khác, và đưa cho họ đúng một hành động tiếp theo mà
> thực sự tồn tại.**

Vì không có đăng ký tự phục vụ, hành động trung thực là **xin cấp quyền**,
chuyển tới người vận hành, và trang phải nói thẳng là **chỉ theo lời mời,
một người vận hành**. Điều đó nhất quán với mục "những gì nó chưa làm được"
mà trang đã có sẵn. Gắn một nút dẫn tới một trang đăng ký không tồn tại thì
tệ hơn là thừa nhận ràng buộc — trang đó vốn đã tiêu uy tín của mình vào
việc thừa nhận ràng buộc.

**Hệ quả cho màn hình đầu tiên của console:** nó **không phải** "traffic của
bạn", vì chưa có traffic nào. Nó là **vòng cài đặt khép lại tới phép đo đầu
tiên** — phát agent, chạy lệnh, và nhìn trục chuyển từ "đang chờ phép đo đầu
tiên" sang một số đếm nguồn sống trong vài giây. **Thành công là khi sigma
đầu tiên xuất hiện, không phải khi tài khoản tồn tại.**

---

## 2. Bảy chỗ mâu thuẫn, và ai nhượng bộ

### 2.1 BA ⟷ Designer — việc số 1 không có chỗ ở

**Designer nhượng bộ**, và nhận là đã ngụ ý sai rằng riêng cái trục trả lời
được. Nhưng từ chối giải pháp "thêm banner", với lập luận đúng: một ô trạng
thái là vật thể độc lập, xoá nó đi thì trục vẫn vẽ tự tin trên dữ liệu rỗng.
Sinh ra lời giải **trục tự báo nguồn tin** (1.1).

**BA làm sắc thêm**: "thiết kế trường hợp rỗng như trường hợp chính" mới chỉ
là một tư thế; thứ phân biệt phải là **một phép đo dương**. Sinh ra luật màn
hình yên ắng.

### 2.2 Designer ⟷ System — biểu đồ tần suất vùng sát ngưỡng

**System thắng.** Designer đề xuất một item riêng, 12 khoang, một UpdateItem
mỗi lô. System bác và đưa ra thứ rẻ hơn: **gộp các ô đếm vào chính lệnh ADD
`agg#<hour>` đã tồn tại** — 0 thao tác, 0 WCU, 0 phát sinh.

System cũng dẫn số liệu tốt hơn: suy từ **bảng dương tính giả đã đo trong
ADR-006** chứ không từ đuôi phân phối chuẩn — vì toàn bộ luận điểm của
ADR-006 là phân phối này **không** chuẩn. z < −3,5 chiếm 0,82% và z < −4,0
chiếm 0,27% số lô bình thường giữ lại, nên dải 3,5–4,0σ là **0,55% đã đo**.

**Nhượng bộ ngược chiều, và System nhượng luôn:** lý do 12 khoang của
Designer vẫn đứng — *một con số đếm duy nhất cho cả dải 3–4σ không trả lời
được nên đặt cổng ở chỗ nào bên trong dải đó, mà đó là câu hỏi duy nhất
người ta dùng cái điều khiển để hỏi*. Hỏi lại System xem nhét được bao nhiêu
khoang, và **trần byte hoá ra không phải ràng buộc**: item `agg#<hour>` hiện
nặng ~119 B, dư 905 B, mỗi khoang tốn 8 B với cách đặt tên `n300`…`n600` —
**nhét được tới ~113 khoang**. 12 khoang tốn 96 B, 13 khoang tốn 104 B.

> Số học: System viết *"12 khoang tốn 96 B → item ~215 B"* rồi khuyến nghị
> **13** khoang mà không tính lại. Đúng phải là 13 × 8 = **104 B**, item
> **~223 B**. Kết luận không đổi (223 còn xa mới tới 1.024), nhưng con số thì
> sai và bản trước của tài liệu này chép lại cái sai đó.

System kết: *"độ phân giải của Designer là miễn phí; cho họ 13 khoang."*
Chốt 13: 12 khoang 0,25σ từ 3,0 đến 6,0 cộng một khoang tràn ≥6,0, để khớp
`SIGMA_CEILING = 6.0` mà `charts.py` đã dùng. Sàn đặt ở 3,0 **vì lý do sản
phẩm chứ không vì byte**: ADR-006 đo được 0,82% dương tính giả ở 3,5σ và tỷ
lệ đó dốc lên rất nhanh khi xuống thấp hơn, nên cái điều khiển không nên mời
người ta đặt một cái cổng mà nó không dám khuyến nghị.

Hai chi tiết thi công đi kèm: `ADD` coi thuộc tính số vắng mặt là 0 và tạo
nó ra — nên **chỉ phát ra những khoang mà lô này chạm tới**, một khoang chưa
bao giờ bị chạm tốn 0 byte. Đổi lại, **khoang vắng mặt đọc về là vắng mặt
chứ không phải 0**, nên biểu đồ phải tự điền 0, đúng như `query_series(fill=True)`
đã làm cho những giờ trống.

Giữ nguyên `tier1_decisions`/`tier2_decisions`: *"cái cổng đã làm gì"* là một
dữ kiện khác với *"cái cổng lẽ ra đã làm gì"*.

**Tôi bắt một lỗi trong câu trả lời của Designer:** họ viết *"tích luỹ ở phía
agent"*. Sai. Agent không tính z — `z_score` và `score_vectors` chỉ tồn tại
trong backend, agent chỉ gửi dòng log thô. Việc tích luỹ nằm ở backend,
trong vòng lặp quyết định. Giá thành không đổi, chỗ đặt thì đổi.

### 2.3 BA ⟷ System — nhật ký kiểm toán

**System thu hẹp, BA thu hẹp, và cả hai cùng đúng.** Hard-no số 12 của System
nói "không có dấu vết kiểm toán"; tiêu chí số 7 của BA đòi "mọi hành động đổi
trạng thái". BA xếp hạng còn **đúng bốn hành động** và bỏ hoàn toàn yêu cầu
hoàn tác. System costed bốn cái đó ở **1 WCU mỗi lần đổi**.

Câu chốt của BA: *"anh ấy đang tính giá cho bốn bản ghi chỉ-thêm, không phải
cho một hệ thống kiểm toán."* Phần "không hoàn tác" của System vẫn đứng; phần
"không nhật ký" thì không.

### 2.4 Designer ⟷ FE — dựng trục bằng gì

**FE thắng, và lời giải của FE tốt hơn cả hai bản trước đó.** Designer, sau
khi bỏ SVG, đề xuất lượng tử hoá thành 25 cột 0,25σ với 25 luật CSS. FE đưa
ra **phân tách hai lớp**: vị trí tuỳ ý vào SVG (**không cần liệt kê gì cả,
độ chính xác tuyệt đối**), chữ vào lưới HTML ở các σ nguyên (**không cần vị
trí tính toán nào**).

Kết quả: **xoá 223 luật liệt kê hiện có (~9KB)** mà không thêm luật nào thay
thế, và vạch nguồn giữ nguyên vị trí chính xác thay vì bị làm tròn về khoang
0,25σ.

### 2.5 Designer ⟷ FE — phóng to (vòng một FE thắng, vòng hai Designer đúng)

**Vòng một: FE thắng.** Designer muốn htmx tráo vùng vẽ để trục không nhúc
nhích. FE chỉ ra `historyEnabled:false` khiến thanh địa chỉ đứng yên, nên
link dán đi tái hiện sai khung nhìn.

**Vòng hai: kết luận bị lật, và Designer đúng ngay từ đầu.** Hai cờ
`historyEnabled` và `historyCacheSize` là độc lập; chỉ cần cờ thứ hai để có
tính chất an toàn. FE tự đọc lại bytes, xác nhận, và **rút lại nhượng bộ này
của chính mình**. Trục giữ được lời hứa không nhúc nhích. Chi tiết ở 1.4 và
3.1.

### 2.6 Designer ⟷ System — trục trên console vận hành

**Cùng kết luận, nhưng System đưa ra bằng chứng còn Designer chỉ có khẩu
vị.** Designer nói hướng này "hợp với console vận hành kém nhất". System nói
mạnh hơn và đúng hơn: **z được chuẩn hoá theo phân phối huấn luyện của riêng
từng tenant (ADR-006), nên không tồn tại một σ xuyên tenant nào có nghĩa.**
Vẽ nó ra sẽ cần N truy vấn `MitigationState` trên một trang vốn đã là Scan +
2 GSI + N GetItem.

Kết luận: mặt vận hành **dùng lại component**, thay đại lượng bên trong — đại
lượng của nó là đồng hồ hạn mức miễn phí đã có sẵn.

### 2.7 Trang giới thiệu — không ai nhận, BA nhận

Designer và System đều không đụng tới nó. BA nhận và phát biểu lại việc của
nó (1.8).

---

## 3. Cái giá — ba trong bốn đã được gỡ ở vòng hai

> **Vòng hai.** Sau khi bản này viết xong, bốn cái đánh đổi bên dưới được
> tấn công lại và ba cái sụp. Chúng không phải giới hạn của nền tảng — chúng
> là dấu vết của việc thiết kế một điều khiển **liên tục** cho một đại lượng
> **rời rạc 13 giá trị**. Phần gạch ngang là bản cũ; phần sau là kết luận.

### 3.0 Kết luận vòng hai

| Cái đánh đổi cũ | Kết luận | Vì sao |
|---|---|---|
| ~~Không kéo thả~~ | **Giữ, nhưng đổi lý do** | Kéo thả không phải quá đắt, nó **kém thông tin hơn**. 13 vị trí hợp lệ nghĩa là cả đường cong phản hồi dựng sẵn được; kéo cho xem một giá trị mỗi lần, đường cong cho xem cả mười ba. FE: *"không có trường hợp nào phản hồi liên tục thắng ở đây"* |
| ~~Trần 40 nguồn~~ | **Gỡ bỏ** | Dưới cổng không ai hành động lên từng nguồn, nên vẽ **mật độ 13 thanh** thay vì N vạch. Chi phí byte thành **O(1) theo quy mô tenant** |
| ~~Phóng to là tải trang~~ | **Gỡ bỏ, có điều kiện** | `historyEnabled:true` + `historyCacheSize:0`. FE đọc bytes xác nhận: hàm lưu cache **short-circuit và return trước mọi lượt ghi**, còn xoá luôn cache do bản cũ để lại |
| ~~Xoá biểu đồ thời gian~~ | **Gỡ bỏ** | Nó trả lời câu khác ("bắt đầu từ khi nào"). Không xoá — **xoay**: mỗi giờ một hàng, hai cổng là hai đường dọc xuyên mọi hàng |
| `sigma_strip` bị thay | **Vẫn đứng** | Trục thay thế nó |

### 3.1 Năm ràng buộc mới sinh ra ở vòng hai

1. **`keys.js` phải tra `#palette` lười trước khi `historyCacheSize:0` ship.**
   Hiện `var palette = document.getElementById("palette")` bắt một lần lúc
   nạp (keys.js:17). Swap khôi phục lịch sử thay con của `body`, tham chiếu
   thành rác, **Ctrl+K im lặng chết sau một cú Back**. Sửa một dòng, ~20
   byte. **Bắt buộc, không phải tuỳ chọn.**
   (`ui-status.js` và `bulk-select.js` sống sót: cả hai uỷ quyền từ
   `document.body`, mà chính node body không bị thay.)
2. **Lưới lịch sử chỉ phát ra ô có dữ liệu.** Thưa (20–40 ô) ≈ 1,4–2,8KB;
   dày đặc cả 312 ô ≈ 21,8KB. Đây là **yêu cầu, không phải tối ưu hoá**.
3. **Bảng song song là bản tóm tắt, không phải ma trận.** Không dựng 24×13 =
   312 ô (~8KB, và không người dùng trình đọc màn hình nào muốn nghe 312 con
   số). Dựng **24 hàng × 3 cột — giờ, số nguồn trên cổng, σ đỉnh** ≈ 2,6KB.
4. **Bấm Back giờ là lưu lượng ghi có tính phí.** `/dashboard/ui` không nằm
   trong `_UNMETERED_PATH_PREFIXES`, nên mỗi lần Back là một lần gọi Lambda
   **cộng một lượt ghi DynamoDB**. Phải tính vào envelope 25 WCU.
5. **Phải xác nhận cấu hình htmx mới trên trình duyệt thật**, theo đúng
   chuẩn ADR-007 tự đặt ra: *"hành vi runtime của htmx được kiểm bằng cách
   đọc bytes, không phải bằng cách chạy. Riêng các override cấu hình được xác
   nhận trong trình duyệt lúc rollout, không phải giả định."* Đọc mã đã nén
   là cần, không đủ.

Ghi chú: `hx-boost` cũng trở nên hoạt động được như một tác dụng phụ. Nó
opt-in theo từng phần tử nên không gì đổi trừ khi ai đó thêm thuộc tính —
nhưng đó phải là **một quyết định tường minh, không phải một tác dụng phụ**.

### 3.2 Ngân sách sau vòng hai

```
CSS   −9KB (223 luật liệt kê)  +2KB (trục)  −4KB (khung sidebar)
      = net −11KB, còn ~69KB trên trần 80KB          KHÔNG ĐỔI
JS    +20 byte (sửa keys.js)                          trước là +0
File  không đổi, 7 mục trong _ASSETS
Markup mỗi trang   ~5,5KB điển hình / 11,5KB xấu nhất
                   (trước là 8,1KB ở trần 40 nguồn)
                   nhưng ĐỘC LẬP VỚI QUY MÔ TENANT
DynamoDB   +1 RCU trên UsageCounters
           +0,5 RCU cho cửa sổ 24 giờ (byte của các khoang)
```

Xấu nhất **cao hơn** con số cũ, và vẫn là thiết kế tốt hơn: trần 40 đang mua
một giới hạn bằng cách **vứt dữ liệu đi**, còn hình dạng mới cho không giới
hạn đó. Tenant 10.000 nguồn tốn đúng bằng tenant 40 nguồn.

### 3.3 Hai chỗ người tổng hợp nói sai, đã sửa

- **"Swap thì không còn là lần gọi Lambda có tính phí"** — sai. `_should_meter`
  (main.py:139) chỉ xét path, `/`, `/ui/prefs/`, status 401/403/429 và
  `auth_failed`; **không bao giờ nhìn `HX-Request`**. Fragment fetch bị tính
  phí y hệt điều hướng đầy đủ. Tiết kiệm so với trần ngày: **bằng không**.
  Thứ swap thật sự tiết kiệm là byte phản hồi và số lượt đọc fragment từ
  chối thực hiện (1 Query thay vì 3).
  Và **đừng miễn trừ `HX-Request` khỏi metering** để lấp khoảng trống đó —
  chúng là những lần gọi Lambda thật.
- **"Biểu đồ thời gian xoay không thêm lượt đọc nào"** — đúng về **số thao
  tác**, sai về **RCU**: +0,5 RCU từ byte của các khoang.

### 3.4 Và một phát hiện quan trọng hơn cả bốn đề xuất

**Cửa sổ 24 giờ không đủ để cổng 5σ trung thực.** ADR-006 đo được **0,00%
dương tính giả ở z < −5,0**, nên các khoang 5,0–6,0 **rỗng gần như mọi
ngày** — đường cong phản hồi không có bằng chứng nào ở chỗ đặt vạch 5σ.

Nên: **24 giờ mặc định cho cổng tier-1; 7 ngày bắt buộc để cổng tier-2 có
nghĩa**, đặt sau tab `?days=7` đã tồn tại, không bật trên mọi trang. 7 ngày
≈ 20 RCU mỗi lượt xem; tín dụng dồn của `TenantHistory` là 300 s × 2 = 600
RCU nên chịu được ~30 lượt — một người vận hành thì ổn, mười người mở cùng
lúc thì không.

### 3.5 Phải sửa ADR

`ADR-007` biện minh cho `historyEnabled:false` **bằng chính mối lo
localStorage** mà `historyCacheSize:0` giải quyết trực tiếp. Đây không phải
lật quyết định cũ mà là **đạt đúng mục tiêu ADR đó đặt ra bằng một flag chính
xác hơn**. ADR cần sửa lại, không phải bỏ.

---

## 3-cũ. ĐÃ BỊ THAY THẾ — bản trước vòng hai

> **Không thi công từ mục này.** Giữ lại để đối chiếu. Ba trong bốn điểm dưới
> đây đã bị bác ở vòng hai; xem 3.0. Đáng chú ý: điểm 2 dưới đây yêu cầu đổi
> `downsample(points, limit=40)` — **vòng hai kết luận trục không cần
> `downsample` chút nào.** Nhưng FE cảnh báo kèm: *đừng xoá hàm `downsample`
> mà chưa rà hết nơi gọi; nó có `test_charts.py` phủ, và một hàm mồ côi là
> đúng loại lỗi tiềm ẩn mà `test_static_assets.py` được viết ra để chặn.*

Bốn thứ bị cắt, theo bản cũ:

1. **Không kéo thả.** Cổng chỉnh bằng nút theo nấc và ô nhập số.
2. **Trục chặn ở ~40 nguồn hiển thị.** Cần đổi `downsample(points, limit=40)`
   — trần 240 hiện tại được tính cho một biểu đồ cột rộng 1.200px, không phải
   cho một dải mà 40 vạch đã là hơn một vạch mỗi 10px ở 390px. Ở 40 nguồn:
   ~8,1KB mỗi trang, ~2KB sau nén. Ở 240: ~38KB, không chấp nhận được.
3. **Phóng to là điều hướng, không phải tráo.** Có một lần vẽ lại.
4. **`sigma_strip` và biểu đồ độ lệch 240 điểm bị xoá** — trục thay thế cả
   hai.

Ngân sách sau khi đổi:

```
CSS   −9KB (223 luật liệt kê)  +2KB (trục)  −4KB (khung sidebar bị xoá)
      = net −11KB, còn ~69KB trên trần 80KB
JS    +0KB
File  không đổi, 7 mục trong _ASSETS
Dung lượng DynamoDB   +1 RCU trên UsageCounters
```

**Và một sự thật kiến trúc vĩnh viễn mà BA phát hiện:** agent bất đồng bộ
theo thiết kế, nên **"thứ chúng ta quyết" và "thứ đang có hiệu lực ở agent"
là hai trạng thái khác nhau, mãi mãi.** Đổi một ngưỡng không rút lại các luật
đã nằm trong nginx của người ta. Dải cổng **phải hiển thị cả hai từ ngày
đầu** — "đã ban hành" và "đang có hiệu lực tại agent" — dựng vào ngay từ
đầu chứ không phải vá sau như một cách lách lỗi.

---

## 4. Phải xong trước khi mở một file CSS

Cả Designer lẫn BA đều tự xếp việc này lên trên đợt dựng lại của chính họ.

1. **Sửa nút Allow không gỡ chặn ở agent.** Lỗi đúng đắn của sản phẩm, trong
   câu chữ chịu tải nặng nhất của nó. Được phép ship độc lập, nhưng phải
   **trước hoặc cùng lúc** với bất kỳ giao diện nào tuyên bố gỡ tức thì,
   không bao giờ sau.

   Trong lúc chưa sửa, giao diện chỉ được nói đúng sự thật:
   > "203.0.113.7 đã vào danh sách cho phép của bạn. Chúng tôi đã ngừng ban
   > hành lệnh chặn này. Agent của bạn sẽ gỡ luật đang có khi hết bộ đếm —
   > còn 42 phút."

   Không dùng tông xanh báo thành công; đây là trạng thái **đang chờ**, kèm
   đồng hồ đếm ngược. Và hộp thoại xác nhận cũng không được hứa tức thì.

2. **Thêm `last_features` vào `MitigationEpisode`.** Đây là **ticket đầu
   tiên**, trước mọi dòng CSS. Thiếu nó thì phân rã σ chỉ là trò ảo thuật
   xem được trực tiếp, lịch sử là một bảng số không có lý do, và không hướng
   thiết kế nào cứu được.

   Kèm luật cho spec, từ phép tính của System: **dòng episode là một bản ghi
   số học kích thước cố định, chặn ở 1 KB; không bao giờ có trường văn bản
   độ dài thay đổi trên nó.** Dư địa là 740 B (~61 đặc trưng số nữa), nhưng
   một trường văn bản tự do 740 B sẽ **nhân đôi chi phí ghi của lượt ghi
   lịch sử nóng nhất sản phẩm** — `record_decision` chạy mỗi IP mỗi giờ, nên
   một giờ có 3.000 IP thành 3.000 lượt ghi ở 2 WCU trên một bảng 2 WCU.

3. **13 ô đếm vùng sát ngưỡng** gộp vào `record_traffic`. Số khoang đã chốt;
   không còn treo gì.

4. **Sửa `keys.js` tra `#palette` lười.** Không chặn CSS, nhưng **chặn việc
   đổi cấu hình htmx**, mà cấu hình đó là thứ cho phép mô hình điều hướng
   bằng phóng to. Một dòng. Xem 3.1 điểm 1.

**Thứ tự:** 1 và 2 độc lập nhau, làm song song được. 3 phụ thuộc vào không
gì cả. 4 phải xong trước khi chạm vào `base.html`.

---

## 5. Còn treo

1. **Số nguồn thực tế trong dải 3–4σ mỗi giờ.** System ước lượng ~42 sự kiện
   chấm điểm sát ngưỡng mỗi giờ mỗi tenant, gom về **4–10 IP riêng biệt**, và
   **0,046 sự kiện/giây cho toàn sản phẩm** khi bị trần toàn cục chặn. Con số
   này là ước lượng suy ra, chưa đo trên dữ liệu thật. Phương án được chọn
   (gộp vào lượt ghi đã có) **không phụ thuộc vào con số này**, nên nó không
   chặn đường — nhưng nó quyết định dải phản hồi có nói được gì hay không.

2. **Độ trễ lan truyền của ngưỡng.** `ModelManager._cache` sống theo vòng đời
   container ấm, nên một thay đổi ngưỡng lan ra trong vài phút. System nói
   rõ đây là **nghĩa vụ của giao diện, không phải lỗi**: màn hình phải nói ra
   điều đó, hoặc phải phơi ra nút xoá cache mà ADR-002 Stage 3 đã lường
   trước. Chưa quyết chọn cái nào.
