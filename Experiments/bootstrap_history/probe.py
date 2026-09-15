"""Scoped historical work, finite local acceptance, and separate future authority."""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bootstrap_exchange'))
from probe import authentic, digest, encode, public, sign
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

GRANT = b'voyage/history-grant/v1\0'
REMOVE = b'voyage/history-remove/v1\0'
WORK = b'voyage/history-work/v1\0'


def decide(work, events, frontier, anchor, scope, acceptance=None, device="newcomer-a"):
    result = {'historical_signature': False, 'past_integration': False,
              'recognized_in_selected_view': False, 'future_key_release': False, 'view': list(frontier), 'retained': copy.deepcopy(events)}
    def done(reason):
        result['reason'] = reason
        return result
    payload = work['payload']
    if not authentic(payload['signer'], WORK, work):
        return done('reject invalid work signature')
    result['historical_signature'] = True
    if payload['scope'] != scope:
        return done('reject wrong berth or purpose')
    if any(event_id not in events for event_id in frontier):
        return done('pause missing selected event')
    grant_id = payload['basis']
    if grant_id not in events:
        return done('pause missing authority basis')
    grant = events[grant_id]
    if digest(encode(grant)) != grant_id or not authentic(anchor, GRANT, grant):
        return done('reject grant authority')
    if grant['payload'] != {'subject': payload['signer'], 'scope': scope, 'parents': []}:
        return done('reject grant scope')
    # The selected closure here is either the grant or its one removal child.
    removed = False
    selected = grant_id in frontier
    for event_id in frontier:
        if event_id == grant_id:
            continue
        event = events[event_id]
        if digest(encode(event)) != event_id or not authentic(anchor, REMOVE, event):
            return done('reject removal evidence')
        if event['payload'] != {'subject': payload['signer'], 'scope': scope, 'parents': [grant_id]}:
            return done('reject removal scope or ancestry')
        removed = selected = True
    if not selected:
        return done('pause authority basis outside selected view')
    result['recognized_in_selected_view'] = not removed
    result['work_cites_valid_grant'] = True
    # A signer can cite its old grant after removal. There is no independent
    # observation of creation time in this model, so never infer one.
    result['creation_before_removal_proved'] = False
    if not removed:
        result['past_integration'] = True
        return done('integrate under selected grant view')
    expected = {'actor': 'newcomer operator', 'device': device, 'work': digest(encode(work)),
                'view': list(frontier), 'scope': scope, 'decision': 'accept finite past work'}
    if acceptance != expected:
        return done('pause removed author for local review')
    result['acceptance'] = copy.deepcopy(acceptance)
    result['past_integration'] = True
    return done('integrate only the explicitly accepted work')


def scenarios():
    anchor, author = [Ed25519PrivateKey.generate() for _ in range(2)]
    scope = {'team': 'team-a', 'berth': 'notes', 'purpose': 'commit-signing'}
    grant = sign(anchor, GRANT, {'subject': public(author), 'scope': scope, 'parents': []})
    grant_id = digest(encode(grant))
    removal = sign(anchor, REMOVE, {'subject': public(author), 'scope': scope, 'parents': [grant_id]})
    removal_id = digest(encode(removal))
    work = sign(author, WORK, {'signer': public(author), 'scope': scope,
                'basis': grant_id, 'content_digest': digest(b'old work'), 'claimed_time': 'before removal'})
    events = {grant_id: grant, removal_id: removal}
    accepted = {'actor': 'newcomer operator', 'device': 'newcomer-a', 'work': digest(encode(work)),
                'view': [removal_id], 'scope': scope, 'decision': 'accept finite past work'}
    outputs = {}
    def check(name, expected, w=work, e=events, view=None, decision=None, requested=scope, device='newcomer-a'):
        result = decide(w, e, [removal_id] if view is None else view,
                        public(anchor), requested, decision, device)
        assert result['reason'] == expected, (name, result)
        outputs[name] = result
        return result
    current = check('valid_grant_view', 'integrate under selected grant view', view=[grant_id])
    paused = check('removed_before_join', 'pause removed author for local review')
    accepted_result = check('explicit_finite_acceptance', 'integrate only the explicitly accepted work', decision=accepted)
    assert accepted_result['past_integration'] and not accepted_result['recognized_in_selected_view']
    assert paused['historical_signature'] and not paused['past_integration']
    assert current['recognized_in_selected_view'] and not paused['recognized_in_selected_view']
    later = sign(author, WORK, {**work['payload'], 'content_digest': digest(b'later malicious work')})
    check('same_key_later_work', 'pause removed author for local review', w=later, decision=accepted)
    # Backdating and citing a real old grant cannot extend the finite decision.
    assert later['payload']['claimed_time'] == work['payload']['claimed_time']
    check('missing_authority_parent', 'pause missing authority basis', e={removal_id: removal})
    check('missing_selected_removal', 'pause missing selected event', e={grant_id: grant})
    check('wrong_berth', 'reject wrong berth or purpose', requested={**scope, 'berth': 'finances'}, decision=accepted)
    broken = copy.deepcopy(work);broken['signature'] = '00' * 64
    check('invalid_signature_override', 'reject invalid work signature', w=broken, decision=accepted)
    changed_decision = {**accepted, 'view': [grant_id]}
    check('decision_for_different_view', 'pause removed author for local review', decision=changed_decision)
    check('decision_from_another_device', 'pause removed author for local review', decision=accepted, device='newcomer-b')
    assert not any(row['future_key_release'] for row in outputs.values())
    assert not any(row.get('creation_before_removal_proved') for row in outputs.values())
    return outputs


if __name__ == '__main__':
    print(json.dumps(scenarios(), indent=2, sort_keys=True))
