/* Общее для всех страниц: шапка, карточка бокса, плавная смена содержимого, ссылки на бронь. */
import { html, $, $$ } from "./dom.js";
import { hhmm, money, plural, TYPES_SHORT } from "./format.js";
import { placeholderArt } from "./icons.js";
import { DEMO } from "./data.js";

export const BOT = "garazh_rent_bot";
/* Заявка оформляется прямо на сайте, Telegram не требуется. */
export const appLink = `https://t.me/${BOT}`;
/* В режиме примеров номер дублируется в #, чтобы превью открывалось и там, где адрес страницы обрезают */
export const garageUrl = (id) => `garage.html?id=${encodeURIComponent(id)}${DEMO ? `&demo#${encodeURIComponent(id)}` : ""}`;
export const catalogUrl = (params = {}) => {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v));
  if (DEMO) q.set("demo", "");
  const s = q.toString().replace(/=(&|$)/g, "$1");
  return `catalog.html${s ? `?${s}` : ""}`;
};

export const countLine = (n) => `${n} ${plural(n, "бокс", "бокса", "боксов")}`;

export function photo(src, alt = "", eager = false) {
  return src
    ? html`<img class="fade-img" src="${src}" alt="${alt}" ${eager ? "" : html`loading="lazy"`} decoding="async">`
    : placeholderArt;
}

export function availabilityLine(g) {
  if (g.free_today > 0) return html`<span class="avail on">Сегодня свободно с ${hhmm(g.next_free)}</span>`;
  if (g.free_today === 0) return html`<span class="avail">Сегодня мест нет</span>`;
  return "";
}

/* Объявление в каталоге, как в Mini App: фото, цена, название, район, свободное время */
export function listing(g) {
  return html`
    <a class="listing" href="${garageUrl(g.id)}">
      <div class="photo">${photo(g.photos[0], g.name)}<span class="tag">${TYPES_SHORT[g.type] || "Бокс"}</span></div>
      <div class="price-row"><span class="price">${money(g.price)}</span><span class="unit">в час</span></div>
      <h3 class="name">${g.name}</h3>
      <div class="where">${g.district}</div>
      ${availabilityLine(g)}
    </a>`;
}

export function skeletonGrid(n = 4) {
  return html`${Array.from({ length: n }, () => html`
    <div aria-hidden="true">
      <div class="skeleton" style="aspect-ratio:4/3;border-radius:12px"></div>
      <div class="skeleton" style="height:20px;width:45%;margin-top:12px"></div>
      <div class="skeleton" style="height:14px;width:85%;margin-top:8px"></div>
      <div class="skeleton" style="height:12px;width:50%;margin-top:8px"></div>
    </div>`)}`;
}

export function errorState(kind) {
  const text = kind === "not_found"
    ? html`<p class="h-3">Такого бокса нет в каталоге</p><p>Возможно, его сняли с размещения. Посмотрите другие боксы.</p><a class="btn secondary" href="${catalogUrl()}">Открыть каталог</a>`
    : html`<p class="h-3">Каталог сейчас не загрузился</p><p>Проверьте интернет и обновите страницу. Если не поможет, напишите нам: ${raw_phone}.</p><button class="btn secondary" type="button" onclick="location.reload()">Обновить страницу</button>`;
  return html`<div class="empty">${text}</div>`;
}
const raw_phone = "+7 (999) 462-47-30";

/* Плавная замена содержимого: браузеры с View Transitions сами смешают старое и новое */
export function swap(el, markup) {
  const apply = () => { el.innerHTML = String(markup); watchImages(el); };
  if (document.startViewTransition && !matchMedia("(prefers-reduced-motion: reduce)").matches && el.isConnected && el.childElementCount) {
    document.startViewTransition(apply);
  } else {
    apply();
  }
}

/* Фото проявляются, когда загрузились, а не появляются кусками */
export function watchImages(root = document) {
  for (const img of $$("img.fade-img:not(.is-loaded)", root)) {
    if (img.complete && img.naturalWidth) img.classList.add("is-loaded");
    else {
      img.addEventListener("load", () => img.classList.add("is-loaded"), { once: true });
      img.addEventListener("error", () => img.replaceWith(document.createRange().createContextualFragment(String(placeholderArt))), { once: true });
    }
  }
}

function initHeader() {
  const header = $(".header");
  if (!header) return;
  const onScroll = () => header.classList.toggle("is-stuck", window.scrollY > 8);
  onScroll();
  window.addEventListener("scroll", onScroll, { passive: true });
}

function initDemoNote() {
  if (!DEMO) return;
  // В режиме примеров ссылки внутри сайта сохраняют ?demo
  for (const a of $$("a[href]")) {
    const href = a.getAttribute("href");
    if (!/^[\w-]+\.html(\?|#|$)/.test(href) || /[?&]demo\b/.test(href)) continue;
    const [path, hash = ""] = href.split("#");
    a.setAttribute("href", `${path}${path.includes("?") ? "&" : "?"}demo${hash ? `#${hash}` : ""}`);
  }
  const note = document.createElement("div");
  note.className = "demo-note";
  note.textContent = "Примеры боксов для показа сайта. Настоящий каталог подставится сам, когда сайт заработает на сервере.";
  document.querySelector(".page").prepend(note);
}

export function boot() {
  initHeader();
  initDemoNote();
  watchImages();
  const year = $("[data-year]");
  if (year) year.textContent = String(new Date().getFullYear());
}
