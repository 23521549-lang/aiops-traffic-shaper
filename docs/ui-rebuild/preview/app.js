/* Bản dựng xem thử: đủ hành vi để bấm thử, không hơn.
 *
 * Mở lý do, và bật tắt trạng thái trên các nút điều khiển. Đây là file
 * riêng chứ không phải script nội tuyến, vì sản phẩm chạy dưới
 * `script-src 'self'` và một mockup hứa hẹn thứ sản phẩm không làm được là
 * một mockup nói dối. */

// Bấm vào một nguồn thì mở lý do của nó ra, tra tới từng đặc trưng.
document.querySelectorAll("[data-why-toggle]").forEach(function (b) {
  b.addEventListener("click", function () {
    var panel = document.getElementById(b.getAttribute("aria-controls"));
    var open = b.getAttribute("aria-expanded") === "true";
    b.setAttribute("aria-expanded", String(!open));
    b.closest(".src").toggleAttribute("open", !open);
    if (panel) panel.hidden = open;
  });
});

// Các nút điều khiển có hai trạng thái: tạm dừng / chạy lại, và tương tự.
// Trong sản phẩm thật mỗi cái là một POST không thân, nên ở đây chỉ đổi
// nhãn và viên trạng thái đi kèm.
document.querySelectorAll("[data-toggle]").forEach(function (b) {
  b.addEventListener("click", function () {
    var on = b.getAttribute("aria-pressed") === "true";
    b.setAttribute("aria-pressed", String(!on));
    var alt = b.dataset.alt;
    if (alt) { b.dataset.alt = b.textContent.trim(); b.textContent = alt; }
    var pill = document.getElementById(b.dataset.toggle);
    if (pill) {
      pill.classList.toggle("on", on);
      pill.classList.toggle("off", !on);
      var t = pill.dataset.alt;
      if (t) { pill.dataset.alt = pill.lastChild.textContent.trim();
               pill.lastChild.textContent = " " + t; }
    }
  });
});

// Chọn một vị trí cổng.
document.querySelectorAll(".gate").forEach(function (g) {
  g.addEventListener("click", function (e) {
    var row = e.target.closest(".gate-row");
    if (!row) return;
    g.querySelectorAll(".gate-row").forEach(function (r) {
      r.removeAttribute("aria-current");
      var n = r.querySelector(".gate-note");
      if (n && n.dataset.note !== undefined) n.textContent = n.dataset.note;
    });
    row.setAttribute("aria-current", "true");
    var note = row.querySelector(".gate-note");
    if (note) { note.dataset.note = note.textContent; note.textContent = "in use now"; }
  });
});
