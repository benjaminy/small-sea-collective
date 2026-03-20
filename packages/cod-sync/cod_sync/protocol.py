# Cod Sync

import os
import sys
import subprocess
import secrets
import yaml
import pathlib
import shutil
import tempfile
import io
import hashlib
import base64
import requests

program_title = "Cod Sync protocol Git remote helper work-a-like"

COD_SYNC_VERSION = "1.0.0"

class CasConflictError( Exception ):
    """Raised when a compare-and-swap write fails due to a concurrent update."""
    pass

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

class CodSync:

    def __init__( self, remote_name ):
        self.remote_name = remote_name
        self.url = None


    def add_remote( self, url, dotdotdot ):
        """Add a Cod Sync remote

        :param remote: The nickname for the remote
        :param url: The URL for the remote. (This should not include 'codsync:')

        Currently the only supported URL schema is file://
        In the fullness of time the idea is to support googledrive: , etc

        This function adds 2 remotes to the underlying repo:
        - One with the actual remote URL
          - This one is never directly `git fetch`d or `git push`d or whatever
        - One with a temp bundle name
          - When pulling from a remote, the bundle is copied here and `git fetch`d
        """

        self.gitCmd( [ "remote", "add", self.remote_name, f"codsync:{url}" ] )
        print( f"Added remote '{self.remote_name}' ({url})" )

        [ bundle_remote, path ] = self.bundle_tmp()
        self.gitCmd( [ "remote", "add", bundle_remote, f"{path}/fetch.bundle" ] )
        print( f"Added remote '{bundle_remote}' ({path})" )


    def remove_remote( self, dotdotdot ):
        """Remove a Cod Sync remote
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
        with some error checking. Plus strip the 'codsync:' prefix,
        """
        self.url = None
        result = self.gitCmd( [ "remote", "get-url", self.remote_name ], False )
        if 0 != result.returncode:
            return

        remote_url = result.stdout.strip()

        if not remote_url.startswith( "codsync:" ):
            print( f"ERROR: Wrong remote protocol '{remote_url}' ({program_title})" )
            return

        # Strip 'codsync:'
        self.url = remote_url[ 8: ]


    def push_to_remote( self, branches ):
        print( f"PUSH {self.remote_name} {self.url} '{branches}'" )

        bundle_uid = CodSync.token_hex( 8 )
        [ _, path_tmp ] = self.bundle_tmp()
        os.makedirs( path_tmp, exist_ok=True )
        bundle_path_tmp = f"{path_tmp}/B-{bundle_uid}.bundle"

        result = self.remote.get_latest_link()
        if result is None:
            latest_link = None
            etag = None
        else:
            ( latest_link, etag ) = result

        if None == latest_link:
            link_uid = "initial-snapshot"
            link_uid_prev = "initial-snapshot"
            prerequisites = { "main": "initial-snapshot" }
            bundle_spec = "main"
            #     return 0

        else:
            [ link_ids, branches, bundles, supp_data ] = latest_link
            link_uid = CodSync.token_hex( 8 )
            link_uid_prev = link_ids[ 0 ]
            assert( 1 == len( branches ) )
            assert( 0 < len( link_ids ) )
            branch = branches[ 0 ]
            assert( "main" == branch[ 0 ] )
            prerequisites = { "main": branch[ 1 ] }
            tag = f"codsync_temp_tag_{'main'}"
            self.gitCmd( [ "tag", tag, branch[ 1 ] ] )
            bundle_spec = f"{tag}..main"

        self.gitCmd( [ "bundle", "create", bundle_path_tmp, bundle_spec ] )

        if None != latest_link:
            self.gitCmd( [ "tag", "-d", tag ] )

        blob = self.build_link_blob( link_uid, link_uid_prev, bundle_uid, prerequisites )
        print( f"Pushing to Cod Sync clone {link_uid} '{bundle_path_tmp}' {blob}" )
        return self.remote.upload_latest_link( link_uid, blob, bundle_uid, bundle_path_tmp, expected_etag=etag )

    def build_link_blob( self, new_link_uid, prev_link_uid, bundle_uid, prerequisites ):
        link_ids = [ new_link_uid, prev_link_uid ]
        branch_names = self.get_branches()
        branches = []
        for branch in branch_names:
            branches.append( [ branch, self.get_branch_head_sha( branch ) ] )
        print( f"BRANCHES {branches}" )
        bundles = [ [ bundle_uid, [ "main", prerequisites[ "main" ] ] ] ]
        supplement = { "cod_version": COD_SYNC_VERSION }
        return [ link_ids, branches, bundles, supplement ]


    def clone_from_remote( self, url ):
        print( f"CLONE {self.remote_name} {url}" )

        git_cmd = [ "git", "rev-parse", "--show-toplevel" ]
        result = subprocess.run( git_cmd, capture_output=True, text=True )
        if 0 == result.returncode:
            print( f"ERROR. Trying to clone, but already in a repo '{os.getcwd()}' '{result.stdout.strip()}' ({program_title})" )
            return -1

        self.remote = CodSyncRemote.init( url )
        result = self.remote.get_latest_link()
        if result is None:
            latest_link = None
        else:
            ( latest_link, _etag ) = result
        if latest_link == None:
            print( f"CLONE SADNESS" )
            return -1

        # Walk back through the link chain to collect all links from initial to latest
        chain = [ latest_link ]
        current = latest_link
        while current[ 0 ][ 0 ] != "initial-snapshot":
            prev_link_uid = current[ 0 ][ 1 ]
            prev_link = self.remote.get_link( prev_link_uid )
            if prev_link is None:
                print( f"CLONE BROKEN CHAIN at {prev_link_uid}" )
                return -1
            chain.append( prev_link )
            current = prev_link

        # chain is [latest, ..., initial] — reverse to get [initial, ..., latest]
        chain.reverse()

        # Clone from the initial snapshot bundle
        initial_link = chain[ 0 ]
        if len( initial_link[ 2 ] ) != 1:
            print( f"CLONE BS {initial_link[ 2 ]}" )
            return -1
        initial_bundle_uid = initial_link[ 2 ][ 0 ][ 0 ]

        with tempfile.TemporaryDirectory() as bundle_temp_dir:
            bundle_path = f"{bundle_temp_dir}/clone.bundle"
            self.remote.download_bundle( initial_bundle_uid, bundle_path )
            self.gitCmd( [ "clone", bundle_path, "." ] )

        self.gitCmd( [ "checkout", "main" ] )

        self.add_remote( url, [] )

        # Apply remaining incremental bundles
        for link in chain[ 1: ]:
            result = self.fetch_chain( link, [ "main" ], True )
            if 0 != result:
                return result

            [ tmp_remote, _ ] = self.bundle_tmp()
            self.gitCmd( [ "merge", f"{tmp_remote}/main" ] )

        print( f"CLONE WORKED!!!" )
        return 0


    def fetch_from_remote( self, branches ):
        print( f"FETCH {self.remote_name} {self.url} {branches}" )
        result = self.remote.get_latest_link()
        if result is None:
            latest_link = None
        else:
            ( latest_link, _etag ) = result

        if latest_link == None:
            print( f"ERROR: Failed to fetch latest link ({program_title})" )
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
        return [ f"{self.remote_name}-codsync-bundle-tmp",
                 f"./.codsync-bundle-tmp/{self.remote_name}" ]



