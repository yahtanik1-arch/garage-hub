import { api } from "../api.js";
import { html } from "../dom.js";
import { dayLong, mapUrl, money, span } from "../format.js";
import { icon } from "../icons.js";
import { go, patch, render } from "../router.js";
import { setActiveBookings } from "../store.js";
import { haptic, openLink } from "../tg.js";
import { emptyState, errorState, statusLabel, thumb } from "../ui/cards.js";
import { openSheet } from "../ui/sheet.js";
import { toast } from "../ui/toast.js";
import { bookingProps, stepper } from "./done.js";

let bookings = [];
let tab = "upcoming";

const byStart = (a, b) => (a.date + String(a.start_hour).padStart(2, "0")).localeCompare(b.date + String(b.start_hour).padStart(2, "0"));
const upcomingOf = () => bookings.filter((b) => b.can_cancel).sort(byStart);
const historyOf = () => bookings.filter((b) => !b.can_cancel);

function row(b) {
  return html`
    <button class="item press" data-action="open" data-id="${b.id}">
      ${thumb(b.garage.photo)}
      <span class="grow">
        <span class="ellipsis" style="display:block;font-weight:600">${b.garage.name}</span>
        <span class="caption num" style="display:block;margin-top:2px">${dayLong(b.date)}, ${span(b.start_hour, b.end_hour)}</span>
        <span class="between mt-s">${statusLabel(b.status)}<span class="price num" style="font-weight:600">${money(b.total)}</span></span>
      </span>
    </button>`;
}

function listMarkup() {
  const list = tab === "upcoming" ? upcomingOf() : historyOf();
  if (!list.length) {
    return tab === "upcoming"
      ? emptyState({ iconName: "calendar", title: "Предстоящих заявок нет", text: "Выберите бокс и удобное время — это займёт пару минут.", button: html`<button class="btn primary" data-action="find">Найти бокс</button>` })
      : emptyState({ iconName: "list", title: "История пуста", text: "Здесь появятся прошедшие, отменённые и отклонённые заявки." });
  }
  return html`<div class="rows">${list.map(row)}</div>`;
}

function tabsMarkup() {
  const up = upcomingOf().length;
  return html`
    <div class="segmented">
      <button class="${tab === "upcoming" ? "is-on" : ""}" data-action="tab" data-value="upcoming" aria-pressed="${tab === "upcoming"}">Предстоящие${up ? html`<span class="badge">${up}</span>` : ""}</button>
      <button class="${tab === "history" ? "is-on" : ""}" data-action="tab" data-value="history" aria-pressed="${tab === "history"}">История</button>
    </div>`;
}

const shell = (body) => html`<h1 class="title-xl">Заявки</h1>${body}`;

async function load(ctx, { silent = false } = {}) {
  try {
    bookings = (await api.myBookings()).bookings;
    setActiveBookings(upcomingOf().length);
    return !ctx || ctx.isCurrent();
  } catch (e) {
    if (!silent && ctx && ctx.isCurrent()) render(ctx, shell(errorState(e.message)));
    return false;
  }
}

/* ── Заявка в шторке ────────────────────────────────────── */

function detailsMarkup(b) {
  return html`
    <div class="between"><h2 class="title-m" style="margin:0">Заявка ${b.id}</h2>${statusLabel(b.status)}</div>
    ${b.status === "rejected" && b.comment ? html`<p class="alert danger mt-m">Причина отказа: ${b.comment}</p>` : ""}
    <div class="mt-l">${bookingProps(b)}</div>
    ${b.status === "pending" || b.status === "confirmed" ? html`<div class="mt-xl">${stepper(b.status)}</div>` : ""}
    <div class="stack mt-xl">
      ${b.unlock_action ? html`<button class="btn primary l block" data-action="unlock">Открыть бокс</button>` : ""}
      ${b.status === "confirmed" ? html`<button class="btn secondary l block" data-action="route">${icon("route")}Показать на карте</button>` : ""}
      ${b.can_cancel
        ? html`<button class="btn danger l block" data-action="askCancel">Отменить заявку</button>`
        : html`<button class="btn primary l block" data-action="again">Забронировать снова</button>`}
    </div>`;
}

