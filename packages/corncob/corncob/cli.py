# CornCob

import sys

import corncob.protocol as Corncob

program_title = "CornCob protocol Git remote helper work-a-like"

def main( remote, cmd, dotdotdot ):
    corn = Corncob.Corncob( args.remote )

    if cmd == "clone":
        if len( dotdotdot ) < 1:
            print( f"ERROR: clone requires a URL ({program_title})" )
            return -1
        return corn.clone_from_remote( dotdotdot[ 0 ] )

    result = corn.change_to_root_git_dir()
    if result != 0:
        return result

    print( f"Hello World! {cmd} {corn.remote_name} {dotdotdot}" )

    if cmd == "add":
        if len( dotdotdot ) < 1:
            print( f"ERROR: remote-add requires a URL ({program_title})" )
            return -1
        corn.add_remote( dotdotdot[ 0 ], dotdotdot[ 1: ] )
        return 0
    elif cmd == "remove":
        return corn.remove_remote( dotdotdot )

    
    corn.initialize_existing_remote()
    if corn.url == None:
        return -1

    corn.remote = Corncob.CornCobRemote.init( corn.url )

    if cmd == "push":
        return corn.push_to_remote( dotdotdot )
    elif cmd == "fetch":
        return corn.fetch_from_remote( dotdotdot )
    elif cmd == "merge":
        return corn.merge_from_remote( dotdotdot )
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
    except Corncob.GitCmdFailed as e:
        print( e )
        exit_code = e.exit_code
    sys.exit( exit_code )
