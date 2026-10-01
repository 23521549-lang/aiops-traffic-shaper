# Hệ thống thiết kế — bản có hiệu lực

Ngày 2026-09-25. Viết sau khi `style-directions.html` đã được áp vào sản phẩm.

Bốn tài liệu trước (`01`–`04`) là **đầu vào**: chúng điều tra và tranh luận.
`05-spec-thiet-ke.md` là **đường biên**: nó nói cái gì được phép tồn tại.
`style-directions.html` là **bản dựng**: nó nói trông như thế nào.

Tài liệu này là thứ còn thiếu: bản ghi các quyết định đã **có hiệu lực trong
code**, để lần sau không ai phải đọc ngược từ CSS ra ý định. Khi tài liệu này
và `tokens.css` mâu thuẫn, `tokens.css` đúng — và đó là một lỗi cần sửa ở đây.

---

## 1. Tài liệu nào quyết định cái gì

| Câu hỏi | Trả lời ở đâu | Tính chất |
|---|---|---|
| Trông như thế nào (chữ, màu, bố cục màn hình, chuyển động) | `style-directions.html` | bản dựng, mở bằng trình duyệt |
| Được phép tồn tại cái gì (ngân sách, điều cấm) | `05-spec-thiet-ke.md` §9, §12 | ràng buộc cứng |
| Vì sao chọn hướng thẩm mỹ đó | `03-tham-my-ba-huong-va-tham-chieu.md` | ba hướng + tham chiếu |
| Vì sao route thật thay vì `#anchor` | `docs/adr/007-portal-redesign.md` | ADR |
| Giá trị cụ thể đang chạy | `services/backend/ui/static/tokens.css` | **nguồn duy nhất** |

Một điều cần nói thẳng: `style-directions.html` là **bản dựng tĩnh**, không
phải spec. Nó không có test nào canh, và nó không biết gì về dữ liệu thật.
Chỗ nào nó và sản phẩm khác nhau, sản phẩm đúng nếu lý do là **sự thật** (luật
1.4: giao diện chỉ được nói điều đúng), còn bản dựng đúng nếu lý do là **thẩm
mỹ**. Hai chỗ đã xử theo nguyên tắc này:

- Bản dựng dùng `preserveAspectRatio="none"` cho trường σ. Sản phẩm không, vì
  kéo giãn biến mọi chấm tròn thành hình bầu dục, mà trường đó **làm bằng
  chấm tròn**.
- Bản dựng đặt sẵn số liệu. Sản phẩm đọc ngưỡng từ chính tenant đang xem, nên
  chú giải màu đổi theo cổng của họ chứ không vẽ mặc định của người khác.

---

## 2. Chế độ mặc định

**Tối là mặc định.** `:root` mang giá trị tối; `[data-theme="light"]` lật
sang sáng; `@media (prefers-color-scheme: light)` chỉ áp khi người dùng chưa
tự chọn (`:root:not([data-theme="dark"])`).

Đây là đảo ngược so với bản cũ. Lý do: màn hình này được mở khi có sự cố,
thường là ban đêm, và nền tối làm ba dải màu tier tách nhau rõ hơn hẳn.

## 3. Màu — màu **là khoảng cách**, không phải phán quyết

Một nguồn ở 3.9σ và một nguồn ở 4.1σ gần như cùng một thứ. Cái khác nhau giữa
chúng là **đường mà khách hàng tự đặt**, và một quyết định là đường kẻ chồng
lên thang đo, không phải thuộc tính của thang đo. Vì vậy thang chạy liên tục
bên dưới, còn cổng vẽ thành hai đường đứt nằm trên.

| Token | Tối | Sáng |
|---|---|---|
| `--bg` | `#08080b` | `#faf9f6` |
| `--surface` | `#101017` | `#ffffff` |
| `--text` | `#ececed` | `#15161a` |
| `--accent` | `#b9a3ff` | `#5b3ad6` |
| `--tier-normal` | `#3fd9c2` | `#0c6b5e` |
| `--tier-limited` | `#ffc247` | `#8a5200` |
| `--tier-blocked` | `#ff6d8a` | `#ab1240` |

Hai luật không được phá:

1. **Không bao giờ chỉ bằng màu.** Mọi chip trạng thái có chữ và viền
   `currentColor`; mọi dải σ có nhãn; ba tier còn có hoa văn SVG riêng (đặc /
   gạch chéo / gạch ca-rô) để sống sót qua ảnh chụp màn hình dán vào ticket,
   qua bản in trắng đen, và qua ~8% nam giới mù màu đỏ-lục.
2. **Không có màu literal trong SVG.** Mọi `fill`/`stroke`/`stop-color` đi qua
   token. Một mã hex trong SVG là bảng màu thứ hai mọc ở chỗ không test CSS
   nào nhìn tới. `test_system_diagram.py` canh điều này.

