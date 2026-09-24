# Review sau khi dựng xong

Viết sau khi bảy phase đã merge, đọc lại toàn bộ đợt rebuild với con mắt
mới. Không phải bản tóm tắt: chỉ ghi những gì **tìm ra thêm** trong vòng tự
kiểm tra, và những gì vẫn còn nợ.

Cách kiểm: đối chiếu code đã ship với spec §12 (những điều từ chối tường
minh) và §9 (bố cục file, ngân sách), quét code chết, quét test rỗng, và
dựng một bài kiểm tra mới cho trạng thái mà không file test nào gieo — tài
khoản trống.

---

## 1. Email của khách hàng đi vào access log

**Nặng nhất.** Spec §12.8 từ chối "ô nhập dài, tự do hoặc bí mật ngoài form
đăng nhập, vì nó đi vào URL, vào log CloudFront, vào `Referer`, vào lịch sử
trình duyệt".

`data-params-in-url` chuyển **mọi** giá trị của form được đánh dấu vào query
string. Chú thích của chính nó lấy chuẩn *"never a credential"* — thấp hơn
chuẩn spec đặt ra. Ba form vượt chuẩn đó, và nặng nhất là form tạo tenant:
người vận hành gõ **email của khách hàng** vào, và nó được ghi thẳng vào access
log của chính nền tảng.

Trớ trêu là lập luận đúng đã nằm sẵn trong `signed-post.js`, file tồn tại
chính vì lý do này: *"a token in a query string lands in CloudFront access
logs, Referer headers and browser history"*. Đúng như thế với mọi thứ khác đặt
vào đó.

**Đã sửa.** Form tạo tenant giờ mang body thật, ký qua `signed-post.js` —
đúng cơ chế ADR-005 dựng ra. Đáp lại bằng **redirect** chỉ mang tenant id
(giá trị vốn đã có trong mọi URL của màn hình đó), và lỗi trả về **plain
text**, vì đó là hợp đồng của file kia: đi theo redirect, còn lại thì in ra
như text.

Hai form còn lại (nhãn agent, lý do allow) được **chặn độ dài** thay vì
chuyển, và đó là quyết định hẹp hơn có chủ ý: chúng là chuỗi khách hàng viết
về hạ tầng của chính họ, không phải dữ liệu của bên thứ ba. Chặn trả lời được
nửa "dài, tự do" của §12.8 mà không phải cho `signed-post.js` thêm ba caller
và một đường thay thế document — thứ đã làm hỏng trải nghiệm trình đọc màn
hình một lần rồi.

Có một test chặn lớp lỗi này quay lại: mọi form còn `data-params-in-url` phải
khai `maxlength` trên từng ô text.

## 2. Dời cổng phải chờ tới đêm mới có hiệu lực

Giá trị của record nằm trên `Tenants`; lần retrain đêm chép nó lên model; và
cho tới giờ **đó là đường duy nhất tới thi hành**. Khách hạ cổng giữa lúc bị
tấn công và không gì thay đổi cho tới lần huấn luyện sau, có thể là 24 giờ.
Màn hình có nói thật điều đó, và nó vẫn là hành vi sai cho một control an
ninh.

**Đã sửa, tốn 0 RCU.** `assert_tenant_active` vốn đã đọc item `Tenants` trên
**mọi** request agent đã xác thực — nó phải đọc, để từ chối tenant bị đình
chỉ. Cổng đã nằm sẵn trên đường dây và bị vứt đi. Giờ nó được `classify` dùng
làm giá trị ưu tiên; bản chép trên model vẫn giữ nguyên làm giá trị dự phòng
cho mọi đường không có ngữ cảnh request. Có test khẳng định số lượt đọc
`Tenants` vẫn đúng **một** lần mỗi batch.

Câu trên màn hình đã đổi theo: nó không còn bảo khách hàng chờ.

## 3. Địa chỉ giữ chỗ trên cửa trước

`access_request_email` mặc định là `access@traffic-shaper.example` — một địa
chỉ **trông có vẻ thật**. Một lần deploy quên đặt giá trị sẽ đặt lên cửa trước
một `mailto:` mở được trình mail, gửi đi, và bật lại ở nơi người đọc không bao
giờ thấy. Đó là kiểu hỏng tệ nhất: **nhìn như đã thành công**.