function openDetails(b) {
  let sheet;
  let command = null;
  let sending = false;
  const showCommand = () => sheet.update(html`
    <h2 class="title-m">Открытие бокса</h2>
    <p role="status" aria-live="polite">${command.message}</p>
    ${command.simulated ? html`<p class="hint">Локальная проверка: физический замок не подключён.</p>` : ""}
    <div class="actions">
      ${command.terminal ? html`<button class="btn secondary l" data-action="backToBooking">Вернуться к брони</button>`
        : html`<button class="btn secondary l" data-action="checkCommand">Проверить результат</button>`}
    </div>`);
  sheet = openSheet(detailsMarkup(b), {
    actions: {
      async unlock(el) {
        if (sending) return;
        sending = true;
        el.disabled = true;
        el.classList.add("is-loading");
        try {
          // Один серверный ключ сохраняется при сетевой ошибке и повторном клике.
          command = await api.unlock(b.id, b.unlock_action);
          showCommand();
        } catch (e) { toast(e.message, "err"); }
        finally { sending = false; el.disabled = false; el.classList.remove("is-loading"); }
      },
      async checkCommand(el) {
        el.disabled = true;
        try { command = await api.lockCommand(command.id); showCommand(); }
        catch (e) { toast(e.message, "err"); }
        finally { el.disabled = false; }
      },
      async backToBooking() {
        if (await load(null, { silent: true })) {
          const fresh = bookings.find((x) => x.id === b.id);
          if (fresh) Object.assign(b, fresh);
        }
        sheet.update(detailsMarkup(b));
      },
      route: () => openLink(mapUrl(b.garage)),
      again: () => { sheet.close(); go(`/g/${b.garage.id}/book`); },
      askCancel() {
        haptic.medium();
        sheet.update(html`
          <h2 class="title-m">Отменить заявку?</h2>
          <p class="muted">${b.garage.name}, ${dayLong(b.date)}, ${span(b.start_hour, b.end_hour)}. Время освободится для других.</p>
          <div class="actions">
            <button class="btn secondary l" data-action="keep">Не отменять</button>
            <button class="btn danger l" data-action="doCancel">Отменить</button>
          </div>`);
      },
      keep: () => sheet.update(detailsMarkup(b)),
      async doCancel(el) {
        el.classList.add("is-loading");
        try {
          await api.cancelBooking(b.id);
          sheet.close();
          toast("Заявка отменена");
          await load(null, { silent: true });
          patch("#tabs", tabsMarkup());
          patch("#list", listMarkup());
        } catch (e) {
          el.classList.remove("is-loading");
          toast(e.message, "err");
        }
      },
    },
  });
}

async function enter(ctx) {
  if (ctx.refresh) {
    if (await load(ctx, { silent: true })) { patch("#tabs", tabsMarkup()); patch("#list", listMarkup()); }
    return;
  }
  render(ctx, shell(html`
    <div class="skeleton mt-l" style="height:38px"></div>
    <div class="mt-l">${[1, 2, 3].map(() => html`<div class="row" style="padding:14px 0"><div class="skeleton" style="width:64px;height:48px"></div><div class="grow"><div class="skeleton" style="height:16px;width:70%"></div><div class="skeleton mt-s" style="height:12px;width:50%"></div></div></div>`)}</div>`));
  if (await load(ctx)) {
    render(ctx, shell(html`<div class="mt-l" id="tabs">${tabsMarkup()}</div><div class="mt-s" id="list">${listMarkup()}</div>`), { animate: false });
  }
}

export async function refreshBadge() {
  await load(null, { silent: true });
}

export default {
  enter,
  actions: {
    tab(el) { haptic.select(); tab = el.dataset.value; patch("#tabs", tabsMarkup()); patch("#list", listMarkup()); },
    open(el) { const b = bookings.find((x) => x.id === Number(el.dataset.id)); if (b) openDetails(b); },
    find: () => go("/", { replace: true }),
    retry: () => location.reload(),
  },
};
