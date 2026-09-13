"""Executable research model of local Constitution intake; no cryptography."""

import argparse
import itertools
import json
import random
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    parents: frozenset[int]
    valid: bool = True


class View:
    def __init__(self, cap):
        assert cap >= 1
        self.cap = cap
        self.events = {}
        self.state = {}
        self.tips = set()

    def receive(self, key, event):
        # Integer IDs abstract content digests; replacing content is forbidden.
        assert key not in self.events or self.events[key] == event
        self.events[key] = event
        self.state.setdefault(key, 'incomplete')
        changed = True
        while changed:
            changed = False
            for node, item in self.events.items():
                if self.state[node] != 'incomplete':
                    continue
                if not item.valid or any(self.state.get(p) == 'invalid' for p in item.parents):
                    self.state[node] = 'invalid'
                elif all(self.state.get(p) in ('active', 'parked') for p in item.parents):
                    candidate = self.tips.difference(item.parents) | {node}
                    if any(self.state[p] == 'parked' for p in item.parents) or len(candidate) > self.cap:
                        self.state[node] = 'parked'
                    else:
                        self.state[node] = 'active'
                        self.tips = candidate
                else:
                    continue
                changed = True
                break

    def check(self):
        active = {n for n, state in self.state.items() if state == 'active'}
        parents = set()
        for node in active:
            assert self.events[node].parents <= active
            parents.update(self.events[node].parents)
        assert self.tips == active - parents
        assert len(self.tips) <= self.cap
        for node, event in self.events.items():
            if any(self.state.get(p) == 'parked' for p in event.parents):
                assert self.state[node] != 'active'
        return tuple(sorted(self.tips))


def reference(events, order, cap):
    """Independent slow oracle: recompute active heads from the full active set."""
    known, states, active = set(), {}, set()
    for key in order:
        known.add(key)
        while True:
            ready = next((n for n in order if n in known and n not in states
                          and events[n].parents <= states.keys()), None)
            if ready is None:
                break
            event = events[ready]
            if not event.valid or any(states[p] == 'invalid' for p in event.parents):
                states[ready] = 'invalid'
                continue
            proposed = active | {ready}
            ancestors = set().union(*(events[n].parents for n in proposed))
            if not event.parents <= active or len(proposed - ancestors) > cap:
                states[ready] = 'parked'
            else:
                states[ready] = 'active'
                active = proposed
    return states


def micro_tests():
    # Flood and attacker merge finish at one tip, but exceed the cap en route.
    events = {0: Event(frozenset()), 1: Event(frozenset({0})),
              2: Event(frozenset({0})), 3: Event(frozenset({1, 2}))}
    outcomes = set()
    for order in itertools.permutations(events):
        view = View(1)
        for key in order:
            view.receive(key, events[key])
            view.check()
        assert view.state[3] == 'parked'
        outcomes.add(view.check())
    assert outcomes == {(1,), (2,)}  # Local selection deliberately need not converge.
    view = View(1)
    view.receive(1, Event(frozenset({0})))
    assert view.state[1] == 'incomplete'
    view.receive(0, Event(frozenset(), False))
    assert view.state == {1: 'invalid', 0: 'invalid'}
    view = View(1)
    view.receive(1, Event(frozenset({0})))
    view.receive(0, Event(frozenset()))
    assert view.check() == (1,)
    # A rolling merge can fit although the graph contains >cap concurrent siblings.
    view = View(2)
    for key, parents in [(0, ()), (1, (0,)), (2, (0,)), (3, (1, 2)),
                         (4, (0,)), (5, (3, 4))]:
        view.receive(key, Event(frozenset(parents)))
        view.check()
    assert set(view.state.values()) == {'active'}
    assert view.check() == (5,)


def exercise(events, order, cap):
    view = View(cap)
    parked = set()
    for key in order:
        view.receive(key, events[key])
        view.check()
        assert all(view.state[n] == 'parked' for n in parked)
        parked.update(n for n, s in view.state.items() if s == 'parked')
    assert view.state == reference(events, order, cap)


def exhaustive(size):
    # Every labelled DAG compatible with numeric topological order, including forests.
    edges = [(child, parent) for child in range(size) for parent in range(child)]
    count = 0
    for bits in range(1 << len(edges)):
        events = {n: Event(frozenset(p for i, (c, p) in enumerate(edges)
                                   if c == n and bits & (1 << i))) for n in range(size)}
        for order in itertools.permutations(events):
            for cap in range(1, size + 1):
                exercise(events, order, cap)
                count += 1
    return count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exhaustive-size', type=int, default=4)
    parser.add_argument('--seed', type=int, default=262)
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--max-nodes', type=int, default=160)
    args = parser.parse_args()
    micro_tests()
    count = exhaustive(args.exhaustive_size)
    print(json.dumps({'exhaustive_cases': count, 'status': 'passed'}), flush=True)
    rng = random.Random(args.seed)
    start = time.monotonic()
    trials = 0
    while time.monotonic() - start < args.seconds:
        size = rng.randint(1, args.max_nodes)
        events = {n: Event(frozenset(rng.sample(range(n), rng.randint(0, min(n, 8)))))
                  for n in range(size)}
        order = list(events)
        rng.shuffle(order)
        cap = rng.randint(1, min(size, 16))
        try:
            exercise(events, order, cap)
        except AssertionError:
            print(json.dumps({'seed': args.seed, 'trial': trials, 'cap': cap,
                              'order': order, 'events': {n: sorted(e.parents) for n, e in events.items()}}))
            raise
        trials += 1
    print(json.dumps({'micro_tests': 'passed', 'exhaustive_cases': count,
                      'random_trials': trials, 'seed': args.seed,
                      'seconds': round(time.monotonic() - start, 3)}))


if __name__ == '__main__':
    main()
