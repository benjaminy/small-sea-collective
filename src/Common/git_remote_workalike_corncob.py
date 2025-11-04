# CornCob

import os
import sys
import subprocess
import secrets
import yaml
import pathlib
import shutil
import tempfile
import requests

program_title = "CornCob protocol Git remote helper work-a-like"

def gitCmd( git_params, raise_on_error=True ):
    git_cmd = [ "git" ] + git_params
    result = subprocess.run( git_cmd, capture_output=True, text=True )
    if 0 != result.returncode:
        exn = GitCmdFailed( git_params, result.returncode, result.stdout, result.stderr )
        if raise_on_error:
            raise exn
        else:
            print( exn )
    return result

class Corncob:

    def __init__( self, remote_name ):
        self.remote_name = remote_name
        self.url = None


    def add_remote( self, url, dotdotdot ):
        """Add a CornCob remote

        :param remote: The nickname for the remote
        :param url: The URL for the remote. (This should not include 'corncob:')

        Currently the only supported URL schema is file://
        In the fullness of time the idea is to support googledrive: , etc

        This function adds 2 remotes to the underlying repo:
        - One with the actual remote URL
          - This one is never directly `git fetch`d or `git push`d or whatever
        - One with a temp bundle name
          - When pulling from a remote, the bundle is copied here and `git fetch`d
        """

        self.gitCmd( [ "remote", "add", self.remote_name, f"corncob:{url}" ] )
        print( f"Added remote '{self.remote_name}' ({url})" )

        [ bundle_remote, path ] = self.bundle_tmp()
        self.gitCmd( [ "remote", "add", bundle_remote, f"{path}/fetch.bundle" ] )
        print( f"Added remote '{bundle_remote}' ({path})" )


    def remove_remote( self, dotdotdot ):
        """Remove a CornCob remote
        """

        result1 = self.gitCmd( [ "remote", "remove", self.remote_name ], False )
        if 0 == result1.returncode:
            print( f"Removed remote '{self.remote_name}'" )

        [ bundle_remote, _ ] = self.bundle_tmp()
        result2 = self.gitCmd( [ "remote", "remove", bundle_remote ] )
        if 0 == result2.returncode:
            print( f"Removed remote '{bundle_remote}'" )

        if 0 != result1.returncode:
            return result1.returncode

        if 0 != result2.returncode:
            return result2.returncode

        return 0


    def initialize_existing_remote( self ):
        """ git remote get-url `remote_name`
        with some error checking. Plus strip the 'corncob:' prefix,
        """
        self.url = None
        result = self.gitCmd( [ "remote", "get-url", self.remote_name ], False )
        if 0 != result.returncode:
            return

        remote_url = result.stdout.strip()

        if not remote_url.startswith( "corncob:" ):
            print( f"ERROR: Wrong remote protocol '{remote_url}' ({program_title})" )
            return

        # Strip 'corncob:'
        self.url = remote_url[ 8: ]


    def push_to_remote( self, branches ):
        print( f"PUSH {self.remote_name} {self.url} '{branches}'" )

        bundle_uid = Corncob.token_hex( 8 )
        [ _, path_tmp ] = self.bundle_tmp()
        os.makedirs( path_tmp, exist_ok=True )
        bundle_path_tmp = f"{path_tmp}/B-{bundle_uid}.bundle"

        latest_link = self.remote.get_latest_link()
        if None == latest_link:
            link_uid = "initial-snapshot"
            link_uid_prev = "initial-snapshot"
            prerequisites = { "main": "initial-snapshot" }
            bundle_spec = "main"
            #     return 0

        else:
            [ link_ids, branches, bundles, supp_data ] = latest_link
            link_uid = Corncob.token_hex( 8 )
            link_uid_prev = link_ids[ 0 ]
            assert( 1 == len( branches ) )
            assert( 0 < len( link_ids ) )
            branch = branches[ 0 ]
            assert( "main" == branch[ 0 ] )
            prerequisites = { "main": branch[ 1 ] }
            tag = f"corncob_temp_tag_{'main'}"
            self.gitCmd( [ "tag", tag, branch[ 1 ] ] )
            bundle_spec = f"{tag}..main"

        self.gitCmd( [ "bundle", "create", bundle_path_tmp, bundle_spec ] )

        if None != latest_link:
            self.gitCmd( [ "tag", "-d", tag ] )

        blob = self.build_link_blob( link_uid, link_uid_prev, bundle_uid, prerequisites )
        print( f"Pushing to Corncob clone {link_uid} '{bundle_path_tmp}' {blob}" )
        return self.remote.upload_latest_link( link_uid, blob, bundle_uid, bundle_path_tmp )

    def build_link_blob( self, new_link_uid, prev_link_uid, bundle_uid, prerequisites ):
        link_ids = [ new_link_uid, prev_link_uid ]
        branch_names = self.get_branches()
        branches = []
        for branch in branch_names:
            branches.append( [ branch, self.get_branch_head_sha( branch ) ] )
        print( f"BRANCHES {branches}" )
        bundles = [ [ bundle_uid, [ "main", prerequisites[ "main" ] ] ] ]
        supplement = {}
        return [ link_ids, branches, bundles, supplement ]


    def clone_from_remote( self, url ):
        print( f"CLONE {self.remote_name} {url}" )

        git_cmd = [ "git", "rev-parse", "--show-toplevel" ]
        result = subprocess.run( git_cmd, capture_output=True, text=True )
        if 0 == result.returncode:
            print( f"ERROR. Trying to clone, but already in a repo '{os.getcwd()}' '{result.stdout.strip()}' ({program_title})" )
            return -1

        self.remote = CornCobRemote.init( url )
        latest_link = self.remote.get_latest_link()
        if latest_link == None:
            print( f"CLONE SADNESS" )
            return -1
        
        [ link_ids, branches, bundles, supp_data ] = latest_link
        if link_ids[ 0 ] != "initial-snapshot":
            print( f"CLONE MORE THAN INIT {link_ids[ 0 ]}" )
            return -1

        if len( bundles ) != 1:
            print( f"CLONE BS {bundles}" )
            return -1

        bundle = bundles[ 0 ]
        bundle_uid = bundle[ 0 ]

        with tempfile.TemporaryDirectory() as bundle_temp_dir:
            bundle_path = f"{bundle_temp_dir}/clone.bundle"
            self.remote.download_bundle( bundle_uid, bundle_path )
            self.gitCmd( [ "clone", bundle_path, "." ] )

        self.gitCmd( [ "checkout", "main" ] )

        self.add_remote( url, [] )
        print( f"CLONE WORKED!!!" )
        return 0
        

    def fetch_from_remote( self, branches ):
        print( f"FETCH {self.remote_name} {self.url} {branches}" )
        latest_link = self.remote.get_latest_link()

        if latest_link == None:
            print( f"ERROR: Failed to fetch latest link '{corncob_url}' ({program_title})" )
            return -1

        return self.fetch_chain( latest_link, branches, False )

    def fetch_chain( self, link, branches, doing_clone ):
        [ link_ids, branches, bundles, supp_data ] = link

        if len( bundles ) != 1:
            print( f"FETCH BS {bundles}" )
            return -1

        bundle = bundles[ 0 ]
        bundle_uid = bundle[ 0 ]
        bundle_prereqs = bundle[ 1 ]
        if 1 != len( bundle_prereqs ) or not "main" in bundle_prereqs.keys():
            print( f"FETCH BSP {bundles}" )
            return -1

        prereq = bundle_prereqs[ "main" ]
        if "initial-snapshot" == prereq:
            print( "ok?" )
        else:
            if doing_clone:
                follow_chain = True
            else:
                result = self.gitCmd( [ "cat-file", "-t", prereq ], False )
                follow_chain = ( "commit" != result.stdout.strip() )

            if follow_chain:
                next_link = self.remote.get_link( link_ids[ 1 ] )
                result = self.fetch_chain( next_link, branches, doing_clone )
                if 0 != result:
                    return result

        [ tmp_remote, path_tmp ] = self.bundle_tmp()
        os.makedirs( path_tmp, exist_ok=True )

        bundle_path = f"{path_tmp}/fetch.bundle"
        self.remote.download_bundle( bundle_uid, bundle_path )

        self.gitCmd( [ "bundle", "verify", bundle_path ] )
        self.gitCmd( [ "fetch", tmp_remote ] )

        return 0


    def merge_from_remote( self, branches ):
        print( f"MERGE {self.remote_name} {branches}" )
        branch = branches[ 0 ]

        [ tmp_remote, _ ] = self.bundle_tmp()

        self.gitCmd( [ "merge", f"{tmp_remote}/{branch}" ] )
        return 0


    def get_branches( self ):
        """ git for-each-ref --format=%(refname:short) refs/heads/
        with error checking
        """
        result = self.gitCmd( [ "for-each-ref", "--format=%(refname:short)", "refs/heads/" ], False )
        # cwd=repo_path, 

        if 0 == result.returncode:
            return result.stdout.splitlines()

        return []


    def get_branch_head_sha( self, branch ):
        print( f"MLERP {branch}" )
        result = self.gitCmd( [ "rev-parse", f"refs/heads/{branch}" ], False )
        # cwd=repo_path,

        if 0 == result.returncode:
            return result.stdout.strip()

        return "0xdeadbeef"


    def token_hex( num_bytes ):
        return "".join( f"{b:02x}" for b in secrets.token_bytes( num_bytes ) )


    def change_to_root_git_dir( self ):
        """ cd $( git rev-parse --show-toplevel )
        with some error checking
        """
        result = self.gitCmd( [ "rev-parse", "--show-toplevel" ] )
        git_dir = result.stdout.strip()
        os.chdir( git_dir )
        if pathlib.Path( git_dir ).resolve() == pathlib.Path( os.getcwd() ).resolve():
            return 0
        print( f"ERROR. Weird os.chdir() failure? {result.stdout} {os.getcwd()} ({program_title})" )
        return -1

    def bundle_tmp( self ):
        return [ f"{self.remote_name}-corncob-bundle-tmp",
                 f"./.corncob-bundle-tmp/{self.remote_name}" ]



