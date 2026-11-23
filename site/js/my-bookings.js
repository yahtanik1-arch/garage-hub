import {html, $} from "./dom.js";
import {dayLong, hhmm} from "./format.js";
import {boot} from "./ui.js";
import {savedBookings, loadBookings, rememberBooking, forgetBooking, validToken} from "./bookings-store.js";
import {openBookingDialog, statusText} from "./booking-details.js";
import {checkout} from "./payment.js";

boot();
const root = $("#my-bookings");
let focused = null;
let busy = false;
let refreshAgain = false;
function readPrivateLink() {
  const token = new URLSearchParams(location.hash.slice(1)).get("booking");
  if (token === null) return;
  focused = validToken(token) ? token.toLowerCase() : token;
  history.replaceState(null, "", location.pathname + location.search);
}
readPrivateLink();

function shell() {
  return html`<h1 class="h-1">Мои бронирования</h1>
    <p class="bookings-intro">Здесь сохраняются брони из этого браузера. На другом устройстве откройте личную ссылку на нужную бронь.</p>
    <form class="import-booking"><label for="booking-link">Есть личная ссылка на бронь?</label>
      <div><input id="booking-link" name="link" type="text" maxlength="2000" placeholder="Вставьте личную ссылку" autocomplete="off" required><button class="btn secondary" type="submit">Открыть бронь</button></div>
      <p class="import-message note" role="status" aria-live="polite"></p>
    </form><div id="bookings-list" aria-live="polite"><p>Загружаем бронирования…</p></div>`;
}
root.innerHTML = String(shell());

function card(b) {
  return html`<article class="my-booking" data-booking="${b.id}">
    <div class="my-booking-row"><div><strong>Бронь №${b.id} · ${b.garage.name}</strong>
      <span class="booking-subtitle">${dayLong(b.date)}, ${hhmm(b.start_hour)}–${hhmm(b.end_hour)} · ${statusText(b)}</span></div>
      <button type="button" class="btn secondary" data-open-booking>Открыть бронь</button>
    </div></article>`;
}

let openedToken = null;
function showBooking(b, returnFocus) {
  openedToken = b.access_token;
  openBookingDialog(b, {returnFocus,
    onResume: b => checkout(root, b, refresh, paid => { refresh(); showBooking(paid); }),
    onForget: b => {
      if (!forgetBooking(b.access_token)) { $(".import-message").textContent = "Не удалось изменить сохранённые брони в браузере."; return; }
      if (focused === b.access_token) focused = null;
      openedToken = null;
      refresh();
    },
  });
}

async function refresh() {
  if (busy) { refreshAgain = true; return; }
  busy = true;
  const list = $("#bookings-list");
  try {
    const tokens = new Set(savedBookings().map(b => b.token));
    if (validToken(focused)) tokens.add(focused.toLowerCase());
    const bookings = await loadBookings([...tokens]);
    bookings.forEach(rememberBooking);
    const foundFocused = bookings.some(b => b.access_token === focused);
    if (focused && !foundFocused) $(".import-message").textContent = "Бронь по этой ссылке не найдена. Проверьте, что ссылка скопирована полностью.";
    else $(".import-message").textContent = "";
    bookings.sort((a,b) => (a.date + a.start_hour.toString().padStart(2,"0")).localeCompare(b.date + b.start_hour.toString().padStart(2,"0")));
    const active = bookings.filter(b => !b.is_past && ["pending", "confirmed"].includes(b.status));
    const history = bookings.filter(b => !active.includes(b)).reverse();
    list.innerHTML = String(bookings.length ? html`
      <h2 class="h-3">Предстоящие</h2>${active.length ? active.map(card) : html`<p>Предстоящих бронирований нет.</p>`}
      ${history.length ? html`<h2 class="h-3 booking-history-title">История</h2>${history.map(card)}` : ""}
      <button class="btn secondary" type="button" data-refresh-bookings>Обновить статусы</button>` : html`
      <div class="empty"><h2 class="h-3">Здесь пока нет бронирований</h2><p>Выберите бокс в каталоге или откройте личную ссылку на уже созданную бронь.</p><a class="btn primary" href="catalog.html">Найти бокс</a></div>`);
    for (const b of bookings) {
      const article = $(`[data-booking="${b.id}"]`, list);
      const open = $("[data-open-booking]", article);
      open.onclick = () => showBooking(b, open);
    }
    const imported = bookings.find(b => b.access_token === focused);
    if (imported && focused !== openedToken) showBooking(imported);
    const button = $("[data-refresh-bookings]");
    if (button) button.onclick = refresh;
  } catch {
    list.innerHTML = String(html`<div class="empty"><h2 class="h-3">Не удалось загрузить бронирования</h2><p>Сохранённые ссылки остались на устройстве. Проверьте интернет и попробуйте снова.</p><button class="btn secondary" type="button" data-retry>Повторить</button></div>`);
    $("[data-retry]").onclick = refresh;
  } finally {
    busy = false;
    if (refreshAgain) { refreshAgain = false; refresh(); }
  }
}

$(".import-booking").addEventListener("submit", e => {
  e.preventDefault();
  const raw = $("#booking-link").value.trim();
  try {
    let token = raw;
    if (!validToken(token)) token = new URLSearchParams(new URL(raw, location.href).hash.slice(1)).get("booking");
    if (!validToken(token)) throw new Error();
    focused = token.toLowerCase();
    openedToken = null;
    refresh();
  } catch { $(".import-message").textContent = "Вставьте личную ссылку из подтверждения брони."; }
});
window.addEventListener("hashchange", () => { readPrivateLink(); openedToken = null; refresh(); });
window.addEventListener("storage", e => { if (e.key === "gh_website_bookings_v1") refresh(); });
refresh();
