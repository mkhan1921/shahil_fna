"""The Monte Carlo cash-flow engine (spec §12.3), vectorised across paths: pure numpy, no LLM calls.

Each path steps through the household's years (to age 100 for the primary client, or the latest planning
age if later). Household money is held in today's rand; each person's retirement capital is held in rand of
the day and run month by month inside each year, with the FNA's own conventions (``computeRetirement``):
monthly compounding at the year's return, contributions at the end of each month escalating on each
anniversary, retirement after round(years × 12) months, income drawn monthly in advance and escalating with
CPI on each retirement anniversary. With zero volatility and every fact at its median this reproduces the
FNA's projected capital and depletion age (§12.8).

One year, for every path at once:

1. Inflation: the path's CPI for the year; amounts in today's money are inflated to the year's rand where
   the retirement funds need them, and foreign amounts move with the path's real USDZAR.
2. Income: employment (salary, bonus, other income) for the months each person still works, growing at the
   real salary escalation (or a self-employed person's sampled business growth), with expected income changes
   from their sampled year; income from assets.
3. Tax: the ``TaxModel`` (the ported FNA tax module) on each person's year: salary, bonus, retirement income
   and asset income, the s11F deduction for contributions, rebates, s6A credits and UIF.
4. Contributions: retirement (payroll, retirement annuity, employer, retirement-earmarked) until retirement,
   escalated by the salary escalation; household investments, education and goal savings.
5. Outflows: living expenses (a working person's share) or the retirement target (a retired person's,
   after tax), debt repayments until each loan is paid off, school and tertiary costs while active (tertiary
   from education savings first), family support, risk premiums, event costs in their sampled years.
6. Surplus / deficit: a surplus goes to discretionary investments; household contributions are paid from
   income only; a deficit draws cash, then discretionary (and offshore), then TFSA, then (post-retirement)
   extra living annuity income up to the 17.5% band. A deficit beyond liquid assets while anyone still works
   is a liquidity breach. What remains first cuts discretionary spending and voluntary retirement
   contributions; anything left is essential spend not met.
7. Retirement income: at retirement the fund balances form a living annuity. On each anniversary the
   drawdown is the needed amount clamped to 2.5–17.5% of the balance; need above the band is a gap the
   household must fund from liquid assets.
8. Returns: each bucket earns its allocation's return from the path's market draw, around the document's
   means (pre/post-retirement return for retirement capital, risk capital return for goal and education
   savings, the pre-retirement return shifted by the asset mix for household investments).
9. Record: net worth (real), retirement income, essential shortfall, liquidity breach, goal funding.

Success (§12.1): essential spend met in every year until the latest planning age. The funded ratio
(assets plus PV of future income over PV of essential spending, discounted at the path's own returns) is the
continuous output the variance attribution uses (§12.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import CMA, DrawdownBand, ForecastConfig, allocation_vector, glide_weights
from .mapper import ForecastInput, PersonIn, _js_round
from .returns import MarketPaths
from .tax import TaxModel, marginal_rate

_TINY = 1e-12


@dataclass
class EngineResult:
    """Per-path outcomes of one simulation."""

    success: np.ndarray  # (P,) essential spend met until the planning horizon
    funded_ratio: np.ndarray  # (P,) assets + PV(income) over PV(essential spending)
    first_shortfall_age: np.ndarray  # (P,) primary client's age in the first year essential spend is not met (nan: never)
    total_shortfall_real: np.ndarray  # (P,) unmet essential spend, today's money
    liquidity_breach: np.ndarray  # (P,) a deficit beyond liquid assets while someone still works
    net_worth_real: np.ndarray  # (P, T+1) at the start of each year and the end of the last, today's money
    retirement_income_real: np.ndarray  # (P, T) living annuity income plus other retirement income
    goal_funded: dict[str, np.ndarray]  # goal key → (P,) paid in full in its target year
    capital_at_retirement: dict[str, np.ndarray]  # person → (P,) rand of the day (FNA projectedCapital)
    capital_at_retirement_real: dict[str, np.ndarray]
    depletion_age: dict[str, np.ndarray]  # person → (P,) FNA definition (nan when the capital lasts to the planning age)
    ages: list[int] = field(default_factory=list)  # primary client's age at each recorded point

    @property
    def n_paths(self) -> int:
        return int(self.success.shape[0])

    @property
    def pos(self) -> float:
        return float(np.mean(self.success))


def _annuity_factor(r: np.ndarray, months: np.ndarray | int, g_n: np.ndarray | None = None) -> np.ndarray:
    """Sum of (1 + r)^k for k = 0..months-1, safe at r = 0."""
    g_n = (1 + r) ** months if g_n is None else g_n
    small = np.abs(r) < 1e-12
    return np.where(small, months, (g_n - 1) / np.where(small, 1.0, r))


def _accumulate(balance: np.ndarray, contribution: np.ndarray, r: np.ndarray, months: np.ndarray) -> np.ndarray:
    """``months`` of monthly compounding with a contribution at the end of each month (FNA computeRetirement)."""
    g_n = (1 + r) ** months
    return balance * g_n + contribution * _annuity_factor(r, months, g_n)


def _drawdown(balance: np.ndarray, withdrawal: np.ndarray, r: np.ndarray, months: np.ndarray) -> np.ndarray:
    """``months`` of withdrawals at the start of each month: B ← (B − W)(1 + r), in closed form."""
    g = 1 + r
    g_n = g**months
    return balance * g_n - withdrawal * g * _annuity_factor(r, months, g_n)


def _deplete(balance: np.ndarray, withdrawal: np.ndarray, r: np.ndarray, months: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """FNA's drawdown without a band: the balance after ``months`` and the month it first reaches zero (0: not).

    The month is the smallest k <= months with B_k <= 0, found from the closed form and checked either side,
    so it equals the month the FNA's month-by-month loop reports.
    """
    end = _drawdown(balance, withdrawal, r, months)
    hit = (months > 0) & (withdrawal > 0) & (end <= 0)
    k = np.zeros(balance.shape, dtype=np.int64)
    if hit.any():
        b, w, rr, n = balance[hit], withdrawal[hit], r[hit], months[hit]
        small = np.abs(rr) < 1e-12
        with np.errstate(divide="ignore", invalid="ignore"):
            a = w * (1 + rr) / np.where(small, 1.0, rr)
            guess = np.where(small, np.ceil(b / w), np.ceil(np.log(a / (a - b)) / np.log1p(np.where(small, 1.0, rr)) - 1e-9))
        guess = np.clip(np.nan_to_num(guess, nan=1.0, posinf=1.0, neginf=1.0), 1, n).astype(np.int64)
        guess = np.where(_drawdown(b, w, rr, guess) > 0, np.minimum(guess + 1, n), guess)
        prev = np.maximum(guess - 1, 0)
        guess = np.where((guess > 1) & (_drawdown(b, w, rr, prev) <= 0), prev, guess)
        k[hit] = guess
    return np.where(hit, 0.0, end), k


def _glide(points, age0: np.ndarray, n_years: int) -> np.ndarray:
    """Glide-path weights (5, P, T), or (5, 1, T) when every path has the same age (the common case)."""
    t = np.arange(n_years)
    ages = age0[:1, None] + t[None, :] if np.ptp(age0) == 0 else age0[:, None] + t[None, :]
    return np.moveaxis(glide_weights(points, ages), -1, 0)


class _Portfolios:
    """Real gross returns (P, T) of every bucket from one market draw (§12.3 step 8, §12.8)."""

    def __init__(self, inp: ForecastInput, market: MarketPaths, cma: CMA) -> None:
        a = inp.assumptions
        T = inp.n_years
        self.r_pre = a.real(a.pre_retirement_return)
        self.r_post = a.real(a.post_retirement_return)
        self.r_cap = a.real(a.risk_capital_return)
        pol = cma.policies
        ref = allocation_vector(pol.reference)
        self.reference = market.portfolio_gross(ref, self.r_pre)
        self.pre: list[np.ndarray] = []
        self.post: list[np.ndarray] = []
        fixed_mix = allocation_vector(pol.risk_profiles[inp.accumulation_mix]) if inp.accumulation_mix else None
        for p in inp.persons:
            w_pre = fixed_mix if fixed_mix is not None else _glide(pol.glide_path, p.age0, T)
            self.pre.append(market.portfolio_gross(w_pre, self.r_pre))
            self.post.append(market.portfolio_gross(_glide(pol.living_annuity, p.age0, T), self.r_post))
        if fixed_mix is not None:
            w_disc = fixed_mix
        elif isinstance(pol.discretionary, str):
            w_disc = _glide(pol.glide_path, inp.persons[0].age0, T)
        else:
            w_disc = allocation_vector(pol.discretionary)
        self.discretionary = market.portfolio_gross(w_disc, market.relative_target(w_disc, ref, self.r_pre))
        for name in ("cash", "tfsa", "offshore"):
            w = allocation_vector(getattr(pol, name))
            setattr(self, name, market.portfolio_gross(w, market.relative_target(w, ref, self.r_pre)))
        self.capital = market.portfolio_gross(allocation_vector(pol.capital), self.r_cap)


@dataclass
class _Loans:
    payments: np.ndarray  # (P, T) nominal repayments in each year
    balance_end: np.ndarray  # (P, T) nominal balance at the end of each year


def _amortise(balance: np.ndarray, rate: np.ndarray, repayment: np.ndarray, n_years: int) -> _Loans:
    """A loan repaid monthly until paid off (FNA monthsToRepay; nominal rate compounded monthly)."""
    if np.ptp(balance) == 0 and np.ptp(rate) == 0 and np.ptp(repayment) == 0 and balance.shape[0] > 1:
        one = _amortise(balance[:1], rate[:1], repayment[:1], n_years)  # the same loan on every path
        shape = (balance.shape[0], n_years)
        return _Loans(np.broadcast_to(one.payments, shape), np.broadcast_to(one.balance_end, shape))
    r = rate / 12
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = balance * r / np.where(repayment > 0, repayment, 1.0)
        months = np.where(
            balance <= 0,
            0.0,
            np.where(
                repayment <= 0,
                np.inf,
                np.where(np.abs(r) < 1e-12, np.ceil(balance / np.where(repayment > 0, repayment, 1.0)),
                         np.where(ratio >= 1, np.inf, np.ceil(-np.log1p(-np.minimum(ratio, 1 - 1e-15)) / np.log1p(r) - 1e-9))),
            ),
        )
    k_end = 12.0 * np.arange(1, n_years + 1)[None, :]
    k_start = k_end - 12

    def bal(k: np.ndarray) -> np.ndarray:
        g = (1 + r)[:, None] ** k
        with np.errstate(divide="ignore", invalid="ignore"):
            annuity = np.where(np.abs(r)[:, None] < 1e-12, k, (g - 1) / np.where(np.abs(r) < 1e-12, 1.0, r)[:, None])
        return balance[:, None] * g - repayment[:, None] * annuity

    n = months[:, None]
    full = np.clip(np.minimum(k_end, n - 1) - k_start, 0, 12)
    final_in = (n > k_start) & (n <= k_end) & np.isfinite(n)
    final = np.where(final_in, np.maximum(bal(np.where(final_in, n - 1, 0)), 0.0) * (1 + r)[:, None], 0.0)
    payments = repayment[:, None] * full + final
    balance_end = np.where(n <= k_end, 0.0, np.maximum(bal(np.minimum(k_end, np.where(np.isfinite(n), n, k_end))), 0.0))
    return _Loans(payments, balance_end)


@dataclass
class _Person:
    """One person's retirement state, in rand of the day."""

    src: PersonIn
    K: np.ndarray  # months of accumulation (FNA monthsToRet), 0 when retired
    tau: np.ndarray  # exact years to retirement (FNA yearsToRetirement)
    ret_start: np.ndarray  # FNA retStartAge
    window: np.ndarray  # months the target is funded (FNA monthsInRet, to the planning age)
    horizon: np.ndarray  # years from today to the planning age (FNA planningAge, at least a year past retirement)
    need_today: np.ndarray  # FNA netTargetToday (monthly)
    has_target: np.ndarray  # a target replaces this person's share of living expenses once retired
    balance: np.ndarray
    shadow: np.ndarray  # FNA's projection: full target drawn, no band, to find the depletion age
    w_need: np.ndarray
    w_eff: np.ndarray
    ann_balance: np.ndarray
    depleted_month: np.ndarray
    cap_ret: np.ndarray
    cap_ret_real: np.ndarray
    contrib0: np.ndarray  # monthly contributions today (FNA monthlyContributions)
    k_mod: np.ndarray  # K mod 12: the month of each year on which the annuity anniversary falls


