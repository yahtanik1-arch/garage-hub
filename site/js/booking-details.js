import {html, $} from "./dom.js";
import {dayLong, hhmm, money, mapUrl} from "./format.js";
import {icon} from "./icons.js";
import {privateBookingUrl, rememberBooking} from "./bookings-store.js";

export function statusText(b) {
  if (b.status === "confirmed") return b.is_past ? "Завершена" : "Подтверждена";
  if (b.status === "rejected") return "Отклонена";
  if (b.status === "cancelled") return b.payment?.status === "expired" ? "Время оплаты истекло" : "Отменена";
  return b.payment?.status === "awaiting" ? "Ожидает оплаты" : "Ожидает подтверждения";
}

export function bookingDetails(b, {canForget = false} = {}) {
  const paid = b.payment?.status === "paid";
  const confirmed = b.status === "confirmed" && !b.is_past;
  const stateIcon = confirmed ? "check" : b.status === "pending" ? "clock" : "info";
  return html`<div class="booking-details">
    <div class="booking-overview">
      <span class="booking-status ${confirmed ? "is-confirmed" : ""}">${icon(stateIcon)}${statusText(b)}</span>
      <h2 class="booking-title">${b.garage.name}</h2>
      <div class="booking-schedule"><span>${icon("calendar")}${dayLong(b.date)}</span><span>${icon("clock")}${hhmm(b.start_hour)}–${hhmm(b.end_hour)}</span></div>
      <p class="booking-timezone">Время московское</p>
      <div class="booking-total-row"><div><span>${paid ? "Тестовая оплата" : "Стоимость брони"}</span>
        ${paid ? html`<p>Деньги не списывались</p>` : ""}</div><strong>${money(b.total)}</strong></div>
    </div>
    ${b.is_demo ? html`<div class="booking-demo-note">${icon("info")}<div><strong>Демонстрационная бронь</strong><p>Это тестовый бокс. Приезжать никуда не нужно.</p></div></div>` : html`
      <section class="booking-info-row">${icon("pin")}<div><h3>Адрес</h3><p>${b.garage.address || "Адрес пока не указан"}</p>
        ${b.arrival?.can_show_route ? html`<a class="booking-route" href="${mapUrl(b.garage)}" target="_blank" rel="noopener noreferrer">${icon("route")}Построить маршрут</a>` : ""}</div></section>
      <section class="booking-info-row">${icon("garage")}<div><h3>Как попасть в бокс</h3>
        <p class="arrival-instructions">${b.arrival?.instructions || (b.is_past ? "Бронирование завершено." : b.status === "pending" ? "Инструкция будет доступна после подтверждения брони." : "Эта бронь не даёт права на вход.")}</p></div></section>`}
    ${b.comment ? html`<div class="booking-demo-note">${icon("info")}<div><strong>Причина отказа</strong><p>${b.comment}</p></div></div>` : ""}
    <section class="booking-info-row booking-support">${icon("phone")}<div><h3>Поддержка</h3><p>${b.support?.message || "Поддержка скоро появится"}</p></div></section>
    ${b.access_token ? html`<details class="private-booking"><summary>${icon("share")}<span><strong>Личная ссылка на бронь</strong><small>Для другого устройства</small></span>${icon("down")}</summary><div class="private-booking-body">
      <p>Личная ссылка откроет эту бронь на другом устройстве. Не публикуйте её.</p>
      <label class="private-link">Личная ссылка<input readonly type="text" value="${privateBookingUrl(b.access_token)}" aria-label="Личная ссылка на бронь" autocomplete="off" spellcheck="false"></label>
      <button type="button" class="btn secondary" data-copy-booking>${icon("share")}Скопировать ссылку</button>
      <p class="copy-result note" role="status" aria-live="polite"></p>
      ${canForget ? html`<div class="forget-booking"><button class="btn" type="button" data-dialog-forget>Убрать с этого устройства</button><p class="note">Удаляет сохранённую ссылку, но не отменяет бронь.</p></div>` : ""}
    </div></details>` : ""}
  </div>`;
}

