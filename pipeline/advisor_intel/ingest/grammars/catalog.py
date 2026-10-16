"""Product and provider names for the ``product`` grammar (§5.3, §5.4), versioned with ``GRAMMAR_VERSION``.

Product types use the FnaDocument's own unions where one exists (``RetirementFundType``, ``AssetType``,
``PolicyType``, ``LiabilityType``), so a reading's value is the type the extraction target expects;
``FNA_LIST`` says which FnaDocument list each type belongs to. Surfaces are in normalised token form
(lowercase, hyphens as spaces). A surface naming several products ("preservation fund", "annuity")
lists each with a cost (−ln p).
"""

from __future__ import annotations

from dataclasses import dataclass

from .tokens import normalize_text


@dataclass(frozen=True)
class Product:
    key: str  # lexicon key
    product_type: str
    surfaces: tuple[str, ...]
    cost: float = 0.0


@dataclass(frozen=True)
class Provider:
    name: str  # canonical name
    surfaces: tuple[str, ...]


@dataclass(frozen=True)
class ProductName:
    """A client- or practice-specific name (a CRM product, a policy in the prior FNA) added per call."""

    surface: str
    product_type: str | None = None
    provider: str | None = None


PRODUCTS: tuple[Product, ...] = (
    Product("ra", "retirement-annuity", ("ra", "ras", "r a", "retirement annuity", "retirement annuities", "retirement annuity fund",
                                          "uittree annuïteit", "uittreeannuïteit", "uittree annuiteit", "uittreeannuiteit")),
    Product("pension_fund", "pension", ("pension fund", "pension", "pensioen", "pensioenfonds", "pensioen fonds")),
    Product("provident_fund", "provident", ("provident fund", "provident", "voorsorgfonds", "voorsorg fonds")),
    Product("pension_preservation", "preservation-pension", ("pension preservation fund", "pension preservation", "preservation pension fund")),
    Product("provident_preservation", "preservation-provident", ("provident preservation fund", "provident preservation", "preservation provident fund")),
    Product("preservation_fund", "preservation-pension", ("preservation fund", "preservation", "preserver fund", "bewaringsfonds"), cost=0.69),
    Product("preservation_fund", "preservation-provident", ("preservation fund", "preservation", "preserver fund", "bewaringsfonds"), cost=0.69),
    Product("living_annuity", "living-annuity", ("living annuity", "living annuities", "lewende annuïteit", "lewende annuiteit")),
    Product("life_annuity", "life-annuity", ("life annuity", "guaranteed annuity", "guaranteed life annuity", "lewenslange annuïteit")),
    Product("annuity", "retirement-annuity", ("annuity", "annuïteit", "annuiteit"), cost=0.5),
    Product("annuity", "living-annuity", ("annuity", "annuïteit", "annuiteit"), cost=1.0),
    Product("annuity", "life-annuity", ("annuity", "annuïteit", "annuiteit"), cost=1.6),
    Product("two_pot", "two-pot", ("two pot", "two pot system", "two pot retirement system", "twee pot", "tweepot")),
    Product("savings_pot", "savings-pot", ("savings pot", "savings component", "savings portion", "spaarpot", "spaar pot")),
    Product("retirement_pot", "retirement-pot", ("retirement pot", "retirement component", "uittree pot")),
    Product("vested_pot", "vested-pot", ("vested pot", "vested component", "vested portion")),
    Product("tfsa", "tfsa", ("tfsa", "tfsas", "t f s a", "tax free savings", "tax free savings account", "tax free account", "tax free investment",
                             "tax free", "belastingvrye spaarrekening", "belastingvrye belegging", "belastingvry")),
    Product("endowment", "endowment", ("endowment", "endowments", "endowment policy", "sinking fund", "uitkeerpolis")),
    Product("unit_trust", "unit-trust", ("unit trust", "unit trusts", "unit trust fund", "collective investment", "collective investment scheme",
                                         "eenheidstrust", "eenheidstrusts")),
    Product("etf", "etf", ("etf", "etfs", "e t f", "exchange traded fund", "exchange traded funds", "index fund", "index tracker", "tracker fund")),
    Product("shares", "shares", ("shares", "share portfolio", "stocks", "equities", "aandele")),
    Product("money_market", "money-market", ("money market", "money market account", "money market fund", "geldmark", "geldmarkrekening")),
    Product("offshore", "offshore", ("offshore", "offshore investment", "offshore portfolio", "offshore account")),
    Product("offshore_allowance", "offshore-allowance", ("offshore allowance", "foreign investment allowance", "discretionary allowance",
                                                         "single discretionary allowance")),
    Product("discretionary", "discretionary-investment", ("discretionary investment", "discretionary portfolio", "voluntary investment")),
    Product("regulation_28", "regulation-28", ("regulation 28", "regulation twenty eight", "reg 28", "reg twenty eight", "regulasie 28")),
    Product("cash", "cash", ("savings account", "fixed deposit", "notice deposit", "call account", "spaarrekening")),
    Product("crypto", "crypto", ("crypto", "bitcoin", "cryptocurrency", "crypto currency")),
    Product("life_cover", "life", ("life cover", "life policy", "life insurance", "life assurance", "death cover", "lewensdekking", "lewenspolis",
                                   "lewensversekering")),
    Product("disability_cover", "disability-lump", ("disability cover", "disability benefit", "lump sum disability", "disability policy",
                                                    "ongeskiktheidsdekking")),
    Product("income_protection", "income-protection", ("income protection", "income protector", "income disability", "income protection benefit",
                                                       "inkomstebeskerming")),
    Product("severe_illness", "severe-illness", ("dread disease", "severe illness", "critical illness", "dread disease cover", "severe illness cover",
                                                 "critical illness cover", "ernstige siekte")),
    Product("funeral", "funeral", ("funeral cover", "funeral policy", "funeral plan", "begrafnispolis", "begrafnisdekking")),
    Product("family_income", "family-income", ("family income benefit", "family income")),
    Product("medical_aid", "medical-aid", ("medical aid", "medical scheme", "mediese fonds")),
    Product("gap_cover", "gap-cover", ("gap cover",)),
    Product("home_loan", "home-loan", ("bond", "home loan", "mortgage", "mortgage bond", "verband", "huislening")),
    Product("vehicle_finance", "vehicle-finance", ("vehicle finance", "car finance", "car loan", "vehicle loan", "motorfinansiering")),
    Product("personal_loan", "personal-loan", ("personal loan", "persoonlike lening")),
    Product("credit_card", "credit-card", ("credit card", "kredietkaart")),
    Product("store_card", "store-card", ("store card", "store account", "winkelrekening")),
    Product("overdraft", "overdraft", ("overdraft", "oortrekking")),
    Product("student_loan", "student-loan", ("student loan", "study loan", "studielening")),
)