class GitCmdFailed( Exception ):
    def __init__( self, params, exit_code, out, err ):
        self.params = params
        self.exit_code = exit_code
        self.out = out
        self.err = err

    def __str__( self ):
        return f"ERROR. git cmd failed. `git {' '.join( self.params )}` => {self.exit_code}. o:'{self.out}' e:'{self.err}'"

class CornCobRemote:
    """ Abstract class for different kinds of remotes (Google Drive, etc)
    """
    @staticmethod
    def init( url ):
        smallsea_prefix = "smallsea://"
        if url.startswith( "file://" ):
            return LocalFolderRemote( url[ 7: ].strip() )
        if url.startswith( smallsea_prefix ):
            return SmallSeaRemote( url[ len( smallsea_prefix ): ].strip() )

        raise NotImplementedError( f"Unsupported CornCob cloud protocol. '{corncob_url}'" )

    def read_link_blob( self, yaml_strm ):
        parsed_data = yaml.load( yaml_strm, Loader=yaml.FullLoader )
        link_ids = parsed_data[ 0 ]
        branches = parsed_data[ 1 ]
        bundles = parsed_data[ 2 ]
        for bundle in bundles:
            ps = bundle[ 1 ]
            bundle[ 1 ] = dict( [ ( ps[ i ], ps[ i + 1 ] ) for i in range( 0, len( ps ), 2 ) ] )
        if len( parsed_data ) > 3:
            supp_data = parsed_data[ 3 ]
        else:
            supp_data = {}
        return [ link_ids, branches, bundles, supp_data ]


