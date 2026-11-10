"""Which provider holds the record. One is active; the rest of the app asks here and never names one."""
from app import config
from app.providers.base import Capabilities, Provider, ProviderError, Unsupported

__all__ = ["Capabilities", "Provider", "ProviderError", "Unsupported", "active", "names"]

_override: Provider | None = None


def names() -> tuple[str, ...]:
    return ("intervals_icu", "demo", "none")


def use(provider: Provider | None) -> None:
    """For tests: put a provider in place of the configured one."""
    global _override
    _override = provider


def active() -> Provider | None:
    """The configured provider, or None when the athlete hasn't connected one yet."""
    if _override is not None:
        return _override
    name = config.settings.provider
    if name == "intervals_icu":
        from app.providers.intervals_icu import IntervalsIcu

        return IntervalsIcu(config.settings.icu_athlete_id, config.settings.icu_api_key)
    if name == "demo":
        from app.providers.demo import Demo

        return Demo()
    return None


def require() -> Provider:
    provider = active()
    if provider is None:
        raise ProviderError(
            "No provider is connected. Set INTERVALS_ICU_API_KEY and INTERVALS_ICU_ATHLETE_ID in .env and restart "
            "(the key is under Settings, Developer Settings on intervals.icu)."
        )
    return provider
