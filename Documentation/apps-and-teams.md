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

There might be some intuitive notion of a hierarchy of leaders, core teammates or owners, but the built-in integration policy in Small Sea is very simple.
The current schema records either `read-write` or `read-only` for each teammate in each berth.
These are protocol expectations and local policy, not centrally enforced entitlements.
One common arrangement is:

1. Peers automatically integrate some teammates' publications for _Team_-SmallSeaCollectiveCore; these teammates are the _admins_ or Core integrators for the team, and they can create invitations for new teammates and propose teammate removals.
2. Other app berths commonly divide the group into contributors whose ordinary publications are automatically integrated and observers whose publications are not.

If people want more fine-grained integration policy, this might be achievable with linked teams.
Or something like that.
I'm not especially interested in this topic.

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
