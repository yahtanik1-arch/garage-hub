import { html, $, $$, safeStorage } from "./dom.js";
import { dayLong, hhmm, money } from "./format.js";

const storage = safeStorage("session");
const key = id => `gh_demo_payment_${id}`;

async function request(token, action) {
  const response = await fetch("/api/public/demo-payment", {
    method: action ? "POST" : "GET",
    headers: { Authorization: `Demo ${token}`, ...(action ? { "Content-Type": "application/json" } : {}) },
    ...(action ? { body: JSON.stringify({ action }) } : {}),
    signal: AbortSignal.timeout(15000),
  });
  const b = await response.json();
  if (!response.ok) throw new Error(b.error?.message || "Не удалось проверить оплату.");
  return b;
}

export function receipt(form, b) {
  form.innerHTML = String(html`<div class="booking-result" role="status">
    <h2 class="h-3">Бронь №${b.id} подтверждена</h2>
    <p>${dayLong(b.date)}, ${hhmm(b.start_hour)}–${hhmm(b.end_hour)}</p>
    <p>Тестовая оплата: ${money(b.total)}. Деньги не списывались.</p>
    <p>Подтверждение администратора не требуется. Бокс демонстрационный — приезжать и платить не нужно.</p>
    <button class="btn l block" type="button" data-new-booking>Выбрать другое время</button>
  </div>`);
  $("[data-new-booking]", form).onclick = () => { storage.removeItem(key(b.garage.id)); location.reload(); };
}

export function checkout(form, b, onCancel) {
  const token = b.payment.token;
  const storageKey = key(b.garage.id);
  storage.setItem(storageKey, token);
  const dialog = document.createElement("dialog");
  dialog.className = "payment-dialog";
  dialog.setAttribute("aria-labelledby", "payment-title");
  dialog.innerHTML = String(html`
    <h2 class="h-3" id="payment-title">Тестовая оплата</h2>
    <p>${b.garage.name}</p>
    <p>${dayLong(b.date)}, ${hhmm(b.start_hour)}–${hhmm(b.end_hour)}</p>
    <p class="payment-amount">${money(b.total)}</p>
    <p>Это демонстрация. Деньги не списываются, реквизиты карты не нужны.</p>
    <p class="payment-timer" aria-live="off"></p>
    <p class="payment-message" role="status" aria-live="polite"></p>
    <button class="btn primary l block" data-pay>Оплатить тестово</button>
    <button class="btn l block" data-cancel>Отменить оплату</button>`);
  document.body.append(dialog);
  let busy = false;
  const message = $(".payment-message", dialog);
  const focusBefore = document.activeElement;
  function finish(result) {
    if (result.status === "confirmed" && result.payment.status === "paid") {
      close(); receipt(form, result); return true;
    }
    if (result.status !== "pending" || result.payment.status !== "awaiting") {
      storage.removeItem(storageKey); close(); onCancel(); return true;
    }
    return false;
  }
  function close() {
    clearInterval(timer); dialog.close(); dialog.remove(); focusBefore?.focus();
  }
  async function act(action) {
    if (busy) return;
    busy = true;
    for (const button of $$("button", dialog)) button.disabled = true;
    message.textContent = action === "pay" ? "Проверяем тестовую оплату…" : "Проверяем бронь…";
    try {
      const result = await request(token, action);
      if (!finish(result)) message.textContent = "Время ещё зарезервировано за вами.";
    } catch (e) {
      message.textContent = "Ответ не получен: повторите действие. Повторная оплата не создаст новую бронь.";
    } finally {
      busy = false;
      for (const button of $$("button", dialog)) button.disabled = false;
    }
  }
  let checkingExpiry = false;
  const tick = () => {
    const remaining = Math.max(0, Math.ceil((Date.parse(b.payment.expires_at) - Date.now()) / 1000));
    $(".payment-timer", dialog).textContent = `Время на оплату: ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
    if (!remaining && !checkingExpiry && !busy) { checkingExpiry = true; act().finally(() => { checkingExpiry = false; }); }
  };
  const timer = setInterval(tick, 1000);
  $("[data-pay]", dialog).onclick = () => act("pay");
  $("[data-cancel]", dialog).onclick = () => act("cancel");
  dialog.addEventListener("cancel", e => { e.preventDefault(); act("cancel"); });
  dialog.showModal(); tick();
}

export async function resumePayment(form, garageId, onCancel) {
  const token = storage.getItem(key(garageId));
  if (!token) return;
  try {
    const b = await request(token);
    if (b.status === "confirmed" && b.payment.status === "paid") { receipt(form, b); }
    else if (b.status === "pending" && b.payment.status === "awaiting") checkout(form, b, onCancel);
    else storage.removeItem(key(garageId));
  } catch {
    // Keep the capability after a network failure so reload can recover the payment.
  }
}