class GitCmdFailed( Exception ):
    def __init__( self, params, exit_code, out, err ):
        self.params = params
        self.exit_code = exit_code
        self.out = out
        self.err = err

    def __str__( self ):
        return f"ERROR. git cmd failed. `git {' '.join( self.params )}` => {self.exit_code}. o:'{self.out}' e:'{self.err}'"

class CodSyncRemote:
    """ Abstract class for different kinds of remotes (Google Drive, etc)
    """
    @staticmethod
    def init( url ):
        smallsea_prefix = "smallsea://"
        if url.startswith( "file://" ):
            return LocalFolderRemote( url[ 7: ].strip() )
        if url.startswith( smallsea_prefix ):
            remainder = url[ len( smallsea_prefix ): ].strip()
            # remainder is host:port/SESSION_HEX
            slash_pos = remainder.find( "/" )
            if slash_pos < 0:
                raise ValueError( f"Invalid smallsea URL, expected smallsea://host:port/SESSION_HEX, got '{url}'" )
            host_port = remainder[ :slash_pos ]
            session_hex = remainder[ slash_pos + 1: ]
            return SmallSeaRemote( session_hex, base_url=f"http://{host_port}" )

        if url.startswith( "s3://" ):
            remainder = url[ 5: ]
            # format: access_key:secret_key@host:port/bucket_name
            at_pos = remainder.find( "@" )
            if at_pos < 0:
                raise ValueError( f"Invalid s3 URL, expected s3://access_key:secret_key@host:port/bucket_name, got '{url}'" )
            creds = remainder[ :at_pos ]
            host_and_bucket = remainder[ at_pos + 1: ]
            colon_pos = creds.find( ":" )
            if colon_pos < 0:
                raise ValueError( f"Invalid s3 URL credentials, expected access_key:secret_key, got '{creds}'" )
            access_key = creds[ :colon_pos ]
            secret_key = creds[ colon_pos + 1: ]
            slash_pos = host_and_bucket.find( "/" )
            if slash_pos < 0:
                raise ValueError( f"Invalid s3 URL, expected host:port/bucket_name, got '{host_and_bucket}'" )
            host_port = host_and_bucket[ :slash_pos ]
            bucket_name = host_and_bucket[ slash_pos + 1: ]
            endpoint_url = f"http://{host_port}"
            return S3Remote( endpoint_url, bucket_name, access_key, secret_key )

        raise NotImplementedError( f"Unsupported Cod Sync cloud protocol. '{url}'" )

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

        # Version compatibility check
        link_version = supp_data.get( "cod_version", "0.0.0" )
        link_major = int( link_version.split( "." )[ 0 ] )
        reader_major = int( COD_SYNC_VERSION.split( "." )[ 0 ] )
        if link_major > reader_major:
            raise ValueError(
                f"Link format version {link_version} is incompatible with this reader "
                f"(supports up to major version {reader_major}). Please upgrade Cod Sync." )

        return [ link_ids, branches, bundles, supp_data ]