def simulate(inp: ForecastInput, market: MarketPaths, *, config: ForecastConfig, cma: CMA, tax_model: TaxModel) -> EngineResult:
    """Run every path of one forecast (§12.3)."""
    return _Simulation(inp, market, config, cma, tax_model).run()


class _Simulation:
    def __init__(self, inp: ForecastInput, market: MarketPaths, config: ForecastConfig, cma: CMA, tax_model: TaxModel) -> None:
        if market.n_years < inp.n_years or market.n_paths != inp.n:
            raise ValueError(f"market has {market.n_paths} paths x {market.n_years} years; the input needs {inp.n} x {inp.n_years}")
        market = market.head(inp.n_years)  # a batch of sensitivity variants shares a market sized for its longest horizon
        self.inp = inp
        self.T = inp.n_years
        self.P = inp.n
        self.config = config
        self.cma = cma
        self.tax = tax_model
        T = self.T
        self.price = market.price[:, : T + 1]
        self.infl = market.infl[:, :T]
        self.fx = market.fx_index[:, : T + 1]
        self.fx_mid = self.fx[:, :T] * np.exp(0.5 * market.fx_log[:, :T])
        self.p_mid = self.price[:, :T] * np.sqrt(1 + self.infl)
        self.port = _Portfolios(inp, market, cma)
        a = inp.assumptions
        self.g_salary = a.real(a.salary_escalation)
        self.g_edu = a.real(a.education_inflation)
        # The living annuity band is a SARS value: the shared table file (through the FNA engine's tax model) is its
        # source; the config's band only stands in for tax models that carry no table (§12.7).
        table_band = getattr(tax_model, "living_annuity_band", None)
        self.band = DrawdownBand(min=float(table_band[0]), max=float(table_band[1])) if table_band else config.drawdown_band
        self.people = [self._person(p) for p in inp.persons]
        self.loans = [_amortise(l.balance, l.rate, l.repayment, T) for l in inp.liabilities]
        nominal = 1 + self.infl
        self.r_pre_m = [(g * nominal) ** (1 / 12) - 1 for g in self.port.pre]
        self.r_post_m = [(g * nominal) ** (1 / 12) - 1 for g in self.port.post]
        self.half = {k: np.sqrt(getattr(self.port, k)) for k in ("cash", "discretionary", "offshore", "tfsa", "capital")}

    def _person(self, p: PersonIn) -> _Person:
        retired = p.retired_now
        ret_start = np.where(retired, p.age0, p.retirement_age)
        tau = np.where(retired, 0.0, np.maximum(0.0, p.retirement_age - p.age0))
        K = np.where(retired, 0, _js_round(tau * 12)).astype(np.int64)
        plan_eff = np.maximum(p.planning_age, np.ceil(ret_start) + 1)
        years_in_ret = np.maximum(1.0, plan_eff - ret_start)
        window = _js_round(years_in_ret * 12).astype(np.int64)
        contrib = p.contrib_payroll_monthly + p.contrib_own_monthly + p.contrib_employer_monthly + p.contrib_earmarked_monthly
        n = self.P
        return _Person(
            src=p, K=K, tau=tau, ret_start=ret_start, window=window, horizon=plan_eff - p.age0,
            need_today=p.net_target_today, has_target=p.target_monthly_today > 0,
            balance=p.capital0.astype(float).copy(), shadow=np.zeros(n), w_need=np.zeros(n), w_eff=np.zeros(n),
            ann_balance=np.zeros(n), depleted_month=np.full(n, -1, dtype=np.int64), cap_ret=np.full(n, np.nan),
            cap_ret_real=np.full(n, np.nan), contrib0=contrib, k_mod=K % 12,
        )

    # ------------------------------------------------------------------ retirement capital, month by month

    def _retirement_year(self, pp: _Person, t: int, j_person: int) -> dict[str, np.ndarray]:
        """Advance one person's retirement capital through year t; returns the year's flows in rand of the day.

        The year splits into at most three runs of months: accumulation until retirement, drawdown until the next
        annuity anniversary, drawdown after it. Each run is closed form, so the result equals the FNA's
        month-by-month loop.
        """
        P = self.P
        price_t = self.price[:, t]
        infl_t = self.infl[:, t]
        r_pre = self.r_pre_m[j_person][:, t]
        r_post = self.r_post_m[j_person][:, t]
        c_nom = pp.contrib0 * ((1 + self.g_salary) ** t) * price_t
        lo, hi = self.band.min, self.band.max
        zeros = np.zeros(P)
        since0 = 12 * t - pp.K  # months since retirement at the start of the year (negative: still saving)
        acc = np.clip(-since0, 0, 12)
        if np.all(acc == 12):
            pp.balance = _accumulate(pp.balance, c_nom, r_pre, acc)
            return {"drawn": zeros, "need": zeros, "headroom": zeros}
        if np.any(acc > 0):
            pp.balance = np.where(acc > 0, _accumulate(pp.balance, c_nom, r_pre, acc), pp.balance)
        retiring = (since0 <= 0) & (since0 > -12)
        if retiring.any():
            pp.cap_ret = np.where(retiring, pp.balance, pp.cap_ret)
            pp.cap_ret_real = np.where(retiring, pp.balance / (price_t * (1 + infl_t) ** (acc / 12)), pp.cap_ret_real)
            pp.shadow = np.where(retiring, pp.balance, pp.shadow)
            self._anniversary(pp, retiring, np.zeros(P, dtype=np.int64), t, lo, hi)
        before = since0 > 0
        seg1 = pp.k_mod * before
        seg2 = 12 - acc - seg1
        start2 = np.maximum(since0, 0) + seg1  # months since retirement when the second run begins
        drawn = np.zeros(P)
        need = np.zeros(P)
        headroom = np.zeros(P)
        for seg, first in ((seg1, np.maximum(since0, 0)), (seg2, start2)):
            if seg is seg2 and before.any():
                self._anniversary(pp, before & (seg1 < 12), start2, t, lo, hi)  # escalate and re-clamp at the anniversary
            if not np.any(seg > 0):
                continue
            pp.balance = np.maximum(_drawdown(pp.balance, pp.w_eff, r_post, seg), 0.0)
            drawn += pp.w_eff * seg
            in_window = np.clip(pp.window - first, 0, seg)
            need += pp.w_need * in_window
            headroom += np.maximum(hi * pp.ann_balance / 12 - pp.w_eff, 0.0) * seg
            live = (in_window > 0) & (pp.depleted_month < 0)
            if live.any():
                shadow, k = _deplete(pp.shadow, pp.w_need, r_post, in_window * live)
                pp.depleted_month = np.where(k > 0, first + k, pp.depleted_month)
                pp.shadow = np.where(live, shadow, pp.shadow)
        return {"drawn": drawn, "need": need, "headroom": headroom}

    def _anniversary(self, pp: _Person, mask: np.ndarray, since: np.ndarray, t: int, lo: float, hi: float) -> None:
        """A new annuity year: the need escalated with the price level, the drawdown clamped to the band."""
        if not mask.any():
            return
        tau = pp.tau + since / 12
        index = self.price[:, t] * (1 + self.infl[:, t]) ** (tau - t)  # the price level on the anniversary
        w_new = np.where(since < pp.window, pp.need_today * index, 0.0)
        pp.w_need = np.where(mask, w_new, pp.w_need)
        pp.w_eff = np.where(mask, np.clip(12 * w_new, lo * pp.balance, hi * pp.balance) / 12, pp.w_eff)
        pp.ann_balance = np.where(mask, pp.balance, pp.ann_balance)

    # ------------------------------------------------------------------ the year loop

    def run(self) -> EngineResult:
        inp, P, T = self.inp, self.P, self.T
        hh = inp.household
        port = self.port
        tol = self.config.success_definition.tolerance
        cash = hh.cash0.astype(float).copy()
        disc = hh.discretionary0.astype(float).copy()
        off = hh.offshore0.astype(float).copy()
        tfsa = hh.tfsa0.astype(float).copy()
        edu = hh.education0.astype(float).copy()
        prop = hh.property0.astype(float).copy()
        biz = hh.business0.astype(float).copy()
        loan_acc = hh.loan_account0.astype(float).copy()
        life = {k: v.astype(float).copy() for k, v in hh.lifestyle0.items()}
        goal_bal = [g.savings0.astype(float).copy() for g in inp.goals]
        goal_funded = {g.key: np.zeros(P, dtype=bool) for g in inp.goals}
        horizon = np.max(np.stack([pp.horizon for pp in self.people]), axis=0)  # years to the latest planning age
        primary = inp.persons[0]

        net_worth = np.empty((P, T + 1))
        ret_income = np.zeros((P, T))
        unmet_total = np.zeros(P)
        failed = np.zeros(P, dtype=bool)
        first_fail = np.full(P, np.nan)
        breach = np.zeros(P, dtype=bool)
        a0 = cash + disc + off + tfsa + edu + sum(goal_bal, np.zeros(P)) + sum((pp.balance for pp in self.people), np.zeros(P))
        pv_income = np.zeros(P)
        pv_ess = np.zeros(P)
        df = np.ones(P)
        net_worth[:, 0] = self._net_worth(cash, disc, off, tfsa, edu, goal_bal, prop, biz, loan_acc, life, 0)

        for t in range(T):
            x0 = self.fx[:, t]
            xm = self.fx_mid[:, t]
            pm = self.p_mid[:, t]
            in_horizon = t < horizon

            # Goals fall due at the start of their target year: dedicated savings first, then liquid assets.
            for gi, g in enumerate(inp.goals):
                due = g.step == t
                if not due.any():
                    continue
                cost = g.cost.at(x0)
                from_goal = np.minimum(goal_bal[gi], cost)
                rest = cost - from_goal
                ok = due & (rest <= cash + disc + off + tfsa + 1e-6)
                goal_funded[g.key] |= ok
                cash, disc, off, tfsa, _ = _draw(np.where(ok, rest, 0.0), cash, disc, off, tfsa)
                disc = disc + np.where(due & ~ok, goal_bal[gi], 0.0)  # an unaffordable goal is dropped; its savings stay invested
                goal_bal[gi] = np.where(due, 0.0, goal_bal[gi])

            # Half a year's growth before the year's flows (flows sit mid-year).
            h = {k: v[:, t] for k, v in self.half.items()}
            cash *= h["cash"]
            disc *= h["discretionary"]
            off *= h["offshore"]
            tfsa *= h["tfsa"]
            edu *= h["capital"]
            goal_bal = [b * h["capital"] for b in goal_bal]

            # People: retirement capital (monthly), income and tax.
            inflow = np.zeros(P)
            need_net_total = np.zeros(P)
            need_gross_total = np.zeros(P)
            worker_share = np.zeros(P)
            any_working = np.zeros(P, dtype=bool)
            ret_vol = []
            premiums = np.zeros(P)
            income_y = np.zeros(P)
            payroll_spend = np.zeros(P)
            extra_capacity = []
            years: list[dict[str, np.ndarray]] = []
            for j, pp in enumerate(self.people):
                src = pp.src
                flows = self._retirement_year(pp, t, j)
                months = np.clip(pp.K - 12 * t, 0, 12)
                fw = months / 12.0
                any_working |= months > 0
                gross, bonus = self._earnings(src, t, xm)
                grow_c = (1 + self.g_salary) ** t
                years.append({
                    "fw": fw, "months": months, "gross": gross, "bonus": bonus, "other": src.other_taxable_monthly.at(xm),
                    "nontax": src.non_taxable_monthly.at(xm), "c_payroll": src.contrib_payroll_monthly * grow_c,
                    "c_own": src.contrib_own_monthly * grow_c, "c_employer": src.contrib_employer_monthly * grow_c,
                    "c_earmarked": src.contrib_earmarked_monthly * grow_c, "drawn": flows["drawn"] / pm, "need": flows["need"] / pm,
                    "headroom": flows["headroom"] / pm, "rental": src.rental_monthly.at(xm) * 12, "age": src.age0 + t + 0.5,
                })
            taxes, need_taxes = self._year_tax(years)
            for j, (pp, y, tax, need_tax) in enumerate(zip(self.people, years, taxes, need_taxes)):
                src = pp.src
                fw = y["fw"]
                earned = (y["gross"] * 12 + y["bonus"] + y["other"] * 12 + y["nontax"] * 12) * fw
                deductions = (y["c_payroll"] + src.medical_payroll_monthly + src.other_payroll_monthly) * 12 * fw
                annual_tax, uif = tax
                inflow += earned + y["rental"] + y["drawn"] - annual_tax - uif * 12 - deductions
                payroll_spend += (src.medical_payroll_monthly + src.other_payroll_monthly) * 12 * fw
                if need_tax is not None:
                    # A retired person's target is a gross income (as in the FNA): their spending is the target after tax.
                    need_net_total += y["need"] - need_tax
                    need_gross_total += y["need"]
                retired_months = 1.0 - fw
                worker_share += src.share * (fw + retired_months * ~pp.has_target)
                ret_vol.append((y["c_own"] + y["c_earmarked"]) * 12 * fw)
                income_y += earned + y["rental"] - annual_tax - uif * 12 + y["c_employer"] * 12 * fw
                extra_capacity.append((y["headroom"], y["drawn"] + y["rental"], y["age"]))
                ret_income[:, t] += y["drawn"] + src.other_income_monthly * 12 * retired_months
                for prem in inp.premiums:
                    if prem.person == j:
                        premiums += prem.monthly * (1 + prem.escalation) ** t / pm * 12 * fw

            # Household spending (today's money).
            child_on = (hh.children_end_step < 0) | (t < hh.children_end_step)
            support_on = (hh.support_end_step < 0) | (t < hh.support_end_step)
            living_ess = (hh.living_essential.at(xm) + hh.children_essential.at(xm) * child_on + hh.support_essential.at(xm) * support_on) * 12
            living_disc = (hh.living_discretionary.at(xm) + hh.children_discretionary.at(xm) * child_on) * 12
            spend_ess = living_ess * worker_share
            spend_disc = living_disc * worker_share
            school, support_dep, tertiary = self._dependant_costs(t, xm)
            from_edu = np.minimum(edu, tertiary)
            edu -= from_edu
            debt = sum((ln.payments[:, t] for ln in self.loans), np.zeros(P)) / pm
            ev_in, ev_ess, ev_disc = self._events(t, xm)
            inflow += ev_in
            income_y += ev_in
            essential = spend_ess + need_net_total + school + support_dep + (tertiary - from_edu) + debt + premiums + ev_ess
            discretionary = spend_disc + ev_disc
            ret_voluntary = sum(ret_vol, np.zeros(P))

            # Contributions to household savings come out of income only (scaled down in a lean year).
            hh_working = any_working.astype(float)
            c_cash = hh.contrib_cash * 12 * hh_working
            c_disc = hh.contrib_discretionary * 12 * hh_working
            c_off = hh.contrib_offshore * 12 * hh_working
            c_tfsa = hh.contrib_tfsa * 12 * hh_working
            c_edu = hh.contrib_education * 12 * hh_working * self._children_saving(t)
            c_goal = [np.where(t < g.step, g.monthly_nominal * 12 * self.price[:, 0] / pm, 0.0) for g in inp.goals]
            c_hh = c_cash + c_disc + c_off + c_tfsa + c_edu + sum(c_goal, np.zeros(P))
            s0 = inflow - essential - discretionary - ret_voluntary
            phi = np.where(c_hh > 0, np.clip(s0 / np.where(c_hh > 0, c_hh, 1.0), 0.0, 1.0), 0.0)
            cash += phi * c_cash
            disc += phi * c_disc
            off += phi * c_off
            tfsa += phi * c_tfsa
            edu += phi * c_edu
            goal_bal = [b + phi * c for b, c in zip(goal_bal, c_goal)]
            surplus = s0 - phi * c_hh
            disc += np.maximum(surplus, 0.0)
            deficit = np.maximum(-surplus, 0.0)
            cash, disc, off, tfsa, deficit = _draw(deficit, cash, disc, off, tfsa)

            # Post-retirement: extra living annuity income, grossed up for tax, within each annuity's band.
            if deficit.any() and any(hr.any() for hr, _, _ in extra_capacity):
                rates = [marginal_rate(self.tax, taxable, age) if hr.any() else np.zeros(P) for hr, taxable, age in extra_capacity]
                capacity = [hr * (1 - rate) for (hr, _, _), rate in zip(extra_capacity, rates)]
                total_cap = sum(capacity, np.zeros(P))
                take = np.minimum(deficit, total_cap)
                for pp, cap, rate in zip(self.people, capacity, rates):
                    share = np.where(total_cap > 0, cap / np.where(total_cap > 0, total_cap, 1.0), 0.0)
                    gross_take = take * share / np.maximum(1 - rate, 0.05)
                    pp.balance = np.maximum(pp.balance - gross_take * pm, 0.0)
                    ret_income[:, t] += gross_take
                deficit = deficit - take
            unmet = deficit
            breach |= in_horizon & any_working & (unmet > 1e-6)
            cut = np.minimum(unmet, discretionary)
            unmet = unmet - cut
            if unmet.any() and ret_voluntary.any():
                cut = np.minimum(unmet, ret_voluntary)
                for pp, rv in zip(self.people, ret_vol):
                    share = np.where(ret_voluntary > 0, rv / np.where(ret_voluntary > 0, ret_voluntary, 1.0), 0.0)
                    pp.balance = np.maximum(pp.balance - cut * share * pm, 0.0)  # contributions not made after all
                unmet = unmet - cut
            # Unmet essential spend within the tolerance counts as met (rounding), so it is not a shortfall either:
            # a successful path reports no shortfall and P(any shortfall) = 1 − PoS.
            short = in_horizon & (unmet > tol * np.maximum(essential, 1.0))
            first_fail = np.where(short & ~failed, primary.age0 + t, first_fail)
            failed |= short
            unmet_total += np.where(short, unmet, 0.0)

            # Funded ratio flows, discounted at the path's own returns (mid-year).
            ess_y = spend_ess + need_gross_total + payroll_spend + school + support_dep + tertiary + debt + premiums + ev_ess
            df_mid = df / np.sqrt(port.reference[:, t])
            pv_income += np.where(in_horizon, income_y * df_mid, 0.0)
            pv_ess += np.where(in_horizon, ess_y * df_mid, 0.0)
            df = df / port.reference[:, t]

            # Second half of the year's growth; assets the plan does not draw on; loans.
            cash *= h["cash"]
            disc *= h["discretionary"]
            off *= h["offshore"]
            tfsa *= h["tfsa"]
            edu *= h["capital"]
            goal_bal = [b * h["capital"] for b in goal_bal]
            ra = self.cma.real_assets
            prop *= 1 + ra.property_real_growth
            biz *= 1 + ra.business_real_growth
            if ra.loan_account_nominal:
                loan_acc /= 1 + self.infl[:, t]
            for k in life:
                life[k] *= 1 + ra.lifestyle_real_growth.get(k, 0.0)
            net_worth[:, t + 1] = self._net_worth(cash, disc, off, tfsa, edu, goal_bal, prop, biz, loan_acc, life, t + 1)

        funded = (a0 + pv_income) / np.maximum(pv_ess, 1.0)
        cap = {pp.src.key: pp.cap_ret for pp in self.people}
        cap_real = {pp.src.key: pp.cap_ret_real for pp in self.people}
        depletion = {pp.src.key: np.where(pp.depleted_month >= 0, pp.ret_start + pp.depleted_month / 12.0, np.nan) for pp in self.people}
        return EngineResult(
            success=~failed,
            funded_ratio=funded,
            first_shortfall_age=first_fail,
            total_shortfall_real=unmet_total,
            liquidity_breach=breach,
            net_worth_real=net_worth,
            retirement_income_real=ret_income,
            goal_funded=goal_funded,
            capital_at_retirement=cap,
            capital_at_retirement_real=cap_real,
            depletion_age=depletion,
            ages=[inp.primary_age_int + t for t in range(T + 1)],
        )

    # ------------------------------------------------------------------ pieces

    def _year_tax(self, years: list[dict[str, np.ndarray]]) -> tuple[list[tuple[np.ndarray, np.ndarray]], list[np.ndarray | None]]:
        """Each person's tax for the year (salary, bonus, retirement and asset income) and the tax on each retiree's
        target, in one call to the tax model: its fixed cost per call dominates small runs."""
        P = self.P
        rows: list[dict[str, np.ndarray]] = []
        for pp, y in zip(self.people, years):
            fw = y["fw"]
            rows.append({
                "age": y["age"], "gross_monthly": y["gross"] * fw, "annual_bonus": y["bonus"] * fw,
                "other_taxable_monthly": y["other"] * fw + (y["rental"] + y["drawn"]) / 12,
                "retirement_contributions_annual": (y["c_payroll"] + y["c_own"] + y["c_employer"]) * 12 * fw,
                "employer_retirement_annual": y["c_employer"] * 12 * fw, "medical_scheme_members": pp.src.medical_members,
                "uif_applies": pp.src.uif & (y["months"] > 0),
            })
        needs = [i for i, y in enumerate(years) if y["need"].any()]
        zero = np.zeros(P)
        for i in needs:
            rows.append({
                "age": years[i]["age"], "gross_monthly": zero, "annual_bonus": zero, "other_taxable_monthly": years[i]["need"] / 12,
                "retirement_contributions_annual": zero, "employer_retirement_annual": zero,
                "medical_scheme_members": self.people[i].src.medical_members, "uif_applies": np.zeros(P, dtype=bool),
            })
        args = {k: np.concatenate([r[k] for r in rows]) for k in rows[0]}  # every field is a (P,) array
        out = self.tax.person_tax(**args)
        tax = np.asarray(out.annual_tax, dtype=float).reshape(len(rows), P)
        uif = np.asarray(out.monthly_uif, dtype=float).reshape(len(rows), P)
        own = [(tax[i], uif[i]) for i in range(len(years))]
        need_tax: list[np.ndarray | None] = [None] * len(years)
        for k, i in enumerate(needs):
            need_tax[i] = tax[len(years) + k]
        return own, need_tax

    def _earnings(self, p: PersonIn, t: int, xm: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Real monthly gross pay and the annual bonus in year t (growth, then any income change)."""
        growth = (1 + p.growth_real) ** t
        base = p.gross_monthly.at(xm)
        gross = base * growth
        for c in p.changes:
            on = c.occurs & (t >= c.step)
            if on.any():
                new = c.new_gross_monthly.at(xm) * (1 + p.growth_real) ** np.maximum(t - c.step, 0)
                gross = np.where(on, new, gross)
        ratio = np.where(base > 0, gross / np.where(base > 0, base * growth, 1.0), 0.0)
        bonus = p.bonus_annual.at(xm) * growth * ratio
        return gross, bonus

    def _children_saving(self, t: int) -> np.ndarray:
        """Education savings continue while any child has yet to start tertiary study (FNA projects to the start)."""
        kids = [d for d in self.inp.dependants if d.is_child]
        if not kids:
            return np.zeros(self.P)
        before = np.zeros(self.P, dtype=bool)
        for d in kids:
            before |= t < np.maximum(0.0, d.tertiary_start_age - d.age0)
        return before.astype(float)

    def _dependant_costs(self, t: int, xm: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """School fees, family support and tertiary costs for year t (today's money, education inflation on top)."""
        P = self.P
        hh = self.inp.household
        esc = (1 + self.g_edu) ** t
        school = np.zeros(P)
        support = np.zeros(P)
        tertiary = np.zeros(P)
        for d in self.inp.dependants:
            age = d.age0 + t
            if d.is_child:
                if hh.school_from_dependants and d.school:
                    in_school = (age >= 6) & (age <= 18)  # FNA computeEducation: Grade R to matric
                    school += d.school_fees_annual.at(xm) * esc * in_school
                to_start = np.maximum(0.0, d.tertiary_start_age - d.age0)
                remaining = np.maximum(0.0, d.tertiary_years - np.maximum(0.0, d.age0 - d.tertiary_start_age))
                local = d.tertiary & (t >= to_start) & (t < to_start + remaining)
                cost = d.tertiary_cost_annual.at(xm) * esc * local
                if d.offshore is not None:
                    o = d.offshore
                    start = np.where(o.start_step >= 0, o.start_step, to_start)
                    away = (t >= start) & (t < start + remaining)
                    cost = np.where(o.occurs, o.annual_cost.at(xm) * esc * away, cost)
                tertiary += cost
            elif hh.support_from_dependants:
                support += d.support_monthly.at(xm) * 12 * (t < d.support_years)
        return school, support, tertiary

    def _events(self, t: int, xm: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Event cash in year t: inflows, essential costs, discretionary costs (today's money)."""
        P = self.P
        inflow = np.zeros(P)
        ess = np.zeros(P)
        disc = np.zeros(P)
        for e in self.inp.events:
            on = e.occurs & ((t >= e.step) if e.recurring else (t == e.step))
            if not on.any():
                continue
            amount = e.amount.at(xm) * on
            inflow += np.maximum(amount, 0.0)
            cost = np.maximum(-amount, 0.0)
            if e.essential:
                ess += cost
            else:
                disc += cost
        return inflow, ess, disc

    def _net_worth(self, cash, disc, off, tfsa, edu, goal_bal, prop, biz, loan_acc, life, t: int) -> np.ndarray:
        price = self.price[:, t]
        total = cash + disc + off + tfsa + edu + prop + biz + loan_acc + sum(goal_bal, np.zeros(self.P)) + sum(life.values(), np.zeros(self.P))
        total = total + sum((pp.balance for pp in self.people), np.zeros(self.P)) / price
        if t > 0:
            total = total - sum((ln.balance_end[:, t - 1] for ln in self.loans), np.zeros(self.P)) / price
        else:
            total = total - sum((l.balance for l in self.inp.liabilities), np.zeros(self.P))
        return total


def _draw(need: np.ndarray, *buckets: np.ndarray) -> tuple[np.ndarray, ...]:
    """Take ``need`` from the buckets in order; returns the buckets and what could not be found."""
    out = []
    for b in buckets:
        take = np.minimum(b, need)
        out.append(b - take)
        need = need - take
    return (*out, need)
