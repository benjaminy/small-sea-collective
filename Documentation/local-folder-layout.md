
This all in the SmallSeaCollective AppData or UserData folder

- small-sea-collective-local.db
- Users/
  - Alice/
    - Private/ *Sync'd*
      - identity.db 
    - Teams/
      - NoteToSelf/ *Sync'd*
        - team-stuff.db
      - School/ *Sync'd*
        - team-stuff.db
  - Bob/
    - Private/ *Sync'd*
      - identity.db 
    - Teams/
      - NoteToSelf/ *Sync'd*
        - team-stuff.db
      - Home/ *Sync'd*
      - Work/ *Sync'd*

This is in some custom app's AppData or UserData folder (in this example: ManyHands)

- ManyHands/
  - blah
  - whatever app stuff
  - SmallSeaCollective/
    - Alice/
      - Meta/ *Sync'd*
      - Teams/
        - Home/ *Sync'd*
        - Work/ *Sync'd*

