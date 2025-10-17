# Top Matter

import requests
from datetime import datetime

class SmallSeaHubUnavailable(Exception):
    def __init__(self):
        pass

class SmallSeaClient:
    """
    """

    PORT_DEFAULT = 11437

    def __init__(
            self,
            port=PORT_DEFAULT):
        self.port = port


    def create_new_participant( self, nickname ):
        try:
            response = self._send_post( "participants", { "nickname" : nickname } )
            print( f"NEW ID {response.json()}" )
        except requests.exceptions.ConnectionError as exn:
            raise SmallSeaHubUnavailable()

    def open_session(
            self,
            nickname,
            app,
            team,
            client ):
        data = {
            "participant": nickname,
            "app": app,
            "team": team,
            "client": client,
        }
        response = self._send_post( f"/sessions", data )
        print( f"NEW SESION {response.json()}" )
        return response.json()

    def add_cloud_location( self, session, backend, url ):
        data = { "session" : session, "backend" : backend, "url" : url }
        response = self._send_post( f"cloud_locations", data )

    def create_new_team( self, session, team_name ):
        data = { "session" : session, "team_name" : team_name }
        response = self._send_post( "teams", data )
        print( f"NEW TEAM {response.json()}" )
        return response

    def make_device_link_invitation(self, session):
        data = {"session" : session}
        response = self._send_post("device-link-invitations", data)
        print(f"DEVICE LINK INVITE {response}")
        return response

    def put_blob(
            self,
            session,
            path,
            blob,
            if_match=None,
            if_none_match=None ):
        data = { "session": sessiom, "path": path, "blob": blob }
        if if_match is not None:
            data["if_match"] = if_match
        if if_none_match is not None:
            data["if_none_match"] = if_none_match
        response = self._send_post("blobs", data)

    def get_blob( self, session, path ):
        pass

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
