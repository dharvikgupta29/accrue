"""The learned guards the stress suite pits against accrue.

Every guard answers the same question - P(this call is a violation), given
the session so far - through one interface:

    guard.fit(trajectories)
    guard.scores(trajectories)       -> per-trajectory lists of P(violation)
    guard.score_next(history, cands) -> P(violation) for each candidate amount
                                        appended to `history` (attack.py)

Arms that read ledger state are trained on the true state but scored on
their own decisions - see FeatureMLP.scores.

Two families:

- FeatureMLP: the per_call_baseline net, fed a chosen set of per-call
  features. Varying the features is how the suite asks "what does it need
  to see?"
- SequenceGRU: a recurrent net that sees only the raw amounts, in order.
  Nobody hands it the running total; if it wants one, it has to learn to
  keep it. This is the reviewer's question - "fine, but what about a model
  with memory?"

Needs scikit-learn and torch (experiments/requirements.txt).
"""

import numpy as np
import torch
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from torch import nn

from .per_call_baseline import to_xy

def _oversample(X, y, seed):
    """Repeat the violating rows until the classes are even. MLPClassifier
    has no class_weight, and with volume's 0.6% positives it otherwise
    learns "never flag" and stops."""
    pos, neg = np.flatnonzero(y), np.flatnonzero(~y)
    if len(pos) == 0 or len(pos) >= len(neg):
        return X, y
    extra = np.random.default_rng(seed).choice(pos, len(neg) - len(pos))
    idx = np.concatenate([neg, pos, extra])
    return X[idx], y[idx]


class FeatureMLP:
    def __init__(self, columns, seed=0, max_iter=2000, early_stopping=False,
                 balance=False):
        self.columns = columns
        self.seed = seed
        self.max_iter = max_iter
        self.early_stopping = early_stopping
        self.balance = balance

    def fit(self, trajectories):
        X, y = to_xy(trajectories, self.columns)
        if self.balance:
            X, y = _oversample(X, y, self.seed)
        self.model = make_pipeline(
            StandardScaler(),
            MLPClassifier(hidden_layer_sizes=(32, 32), max_iter=self.max_iter,
                          early_stopping=self.early_stopping,
                          random_state=self.seed),
        )
        self.model.fit(X, y)
        return self

    # Features that depend on what the guard itself decided earlier.
    LEDGER_STATE = {"allowed_before", "denied_before"}

    def _row(self, amount, position, attempted, allowed, denied):
        row = {"amount": amount, "position": position,
               "running_total_before": attempted,
               "allowed_before": allowed, "denied_before": float(denied)}
        return [row[c] for c in self.columns]

    def scores(self, trajectories):
        """Training uses the true ledger state (teacher forcing). Scoring
        must not: a deployed guard's state comes from its *own* earlier
        decisions. Feeding it the ground-truth ledger would tell it "a call
        was already denied" even when it missed that call itself. So arms
        that read ledger state are run call by call - a call it flags is
        denied and stops counting toward its total, as accrue does."""
        if not self.LEDGER_STATE & set(self.columns):
            out = []
            for t in trajectories:
                X, _ = to_xy([t], self.columns)
                out.append(self.model.predict_proba(X)[:, 1].tolist())
            return out

        out = []
        for t in trajectories:
            attempted = allowed = 0
            denied, probs = False, []
            for i, amount in enumerate(t.amounts):
                x = np.array([self._row(amount, i, attempted, allowed, denied)], dtype=float)
                p = self.model.predict_proba(x)[0, 1]
                probs.append(p)
                if p >= 0.5:
                    denied = True
                else:
                    allowed += amount
                attempted += amount
            out.append(probs)
        return out

    def score_next(self, history, candidates):
        """P(violation) for each candidate appended to `history`. The
        attacker only ever sends calls the guard let through, so the whole
        history was allowed: nothing denied, allowed total = plain sum."""
        spent = sum(history)
        X = np.array([self._row(c, len(history), spent, spent, False) for c in candidates],
                     dtype=float)
        return self.model.predict_proba(X)[:, 1]


