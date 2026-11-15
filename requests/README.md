# Song requests by file

Drop a JSON file here and push: the `song request` workflow hears the recording, writes the
robots' arrangement and films a preview (it does not post). Fields:

```json
{ "audio_url": "https://drive.google.com/file/d/…/view", "slug": "artist-song", "title": "Song",
  "artist": "Artist", "start": "auto", "length": 59, "arrangement": "band", "lang": "auto", "voice": "auto" }
```

In a private repo the preview video is also pushed to the `previews` branch.