## 4. Chữ — một họ, tự host

`--font-sans: Inter`. **Một** file woff2 nằm trong repo
(`inter-vi.woff2`, 54.340 B, latin + tiếng Việt, trục wght 100–900) vì §12.9
cấm mọi tài nguyên bên thứ ba, **kể cả font**: một request tới Google Fonts
là một địa chỉ IP khách hàng được gửi cho bên khác, trên một sản phẩm bảo mật.

Không còn `--font-mono`. Thứ mà họ chữ thứ hai từng mua được là **số có bề
ngang bằng nhau**, và `font-variant-numeric: tabular-nums` đặt một lần trên
`body` mua đúng thứ đó mà không cần file thứ hai. Inter có `tnum`, kiểm bằng
cách đọc bảng đặc trưng của chính file font chứ không tin trang mô tả.

Ba lần đổi, và lý do của lần cuối đáng ghi lại vì nó không phải chuyện thẩm mỹ:

| Bản | Họ chữ | Vì sao thôi dùng |
|---|---|---|
| 1 | Plex Sans + Plex Mono | hai họ, và 29 khai báo `--font-mono` không đổi gì |
| 2 | Golos Text | **không có tiếng Việt** |
| 3 | Inter | đang dùng |

Golos Text thiếu 102 điểm mã mà sản phẩm này render sau khi giao diện được
dịch sang tiếng Việt. Hậu quả không phải là chữ xấu mà là **chữ đổi họ giữa
chừng một từ**: mọi ký tự có dấu rơi xuống phông hệ thống. Google phục vụ
Golos theo bốn subset và không subset nào là `vietnamese`, nên không có cách
nào cắt lại để lấy ra. Phát hiện bằng `fontTools`, đọc `cmap` của đúng file
đang được phục vụ, chứ không suy từ tên họ chữ.

Bài học đủ tổng quát để ghi ở đây: **một họ chữ chỉ được chọn sau khi đã kiểm
nó phủ hết bộ ký tự mà sản phẩm thật sự in ra.** Ba ứng viên qua được vòng
đó (Inter, Public Sans, Archivo); Be Vietnam Pro phủ đủ tiếng Việt nhưng
không có `tnum`, nên bị loại theo đúng tiêu chí đã loại Fraunces và
Instrument Serif trước đó.

## 5. Bố cục — một mặt phẳng, không phải chồng khung

Đây là quyết định lớn nhất và là thứ đổi nhiều nhất so với mọi bản trước.

Trước: mỗi mục là một hộp có viền, đặt trên nền màu khác, lồng trong hộp
khác. Đọc một con số phải vượt hai ba đường kẻ không mang thông tin gì.

Giờ **chỉ còn ba loại đường kẻ**, mỗi loại có việc:

1. chia ba vùng của ứng dụng — thanh trên / điều hướng / vùng làm việc;
2. một đường mảnh dưới tiêu đề mỗi mục;
3. giữa các dòng của bảng.

Không còn: viền panel, viền bảng, lưới số liệu, hộp khối chi tiết, viền
trạng thái rỗng, ô vòng đời agent, viền ô cảnh báo, viền khối mã.

**Nền lõm** (`--surface-sunken`) chỉ dùng cho chỗ *đưa thứ gì vào*: ô nhập
liệu và khối code. **Nổi lên khỏi trang** chỉ có hai thứ thực sự nổi: command
palette và thanh chọn dính đáy. Nhờ vậy khi có gì nổi lên thì nó có nghĩa.

**Một ngoại lệ:** trang đăng nhập và trang lỗi giữ nguyên khung. Chúng không
phải một mục của trang nào, mà là một việc duy nhất trên trang trắng, và cái
nền dưới nó chính là thứ nói cho người dùng biết việc đang ở đâu.

### Đơn vị bố cục: thẻ

```html
<section class="c-card">
  <div class="c-card-head">
    <h2 class="c-card-title">Tiêu đề</h2>
    <div class="c-card-meta">dữ kiện làm rõ tiêu đề</div>
  </div>
  <div class="c-card-body">…</div>
</section>
```

Ô bên phải là phần đáng giá nhất. Một tiêu đề "Traffic của bạn, theo khoảng
cách khỏi bình thường" lập tức đặt ra câu hỏi "trong bao lâu?", và câu trả
lời trước đây nằm ba dòng bên dưới, hoặc không có. Nó là **cửa sổ thời gian,
một con số đếm, hoặc chính nút hành động** — không bao giờ là trang trí.

`.c-panel` là cùng khối khoảng trống đó nhưng không có đầu thẻ: một mục không
có gì để đặt tiêu đề. Hai tên, hai việc, một luật CSS chung để không lệch.

