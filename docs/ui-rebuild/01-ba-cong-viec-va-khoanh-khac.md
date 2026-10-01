# Báo cáo BA — công việc thật của người dùng, và những khoảnh khắc quyết định niềm tin

Ngày 2026-09-24. Đầu vào cho đợt dựng lại toàn bộ UI/UX.
Mọi đường dẫn tương đối với `services/backend/`.

> Ghi chú của người tổng hợp: mọi cáo buộc có dấu **[đã kiểm]** đã được xác
> minh trực tiếp trong mã nguồn, không chỉ là nhận định của agent.

---

## 1. Công việc người dùng đến làm

### Khách hàng (tenant)

Xếp theo tần suất nhân với mức khẩn cấp.

1. **"Có gì hỏng không, và lỗi tại tôi hay tại các anh?"** — cái liếc mắt 20
   giây. Hôm nay câu này cần ba trang (Protection, Agents, Model) và **không
   trang nào tự trả lời được**.
2. **"Các anh vừa chặn một người. Thuyết phục tôi là các anh đúng."** — phần
   phân rã σ. Hiếm khi xảy ra, nhưng đó *là* toàn bộ sản phẩm. Mọi thứ còn
   lại chỉ là điều kiện cần.
3. **"Cho người này qua, ngay bây giờ."** — cú bấm hoảng loạn lên một khách
   hàng thật, một CDN, hoặc một đầu dò giám sát.
4. **"Tuần này các anh đã làm được gì cho tôi?"** — câu hỏi lúc gia hạn hợp
   đồng. Hiện không trả lời được (xem mục 3).
5. **"Bảo vệ thêm một máy chủ mới."** — thỉnh thoảng, mỗi máy một lần.

### Người vận hành (publisher)

Một người, không lương, trông chừng một thứ có hoá đơn đính kèm.

1. **"Tôi sắp bị tính tiền chưa?"** — thứ duy nhất có thể làm anh ta đau.
2. **"Tenant nào gây ra, và có nên đình chỉ không?"**
3. **"Agent của ai đã chết?"** — vì một agent chết là một khách hàng đang
   tưởng mình được bảo vệ.
4. **"Một tenant vừa gửi mail. Tài khoản họ đang có chuyện gì?"**
5. **"Tạo tenant mới."**

### Người lạ (trang giới thiệu)

1. **"Cái này khác WAF chỗ nào, hay lại một cái dashboard nữa?"**
2. **"Tôi mất gì — tiền, rủi ro, dữ liệu cá nhân, thời gian cài đặt?"**
3. **"Làm sao để có nó?"**

**Phán quyết thẳng về mặt thứ ba:** trang giới thiệu hiện **không có việc số
3**, vì không tồn tại đường đăng ký. `landing.html:252` viết "Install the
agent"; `landing.html:263-266` đòi tenant-id/agent-id/api-key "lấy từ
console"; mà route duy nhất tạo được tenant là `control_platform.py:208`,
chỉ admin gọi được. **[đã kiểm]** Một người lạ đọc xong, tin hoàn toàn, vẫn
không có cách nào trở thành khách hàng.

Hoặc đợt dựng lại phải kèm đường tự đăng ký, hoặc việc của trang giới thiệu
phải được định nghĩa lại cho trung thực là *"thuyết phục một người lạ gửi
email cho người vận hành"* và thiết kế đúng như thế.

Ngược lại, console vận hành chỉ xứng đáng có một trang cho đúng hai việc:
ngân sách và đình chỉ. Trang Agents bên đó là một bộ lọc trên một danh sách,
không phải một công việc.

---

## 2. Những khoảnh khắc quyết định niềm tin

### 2.1 Danh sách trống

Đã biết, đã xử lý một lần (`presenters.py:100-111`) — nhưng cách xử lý nằm ở
từng trang. Phải giải quyết một lần ở tầng chung, nếu không nó sẽ mọc lại.

### 2.2 Cú chặn cứng đầu tiên lên một khách hàng thật **[đã kiểm]**

Khách bấm Allow. Console nói *"Any block on it has been lifted"*
(`ui/dashboard.py:353`). Trang giới thiệu thề rằng lỗi này đã sửa
(`landing.html:307-311`).

