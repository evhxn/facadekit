"""Policies compared in the simulation. All are pure functions of (history, observation).

``BetaPolicy`` with default parameters is the shipped policy (calib.learn.choose); a test
checks they agree on random histories. Everything else is a candidate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from facadekit.calib.learn import (
    DIFFERENT_SCALE,
    MISTAKE_WEIGHT,
    SAME_BUCKET_OTHER_CAMERA,
    SAME_CAMERA_OTHER_BUCKET,
    SIBLING_SHARE,
    Strategy,
)
from facadekit.calib.quality import finest_readable_skip
from facadekit.sim.rl.env import Obs, Rec, Table

LOW = 0  # bucket index of "low"


def floor_skip(s: float) -> int:
    return finest_readable_skip(s)


def coarse(s: float) -> bool:
    """calib.learn.scale_class(s) == "coarse"."""
    return finest_readable_skip(s) > 2


def _allowed(arms: tuple[Strategy, ...], floor: int, slack: int) -> np.ndarray:
    """Arms whose stripes the camera can resolve (skip_lsb >= floor - slack); else the coarsest."""
    need = floor - slack
    mask = np.array([a.skip_lsb >= need for a in arms])
    if not mask.any():
        top = max(a.skip_lsb for a in arms)
        mask = np.array([a.skip_lsb == top for a in arms])
    return mask


@dataclass
class Fixed:
    arm: int
    name: str = "fixed"

    def choose(self, history: list[Rec], obs: Obs, rng: np.random.Generator) -> int:
        return self.arm


@dataclass
class Oracle:
    """Knows the table: the upper bound, not a policy anyone could ship."""

    table: Table
    name: str = "oracle"

    def __post_init__(self) -> None:
        self._by_obs = {id(o): i for i, o in enumerate(self.table.obs)}

    def choose(self, history: list[Rec], obs: Obs, rng: np.random.Generator) -> int:
        return int(np.argmax(self.table.mean_reward[self._by_obs[id(obs)]]))


@dataclass
class BetaPolicy:
    """Beta posterior per arm with context sharing. Defaults = the shipped policy."""

    arms: tuple[Strategy, ...]
    name: str = "shipped"
    priors: np.ndarray | None = None  # (arms, 2); None = each Strategy.prior
    mistake_weight: float = MISTAKE_WEIGHT
    other_camera: float = SAME_BUCKET_OTHER_CAMERA
    other_bucket: float = SAME_CAMERA_OTHER_BUCKET
    different_scale: float = DIFFERENT_SCALE
    sibling_share: float = SIBLING_SHARE
    scale_sigma: float = 0.0  # > 0: weight evidence by exp(-(dlog s)^2 / 2 sigma^2) instead
    discount: float = 1.0  # evidence of a scan `age` scans ago counts discount**age
    select: str = "greedy"  # greedy | thompson | ucb
    ucb_c: float = 0.0
    floor: bool = False  # skip strategies the picture scale cannot resolve
    floor_slack: int = 0
    probe: bool = False  # the floor also uses the stripe probe (Obs.blur_floor), if taken
    # Reward given up per skip_lsb step above 2 for a scan that worked: coarser stripes measure
    # fewer points, and coverage (all the product reward sees) does not show the lost accuracy.
    skip_cost: float = 0.0
    _prior: np.ndarray = field(init=False, repr=False)
    _skip: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        base = np.array([a.prior for a in self.arms], dtype=np.float64)
        self._prior = base if self.priors is None else np.asarray(self.priors, dtype=np.float64)
        self._skip = np.array([a.skip_lsb for a in self.arms])

    def posterior(self, history: list[Rec], obs: Obs) -> np.ndarray:
        post = self._prior.copy()
        here = coarse(obs.s)
        for age, rec in enumerate(reversed(history)):
            same_cam, same_bucket = rec.cam == obs.cam, rec.bucket == obs.bucket
            if same_cam and same_bucket:
                w = 1.0
            elif same_bucket:
                w = self.other_camera
            elif same_cam:
                w = self.other_bucket
            else:
                continue
            if not rec.ok:
                w *= self.mistake_weight
            if self.scale_sigma > 0:
                d = math.log(max(rec.s, 1e-6) / max(obs.s, 1e-6))
                w *= math.exp(-0.5 * (d / self.scale_sigma) ** 2)
            elif coarse(rec.s) != here:
                w *= self.different_scale
            if self.discount != 1.0:
                w *= self.discount**age
            r = rec.reward
            if self.skip_cost and rec.ok:
                r = max(0.0, r - self.skip_cost * (int(self._skip[rec.arm]) - 2))
            post[rec.arm, 0] += w * r
            post[rec.arm, 1] += w * (1.0 - r)
            if not rec.ok and rec.bucket != LOW and self.sibling_share:
                sib = self._skip <= self._skip[rec.arm]
                sib[rec.arm] = False
                post[sib, 1] += self.sibling_share * w
        return post

    def choose(self, history: list[Rec], obs: Obs, rng: np.random.Generator) -> int:
        post = self.posterior(history, obs)
        a, b = post[:, 0], post[:, 1]
        if self.select == "thompson":
            score = rng.beta(np.maximum(a, 1e-6), np.maximum(b, 1e-6))
        else:
            score = a / (a + b)
            if self.select == "ucb":
                score = score + self.ucb_c * np.sqrt(a * b / ((a + b) ** 2 * (a + b + 1.0)))
        if self.floor:
            need = floor_skip(obs.s)
            if self.probe:
                need = max(need, obs.blur_floor)
            score = np.where(_allowed(self.arms, need, self.floor_slack), score, -np.inf)
        best, best_score = 0, -math.inf
        for i, v in enumerate(score):  # ties go to the earlier arm, as in calib.learn.choose
            if v > best_score + 1e-9:
                best, best_score = i, float(v)
        return best


def fitted_priors(
    train: Table, strength: float, *, by_floor: bool
) -> dict[tuple[int, int], np.ndarray]:
    """Empirical-Bayes priors: Beta(strength * m, strength * (1 - m)) with m the mean train
    reward of the arm among train scenes of the same lighting bucket (and scale floor)."""
    out: dict[tuple[int, int], np.ndarray] = {}
    mean = train.mean_reward
    overall = mean.mean(axis=0)
    keys = {(o.bucket, min(floor_skip(o.s), 5) if by_floor else 0) for o in train.obs}
    for key in keys | {(b, f) for b in range(3) for f in (range(6) if by_floor else (0,))}:
        idx = [
            i for i, o in enumerate(train.obs)
            if o.bucket == key[0] and (not by_floor or min(floor_skip(o.s), 5) == key[1])
        ]  # fmt: skip
        # shrink small cells towards the overall mean (10 pseudo-scenes)
        m = (mean[idx].sum(axis=0) + 10 * overall) / (len(idx) + 10)
        m = np.clip(m, 0.02, 0.98)
        out[key] = np.stack([strength * m, strength * (1 - m)], axis=1)
    return out


@dataclass
class FittedBeta:
    """The shipped update rule with priors measured on train scenes, per context."""

    arms: tuple[Strategy, ...]
    priors: dict[tuple[int, int], np.ndarray]
    by_floor: bool
    name: str = "fitted-priors"
    kwargs: dict[str, float | bool | str | int] = field(default_factory=dict)
    _cache: dict[tuple[int, int], BetaPolicy] = field(default_factory=dict, repr=False)

    def choose(self, history: list[Rec], obs: Obs, rng: np.random.Generator) -> int:
        key = (obs.bucket, min(floor_skip(obs.s), 5) if self.by_floor else 0)
        pol = self._cache.get(key)
        if pol is None:
            pol = BetaPolicy(self.arms, priors=self.priors[key], **self.kwargs)  # type: ignore[arg-type]
            self._cache[key] = pol
        return pol.choose(history, obs, rng)


@dataclass
class ScenePosterior:
    """Nearest-scene policy: weigh every train scene by how well it matches what has been seen
    (pre-check numbers and the outcomes at this site), then pick the arm with the best
    weighted train reward. It uses the joint structure the Beta rule ignores ("standard failed
    and the picture is small, so very_soft works"). It needs the train table at run time.
    """

    train: Table
    name: str = "scene-posterior"
    sigma_s: float = 0.25  # on log(px per projector px)
    sigma_c: float = 0.6  # on log(contrast)
    sigma_snr: float = 0.8  # on log(snr)
    sigma_r: float = 0.2  # on reward differences
    site_tol: float = 0.12  # |dlog s| below this = same site
    exclude: int | None = None  # leave-one-out when the episode scene is a train scene

    def __post_init__(self) -> None:
        o = self.train.obs
        self._ls = np.log(np.maximum([x.s for x in o], 1e-6))
        self._lc = np.log(np.maximum([x.contrast for x in o], 1.0))
        self._lsnr = np.log(np.maximum([x.snr for x in o], 0.5))
        self._mean = self.train.mean_reward
        self._index = {id(x): i for i, x in enumerate(o)}

    def choose(self, history: list[Rec], obs: Obs, rng: np.random.Generator) -> int:
        ls = math.log(max(obs.s, 1e-6))
        logw = -0.5 * ((self._ls - ls) / self.sigma_s) ** 2
        logw -= 0.5 * ((self._lc - math.log(max(obs.contrast, 1.0))) / self.sigma_c) ** 2
        logw -= 0.5 * ((self._lsnr - math.log(max(obs.snr, 0.5))) / self.sigma_snr) ** 2
        for rec in history:
            if rec.cam == obs.cam and abs(math.log(max(rec.s, 1e-6)) - ls) < self.site_tol:
                logw -= 0.5 * ((self._mean[:, rec.arm] - rec.reward) / self.sigma_r) ** 2
        own = self._index.get(id(obs))  # never let a train scene vote for itself
        if own is not None:
            logw = logw.copy()
            logw[own] = -np.inf
        w = np.exp(logw - logw.max())
        score = w @ self._mean / w.sum()
        best, best_score = 0, -math.inf
        for i, v in enumerate(score):
            if v > best_score + 1e-9:
                best, best_score = i, float(v)
        return best


def _status(reward: float, ok: bool) -> int:
    return 1 if not ok else (2 if reward < 0.9 else 3)


def site_state(
    history: list[Rec], obs: Obs, n_arms: int, site_tol: float = 0.12
) -> tuple[int, ...]:
    """(bucket, scale floor, per-arm status at this site): 0 untried, 1 failed, 2 fair, 3 good."""
    status = [0] * n_arms
    ls = math.log(max(obs.s, 1e-6))
    for rec in history:
        if rec.cam == obs.cam and abs(math.log(max(rec.s, 1e-6)) - ls) < site_tol:
            status[rec.arm] = _status(rec.reward, rec.ok)
    return (obs.bucket, min(floor_skip(obs.s), 5), *status)


@dataclass
class QTable:
    """Tabular Q-learning over the retry MDP. State: `site_state`. Trained on train episodes."""

    n_arms: int
    q: dict[tuple[int, ...], np.ndarray] = field(default_factory=dict)
    name: str = "q-learning"
    fallback: BetaPolicy | None = None  # for states never visited in training

    def choose(self, history: list[Rec], obs: Obs, rng: np.random.Generator) -> int:
        row = self.q.get(site_state(history, obs, self.n_arms))
        if row is None:
            assert self.fallback is not None
            return self.fallback.choose(history, obs, rng)
        return int(np.argmax(row))


def train_q(
    train: Table,
    fallback: BetaPolicy,
    *,
    scans: int = 8,
    episodes: int = 200_000,
    alpha: float = 0.1,
    epsilon: float = 0.2,
    gamma: float = 0.9,
    seed: int = 0,
) -> QTable:
    """Epsilon-greedy Q-learning with optimistic initial values, single-site episodes."""
    rng = np.random.default_rng(seed)
    k = len(train.arms)
    q: dict[tuple[int, ...], np.ndarray] = {}
    reals = train.reward.shape[2]
    for _ in range(episodes):
        i = int(rng.integers(train.n))
        obs = train.obs[i]
        history: list[Rec] = []
        state = site_state(history, obs, k)
        for _t in range(scans):
            row = q.setdefault(state, np.ones(k) / (1 - gamma))
            a = int(rng.integers(k)) if rng.random() < epsilon else int(np.argmax(row))
            r = int(rng.integers(reals))
            rew = float(train.reward[i, a, r])
            history.append(Rec(obs.cam, obs.bucket, obs.s, a, rew, bool(train.ok[i, a, r])))
            nxt = site_state(history, obs, k)
            target = rew + gamma * float(np.max(q.setdefault(nxt, np.ones(k) / (1 - gamma))))
            row[a] += alpha * (target - row[a])
            state = nxt
    return QTable(k, q, fallback=fallback)
