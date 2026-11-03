/* Раздел администратора: заявки, расписание, объекты, заявки владельцев. Права проверяет сервер. */
import { api } from "../api.js";
import { BOT_USERNAME } from "../config.js";
import { html } from "../dom.js";
import { ago, dayLong, dayNumber, money, span, weekdayShort } from "../format.js";
import { icon } from "../icons.js";
import { patch, render } from "../router.js";
import { callPhone, haptic, openTelegramLink } from "../tg.js";
import { emptyState, errorState, hoursText, statusLabel, thumb } from "../ui/cards.js";
import { openSheet } from "../ui/sheet.js";
import { toast } from "../ui/toast.js";
import { lockRows, manualUnlock, resolveLock, configureLock } from "../ui/locks.js";
import { state } from "../store.js";

const REASONS = ["Это время уже занято", "Площадка в этот день не работает", "На площадке технические работы"];
const SECTIONS = [
  { id: "inbox", label: "Заявки", count: "pending" },
  { id: "day", label: "Расписание" },
  { id: "objects", label: "Объекты" },
  { id: "leads", label: "Владельцы", count: "leads" },
  { id: "locks", label: "Замки" },
];

let section = "inbox";
let dayOffset = 0;
let summary = null;
const items = { inbox: [], day: null, objects: [], leads: [], locks: [] };
let canOperate = false;
let queue = null;

/* ── Помощники ──────────────────────────────────────────── */

const handle = (value) => String(value || "").replace(/^@/, "");
const isHandle = (value) => /^@?[A-Za-z0-9_]{4,}$/.test(String(value || "").trim());

function ownerContacts(x) {
  const parts = [];
  if (x.owner_phone) parts.push(html`<a data-action="call" data-phone="${x.owner_phone}">${x.owner_phone}</a>`);
  if (x.owner_contact) {
    parts.push(isHandle(x.owner_contact)
      ? html`<a data-action="tg" data-handle="${handle(x.owner_contact)}">@${handle(x.owner_contact)}</a>`
      : html`<span>${x.owner_contact}</span>`);
  }
  return parts.length ? html`${parts.map((p, i) => html`${i ? ", " : ""}${p}`)}` : html`контакты не указаны`;
}

function shiftDay(iso, delta) {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + delta);
  return d.toISOString().slice(0, 10);
}

/* ── Заявки ─────────────────────────────────────────────── */

function request(b) {
  const client = b.client_username ? html`<a data-action="tg" data-handle="${b.client_username}">@${b.client_username}</a>` : html`id ${b.client_id}`;
  return html`
    <article class="req" data-id="${b.id}">
      <div class="top">
        <div class="grow">
          <div style="font-weight:600" class="ellipsis">${b.garage.name}</div>
          <div class="num mt-xs">${dayLong(b.date)}, ${span(b.start_hour, b.end_hour)}</div>
        </div>
        ${statusLabel(b.status)}
      </div>
      <div class="caption mt-s num">${hoursText(b.hours)}, ${money(b.total)}. Клиент ${client}${b.minutes_ago != null ? html`, ${ago(b.minutes_ago)}` : ""}</div>
      ${b.comment ? html`<div class="caption mt-xs">Причина отказа: ${b.comment}</div>` : ""}
      <div class="owner">Владелец ${b.owner_name || "не указан"}: ${ownerContacts(b)}</div>
      ${b.status === "pending" ? html`
        <div class="btns">
          <button class="btn secondary" data-action="reject" data-id="${b.id}">Отказать</button>
          <button class="btn primary" data-action="confirm" data-id="${b.id}">Подтвердить</button>
        </div>` : ""}
    </article>`;
}

function inboxMarkup() {
  if (!items.inbox.length) return emptyState({ iconName: "check", title: "Новых заявок нет", text: "Все заявки разобраны. Новые появятся здесь и придут сообщением в бота." });
  return html`<div class="rows">${items.inbox.map(request)}</div>`;
}

