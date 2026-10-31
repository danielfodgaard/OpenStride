"""`python -m app.garmin.login`: connect once from a terminal. The password is never stored."""
from getpass import getpass

from app.config import settings
from app.garmin import provider

UNOFFICIAL_WARNING = (
    "Unofficial mode uses python-garminconnect, which logs in the way Garmin's mobile app does.\n"
    "Garmin does not sanction it, it can break without notice, and it sits in a grey area under\n"
    "Garmin's terms. Your password is used once for this login and never saved; only tokens are,\n"
    "and those can reach your account, so treat data/secrets/ like a password.\n"
)


def login_personal() -> None:
    print("Create a key in your Garmin Connect account settings. It is shown only once.")
    key = getpass("Garmin Personal API key: ").strip()
    if not key:
        raise SystemExit("No key entered.")
    provider.save_secret(provider.PERSONAL_KEY_FILE, key)
    print("Saved to data/secrets/ with mode 600.")


def login_unofficial() -> None:
    from garminconnect import Garmin

    print(UNOFFICIAL_WARNING)
    email = input("Garmin email: ").strip()
    password = getpass("Garmin password: ")
    client = Garmin(email, password, prompt_mfa=lambda: input("MFA code: ").strip())
    try:
        with provider.guarded():
            client.login(tokenstore=str(provider.secrets_dir()))
    except provider.GarminError as error:
        raise SystemExit(f"Login failed: {error}") from None
    print("Logged in. Tokens saved to data/secrets/ with mode 600.")


def main() -> None:
    if settings.garmin_mode == "personal_api":
        login_personal()
    elif settings.garmin_mode == "unofficial":
        login_unofficial()
    else:
        raise SystemExit("Set GARMIN_MODE to personal_api or unofficial in .env first.")


if __name__ == "__main__":
    main()