class LocalFolderRemote( CodSyncRemote ):
    """ Mostly for debugging purposes. Pretend a local folder is a cloud location.
    """

    def __init__( self, path ):
        self.path = None

        if not os.path.isdir( path ):
            print( f"ERROR: File URL not a folder '{path}' ({program_title})" )
            return -1

        self.path = path


    @staticmethod
    def _file_etag( path ):
        """Compute an etag (MD5 hex digest) for a file's content."""
        h = hashlib.md5()
        with open( path, "rb" ) as f:
            for chunk in iter( lambda: f.read( 8192 ), b"" ):
                h.update( chunk )
        return h.hexdigest()

    def upload_latest_link( self, link_uid, blob, bundle_uid, local_bundle_path, expected_etag=None ):
        path_bundle = f"{self.path}{os.path.sep}B-{bundle_uid}.bundle"
        shutil.copy( local_bundle_path, path_bundle )

        path_latest = f"{self.path}{os.path.sep}latest-link.yaml"

        # CAS check for latest-link.yaml
        if expected_etag is not None:
            if not os.path.exists( path_latest ):
                raise CasConflictError( "expected existing file but latest-link.yaml does not exist" )
            current_etag = self._file_etag( path_latest )
            if current_etag != expected_etag:
                raise CasConflictError(
                    f"CAS conflict on latest-link.yaml: expected etag {expected_etag}, got {current_etag}" )
        elif os.path.exists( path_latest ) and expected_etag is None:
            # First push should not have an etag; subsequent pushes should.
            # We allow None for backward compat but the caller should pass etags.
            pass

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
            link = self.read_link_blob( link_file_strm )

        if uid == "latest-link":
            etag = self._file_etag( path_link )
            return ( link, etag )
        return link


    def get_latest_link( self ):
        return self.get_link( "latest-link" )


    def download_bundle( self, bundle_uid, local_bundle_path ):
        path_bundle = f"{self.path}{os.path.sep}B-{bundle_uid}.bundle"
        shutil.copy( path_bundle, local_bundle_path )