class _GRUNet(nn.Module):
    def __init__(self, hidden):
        super().__init__()
        self.gru = nn.GRU(1, hidden, batch_first=True)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x):
        out, _ = self.gru(x)
        return self.head(out).squeeze(-1)


class SequenceGRU:
    """One-layer GRU over the amounts, one logit per call.

    Input is amount / scale, where scale is the largest amount seen in
    training - an ordinary preprocessing choice fixed at fit time, exactly
    like the MLP's StandardScaler. It is not told the budget.
    """

    def __init__(self, seed=0, hidden=64, epochs=60, lr=3e-3, batch=64,
                 balance=False):
        self.balance = balance
        self.seed = seed
        self.hidden = hidden
        self.epochs = epochs
        self.lr = lr
        self.batch = batch

    def _pad(self, sequences):
        n, longest = len(sequences), max(len(s) for s in sequences)
        x = torch.zeros(n, longest, 1)
        mask = torch.zeros(n, longest, dtype=torch.bool)
        for i, s in enumerate(sequences):
            x[i, :len(s), 0] = torch.tensor(s, dtype=torch.float32) / self.scale
            mask[i, :len(s)] = True
        return x, mask

    def fit(self, trajectories):
        torch.manual_seed(self.seed)
        rng = np.random.default_rng(self.seed)
        self.scale = float(max(max(t.amounts) for t in trajectories))
        self.net = _GRUNet(self.hidden)
        opt = torch.optim.Adam(self.net.parameters(), lr=self.lr)
        seqs = [t.amounts for t in trajectories]
        labels = [t.labels() for t in trajectories]

        # Same idea as FeatureMLP's oversampling: weight the rare violating
        # calls up so "never flag" isn't the cheapest answer.
        pos_weight = None
        if self.balance:
            flat = [y for ys in labels for y in ys]
            pos_weight = torch.tensor((len(flat) - sum(flat)) / max(sum(flat), 1))
        loss_fn = nn.BCEWithLogitsLoss(reduction="none", pos_weight=pos_weight)
        self.net.train()
        for _ in range(self.epochs):
            order = rng.permutation(len(seqs))
            for start in range(0, len(order), self.batch):
                idx = order[start:start + self.batch]
                x, mask = self._pad([seqs[i] for i in idx])
                y = torch.zeros(mask.shape)
                for row, i in enumerate(idx):
                    y[row, :len(labels[i])] = torch.tensor(labels[i], dtype=torch.float32)
                loss = (loss_fn(self.net(x), y) * mask).sum() / mask.sum()
                opt.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self.net.parameters(), 1.0)
                opt.step()
        self.net.eval()
        return self

    @torch.no_grad()
    def _probs(self, sequences):
        x, mask = self._pad(sequences)
        p = torch.sigmoid(self.net(x))
        return [p[i, :len(s)].tolist() for i, s in enumerate(sequences)]

    def scores(self, trajectories):
        out = []
        for start in range(0, len(trajectories), 256):
            out.extend(self._probs([t.amounts for t in trajectories[start:start + 256]]))
        return out

    def score_next(self, history, candidates):
        probs = self._probs([tuple(history) + (c,) for c in candidates])
        return np.array([p[-1] for p in probs])


def guards(seed=0, balance=False, **mlp_kwargs):
    """The learned arms, in the order the suite reports them. `balance`
    weights rare violating calls up in every arm; `mlp_kwargs` go to every
    FeatureMLP (stress.py uses both for the volume class)."""
    mlp_kwargs["balance"] = balance
    return {
        "MLP: amount only": FeatureMLP(["amount"], seed, **mlp_kwargs),
        "MLP: + call position": FeatureMLP(["amount", "position"], seed, **mlp_kwargs),
        "MLP: + running total": FeatureMLP(["amount", "allowed_before"], seed, **mlp_kwargs),
        "MLP: + full ledger state": FeatureMLP(
            ["amount", "allowed_before", "denied_before"], seed, **mlp_kwargs),
        "GRU: raw sequence": SequenceGRU(seed, balance=balance),
    }
