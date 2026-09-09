# Design record — sibling berth placement disagreement (#238)

The implemented rules live in the Manager and Hub specifications.
This record preserves the reasons for the chosen scope and the assumptions behind the experiments that preceded it.

## A readable location does not have to be the globally winning location

The early single-route experiments asked how a reader could recognize one settled placement despite delayed signatures, concurrent siblings, incomplete ancestry and replay.
That question made public predecessor links and retirement or resolution records appear necessary.
The models exposed genuine failures under that assumption, but their proposed machinery answered a stronger question than the final behavior needs.

The accepted path lets a device preserve and inspect known alternatives while ordinary operations for its disputed berth wait for a human decision.
Inspection does not require proving which location superseded every other location or that all siblings have accepted one answer.
That removes the reason to publish a selection DAG solely for repair, along with its disclosure cost and orphan-record semantics.
The historical models remain evidence about the assumptions they tested, not obligations inherited by this implementation.

## Waiting has an identifiable owner and cost

A person may leave their own device paused indefinitely.
The useful guarantee is that the question stays visible, the alternatives remain inspectable, and a choice of either existing location works without losing unrelated work.
Automatic convergence, failover and agreement by disconnected siblings are not required to make that useful.

The unique allocation index prevented even unrelated NoteToSelf rows from being adopted when siblings chose different locations.
Allowing competing rows during disagreement lets NoteToSelf carry both the evidence and the later resolution.
Deleting losing live rows makes an explicit choice effective through ordinary shared-state integration; retained device-local evidence keeps that deletion from deciding for an already-paused sibling.
A separate public resolution protocol would duplicate coordination that the chosen scope can express through existing state.

## Inspection breaks a bootstrap circle without authorizing writes

Requiring a locally held storage announcement before inspecting a sibling's candidate created a circle: the announcement could be inside the Core chain at the location being investigated.
A retained candidate, the Manager-only inspection boundary and local account credentials can authorize that read without claiming that the device should publish there.
Announcement status therefore remains evidence in the report rather than an inspection gate.
This does not establish publication authorship or replace the unresolved device-author verification work tracked separately.

## Placement and content remain separate decisions

Choosing a sibling's location can uncover a divergent Core chain.
The placement decision identifies where future publication should go; it is not consent to discard or integrate that chain.
The publication protocol preserves the competing head and reports the ordinary integration requirement.
The connected scenarios use the installed SQLite Git merge driver to reach a publishable head before testing interrupted publication and retry.
They do not imply that a new Manager Core-integration interface exists.

## Persistence has a boundary

Restart and replay preserve a decision while its records remain on disk.
Restoring an older snapshot can erase the only record that this device paused or decided.
Competing live rows still cause the Hub to refuse ambiguity and the Manager to project a pause again, but a sole surviving row and old Git history cannot establish a lost human decision.
A snapshot that retained the pause can conversely restore it after a later resolution.
The branch records these limits rather than inventing a recovery subsystem for research installations.
