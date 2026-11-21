"""Log a Spotify account in once and save its token to tokens/<name>.json.

The account must first be added under User Management in the Spotify developer
dashboard (Development Mode allows 5 accounts). After this, the pipeline refreshes
the token by itself.

Usage (from the repo root):
    python src/login.py yash            log in on this computer (opens a browser)
    python src/login.py alex --remote   print a link to send to someone else
"""

import importlib
import sys

import spotipy

ingest = importlib.import_module("01_ingest")  # a module name can't start with a digit


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    name = sys.argv[1].lower()
    remote = "--remote" in sys.argv

    if remote:
        print("Send this link to the account owner. After they log in and agree, their")
        print("browser goes to a 127.0.0.1 page that won't load. That's expected: they")
        print("copy the full address from the address bar and send it back. It works once")
        print("and only for a few minutes.\n")

    sp = spotipy.Spotify(auth_manager=ingest.auth_manager(name, open_browser=not remote))
    me = sp.current_user()
    print(f"\nLogged in {me['display_name']} ({me['id']}), saved to tokens/{name}.json")


if __name__ == "__main__":
    main()
