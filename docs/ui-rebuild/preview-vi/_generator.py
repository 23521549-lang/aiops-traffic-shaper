# -*- coding: utf-8 -*-
"""Bản tiếng Việt, và bàn điều khiển đầy đủ.

Bản trước thiếu nút. Bản này liệt kê MỌI thứ một nền tảng control cho sản
phẩm này cần điều khiển, và đánh dấu trung thực từng cái: có route thật,
xây được, hay bị từ chối vì phá luật chi phí bằng 0.
"""
import io
import os

OUT = "docs/ui-rebuild/preview-vi"
NAV = [
    ("Điều khiển", [("index.html", "Cổng", "2"), ("history.html", "Lịch sử", ""),
                    ("allowed.html", "Cho qua", "6")]),
    ("Hệ thống", [("agents.html", "Agent", "5"), ("model.html", "Mô hình", ""),
                  ("system.html", "Cách hoạt động", "")]),
    ("Sổ sách", [("audit.html", "Nhật ký", "")]),
]
PAGES = ["index.html", "agents.html", "history.html", "allowed.html",
         "model.html", "audit.html", "system.html"]
VI = {"index.html": "Cổng", "agents.html": "Agent", "history.html": "Lịch sử",
      "allowed.html": "Cho qua", "model.html": "Mô hình",
      "audit.html": "Nhật ký", "system.html": "Cách hoạt động"}


def b(label, kind="", attrs="", real=False):
    """Nút. Mặc định là nét đứt: chưa có route trong sản phẩm."""
    cls = "btn" + (" " + kind if kind else "")
    new = "" if real else ' data-new title="Chưa có trong sản phẩm"'
    return f'<button class="{cls}"{new} {attrs}>{label}</button>'


def shell(active, h1, sub, actions, body):
    nav = []
    for group, items in NAV:
        nav.append(f'      <div class="gp">{group}</div>')
        for href, label, ct in items:
            cur = ' aria-current="page"' if href == active else ""
            c = f'<span class="ct">{ct}</span>' if ct else ""
            nav.append(f'      <a href="{href}"{cur}><span>{label}</span>{c}</a>')
    links = " &middot; ".join(f'<a href="{p}">{VI[p]}</a>' for p in PAGES)
    return f'''<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{h1} &middot; Traffic Shaper</title>
<link rel="stylesheet" href="app.css">
</head>
<body>
<div class="mock"><span>Bản dựng xem thử</span>
  <span class="key"><span class="key-s"></span>nét đứt = chưa có trong sản phẩm</span>
  <span class="sp"></span><span>{links}</span></div>
<div class="app">
  <header class="bar">
    <span class="brand"><span class="beacon"></span>Traffic Shaper</span>
    <span class="sp"></span>
    <span class="who">acme-prod</span>
    <span class="cost">Hôm nay <b>0 đồng</b></span>
    {b("Chỉ theo dõi", "", 'data-toggle="pl-top" data-alt="Bật lại bảo vệ"', real=True)}
    {b("Tạm dừng 30 phút")}
    {b("Lệnh<kbd>K</kbd>", real=True)}
    {b("Đăng xuất", real=True)}
  </header>
  <div class="body">
    <nav class="nav">
{chr(10).join(nav)}
    </nav>
    <main class="main">
      <div class="head">
        <div><h1 class="h1">{h1}</h1><p class="sub">{sub}</p></div>
        <div class="head-act">{actions}</div>
      </div>
{body}
    </main>
  </div>
</div>
<script src="app.js"></script>
</body>
</html>
'''


def sec(title, meta, inner):
    m = f'<div class="sec-m">{meta}</div>' if meta else ""
    return (f'      <section class="sec">\n'
            f'        <div class="sec-h"><h2 class="sec-t">{title}</h2>{m}</div>\n'
            f'{inner}      </section>\n')


# ══ đường đi của traffic ═════════════════════════════════════════════
P_PASS = "M26,112 L856,112 C898,112 896,36 938,36 L1024,36"
P_SLOW = "M26,112 L856,112 L1024,112"
P_STOP = "M26,112 L856,112 C898,112 896,190 938,190 L1024,190"
parts = []
for i in range(14):
    parts.append(f'    <circle class="p1" r="3"><animateMotion dur="6s" '
                 f'begin="{i*0.42:.2f}s" repeatCount="indefinite" path="{P_PASS}"/></circle>')
for i in range(3):
    parts.append(f'    <circle class="p3" r="3.6"><animateMotion dur="6s" '
                 f'begin="{1.1+i*1.9:.2f}s" repeatCount="indefinite" path="{P_SLOW}"/></circle>')
parts.append('    <circle class="pk" r="4.4"><animateMotion dur="6s" begin="2.6s" '
             f'repeatCount="indefinite" path="{P_STOP}"/></circle>')
STAGES = [(150, "1 &middot; Đọc", "đọc log truy cập của bạn", False),
          (330, "2 &middot; Đếm", "7 đặc trưng mỗi nguồn", False),
          (510, "3 &middot; Chấm", "so với bình thường của bạn", False),
          (690, "4 &middot; Cổng", "4.00 chậm &middot; 5.00 chặn", True)]
stage_svg = ""
for x, t, s2, on in STAGES:
    cls = "pipe-stage pipe-stage--on" if on else "pipe-stage"
    # Hop bao lay duong day: day o y=112, hop 56..132, nen hat chay XUYEN
    # QUA hop o mot phan ba duoi, con chu nam tren no va khong bi de.
    stage_svg += (f'    <rect class="{cls}" x="{x}" y="56" width="152" height="76" rx="5"/>\n'
                  f'    <text class="pipe-t" x="{x+14}" y="80">{t}</text>\n'
                  f'    <text class="pipe-s" x="{x+14}" y="97">{s2}</text>\n')