PROVIDERS: tuple[Provider, ...] = (
    Provider("Allan Gray", ("allan gray", "alan gray", "allen gray", "allan grey", "alan grey")),
    Provider("Coronation", ("coronation",)),
    Provider("Ninety One", ("ninety one", "investec asset management")),
    Provider("Investec", ("investec",)),
    Provider("Old Mutual", ("old mutual", "old mutual wealth")),
    Provider("Sanlam", ("sanlam",)),
    Provider("Glacier", ("glacier", "glacier by sanlam")),
    Provider("Discovery", ("discovery", "discovery life", "discovery invest", "discovery health", "discovery bank")),
    Provider("Liberty", ("liberty", "liberty life")),
    Provider("Momentum", ("momentum", "mmi", "momentum wealth", "momentum life")),
    Provider("Metropolitan", ("metropolitan", "metropolitan life")),
    Provider("PSG", ("psg", "p s g", "psg wealth")),
    Provider("Stanlib", ("stanlib", "stan lib")),
    Provider("10X", ("10x", "ten x")),
    Provider("Sygnia", ("sygnia", "signia")),
    Provider("Satrix", ("satrix",)),
    Provider("Nedgroup Investments", ("nedgroup", "nedgroup investments")),
    Provider("M&G", ("m&g", "m and g", "prudential", "m&g investments")),
    Provider("Foord", ("foord",)),
    Provider("Truffle", ("truffle",)),
    Provider("Fairtree", ("fairtree", "fair tree")),
    Provider("Camissa", ("camissa",)),
    Provider("Marriott", ("marriott",)),
    Provider("PPS", ("pps", "p p s")),
    Provider("Hollard", ("hollard",)),
    Provider("BrightRock", ("brightrock", "bright rock")),
    Provider("Assupol", ("assupol",)),
    Provider("1Life", ("1life",)),  # "one life" is left out: it would swallow the count in "one life policy"
    Provider("Clientèle", ("clientele", "clientèle")),
    Provider("OUTsurance", ("outsurance", "out surance")),
    Provider("Absa", ("absa",)),
    Provider("FNB", ("fnb", "f n b", "first national bank")),
    Provider("Standard Bank", ("standard bank",)),
    Provider("Nedbank", ("nedbank", "ned bank")),
    Provider("Capitec", ("capitec",)),
    Provider("EasyEquities", ("easyequities", "easy equities")),
    Provider("Alexander Forbes", ("alexander forbes", "alex forbes")),
    Provider("Bonitas", ("bonitas",)),
    Provider("GEPF", ("gepf", "government employees pension fund")),
    Provider("SARS", ("sars",)),
    Provider("JSE", ("jse", "j s e", "johannesburg stock exchange")),
)

