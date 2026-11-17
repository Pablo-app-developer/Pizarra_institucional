// Menú móvil
const botonMenu = document.querySelector("[data-menu]");
if (botonMenu) {
  botonMenu.addEventListener("click", () => {
    const abierto = document.body.classList.toggle("menu-abierto");
    botonMenu.setAttribute("aria-expanded", abierto);
  });
  document.addEventListener("click", (e) => {
    if (document.body.classList.contains("menu-abierto") && !e.target.closest(".lateral") && !e.target.closest("[data-menu]")) {
      document.body.classList.remove("menu-abierto");
      botonMenu.setAttribute("aria-expanded", "false");
    }
  });
}

// Confirmación antes de eliminar
document.querySelectorAll("form[data-confirmar]").forEach((f) => {
  f.addEventListener("submit", (e) => {
    if (!window.confirm(f.dataset.confirmar)) e.preventDefault();
  });
});

// Filtros que se aplican al cambiar
document.querySelectorAll("[data-autoenviar]").forEach((el) => {
  el.addEventListener("change", () => el.form.submit());
});

// Planilla: moverse con flechas y Enter como en una hoja de cálculo,
// validar la escala al salir de la celda y avisar si hay cambios sin guardar.
const planilla = document.querySelector("[data-planilla]");
if (planilla) {
  const min = parseFloat(planilla.dataset.min), max = parseFloat(planilla.dataset.max);
  const estado = document.querySelector("[data-estado-planilla]");
  let sucio = false;
  const celdas = () => Array.from(planilla.querySelectorAll("tbody tr")).map((tr) => Array.from(tr.querySelectorAll("input")));
  planilla.addEventListener("keydown", (e) => {
    if (e.target.tagName !== "INPUT") return;
    const m = celdas();
    const f = m.findIndex((fila) => fila.includes(e.target));
    const c = m[f].indexOf(e.target);
    let destino = null;
    if (e.key === "ArrowDown" || e.key === "Enter") destino = m[f + 1]?.[c];
    else if (e.key === "ArrowUp") destino = m[f - 1]?.[c];
    else if (e.key === "ArrowRight" && e.target.selectionStart === e.target.value.length) destino = m[f][c + 1];
    else if (e.key === "ArrowLeft" && e.target.selectionEnd === 0) destino = m[f][c - 1];
    else return;
    if (e.key === "Enter") e.preventDefault();
    if (destino) { e.preventDefault(); destino.focus(); destino.select(); }
  });
  planilla.addEventListener("input", () => {
    sucio = true;
    if (estado) estado.textContent = "Hay cambios sin guardar.";
  });
  planilla.addEventListener("focusout", (e) => {
    if (e.target.tagName !== "INPUT") return;
    const texto = e.target.value.trim().replace(",", ".");
    const n = Number(texto);
    const mala = texto !== "" && (Number.isNaN(n) || n < min || n > max);
    e.target.classList.toggle("invalida", mala);
    e.target.setAttribute("aria-invalid", mala);
  });
  planilla.closest("form").addEventListener("submit", () => { sucio = false; });
  window.addEventListener("beforeunload", (e) => { if (sucio) { e.preventDefault(); e.returnValue = ""; } });
}

// Asistencia: marcar a todos de una vez
document.querySelectorAll("[data-marcar-todos]").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(`input[type=radio][value="${b.dataset.marcarTodos}"]`).forEach((r) => { r.checked = true; });
  });
});