PIPE = f'''        <svg class="pipe" viewBox="0 0 1150 232" role="img" aria-labelledby="pp-t pp-d">
          <title id="pp-t">Một request đi đâu, và cái gì quyết định</title>
          <desc id="pp-d">Request vào từ máy chủ của bạn, được đọc, đếm thành bảy đặc
            trưng, chấm điểm so với bình thường của chính bạn, rồi gặp hai cổng bạn đặt.
            24 giờ qua: 183.942 đi thẳng, 246 bị làm chậm, 32 bị chặn.</desc>
          <text class="pipe-z" x="14" y="42">TRAFFIC CỦA BẠN</text>
          <text class="pipe-z" x="150" y="42">TRÊN MÁY CHỦ CỦA BẠN</text>
          <text class="pipe-z" x="510" y="42">TRÊN NỀN TẢNG</text>
          <text class="pipe-z" x="1040" y="18">KẾT CỤC</text>
          <path class="pipe-lane" d="{P_PASS}"/>
          <path class="pipe-lane pipe-lane--hot" d="{P_SLOW}"/>
          <path class="pipe-lane pipe-lane--stop" d="{P_STOP}"/>
{stage_svg}          <line class="pipe-gate" x1="856" y1="18" x2="856" y2="214"/>
          <circle cx="26" cy="112" r="5" fill="#0c0e12"/>
          <text class="pipe-s" x="14" y="134">nginx</text>
          <circle cx="1024" cy="36" r="5" class="p1"/>
          <text class="pipe-n" x="1040" y="32">183.942</text>
          <text class="pipe-s" x="1040" y="49">đi thẳng qua</text>
          <circle cx="1024" cy="112" r="5" class="p3"/>
          <text class="pipe-n" x="1040" y="108">246</text>
          <text class="pipe-s" x="1040" y="125">bị làm chậm 5 phút</text>
          <circle cx="1024" cy="190" r="5" class="pk"/>
          <text class="pipe-n" x="1040" y="186">32</text>
          <text class="pipe-s" x="1040" y="203">bị chặn 1 giờ</text>
{chr(10).join(parts)}
        </svg>

        <div class="pipe-ctl">
          <div><span class="label">Traffic</span>
            <span class="pill on" id="pl-top"><i></i>đang chảy</span></div>
          <div><span class="label">Đọc log</span>
            {b("Tạm dừng đọc")}{b("Đổi đường dẫn log")}</div>
          <div><span class="label">Đếm</span>
            {b("Xem 7 đặc trưng")}{b("Bỏ qua một đặc trưng")}</div>
          <div><span class="label">Chấm điểm</span>
            {b("Quay lui v40")}{b("Đóng băng mô hình")}</div>
          <div><span class="label">Thi hành</span>
            {b("Chỉ theo dõi", "", 'data-toggle="pl-enf" data-alt="Thi hành lại"', real=True)}
            <span class="pill on" id="pl-enf"><i></i>đang thi hành</span></div>
        </div>
'''