**Đã sửa.** Mặc định là rỗng. Không có địa chỉ thì trang không in liên kết nào
cả, và nói thẳng rằng quyền truy cập được thu xếp trực tiếp. `/ready` báo cáo
trường này trong `warnings` — có chủ ý **không** chặn readiness, vì rút một
instance đang phục vụ ra khỏi vòng quay vì một liên kết marketing mới là sai
lầm lớn hơn.

## 4. Những thứ nhỏ, cùng một họ

- **`_agents_table.html` mồ côi** — sót lại khi xoá trang Agents bên vận hành
  ở Phase 2. Đúng cái ghi chú tôi tự viết lúc đó: "việc xoá chỉ xong sau lượt
  đọc thứ hai". Lượt thứ ba mới bắt được.
- **`SyntaxWarning: invalid escape sequence '\]'`** trong docstring của
  `test_shipped_scripts.py` — nó trích lại chính con regex hỏng ngày trước.
  Vô hại hôm nay, là lỗi ở Python sau này.
- **Hai test đua với biên bucket 5 giây.** Cả ghi lẫn đọc đều lấy đồng hồ
  thật, nên dưới coverage (chậm hơn) chúng vượt biên và fail. Test ngay bên
  cạnh đã ghim `now=` sẵn vì lý do y hệt; hai test này chỉ là chưa theo.

## 5. Thứ không phải lỗi, và tại sao đáng ghi

Bài kiểm mới báo một tenant mở được `/admin/ui` với mã 200 — nghe như lỗ hổng
phân quyền. Không phải: test thiếu `follow_redirects=False`, nên httpx đi theo
302 tới trang login rồi báo về 200 của trang đó.

Ghi lại vì đây là kiểu test sai đáng sợ nhất — nó **báo động giả về đúng thứ
nguy hiểm nhất**, và cách duy nhất biết được là dựng lại trong môi trường cô
lập chứ không phải đoán theo hướng nào.

## 6. Bài kiểm mới: tài khoản trống

Mỗi màn hình có file test riêng, và mỗi file gieo đúng trạng thái nó nói về.
Tổ hợp không ai viết test là **tài khoản trống** — trạng thái mọi khách hàng ở
trong vào ngày đầu, và trạng thái mọi màn hình của sản phẩm này dành phần lớn
đời để hiển thị.

`test_every_screen_renders.py` đi qua từng màn hình hai lần: một tài khoản
chưa có gì, và một tài khoản có đủ mỗi thứ một cái. Nó cố ý nông — chỉ khẳng
định mọi route trả lời, không route nào lỗi máy chủ, và không trang nào ship
ra biểu thức template chưa render.

---

## Còn nợ

**1. Chưa deploy lần nào.** Bảy phase. Production vẫn thiếu `bulk-select.js`
và `keys.js`. Cache behaviour của `/ui/static/*` phải ship **riêng và cuối
cùng** — đó là bước duy nhất Lambda alias không rollback được. Việc này chạm
production nên cần người quyết định.

**2. `access_request_email` cần một địa chỉ thật** trước khi trang chủ ra
ngoài. Giờ nó hỏng một cách an toàn thay vì im lặng, và `/ready` báo.

**3. Cấu hình htmx của ADR-007 vẫn cần xác nhận trên browser thật** —
`localStorage.getItem("htmx-history-cache")` là `null`, một điều hướng
`hx-push-url` đổi được thanh địa chỉ, và Back rồi Ctrl+K mở được palette.
Điều đầu đã kiểm được từ bytes của htmx đã vendor; hai điều sau là hành vi,
chỉ browser trả lời được. Máy này còn ~1GB RAM trống trên 7,7GB, và một
server local kèm Chromium là đúng thứ đã bị hệ thống dừng hai lần trước đó.

**4. Cổng coverage — đã giải.** Không chạy được một lượt trên máy này, nhưng
chạy hai phần với `--cov-append` rồi `coverage report --fail-under=80` áp
đúng cổng đó lên toàn bộ. **99%**, exit 0. Cách chạy đã ghi vào ghi chú môi
trường.
