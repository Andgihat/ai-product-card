const $ = (selector) => document.querySelector(selector);

const form = $("#product-form");
const specsBox = $("#specs");
const resultBox = $("#result");
const resultSub = $("#result-sub");
const footer = $("#result-footer");
const tabs = $("#tabs");
const submitBtn = $("#submit");
const formNote = $("#form-note");
const toast = $("#toast");
const submitDefaultHtml = submitBtn.innerHTML;

const state = { result: null, tab: "card" };

/* ---------- характеристики ---------- */

function addSpecRow(name = "", value = "") {
  const row = $("#spec-row").content.firstElementChild.cloneNode(true);
  row.querySelector(".spec-name").value = name;
  row.querySelector(".spec-value").value = value;
  row.querySelector(".spec-remove").addEventListener("click", () => row.remove());
  specsBox.appendChild(row);
}

function readSpecs() {
  const specs = {};
  specsBox.querySelectorAll(".spec-row").forEach((row) => {
    const name = row.querySelector(".spec-name").value.trim();
    const value = row.querySelector(".spec-value").value.trim();
    if (name && value) specs[name] = value;
  });
  return specs;
}

function setSpecs(specs) {
  specsBox.innerHTML = "";
  const entries = Object.entries(specs || {});
  if (entries.length === 0) {
    addSpecRow();
    return;
  }
  entries.forEach(([name, value]) => addSpecRow(name, value));
}

/* ---------- форма ---------- */

function collectProduct() {
  const name = $("#name").value.trim();
  if (!name) throw new Error("Укажите название товара — без него карточку не собрать.");
  return {
    name,
    manufacturer: $("#manufacturer").value.trim() || null,
    category: $("#category").value.trim() || null,
    specs: readSpecs(),
    description: $("#description").value.trim() || null,
  };
}

function fillForm(product) {
  $("#name").value = product.name || "";
  $("#manufacturer").value = product.manufacturer || "";
  $("#category").value = product.category || "";
  $("#description").value = product.description || "";
  setSpecs(product.specs || {});
  formNote.textContent = "";
}

const emptyState = resultBox.innerHTML;

$("#add-spec").addEventListener("click", () => addSpecRow());

$("#reset").addEventListener("click", () => {
  form.reset();
  setSpecs({});
  state.result = null;
  tabs.hidden = true;
  footer.hidden = true;
  resultSub.textContent = "Карточка появится здесь";
  resultBox.innerHTML = emptyState;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formNote.textContent = "";

  let product;
  try {
    product = collectProduct();
  } catch (error) {
    formNote.textContent = error.message;
    return;
  }

  setLoading(true, product.name);
  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(product),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const details = data.details ? ` (${data.details.join("; ")})` : "";
      throw new Error((data.detail || data.error || `Ошибка HTTP ${response.status}`) + details);
    }
    state.result = data;
    state.tab = "card";
    tabs.hidden = false;
    footer.hidden = false;
    resultSub.textContent = `${data.product.name} · ${Math.round(data.elapsed_ms / 1000)} с`;
    render();
    syncTabs();
  } catch (error) {
    showError(error.message);
  } finally {
    setLoading(false);
  }
});

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    form.requestSubmit();
  }
});

function showError(message) {
  resultBox.innerHTML = "";
  const alert = document.createElement("div");
  alert.className = "alert alert--err";
  alert.textContent = message;
  resultBox.appendChild(alert);
  tabs.hidden = true;
  footer.hidden = true;
  resultSub.textContent = "Не удалось сгенерировать";
}

