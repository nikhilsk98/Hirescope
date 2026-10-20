from .aggregator import AggregatorAdapter
from .amazon import AmazonAdapter
from .ashby import AshbyAdapter
from .base import Adapter, HttpClient
from .eightfold import EightfoldAdapter
from .greenhouse import GreenhouseAdapter
from .hn_hiring import HNHiringAdapter
from .icims import IcimsAdapter
from .jibe import JibeAdapter
from .jobvite import JobviteAdapter
from .jsonld import CareersPageAdapter, NetflixAdapter, SitemapJsonLdAdapter
from .lever import LeverAdapter
from .oracle_cloud import OracleCloudAdapter
from .linkedin_alerts import LinkedInAlertsAdapter
from .rippling import RipplingAdapter
from .simplify_github import SimplifyAdapter
from .smallats import AvatureAdapter, BambooHRAdapter, JazzHRAdapter, PaylocityAdapter
from .smartrecruiters import SmartRecruitersAdapter
from .workable import WorkableAdapter
from .workday import WorkdayAdapter

ADAPTERS: dict[str, type[Adapter]] = {a.name: a for a in (
    GreenhouseAdapter, LeverAdapter, AshbyAdapter, WorkdayAdapter, SmartRecruitersAdapter, AmazonAdapter,
    RipplingAdapter, JobviteAdapter, EightfoldAdapter, WorkableAdapter, IcimsAdapter, JibeAdapter,
    OracleCloudAdapter, BambooHRAdapter, JazzHRAdapter, PaylocityAdapter, AvatureAdapter,
    NetflixAdapter, SitemapJsonLdAdapter, CareersPageAdapter, SimplifyAdapter, HNHiringAdapter, LinkedInAlertsAdapter,
    AggregatorAdapter,
)}


def get_adapter(name: str, client: HttpClient) -> Adapter:
    try:
        return ADAPTERS[name](client)
    except KeyError:
        raise KeyError(f"unknown source adapter '{name}' (known: {', '.join(ADAPTERS)})") from None