export function wireBookingDetails(root, b) {
  if (b.access_token && !rememberBooking(b)) {
    const result = $(".copy-result", root);
    if (result) result.textContent = "Не удалось сохранить бронь в браузере. Скопируйте личную ссылку, чтобы вернуться к ней.";
    const details = $(".private-booking", root);
    if (details) details.open = true;
  }
  const button = $("[data-copy-booking]", root);
  if (!button) return;
  button.addEventListener("click", async () => {
    const input = $(".private-link input", root), result = $(".copy-result", root);
    try { await navigator.clipboard.writeText(input.value); result.textContent = "Личная ссылка скопирована."; }
    catch { input.focus(); input.select(); result.textContent = "Скопируйте выделенную ссылку."; }
  });
}

export function openBookingDialog(b, {onResume, onForget, returnFocus} = {}) {
  document.querySelectorAll(".booking-dialog").forEach(dialog => dialog.close());
  const dialog = document.createElement("dialog");
  dialog.className = "booking-dialog";
  dialog.setAttribute("aria-labelledby", "booking-dialog-title");
  const resumable = onResume && b.status === "pending" && b.payment?.status === "awaiting";
  dialog.innerHTML = String(html`<header class="booking-dialog-header">
    <h2 id="booking-dialog-title">Бронь №${b.id}</h2>
    <button type="button" class="booking-dialog-close" aria-label="Закрыть информацию о брони">${icon("x")}</button>
    </header><div class="booking-dialog-content">${bookingDetails(b, {canForget: Boolean(onForget)})}</div>
    <footer class="booking-dialog-footer">
      ${!onForget && b.access_token ? html`<a href="bookings.html" class="btn secondary">Мои бронирования</a>` : ""}
      ${resumable ? html`<button class="btn primary" type="button" data-dialog-resume>Продолжить оплату</button>` : html`<button class="btn primary" type="button" data-dialog-done>Готово</button>`}
    </footer>`);
  const previous = returnFocus || document.activeElement;
  let closing = false;
  const close = callback => {
    if (closing) return;
    closing = true;
    dialog.classList.add("is-closing");
    const animations = dialog.getAnimations ? dialog.getAnimations() : [];
    Promise.allSettled(animations.map(animation => animation.finished)).then(() => {
      dialog.close();
      if (callback) callback();
    });
  };
  dialog.addEventListener("close", () => { dialog.remove(); if (previous?.isConnected) previous.focus(); }, {once: true});
  dialog.addEventListener("cancel", e => { e.preventDefault(); close(); });
  $(".booking-dialog-close", dialog).onclick = () => close();
  const done = $("[data-dialog-done]", dialog), resume = $("[data-dialog-resume]", dialog), forget = $("[data-dialog-forget]", dialog);
  if (done) done.onclick = () => close();
  if (resume) resume.onclick = () => close(() => onResume(b));
  if (forget) forget.onclick = () => close(() => onForget(b));
  document.body.append(dialog);
  wireBookingDetails(dialog, b);
  dialog.showModal();
}

export function bookingConfirmation(root, b, onNewBooking) {
  const heading = b.status === "confirmed" ? `Бронь №${b.id} подтверждена`
    : b.status === "pending" ? `Заявка №${b.id} отправлена` : `Бронь №${b.id} · ${statusText(b)}`;
  root.innerHTML = String(html`<div class="booking-result" role="status">
    <h2 class="h-3">${heading}</h2>
    <p>${dayLong(b.date)}, ${hhmm(b.start_hour)}–${hhmm(b.end_hour)} · ${money(b.total)}</p>
    <div class="booking-confirmation-actions"><button type="button" class="btn secondary" data-open-booking>Открыть бронь</button>
    <a href="bookings.html" class="btn secondary">Мои бронирования</a>
    ${onNewBooking ? html`<button class="btn l block" type="button" data-new-booking>Выбрать другое время</button>` : ""}
    </div>
  </div>`);
  rememberBooking(b);
  const open = $("[data-open-booking]", root);
  open.onclick = () => openBookingDialog(b, {returnFocus: open});
  if (onNewBooking) $("[data-new-booking]", root).onclick = onNewBooking;
  openBookingDialog(b, {returnFocus: open});
}
