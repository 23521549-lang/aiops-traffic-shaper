# Báo cáo Thiết kế — thẩm mỹ, ba hướng, và tham chiếu

Ngày 2026-09-24. Đầu vào cho đợt dựng lại toàn bộ UI/UX.

Đọc trước: `services/backend/ui/static/console.css`,
`services/backend/ui/charts.py`,
`services/backend/ui/presenters.py` (`decompose()`, dòng 241),
`services/backend/ui/dashboard.py`, `services/backend/ui/control_platform.py`.

---

## 0. Vì sao lần thử thứ hai thất bại

Nói gọn trong một câu: **nó là một mặt phẳng để ĐỌC.**

Split pane, header dính, một command palette dùng để điều hướng. **Mọi động
từ đều bị đẩy ra góc màn hình.** Khách hàng nói *nền tảng để control*; một
danh sách cộng một ngăn chi tiết là **một tủ hồ sơ có dán mấy cái nút lên
ngăn kéo**.

Đây không phải vấn đề bảng màu. Bằng chứng nằm ở tầng mã:

```python
# services/backend/ml/model.py
TIER1_Z = -4.0
TIER2_Z = -5.0
```

Hai hằng số module. Console **giải thích một quyết định mà không cho ai thay
đổi nó**. Không tồn tại đường nào để một tenant chỉnh ngưỡng của mình. Đó
chính là lý do hai bản dựng lại tử tế đều đọc ra như dashboard, và tại sao
không bảng màu nào sửa được.

---

## 1. Ba hướng

Không phải ba bảng màu — **ba lý thuyết khác nhau về việc phần mềm này *là*
cái gì**, và do đó con người di chuyển trong nó ra sao.

### 1.1 SET POINT

**Luận đề:** đây không phải phần mềm báo cáo về traffic, nó là **một bộ điều
tốc có điểm đặt**, và việc của người vận hành là dịch chuyển điểm đặt trong
lúc quan sát máy đang phải làm việc nặng đến đâu.

**Màn hình chính — cái Cổng.** Một trục σ nằm ngang, 0 đến 6+, chạy trọn
chiều rộng, có mặt thường trực trên mọi trang. Mỗi nguồn đang sống là một
vạch trên đó. Hai ngưỡng 4σ và 5σ là hai cổng vẽ **trên chính trục ấy**, và
bạn thay đổi mức thi hành bằng cách tác động **lên trục** (POST một điểm đặt
mới), chứ không phải bằng cách mở trang cài đặt. Dưới trục là **dải giảm
độ**: cổng này đang bắt bao nhiêu nguồn, và hôm qua ở mức cũ thì bắt bao
nhiêu. Bạn tinh chỉnh bằng cách nhìn vào **mức can thiệp**, không nhìn vào
đầu vào.

**Điều hướng: độ phóng, không phải trang.** Cùng một trục ở ba mức phóng:
toàn bộ nguồn → một nguồn theo thời gian → một đặc trưng của một nguồn. htmx
tráo vùng biểu đồ; trục không bao giờ di chuyển. **Sidebar chết.** Breadcrumb
là vệt phóng to.

**Chữ và màu:** một bộ sans duy nhất, chữ số dạng bảng ở mọi nơi, và cỡ chữ
chỉ dùng để biểu thị **độ sâu phóng** (con số tiêu đề ở mức phóng hiện tại,
mọi thứ khác một cỡ). Màu là **hệ dải băng, không phải bảng màu**: trục mang
ba dải (dưới 4σ, 4 đến 5, quá 5) và **không thứ gì khác trong sản phẩm được
phép có màu**. Một cái nút có màu sẽ cạnh tranh với thứ màu duy nhất có
nghĩa trên màn hình. Vị trí trên trục là kênh chính, con số in ra là kênh
thứ hai, chữ là kênh thứ ba.

**Từ chối:** trang chủ, các ô đếm số, mọi mục cài đặt. Nếu một giá trị chỉnh
được thì nó chỉnh được ngay tại chỗ nó hiện ra.

### 1.2 THE DOCKET

**Luận đề:** đây là hệ thống **cáo buộc người ta và tước quyền truy cập của
họ**, nên sản phẩm của nó là một **phán quyết có ghi chép, có ngày tháng, và
khiếu nại được**, còn console là bộ hồ sơ các phán quyết cộng với phương tiện
để lật chúng.