class LocalFolderRemote( CornCobRemote ):
    """ Mostly for debugging purposes. Pretend a local folder is a cloud location.
    """

    def __init__( self, path ):
        self.path = None

        if not os.path.isdir( path ):
            print( f"ERROR: File URL not a folder '{path}' ({program_title})" )
            return -1

        self.path = path


class SmallSeaRemote( CornCobRemote ):
    """

    """

    def __init__( self, path ):
        self.path = None
        self.session_token = None

        url = f"http://localhost:11437/session/{path}"
        response = requests.get( url )

        if 200 != response.status_code:
            print( f"ERROR. {response.status_code} {response}" )
            return

        response_data = response.json()

        self.path = path
        self.session_token = response_data[ "token" ]

        print( f"SHNIFTY {self.path} {self.session_token}" )


    def upload_latest_link( self, link_uid, blob, bundle_uid, local_bundle_path ):
        path_bundle = f"{self.path}{os.path.sep}B-{bundle_uid}.bundle"
        # TODO: error handling
        shutil.copy( local_bundle_path, path_bundle )

        path_latest = f"{self.path}{os.path.sep}latest-link.yaml"
        with open( path_latest, "w", encoding="utf-8" ) as link_strm:
            yaml.dump( blob, link_strm, default_flow_style=False )

        path_uid = f"{self.path}{os.path.sep}L-{link_uid}.yaml"
        with open( path_uid, "w", encoding="utf-8" ) as link_strm:
            yaml.dump( blob, link_strm, default_flow_style=False )


    def get_link( self, uid ):
        if uid == "latest-link":
            path_link = f"{self.path}{os.path.sep}latest-link.yaml"
        else:
            path_link = f"{self.path}{os.path.sep}L-{uid}.yaml"

        if not os.path.exists( path_link ):
            print( f"FILE DOES NOT EXIST {path_link}" )
            return None

        with open( path_link, "r" ) as link_file_strm:
            return self.read_link_blob( link_file_strm )


    def get_latest_link( self ):
        return self.get_link( "latest-link" )


    def download_bundle( self, bundle_uid, local_bundle_path ):
        path_bundle = f"{self.path}{os.path.sep}B-{bundle_uid}.bundle"
        # TODO: error handling
        shutil.copy( path_bundle, local_bundle_path )


if __name__ == "__main__":
    print( "ERROR. This file contains no `main`" )
