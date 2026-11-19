import { html, $, $$ } from "./dom.js";
import { dayLong, hhmm, money } from "./format.js";
import { DEMO } from "./data.js";
import { wirePhone } from "./phone.js";
import { checkout, resumePayment } from "./payment.js";

export function bookingForm(av, demoPayment = false) {
  if (!av?.days?.length) return html`<p class="note">Расписание сейчас недоступно. Обновите страницу перед бронированием.</p>`;
  return html`
    <form class="booking-form" id="booking" aria-label="Заявка на бронирование">
      <h2 class="h-3">Выберите время</h2>
      <p class="note">${demoPayment ? "Тестовый бокс: после выбора времени откроется демонстрационная оплата без списания денег." : "Время московское. Оплата на месте, напрямую площадке."}</p>
      <label>Дата<select name="date" required>${av.days.map(d => html`
        <option value="${d.date}" ${d.free ? "" : "disabled"}>${dayLong(d.date)}${d.free ? "" : " — мест нет"}</option>`)}</select></label>
      <div class="booking-times">
        <label>С<select name="start" required aria-label="Начало аренды"></select></label>
        <label>До<select name="end" required aria-label="Окончание аренды"></select></label>
      </div>
      <p class="booking-total" aria-live="polite"></p>
      <label>Ваше имя<input name="name" autocomplete="given-name" required minlength="2" maxlength="80"></label>
      <label>Телефон<span class="phone-control"><span aria-hidden="true">+7-</span><input name="phone" type="tel" inputmode="numeric" autocomplete="tel-national" required minlength="13" maxlength="16" pattern="[0-9]{3}-[0-9]{3}-[0-9]{2}-[0-9]{2}" placeholder="777-777-77-77" aria-label="Телефон, код страны +7" aria-describedby="phone-hint phone-error"></span></label>
      <span class="phone-feedback">
        <span class="note" id="phone-hint">Введите 10 цифр номера. +7 уже указан.</span>
        <span class="phone-error" id="phone-error" role="status" aria-live="polite" hidden></span>
      </span>
      <label class="booking-trap" aria-hidden="true">Компания<input name="company" tabindex="-1" autocomplete="off"></label>
      <label class="booking-consent"><input name="consent" type="checkbox" required><span>Согласен на обработку имени и телефона для этой брони.</span></label>
      <button class="btn primary l block" type="submit" ${DEMO ? "disabled" : ""}>${demoPayment ? "Перейти к тестовой оплате" : "Отправить заявку"}</button>
      <p class="booking-result note" role="status" aria-live="polite">${DEMO ? "Режим примеров: заявки не отправляются." : demoPayment ? "После успешной тестовой оплаты бронь подтвердится автоматически. На оплату 10 минут." : "Бронь действует после подтверждения."}</p>
    </form>`;
}

export function wireBooking(root, g, av) {
  const form = $("#booking", root);
  if (!form) return;
  const field = name => form.elements.namedItem(name);
  const validatePhone = wirePhone(field("phone"), $("#phone-error", form));
  const button = $('button[type="submit"]', form);
  const result = $(".booking-result", form);
  let requestKey = crypto.randomUUID();
  let sending = false;
  const day = () => av.days.find(d => d.date === field("date").value);
  function total() {
    const count = Number(field("end").value) - Number(field("start").value);
    $(".booking-total", form).textContent = count > 0 ? `Итого: ${money(count * g.price)}` : "Свободных часов нет";
    button.disabled = DEMO || !(count > 0);
  }
  function ends() {
    const start = Number(field("start").value);
    const blocked = new Set(day()?.blocked || []);
    const options = [];
    if (field("start").value !== "") {
      for (let h = start; h < av.close_hour && !blocked.has(h); h++) options.push(h + 1);
    }
    field("end").innerHTML = String(html`${options.map(h => html`<option value="${h}">${hhmm(h)}</option>`)}`);
    total();
  }
  function starts() {
    const blocked = new Set(day()?.blocked || []);
    const hours = !day() ? [] : Array.from({ length: av.close_hour - av.open_hour }, (_, i) => i + av.open_hour).filter(h => !blocked.has(h));
    field("start").innerHTML = String(html`${hours.map(h => html`<option value="${h}">${hhmm(h)}</option>`)}`);
    ends();
  }
  form.addEventListener("change", e => {
    requestKey = crypto.randomUUID();
    if (e.target.name === "date") starts();
    else if (e.target.name === "start") ends();
    else if (e.target.name === "end") total();
  });
  form.addEventListener("input", () => { requestKey = crypto.randomUUID(); });
  form.addEventListener("submit", async e => {
    e.preventDefault();
    if (sending || DEMO) return;
    if (!validatePhone()) { field("phone").focus(); return; }
    if (!form.reportValidity()) return;
    sending = true;
    const payload = {
      garage_id: g.id, date: field("date").value,
      hours: Array.from({ length: Number(field("end").value) - Number(field("start").value) }, (_, i) => Number(field("start").value) + i),
      name: field("name").value.trim(), phone: "+7" + field("phone").value.replace(/\D/g, ""),
      consent: field("consent").checked, company: field("company").value,
    };
    for (const input of $$('input, select, button', form)) input.disabled = true;
    result.textContent = "Отправляем заявку…";
    try {
      const response = await fetch("/api/public/bookings", {
        method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": requestKey },
        body: JSON.stringify(payload), signal: AbortSignal.timeout(15000),
      });
      const b = await response.json();
      if (!response.ok) throw new Error(b.error?.message || "Не удалось отправить заявку.");
      if (b.payment?.mode === "demo") {
        checkout(form, b, reset);
        return;
      }
      form.innerHTML = String(html`<div class="booking-result" role="status">
        <h2 class="h-3">Заявка №${b.id} отправлена</h2>
        <p>${dayLong(b.date)}, ${hhmm(b.start_hour)}–${hhmm(b.end_hour)}</p>
        <p>Сумма: ${money(b.total)}. Оплата на месте.</p>
        <p>Ожидайте подтверждения по указанному телефону. Отправка заявки ещё не означает подтверждение брони.</p>
      </div>`);
    } catch (error) {
      result.textContent = error.name === "TimeoutError" || error.name === "TypeError"
        ? "Ответ не получен. Повторите отправку с теми же данными — повторная заявка не создастся."
        : error.message;
      for (const input of $$('input, select, button', form)) input.disabled = false;
      total();
      sending = false;
    }
  });
  function reset() {
    for (const input of $$('input, select, button', form)) input.disabled = false;
    sending = false; requestKey = crypto.randomUUID(); total();
    result.textContent = "Оплата не завершена. Выберите время и попробуйте снова.";
  }
  starts();
  if (g.demo_payment && !DEMO) resumePayment(form, g.id, reset);
}
