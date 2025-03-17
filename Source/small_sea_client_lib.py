# Top Matter

import requests

class SmallSeaClient:
    """
    """

    PORT_DEFAULT = 11437
    
    def __init__( self ):
        self.port = SmallSeaClient.PORT_DEFAULT


    def new_identity( self, nickname ):
        self.send_post( "synthesize_new_user", { "nickname" : nickname } )
        

    def send_get( self, path ):
        scheme = "http"
        host = "127.0.0.1"
        url = f"{scheme}://{host}/{path}"
            
    def send_post( self, path, json_data ):
        scheme = "http"
        # host = "127.0.0.1"
        host = "localhost"
        url = f"{scheme}://{host}:{self.port}/{path}"
        print( f"POST URL {url}" )
        response = requests.post( url, json=json_data )
        print( response.json() )