CONNECTORS = ("with", "at", "from", "through", "via", "by", "of", "van", "met", "by die", "with the", "at the")

FNA_LIST: dict[str, str] = {
    **{t: "retirementFunds" for t in ("pension", "provident", "retirement-annuity", "preservation-pension", "preservation-provident",
                                      "living-annuity", "life-annuity", "savings-pot", "retirement-pot", "vested-pot", "two-pot")},
    **{t: "assets" for t in ("tfsa", "endowment", "unit-trust", "shares", "money-market", "offshore", "cash", "crypto", "etf",
                             "discretionary-investment")},
    **{t: "policies" for t in ("life", "disability-lump", "income-protection", "severe-illness", "funeral", "family-income")},
    **{t: "liabilities" for t in ("home-loan", "vehicle-finance", "personal-loan", "credit-card", "store-card", "overdraft", "student-loan")},
}


def provider_for(surface: str) -> str | None:
    """The canonical provider for a surface form, if the catalogue knows it."""
    norm = normalize_text(surface)
    for p in PROVIDERS:
        if norm in p.surfaces:
            return p.name
    return None


def all_surfaces(extra: tuple[ProductName, ...] = ()) -> list[str]:
    """Every product, provider and client-specific surface in normalised form."""
    out = [s for p in PRODUCTS for s in p.surfaces] + [s for pv in PROVIDERS for s in pv.surfaces]
    out += [normalize_text(n.surface) for n in extra]
    return [s for s in out if s]


def product_vocabulary(extra: tuple[ProductName, ...] = ()) -> frozenset[str]:
    """Single tokens of every product and provider surface, plus the connectors (for the span finder)."""
    words: set[str] = set()
    for s in all_surfaces(extra):
        words.update(s.split())
    for c in CONNECTORS:
        words.update(c.split())
    return frozenset(words)


def edge_tokens(extra: tuple[ProductName, ...] = ()) -> tuple[frozenset[str], frozenset[str]]:
    """Tokens a product span can start with and end with (first and last tokens of the surfaces)."""
    surfaces = all_surfaces(extra)
    return frozenset(s.split()[0] for s in surfaces), frozenset(s.split()[-1] for s in surfaces)