ARCH = '''        <svg class="plot" viewBox="0 0 1080 430" role="img" aria-labelledby="ar-t ar-d">
          <title id="ar-t">Agent, nền tảng và bộ nhớ</title>
          <desc id="ar-d">Agent đọc log của bạn và gửi các đặc trưng đã đếm mỗi năm giây.
            Nền tảng chấm điểm và quyết định. Agent của bạn nhận quyết định ở lần gọi kế
            tiếp rồi tự ghi luật trên máy.</desc>
          <rect class="sys-zone" x="14" y="40" width="322" height="300" rx="6"/>
          <rect class="sys-edge" x="368" y="40" width="330" height="300" rx="6"/>
          <rect class="sys-edge" x="730" y="40" width="336" height="300" rx="6"/>
          <text class="sys-z" x="14" y="30">MÁY CHỦ CỦA BẠN</text>
          <text class="sys-z" x="368" y="30">NỀN TẢNG</text>
          <text class="sys-z" x="730" y="30">NHỮNG GÌ NÓ NHỚ</text>

          <rect class="sys-box" x="40" y="62" width="270" height="46" rx="5"/>
          <text class="sys-t" x="54" y="82">nginx hoặc apache</text>
          <text class="sys-s" x="54" y="98">ghi log truy cập, như nó vẫn đang làm</text>
          <path class="sys-wire" d="M175,108 L175,138"/><polygon points="175,146 171,136 179,136" fill="#9aa3e0"/>
          <rect class="sys-box sys-box--on" x="40" y="146" width="270" height="58" rx="5"/>
          <text class="sys-t" x="54" y="166">aiops-agent</text>
          <text class="sys-s" x="54" y="182">7 đặc trưng đếm được cho mỗi nguồn</text>
          <text class="sys-s" x="54" y="196">một lô mỗi 5 giây</text>
          <path class="sys-wire" d="M175,204 L175,238"/><polygon points="175,246 171,236 179,236" fill="#9aa3e0"/>
          <rect class="sys-box" x="40" y="246" width="270" height="70" rx="5"/>
          <text class="sys-t" x="54" y="266">nơi ghi luật</text>
          <text class="sys-s" x="54" y="282">/etc/nginx/aiops-agent-deny.conf</text>
          <text class="sys-s" x="54" y="296">nginx -s reload</text>
          <text class="sys-s" x="54" y="310">hoặc một luật iptables DROP mỗi nguồn</text>

          <path class="sys-wire" d="M310,172 C340,172 340,104 396,104"/>
          <text class="sys-s" x="364" y="100" text-anchor="end">chỉ con số</text>
          <circle class="p5" r="4"><animateMotion dur="5s" repeatCount="indefinite"
            path="M310,172 C340,172 340,104 396,104"/></circle>

          <rect class="sys-box" x="396" y="82" width="278" height="42" rx="5"/>
          <text class="sys-t" x="410" y="101">1 &middot; Nhận lô</text>
          <text class="sys-s" x="410" y="116">chạm trần thì từ chối, với mọi khách hàng</text>
          <rect class="sys-box" x="396" y="136" width="278" height="42" rx="5"/>
          <text class="sys-t" x="410" y="155">2 &middot; So với bình thường của bạn</text>
          <text class="sys-s" x="410" y="170">trung bình và độ lệch của riêng bạn</text>
          <rect class="sys-box" x="396" y="190" width="278" height="42" rx="5"/>
          <text class="sys-t" x="410" y="209">3 &middot; Đặt lên thang đo</text>
          <text class="sys-s" x="410" y="224">một khoảng cách tính bằng sigma, không gì khác</text>
          <rect class="sys-box sys-box--on" x="396" y="244" width="278" height="56" rx="5"/>
          <text class="sys-t" x="410" y="264">4 &middot; Áp cổng bạn đặt</text>
          <text class="sys-s" x="410" y="279">quá 4,00 thì làm chậm 5 phút</text>
          <text class="sys-s" x="410" y="293">quá 5,00 thì chặn 1 giờ</text>

          <path class="sys-wire" d="M674,158 C706,158 706,132 764,132"/>
          <circle class="p5" r="3.5"><animateMotion dur="5s" begin="1.2s" repeatCount="indefinite"
            path="M674,158 C706,158 706,132 764,132"/></circle>
          <rect class="sys-box" x="764" y="108" width="280" height="48" rx="5"/>
          <text class="sys-t" x="778" y="128">bình thường của bạn</text>
          <text class="sys-s" x="778" y="144">huấn luyện lại mỗi đêm, chỉ từ traffic của bạn</text>

          <path class="sys-wire" d="M674,270 C708,270 708,228 764,228"/>
          <circle class="p5" r="3.5"><animateMotion dur="5s" begin="1.7s" repeatCount="indefinite"
            path="M674,270 C708,270 708,228 764,228"/></circle>
          <rect class="sys-box" x="764" y="204" width="280" height="48" rx="5"/>
          <text class="sys-t" x="778" y="224">những gì đang bị giữ</text>
          <text class="sys-s" x="778" y="240">hàng tự hết hạn, luật trên máy bạn thì không</text>

          <path class="sys-wire sys-wire--back" d="M396,286 C346,286 358,366 190,366 L190,326"/>
          <circle class="pk" r="4"><animateMotion dur="5s" begin="2.5s" repeatCount="indefinite"
            path="M396,286 C346,286 358,366 190,366 L190,326"/></circle>
          <text class="sys-s" x="222" y="382">agent của bạn nhận nó ở lần gọi kế tiếp</text>

          <line x1="14" y1="398" x2="1066" y2="398" stroke="#e8e9ed"/>
          <text class="sys-z" x="14" y="422">MỘT NGUỒN, MỌI MÁY CHỦ</text>
          <circle cx="212" cy="418" r="5" class="p5"/><circle cx="230" cy="418" r="5" class="p5"/>
          <circle cx="248" cy="418" r="5" class="p5"/>
          <text class="sys-s" x="268" y="422">bắt ở web-01, chặn ở cả 3 máy, vì quyết định là của bạn chứ không của một máy</text>
          <text class="sys-n" x="1066" y="422" text-anchor="end">0 đồng</text>
        </svg>
'''

FLEET = '''        <svg class="plot" viewBox="0 0 980 230" role="img" aria-labelledby="fl-t fl-d">
          <title id="fl-t">Năm agent, và mỗi cái tươi mới tới đâu</title>
          <desc id="fl-d">Bốn agent đang nhận quyết định mỗi vài giây. Một cái không có bộ
            thi hành nên không ghi luật nào.</desc>
          <rect class="sys-box sys-box--on" x="400" y="88" width="180" height="52" rx="5"/>
          <text class="sys-t" x="490" y="110" text-anchor="middle">một quyết định</text>
          <text class="sys-s" x="490" y="126" text-anchor="middle">cho mọi máy, không theo từng máy</text>
          <g class="sys-wire">
            <path d="M400,114 L220,44"/><path d="M400,114 L220,100"/><path d="M400,114 L220,158"/>
            <path d="M580,114 L760,44"/>
          </g>
          <path class="sys-wire" style="stroke:#d4d6dd;stroke-dasharray:4 4" d="M580,114 L760,166"/>
          <circle class="p5" r="3.5"><animateMotion dur="5s" repeatCount="indefinite" path="M220,44 L400,114"/></circle>
          <circle class="p5" r="3.5"><animateMotion dur="5s" begin=".8s" repeatCount="indefinite" path="M220,100 L400,114"/></circle>
          <circle class="p5" r="3.5"><animateMotion dur="5s" begin="1.6s" repeatCount="indefinite" path="M220,158 L400,114"/></circle>
          <circle class="p5" r="3.5"><animateMotion dur="5s" begin="2.4s" repeatCount="indefinite" path="M760,44 L580,114"/></circle>
          <rect class="sys-box" x="60" y="22" width="160" height="42" rx="5"/>
          <text class="sys-t" x="74" y="41">web-01</text><text class="sys-s" x="74" y="56">nginx &middot; 2 giây</text>
          <rect class="sys-box" x="60" y="80" width="160" height="42" rx="5"/>
          <text class="sys-t" x="74" y="99">web-02</text><text class="sys-s" x="74" y="114">nginx &middot; 4 giây</text>
          <rect class="sys-box" x="60" y="138" width="160" height="42" rx="5"/>
          <text class="sys-t" x="74" y="157">api-01</text><text class="sys-s" x="74" y="172">iptables &middot; 3 giây</text>
          <rect class="sys-box" x="760" y="22" width="160" height="42" rx="5"/>
          <text class="sys-t" x="774" y="41">api-02</text><text class="sys-s" x="774" y="56">nginx &middot; 8 giây</text>
          <rect class="sys-box" x="760" y="144" width="160" height="42" rx="5"/>
          <text class="sys-t" x="774" y="163">batch-01</text>
          <text class="sys-s" x="774" y="178">không bộ thi hành &middot; 4 giờ</text>
          <circle cx="926" cy="165" r="6" fill="#0c0e12"/>
        </svg>
'''

