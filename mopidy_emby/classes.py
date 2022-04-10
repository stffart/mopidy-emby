from mopidy.models import Playlist, Track, Ref, fields, Artist, Album

class ARef(Ref):
  artwork = fields.String()

class AAlbum(Album):
  artwork = fields.String()

class AArtist(Artist):
  artwork = fields.String()

class ATrack(Track):
  artwork = fields.String()
