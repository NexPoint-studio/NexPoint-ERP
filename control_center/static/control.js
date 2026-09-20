(() => {
  "use strict";
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  document.querySelectorAll("[data-menu-toggle]").forEach((button) => {
    button.addEventListener("click", () => document.body.classList.toggle("menu-open"));
  });
  document.querySelectorAll("[data-copy-value]").forEach((button) => {
    button.addEventListener("click", async () => {
      const value = button.dataset.copyValue || "";
      if (!value) return;
      try {
        await navigator.clipboard.writeText(value);
        button.textContent = "Copiado";
      } catch (_error) {
        button.textContent = "Selecione e copie o token";
      }
    });
  });

  const root = document.querySelector("[data-control-nexa]");
  if (!root) return;
  const form = root.querySelector("[data-control-nexa-form]");
  const messages = root.querySelector("[data-control-nexa-messages]");
  const status = root.querySelector("[data-control-nexa-status]");
  if (!form || !messages || !status) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = form.elements.message;
    const message = String(input.value || "").trim();
    const targetId = root.dataset.targetId || "";
    const targetType = root.dataset.targetType === "event" ? "event" : "ticket";
    if (!message || !targetId) return;
    const question = document.createElement("p");
    question.className = "nexa-question";
    question.textContent = message;
    messages.append(question);
    input.value = "";
    input.disabled = true;
    status.textContent = "Investigando…";
    try {
      const response = await fetch("/nexa/chat", {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": csrfToken},
        body: JSON.stringify({[`${targetType}_id`]: targetId, message}),
      });
      const payload = await response.json();
      const answer = document.createElement("p");
      answer.className = "nexa-answer";
      answer.textContent = response.ok ? payload.reply : (payload.error || "A Nexa está indisponível.");
      messages.append(answer);
    } catch (_error) {
      const answer = document.createElement("p");
      answer.className = "nexa-answer";
      answer.textContent = "A Nexa está temporariamente indisponível. O Control Center continua funcionando.";
      messages.append(answer);
    } finally {
      input.disabled = false;
      input.focus();
      status.textContent = "";
      messages.scrollTop = messages.scrollHeight;
    }
  });
})();
