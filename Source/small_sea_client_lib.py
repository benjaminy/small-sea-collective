# Top Matter

import requests
from datetime import datetime

class SmallSeaClient:
    """
    """

    PORT_DEFAULT = 11437
    
    def __init__( self ):
        self.port = SmallSeaClient.PORT_DEFAULT


    def new_identity( self, nickname ):
        response = self._send_post( "synthesize_new_user", { "nickname" : nickname } )
        print( f"NEW ID {response.json()}" )

    def start_session_user( self, nickname ):
        response = self._send_get( f"/session/user/{nickname}" )
        print( f"NEW SESION {response.json()}" )
        return response.json()

    def add_cloud_location( self, url ):
        data = { "session" : session, "team_name" : team_name }
        response = self._send_post( f"add_cloud_location", data )

    def new_team( self, session, team_name ):
        data = { "session" : session, "team_name" : team_name }
        response = self._send_post( "synthesize_new_team", data )
        print( f"NEW TEAM {response.json()}" )

    def _send_get( self, path ):
        scheme = "http"
        host = "127.0.0.1"
        # host = "localhost"
        url = f"{scheme}://{host}:{self.port}/{path}"
        before = datetime.now()
        response = requests.get( url )
        after = datetime.now()
        print( f"get took {after - before}" )
        return response
            
    def _send_post( self, path, json_data ):
        scheme = "http"
        host = "127.0.0.1"
        url = f"{scheme}://{host}:{self.port}/{path}"
        return requests.post( url, json=json_data )
