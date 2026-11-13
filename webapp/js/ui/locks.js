import { api } from "../api.js";
import { html } from "../dom.js";
import { openSheet } from "./sheet.js";
import { toast } from "./toast.js";

// Ключ хранится до получения ответа: повтор запроса после обрыва связи безопасен.
const keys = new Map();
const timeLabel = (value) => value ? new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" }).format(new Date(value)) : "не получено";

export function lockRows(devices, canOperate) {
  if (!devices.length) return html`<p class="hint">Замки пока не назначены. Выберите объект и подключите тестовый замок.</p>`;
  return html`<div class="rows">${devices.map((d) => html`
    <article class="req">
      <h2 class="title-s">Бокс ${d.garage_id}</h2>
      <p class="caption">${d.provider === "simulator" ? "Тестовый замок" : "Tuya"}: ${d.provider_device_id}</p>
      <p>${d.enabled ? "Включён" : "Отключён"}. ${d.stale ? "Актуальное состояние неизвестно" : "Состояние получено: " + timeLabel(d.observed_at)}</p>
      ${d.telemetry?.battery != null ? html`<p>Заряд батареи: ${d.telemetry.battery}%</p>` : ""}
      ${d.commands.map((c) => html`<div class="mt-m"><p role="status">${c.message}</p><p class="caption">${c.id}</p>
        ${c.state === "unknown" && canOperate ? html`<button class="btn secondary" data-action="resolveLock" data-id="${c.id}">Зафиксировать результат проверки</button>` : ""}</div>`)}
      ${canOperate && d.enabled ? html`<button class="btn secondary mt-m" data-action="manualUnlock" data-id="${d.id}">Открыть вручную</button>` : ""}
    </article>`)}</div>`;
}

export function manualUnlock(deviceId, refreshed) {
  let reason = "";
  let busy = false;
  let sheet;
  sheet = openSheet(html`
    <h2 class="title-m">Ручное открытие</h2>
    <label class="field mt-l"><span>Причина</span><textarea class="input" data-input="reason" maxlength="300"></textarea></label>
    <button class="btn primary l block mt-l" data-action="send">Отправить команду</button>`, {
    actions: {
      "input:reason": (el) => { reason = el.value; },
      async send(el) {
        if (busy) return;
        if (reason.trim().length < 3) { toast("Укажите причину", "err"); return; }
        const previous = keys.get(deviceId);
        if (previous && previous.reason !== reason.trim()) {
          toast("Сначала проверьте результат предыдущего запроса с указанной ранее причиной.", "err");
          return;
        }
        const action = previous || { key: crypto.randomUUID(), reason: reason.trim() };
        keys.set(deviceId, action);
        busy = true; el.disabled = true;
        try {
          const c = await api.admin.unlock(deviceId, action.key, action.reason);
          keys.delete(deviceId);
          sheet.update(html`<h2 class="title-m">Открытие</h2><p role="status">${c.message}</p><button class="btn secondary mt-l" data-action="refresh">Проверить результат</button>`);
        } catch (e) { toast(e.message, "err"); }
        finally { busy = false; el.disabled = false; }
      },
      refresh() { sheet.close(); refreshed(); },
    },
  });
}

export function resolveLock(commandId, refreshed) {
  let reason = "";
  let sheet;
  sheet = openSheet(html`<h2 class="title-m">Результат проверки замка</h2>
    <p>Выберите результат после проверки на месте. Повторная команда не отправляется.</p>
    <label class="field"><span>Что проверено</span><textarea class="input" data-input="reason" maxlength="300"></textarea></label>
    <div class="actions"><button class="btn secondary" data-action="failed">Не выполнено</button><button class="btn secondary" data-action="confirmed">Разблокировка подтверждена</button></div>`, {
    actions: {
      "input:reason": (el) => { reason = el.value; },
      failed: (el) => send(el, "failed"),
      confirmed: (el) => send(el, "device_confirmed"),
    },
  });
  async function send(el, state) {
    el.disabled = true;
    try { await api.admin.resolve(commandId, state, reason); sheet.close(); refreshed(); }
    catch (e) { toast(e.message, "err"); }
    finally { el.disabled = false; }
  }
}

export function configureLock(garageId, existing, refreshed) {
  let provider = existing?.provider || "simulator";
  let id = existing?.provider_device_id || `local-box-${garageId}`;
  let reason = "";
  let sheet;
  sheet = openSheet(html`<h2 class="title-m">Назначить замок</h2>
    <label class="field"><span>Тип</span><select class="input" data-input="provider"><option value="simulator" ${provider === "simulator" ? "selected" : ""}>Тестовый</option><option value="tuya" ${provider === "tuya" ? "selected" : ""}>Tuya (пока отключён)</option></select></label>
    <label class="field mt-m"><span>Идентификатор устройства</span><input class="input" data-input="device" maxlength="128" value="${id}"></label>
    <label class="field mt-m"><span>Причина изменения</span><textarea class="input" data-input="reason" maxlength="300"></textarea></label>
    <div class="actions"><button class="btn secondary" data-action="disable">Отключить</button><button class="btn primary" data-action="save">Сохранить</button></div>`, {
    actions: {
      "input:provider": (el) => { provider = el.value; },
      "input:device": (el) => { id = el.value; },
      "input:reason": (el) => { reason = el.value; },
      save: (el) => save(el, provider === "simulator"),
      disable: (el) => save(el, false),
    },
  });
  async function save(el, enabled) {
    el.disabled = true;
    try { await api.admin.setLock({ garage_id: garageId, provider, provider_device_id: id, enabled, reason }); sheet.close(); refreshed(); }
    catch (e) { toast(e.message, "err"); }
    finally { el.disabled = false; }
  }
}
