// The update button's sign-in path. When the releases host wants a login the
// machine does not have, the button offers it; the host answers with the update
// the sign-in revealed, or clears the button when there is none.

const BUTTON = ".update-btn";
const HOST = "github.sec.samsung.net";

export const tests = {
  async "a login wall offers to sign in, and the click asks the host to"(ui, assert) {
    await ui.fromHost({ type: "updateLogin", host: HOST });
    const button = await ui.page.waitForSelector(BUTTON);
    const label = await button.innerText();
    assert(/Sign in to update|Entrar para atualizar/.test(label), `label was ${label}`);
    const title = await button.getAttribute("title");
    assert(title.includes(HOST), `title was ${title}`);
    await ui.shot("update-sign-in");

    await button.click();
    assert((await ui.toHost("signInForUpdates")).length === 1, "the click should ask to sign in");
    assert(!(await ui.toHost("installUpdate")).length, "signing in is not installing");

    // The panel opens on the history; a conversation shows the same button.
    await ui.page.click(".home-new");
    await ui.page.waitForSelector(`.statusbar ${BUTTON}`);
    await ui.shot("update-sign-in-chat");
  },

  async "while signing in the button waits, then becomes the update"(ui, assert) {
    await ui.fromHost({ type: "updateLogin", host: HOST });
    await ui.fromHost({ type: "updateProgress", state: "signingIn" });
    const busy = await ui.page.waitForSelector(`${BUTTON}:disabled`);
    assert(/Signing in|Entrando/.test(await busy.innerText()), "it should say it is signing in");
    await ui.shot("update-signing-in");

    // A background check landing mid sign-in must not put the invitation back.
    await ui.fromHost({ type: "updateLogin", host: HOST });
    assert(await ui.page.$(`${BUTTON}:disabled`), "a check mid sign-in reset the button");

    await ui.fromHost({
      type: "updateAvailable",
      version: "0.31.0",
      current: "0.30.1",
      notes: "",
      url: "",
    });
    const label = await ui.page.innerText(BUTTON);
    assert(label.includes("v0.31.0"), `label was ${label}`);
    await ui.shot("update-after-sign-in");
  },

  async "signed in with nothing newer, the button goes away"(ui, assert) {
    await ui.fromHost({ type: "updateLogin", host: HOST });
    await ui.fromHost({ type: "updateCleared" });
    assert(!(await ui.page.$(BUTTON)), "the button should be gone");
  },

  async "a failed sign-in goes back to the invitation, with the reason"(ui, assert) {
    await ui.fromHost({ type: "updateLogin", host: HOST });
    await ui.fromHost({ type: "updateProgress", state: "signingIn" });
    await ui.fromHost({ type: "updateProgress", state: "login", message: "The sign-in did not finish." });
    const button = await ui.page.waitForSelector(`${BUTTON}:not(:disabled)`);
    const title = await button.getAttribute("title");
    assert(title.startsWith("The sign-in did not finish."), `title was ${title}`);
  },
};
