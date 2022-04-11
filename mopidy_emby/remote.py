from __future__ import unicode_literals

import hashlib

import logging

from collections import OrderedDict, defaultdict

from urllib.parse import urlencode, quote
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from mopidy import httpclient, models

import requests

import mopidy_emby

from mopidy_emby.utils import cache

from .classes import AAlbum, AArtist, ATrack, ARef

logger = logging.getLogger(__name__)


class EmbyHandler(object):
    def __init__(self, config):
        self.hostname = config['emby']['hostname']
        self.port = config['emby']['port']
        self.username = config['emby']['username']
        self.password = config['emby']['password']
        self.proxy = config['proxy']
        self.user_id = config['emby'].get('user_id', False)

        # create authentication headers
        self.auth_data = self._password_data()
        self.user_id = self.user_id or self._get_user()[0]['Id']
        self.headers = self._create_headers()

        self.token =  config['emby']['password']

#self._get_token()

        self.headers = self._create_headers(token=self.token)

    def _get_user(self):
        """Return user dict from server or None if there is no user.
        """
        url = self.api_url('/Users/Public')
        r = requests.get(url)
        user = [i for i in r.json() if i['Name'] == self.username]

        if user:
            return user
        else:
            raise Exception('No Emby user {} found'.format(self.username))

    def _get_token(self):
        """Return token for a user.
        """
        url = self.api_url('/Users/AuthenticateByName')
        r = requests.post(url, headers=self.headers, data=self.auth_data)
        return r.json().get('AccessToken')

    def _password_data(self):
        """Returns a dict with username and its encoded password.
        """
        return {
            'username': self.username,
            'password': hashlib.sha1(
                self.password.encode('utf-8')).hexdigest(),
            'passwordMd5': hashlib.md5(
                self.password.encode('utf-8')).hexdigest()
        }

    def _create_headers(self, token=None):
        """Return header dict that is needed to talk to the Emby API.
        """
        headers = {}

        authorization = (
            'MediaBrowser UserId="{user_id}", '
            'Client="other", '
            'Device="mopidy", '
            'DeviceId="mopidy", '
            'Version="0.0.0"'
        ).format(user_id=self.user_id)

        headers['x-emby-authorization'] = authorization

        if token:
            headers['x-emby-token'] = self.token

        return headers

    def _get_session(self):
        proxy = httpclient.format_proxy(self.proxy)
        full_user_agent = httpclient.format_user_agent(
            '/'.join(
                (mopidy_emby.Extension.dist_name, mopidy_emby.__version__)
            )
        )

        session = requests.Session()
        session.proxies.update({'http': proxy, 'https': proxy})
        session.headers.update({'user-agent': full_user_agent})

        return session

    def r_get(self, url):
        logger.debug(url)
        counter = 0
        session = self._get_session()
        session.headers.update(self.headers)
        while counter <= 5:

            try:
                r = session.get(url)
                rv = r.json()

                logger.debug(str(rv))

                return rv

            except Exception as e:
                logger.info(
                    'Emby connection on try {} with problem: {}'.format(
                        counter, e
                    )
                )
                counter += 1

        raise Exception('Cant connect to Emby API')

    def api_url(self, endpoint):
        """Returns a joined url.

        Takes host, port and endpoint and generates a valid emby API url.
        """
        # check if http or https is defined as host and create hostname
        hostname_list = [self.hostname]
        if self.hostname.startswith('http://') or \
                self.hostname.startswith('https://'):
            hostname = ''.join(hostname_list)
        else:
            hostname_list.insert(0, 'http://')
            hostname = ''.join(hostname_list)

        joined = urljoin(
            '{hostname}:{port}'.format(
                hostname=hostname,
                port=self.port
            ),
            endpoint
        )

        scheme, netloc, path, query_string, fragment = urlsplit(joined)
        query_params = parse_qs(query_string)

        query_params['format'] = ['json']
        new_query_string = urlencode(query_params, doseq=True)

        return urlunsplit((scheme, netloc, path, new_query_string, fragment))

    def get_music_root(self):
        url = self.api_url(
            '/Users/{}/Views'.format(self.user_id)
        )

        data = self.r_get(url)

        id = [i['Id']
              for i in data['Items']
              if 'CollectionType' in i.keys()
              if i['CollectionType'] == 'music']

        if id:
            logging.debug(
                'Emby: Found music root dir with ID: {}'.format(id[0])
            )
            return id[0]

        else:
            logging.debug(
                'Emby: All directories found: {}'.format(
                    [i['CollectionType']
                     for i in data['Items']
                     if 'CollectionType' in i.items()]
                )
            )
            raise Exception('Emby: Cant find music root directory')

    def get_artists(self):
        music_root = self.get_music_root()
        albums = sorted(
            self.get_item_type(music_root,'MusicAlbum')['Items'],
            key=lambda k: k['Name']
        )
        res_artists  = []
        artist_names = []
        for album in albums:
          artists = []
          artwork = ""
          if 'Primary' in album['ImageTags']:
            image_tag = album['ImageTags']['Primary']
            artwork = "{}:{}/emby/Items/{}/Images/Primary?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,album['Id'],image_tag)
          else:
            logger.error(album)
          for artist in album['AlbumArtists']:
            if artist['Name'] not in artist_names:
              res_artists.append(ARef(uri='emby:artist:{}'.format(artist['Id']), name=artist['Name'], artwork=artwork ))
              artist_names.append( artist['Name'] )

        return res_artists


    def get_albums_list(self):
       music_root = self.get_music_root()
       albums = self.get_item_type(music_root,'MusicAlbum')['Items']
       return [
          album['Name']
          for album in albums
          if album
       ]

    def get_artists_list(self):
       music_root = self.get_music_root()
       artists = self.get_directory(music_root)['Items']
       return [
          artist['Name']
          for artist in artists
          if artist
       ]

    def get_albums(self, artist_id):
        music_root = self.get_music_root()
        albums = self.get_item_type(music_root,'MusicAlbum')['Items']
        res_albums = []
        for album in albums:
          artists = []
          skip = True
          for artist in album['ArtistItems']:
              if artist['Id'] == artist_id:
                 skip = False
                 break
          if skip:
            continue
          artwork = ""
          if 'Primary' not in album['ImageTags']:
            if 'ParentBackdropImageTags' in album:
               artwork = "{}:{}/emby/Items/{}/Images/Backdrop?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,album['ParentBackdropItemId'],album['ParentBackdropImageTags'][0])
          else:
            image_tag = album['ImageTags']['Primary']
            artwork = "{}:{}/emby/Items/{}/Images/Primary?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,album['Id'],image_tag)

          res_albums.append(ARef(uri='emby:album:{}'.format(album['Id']), name=album['Name'], artwork=artwork ))
        return res_albums


        albums = sorted(
            self.get_directory(artist_id)['Items'],
            key=lambda k: k['Name']
        )
        return [
            models.Ref.album(
                uri='emby:album:{}'.format(i['Id']),
                name=i['Name']
            )
            for i in albums
            if i
        ]

    def list_albums(self):
        music_root = self.get_music_root()
        albums = sorted(
            self.get_item_type(music_root,'MusicAlbum')['Items'],
            key=lambda k: k['Name']
        )
        res_albums  = []
        for album in albums:
          artists = []
          artwork = ""
          if 'Primary' not in album['ImageTags']:
            if 'ParentBackdropImageTags' in album:
               artwork = "{}:{}/emby/Items/{}/Images/Backdrop?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,album['ParentBackdropItemId'],album['ParentBackdropImageTags'][0])
          else:
            image_tag = album['ImageTags']['Primary']
            artwork = "{}:{}/emby/Items/{}/Images/Primary?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,album['Id'],image_tag)
          for artist in album['AlbumArtists']:
            artists.append(models.Artist(uri="emby:artist:{}".format(artist['Id']),name=artist['Name']))
          res_albums.append(AAlbum(uri='emby:album:{}'.format(album['Id']), name=album['Name'], artists=artists,artwork=artwork ))
        return res_albums

    def list_artists(self):
        music_root = self.get_music_root()
        albums = sorted(
            self.get_item_type(music_root,'MusicAlbum')['Items'],
            key=lambda k: k['Name']
        )
        res_artists  = []
        for album in albums:
          artists = []
          artwork = ""
          if 'Primary' in album['ImageTags']:
            image_tag = album['ImageTags']['Primary']
            artwork = "{}:{}/emby/Items/{}/Images/Primary?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,album['Id'],image_tag)
          else:
            logger.error(album)
          for artist in album['AlbumArtists']:
            res_artists.append(AArtist(uri='emby:artist:{}'.format(artist['Id']), name=artist['Name'], artwork=artwork ))

        return res_artists

    def get_tracks(self, album_id):
        tracks = sorted(
            self.get_directory(album_id)['Items'],
            key=lambda k: k['IndexNumber']
        )
        res_tracks = []
        for track in tracks:
          res_tracks.append(self.create_track_ref(track) )
        return res_tracks

    @cache()
    def get_directory(self, id):
        """Get directory from Emby API.

        :param id: Directory ID
        :type id: int
        :returns Directory
        :rtype: dict
        """
        return self.r_get(
            self.api_url(
                '/Users/{}/Items?ParentId={}&SortOrder=Ascending'.format(
                    self.user_id,
                    id
                )
            )
        )

    @cache()
    def get_item_type(self, parent_id, t):
        """Get directory from Emby API.

        :param id: Directory ID
        :type id: int
        :returns Directory
        :rtype: dict
        """
        return self.r_get(
            self.api_url(
                '/Users/{}/Items?Recursive=true&SortOrder=Ascending&ParentId={}&IncludeItemTypes={}'.format(
                    self.user_id,
                    parent_id,
                    t
                )
            )
        )

    @cache()
    def get_item(self, id):
        """Get item from Emby API.

        :param id: Item ID
        :type id: int
        :returns: Item
        :rtype: dict
        """
        data = self.r_get(
            self.api_url(
                '/Users/{}/Items/{}'.format(self.user_id, id)
            )
        )

        logger.debug('Emby item: {}'.format(data))

        return data

    def create_track(self, track):
        """Create track from Emby API track dict.

        :param track: Track from Emby API
        :type track: dict
        :returns: Track
        :rtype: mopidy.models.Track
        """
        # TODO: add more metadata
        artwork = ""
        if 'Primary' not in track['ImageTags']:
          if 'AlbumPrimaryImageTag' in track:
            if track['AlbumPrimaryImageTag'] != '':
                artwork = "{}:{}/emby/Items/{}/Images/Primary?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,track['AlbumId'],track['AlbumPrimaryImageTag'])
          if 'ParentBackdropImageTags' in track:
               artwork = "{}:{}/emby/Items/{}/Images/Backdrop?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,track['ParentBackdropItemId'],track['ParentBackdropImageTags'][0])
        else:
            image_tag = track['ImageTags']['Primary']
            artwork = "{}:{}/emby/Items/{}/Images/Primary?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,track['Id'],image_tag)

        logger.error(track)
        logger.error(artwork)
        return ATrack(
            uri='emby:track:{}'.format(
                track['Id']
            ),
            name=track.get('Name'),
            track_no=track.get('IndexNumber'),
            genre=track.get('Genre'),
            artists=self.create_artists(track),
            album=self.create_album(track),
            artwork=artwork,
            length=int(self.ticks_to_milliseconds(track['RunTimeTicks']))
        )

    def create_track_ref(self, track):
        """Create track from Emby API track dict.

        :param track: Track from Emby API
        :type track: dict
        :returns: Track
        :rtype: mopidy.models.Track
        """
        # TODO: add more metadata
        artwork = ""
        if 'Primary' not in track['ImageTags']:
          if 'AlbumPrimaryImageTag' in track:
            if track['AlbumPrimaryImageTag'] != '':
                artwork = "{}:{}/emby/Items/{}/Images/Primary?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,track['AlbumId'],track['AlbumPrimaryImageTag'])
          if 'ParentBackdropImageTags' in track:
               artwork = "{}:{}/emby/Items/{}/Images/Backdrop?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,track['ParentBackdropItemId'],track['ParentBackdropImageTags'][0])
        else:
            image_tag = track['ImageTags']['Primary']
            artwork = "{}:{}/emby/Items/{}/Images/Primary?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,track['Id'],image_tag)

        return ARef(
            uri='emby:track:{}'.format(
                track['Id']
            ),
            type=ARef.TRACK,
            name=track.get('Name'),
            artwork=artwork
        )

    def create_album_id(self, album_id):
          music_root = self.get_music_root()
          albums = self.get_item_type(music_root,'MusicAlbum')['Items']
          for album in albums:
            if album['Id'] == album_id:
              if 'Primary' not in album['ImageTags']:
                if 'ParentBackdropImageTags' in album:
                   artwork = "{}:{}/emby/Items/{}/Images/Backdrop?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,album['ParentBackdropItemId'],album['ParentBackdropImageTags'])
              else:
                image_tag = album['ImageTags']['Primary']
                artwork = "{}:{}/emby/Items/{}/Images/Primary?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,album['Id'],image_tag)
              artists = []
              for artist in album['AlbumArtists']:
                artists.append(models.Artist(uri="emby:artist:{}".format(artist['Id']),name=artist['Name']))
              return AAlbum(uri='emby:album:{}'.format(album['Id']), name=album['Name'], artists=artists,artwork=artwork )
          return None

    def create_artist_id(self, artist_id):
        music_root = self.get_music_root()
        albums = self.get_item_type(music_root,'MusicAlbum')['Items']
        res_artist = None
        for album in albums:
          for artist in album['AlbumArtists']:
            if artist["Id"] == artist_id:
              artwork = ""
              if 'Primary' not in album['ImageTags']:
                if 'ParentBackdropImageTags' in album:
                   artwork = "{}:{}/emby/Items/{}/Images/Backdrop?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,album['ParentBackdropItemId'],album['ParentBackdropImageTags'])
              else:
                image_tag = album['ImageTags']['Primary']
                artwork = "{}:{}/emby/Items/{}/Images/Primary?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,album['Id'],image_tag)
              res_artist = AArtist(uri='emby:artist:{}'.format(artist['Id']), name=artist['Name'], artwork=artwork )
        return res_artist

    def create_artist_name(self, artist_name):
        music_root = self.get_music_root()
        albums = self.get_item_type(music_root,'MusicAlbum')['Items']
        res_artist = None
        res_albums = []
        for album in albums:
          for artist in album['AlbumArtists']:
            if artist["Name"] == artist_name:
              if 'Primary' not in album['ImageTags']:
                if 'ParentBackdropImageTags' in album:
                   artwork = "{}:{}/emby/Items/{}/Images/Backdrop?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,album['ParentBackdropItemId'],album['ParentBackdropImageTags'])
              else:
                image_tag = album['ImageTags']['Primary']
                artwork = "{}:{}/emby/Items/{}/Images/Primary?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,album['Id'],image_tag)
              res_albums.append(AAlbum(uri='emby:album:{}'.format(album['Id']), name=album['Name'], artwork = artwork))
              if res_artist == None:
                res_artist = AArtist(uri='emby:artist:{}'.format(artist['Id']), name=artist['Name'], artwork=artwork )
        return res_artist, res_albums


    def create_album(self, track):
        """Create album object from track.

        :param track: Track
        :type track: dict
        :returns: Album
        :rtype: mopidy.models.Album
        """
        return models.Album(
            name=track.get('Album'),
            artists=self.create_artists(track)
        )

    def create_artists(self, track):
        """Create artist object from track.

        :param track: Track
        :type track: dict
        :returns: List of artists
        :rtype: list of mopidy.models.Artist
        """
        
        return [
            models.Artist(
                name=artist['Name']
            )
            for artist in track['ArtistItems']
        ]

    @cache()
    def get_track(self, track_id):
        """Get track.

        :param track_id: ID of a Emby track
        :type track_id: int
        :returns: track
        :rtype: mopidy.models.Track
        """
        track = self.get_item(track_id)

        return self.create_track(track)

    def _get_search(self, itemtype, term):
        """Gets search data from Emby API.

        :param itemtype: Type to search for
        :param term: Search term
        :type itemtype: str
        :type term: str
        :returns: List of result dicts
        :rtype: list
        """
        if itemtype == 'any':
            query = 'Audio,MusicAlbum,MusicArtist'
        elif itemtype == 'artist':
            query = 'MusicArtist'
        elif itemtype == 'album':
            query = 'MusicAlbum'
        elif itemtype == 'track_name':
            query = 'Audio'
        else:
            raise Exception('Emby search: no itemtype {}'.format(itemtype))

        data = self.r_get(
            self.api_url(
                ('/Search/Hints?SearchTerm={}&'
                 'IncludeItemTypes={}').format(
                     quote(term),
                     query
                )
            )
        )
        res_tracks = []
        res_artists = []
        res_albums = []
        for result in data.get('SearchHints'):
           if result['Type'] == 'MusicArtist':
              artist,albums = self.create_artist_name(result['Name'])
              res_artists.append(artist)
              res_albums.extend(albums)
           if result['Type'] == 'MusicAlbum':
              res_albums.append(self.create_album_id(result['Id']))
           if result['Type'] == 'Audio':
              res_tracks.append(self.get_track(result['Id']))
        return res_tracks, res_artists, res_albums

    @cache()
    def search(self, query):
        """Search Emby for a term.

        :param query: Search query
        :type query: dict
        :returns: Search results
        :rtype: mopidy.models.SearchResult
        """
        logger.debug('Searching in Emby for {}'.format(query))

        # something to store the results in
        data = []
        tracks = []
        albums = []
        artists = []

        for itemtype, term in query.items():

            for item in term:
                ntracks,nartists,nalbums = self._get_search(itemtype, item)
                tracks.extend(ntracks)
                artists.extend(nartists)
                albums.extend(nalbums)
        search_res = models.SearchResult(
            uri='emby:search',
            tracks=tracks,
            artists=artists,
            albums=albums
        )
        return search_res

    def lookup_artist(self, artist_id):
        """Lookup all artist tracks and sort them.

        :param artist_id: Artist ID
        :type artist_id: int
        :returns: List of tracks
        :rtype: list
        """
        music_root = self.get_music_root()
        albums = self.get_item_type(music_root,'MusicAlbum')['Items']
        res_albums = []
        for album in albums:
          artists = []
          skip = True
          for artist in album['ArtistItems']:
              if artist['Id'] == artist_id:
                 skip = False
                 break
          if skip:
            continue
          artwork = ""
          if 'Primary' not in album['ImageTags']:
            if 'ParentBackdropImageTags' in album:
               artwork = "{}:{}/emby/Items/{}/Images/Backdrop?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,album['ParentBackdropItemId'],album['ParentBackdropImageTags'][0])
          else:
            image_tag = album['ImageTags']['Primary']
            artwork = "{}:{}/emby/Items/{}/Images/Primary?maxHeight=%1&maxWidth=%2&tag={}".format(self.hostname,self.port,album['Id'],image_tag)
          for artist in album['AlbumArtists']:
            artists.append(models.Artist(uri="emby:artist:{}".format(artist['Id']),name=artist['Name']))
          res_albums.append(ATrack(uri='emby:album:{}'.format(album['Id']), name=album['Name'], artists=artists,artwork=artwork ))
        return res_albums


    @staticmethod
    def ticks_to_milliseconds(ticks):
        """Converts Emby track length ticks to milliseconds.

        :param ticks: Ticks
        :type ticks: int
        :returns: Milliseconds
        :rtype: int
        """
        return ticks / 10000

    @staticmethod
    def milliseconds_to_ticks(milliseconds):
        """Converts milliseconds to ticks.

        :param milliseconds: Milliseconds
        :type milliseconds: int
        :returns: Ticks
        :rtype: int
        """
        return milliseconds * 10000
