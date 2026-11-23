import {html, $} from "./dom.js";
import {dayLong, hhmm, money, mapUrl} from "./format.js";
import {privateBookingUrl, rememberBooking} from "./bookings-store.js";

export function statusText(b) {
  if (b.status === "confirmed") return b.is_past ? "Завершена" : "Подтверждена";
  if (b.status === "rejected") return "Отклонена";
  if (b.status === "cancelled") return b.payment?.status === "expired" ? "Время оплаты истекло" : "Отменена";
  return b.payment?.status === "awaiting" ? "Ожидает оплаты" : "Ожидает подтверждения";
}

export function bookingDetails(b) {
  const paid = b.payment?.status === "paid";
  return html`<div class="booking-details">
    <p class="booking-badge">${statusText(b)}</p>
    <h2 class="h-3">${b.garage.name}</h2>
    <p>${dayLong(b.date)}, ${hhmm(b.start_hour)}–${hhmm(b.end_hour)} · московское время</p>
    <p>${paid ? "Тестовая оплата" : "Стоимость"}: <strong>${money(b.total)}</strong>${paid ? ". Деньги не списывались." : ""}</p>
    ${b.is_demo ? html`<p class="booking-notice">Демонстрационный бокс: физической площадки нет. Приезжать и платить не нужно.</p>` : ""}
    ${b.comment ? html`<p>Причина отказа: ${b.comment}</p>` : ""}
    <section><h3 class="h-3">Адрес</h3><p>${b.garage.address || "Адрес пока не указан"}</p>
      ${b.arrival?.can_show_route ? html`<a class="btn secondary" href="${mapUrl(b.garage)}" target="_blank" rel="noopener noreferrer">Построить маршрут</a>` : ""}
    </section>
    <section><h3 class="h-3">Как попасть в бокс</h3>
      <p class="arrival-instructions">${b.arrival?.instructions || (b.is_past ? "Бронирование завершено." : b.status === "pending" ? "Инструкция будет доступна после подтверждения брони." : "Эта бронь не даёт права на вход.")}</p>
    </section>
    <section><h3 class="h-3">Поддержка</h3><p>${b.support?.message || "Поддержка скоро появится"}</p></section>
    ${b.access_token ? html`<section class="private-booking"><h3 class="h-3">Сохранить бронь</h3>
      <p>В «Моих бронированиях» на этом устройстве или по личной ссылке на другом. Ссылка даёт доступ к этой брони — не публикуйте её.</p>
      <label class="private-link">Личная ссылка<input readonly type="text" value="${privateBookingUrl(b.access_token)}" aria-label="Личная ссылка на бронь" autocomplete="off" spellcheck="false"></label>
      <button type="button" class="btn secondary" data-copy-booking>Скопировать ссылку</button>
      <p class="copy-result note" role="status" aria-live="polite"></p>
      <a href="bookings.html" class="btn secondary">Мои бронирования</a>
    </section>` : ""}
  </div>`;
}

export function wireBookingDetails(root, b) {
  if (b.access_token && !rememberBooking(b)) {
    const result = $(".copy-result", root);
    if (result) result.textContent = "Не удалось сохранить бронь в браузере. Скопируйте личную ссылку, чтобы вернуться к ней.";
  }
  const button = $("[data-copy-booking]", root);
  if (!button) return;
  button.addEventListener("click", async () => {
    const input = $(".private-link input", root), result = $(".copy-result", root);
    try { await navigator.clipboard.writeText(input.value); result.textContent = "Личная ссылка скопирована."; }
    catch { input.focus(); input.select(); result.textContent = "Скопируйте выделенную ссылку."; }
  });
}
