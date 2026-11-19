import { html, $, $$ } from "./dom.js";
import { dayNumber, hhmm, mapUrl, money, SIZES, TOOLS, TYPES, weekdayShort } from "./format.js";
import { icon } from "./icons.js";
import * as data from "./data.js";
import { bookingForm, wireBooking } from "./booking.js";
import { availabilityLine, boot, catalogUrl, errorState, photo, watchImages } from "./ui.js";

boot();

const id = new URLSearchParams(location.search).get("id") || (location.hash.match(/^#(\d+)$/) || [])[1];
const root = $("#garage");

function props(g) {
  const rows = [
    ["Тип", TYPES[g.type]],
    ["Район", g.district],
    ["Адрес", g.address && html`${g.address}<br><a class="link" href="${mapUrl(g)}" target="_blank" rel="noopener">Открыть на Яндекс Картах</a>`],
    ["Часы работы", g.working_hours],
    g.type === "garage" && ["Подъёмник", g.has_lift ? "Есть" : "Нет"],
    g.type === "garage" && ["Смотровая яма", g.has_pit ? "Есть" : "Нет"],
    ["Инструмент", TOOLS[g.tools]],
    ["Размер бокса", SIZES[g.size]],
  ].filter((r) => r && r[1]);
  return html`<dl class="props">${rows.map(([k, v]) => html`<div><dt>${k}</dt><dd>${v}</dd></div>`)}</dl>`;
}

function week(av) {
  if (!av || !av.days || !av.days.length) return "";
  return html`
    <div class="week" role="list" aria-label="Свободные часы на неделю">
      ${av.days.map((d) => html`
        <div class="day ${d.free > 0 ? "" : "full"}" role="listitem" aria-label="${weekdayShort(d.date)} ${dayNumber(d.date)}: ${d.free > 0 ? `свободно ${d.free} ч` : "мест нет"}">
          <span>${weekdayShort(d.date)}</span><b>${dayNumber(d.date)}</b><em>${d.free > 0 ? `${d.free} ч` : "нет"}</em>
        </div>`)}
    </div>
    <p class="week-caption">Свободные часы на ближайшую неделю, с ${hhmm(av.open_hour)} до ${hhmm(av.close_hour)}.</p>`;
}

function page(g, av) {
  const photos = g.photos.length ? g.photos : [null];
  return html`
    <nav class="crumbs" aria-label="Навигация">
      <a href="${catalogUrl()}">Каталог</a><span aria-hidden="true">/</span>
      ${g.district ? html`<a href="${catalogUrl({ district: g.district })}">${g.district}</a><span aria-hidden="true">/</span>` : ""}
      <span>${g.name}</span>
    </nav>
    <div class="detail">
      <div class="gallery">
        <button class="photo main" type="button" data-open="0" aria-label="Открыть фото крупно">${photo(photos[0], g.name, true)}</button>
        ${photos.length > 1 ? html`<div class="thumbs">${photos.map((src, i) => html`
          <button type="button" data-thumb="${i}" aria-label="Фото ${i + 1}" aria-current="${i === 0}"><img src="${src}" alt="" loading="lazy"></button>`)}</div>` : ""}
      </div>
      <div class="info">
        <section class="about">
          <h2 class="h-2">Что есть в боксе</h2>
          ${props(g)}
        </section>
        ${g.description ? html`<section class="about"><h2 class="h-2">Описание</h2><p class="desc">${g.description}</p></section>` : ""}
      </div>
      <aside class="panel" aria-label="Бронирование">
        <p class="type">${TYPES[g.type]}</p>
        <h1 class="h-1">${g.name}</h1>
        <p class="where">${[g.district, g.address].filter(Boolean).join(", ")}</p>
        <div class="price-big"><span class="price">${money(g.price)}</span><span class="unit">в час</span></div>
        ${availabilityLine(g)}
        ${week(av)}
        ${bookingForm(av)}
      </aside>
    </div>
    <div class="cta-bar" id="cta-bar">
      <div><span class="price">${money(g.price)}</span> <span class="unit">в час</span></div>
      <a class="btn primary" href="#booking">Выбрать время</a>
    </div>`;
}

/* Крупный просмотр фото: кнопки и стрелки на клавиатуре, Esc закрывает */
function lightbox(photos, start) {
  let i = start;
  const box = document.createElement("div");
  box.className = "lightbox";
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  box.setAttribute("aria-label", "Фото бокса");
  const draw = () => {
    box.innerHTML = String(html`
      <img src="${photos[i]}" alt="Фото ${i + 1}">
      <button class="close" type="button" data-lb="close" aria-label="Закрыть">${icon("x")}</button>
      ${photos.length > 1 ? html`
        <button class="prev" type="button" data-lb="prev" aria-label="Предыдущее фото">${icon("left")}</button>
        <button class="next" type="button" data-lb="next" aria-label="Следующее фото">${icon("right")}</button>
        <div class="counter">${i + 1} из ${photos.length}</div>` : ""}`);
  };
  const step = (d) => { i = (i + d + photos.length) % photos.length; draw(); };
  const close = () => { box.remove(); document.removeEventListener("keydown", onKey); opener.focus(); };
  const onKey = (e) => {
    if (e.key === "Escape") close();
    if (e.key === "ArrowRight") step(1);
    if (e.key === "ArrowLeft") step(-1);
  };
  const opener = document.activeElement;
  box.addEventListener("click", (e) => {
    const a = e.target.closest("[data-lb]");
    if (!a) { if (e.target === box) close(); return; }
    if (a.dataset.lb === "close") close();
    else step(a.dataset.lb === "next" ? 1 : -1);
  });
  document.addEventListener("keydown", onKey);
  draw();
  document.body.append(box);
  $(".close", box).focus();
}

function wire(g, av) {
  wireBooking(root, g, av);
  const photos = g.photos;
  let current = 0;
  const main = $(".photo.main");
  root.addEventListener("click", (e) => {
    const thumb = e.target.closest("[data-thumb]");
    if (thumb) {
      current = Number(thumb.dataset.thumb);
      const img = $("img", main);
      img.style.opacity = "0";
      setTimeout(() => { img.src = photos[current]; img.onload = () => { img.style.opacity = ""; }; }, 140);
      for (const t of $$("[data-thumb]")) t.setAttribute("aria-current", String(t === thumb));
      return;
    }
    if (e.target.closest(".photo.main") && photos.length) lightbox(photos, current);
  });

  // На телефоне цена и кнопка прилипают к низу, когда основная кнопка ушла с экрана
  const bar = $("#cta-bar");
  if ("IntersectionObserver" in window && $("#booking")) {
    new IntersectionObserver(([entry]) => bar.classList.toggle("is-on", !entry.isIntersecting && entry.boundingClientRect.top < 0))
      .observe($("#booking"));
  }
}

async function main() {
  if (!id) { root.innerHTML = String(errorState("not_found")); return; }
  let g, av;
  try {
    [g, av] = await Promise.all([data.garage(id), data.availability(id).catch(() => null)]);
  } catch (e) {
    root.innerHTML = String(errorState(e.message));
    return;
  }
  document.title = `${g.name}, ${g.district}: ${money(g.price)} в час | GarageHub`;
  const meta = $('meta[name="description"]');
  if (meta) meta.content = `${g.name} в районе ${g.district}. ${money(g.price)} в час. Бронь по часам, оплата на месте.`;
  root.innerHTML = String(page(g, av));
  watchImages(root);
  wire(g, av);
}

main();