FEAT_A = [("Request mỗi phút", "8,4", "412", 5.8, True),
          ("Giãn cách giữa request", "7,2 s", "0,14 s", 4.9, True),
          ("Số đường dẫn khác nhau", "23", "1", 4.1, True),
          ("Tỉ lệ không tìm thấy", "0,04", "0,94", 3.7, True),
          ("Độ dài phiên", "4 p 20 s", "11 s", 2.1, False),
          ("Byte mỗi request", "14 kB", "2 kB", 1.2, False),
          ("Số user agent", "3,1", "1", 0.4, False)]
FEAT_B = [("Tỉ lệ không tìm thấy", "0,04", "0,71", 4.2, True),
          ("Số đường dẫn khác nhau", "23", "2", 3.9, True),
          ("Request mỗi phút", "8,4", "61", 2.4, False),
          ("Giãn cách giữa request", "7,2 s", "0,98 s", 2.2, False),
          ("Số user agent", "3,1", "1", 1.1, False),
          ("Byte mỗi request", "14 kB", "9 kB", 0.7, False),
          ("Độ dài phiên", "4 p 20 s", "3 p 02 s", 0.3, False)]


def why(pid, rows, raw):
    o = [f'          <div class="src-why" id="{pid}" hidden>']
    for nm, normal, got, sig, drives in rows:
        w = min(100, sig / 6 * 100)
        tint = "#2f3a90" if sig >= 4 else ("#5f6cc4" if sig >= 2 else "#ccd1f0")
        o.append(f'''            <div class="why-row">
              <span class="why-n">{nm}</span>
              <svg class="why-bar" viewBox="0 0 300 12" preserveAspectRatio="none" aria-hidden="true">
                <rect width="300" height="12" rx="2" fill="#f7f8fa"/>
                <rect class="grow" width="{w*3:.0f}" height="12" rx="2" fill="{tint}"/>
              </svg>
              <span class="why-v">{got} <span style="color:#838794">so với {normal}</span></span>
              <span class="why-d{' drives' if drives else ''}">+{sig:.1f} sigma</span>
            </div>''')
    o.append(f'            <p class="why-raw">{raw}</p>')
    o.append('            <div class="why-act">' +
             b("Cho qua<kbd>A</kbd>", "btn--p", real=True) +
             b("Cho qua vì giải thích được", real=True) +
             b("Chặn 24 giờ") + b("Gia hạn thêm 1 giờ") + b("Thả ra ngay") +
             b("Mọi đợt của nguồn này", real=True) + b("Sao chép làm bằng chứng") +
             b("Không bao giờ xét lại") + '</div>')
    o.append('          </div>')
    return "\n".join(o)


def src(pid, ip, note, sigma, verdict, filled, reach, rows, raw):
    mark = "" if filled else " open"
    return f'''        <div class="src">
          <button class="src-h" data-why-toggle aria-expanded="false" aria-controls="{pid}">
            <span><span class="src-ip">{ip}</span><span class="src-sub">{note}</span></span>
            <span class="src-fig">{sigma}</span>
            <span class="verdict{mark}"><i></i>{verdict}</span>
            <span>{reach}</span>
            <span class="src-chev">&#9656; vì sao</span>
          </button>
{why(pid, rows, raw)}
        </div>
'''


GATE = ""
for s2, w, n, note, cur in [("3,50", 100, "1.482", "báo nhầm nhiều hơn", False),
                            ("3,75", 63, "940", "báo nhầm nhiều hơn", False),
                            ("4,00", 25, "371", "đang dùng", True),
                            ("4,25", 11, "164", "", False),
                            ("4,50", 4, "61", "", False)]:
    a = ' aria-current="true"' if cur else ""
    GATE += f'''          <button class="gate-row"{a}>
            <span>{s2} sigma</span>
            <svg class="gate-track" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">
              <rect class="gate-rail" width="100" height="8" rx="1"/>
              <rect class="gate-bar grow" width="{w}" height="8" rx="1"/>
            </svg>
            <span class="gate-n">{n}</span>
            <span class="gate-note">{note}</span>
          </button>
'''

gate_body = (
    sec("Một request đi đâu", "chạy thật &middot; chu kỳ 6 giây &middot; 0 request tới máy chủ", PIPE)
    + sec("Đang bị giữ", "2 nguồn &middot; bấm một dòng để xem vì sao",
          src("w-a", "203.0.113.44", "44 req/s lúc đỉnh &middot; giữ 11 phút", "6,41",
              "đang chặn", True, "3/3 đã ghi luật", FEAT_A,
              "Điểm thô -0,412 &middot; mô hình v41 &middot; quyết định 11 phút trước, từ lô #88.204")
          + src("w-b", "198.51.100.7", "một đường dẫn, 71% không tìm thấy &middot; giữ 40 giây",
                "4,28", "đang làm chậm", False, "1/3 chưa nhận", FEAT_B,
                "Điểm thô -0,208 &middot; mô hình v41 &middot; quyết định 40 giây trước, từ lô #88.207"))
    + sec("Cổng làm chậm", "13 vị trí &middot; bấm là dời",
          '        <div class="gate">\n' + GATE + '        </div>\n'
          '        <div class="ctl-b" style="margin-top:14px">'
          + b("Hẹn giờ đổi cổng") + b("Đặt cổng riêng cho giờ cao điểm")
          + b("Về mặc định khuyến nghị") + '</div>\n')
)