**Nó chưa sửa.** `add_whitelist` xoá dòng `MitigationState` phía backend, nên
IP thôi *được phục vụ* quyết định chặn. Nhưng phía agent:

```python
# services/agent/enforcer/__init__.py
def apply(...)          # chỉ THÊM vào self._active
def sweep_expired(...)  # chỉ gỡ khi expires_at cục bộ đã qua
```

Không có gì đối chiếu `_active` với danh sách backend đang phục vụ. Luật
`deny` trong nginx **đứng nguyên tới một tiếng**. Đúng cái lỗi đã có test và
ADR viết riêng cho nó, mọc lại ở tầng ngoài, và giao diện phát biểu kết quả
sai bằng lời.

### 2.3 Tenant bị chặn hạn mức được báo sai nguyên nhân

Vượt quota → 429 → agent ngừng gửi → sau 300 giây chuyển Quiet → console nói
với khách *"tiến trình đã dừng, hoặc nó không với tới được API"*
(`ui/templates/dashboard_agents.html:158-162`). Nguyên nhân thật chỉ người
vận hành nhìn thấy (`ui/control_platform.py:147`). Khách sẽ khởi động lại
một tiến trình đang khoẻ mạnh, lúc 3 giờ sáng, và vô ích.

### 2.4 Kích hoạt lại sau khi bị đình chỉ

Khoá vẫn bị thu hồi, đúng thiết kế (`api/routes/admin.py:72-86`). Tenant
nhìn thấy "Revoked", được cho biết điều đó xảy ra khi tenant bị đình chỉ, và
không được cho biết lý do, không có địa chỉ liên hệ, không có bước tiếp
theo. Agent của họ sẽ không bao giờ tự quay lại.

### 2.5 Đồng hồ đếm ngược đã ngừng đếm

"Ends in 4 min" được tính lúc render (`ui/presenters.py:62-82`) trên một
trang không bao giờ tự làm mới — đúng cái lỗi mà docstring của nó nói là
đang sửa, trong một bộ áo mới. Một tab để mở suốt sự cố sẽ hiển thị một con
số tự tin và sai.

### 2.6 [chưa ai nghĩ tới] Baseline dùng để so sánh không phải baseline đã ra quyết định

`z` được đóng băng lúc ra quyết định (`api/routes/agent.py:138-140`, cố ý).
Nhưng `means`/`stds` trong phần phân rã được đọc **trực tiếp từ mô hình đang
nạp** (`ui/dashboard.py:117-123`). Một lần huấn luyện lại ban đêm chen vào
giữa lúc ra quyết định và lúc xem là đủ để cột "bình thường của bạn" và con
số "+8.2σ" bên cạnh nó mô tả **hai mô hình khác nhau** — im lặng, hợp lý, và
ngay trên màn hình là toàn bộ sự khác biệt của sản phẩm.

### 2.7 [chưa ai nghĩ tới] Cho một IP qua là tự thu hẹp định nghĩa "bình thường" của chính mình

Whitelist vừa miễn trừ khỏi mitigation **vừa** loại khỏi huấn luyện
(`landing.html:303-306`). Một khách hàng cho qua từng IP của CDN trong lúc
xảy ra sự cố đang lần lượt xoá nguồn traffic lớn nhất của mình khỏi
baseline, điều này **làm phồng σ của mọi thứ còn lại** và gây ra lần chặn
nhầm tiếp theo. Không chỗ nào hiển thị tác động tích luỹ của danh sách cho
phép lên mô hình. Không có đường hoàn tác, không có lời nhắc rà soát.

### 2.8 Lệnh cho qua hàng loạt chỉ thành công một phần

Chọn 50, 3 cái sai định dạng, kết quả trả về là hai con số nguyên
(`ui/dashboard.py:238-240`) — người vận hành **không bao giờ được biết ba
cái nào vẫn còn bị chặn**. Trong lúc sự cố, đó là thứ duy nhất họ cần.

---

## 3. Dữ liệu đang thiếu

- **Việc số 2 của khách hàng — điểm khác biệt của sản phẩm — chết sau một
  tiếng. [đã kiểm]** Vector bảy đặc trưng chỉ sống trên dòng
  `MitigationState` đang hoạt động. `MitigationEpisode`
  (`api/routes/dashboard.py:173-184`) lưu số lần theo tier, `last_score`,
  `last_z` — **không có features**. Nên "tại sao cái này bị chặn" chỉ trả lời
  được trong lúc còn chặn (≤1 giờ) và không bao giờ trả lời được cho quá
  khứ. Năng lực duy nhất không ai sánh được lại vắng mặt đúng lúc khách hàng
  hỏi: lúc sau.

