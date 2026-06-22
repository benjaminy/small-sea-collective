# Apps and Teams

The primary organizing concepts in Small Sea are **teams** and **apps**.
Each _team_-_app_ combination defines a _berth_ that things like storage space can be allocated to.

There is one special built-in team (NoteToSelf) and one special built-in app (SmallSeaCollectiveCore).
The berth at the intersection of these two (NoteToSelf-SmallSeaCollectiveCore) is where special stuff like information about devices recognized as belonging to the participant is kept.
This particular berth comes up often enough that it has a name: the **Core berth**.
The _Team_-SmallSeaCollectiveCore berths are where team membership, invitations and associated metadata is kept.
The NoteToSelf-_App_ berths are where apps can keep any user-private customization.

Teams in Small Sea are similar to any other groupware framework, but there is at least one important difference.
The sharing in Small Sea is entirely distributed and voluntary.
There is no team server that grants or denies writes.
Each teammate publishes their own history, and each participant decides which histories to watch, fetch, and integrate into their local clones.

There might be some intuitive notion of a hierarchy of leaders, core teammates, or owners, and a medium-sized team really does have different levels of engagement and accountability.
Small Sea reflects that today with just two built-in per-berth integration modes, leaving richer team-configurable roles as a possible future direction.
Peers monitor and integrate every valid ordinary publication from an **automatic** teammate by default.
Peers do not monitor ordinary publications from a **proposal-only** teammate, who instead submits signed proposals for endorsement by at least one automatic integrator.

Both modes describe full teammates who may read, author, and sign data.
The distinction is integration behavior, not permission to produce a change.
The current schema's `read-write` and `read-only` values approximate `automatic` and `proposal-only` respectively.

An _admin_ is useful shorthand for an automatic integrator on _Team_-SmallSeaCollectiveCore.
Core records significant teammate facts as signed append-only history, so past membership and integrator standing remain inspectable even after the current view changes.

<table>
<tr>
<td></td>
<th colspan="100%" style="text-align:left;">Apps →</th>
</tr>
<tr>
<th>Teams ↓</th>
<th style="background-color:rgba(255,255,0,0.2)">SmallSeaCollectiveCore</th>
<th>FileShare</th>
<th>Notes</th>
<th>ManyHands</th>
</tr>
<tr>
<th style="background-color:rgba(0,0,255,0.2)">NoteToSelf</th>
<td style="background-color:rgba(0,255,0,0.2)">devices,<br>personal keys, etc</td>
<td style="background-color:rgba(0,0,255,0.2)">app config</td>
<td style="background-color:rgba(0,0,255,0.2)">app config</td>
<td style="background-color:rgba(0,0,255,0.2)">app config</td>
</tr>
<tr>
<th>JugBand</th>
<td style="background-color:rgba(255,255,0,0.2)">membership,<br>invitations, etc</td>
<td></td>
<td></td>
<td></td>
</tr>
<tr>
<th>Family</th>
<td style="background-color:rgba(255,255,0,0.2)">membership,<br>invitations, etc</td>
<td></td>
<td></td>
<td></td>
</tr>
<tr>
<th>GameGroup</th>
<td style="background-color:rgba(255,255,0,0.2)">membership,<br>invitations, etc</td>
<td></td>
<td></td>
<td></td>
</tr>
</table>

### Apps vs Clients

An _app_ in Small Sea jargon is distinct from _client_ software.
An app is a way to organize resources like storage, connections and notifications.
A client is actual software that can access an app's resources.
Typically a client will only access one apps's resources, but there may be several different clients that access a single app (for example a command line client and a GUI client).

Local client access control for apps is different and real.
It would _not_ be great if clients could easily peek into any app's data or "impersonate" any app.
When some client software wants to access any resource associated with an app/berth it has to start a session with the Small Sea Hub.
The request for a new session will prompt a user with a two-step process that involves the Hub generating a PIN that the user has to input to the client.
This should help keep clients out of each others' business.
