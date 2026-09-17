"""Two local evidence-delivery models; no runtime bootstrap implementation."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bootstrap_exchange'))
from probe import authentic, digest, encode, public, sign
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REQUEST = b'voyage/team-request/v1\0'
POSSESSION = b'voyage/team-possession/v1\0'
RESPONSE = b'voyage/team-response/v1\0'
BINDING = b'voyage/identity-team-binding/v1\0'
GRANT = b'voyage/team-enrollment-authority/v1\0'
ENROLL = b'voyage/team-enrollment/v1\0'
POLICY = 'single-anchor-enrollment-v1'


def request(identity, fresh, recipient, team='team-a', attempt='attempt-a'):
    body = {'version': 1, 'team': team, 'attempt': attempt,
            'identity_key': public(identity), 'fresh_team_key': public(fresh),
            'recipient_identity_key': recipient, 'recognition_basis': 'local-identity-decision-a',
            'teammate': 'alice', 'berth': 'notes', 'purpose': 'commit-signing'}
    return {'identity': sign(identity, REQUEST, body),
            'possession': sign(fresh, POSSESSION, body)}


def respond(req, sibling_identity, sibling_team, anchor, *, recognized_requester, has_authority=True,
            false_projection=False):
    body = req['identity']['payload']
    assert body['recipient_identity_key'] == public(sibling_identity), 'wrong intended sibling'
    assert body['identity_key'] == recognized_requester, 'requester identity not recognized'
    assert req['possession']['payload'] == body
    assert authentic(body['identity_key'], REQUEST, req['identity'])
    assert authentic(body['fresh_team_key'], POSSESSION, req['possession'])
    grant = sign(anchor, GRANT, {
        'team': body['team'], 'teammate': body['teammate'],
        'berth': body['berth'], 'purpose': 'enroll-device',
        'subject': public(sibling_team),
    })
    enrollment = sign(sibling_team, ENROLL, {
        'request_digest': digest(encode(req)), 'team': body['team'],
        'teammate': body['teammate'], 'berth': body['berth'],
        'purpose': body['purpose'], 'subject': body['fresh_team_key'],
    })
    events = [grant, enrollment] if has_authority else [enrollment]
    projected = copy.deepcopy(enrollment['payload'])
    if false_projection:
        projected['berth'] = 'finances'
    raw = encode({'version': 1, 'events': events, 'projection': [projected]})
    binding = sign(sibling_identity, BINDING, {
        'identity_key': public(sibling_identity), 'team_key': public(sibling_team),
        'team': body['team'], 'teammate': body['teammate'],
        'request_digest': digest(encode(req)),
    })
    response = sign(sibling_identity, RESPONSE, {
        'version': 1, 'request': req, 'sibling_identity': public(sibling_identity),
        'sibling_team': public(sibling_team), 'binding': binding,
        'anchor': public(anchor), 'policy': POLICY,
        'frontier': sorted(digest(encode(e)) for e in events),
        'snapshot_digest': digest(raw),
    })
    return response, {digest(raw): raw}


def receive(local_req, response, store, delivery, *, mode, identity_key=None,
            compared=None, anchor_decision=None, identity_basis=None, weak=False):
    assert not store, 'every attempt starts with an empty team store'
    evidence = {'authentication': 'unproved', 'adopted': False}

    def done(result):
        return {'result': result, 'evidence': evidence, 'teams': sorted(store)}

    p = response['payload']
    expected = local_req['identity']['payload']
    if p['request'] != local_req:
        return done('reject: request differs')
    if mode == 'comparison':
        if compared is None and not weak:
            return done('pause: comparison missing')
        if not weak and compared != digest(encode(response)):
            return done('reject: comparison differs')
    elif mode == 'identity-channel':
        if identity_key is None or p['sibling_identity'] != identity_key:
            return done('reject: sibling identity not recognized')
        if identity_basis is None or identity_basis['key'] != identity_key or identity_basis['id'] != expected['recognition_basis']:
            return done('pause: prior identity basis unavailable')
        evidence['identity_basis'] = copy.deepcopy(identity_basis)
    else:
        raise ValueError(mode)
    if not authentic(p['sibling_identity'], RESPONSE, response):
        return done('reject: response signature')
    if not weak and p['sibling_identity'] != expected['recipient_identity_key']:
        return done('reject: wrong intended sibling')
    evidence['authentication'] = 'unproved' if weak else mode
    # This persists before consulting delivery, including on a missing snapshot.
    evidence['commitment'] = copy.deepcopy(response)
    evidence['request'] = copy.deepcopy(local_req)
    if anchor_decision is None:
        return done('pause: anchor policy not adopted')
    expected_adoption = {'actor': 'newcomer operator', 'device': 'newcomer-a',
        'anchor': p['anchor'], 'policy': p['policy'], 'team': expected['team'],
        'exchange': digest(encode(response))}
    if anchor_decision != expected_adoption:
        return done('reject: anchor decision names another view')
    if p['policy'] != POLICY:
        return done('pause: unsupported authority policy')
    evidence['local_anchor_decision'] = copy.deepcopy(anchor_decision)
    binding = p['binding']
    wanted = {'identity_key': p['sibling_identity'], 'team_key': p['sibling_team'],
              'team': expected['team'], 'teammate': expected['teammate'],
              'request_digest': digest(encode(local_req))}
    if binding['payload'] != wanted or not authentic(p['sibling_identity'], BINDING, binding):
        return done('reject: identity-to-team binding')
    raw = delivery.get(p['snapshot_digest'])
    if raw is None:
        return done('pause: pinned snapshot unavailable')
    if digest(raw) != p['snapshot_digest']:
        return done('reject: snapshot digest')
    snapshot = json.loads(raw)
    events = {digest(encode(e)): e for e in snapshot['events']}
    if set(events) != set(p['frontier']):
        return done('pause: selected evidence missing or extra')
    grants = [e for e in events.values() if authentic(p['anchor'], GRANT, e)]
    wanted_grant = {'team': expected['team'], 'teammate': expected['teammate'],
                    'berth': expected['berth'], 'purpose': 'enroll-device',
                    'subject': p['sibling_team']}
    if not any(e['payload'] == wanted_grant for e in grants):
        return done('pause: sibling lacks team enrollment authority')
    wanted_enrollment = {'request_digest': digest(encode(local_req)),
        'team': expected['team'], 'teammate': expected['teammate'],
        'berth': expected['berth'], 'purpose': expected['purpose'],
        'subject': expected['fresh_team_key']}
    if not any(e['payload'] == wanted_enrollment and authentic(p['sibling_team'], ENROLL, e)
               for e in events.values()):
        return done('reject: fresh-key enrollment evidence')
    evidence['reconstructed'] = [wanted_enrollment]
    evidence['delivered_projection'] = snapshot['projection']
    if snapshot['projection'] != evidence['reconstructed']:
        return done('pause: projection differs on evidence')
    evidence['adopted'] = True
    store[expected['team']] = {'snapshot': raw.decode(), 'evidence': copy.deepcopy(evidence)}
    return done('adopted selected team')


def scenarios():
    newcomer, fresh, sibling, sibling_team, anchor, attacker = [
        Ed25519PrivateKey.generate() for _ in range(6)]
    req = request(newcomer, fresh, public(sibling))
    response, delivery = respond(req, sibling, sibling_team, anchor, recognized_requester=public(newcomer))
    output = {}
    prior_identity = {'id': 'local-identity-decision-a', 'key': public(sibling),
        'method': 'retained-delegation', 'comparison': 'matched',
        'missing_proof': ['current key holder is honest']}
    for mode in ('comparison', 'identity-channel'):
        auth = {'compared': digest(encode(response))} if mode == 'comparison' else {
            'identity_key': public(sibling), 'identity_basis': prior_identity}

        def check(name, expected, resp=response, data=delivery, local=req, **opts):
            # The harness supplies a simulated local decision deliberately.
            # receive() has no default adoption and never invents this record.
            adoption = {'actor': 'newcomer operator', 'device': 'newcomer-a',
                'anchor': resp['payload']['anchor'], 'policy': resp['payload']['policy'],
                'team': local['identity']['payload']['team'], 'exchange': digest(encode(resp))}
            verdict = receive(local, resp, {}, data, mode=mode,
                              **(auth | {'anchor_decision': adoption} | opts))
            assert verdict['result'] == expected, (mode, name, verdict)
            assert verdict['teams'] == (['team-a'] if expected == 'adopted selected team' else [])
            output[f'{mode}/{name}'] = verdict
            return verdict

        check('valid', 'adopted selected team')
        for field, value in [('team', 'team-b'), ('attempt', 'attempt-b'),
                             ('fresh_team_key', public(attacker))]:
            altered = copy.deepcopy(req)
            altered['identity']['payload'][field] = value
            check(field + '_replaced', 'reject: request differs', local=altered)
        changed = dict(delivery)
        changed[response['payload']['snapshot_digest']] += b'\n'
        check('replaced_baseline', 'reject: snapshot digest', data=changed)
        absent = check('unavailable_snapshot', 'pause: pinned snapshot unavailable', data={})
        assert absent['evidence']['commitment'] == response
        check('declined_anchor', 'pause: anchor policy not adopted', anchor_decision=None)
        check('wrong_anchor_decision', 'reject: anchor decision names another view', anchor_decision={})
        if mode == 'identity-channel':
            check('missing_prior_identity_basis', 'pause: prior identity basis unavailable', identity_basis=None)
            local_basis = {**prior_identity, 'method': 'local-recognition',
                'missing_proof': ['continuity with any earlier key', 'current key holder is honest']}
            inherited = check('recognition_only_basis_retained', 'adopted selected team', identity_basis=local_basis)
            assert inherited['evidence']['identity_basis'] == local_basis
        unsigned = copy.deepcopy(response)
        unsigned['signature'] = '00' * 64
        expected = 'reject: comparison differs' if mode == 'comparison' else 'reject: response signature'
        check('forged_signature', expected, resp=unsigned)
        for name, kwargs, expected in [
            ('no_team_enrollment', {'has_authority': False}, 'pause: sibling lacks team enrollment authority'),
            ('false_projection', {'false_projection': True}, 'pause: projection differs on evidence'),
        ]:
            resp, data = respond(req, sibling, sibling_team, anchor, recognized_requester=public(newcomer), **kwargs)
            opts = {'compared': digest(encode(resp))} if mode == 'comparison' else {}
            verdict = check(name, expected, resp=resp, data=data, **opts)
            if name == 'false_projection':
                assert verdict['evidence']['reconstructed'][0]['berth'] == 'notes'
                assert verdict['evidence']['delivered_projection'][0]['berth'] == 'finances'
        changed = copy.deepcopy(response['payload'])
        changed['binding']['payload']['team_key'] = public(attacker)
        rebound = sign(sibling, RESPONSE, changed)
        opts = {'compared': digest(encode(rebound))} if mode == 'comparison' else {}
        check('false_identity_team_binding', 'reject: identity-to-team binding', resp=rebound, **opts)
        for field, value in [('team', 'team-b'), ('berth', 'finances'), ('purpose', 'read-data')]:
            snapshot = json.loads(next(iter(delivery.values())))
            grant_body = snapshot['events'][0]['payload']
            grant_body[field] = value
            snapshot['events'][0] = sign(anchor, GRANT, grant_body)
            raw = encode(snapshot)
            body = copy.deepcopy(response['payload'])
            body['snapshot_digest'] = digest(raw)
            body['frontier'] = sorted(digest(encode(e)) for e in snapshot['events'])
            scoped = sign(sibling, RESPONSE, body)
            opts = {'compared': digest(encode(scoped))} if mode == 'comparison' else {}
            check('wrong_grant_' + field, 'pause: sibling lacks team enrollment authority',
                  resp=scoped, data={digest(raw): raw}, **opts)
        # The attacker knows the intercepted public request, not the newcomer
        # private key. It signs every replacement authority record itself.
        fake_body = copy.deepcopy(response['payload'])
        fake_body.update(sibling_identity=public(attacker), sibling_team=public(attacker),
                         anchor=public(attacker))
        fake_body['binding'] = sign(attacker, BINDING, {
            'identity_key': public(attacker), 'team_key': public(attacker),
            'team': 'team-a', 'teammate': 'alice', 'request_digest': digest(encode(req))})
        fake_grant = sign(attacker, GRANT, {
            'team': 'team-a', 'teammate': 'alice', 'berth': 'notes',
            'purpose': 'enroll-device', 'subject': public(attacker)})
        enrollment_body = {'request_digest': digest(encode(req)), 'team': 'team-a',
            'teammate': 'alice', 'berth': 'notes', 'purpose': 'commit-signing',
            'subject': req['identity']['payload']['fresh_team_key']}
        fake_enrollment = sign(attacker, ENROLL, enrollment_body)
        forged_snapshot = {'version': 1, 'events': [fake_grant, fake_enrollment],
                           'projection': [enrollment_body]}
        fake_raw = encode(forged_snapshot)
        fake_body['frontier'] = sorted(digest(encode(e)) for e in forged_snapshot['events'])
        fake_body['snapshot_digest'] = digest(fake_raw)
        substitute = sign(attacker, RESPONSE, fake_body)
        forged_delivery = {digest(fake_raw): fake_raw}
        expected = 'reject: comparison differs' if mode == 'comparison' else 'reject: sibling identity not recognized'
        check('whole_exchange_substitute', expected, resp=substitute, data=forged_delivery)
        if mode == 'comparison':
            check('missing_comparison', 'pause: comparison missing', compared=None)
            check('weakened_self_consistency', 'adopted selected team', resp=substitute,
                  data=forged_delivery, weak=True)
    # A request self-signed by a different identity is not a recognized sibling.
    stranger_request = request(attacker, fresh, public(sibling))
    try:
        respond(stranger_request, sibling, sibling_team, anchor,
                recognized_requester=public(newcomer))
    except AssertionError as error:
        assert str(error) == 'requester identity not recognized'
    else:
        raise AssertionError('unrecognized requester admitted')
    # Independent possession of omitted evidence shows the offered view need not
    # be complete. Neither delivery path can discover an unreferenced grant.
    omitted = sign(anchor, GRANT, {'team': 'team-a', 'subject': public(attacker),
        'teammate': 'mallory', 'berth': 'notes', 'purpose': 'enroll-device'})
    assert digest(encode(omitted)) not in response['payload']['frontier']
    assert authentic(public(anchor), GRANT, omitted)
    # An independent cryptographic check bypasses receive: flipping any one
    # payload field must invalidate the original response signature.
    for field in response['payload']:
        changed = copy.deepcopy(response)
        changed['payload'][field] = {'tampered': True}
        assert not authentic(public(sibling), RESPONSE, changed), field
    return output


if __name__ == '__main__':
    print(json.dumps(scenarios(), indent=2, sort_keys=True))
