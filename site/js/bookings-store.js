// The browser stores only random booking capabilities, not names or phone numbers.
const STORAGE_KEY = "gh_website_bookings_v1";
export const validToken = token => typeof token === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(token);

export function savedBookings() {
  try {
    const rows = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(rows) ? rows.filter(x => x && validToken(x.token) && Number.isSafeInteger(x.id)) : [];
  } catch { return []; }
}

export function rememberBooking(b) {
  if (!validToken(b.access_token)) return false;
  try {
    const existing = savedBookings();
    if (existing.some(x => x.token === b.access_token && x.id === b.id)) return true;
    const rows = existing.filter(x => x.token !== b.access_token);
    rows.unshift({id: b.id, token: b.access_token});
    localStorage.setItem(STORAGE_KEY, JSON.stringify(rows));
    return true;
  } catch { return false; }
}

export function forgetBooking(token) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(savedBookings().filter(b => b.token !== token))); return true; }
  catch { return false; }
}

export const privateBookingUrl = token => new URL(`bookings.html#booking=${encodeURIComponent(token)}`, location.href).href;

export async function loadBookings(tokens) {
  const bookings = [];
  for (let start = 0; start < tokens.length; start += 100) {
    const response = await fetch("/api/public/my-bookings", {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({tokens: tokens.slice(start, start + 100)}),
      signal: AbortSignal.timeout(15000)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.message || "Не удалось загрузить бронирования.");
    bookings.push(...result.bookings);
  }
  return bookings;
}