# ══ AGENT ═══════════════════════════════════════════════════════════
AGENTS = [("web-01", "on", "đang chạy", "nginx &middot; v1.4.0 &middot; thu 2 giây trước",
           "Ubuntu 22.04 &middot; 4 vCPU &middot; tải 0,42"),
          ("web-02", "on", "đang chạy", "nginx &middot; v1.4.0 &middot; thu 4 giây trước",
           "Ubuntu 22.04 &middot; 4 vCPU &middot; tải 0,38"),
          ("api-01", "on", "đang chạy", "iptables &middot; v1.4.0 &middot; thu 3 giây trước",
           "Debian 12 &middot; 2 vCPU &middot; tải 1,10"),
          ("api-02", "on", "đang chạy", "nginx &middot; v1.3.2 &middot; thu 8 giây trước",
           "Ubuntu 20.04 &middot; 2 vCPU &middot; tải 0,21"),
          ("batch-01", "off", "không có bộ thi hành", "không &middot; v1.4.0 &middot; thu 4 giờ trước",
           "Debian 12 &middot; 8 vCPU &middot; tải 3,90")]
cards = ""
for i, (nm, st, lbl, meta, host) in enumerate(AGENTS):
    cards += f'''        <div class="ctl">
          <div class="ctl-h"><span class="ctl-n">{nm}</span>
            <span class="pill {st}" id="ag-{i}"><i></i>{lbl}</span></div>
          <div class="ctl-d">{meta}<br>{host}</div>
          <div class="ctl-b">
            {b("Chỉ theo dõi", "", f'data-toggle="ag-{i}" data-alt="Thi hành lại"')}
            {b("Nạp lại nginx")}{b("Xoá luật cục bộ")}{b("Khởi động lại")}
            {b("Đổi bộ thi hành")}{b("Nâng cấp agent")}{b("Đổi khoá")}
            {b("Thu hồi", "btn--d")}
          </div>
        </div>
'''
agents_body = (
    sec("Đội agent của bạn", "5 đã đăng ký &middot; 4 đang thu",
        '        <p class="one">Báo cáo không phải là bảo vệ. Một agent không có bộ'
        ' thi hành vẫn gửi số và <b>không ghi luật nào</b>, mà mọi màn khác đều'
        ' gọi nó là khoẻ. Cái dưới đây được đánh dấu mực, vì đó là một phán'
        ' quyết chứ không phải một độ lớn.</p>\n' + FLEET)
    + sec("Điều khiển từng agent", "và máy chủ nó đang chạy trên đó",
        '        <p class="one">Mỗi nút là một lệnh trên đúng máy đó. '
        '<b>Chỉ theo dõi</b> giữ nó vẫn báo cáo và ngừng ghi luật.</p>\n'
        '        <div class="ctl-grid">\n' + cards + '        </div>\n')
    + sec("Cả đội", "tác động lên cả 5 máy",
          '        <div class="ctl-b">' + b("Nạp lại mọi bộ thi hành")
          + b("Chỉ theo dõi toàn bộ", "", 'data-toggle="pl-all" data-alt="Thi hành lại toàn bộ"', real=True)
          + b("Xoá mọi luật cục bộ") + b("Nâng cấp cả đội")
          + b("Thu hồi mọi khoá", "btn--d") + '</div>\n'
          '        <p class="one" style="margin-top:14px">Thêm một máy:</p>\n'
          '        <div class="code">curl -fsSL https://get.trafficshaper.dev/install.sh | sh\n'
          'sudo aiops-agent register --key acme-prod.&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;</div>\n')
)

# ══ LỊCH SỬ ═════════════════════════════════════════════════════════
EP = [(11.0, 7, "203.0.113.44", "6,41"), (14.5, 5, "198.51.100.7", "4,28"),
      (78.0, 6, "192.0.2.144", "5,02")]
tl = ['        <div class="tl">', '          <div class="tl-axis"></div>']
for d, lbl in enumerate(["T2", "T3", "T4", "T5", "T6", "T7", "CN"]):
    tl.append(f'          <span class="tl-day" style="left:{d/7*100:.2f}%"></span>')
    tl.append(f'          <span class="tl-lbl" style="left:{d/7*100+100/14:.2f}%">{lbl}</span>')
import random
random.seed(11)
for i in range(70):
    r = random.choice([4, 5, 6, 7, 9, 11])
    tl.append(f'          <span class="tl-load" style="left:{i/70*100+0.4:.2f}%;'
              f'width:{r}px;height:{r}px"></span>')
for left, r, ip, sg in EP:
    tl.append(f'''          <button class="tl-ep" style="left:{left}%">
            <span class="cap">{sg} sigma<b>{ip}</b></span><i style="width:{r*2}px;height:{r*2}px"></i>
          </button>''')
tl.append('        </div>')
history_body = (
    sec("Bảy ngày", "3 đợt &middot; bấm một dấu",
        '        <p class="one">Chấm mờ là lượng đo được. '
        '<b>Dấu mực là ba lần thật sự có hành động.</b></p>\n' + "\n".join(tl) + "\n")
    + sec("Các đợt", "bấm một dòng để xem vì sao",
          src("h-a", "203.0.113.44", "T5 14:02 &middot; kéo dài 1 g 12 p", "6,41",
              "đã chặn", True, "3/3 đã ghi", FEAT_A,
              "Điểm thô -0,412 &middot; mô hình v41 &middot; 2.204 request trong đợt")
          + src("h-b", "198.51.100.7", "T5 14:38 &middot; kéo dài 9 p", "4,28",
                "đã làm chậm", False, "3/3 đã ghi", FEAT_B,
                "Điểm thô -0,208 &middot; mô hình v41 &middot; 540 request trong đợt"))
    + sec("Cổng chặn", "7 ngày làm bằng chứng &middot; bấm là dời",
          '        <p class="one">Đo trên traffic giữ lại: '
          '<b>0,27% báo nhầm ở 4,0 sigma; 0,00% ở 5,0</b>.</p>\n'
          '        <div class="gate">\n' + GATE.replace("4,00", "5,00") + '        </div>\n')
)

