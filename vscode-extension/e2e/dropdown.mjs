// The panel's own dropdown (webview/dropdown.tsx), driven the way a user does:
// its options only exist while it is open.

/** What the dropdown under `root` offers, as { value, label }. */
export async function listed(ui, root) {
  await ui.page.click(`${root} .dropdown-button`);
  const options = await ui.page.$$eval(`${root} .dropdown-opt`, (items) =>
    items.map((item) => ({ value: item.dataset.value, label: item.textContent.trim() }))
  );
  await ui.page.keyboard.press("Escape");
  return options;
}

/** The value it holds. */
export const held = (ui, root) => ui.page.getAttribute(root, "data-value");

/** Open it and pick `value` with the mouse. */
export async function choose(ui, root, value) {
  await ui.page.click(`${root} .dropdown-button`);
  await ui.page.click(`${root} .dropdown-opt[data-value="${value}"]`);
}
