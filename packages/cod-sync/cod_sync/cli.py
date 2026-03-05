# Cod Sync

import sys

import cod_sync.protocol as CodSync

program_title = "Cod Sync protocol Git remote helper work-a-like"

def main( remote, cmd, dotdotdot ):
    cod = CodSync.CodSync( args.remote )

    if cmd == "clone":
        if len( dotdotdot ) < 1:
            print( f"ERROR: clone requires a URL ({program_title})" )
            return -1
        return cod.clone_from_remote( dotdotdot[ 0 ] )

    result = cod.change_to_root_git_dir()
    if result != 0:
        return result

    print( f"Hello World! {cmd} {cod.remote_name} {dotdotdot}" )

    if cmd == "add":
        if len( dotdotdot ) < 1:
            print( f"ERROR: remote-add requires a URL ({program_title})" )
            return -1
        cod.add_remote( dotdotdot[ 0 ], dotdotdot[ 1: ] )
        return 0
    elif cmd == "remove":
        return cod.remove_remote( dotdotdot )


    cod.initialize_existing_remote()
    if cod.url == None:
        return -1

    cod.remote = CodSync.CodSyncRemote.init( cod.url )

    if cmd == "push":
        return cod.push_to_remote( dotdotdot )
    elif cmd == "fetch":
        return cod.fetch_from_remote( dotdotdot )
    elif cmd == "merge":
        return cod.merge_from_remote( dotdotdot )
    else:
        print( f"ERROR: Unknown command '{cmd}' ({program_title})" )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser( program_title )
    parser.add_argument( "command", type=str )
    parser.add_argument( "remote", type=str )
    parser.add_argument( "branches", nargs=argparse.REMAINDER )

    args = parser.parse_args()

    try:
        exit_code = main( args.remote, args.command, args.branches )
    except CodSync.GitCmdFailed as e:
        print( e )
        exit_code = e.exit_code
    sys.exit( exit_code )