`.c-say` là một câu bằng ngôn ngữ người đọc, đặt **trước** con số đầu tiên.
Mọi bản console trước đây mở màn bằng một biểu đồ và để người đọc tự suy ra ý
nghĩa của trục từ chính cái trục đó.

## 6. Chuyển động — 0 request, hoặc không có

Console **không được tự làm mới**. Lý do không phải hiệu năng mà là chi phí:
vượt trần request không sinh hoá đơn, nó khiến `enforce_usage_ceiling` từ
chối telemetry (429) cho **mọi tenant**. Một tab poll 5 giây là 17.280
request/ngày = **52% trần cả ngày của toàn tài khoản**. Một dashboard poll
sẽ tự tắt đúng thứ nó đang báo cáo.

Nên mọi sự sống động đến từ **CSS/SVG lúc render**: 7 `@keyframes` tổng cộng.
Nhịp 5 giây khắp nơi không phải con số đẹp — đó là chu kỳ flush thật của
agent. Mỗi animation phải chịu `prefers-reduced-motion: reduce`.

Chuyển động chỉ được nói một trong hai điều:

- **cái này vừa tích luỹ** (ridge mọc lên, chấm rơi xuống, thanh cổng kéo ra),
- **cái này đang diễn ra ngay lúc này** (quầng sáng thở quanh nguồn đang bị
  giữ, gói tin chạy trên sơ đồ, đèn beacon trên thanh brand).

Không có animation nào chỉ để cho vui.

## 7. Hình học đi vào SVG, không đi vào CSS

CSP là `style-src 'self'`, chặn **cả thuộc tính `style=`** chứ không riêng
khối `<style>`. Nhưng `x`, `width`, `d`, `points` của SVG **không phải CSS**
và chính sách không chạm tới.

Hệ quả thực tế, hai lần đã trả giá:

- Trục σ tách hai lớp: SVG giữ hình, HTML giữ chữ. Thay 223 luật CSS liệt kê
  (~9KB) bằng không luật nào.
- Thanh cổng chuyển sang SVG, bỏ 21 luật `[data-bar="N"]` (~1,2KB) từng làm
  tròn mọi cổng về bội số 5%.

Còn stagger của animation thì **không** dùng được `style="--i: …"` — phải
enumerate bằng `:nth-child`. Đây là chỗ CSP thật sự cắn.

## 8. Ngân sách, và con số hiện tại

| Ràng buộc | Trần | Hiện tại |
|---|---|---|
| CSS mỗi mặt (console) | 80.000 B | **78.891** |
| CSS mỗi mặt (landing) | 80.000 B | **60.317** |
| JS viết tay | 20.480 B / ≤5 file | **18.452 / 4 file** |
| Số stylesheet mỗi mặt phải tải | 3 | 3 |

Ngân sách CSS đo **theo từng mặt**, không phải tổng mọi file. Tổng là con số
không người đọc nào tải. Trước khi tách `landing.css`, người vào console tải
65 luật của trang giới thiệu, còn khách vào trang giới thiệu tải toàn bộ
component của console — cả hai đều không giảm được request nào.

Trần JS là về **khả năng một người đọc hết trong một buổi**, không phải về
dung lượng truyền. Nó là lý do console này không có framework.

## 9. Test nào canh điều gì

| Luật | Test |
|---|---|
| Tương phản WCAG cả hai chế độ | `test_contrast.py` (92 cặp) |
| Ngân sách CSS theo mặt, mỗi asset có `_ASSETS` | `test_static_assets.py` |
| Mỗi mặt tải đúng 3 sheet; console không style class dùng chung | `test_shared_chart_styles.py` |
| Trục không có `style=`, không enumerate hình học | `test_axis_markup.py` |
| Điện thoại 390px không mất control nào | `test_narrow_console.py` |
| Sơ đồ đọc từ hằng số code, không màu literal, không gọi server | `test_system_diagram.py` |
| Mọi màn render được | `test_every_screen_renders.py` |

Không có test nào canh "trông có đẹp không". Đó là việc của con người, và của
`style-directions.html`.

## 10. Còn nợ

- Trang giới thiệu (`landing.html`) mới chỉ được làm phẳng theo §5, chưa dựng
  lại theo ngôn ngữ mới.
- Bảng điều hành của nhà phát hành mới chuyển bố cục thẻ một cách cơ học,
  chưa soi từng màn theo bản dựng.
- `settings.access_request_email` vẫn rỗng; trang giới thiệu chưa nên phát
  hành trước khi có địa chỉ thật.
- Cấu hình htmx của ADR-007 vẫn chưa xác nhận trên trình duyệt thật.
