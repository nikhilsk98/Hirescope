from .base import Adapter, HttpClient
from .greenhouse import GreenhouseAdapter

ADAPTERS: dict[str, type[Adapter]] = {
    GreenhouseAdapter.name: GreenhouseAdapter,
}


def get_adapter(name: str, client: HttpClient) -> Adapter:
    try:
        return ADAPTERS[name](client)
    except KeyError:
        raise KeyError(f"unknown source adapter '{name}' (known: {', '.join(ADAPTERS)})") from None