class SmallSeaRemote( CodSyncRemote ):
    """Hub-backed cloud storage remote.

    Talks to the hub's POST /cloud_file and GET /cloud_file endpoints.
    """

    def __init__( self, session_hex, base_url="http://localhost:11437", client=None ):
        self.session_hex = session_hex

        if client is not None:
            self._post = client.post
            self._get = client.get
            self._url_prefix = ""
        else:
            self._url_prefix = base_url
            self._post = lambda path, **kw: requests.post( f"{base_url}{path}", **kw )
            self._get = lambda path, **kw: requests.get( f"{base_url}{path}", **kw )


    def _upload( self, cloud_path, data_bytes, expected_etag=None ):
        payload = {
            "session": self.session_hex,
            "path": cloud_path,
            "data": base64.b64encode( data_bytes ).decode(),
        }
        if expected_etag is not None:
            payload[ "expected_etag" ] = expected_etag
        resp = self._post( "/cloud_file", json=payload )
        if resp.status_code == 409:
            raise CasConflictError( f"CAS conflict uploading {cloud_path}" )
        if resp.status_code != 200:
            raise RuntimeError( f"cloud upload failed ({resp.status_code}): {cloud_path}" )
        return resp


    def _download( self, cloud_path ):
        resp = self._get( "/cloud_file", params={
            "session": self.session_hex,
            "path": cloud_path,
        })
        if resp.status_code != 200:
            return ( None, None )
        body = resp.json()
        data = base64.b64decode( body[ "data" ] )
        etag = body.get( "etag" )
        return ( data, etag )


    def upload_latest_link( self, link_uid, blob, bundle_uid, local_bundle_path, expected_etag=None ):
        # 1. Upload bundle
        with open( local_bundle_path, "rb" ) as f:
            bundle_bytes = f.read()
        self._upload( f"B-{bundle_uid}.bundle", bundle_bytes )

        # 2. Serialize link YAML
        link_yaml = yaml.dump( blob, default_flow_style=False ).encode( "utf-8" )

        # 3. Upload latest-link.yaml (with CAS) and L-{link_uid}.yaml
        self._upload( "latest-link.yaml", link_yaml, expected_etag=expected_etag )
        self._upload( f"L-{link_uid}.yaml", link_yaml )


    def get_link( self, uid ):
        if uid == "latest-link":
            cloud_path = "latest-link.yaml"
        else:
            cloud_path = f"L-{uid}.yaml"

        ( data, etag ) = self._download( cloud_path )
        if data is None:
            return None

        link = self.read_link_blob( io.BytesIO( data ) )

        if uid == "latest-link":
            return ( link, etag )
        return link


    def get_latest_link( self ):
        return self.get_link( "latest-link" )


    def download_bundle( self, bundle_uid, local_bundle_path ):
        data = self._download( f"B-{bundle_uid}.bundle" )
        if data is None:
            raise RuntimeError( f"Failed to download bundle B-{bundle_uid}.bundle" )
        with open( local_bundle_path, "wb" ) as f:
            f.write( data )


class S3Remote( CodSyncRemote ):
    """S3-backed cloud storage remote (works with MinIO or AWS S3)."""

    def __init__( self, endpoint_url, bucket_name, access_key, secret_key ):
        import boto3
        from botocore.config import Config

        self.bucket_name = bucket_name
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config( signature_version="s3v4" ),
            region_name="us-east-1",
        )
        # Ensure bucket exists
        try:
            self.s3.head_bucket( Bucket=bucket_name )
        except Exception:
            self.s3.create_bucket( Bucket=bucket_name )


    def upload_latest_link( self, link_uid, blob, bundle_uid, local_bundle_path, expected_etag=None ):
        # TODO: S3Remote is slated for elimination (all cloud access should go through Hub).
        # expected_etag is accepted for interface compatibility but not enforced here.
        # 1. Upload bundle
        self.s3.upload_file( local_bundle_path, self.bucket_name, f"B-{bundle_uid}.bundle" )

        # 2. Serialize link YAML
        link_yaml = yaml.dump( blob, default_flow_style=False ).encode( "utf-8" )

        # 3. Upload latest-link.yaml and L-{link_uid}.yaml
        self.s3.put_object( Bucket=self.bucket_name, Key="latest-link.yaml", Body=link_yaml )
        self.s3.put_object( Bucket=self.bucket_name, Key=f"L-{link_uid}.yaml", Body=link_yaml )


    def get_link( self, uid ):
        if uid == "latest-link":
            key = "latest-link.yaml"
        else:
            key = f"L-{uid}.yaml"

        try:
            resp = self.s3.get_object( Bucket=self.bucket_name, Key=key )
            link = self.read_link_blob( io.BytesIO( resp[ "Body" ].read() ) )
            if uid == "latest-link":
                etag = resp.get( "ETag" )
                return ( link, etag )
            return link
        except self.s3.exceptions.NoSuchKey:
            return None
        except Exception as e:
            if "NoSuchKey" in str( e ) or "Not Found" in str( e ):
                return None
            raise


    def get_latest_link( self ):
        return self.get_link( "latest-link" )


    def download_bundle( self, bundle_uid, local_bundle_path ):
        self.s3.download_file( self.bucket_name, f"B-{bundle_uid}.bundle", local_bundle_path )


if __name__ == "__main__":
    print( "ERROR. This file contains no `main`" )
