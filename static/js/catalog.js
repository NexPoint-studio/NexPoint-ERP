"use strict";
document.querySelectorAll("[data-category-selector]").forEach((selector) => {
  const rows = [...selector.querySelectorAll("[data-category-choice]")];
  const search = selector.querySelector("[data-category-search]");
  const normalize = (value) => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-BR");
  const refresh = () => {
    const term = normalize(search.value.trim());
    let selected = 0;
    let visible = 0;
    rows.forEach((row) => {
      const checkbox = row.querySelector('input[type="checkbox"]');
      selected += Number(checkbox.checked);
      row.querySelector("[data-category-price]").disabled = !checkbox.checked;
      row.classList.toggle("is-selected", checkbox.checked);
      row.hidden = !checkbox.checked && !normalize(row.dataset.categoryName).includes(term);
      visible += Number(!row.hidden);
    });
    selector.querySelector("[data-category-count]").textContent = selected === 1 ? "1 categoria selecionada" : `${selected} categorias selecionadas`;
    selector.querySelector("[data-category-empty]").hidden = visible > 0 || rows.length === 0;
  };
  selector.addEventListener("change", refresh);
  search.addEventListener("input", refresh);
  refresh();
});