# ══ CHO QUA ═════════════════════════════════════════════════════════
rows_allow = ""
for ip, who, when, why_, n in [
        ("192.0.2.144", "owner@acme.example", "2 giờ trước", "Bộ kiểm tra uptime của chúng tôi", "4.210"),
        ("203.0.113.9", "ops@acme.example", "Hôm qua", "Đối tác nhập liệu theo lô", "3.806"),
        ("198.51.100.212", "owner@acme.example", "6 ngày trước", "Bot của công cụ tìm kiếm", "2.140"),
        ("203.0.113.77", "ops@acme.example", "12 ngày trước", "Webhook thanh toán", "1.902"),
        ("192.0.2.8", "owner@acme.example", "28 ngày trước", "IP ra của văn phòng", "642"),
        ("198.51.100.40", "ops@acme.example", "34 ngày trước", "Giám sát cũ", "240")]:
    rows_allow += (f'            <tr><td><span class="ip">{ip}</span></td><td>{who}</td>'
                   f'<td>{when}</td><td>{why_}</td><td>{n}</td>'
                   f'<td>{b("Bỏ", "btn--sm")}{b("Vĩnh viễn", "btn--sm")}</td></tr>\n')
allowed_body = (
    sec("Nó tốn gì", "7,0% lượng đo được",
        f'''        <div class="wide-two">
          <div>
            <p class="one">Một địa chỉ được cho qua thì không bị hành động, và
            <b>không dạy gì cho mô hình của bạn</b>. Vế thứ hai mới là cái giá.</p>
            <svg class="plot" viewBox="0 0 520 88" role="img"
                 aria-label="7 phần trăm lượng đo được bị loại khỏi huấn luyện">
              <rect x="0" y="16" width="520" height="28" rx="3" fill="#eceefa"/>
              <rect class="grow" x="0" y="16" width="36" height="28" rx="3" fill="#2f3a90"/>
              <text x="0" y="10" font-size="11" fill="#585c66">12.940 bị loại</text>
              <text x="520" y="10" font-size="11" fill="#838794" text-anchor="end">184.220 đo được</text>
              <text x="0" y="66" font-size="13" font-weight="600" fill="#0c0e12">7,0%</text>
              <text x="34" y="66" font-size="12" fill="#585c66">lượng bạn đo được không thể huấn luyện</text>
              <text x="0" y="82" font-size="11" fill="#838794">Quá khoảng 20% thì mô hình bắt đầu lệch.</text>
            </svg>
          </div>
          <div><span class="label">Điều khiển</span>
            <div class="ctl-b" style="margin-top:8px">
              {b("Cho qua một địa chỉ", "btn--p", real=True)}{b("Nhập từ file")}
              {b("Xuất sổ")}{b("Bỏ mọi mục cũ hơn 90 ngày", "btn--d")}
            </div>
          </div>
        </div>
''')
    + sec("Sổ đăng ký", "6 mục &middot; chỉ ghi thêm, không xoá dấu vết",
          '        <table class="tbl">\n'
          '          <thead><tr><th>Địa chỉ</th><th>Ai cho qua</th><th>Khi nào</th>'
          '<th>Vì sao</th><th>Traffic</th><th></th></tr></thead>\n'
          '          <tbody>\n' + rows_allow + '          </tbody>\n        </table>\n')
)

# ══ MÔ HÌNH ═════════════════════════════════════════════════════════
FEATS = [("Request mỗi phút", 18, 62, "8,4"), ("Số đường dẫn khác nhau", 30, 70, "23"),
         ("Tỉ lệ không tìm thấy", 8, 34, "0,04"), ("Số user agent", 22, 55, "3,1"),
         ("Byte mỗi request", 26, 74, "14 kB"), ("Độ dài phiên", 34, 80, "4 p 20 s"),
         ("Giãn cách giữa request", 14, 58, "7,2 s")]
feat_rows = ""
for nm, a, bb, val in FEATS:
    feat_rows += f'''        <div class="feat">
          <span class="nm">{nm}</span>
          <svg class="plot" viewBox="0 0 300 20" preserveAspectRatio="none" aria-hidden="true">
            <line x1="0" y1="10" x2="300" y2="10" stroke="#e8e9ed"/>
            <rect class="grow" x="{a*3}" y="4" width="{(bb-a)*3}" height="12" rx="2" fill="#ccd1f0"/>
            <rect x="{(a+bb)*1.5-1}" y="0" width="2" height="20" fill="#2f3a90"/>
          </svg>
          <span class="val">TB {val}</span>
          <span class="val">{b("Theo dõi riêng", "btn--sm")}</span>
        </div>
'''
model_body = (
    sec("Mô hình của bạn", "v41 &middot; huấn luyện 6 giờ trước",
        f'''        <div class="stats">
          <div class="stat"><div class="v">v41</div><div class="k">đang chạy</div></div>
          <div class="stat"><div class="v">7</div><div class="k">đặc trưng</div></div>
          <div class="stat"><div class="v">2,1<small>tr</small></div><div class="k">lượt đo</div></div>
          <div class="stat"><div class="v">0,27<small>%</small></div><div class="k">báo nhầm</div></div>
        </div>
        <div class="ctl-b" style="margin-top:20px">
          {b("Quay lui v40", "btn--p")}{b("So v41 với v42")}{b("Đóng băng mô hình")}
          {b("Loại một khoảng thời gian khỏi huấn luyện")}{b("Tải bản mô tả bình thường")}
          {b("Quên hết và dựng lại", "btn--d")}
        </div>
        <p class="one" style="margin-top:14px"><b>Huấn luyện lại ngay</b> không có ở đây,
        và sẽ không có: nó tốn compute theo yêu cầu, tức là phá luật chi phí bằng 0.</p>
''')
    + sec("Bình thường của bạn nghĩa là gì", "trung bình và một độ lệch chuẩn",
          '        <p class="one">Đo từ traffic của riêng bạn. Vạch mực là trung bình '
          '<b>của bạn</b>.</p>\n' + feat_rows)
    + sec("Đêm qua huấn luyện, không thăng cấp", "v42 chấm tệ hơn",
          '''        <dl class="kv">
          <dt>Ứng viên v42</dt><dd>0,44% báo nhầm ở 4,0 sigma</dd>
          <dt>Đang chạy v41</dt><dd>0,27% báo nhầm ở 4,0 sigma</dd>
          <dt>Quyết định</dt><dd>giữ v41</dd>
        </dl>
''')
)

