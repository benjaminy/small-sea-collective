# Configurable Team Policies

Status: deferred design direction, not a prerequisite for compelling demos.

Small Sea should eventually let teams and device owners choose policy within a supported set of options.
The bootstrap research exposes choices about identity recovery, admission, history acceptance, and authority at the time of an action.
Those choices need not become universal rules in the Constitution core.
For now, a demo can use one explicit policy for the behavior it exercises without implementing policy selection or a general policy engine.

## What stays fixed

The [Constitution core](team-constitution.md#extension-boundary) verifies evidence; extensions and local policy decide its effect.
A policy choice cannot turn an invalid signature into a valid one, restore missing evidence, or make human recognition prove historical delegation.
Accepting particular past work does not by itself authorize future work, and recognizing an identity does not by itself grant team authority.
The Manager owns management decisions; the Hub enforces its local access boundary.

Policy scope matters.
A team admission policy describes the evidence required to admit someone, while a device owner's local policy may determine whether to integrate particular history.
Publishing a team policy does not force every participant to recognize it or select the same Constitution view.

## Choices to revisit

These are examples of future policy choices, not a promised configuration schema:

- What evidence or human recognition permits identity recovery, and whether later team enrollment requires an additional independent endorsement.
- Who may admit a teammate or enroll a device, with which thresholds and human comparisons.
- How a device accepts or revisits specific work from a removed author without granting that author future authority.
- Which actions may use a previously selected authority view and which require another check before acting, especially when releasing fresh encryption keys.

Each extension can define the choices it supports and explain their consequences.
We defer a general configuration language, policy editor, selectable profiles, and machinery for authorizing, distributing, changing, or reconciling policies.
Who may change a policy, how participants adopt that change, and which policy applies to earlier work remain open design questions.

## Demo scope

Choose and document one concrete policy for each demonstrated flow.
State what evidence it uses and what happens when the flow lacks that evidence; an unsupported case may pause for a human or remain outside the demo.
Implement the checks needed to support the demo's actual claims and protect developer data and credentials.
Deferring configurability does not establish that today's runtime already performs those checks.

The [bootstrap transcript](bootstrap-trust.md) supplies proposed flows and evidence requirements, not a requirement to expose every choice as a setting.
Its identity-signed team exchange can remain the proposed default while broader ceremony policy is deferred.
The #263 and #266 research preserves questions about removed-author history and scoped authority; it does not make a complete policy framework a dependency of every demo.
Revisit a deferred choice when a concrete demo needs it, when two supported workflows need different rules, or when a fixed rule would otherwise leak into the core protocol.