- **Việc số 4 của khách hàng — chứng minh giá trị.** Không gì ghi lại số
  request *thực sự bị từ chối*. Agent thi hành cục bộ và không báo lại;
  `TelemetryBatch` chỉ mang log. Câu "tuần này chúng tôi đã chặn 412 thứ cho
  anh" — đúng câu mà bảng lịch sử sinh ra để nói — không tính được. Số quyết
  định không phải số request.

- **Việc số 3 của khách hàng — dấu vết kiểm toán.** `added_by` có trong
  `docs/schema.md` và **không bao giờ được ghi**
  (`api/routes/dashboard.py:82-85`). Trong một tenant nhiều người dùng, "ai
  đã cho IP này vào và vì sao" không có câu trả lời; các mục thêm hàng loạt
  đều mang cùng một chữ `"allowed in bulk"` (`ui/dashboard.py:230`).

- **Việc số 1 của khách hàng — hạn mức.** `tenant_requests_today` tồn tại và
  không có route nào cho khách hàng. Trang giới thiệu hứa *"console sẽ báo
  bạn ở mức 80%"* (`landing.html:250`). Nó không báo.

- **Việc 1 và 2 của người vận hành — quy trách nhiệm chi phí.**
  `UsageCounters` giữ GB-seconds ở mức toàn cục, còn dòng theo tenant chỉ
  mang `total_requests`. Người vận hành không biết tenant nào đang đốt *chi
  phí*, chỉ biết tenant nào đốt số lượng request. Và không có lịch sử — một
  con số cho hôm nay, không có xu hướng, nên "đây là đột biến hay là mức
  bình thường mới" là câu không trả lời được.

- **Mọi thứ đều phải tự vào xem.** Không email, không webhook, không cảnh
  báo, ở bất kỳ đâu trong mã nguồn. Cả "agent của bạn đã chết" lẫn "một
  tenant đã dùng 90% ngân sách ngày" đều đòi hỏi có người đang mở sẵn trang.

- **Sức khoẻ của việc thi hành là không quan sát được.** Không gì phân biệt
  "agent đang gửi telemetry" với "agent đang ghi được luật thành công". Một
  agent không tìm thấy bộ thi hành nào (`services/agent/cli.py:137`) vẫn báo
  cáo vui vẻ và không bảo vệ gì cả; console hiển thị "Reporting", màu xanh.

- **Whitelist chỉ nhận từng IP một** (`schemas/whitelist.py:13`,
  `ui/templates/dashboard_whitelist.html:18`). "Cho dải văn phòng / CDN của
  tôi qua" không diễn đạt được.

- **Chặn thủ công không tồn tại.** `ui/presenters.py:207` ánh xạ một lý do
  `manual_block` mà không đường mã nào ghi ra.

- **Loại trừ khỏi huấn luyện không có giao diện nào**
  (`api/routes/admin.py:89-109`) — biện pháp khắc phục được ghi trong tài
  liệu cho một mô hình bị đầu độc chỉ gọi được bằng API, và khách hàng không
  với tới được.

---

## 4. Giao diện hiện tại sai ở tầng công việc

Không phải "nó xấu", mà là "người đến làm việc X phải làm thêm Y bước, hoặc
không làm được, hoặc bị nói cho một điều sai".

- **"Cho tôi xem mọi thứ nguồn này đã làm" là một đường link chết. [đã kiểm]**
  `dashboard_status.html:212` trỏ tới `/dashboard/ui/history?ip=…`.
  `history_page` (`ui/dashboard.py:412`) **không có tham số `ip`** — route
  JSON thì có (`api/routes/dashboard.py:157`). Người dùng rơi vào lịch sử
  không lọc và bị hiển thị sai đối tượng, trong im lặng.

