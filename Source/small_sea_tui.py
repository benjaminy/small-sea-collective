#

import sys
import os
import secrets

import small_sea_client_lib as SmallSeaLib

program_title = "Small Sea Collective TUI"

class SmallSeaTui:

    ILLEGAL_NICKNAME = "SmallSeaIllegalNickname"

    def __init__( self, hub_port=None ):
        self.hub_port = hub_port

        print( f"Root Cooperative Clique Directory: {self.hub_port}" )


    def create_new_user( self, nickname, primary_cloud_location=None ):
        """ This makes a new, globally unique identity. In normal day to day
        operations, it should be an uncommon command
        """
        small_sea = SmallSeaLib.SmallSeaClient()
        small_sea.new_identity( nickname )


    def add_new_cloud( self ):
        pass


    def connect_to_existing_cloud( self, url ):
        pass


    def import_user( self, primary_cloud_location ):
        pass


    def create_new_team( self ):
        pass


    def invite_user_to_team( self ):
        pass


    def remove_user_from_team( self ):
        pass


    def main( self, cmd, args ):

        if "new_user" == cmd:
            if SmallSeaTui.ILLEGAL_NICKNAME == args.nickname:
                print( "Pick a better nick" )
                return
            self.create_new_user( args.nickname )
        return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser( program_title )
    parser.add_argument( "command", type=str )
    parser.add_argument( "--local-hub-port", type=int, default=11437 )
    parser.add_argument( "--nickname", type=str, default=SmallSeaTui.ILLEGAL_NICKNAME )
    parser.add_argument( "more_args", nargs=argparse.REMAINDER )

    args = parser.parse_args()

    sea = SmallSeaTui( hub_port=args.local_hub_port )
    exit_code = sea.main( args.command, args )
    sys.exit( exit_code )
