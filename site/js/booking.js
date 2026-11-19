import { html, $, $$ } from "./dom.js";
import { dayLong, hhmm, money } from "./format.js";
import { DEMO } from "./data.js";

export function bookingForm(av) {
  if (!av?.days?.length) return html`<p class="note">Расписание сейчас недоступно. Обновите страницу перед бронированием.</p>`;
  return html`
    <form class="booking-form" id="booking" aria-label="Заявка на бронирование">
      <h2 class="h-3">Выберите время</h2>
      <p class="note">Время московское. Оплата на месте, напрямую площадке.</p>
      <label>Дата<select name="date" required>${av.days.map(d => html`
        <option value="${d.date}" ${d.free ? "" : "disabled"}>${dayLong(d.date)}${d.free ? "" : " — мест нет"}</option>`)}</select></label>
      <div class="booking-times">
        <label>С<select name="start" required aria-label="Начало аренды"></select></label>
        <label>До<select name="end" required aria-label="Окончание аренды"></select></label>
      </div>
      <p class="booking-total" aria-live="polite"></p>
      <label>Ваше имя<input name="name" autocomplete="given-name" required minlength="2" maxlength="80"></label>
      <label>Телефон<input name="phone" type="tel" inputmode="tel" autocomplete="tel" required minlength="10" maxlength="30" placeholder="+7 900 000-00-00"></label>
      <label class="booking-trap" aria-hidden="true">Компания<input name="company" tabindex="-1" autocomplete="off"></label>
      <label class="booking-consent"><input name="consent" type="checkbox" required><span>Согласен на обработку имени и телефона для этой заявки. Со мной свяжутся для подтверждения.</span></label>
      <button class="btn primary l block" type="submit" ${DEMO ? "disabled" : ""}>Отправить заявку</button>
      <p class="booking-result note" role="status" aria-live="polite">${DEMO ? "Режим примеров: заявки не отправляются." : "Бронь действует после подтверждения."}</p>
    </form>`;
}

export function wireBooking(root, g, av) {
  const form = $("#booking", root);
  if (!form) return;
  const field = name => form.elements.namedItem(name);
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
    if (sending || DEMO || !form.reportValidity()) return;
    sending = true;
    const payload = {
      garage_id: g.id, date: field("date").value,
      hours: Array.from({ length: Number(field("end").value) - Number(field("start").value) }, (_, i) => Number(field("start").value) + i),
      name: field("name").value.trim(), phone: field("phone").value,
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
  starts();
}