- **Thanh điều hướng không thể cảnh báo bạn về trang bạn đang không đứng.
  [đã kiểm]** `active_count`, `quiet_count`, `whitelist_count` chỉ được
  truyền bởi chính route của chúng (`ui/dashboard.py:131,184,292`), nên badge
  "3 blocked" chỉ hiện khi bạn *đã* đang nhìn vào chỗ bị chặn. `unread_count`
  (`app_shell.html:27`) **không route nào cấp** — cái badge đó không bao giờ
  render được. Nav là trang trí, không phải trạng thái.

- **"Site tôi có đang được bảo vệ không" tốn ba trang.** Protection biết sức
  khoẻ agent, Agents biết đội máy, Model biết có đang thi hành gì không. Việc
  số 1 đòi bạn đi cả ba trang rồi tự ghép câu trả lời.

- **Việc trung tâm của người vận hành là một con số trần.**
  `_tenants_table.html:36` in `{{ row.used }}` không có mẫu số — trái đúng
  quy tắc do chính `ui/presenters.py:161-166` đặt ra. Hạn mức là 25% của
  ~33.333/ngày; không gì trên màn hình nói thế. Nút Suspend vẫn không nhắm
  được, và bảng không sắp xếp được theo đúng cột duy nhất có ý nghĩa.

- **Ngày đầu tiên nói với khách hàng một điều sai.**
  `dashboard_model.html:50-53`: *"bạn thấy mọi thứ nó thấy."* Khi chưa có mô
  hình, `score_vectors` trả 0.0 cho tất cả (`ml/model.py:119-121`), không gì
  được ghi lại, và không có trang nào hiển thị việc chấm điểm trước khi thi
  hành. 24 giờ đầu của mối quan hệ là một màn hình trống có một lời hứa dán
  lên.

- **"Bảo vệ thêm một máy" kết thúc ở một credential hiện một lần**
  (`dashboard_agents.html:19-43`), nằm trong một trang mà bất kỳ cú bấm nào
  cũng rời đi được, và không có bước xác nhận là đã chạy được.

---

## 5. Tiêu chí thành công, đo được sau sáu tháng

1. Thời gian từ lúc mở console tới câu trả lời đúng cho *"site tôi có đang
   được bảo vệ không"* ≤ **10 giây**, trên **một trang**, đo bằng test tác
   vụ — bao gồm cả trường hợp agent đang im và trường hợp tenant bị chặn hạn
   mức.
2. Phân rã đặc trưng truy xuất được cho **mọi** quyết định sản phẩm từng đưa
   ra trong thời hạn lưu trữ, không chỉ trong một tiếng nó còn hiệu lực. Đo
   bằng: tỷ lệ dòng lịch sử mở được phần "tại sao".
3. **Không câu chữ nào trên giao diện bị hành vi thực tế bác bỏ.** Cụ thể:
   cú bấm Allow gỡ luật ở phía agent, kiểm chứng đầu-cuối; console cảnh báo
   ở mức 80% hạn mức đúng như trang giới thiệu tuyên bố. Cưỡng chế bằng test
   cùng loại với `test_landing_claims.py`, mở rộng sang câu chữ của console.
4. Mọi lần gỡ thi hành đều tạo ra thay đổi quan sát được **trên chính màn
   hình người vận hành đang đứng**, trong một thao tác, **có nêu tên các địa
   chỉ bị ảnh hưởng** — không có kết quả dạng số nguyên trần.
5. Người vận hành gọi được tên tenant tiêu tốn ngân sách nhất, **kèm mẫu số
   và xu hướng 7 ngày**, không rời khỏi một trang; và nhận được thông tin đó
   **mà không cần đang mở trang**.
6. Tỷ lệ cho-qua-lặp-lại giảm: tỷ lệ IP được cho qua mà trước đó từng được
   cho qua rồi gỡ, và số mục cho qua không có lý do ghi lại, cả hai tiến về
   gần 0.
7. Mọi hành động làm thay đổi trạng thái trả lời được "ai, khi nào, vì sao"
   từ dữ liệu đã lưu — kiểm bằng cách bốc ngẫu nhiên một mục whitelist hoặc
   một lần đình chỉ và dựng lại câu chuyện.
8. **Số vụ khách hàng báo "tôi tưởng mình được bảo vệ mà hoá ra không": 0.**
   Đây là con số duy nhất thực sự quan trọng, và mọi tiêu chí phía trên chỉ
   là đại lượng thay thế cho nó.
