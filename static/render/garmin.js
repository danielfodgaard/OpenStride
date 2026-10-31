// Garmin settings: what's connected, the login for sending, and Disconnect.
import {
  actions, api, breadcrumb, button, chip, el, input, labelled, main, notice, postJson, refresh, stale, statusLine, viewToken,
} from "./ui.js";

const UNOFFICIAL_WARNING = "Sending uses python-garminconnect, which Garmin does not sanction and which can break without notice. Your password is used once and never stored, but the saved token gives full access to your Garmin account, so keep MFA on.";

function syncSection(garmin) {
  const section = el("section");
  section.append(el("h2", "Syncing runs"));
  if (garmin.sync === "off") section.append(el("p", "Off. Set GARMIN_SYNC=personal_api and GARMIN_PERSONAL_API_KEY in .env, then restart."));
  else if (!garmin.connected) section.append(notice("GARMIN_PERSONAL_API_KEY is missing from .env.", "critical"));
  else section.append(el("p", "Connected through Garmin's official Personal API."));
  if (garmin.last_error) section.append(notice(garmin.last_error, "critical"));
  return section;
}

function loginForm() {
  const box = el("div");
  const status = statusLine();
  const email = input("email");
  email.autocomplete = "username";
  const password = input("password");
  password.autocomplete = "current-password";
  const code = input("text");
  code.autocomplete = "one-time-code";
  code.inputMode = "numeric";
  const codeField = labelled("Code from Garmin", code);
  codeField.hidden = true;

  const logIn = button("Log in to Garmin", status, async () => {
    if (codeField.hidden) {
      const result = await postJson("/api/garmin/login", { email: email.value, password: password.value });
      password.value = "";
      if (result.status === "connected") return refresh();
      codeField.hidden = false;
      logIn.textContent = "Send code";
      status.textContent = "";
      return undefined;
    }
    await postJson("/api/garmin/login/mfa", { code: code.value });
    return refresh();
  }, "Logging in…", "primary");
  box.append(labelled("Garmin email", email), labelled("Garmin password", password), codeField, actions(logIn), status);
  return box;
}

function sendSection(garmin) {
  const section = el("section");
  const status = statusLine();
  section.append(el("h2", "Sending workouts"));
  if (garmin.push === "off") {
    section.append(el("p", "Off. Set GARMIN_PUSH=unofficial in .env and restart to turn it on. Download workout .fit works without it."));
  } else {
    section.append(notice(UNOFFICIAL_WARNING, "warning"));
    if (garmin.push_blocked) section.append(notice(garmin.push_blocked, "critical"));
    else if (garmin.push_connected) section.append(actions(chip("Connected", "good")));
    else if (garmin.login_allowed) section.append(loginForm());
    else section.append(el("p", "Log in from this computer at 127.0.0.1, or over the Tailscale HTTPS address."));
  }
  const disconnect = button("Disconnect Garmin", status, async () => {
    if (!confirm("Delete the saved Garmin token and turn sending off?")) return;
    await postJson("/api/garmin/disconnect", {});
    refresh();
  }, "Disconnecting…", "danger");
  if (!garmin.push_connected) {
    delete disconnect.dataset.online;  // or coming back online would enable it
    disconnect.disabled = true;
  }
  section.append(actions(disconnect), status);
  return section;
}

export async function garminView() {
  const token = viewToken();
  const garmin = await api("/api/garmin/status");
  if (stale(token)) return;
  main.replaceChildren(
    breadcrumb(["Workouts", "#/workouts"], ["Garmin settings"]),
    el("h1", "Garmin settings"),
    syncSection(garmin),
    sendSection(garmin),
  );
}