function dayMarkup() {
  const d = items.day;
  const strip = html`<div class="dates">${[0, 1, 2, 3, 4, 5, 6].map((i) => {
    const date = d ? shiftDay(d.date, i - d.offset) : null;
    return html`<button class="date ${i === dayOffset ? "is-on" : ""}" data-action="day" data-offset="${i}" aria-pressed="${i === dayOffset}">
      <span class="wd">${i === 0 ? "сегодня" : date ? weekdayShort(date) : ""}</span><span class="dn">${date ? dayNumber(date) : ""}</span></button>`;
  })}</div>`;
  if (!d) return html`${strip}<div class="skeleton mt-l" style="height:120px"></div>`;
  const list = d.bookings.length
    ? html`<div class="rows mt-l">${d.bookings.map(request)}</div>
        <div class="between mt-l"><span class="muted">Подтверждено на сумму</span><span class="price num">${money(d.confirmed_total)}</span></div>`
    : emptyState({ iconName: "calendar", title: "Броней нет", text: `На ${dayLong(d.date)} заявок пока нет.` });
  return html`${strip}${list}`;
}

/* ── Объекты ────────────────────────────────────────────── */

function objectsMarkup() {
  const list = items.objects.length
    ? html`<div class="rows">${items.objects.map((g) => html`
        <button class="item press" data-action="object" data-id="${g.id}">
          ${thumb(g.photos[0])}
          <span class="grow">
            <span class="ellipsis" style="display:block;font-weight:600">${g.name}</span>
            <span class="caption num" style="display:block;margin-top:2px">${[g.district, `${money(g.price)} за час`].filter(Boolean).join(", ")}${g.upcoming ? `, броней: ${g.upcoming}` : ""}</span>
          </span>
          ${g.is_active ? "" : html`<span class="status archived">В архиве</span>`}
          <span class="chev">${icon("right")}</span>
        </button>`)}</div>`
    : emptyState({ iconName: "garage", title: "Объектов нет", text: "Добавьте первый объект в боте кнопкой «Добавить объект»." });
  return html`
    ${list}
    <p class="hint mt-l">${icon("info")}<span>Добавлять объекты, менять данные и фото пока можно только в боте.</span></p>
    <button class="btn secondary l block mt-m" data-action="bot">Открыть бота</button>`;
}

function objectSheet(g) {
  let sheet;
  const markup = () => html`
    <div class="between"><h2 class="title-m" style="margin:0">${g.name}</h2>${g.is_active ? "" : html`<span class="status archived">В архиве</span>`}</div>
    <dl class="props mt-l">
      <dt>Район</dt><dd>${g.district || "—"}</dd>
      <dt>Адрес</dt><dd>${g.address || "—"}</dd>
      <dt>Цена</dt><dd class="num">${money(g.price)} за час</dd>
      <dt>Время для брони</dt><dd class="num">${span(g.open_hour, g.close_hour)}</dd>
      <dt>Фото</dt><dd>${g.photos.length} из 5</dd>
      <dt>Активных броней</dt><dd>${g.upcoming}</dd>
      <dt>Владелец</dt><dd>${g.owner_name || "—"}</dd>
      <dt>Контакты</dt><dd>${ownerContacts(g)}</dd>
    </dl>
    ${g.is_active && g.upcoming ? html`<p class="alert mt-l">У объекта ${g.upcoming} активных броней. После переноса в архив клиенты их не потеряют, но решение по ним за вами.</p>` : ""}
    <div class="mt-xl">
      ${canOperate ? html`<button class="btn secondary l block" data-action="configureLock">Настроить замок</button>` : ""}
      ${g.is_active
        ? html`<button class="btn danger l block" data-action="archive">${icon("archive")}Убрать в архив</button>`
        : html`<button class="btn primary l block" data-action="restore">${icon("restore")}Вернуть в каталог</button>`}
    </div>`;
  const setActive = (fn, message) => async (el) => {
    el.classList.add("is-loading");
    try {
      Object.assign(g, await fn(g.id));
      sheet.close();
      toast(message);
      patch("#section", objectsMarkup());
      loadSummary();
    } catch (e) {
      el.classList.remove("is-loading");
      toast(e.message, "err");
    }
  };
  sheet = openSheet(markup(), {
    actions: {
      call: (el) => callPhone(el.dataset.phone),
      tg: (el) => openTelegramLink(`https://t.me/${el.dataset.handle}`),
      archive: setActive(api.admin.archive, "Объект убран в архив"),
      restore: setActive(api.admin.restore, "Объект снова в каталоге"),
      configureLock() {
        sheet.close();
        configureLock(g.id, items.locks.find((d) => d.garage_id === g.id), loadSection);
      },
    },
  });
}

