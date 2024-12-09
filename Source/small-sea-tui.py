#

import sys
import os
import sqlite3
import secrets

import requests

program_title = "Small Sea Collective TUI"

class SmallSea:

    def __init__( self, root_dir=None ):
        if None == root_dir:
            app_name = "CooperativeClique"
            app_author = "MyCompany"

            root_dir = platformdirs.user_data_dir( app_name, app_author )
        self.root_dir = root_dir
        os.makedirs( self.root_dir, exist_ok=True )

        print( f"Root Cooperative Clique Directory: {self.root_dir}" )


    def create_new_user( self, nickname, primary_cloud_location ):
        """ This makes a new, globally unique identity. In normal day to day
        operations, it should be an uncommon command
        """
        user_identity = CooperativeClique.token_hex( 8 )
        path = "Sdfsdf"
        os.makedirs( path_tmp, exist_ok=True )
        # set up idendetity.db
        # sync
        # add nickname to local db


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


    def main( self, cmd, more_args ):

        if "new_user" == cmd:
            print( f"dsfsf {type(more_args)} {more_args}" )
        return 0


    def token_hex( num_bytes ):
        return "".join( f"{b:02x}" for b in secrets.token_bytes( num_bytes ) )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser( program_title )
    parser.add_argument( "command", type=str )
    parser.add_argument( "--local-hub-port", type=int, default=11347 b)
    parser.add_argument( "more_args", nargs=argparse.REMAINDER )

    args = parser.parse_args()

    sea = SmallSea( hub_port=args.local_hub_port )
    exit_code = sea.main( args.command, args.more_args )
    sys.exit( exit_code )