# ══ NHẬT KÝ ═════════════════════════════════════════════════════════
# Mới nhất trước, nghiêm ngặt. Bản trước xếp 22:14 trên 22:09 rồi 22:41,
# và một cuốn sổ sai thứ tự thì không còn là sổ. Ba dòng giữa kể đúng một
# câu chuyện: tắt thi hành lúc 22:09, dời cổng lúc 22:14, bật lại lúc 22:41.
LOG = [("2 giờ trước", "Cho qua một địa chỉ", "192.0.2.144", "", "owner@acme.example"),
       ("hôm qua 22:41", "Bật lại thi hành", "chỉ theo dõi", "đang thi hành", "ops@acme.example"),
       ("hôm qua 22:14", "Dời cổng làm chậm", "3,75 sigma", "4,00 sigma", "ops@acme.example"),
       ("hôm qua 22:09", "Tắt thi hành", "đang thi hành", "chỉ theo dõi", "ops@acme.example"),
       ("3 ngày trước", "Dời cổng chặn", "5,50 sigma", "5,00 sigma", "owner@acme.example"),
       ("6 ngày trước", "Cho qua một địa chỉ", "198.51.100.212", "", "owner@acme.example"),
       ("12 ngày trước", "Đăng ký agent", "api-02", "", "ops@acme.example"),
       ("28 ngày trước", "Thu hồi khoá", "web-old-03", "", "owner@acme.example")]
log_rows = ""
for when, what, old, new, who in LOG:
    move = (f'<span class="log-move"><s>{old}</s> &rarr; <b>{new}</b></span>'
            if new else f'<span class="log-move">{old}</span>')
    log_rows += (f'        <div class="log-row"><span class="log-when">{when}</span>'
                 f'<span class="log-what"><b>{what}</b> &middot; {move}</span>'
                 f'<span class="log-who">{who}</span></div>\n')
audit_body = (
    sec("Ai đổi gì", "8 thay đổi trong 30 ngày",
        '        <p class="one">Sản phẩm <b>đã ghi</b> sổ này từ lâu và chưa từng cho ai '
        'xem. Đây là câu hỏi đầu tiên của mọi buổi rà soát sau sự cố.</p>\n' + log_rows)
    + sec("Điều khiển", "",
          '        <div class="ctl-b">' + b("Xuất sổ") + b("Lọc theo người")
          + b("Lọc theo loại thay đổi") + b("Gửi webhook mỗi thay đổi")
          + b("Báo email khi tắt thi hành") + '</div>\n')
)

# ══ CÁCH HOẠT ĐỘNG + BẢNG NĂNG LỰC ══════════════════════════════════
CAPS = [
    ("Thi hành", [
        ("Chỉ theo dõi / bật lại", "có", "Tập phục vụ về rỗng, agent nhả luật trong ~5 giây. Chạy được với agent đã cài sẵn."),
        ("Tạm dừng có hẹn giờ", "xây được", "Bạn tắt lúc 3 giờ sáng rồi quên bật. Tự bật lại là tính năng an toàn, không phải tiện nghi."),
        ("Thả một nguồn ngay", "có", "Cho qua địa chỉ đó; cùng đường reconcile."),
    ]),
    ("Cổng", [
        ("Dời cổng chậm / chặn", "có", "13 vị trí hợp lệ, mỗi vị trí kèm hệ quả đo được."),
        ("Hẹn giờ đổi cổng", "xây được", "Một hàng nhỏ và một lần đọc thêm."),
        ("Cổng riêng theo khung giờ", "xây được", "Cần một trường nữa trên tenant; đường chấm điểm phải đọc giờ."),
    ]),
    ("Một nguồn", [
        ("Cho qua, kèm lý do", "có", "Ghi sổ, và loại khỏi huấn luyện."),
        ("Chặn tay 24 giờ", "xây được", "Đối xứng với cho qua, cùng bảng."),
        ("Gia hạn / rút ngắn", "xây được", "Một lần ghi expires_at."),
        ("Sao chép làm bằng chứng", "xây được", "Thuần trình duyệt, không tốn gì."),
    ]),
    ("Mô hình", [
        ("Quay lui bản trước", "xây được", "Cần đổi cách lưu: mỗi bản một khoá riêng, thêm một con trỏ vài byte. Rẻ hơn hiện tại."),
        ("Đóng băng, ngừng huấn luyện đêm", "xây được", "Một cờ trên tenant."),
        ("Loại một khoảng thời gian", "xây được", "Đã có cơ chế loại bucket khỏi huấn luyện."),
        ("Huấn luyện lại ngay", "bị từ chối", "Tốn compute theo yêu cầu. Phá luật chi phí bằng 0."),
    ]),
    ("Agent và máy chủ", [
        ("Đăng ký, thu hồi khoá", "có", ""),
        ("Nạp lại nginx, xoá luật cục bộ", "xây được", "Một cờ một lần trong phản hồi telemetry."),
        ("Khởi động lại, nâng cấp", "xây được", "Cùng cơ chế, rủi ro cao hơn: tự khởi động lại mất liên lạc."),
        ("Thu ngay", "bị từ chối", "Agent đã gọi mỗi 5 giây. Nút này chỉ là diễn."),
    ]),
    ("Chi phí", [
        ("Hạ trần của chính mình", "xây được", "Một trường trên item vốn đã đọc."),
        ("Báo khi dùng tới 80%", "xây được", "Cần kênh gửi thông báo."),
    ]),
    ("Sổ sách", [
        ("Nhật ký ai đổi gì", "xây được", "Dữ liệu ĐÃ ghi từ lâu, chỉ thiếu màn hình."),
        ("Xuất bằng chứng CSV", "có", ""),
        ("Webhook mỗi quyết định", "xây được", "Một lần gọi ra ngoài mỗi quyết định; phải đếm vào trần."),
    ]),
]
cap_rows = ""
for group, items in CAPS:
    for i, (name, state, note) in enumerate(items):
        cls = {"có": "", "xây được": " soon", "bị từ chối": " no"}[state]
        g = group if i == 0 else ""
        cap_rows += (f'            <tr><td>{g}</td><td>{name}</td>'
                     f'<td><span class="tagx{cls}"><i></i>{state}</span></td>'
                     f'<td class="why">{note}</td></tr>\n')

