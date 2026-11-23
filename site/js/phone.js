// Russian phone input: the country prefix is fixed, the field contains 10 national digits.
export function formatPhone(digits) {
  return [digits.slice(0, 3), digits.slice(3, 6), digits.slice(6, 8), digits.slice(8, 10)].filter(Boolean).join("-");
}

export function parsePhone(value) {
  const raw = String(value).trim();
  if (!/^\+?[0-9\s()-]*$/.test(raw)) throw new Error("В номере телефона допустимы только цифры.");
  let digits = raw.replace(/\D/g, "");
  if (raw.startsWith("+") && !(digits.length === 11 && digits.startsWith("7"))) {
    throw new Error("Введите российский номер: +7 и 10 цифр.");
  }
  if (digits.length === 11 && /^[78]/.test(digits)) digits = digits.slice(1);
  if (digits.length > 10) throw new Error("После +7 должно быть 10 цифр. Лишние цифры не добавлены.");
  return digits;
}

export function wirePhone(input, error) {
  let previous = "";
  const digits = () => input.value.replace(/\D/g, "");
  function message(text = "") {
    input.setCustomValidity(text);
    input.setAttribute("aria-invalid", String(Boolean(text)));
    error.textContent = text;
    error.hidden = !text;
    const hint = input.form.querySelector("#phone-hint");
    if (hint) hint.style.visibility = text ? "hidden" : "";
  }
  function caret(count) {
    if (!count) return 0;
    let found = 0;
    for (let i = 0; i < input.value.length; i++) {
      if (/\d/.test(input.value[i]) && ++found === count) return i + 1;
    }
    return input.value.length;
  }
  function set(value, count = value.length) {
    input.value = formatPhone(value);
    previous = input.value;
    const at = caret(count);
    input.setSelectionRange(at, at);
    message();
  }
  input.addEventListener("beforeinput", e => {
    if (e.inputType === "insertText" && /^\d$/.test(e.data || "")) {
      const selected = input.value.slice(input.selectionStart, input.selectionEnd).replace(/\D/g, "").length;
      if (digits().length - selected >= 10) {
        e.preventDefault(); // The field is full; ignoring a key does not invalidate the existing number.
      }
    }
  });
  input.addEventListener("input", e => {
    const raw = input.value;
    // Some mobile keyboards deliver input without a cancellable beforeinput.
    if (/^[0-9]$/.test(e.data || "") && previous.replace(/\D/g, "").length === 10 && raw.replace(/\D/g, "").length > 10) {
      input.value = previous;
      return;
    }
    const before = raw.slice(0, input.selectionStart).replace(/\D/g, "").length;
    try {
      const value = parsePhone(raw);
      set(value, before - Math.max(0, raw.replace(/\D/g, "").length - value.length));
    } catch (e) {
      input.value = previous;
      message(e.message);
    }
  });
  input.addEventListener("paste", e => {
    e.preventDefault();
    try {
      const raw = e.clipboardData.getData("text");
      const value = parsePhone(raw);
      if (raw.trim().startsWith("+") || raw.replace(/\D/g, "").length >= 10) set(value);
      else {
        const left = input.value.slice(0, input.selectionStart).replace(/\D/g, "");
        const right = input.value.slice(input.selectionEnd).replace(/\D/g, "");
        if ((left + value + right).length > 10) throw new Error("После +7 должно быть 10 цифр. Лишние цифры не добавлены.");
        const combined = left + value + right;
        set(combined, left.length + value.length);
      }
      // Notify the form that its payload changed (including its idempotency key).
      input.dispatchEvent(new Event("change", { bubbles: true }));
    } catch (e) { message(e.message); }
  });
  input.addEventListener("keydown", e => {
    if (e.key !== "Backspace" && e.key !== "Delete") return;
    if (input.selectionStart !== input.selectionEnd) return;
    const count = input.value.slice(0, input.selectionStart).replace(/\D/g, "").length;
    const at = e.key === "Backspace" ? count - 1 : count;
    if (at < 0 || at >= digits().length) { message(); return; }
    e.preventDefault();
    const value = digits();
    set(value.slice(0, at) + value.slice(at + 1), at);
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  function validate() {
    // Keep a rejected paste error until the user edits the number.
    if (input.validationMessage && input.getAttribute("aria-invalid") === "true") return false;
    const valid = digits().length === 10;
    message(valid ? "" : "Введите номер полностью: 10 цифр после +7.");
    return valid;
  }
  input.addEventListener("blur", () => { if (input.value) validate(); });
  input.addEventListener("invalid", e => { e.preventDefault(); validate(); input.focus(); });
  return validate;
}
