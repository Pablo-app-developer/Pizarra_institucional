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

// Planilla de notas: navegar como en una hoja de cálculo, pegar desde Excel, entender "45" como 4,5,
// colorear por desempeño, recalcular la definitiva y guardar un borrador en el equipo.
const planilla = document.querySelector("[data-planilla]");
if (planilla) {
  const d = planilla.dataset;
  const min = parseFloat(d.min), max = parseFloat(d.max), dec = parseInt(d.decimales, 10) || 0;
  const cortes = { basico: parseFloat(d.basico), alto: parseFloat(d.alto), superior: parseFloat(d.superior) };
  const form = planilla.closest("form");
  const estado = document.querySelector("[data-estado-planilla]");
  const entradas = () => Array.from(planilla.querySelectorAll("input[type=text]:not([disabled]):not(#nueva-actividad)"));
  const matriz = () => Array.from(planilla.querySelectorAll("tbody tr, li")).map((f) => Array.from(f.querySelectorAll("input[type=text]:not([disabled])")))
    .filter((f) => f.length);
  let sucio = false;

  const leer = (texto) => {
    texto = String(texto).trim().replace(",", ".");
    if (texto === "") return { vacio: true };
    let n = Number(texto);
    if (Number.isNaN(n)) return { malo: true };
    // En escalas con decimal, "45" se entiende como 4,5 (y "100" en escala 1 a 10, como 10,0).
    if (dec > 0 && /^\d+$/.test(texto) && n > max && n / 10 >= min && n / 10 <= max) n = n / 10;
    if (n < min || n > max) return { malo: true };
    const factor = 10 ** dec;
    return { valor: Math.round(n * factor) / factor };
  };
  const mostrar = (n) => (dec ? n.toFixed(dec).replace(".", ",") : String(Math.round(n)));
  const nivel = (n) => (n >= cortes.superior ? "superior" : n >= cortes.alto ? "alto" : n >= cortes.basico ? "basico" : "bajo");
  const etiqueta = { superior: "Superior", alto: "Alto", basico: "Básico", bajo: "Bajo" };

  const pintar = (input, normalizar) => {
    const r = leer(input.value);
    input.classList.remove("invalida", "n-superior", "n-alto", "n-basico", "n-bajo");
    input.removeAttribute("aria-invalid");
    if (r.malo) {
      input.classList.add("invalida");
      input.setAttribute("aria-invalid", "true");
      input.title = `Escriba una nota entre ${mostrar(min)} y ${mostrar(max)}.`;
    } else if (!r.vacio) {
      input.classList.add(`n-${nivel(r.valor)}`);
      input.title = "";
      if (normalizar) input.value = mostrar(r.valor);
    }
    return r;
  };

  // Definitiva de cada fila: promedio ponderado por el porcentaje de cada actividad, como en el servidor.
  const pesos = Array.from(planilla.querySelectorAll("thead th[data-peso]")).map((th) => parseFloat(th.dataset.peso) || 0);
  const recalcular = (fila) => {
    const celda = fila.querySelector("[data-definitiva]");
    if (!celda) return;
    const pares = Array.from(fila.querySelectorAll("td.celda input")).map((inp, i) => [leer(inp.value), pesos[i] || 0])
      .filter(([r]) => !r.vacio && !r.malo).map(([r, p]) => [r.valor, p]);
    if (!pares.length) { celda.innerHTML = '<span class="secundario">—</span>'; return; }
    const total = pares.reduce((s, [, p]) => s + p, 0);
    const prom = total ? pares.reduce((s, [v, p]) => s + v * p, 0) / total : pares.reduce((s, [v]) => s + v, 0) / pares.length;
    const valor = Math.round(prom * 10 ** dec + 1e-9) / 10 ** dec;
    celda.innerHTML = `<span class="nota nota--${nivel(valor)}"><b>${mostrar(valor)}</b> ${etiqueta[nivel(valor)]}</span>`;
  };

  // Borrador local: si se cae el internet o se cierra la pestaña, lo escrito no se pierde.
  const claveBorrador = d.borradorClave ? `pizarra:${d.borradorClave}` : null;
  const guardarBorrador = () => {
    if (!claveBorrador) return;
    const datos = {};
    entradas().forEach((i) => { if (i.value !== i.defaultValue) datos[i.name] = i.value; });
    try {
      if (Object.keys(datos).length) localStorage.setItem(claveBorrador, JSON.stringify({ t: Date.now(), datos }));
      else localStorage.removeItem(claveBorrador);
    } catch (e) { /* sin almacenamiento local: no pasa nada */ }
  };
  const avisoBorrador = document.querySelector("[data-borrador]");
  if (claveBorrador && avisoBorrador) {
    let previo = null;
    try { previo = JSON.parse(localStorage.getItem(claveBorrador) || "null"); } catch (e) { previo = null; }
    const pendientes = previo && previo.datos && Object.entries(previo.datos).filter(([n, v]) => {
      const i = form.querySelector(`input[name="${n}"]`);
      return i && i.value !== v;
    });
    if (pendientes && pendientes.length && Date.now() - previo.t < 1000 * 60 * 60 * 24 * 7) {
      avisoBorrador.hidden = false;
      avisoBorrador.querySelector("[data-borrador-recuperar]").addEventListener("click", () => {
        pendientes.forEach(([n, v]) => { const i = form.querySelector(`input[name="${n}"]`); i.value = v; pintar(i); });
        planilla.querySelectorAll("tbody tr").forEach(recalcular);
        sucio = true;
        if (estado) estado.textContent = "Notas recuperadas. Revíselas y guarde.";
        avisoBorrador.hidden = true;
      });
      avisoBorrador.querySelector("[data-borrador-descartar]").addEventListener("click", () => {
        try { localStorage.removeItem(claveBorrador); } catch (e) { /* nada */ }
        avisoBorrador.hidden = true;
      });
    } else if (previo) {
      try { localStorage.removeItem(claveBorrador); } catch (e) { /* nada */ }
    }
  }

  entradas().forEach((i) => pintar(i));
  planilla.addEventListener("keydown", (e) => {
    if (e.target.tagName !== "INPUT" || e.target.id === "nueva-actividad") return;
    const m = matriz();
    const f = m.findIndex((fila) => fila.includes(e.target));
    if (f < 0) return;
    const c = m[f].indexOf(e.target);
    let destino = null;
    if (e.key === "ArrowDown" || e.key === "Enter") destino = m[f + 1]?.[c];
    else if (e.key === "ArrowUp") destino = m[f - 1]?.[c];
    else if (e.key === "ArrowRight" && e.target.selectionStart === e.target.value.length) destino = m[f][c + 1];
    else if (e.key === "ArrowLeft" && e.target.selectionEnd === 0) destino = m[f][c - 1];
    else return;
    if (e.key === "Enter") e.preventDefault();  // Enter baja; nunca envía el formulario por accidente
    if (destino) { e.preventDefault(); destino.focus(); destino.select(); }
  });
  planilla.addEventListener("input", (e) => {
    if (e.target.id === "nueva-actividad") return;
    sucio = true;
    if (estado) estado.textContent = "Hay cambios sin guardar.";
    const fila = e.target.closest("tr");
    if (fila) recalcular(fila);
    guardarBorrador();
  });
  planilla.addEventListener("focusout", (e) => {
    if (e.target.tagName !== "INPUT" || e.target.id === "nueva-actividad") return;
    pintar(e.target, true);
    const fila = e.target.closest("tr");
    if (fila) recalcular(fila);
    guardarBorrador();
  });
  // Pegar desde Excel: una columna, una fila o un bloque, a partir de la casilla elegida.
  planilla.addEventListener("paste", (e) => {
    if (e.target.tagName !== "INPUT" || e.target.id === "nueva-actividad") return;
    const texto = (e.clipboardData || window.clipboardData).getData("text");
    if (!/[\t\n]/.test(texto.trim())) return;
    e.preventDefault();
    const m = matriz();
    const f0 = m.findIndex((fila) => fila.includes(e.target));
    const c0 = m[f0].indexOf(e.target);
    const filas = texto.replace(/\r/g, "").replace(/\n$/, "").split("\n").map((l) => l.split("\t"));
    let puestas = 0, malas = 0;
    filas.forEach((valores, i) => valores.forEach((v, j) => {
      const input = m[f0 + i]?.[c0 + j];
      if (!input) return;
      input.value = v.trim();
      const r = pintar(input, true);
      if (r.malo) malas += 1; else puestas += 1;
    }));
    planilla.querySelectorAll("tbody tr").forEach(recalcular);
    sucio = true;
    guardarBorrador();
    if (estado) estado.textContent = `Se pegaron ${puestas} notas${malas ? `; ${malas} quedaron en rojo por estar fuera de la escala` : ""}. Revise y guarde.`;
  });
  form.addEventListener("submit", (e) => {
    const malas = entradas().filter((i) => pintar(i).malo);
    if (malas.length) {
      e.preventDefault();
      malas[0].focus();
      if (estado) estado.textContent = `Corrija ${malas.length === 1 ? "la nota marcada" : `las ${malas.length} notas marcadas`} en rojo antes de guardar.`;
      return;
    }
    sucio = false;
    try { if (claveBorrador) localStorage.removeItem(claveBorrador); } catch (err) { /* nada */ }
  });
  window.addEventListener("beforeunload", (e) => { if (sucio) { e.preventDefault(); e.returnValue = ""; } });

  // Después de agregar una columna, el cursor queda en su primera casilla.
  if (d.foco) {
    const th = planilla.querySelector(`th[data-actividad="${d.foco}"]`);
    if (th) {
      const col = Array.from(th.parentElement.children).indexOf(th);
      const primera = planilla.querySelector(`tbody tr td:nth-child(${col + 1}) input`);
      if (primera) { primera.focus(); th.classList.add("recien"); primera.scrollIntoView({ block: "nearest", inline: "center" }); }
    }
  }
}