system_body = (
    sec("Vòng lặp, từ đầu tới cuối", "một chu kỳ là 5 giây &middot; 0 request từ trang này",
        '        <p class="one">Một console hỏi máy chủ mỗi 5 giây sẽ ăn '
        '<b>52% trần request cả ngày của toàn tài khoản</b>, và chạm trần thì '
        'nền tảng từ chối telemetry của mọi khách hàng. Nên trang này không hỏi '
        'máy chủ điều gì.</p>\n' + ARCH)
    + sec("Nền tảng này cần điều khiển được những gì", "22 mục &middot; 5 đã có",
        '        <p class="one">Cột cuối là phần đáng đọc nhất: <b>một nền tảng miễn phí '
        'được định nghĩa bằng thứ nó từ chối làm</b> nhiều hơn thứ nó làm.</p>\n'
        '        <table class="cap">\n'
        '          <thead><tr><th>Nhóm</th><th>Điều khiển</th><th>Trạng thái</th>'
        '<th>Ghi chú</th></tr></thead>\n'
        '          <tbody>\n' + cap_rows + '          </tbody>\n        </table>\n')
    + sec("Ngân sách hôm nay", "1.418 trên 8.333",
          f'''        <div class="wide-two">
          <div>
            <svg class="plot" viewBox="0 0 520 74" role="img"
                 aria-label="1.418 trên phần 8.333 request của bạn hôm nay">
              <rect x="0" y="14" width="520" height="28" rx="3" fill="#eceefa"/>
              <rect class="grow" x="0" y="14" width="88" height="28" rx="3" fill="#2f3a90"/>
              <text x="0" y="8" font-size="11" fill="#585c66">1.418 đã dùng</text>
              <text x="520" y="8" font-size="11" fill="#838794" text-anchor="end">8.333 phần của bạn</text>
              <text x="0" y="64" font-size="12" fill="#585c66">Chạm trần thì nền tảng
                <tspan font-weight="600" fill="#0c0e12">TỪ CHỐI việc, chứ không tính tiền.</tspan></text>
            </svg>
          </div>
          <div><span class="label">Điều khiển</span>
            <div class="ctl-b" style="margin-top:8px">
              {b("Báo tôi ở 80%")}{b("Hạ trần của tôi")}{b("Xem theo từng agent")}
            </div>
          </div>
        </div>
''')
)

DEFS = [
    ("index.html", "Cổng", "5 agent &middot; báo gần nhất 8 giây trước &middot; <b>184.220</b> request trong 24 giờ",
     b("Xuất CSV", real=True) + b("Thả tất cả ngay", "btn--d"), gate_body),
    ("agents.html", "Agent", "<b>5</b> đã đăng ký &middot; 4 đang thu &middot; 1 không ghi luật",
     b("Thêm agent", real=True) + b("Nâng cấp cả đội"), agents_body),
    ("history.html", "Lịch sử", "7 ngày &middot; <b>3</b> đợt &middot; 1 do bạn cho qua",
     b("1 ngày", real=True) + b("7 ngày", "btn--p", real=True) + b("Xuất", real=True), history_body),
    ("allowed.html", "Cho qua", "<b>6</b> địa chỉ &middot; 7,0% lượng đo được",
     b("Cho qua một địa chỉ", "btn--p", real=True), allowed_body),
    ("model.html", "Mô hình", "v41 &middot; huấn luyện 6 giờ trước &middot; <b>0,27%</b> báo nhầm",
     b("Quay lui v40") + b("Đóng băng"), model_body),
    ("audit.html", "Nhật ký", "<b>8</b> thay đổi trong 30 ngày",
     b("Xuất sổ") + b("Lọc"), audit_body),
    ("system.html", "Cách hoạt động", "Nền tảng này điều khiển được gì, và cố tình không làm gì",
     b("In trang này"), system_body),
]
os.makedirs(OUT, exist_ok=True)
for fn, h1, sub, act, body in DEFS:
    io.open(os.path.join(OUT, fn), "w", encoding="utf-8").write(
        shell(fn, h1, sub, act, body))
    print("wrote", fn)
