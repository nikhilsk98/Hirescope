from .aggregator import AggregatorAdapter
from .amazon import AmazonAdapter
from .ashby import AshbyAdapter
from .base import Adapter, HttpClient
from .eightfold import EightfoldAdapter
from .greenhouse import GreenhouseAdapter
from .hn_hiring import HNHiringAdapter
from .jobvite import JobviteAdapter
from .jsonld import CareersPageAdapter, NetflixAdapter, SitemapJsonLdAdapter
from .lever import LeverAdapter
from .linkedin_alerts import LinkedInAlertsAdapter
from .rippling import RipplingAdapter
from .simplify_github import SimplifyAdapter
from .smartrecruiters import SmartRecruitersAdapter
from .workday import WorkdayAdapter

ADAPTERS: dict[str, type[Adapter]] = {a.name: a for a in (
    GreenhouseAdapter, LeverAdapter, AshbyAdapter, WorkdayAdapter, SmartRecruitersAdapter, AmazonAdapter,
    RipplingAdapter, JobviteAdapter, EightfoldAdapter,
    NetflixAdapter, SitemapJsonLdAdapter, CareersPageAdapter, SimplifyAdapter, HNHiringAdapter, LinkedInAlertsAdapter,
    AggregatorAdapter,
)}


def get_adapter(name: str, client: HttpClient) -> Adapter:
    try:
        return ADAPTERS[name](client)
    except KeyError:
        raise KeyError(f"unknown source adapter '{name}' (known: {', '.join(ADAPTERS)})") from None