function setLoading(isLoading, productName = "") {
  submitBtn.disabled = isLoading;
  submitBtn.innerHTML = isLoading
    ? '<span class="spinner"></span><span class="btn__label">Генерирую…</span>'
    : submitDefaultHtml;

  form.querySelectorAll("input, textarea, button").forEach((element) => {
    if (element !== submitBtn) element.disabled = isLoading;
  });

  if (isLoading) {
    tabs.hidden = true;
    footer.hidden = true;
    resultSub.textContent = "Генерирую карточку…";
    resultBox.innerHTML =
      '<div class="skeleton">' +
      '<div class="skeleton__line skeleton__line--sm"></div>' +
      '<div class="skeleton__line skeleton__line--lg"></div>' +
      '<div class="skeleton__line"></div>' +
      '<div class="skeleton__line"></div>' +
      '<div class="skeleton__line skeleton__line--sm"></div>' +
      "</div>";
    if (productName) resultSub.textContent = `${productName} · генерирую…`;
  }
}

/* ---------- примеры ---------- */

async function loadExample(id) {
  try {
    const response = await fetch(`/api/examples/${id}`);
    if (!response.ok) throw new Error("Пример не загрузился");
    fillForm(await response.json());
  } catch (error) {
    formNote.textContent = error.message;
  }
}

$("#load-example-1").addEventListener("click", () => loadExample("pd100"));
$("#load-example-2").addEventListener("click", () => loadExample("rd2r"));

/* ---------- рендер результата ---------- */

tabs.addEventListener("click", (event) => {
  const tab = event.target.closest(".tab");
  if (!tab) return;
  state.tab = tab.dataset.tab;
  syncTabs();
  render();
});

function syncTabs() {
  tabs.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.tab === state.tab);
  });
}

function render() {
  if (!state.result) return;
  resultBox.innerHTML = "";
  if (state.tab === "json") {
    resultBox.appendChild(codeBlock(JSON.stringify(state.result.card, null, 2)));
  } else if (state.tab === "markdown") {
    resultBox.appendChild(codeBlock(state.result.markdown));
  } else {
    resultBox.appendChild(renderCardView(state.result));
  }
}

function codeBlock(text) {
  const pre = document.createElement("pre");
  pre.className = "code";
  pre.textContent = text;
  return pre;
}

function renderCardView({ card, validation, product }) {
  const wrap = document.createElement("div");
  wrap.className = "card-view";

  const checks = document.createElement("div");
  checks.className = "checks";
  checks.appendChild(
    chip(
      `Заголовок: ${validation.title_length} симв.`,
      validation.title_ok ? "chip--ok" : "chip--warn"
    )
  );
  checks.appendChild(
    chip(
      `Описание: ${validation.description_length} симв.`,
      validation.description_ok ? "chip--ok" : "chip--warn"
    )
  );
  checks.appendChild(
    chip(
      `Характеристики: ${validation.specs_verified}/${validation.specs_total}`,
      validation.specs_verified === validation.specs_total ? "chip--ok" : "chip--warn"
    )
  );
  if (validation.repaired) checks.appendChild(chip("формат исправлен"));
  if (validation.verifier_used) checks.appendChild(chip("фактчек выполнен", "chip--ok"));
  wrap.appendChild(checks);

  if (validation.warnings.length || validation.dropped_specs.length) {
    const alert = document.createElement("div");
    alert.className = "alert alert--warn";
    validation.warnings.forEach((text) => {
      const line = document.createElement("div");
      line.textContent = text;
      alert.appendChild(line);
    });
    if (validation.dropped_specs.length) {
      const list = document.createElement("ul");
      list.className = "dropped-list";
      validation.dropped_specs.forEach((item) => {
        const entry = document.createElement("li");
        entry.textContent = `${item.name}: ${item.value}`;
        list.appendChild(entry);
      });
      alert.appendChild(list);
    }
    wrap.appendChild(alert);
  }

  if (validation.unsupported_claims.length) {
    const claims = document.createElement("div");
    claims.className = "alert alert--err";
    const header = document.createElement("div");
    header.textContent = "Второй проход не нашёл подтверждения:";
    claims.appendChild(header);
    const list = document.createElement("ul");
    list.className = "claim-list";
    validation.unsupported_claims.forEach((claim) => {
      const item = document.createElement("li");
      item.textContent = `«${claim.quote}» — ${claim.reason}`;
      list.appendChild(item);
    });
    claims.appendChild(list);
    wrap.appendChild(claims);
  }

  const title = document.createElement("h3");
  title.className = "card-view__title";
  title.textContent = card.seo_title;
  wrap.appendChild(title);

  const meta = document.createElement("div");
  meta.className = "card-view__meta";
  meta.textContent = [product.manufacturer, product.category].filter(Boolean).join(" · ");
  if (meta.textContent) wrap.appendChild(meta);

  wrap.appendChild(
    block(
      "Ключевые характеристики",
      specTable(card.key_specs)
    )
  );
  wrap.appendChild(block("Описание", paragraph(card.seo_description)));
  wrap.appendChild(block("Поисковые запросы", chipsList(card.search_queries)));
  wrap.appendChild(block("Коммерческий блок", paragraph(card.commercial_block)));

  if (card.missing_data.length) {
    wrap.appendChild(block("Что уточнить", missingList(card.missing_data)));
  }
  return wrap;
}

