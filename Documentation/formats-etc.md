
- `synthesize_new_user`

Git remote stuff:

smallsea://user/alice
smallsea://team/alice/friends
smallsea://app/alice/supernotes
smallsea://app-team/alice/supernotes/friends


            #         - - uid for this link
            #           - uid for prev link -or- "initial-snapshot"
            #           - uid for link before that, etc
            #         - *** branches ***
            #         - - - random id for latest bundle
            #             - - branch name
            #               - sha before
            #             - - branch name
            #               - sha before
            #           - - random id for prev bundle
            #             - - branch name
            #               - sha before
            #             - - branch name
            #               - sha before
            #           - - random id for bundle before that, etc
            #         - { k/v s }
                   

# Class Diagram: Software Architecture

We'll create a visual representation of the main software components and their relationships.

## Classes

1. **Cod Sync**
   - Manages remote operations for different types of remotes (Google Drive, etc)
   - Methods:
     - `add_remote()`
     - `remove_remote()`
     - `initialize_existing_remote()`
     - `push_to_remote()`
     - `build_link_blob()`
     - `clone_from_remote()`
     - `fetch_from_remote()`
     - `merge_from_remote()`
     - `get_branches()`
     - `token_hex()`
     - `change_to_root_git_dir()`
     - `bundle_tmp()`
     - `gitCmd()`

2. **GitCmdFailed**
   - Exception class for handling errors in Git commands

3. **CodSyncRemote**
   - Abstract base class for different types of remotes
   - Methods:
     - `init()`
     - `read_link_blob()`
     - `upload_latest_link()`
     - `get_link()`
     - `get_latest_link()`
     - `download_bundle()`

4. **SmallSeaRemote**
   - Implements remote operations for Small Sea
   - Inherits from CodSyncRemote

5. **LocalFolderRemote**
   - Implements local folder remote operations
   - Inherits from CodSyncRemote

6. **SmallSea**
   - Main application class that interacts with Cod Sync
   - Methods:
     - `token_hex()`
     - Interaction with Cod Sync for remote operations

## Relationships

- **Cod Sync** is used by **SmallSea**
- **CodSyncRemote** is extended by both **SmallSeaRemote** and **LocalFolderRemote**

The UML diagram will visually represent these relationships with arrows showing inheritance and method calls.

### Class Diagram (Mermaid Syntax)

```mermaid
classDiagram

    class Cod Sync {
        +String remote_name
        +method add_remote(url, dotdotdot)
        +method remove_remote(dotdotdot)
        +method initialize_existing_remote()
        +method push_to_remote(branches)
        +method build_link_blob(new_link_uid, prev_link_uid, bundle_uid, prerequisites)
        +method clone_from_remote(url)
        +method fetch_from_remote(branches)
        +method fetch_chain(link, branches, doing_clone)
        +method merge_from_remote(branches)
        +method get_branches()
        +method get_branch_head_sha(branch)
        +method token_hex(num_bytes)
        +method change_to_root_git_dir()
        +method bundle_tmp()
        +method gitCmd(git_params, raise_on_error)
    }

    class GitCmdFailed {
        +String message
        +method __init__(message)
    }

    abstract class CodSyncRemote {
        +static method init(url)
        +method read_link_blob(yaml_strm)
        +method upload_latest_link(link_uid, blob, bundle_uid, local_bundle_path)
        +method get_link(uid)
        +method get_latest_link()
        +method download_bundle(bundle_uid, local_bundle_path)
    }

    class SmallSeaRemote extends CodSyncRemote {
        +method upload_latest_link(...)
        +method get_link(...)
        +method get_latest_link()
        +method download_bundle(...)
    }

    class LocalFolderRemote extends CodSyncRemote {
        #local folder operations
    }

    class SmallSea {
        +String root_dir
        +method __init__(root_dir)
        +method main(cmd, more_args)
        +method token_hex(num_bytes)
        +method interact_with_cod_sync()
    }

    Cod Sync --> SmallSea: Uses "API"
    CodSyncRemote --> SmallSeaRemote: Extends
    CodSyncRemote --> LocalFolderRemote: Extends