/* ── Заявки владельцев ──────────────────────────────────── */

function leadsMarkup() {
  if (!items.leads.length) return emptyState({ iconName: "mail", title: "Новых заявок нет", text: "Заявки владельцев на размещение появятся здесь." });
  return html`<div class="rows">${items.leads.map((r) => html`
    <article class="req" data-id="${r.id}">
      <div class="top"><div class="grow"><div style="font-weight:600">${r.name}</div><div class="mt-xs">${r.address}</div></div><span class="status pending">${r.object_type}</span></div>
      <div class="owner"><a data-action="call" data-phone="${r.phone}">${r.phone}</a>${isHandle(r.telegram) ? html`, <a data-action="tg" data-handle="${handle(r.telegram)}">@${handle(r.telegram)}</a>` : r.telegram ? html`, ${r.telegram}` : ""}</div>
      <div class="btns"><button class="btn secondary" data-action="leadDone" data-id="${r.id}">Отметить обработанной</button></div>
    </article>`)}</div>`;
}

/* ── Каркас ─────────────────────────────────────────────── */

function tabsMarkup() {
  const s = summary || {};
  return html`<div class="segmented compact">${SECTIONS.map((x) => {
    const n = x.count ? s[x.count] : 0;
    return html`<button class="${section === x.id ? "is-on" : ""}" data-action="section" data-value="${x.id}" aria-pressed="${section === x.id}">${x.label}${n ? html`<span class="badge">${n}</span>` : ""}</button>`;
  })}</div>`;
}

const sectionMarkup = () => ({ inbox: inboxMarkup, day: dayMarkup, objects: objectsMarkup, leads: leadsMarkup,
  locks: () => html`${queue ? html`<p class="caption">Незавершённых команд: ${queue.commands.length}${queue.commands.length === queue.limit ? "+" : ""}.
    Ожидают уведомления: ${queue.notifications.length}${queue.notifications.length === queue.limit ? "+" : ""}.
    ${heartbeatLabel()}.</p>` : ""}
    ${lockRows(items.locks, canOperate)}<button class="btn secondary mt-l" data-action="refreshLocks">Обновить</button>` }[section]());

function heartbeatLabel() {
  const value = queue?.heartbeats.find((h) => h.name === "worker")?.seen_at;
  if (!value || Date.now() - new Date(value).getTime() > 60000) return "Обработка очереди давно не подтверждалась";
  return "Очередь проверена: " + new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}

async function loadSummary() {
  try { summary = await api.admin.summary(); patch("#tabs", tabsMarkup()); } catch { /* не критично */ }
}

async function loadSection() {
  try {
    if (section === "inbox") items.inbox = (await api.admin.inbox()).bookings;
    else if (section === "day") items.day = await api.admin.day(dayOffset);
    else if (section === "objects") items.objects = (await api.admin.garages()).garages;
    else if (section === "leads") items.leads = (await api.admin.leads()).leads;
    else { const data = await api.admin.locks(); items.locks = data.locks; canOperate = data.can_operate; queue = data.queue; }
    patch("#section", sectionMarkup());
  } catch (e) {
    patch("#section", errorState(e.message));
  }
}

function showSection(next) {
  section = next;
  patch("#tabs", tabsMarkup());
  patch("#section", html`<div class="skeleton" style="height:120px"></div>`);
  loadSection();
}

/* ── Решения по заявке ──────────────────────────────────── */