// Calificar una actividad: poner la misma nota a quienes no tienen.
document.querySelectorAll("[data-llenar-vacias]").forEach((b) => b.addEventListener("click", () => {
  const valor = document.getElementById("llenar").value.trim();
  if (!valor) return;
  document.querySelectorAll(".calificar input[type=text]").forEach((i) => {
    if (!i.value.trim()) { i.value = valor; i.dispatchEvent(new Event("input", { bubbles: true })); i.dispatchEvent(new Event("focusout", { bubbles: true })); }
  });
}));

// Subir un Excel en cuanto se elige el archivo.
document.querySelectorAll("[data-subir-al-elegir]").forEach((i) => i.addEventListener("change", () => { if (i.files.length) i.form.submit(); }));

// Asistencia: marcar a todos de una vez
document.querySelectorAll("[data-marcar-todos]").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(`input[type=radio][value="${b.dataset.marcarTodos}"]`).forEach((r) => { r.checked = true; });
  });
});

// Directivos: al elegir el cargo se marcan sus permisos habituales (después se ajustan a mano).
const porCargo = document.getElementById("permisos-por-cargo");
const cargo = document.getElementById("id_cargo");
if (porCargo && cargo) {
  const mapa = JSON.parse(porCargo.textContent);
  cargo.addEventListener("change", () => {
    const marcados = mapa[cargo.value] || [];
    document.querySelectorAll('input[name="permisos"]').forEach((c) => { c.checked = marcados.includes(c.value); });
  });
}

