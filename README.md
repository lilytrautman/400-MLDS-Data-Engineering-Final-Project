# 400-MLDS-Data-Engineering-Final-Project

## Read from the Spotify API

The script [`src/read_store_api.py`](src/read_store_api.py) requests the
current user's top tracks and prints the JSON response.

1. Create a Spotify app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Add `http://127.0.0.1:8888/callback` as a Redirect URI in its settings.
3. Add the app credentials to a `.env` file in the project root:

   ```text
   SPOTIFY_CLIENT_ID=your_client_id
   SPOTIFY_CLIENT_SECRET=your_client_secret
   ```

Run it from the project root:

```bash
python src/read_store_api.py
```

The first run opens Spotify login and asks you to authorize access. The API
request uses the access token in its `Authorization` header, sends query
parameters with `requests.get`, checks for HTTP errors, and reads the JSON
response with `response.json()`.
