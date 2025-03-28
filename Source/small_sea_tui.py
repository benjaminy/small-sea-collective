#

import sys
import os
import secrets

import small_sea_client_lib as SmallSeaLib

program_title = "Small Sea Collective TUI"

class SmallSeaTui:

    ILLEGAL_NAME = "SmallSeaIllegalNameNeverUseMe"

    def __init__( self, hub_port=None ):
        self.hub_port = hub_port

        print( f"Root Cooperative Clique Directory: {self.hub_port}" )


    def create_new_user( self, nickname, primary_cloud_location=None ):
        """ This makes a new, globally unique identity. In normal day to day
        operations, it should be an uncommon command
        """
        small_sea = SmallSeaLib.SmallSeaClient()
        small_sea.new_identity( nickname )

    def start_user_session( self, nickname ):
        small_sea = SmallSeaLib.SmallSeaClient()
        session = small_sea.start_session_user( nickname )
        print( f"OMG {session}" )


    def add_new_cloud( self, nickname, url ):
        small_sea = SmallSeaLib.SmallSeaClient()
        session = small_sea.start_session_user( nickname )
        small_sea.add_cloud_location( session, url )


    def connect_to_existing_cloud( self, url ):
        pass


    def import_user( self, primary_cloud_location ):
        pass


    def create_new_team( self, nick, team_name ):
        small_sea = SmallSeaLib.SmallSeaClient()
        session = small_sea.start_session_user( nickname )
        small_sea.create_new_team( session, team_name )


    def invite_user_to_team( self ):
        pass


    def remove_user_from_team( self ):
        pass


    def main( self, cmd, args ):

        if "new_user" == cmd:
            if SmallSeaTui.ILLEGAL_NAME == args.nickname:
                print( "Pick a better nick" )
                return
            self.create_new_user( args.nickname )
        elif "start_user_session" == cmd:
            if SmallSeaTui.ILLEGAL_NAME == args.nickname:
                print( "WHO ARE YOU?" )
                return
            self.start_user_session( args.nickname )
        elif "new_team" == cmd:
            if SmallSeaTui.ILLEGAL_NAME == args.nickname:
                print( "WHO ARE YOU?" )
                return
            self.create_new_team( args.nickname, args.team_name )
        else:
            print( f"Unknown command '{cmd}'" )
        return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser( program_title )
    parser.add_argument( "command", type=str )
    parser.add_argument( "--local-hub-port", type=int, default=11437 )
    parser.add_argument( "--nickname", type=str, default=SmallSeaTui.ILLEGAL_NAME )
    parser.add_argument( "--team_name", type=str, default=SmallSeaTui.ILLEGAL_NAME )
    parser.add_argument( "more_args", nargs=argparse.REMAINDER )

    args = parser.parse_args()

    sea = SmallSeaTui( hub_port=args.local_hub_port )
    exit_code = sea.main( args.command, args )
    sys.exit( exit_code )