// Sistema de evaluación: la escala elegida llena los valores y la tabla muestra la equivalencia.
const escalas = document.getElementById("escalas");
const selEscala = document.getElementById("id_escala");
if (escalas && selEscala) {
  const datos = JSON.parse(escalas.textContent);
  const ids = ["id_nota_minima", "id_desde_basico", "id_desde_alto", "id_desde_superior", "id_nota_maxima"];
  const campo = (id) => document.getElementById(id);
  const num = (id) => parseFloat(String(campo(id).value).replace(",", "."));
  const dec = () => parseInt(campo("id_decimales").value, 10) || 0;
  const fmt = (n) => n.toFixed(dec()).replace(".", ",");
  const paso = () => (dec() ? 0.1 : 1);
  const pintar = () => {
    const [min, bas, alt, sup, max] = ids.map(num);
    const texto = { bajo: [min, bas - paso()], basico: [bas, alt - paso()], alto: [alt, sup - paso()], superior: [sup, max] };
    Object.entries(texto).forEach(([k, [a, b]]) => {
      const celda = document.querySelector(`[data-rango="${k}"]`);
      if (celda) celda.textContent = [a, b].some(Number.isNaN) ? "" : `de ${fmt(a)} a ${fmt(b)}`;
    });
  };
  selEscala.addEventListener("change", () => {
    const e = datos[selEscala.value];
    if (!e) return;
    const anteriorBasico = num("id_desde_basico");
    ids.forEach((id, i) => { campo(id).value = e.valores[i]; });
    campo("id_decimales").value = e.decimales;
    const tope = campo("id_tope_recuperacion");
    if (tope && (tope.value === "" || parseFloat(tope.value.replace(",", ".")) === anteriorBasico)) tope.value = e.valores[1];
    pintar();
  });
  [...ids, "id_decimales"].forEach((id) => campo(id).addEventListener("input", () => { selEscala.value = ""; pintar(); }));
  pintar();
}
