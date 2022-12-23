import vlc
import pathlib
import sqlite3
import os
import sys
import hashlib
from PySide2 import QtGui, QtCore, QtWidgets

SQL_GENERATE_BASE = """
CREATE TABLE IF NOT EXISTS medialibrary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title STRING,
    artist STRING,
    album STRING,
    plays INTEGER DEFAULT 0,
    finishes INTEGER DEFAULT 0,
    first_play INTEGER DEFAULT -1 NOT NULL,
    last_play INTEGER DEFAULT -1 NOT NULL
);
CREATE TABLE IF NOT EXISTS file_hashes (
    hash STRING PRIMARY KEY NOT NULL,
    base INTEGER NOT NULL,
);
"""

def resolve_config(filename: str | os.PathLike) -> pathlib.Path:
    if sys.platform == "linux":
        cfg_dir = pathlib.Path("~/.local/share/horseman").expanduser().resolve()
        cfg_dir.mkdir(parents=True, exist_ok=True)
        return pathlib.Path(cfg_dir, filename)

class Meta(QtCore.QObject):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.db = sqlite3.connect(resolve_config("data.db"))
        self.cursor = self.db.cursor()
        self.cursor.executescript(SQL_GENERATE_BASE)
    
    def get_media_id(self, file: pathlib.Path) -> str:
        filename_hash = hashlib.md5(file.name.encode("utf8")).hexdigest()
        media_id = self.cursor.execute("SELECT base FROM file_hashes WHERE hash = ?", (filename_hash,)).fetchone()
        if media_id: #check better
            return media_id
        else:
            raise NotImplementedError("Matching file with entry")
    
    @QtCore.Slot(int, result=bool)
    def media_started(self, media_id):
        pass
        return True

class Tray(QtCore.QObject):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.tray = QtWidgets.QSystemTrayIcon()
        self.tray.activated.connect
    

class Mpris(object): pass
class Player(QtCore.QObject):
    media_changed = QtCore.Signal()
    media_paused = QtCore.Signal()
    media_played = QtCore.Signal()

    def __init__(self, inst: vlc.Instance, parent=None) -> None:
        super().__init__(parent)
        self.instance = inst
        self.player = self.instance.media_player_new()
    
    @QtCore.Slot(result=bool)
    def receive_pause(self):
        pass
        return True
    
    @QtCore.Slot(result=bool)
    def receive_play(self):
        pass
        return True

if __name__ == "__main__":
    pass
