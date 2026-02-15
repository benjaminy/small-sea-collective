# Apps and Teams

The primary organizing concepts in Small Sea are **teams** and **apps**.
Each _team_-_app_ combination defines a _station_ that things like storage space can be allocated to.

There is one special built-in team (NoteToSelf) and one special built-in app (SmallSeaCore).
The station at the intersection of these two (NoteToSelf-SmallSeaCore) is where special stuff like information about a users' authorized devices is kept.
The _Team_-SmallSeaCore stations are where team membership, invitations and associated metadata is kept.
The NoteToSelf-_App_ stations are where apps can keep any user-private customization.

Teams in Small Sea are similar to any other groupware framework, but there is at least one important difference.
The sharing in Small Sea is entirely distributed and voluntary.
There might be some intuitive notion of a heirarchy of leaders, core members or owners, but the built-in permission structure in Small Sea is very simple.
Each participant has either full or read-only permissions in each station.
One common arrangement is:

1. Some participants have read-write permissions for _Team_-SmallSeaCore; these are the _admins_ for the team; they can create invitations for new members and propose member removals.
2. All other apps have the same permissions for participants, dividing the group into authors and observers.

If people want more fine-grained permissions systems, this might be achievable with linked teams.
Or something like that.
I'm not especially interested in this topic.

<table>
<tr>
<td></td>
<th colspan="100%" style="text-align:left;">Apps →</th>
</tr>
<tr>
<th>Teams ↓</th>
<th style="background-color:rgba(255,255,0,0.2)">SmallSeaCore</th>
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

Access control for apps is interesting.
It would _not_ be great if clients could easily peek into any app's data or "impersonate" any app.
When some client software wants to access any resource associated with an app/zone it has to start a session with the Small Sea Hub.
The request for a new session will prompt a user with a two-step process that involves the Hub generating a PIN that the user has to input to the client.
This should help keep clients out of each others' business.
