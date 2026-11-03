import { initData, inTelegram } from "./tg.js";
import { safeStorage } from "./dom.js";

export class ApiError extends Error {
  constructor(message, code, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

/* Вход без Telegram — только для разработки: ?dev=client или ?dev=admin (работает, только если сервер запущен в dev-режиме) */
const session = safeStorage("session");
const devParam = new URLSearchParams(location.search).get("dev");
if (devParam) session.setItem("gh_dev", devParam);
const devAlias = session.getItem("gh_dev");

export const canAuthenticate = () => inTelegram || Boolean(devAlias);

function authHeader() {
  if (inTelegram) return `tma ${initData()}`;
  if (devAlias) return `dev ${devAlias}`;
  return "";
}

async function request(method, path, body, headers = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: { Authorization: authHeader(), ...(body ? { "Content-Type": "application/json" } : {}), ...headers },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Нет соединения. Проверьте интернет и попробуйте ещё раз.", "network", 0);
  }
  let data = null;
  try { data = await response.json(); } catch { /* пустой ответ */ }
  if (!response.ok) {
    const e = (data && data.error) || {};
    throw new ApiError(e.message || "Что-то пошло не так. Попробуйте ещё раз.", e.code || "error", response.status);
  }
  return data;
}

const get = (path) => request("GET", path);
const post = (path, body) => request("POST", path, body || {});

export const api = {
  me: () => get("/api/me"),
  garages: () => get("/api/garages"),
  garage: (id) => get(`/api/garages/${id}`),
  availability: (id) => get(`/api/garages/${id}/availability`),
  createBooking: (payload) => post("/api/bookings", payload),
  myBookings: () => get("/api/bookings"),
  cancelBooking: (id) => post(`/api/bookings/${id}/cancel`),
  unlock: (id, key) => request("POST", `/api/bookings/${id}/unlock`, {}, { "Idempotency-Key": key }),
  lockCommand: (id) => get(`/api/lock-commands/${id}`),
  ownerRequest: (payload) => post("/api/owner-requests", payload),
  admin: {
    summary: () => get("/api/admin/summary"),
    inbox: () => get("/api/admin/inbox"),
    day: (offset) => get(`/api/admin/day?offset=${offset}`),
    confirm: (id) => post(`/api/admin/bookings/${id}/confirm`),
    reject: (id, reason) => post(`/api/admin/bookings/${id}/reject`, { reason }),
    garages: () => get("/api/admin/garages"),
    archive: (id) => post(`/api/admin/garages/${id}/archive`),
    restore: (id) => post(`/api/admin/garages/${id}/restore`),
    leads: () => get("/api/admin/leads"),
    leadDone: (id) => post(`/api/admin/leads/${id}/done`),
    locks: () => get("/api/admin/locks"),
    setLock: (payload) => post("/api/admin/locks", payload),
    unlock: (id, key, reason) => request("POST", `/api/admin/locks/${id}/unlock`, { reason }, { "Idempotency-Key": key }),
    resolve: (id, state, reason) => post(`/api/admin/lock-commands/${id}/resolve`, { state, reason }),
  },
};