**Màn hình chính: cuốn sổ.** Một cột phán quyết xếp theo thời gian, phân cấp
kiểu báo in: một cú chặn 8,2σ được cả khổ rộng cùng một khối bằng chứng sắp
chữ; một cú làm chậm 4,1σ được một dòng. Mở một cái ra là **một trang**,
không phải một ngăn: câu phán quyết viết thành văn xuôi ("error ratio 0.71,
bình thường của bạn là 0.04 ± 0.08, tức +8.2σ"), bảng bằng chứng bảy đặc
trưng bên dưới, ai đã hành động, khi nào hết hiệu lực, và **các thao tác
kháng nghị đặt ở lề, ngay cạnh đúng dòng bằng chứng mà chúng bác bỏ**. "Cho
IP này qua vì tỷ lệ POST giải thích được" nằm ngay cạnh dòng POST ratio.

**Điều hướng: bộ hồ sơ.** Ngày, nguồn, kết quả, và một thứ tự đọc "có gì thay
đổi kể từ". Mọi thứ đều là permalink, vì mục đích tồn tại của một phán quyết
là để được dán vào một ticket.

**Chữ và màu:** khổ chữ văn xuôi thật (65 ký tự) bằng serif hệ thống cho phán
quyết, mono hệ thống chỉ dành cho bằng chứng. Gần như đơn sắc trên nền trắng
giấy; **màu nhấn duy nhất đánh dấu các mục bị tranh chấp** — những cái con
người đã can thiệp lật lại. Màu đánh dấu **sự can thiệp của con người**,
không đánh dấu mức nghiêm trọng, vì mức nghiêm trọng vốn đã là một con số
nằm trong câu.

**Từ chối:** mọi thứ cập nhật trực tiếp, split pane, và mật độ cao. Nó sẽ
không hiển thị 200 dòng cho bạn. Nó lập luận rằng **200 dòng không phân biệt
được chính là thất bại mà sản phẩm sinh ra để ngăn**.

### 1.3 SEVEN AXES

**Luận đề:** chủ thể thật của sản phẩm không phải các địa chỉ IP, mà là
**hình dạng traffic của bạn trong bảy chiều**, và việc thi hành là lãnh thổ
vẽ ra trong không gian đó.

**Màn hình chính: bảy biểu đồ nhỏ**, thang đo giống hệt nhau, mỗi cái một đặc
trưng, mỗi cái hiển thị phân bố baseline của chính tenant này với mọi nguồn
hiện tại chấm lên. Bạn thấy tức khắc rằng sáu đặc trưng đang im lặng và
error ratio có một cụm văng ra +8σ. Chọn một chấm thì nó sáng lên tại vị trí
của nguồn đó **trên cả bảy bảng cùng lúc**. Ngưỡng theo từng đặc trưng được
vẽ thành đường trong các bảng, và bạn kéo lãnh thổ riêng cho từng đặc trưng:
*"riêng POST ratio thì bình thường của tôi vốn đã rộng, đừng hành động
trước 9σ."*

**Điều hướng: ngữ nghĩa bản đồ.** Phóng to đổi **nội dung**, không đổi kích
thước: ở trên cùng bạn thấy bảy phân bố, vào một mức bạn thấy một phân bố có
gắn nhãn các nguồn, vào sâu hơn bạn thấy lịch sử của một nguồn trong đặc
trưng đó.

**Chữ và màu:** các bảng giống hệt nhau về mặt sắp chữ và **không có nhãn**
ngoài một dòng chú thích duy nhất, vì small multiples chỉ hoạt động khi
không gì phân biệt các bảng ngoài chính dữ liệu. Màu mã hoá **đặc trưng nào
đang là nguyên nhân chính**, và chỉ dùng ở đúng một chỗ là huy hiệu nguyên
nhân, khớp theo vị trí trên cả bảy bảng.

**Từ chối:** một con số mức nghiêm trọng duy nhất trên màn hình chính. Nó lập
luận rằng điểm tổng hợp là bản tóm tắt **làm mất thông tin** của bảy chiều,
mà bảy chiều mới là sản phẩm.

---

## 2. Tham chiếu, và đúng phẩm chất được mượn

Cố ý đi ra ngoài ngành SaaS.

1. **DOM / price ladder (Bookmap, CQG)** — đặt lệnh và đọc thị trường là
   **cùng một cử chỉ trên cùng một trục**. Đây là toàn bộ cơ chế của SET
   POINT: cái cổng sống trên chính cái thang mà nó điều khiển.
2. **Universal Audio / channel strip máy nén SSL** — đồng hồ hiển thị **mức
   giảm độ**, tức lượng can thiệp, chứ không hiển thị tín hiệu vào. Bạn đặt
   ngưỡng bằng cách nhìn máy đang làm việc nặng đến đâu.
3. **Thang tốc độ Garmin G1000** — một thang trượt với các cung trắng/lục/
   vàng/đỏ, nơi việc đọc **hoàn toàn là vị trí của kim so với dải màu**.
   Đúng ngữ nghĩa 4σ/5σ, và đã được chứng minh lúc 3 giờ sáng.
4. **ECAM của Airbus** — **cảnh báo tự mang theo quy trình xử lý của nó.**
   Biện pháp khắc phục gắn liền với thông điệp, không bao giờ nằm trong một
   cây cài đặt.
5. **Monitor giường bệnh Philips IntelliVue** — đường giới hạn báo động được
   vẽ **trên chính dải xu hướng của sinh hiệu**. Ngưỡng và phép đo dùng
   chung một hệ toạ độ.
6. **Mnemonic của Bloomberg Terminal** (`AAPL US Equity GO`) — điều hướng
   như một **ngữ pháp tổ hợp** gồm đối tượng cộng chức năng, không phải cây
   menu và cũng không phải danh sách tìm mờ.
7. **Command palette của Linear** — phẩm chất cụ thể: điều hướng trở thành
   **một hành vi gõ chứ không phải một hành vi trỏ**. Yếu hơn Bloomberg ở
   chỗ nó là tìm kiếm chứ không phải ngữ pháp; chỉ trích dẫn nó cho phần
   tiết kiệm phím bấm.
8. **Cấu trúc báo cáo tai nạn NTSB** — phán quyết, rồi phụ lục bằng chứng,
   rồi ghi chép hành động đã thực hiện. Đây là lý do THE DOCKET đọc được đối
   với một luật sư.
9. **Trang nhất Guardian / NYT** — phân cấp bằng **khổ cột và trọng lượng
   tiêu đề, không bao giờ bằng khung hộp**. Một cách nói "cái này quan trọng
   hơn" mà không cần tới card.
10. **Tufte, small multiples (*Envisioning Information*)** — thang đo giống
    hệt, khung giống hệt, để **mắt làm việc so sánh** thay vì bắt người đọc
    làm phép tính. SEVEN AXES phụ thuộc vào điều này theo nghĩa đen.
11. **Hải đồ Admiralty** — số đo độ sâu in ra và đường đẳng sâu suy ra từ
    chúng **cùng tồn tại trên một mặt giấy**, không cái nào che cái nào. Đó
    chính là baseline cộng ngưỡng.
12. **Ngữ nghĩa phóng to của OpenStreetMap** — đối tượng xuất hiện và biến
    mất theo tỷ lệ. Cùng một bản đồ ở hai mức phóng là **nội dung khác
    nhau**, không phải cùng nội dung phóng to.

---

## 3. Khuyến nghị

**Lấy SET POINT làm xương sống. Dùng THE DOCKET làm hình thái chi tiết của
nó. Để dành SEVEN AXES cho một màn hình ở mức phóng sâu nhất của SET POINT**,
nơi nó trở thành trình chỉnh ngưỡng theo từng đặc trưng tốt nhất mà ai đó có
thể thiết kế ra.

**Lập luận:** câu của khách hàng là "nền tảng để control, không phải
dashboard". Câu trả lời **mang tính cấu trúc** duy nhất cho điều đó là làm
cho đối tượng chính **chỉnh được**, và đặt cái điều khiển **sống trên chính
thứ nó điều khiển**. SET POINT cũng vừa với ràng buộc tiền bạc: trục σ cho
cả một tenant tính được từ danh sách mitigation vốn đã đọc sẵn trên
`/dashboard/ui`, nên màn hình chính là **một Query, không toả nhánh**.

### Cái giá, nói thẳng

- **Đây không phải việc của CSS.** Ngưỡng ghi đè theo tenant, nhật ký kiểm
  toán các lần đổi điểm đặt, và phần đối chiếu "mức cổng này hôm qua sẽ bắt
  được gì" đều là **việc backend thật**. Thiếu phần đối chiếu thì cái cổng là
  một thanh trượt không có phản hồi, và như thế **tệ hơn console hiện tại**.
- **Console vận hành hợp với hướng này kém nhất.** Mối bận tâm của người vận
  hành là hạn mức, số tenant và tình trạng sống của agent — **không cái nào
  có σ**. Ép cái trục lên `/admin/ui` là áp đặt phép ẩn dụ bằng sắc lệnh. Hãy
  để mặt vận hành thừa hưởng hệ chữ và hệ màu, và **không thừa hưởng gì
  khác**.
- **Một tenant yên ắng sẽ nhận một màn hình nhạt nhẽo.** Không mitigation
  nào nghĩa là một cái trục trống. **Điều đó phải được thiết kế như trường
  hợp chính, không phải như empty state**: trục hiển thị dải baseline cùng
  câu "bình thường của bạn, đo từ 4,2 triệu request", cộng với dữ kiện sống
  chết của agent từ `agent_health()` — bởi vì **một cái trục trống và một
  agent đã chết không bao giờ được phép trông giống nhau**.
- **Khả năng tiếp cận nhân đôi khối lượng việc.** Vị trí trên một trục là
  thứ trình đọc màn hình không đọc được, nên **mọi biểu đồ đều cần một bảng
  song song** (mẫu này đã có trong `DeviationChart.rows`, và nó phải trở
  thành bắt buộc). Nhiều markup hơn, nhiều byte hơn, trên mỗi trang.
- **Ở 390px, một trục 6σ chỉ còn khoảng 65px cho mỗi sigma.** Đọc được,
  nhưng "bấm vào một mức để đặt cổng" thì thù địch với ngón tay cái. Di động
  cần một điều khiển theo nấc, tức là **một mô hình tương tác thứ hai** phải
  dựng và phải kiểm thử.
- **Nó phục vụ người đọc thông thường kém hơn.** Ai mở console mỗi tháng một
  lần chỉ để xem có gì cháy không sẽ không tìm thấy cái ô nào ghi "ổn". THE
  DOCKET phục vụ người đó tốt hơn — và phục vụ người trực sự cố tệ hơn nhiều.

---

## 4. Điều duy nhất phải đúng

> **Lời giải thích và cái điều khiển phải là cùng một vật thể.**

Trên màn hình của sản phẩm này, câu *"error ratio 0.71, bình thường của bạn
là 0.04 ± 0.08, tức +8.2σ"* và cái điều khiển nói *"với error ratio, đừng
hành động trước +9σ"* **phải là cùng một mảnh hình học, trên cùng một trục,
trong cùng một đơn vị**. Không phải một ngăn chi tiết ở đằng này và một form
cài đặt ở đằng kia: **một cây thước vừa báo độ lệch vừa giữ cái cổng**.

Không sản phẩm nào khác làm được điều này, và lý do chính xác nằm ở
`decompose()`. Một rule engine giải thích chính nó bằng một mã luật, mà một
mã luật thì **không nằm trên thang đo nào**, nên không kéo được. Một dịch vụ
IP reputation giải thích chính nó bằng một ý kiến về traffic của người khác,
thứ **không có cây thước nào trong đơn vị của bạn** cả.

Traffic Shaper là sản phẩm duy nhất mà **lời giải thích của nó vốn đã là một
toạ độ trong không gian đo lường của chính khách hàng** — điều đó có nghĩa
lời giải thích của nó là thứ duy nhất có thể kiêm luôn vai trò mặt điều
khiển.

**Nếu thiết kế cuối cùng đặt phần phân rã và phần tinh chỉnh ở hai nơi khác
nhau, nó đã vứt đi thứ duy nhất mà sản phẩm này sở hữu.**