function block(title, content) {
  const section = document.createElement("section");
  section.className = "block";
  const heading = document.createElement("h4");
  heading.className = "block__title";
  heading.textContent = title;
  section.append(heading, content);
  return section;
}

function specTable(items) {
  const table = document.createElement("table");
  table.className = "spec-table";
  items.forEach((item) => {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = item.name;
    const value = document.createElement("td");
    value.textContent = item.value;
    row.append(name, value);
    table.appendChild(row);
  });
  return table;
}

function paragraph(text) {
  const p = document.createElement("p");
  p.className = "text-block";
  p.textContent = text;
  return p;
}

function chipsList(items) {
  const list = document.createElement("ul");
  list.className = "chips";
  items.forEach((text) => {
    const item = document.createElement("li");
    item.className = "chip";
    item.textContent = text;
    list.appendChild(item);
  });
  return list;
}

function missingList(items) {
  const list = document.createElement("ul");
  list.className = "missing-list";
  items.forEach((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    list.appendChild(item);
  });
  return list;
}

function chip(text, className = "") {
  const span = document.createElement("span");
  span.className = `chip ${className}`.trim();
  span.textContent = text;
  return span;
}

/* ---------- копирование и скачивание ---------- */

function currentText() {
  if (!state.result) return "";
  return state.tab === "json"
    ? JSON.stringify(state.result.card, null, 2)
    : state.result.markdown;
}

$("#copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(currentText());
    showToast("Скопировано в буфер обмена");
  } catch {
    showToast("Браузер запретил доступ к буферу — выделите текст вручную");
  }
});

$("#download").addEventListener("click", () => {
  if (!state.result) return;
  const blob = new Blob([currentText()], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  const name = state.result.product.name.replace(/[\\/:*?"<>|]+/g, "-").trim() || "card";
  link.href = URL.createObjectURL(blob);
  link.download = state.tab === "json" ? `${name}.json` : `${name}.md`;
  link.click();
  URL.revokeObjectURL(link.href);
  showToast("Файл сохранён");
});

let toastTimer = null;
function showToast(message) {
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.hidden = true), 2600);
}

/* ---------- статус модели ---------- */

async function checkHealth() {
  const status = $("#status");
  const text = status.querySelector(".status__text");
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    if (data.llm_available) {
      status.classList.add("is-ok");
      text.textContent = `модель на связи · ${data.llm_model}`;
      status.title = data.llm_base_url;
    } else {
      status.classList.add("is-bad");
      text.textContent = "модель недоступна";
      status.title = `${data.llm_base_url}: ${data.detail || "нет ответа"}`;
    }
  } catch {
    status.classList.add("is-bad");
    text.textContent = "сервис недоступен";
  }
}

setSpecs({});
checkHealth();