function applyDecision(updated) {
  items.inbox = items.inbox.filter((b) => b.id !== updated.id);
  if (items.day) items.day.bookings = items.day.bookings.map((b) => (b.id === updated.id ? updated : b));
  const el = document.querySelector(`#section .req[data-id="${updated.id}"]`);
  if (section === "inbox" && el) {
    el.classList.add("is-gone");
    setTimeout(() => patch("#section", sectionMarkup()), 240);
  } else {
    patch("#section", sectionMarkup());
  }
  loadSummary();
}

async function confirm(el) {
  el.classList.add("is-loading");
  try {
    const { booking, delivered } = await api.admin.confirm(Number(el.dataset.id));
    haptic.success();
    toast(delivered ? "Подтверждено, клиент получил сообщение" : "Подтверждено, уведомление в очереди");
    applyDecision(booking);
  } catch (e) {
    el.classList.remove("is-loading");
    toast(e.message, "err");
    if (e.code === "decided") loadSection();
  }
}

function rejectSheet(id) {
  let reason = "";
  let sheet;
  const send = async (el) => {
    const text = reason.trim();
    if (text.length < 2) { haptic.error(); toast("Укажите причину отказа", "err"); return; }
    el.classList.add("is-loading");
    try {
      const { booking, delivered } = await api.admin.reject(id, text);
      sheet.close();
      toast(delivered ? "Отказ отправлен клиенту" : "Отклонено, уведомление в очереди");
      applyDecision(booking);
    } catch (e) {
      el.classList.remove("is-loading");
      toast(e.message, "err");
    }
  };
  sheet = openSheet(html`
    <h2 class="title-m">Причина отказа</h2>
    <p class="caption" style="margin:-4px 0 8px">Клиент увидит её в сообщении.</p>
    <div>${REASONS.map((r) => html`<button class="option" data-action="preset" data-reason="${r}"><span class="grow">${r}</span><span class="chev" style="width:20px;height:20px;color:var(--text-3)">${icon("right")}</span></button>`)}</div>
    <label class="field mt-l"><span>Своя причина</span><textarea class="input" data-input="reason" maxlength="300"></textarea></label>
    <div class="actions"><button class="btn danger l" data-action="send">Отказать</button></div>`, {
    actions: {
      preset(el) { reason = el.dataset.reason; send(el); },
      "input:reason": (el) => { reason = el.value; },
      send,
    },
  });
}

/* ── Экран ──────────────────────────────────────────────── */

async function enter(ctx) {
  canOperate = Boolean(state.me?.can_operate_locks);
  render(ctx, html`
    <h1 class="title-xl">Управление</h1>
    <div class="mt-l" id="tabs">${tabsMarkup()}</div>
    <div class="mt-m" id="section"><div class="skeleton" style="height:120px"></div></div>`, { animate: !ctx.refresh });
  loadSummary();
  await loadSection();
}

export default {
  enter,
  actions: {
    section(el) { haptic.select(); showSection(el.dataset.value); },
    day(el) {
      haptic.select();
      dayOffset = Number(el.dataset.offset);
      items.day = null;
      patch("#section", dayMarkup());
      loadSection();
    },
    confirm,
    reject: (el) => rejectSheet(Number(el.dataset.id)),
    call: (el) => callPhone(el.dataset.phone),
    tg: (el) => openTelegramLink(`https://t.me/${el.dataset.handle}`),
    object(el) { const g = items.objects.find((x) => x.id === Number(el.dataset.id)); if (g) objectSheet(g); },
    bot: () => openTelegramLink(`https://t.me/${BOT_USERNAME}`),
    async leadDone(el) {
      el.classList.add("is-loading");
      try {
        const id = Number(el.dataset.id);
        await api.admin.leadDone(id);
        items.leads = items.leads.filter((r) => r.id !== id);
        el.closest(".req").classList.add("is-gone");
        setTimeout(() => patch("#section", leadsMarkup()), 240);
        loadSummary();
      } catch (e) {
        el.classList.remove("is-loading");
        toast(e.message, "err");
      }
    },
    retry: () => loadSection(),
    refreshLocks: () => loadSection(),
    manualUnlock: (el) => manualUnlock(el.dataset.id, loadSection),
    resolveLock: (el) => resolveLock(el.dataset.id, loadSection),
  },
};
