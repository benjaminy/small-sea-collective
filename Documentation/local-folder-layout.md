
This all in the SmallSeaCollective AppData or UserData folder

- Local/
  - small-sea-collective.db
- Participants/
  - Alice/
    - NoteToSelf/
      - Local/
        - most-recent-link.yaml
      - Sync/
        - team-etc.db
    - School/
      - Local/
      - Sync/
        -team-etc.db
  - Bob/
    - NoteToSelf/
      - Local/
      - Sync/
        - team-etc.db
    - Home/
      - Local/
      - Sync/
        - team-etc.db
    - Work/
      - Local/
      - Sync/
        - team-etc.db

This is in some custom app's AppData or UserData folder (in this example: ManyHands)

- ManyHands/
  - blah
  - whatever app stuff
  - SmallSeaCollective/
    - Alice/
      - NoteToSelf/
        - Local/
	- Sync/
          - general-syncd-app-stuff
      - Home/ *Sync'd*
      - Work/ *Sync'd*
    - Bob/

- SmallSeaFileArchive
  - Local/
    - ...
  - SmallSeaCollective/
    - Alice/
      - NoteToSelf/
        - Local/
	- Sync/
    - Bob/