import argparse
import hashlib
import os
import pathlib
import random
import sqlite3
import sys
import threading
import time
import typing
import logging

import mpris_server
import mpris_server.events
from . import basic_player
from . import song_select
from . import mpd
from PySide2 import QtCore, QtGui, QtWidgets

#TODO: add config file support for stuff
#TODO: if paused when switching songs, stay paused

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
    base INTEGER NOT NULL
);
"""

def resolve_data(filename: str | os.PathLike) -> pathlib.Path:
    if sys.platform == "linux":
        cfg_dir = pathlib.Path("~/.local/share/dullahan").expanduser().resolve()
        cfg_dir.mkdir(parents=True, exist_ok=True)
        return pathlib.Path(cfg_dir, filename)
    else:
        raise NotImplementedError("OS data folder unknown")

def resolve_config(filename: str | os.PathLike) -> pathlib.Path:
    if sys.platform == "linux":
        cfg_dir = pathlib.Path("~/.config/dullahan").expanduser().resolve()
        cfg_dir.mkdir(parents=True, exist_ok=True)
        return pathlib.Path(cfg_dir, filename)
    else:
        raise NotImplementedError("OS config folder unknown")

class Meta(QtCore.QObject):
    def __init__(self, config, player: basic_player.BasicPlayer) -> None:
        super().__init__(None)
        self.config = config
        self.player = player
        self.db = sqlite3.connect(resolve_data("data.db"))
        self.cursor = self.db.cursor()
        self.cursor.executescript(SQL_GENERATE_BASE)
        
        self.player.media_meta_ready.connect(lambda: self.add_media_play())
        self.player.finished.connect(lambda: self.exit())
        self.player.media_finished.connect(lambda: self.add_media_finish())
    
    def exit(self) -> None:
        self.db.commit()
        self.cursor.close()
        self.db.close()
    
    def add_media_finish(self):
        uri = self.player.get_current_uri()
        if not uri:
            return
        file = pathlib.Path(uri)
        media_id = self.get_media_id(file)
        self.cursor.execute("UPDATE medialibrary SET finishes = finishes + 1 WHERE id = ?", (media_id,))
        self.db.commit()
    
    def add_media_play(self):
        uri = self.player.get_current_uri()
        if not uri:
            return
        file = pathlib.Path(uri)
        media_id = self.get_media_id(file)
        ct = int(time.time()*1000)
        if self.cursor.execute("SELECT first_play FROM medialibrary WHERE id = ?", (media_id,)).fetchone() == (-1,):
            self.cursor.execute("UPDATE medialibrary SET first_play = ? WHERE id = ?", (ct, media_id))
        self.cursor.execute("UPDATE medialibrary SET last_play = ? WHERE id = ?", (ct, media_id))
        self.cursor.execute("UPDATE medialibrary SET plays = plays + 1 WHERE id = ?", (media_id,))
        self.db.commit()
        #TODO: finishes
        
    def get_media_id(self, file: pathlib.Path) -> str:
        filename_hash = hashlib.md5(file.name.encode("utf8")).hexdigest()
        media_id = self.cursor.execute("SELECT base FROM file_hashes WHERE hash = ?", (filename_hash,)).fetchone()
        if media_id: #check better
            return media_id[0]
        else:
            self.add_media_entry(file)
            return self.cursor.execute("SELECT base FROM file_hashes WHERE hash = ?", (filename_hash,)).fetchone()[0]
            
    
    def add_media_entry(self, file: pathlib.Path):
        #media: vlc.Media = vlc.Media(str(file))
        #title = media.get_meta(0)
        #album = media.get_meta(4)
        #artist = media.get_meta(1)
        title = self.player.get_current_title()
        artist = self.player.get_current_artist()
        album = self.player.get_current_album()
        filename_hash = hashlib.md5(file.name.encode("utf8")).hexdigest()
        self.cursor.execute("INSERT INTO medialibrary(title, artist, album) VALUES (?, ?, ?)", (title, artist, album))
        media_id = self.cursor.execute("SELECT id FROM medialibrary WHERE title==? AND artist==? AND album==?", (title, artist, album)).fetchone()[0]
        self.cursor.execute("INSERT INTO file_hashes(hash, base) VALUES (?, ?)", (filename_hash, media_id))
        self.db.commit()
    
    @staticmethod
    def generate_queue(path):
        playlist_data = pathlib.Path(path).resolve()
        if playlist_data.is_file():
            playlist_raw = playlist_data.read_text()
            playlist = [pathlib.Path(Meta._parse_playlist_string(playlist_data, line)) for line in playlist_raw.split("\n") if line]
        else:
            playlist = [f.resolve() for f in playlist_data.rglob("*") if f.is_file()]
        return playlist

    @staticmethod
    def _parse_playlist_string(playlist_file: pathlib.Path, item: str) -> str:
        if not item[0] == "/" or item.startswith("file:/"):
            return str(pathlib.Path(playlist_file.parent, item))
        return item

class Tray(QtCore.QObject):
    def __init__(self, config, player: basic_player.BasicPlayer) -> None:
        self.player = player
        self.config = config
        super().__init__(None)
        self.tray = QtWidgets.QSystemTrayIcon()
        self.tray.setToolTip("Dullahan")
        
        #self.pm = QtGui.QPixmap.fromImage("dullahan.png", )
        self.icon = QtGui.QIcon.fromTheme("dullahan", self._get_icon("emblem-music-symbolic"))
        self.tray.setIcon(self.icon) #self._get_icon("emblem-music-symbolic")
        self.tray.activated.connect(self.handle_clicks)
        
        self.popup = song_select.SongSelect(self.player.get_queue(), self.player)
        self.popup.song_selected.connect(self.select_song)
        
        self.player.queue_loaded.connect(lambda: self.popup.set_file_list(self.player.get_queue()))
        self.player.media_changed.connect(lambda: self.tray.setToolTip(f"{self.player.get_current_title()} \nby {self.player.get_current_artist()} (Dullahan)"))
        self.player.media_paused.connect(lambda: self.on_pauseplay(True))
        self.player.media_stopped.connect(lambda: self.on_pauseplay(True))
        self.player.media_stopped.connect(lambda: self.tray.setToolTip(f"Dullahan"))
        self.player.media_played.connect(lambda: self.on_pauseplay(False))
        self.player.media_meta_ready.connect(lambda: self.tray.setToolTip(f"{self.player.get_current_title()} \nby {self.player.get_current_artist()} (Dullahan)"))
        
        #menu
        self.menu = QtWidgets.QMenu()
        self.act_toggle = QtWidgets.QAction(self._get_icon("SP_MediaPause"), "Pause", self.menu)
        self.act_toggle.triggered.connect(lambda: self.player.set_playing(None))
        act_next = QtWidgets.QAction(self._get_icon("SP_MediaSkipForward"), "Next", self.menu)
        act_next.triggered.connect(lambda: self.player.next())
        act_prev = QtWidgets.QAction(self._get_icon("SP_MediaSkipBackward"), "Prev", self.menu)
        act_prev.triggered.connect(lambda: self.player.previous())
        act_stop = QtWidgets.QAction(self._get_icon("SP_MediaStop"), "Stop", self.menu)
        act_stop.triggered.connect(lambda: self.player.set_stopped(True))
        act_shuffle = QtWidgets.QAction(self._get_icon("shuffle"), "Shuffle", self.menu)
        act_shuffle.setCheckable(True)
        act_shuffle.setChecked(self.config.shuffle)
        act_shuffle.triggered.connect(lambda *args, **kwargs: self.player.set_shuffle(act_shuffle.isChecked()))
        act_loop = QtWidgets.QAction(self._get_icon("media-playlist-repeat"), "Loop", self.menu)
        act_loop.setCheckable(True)
        act_loop.setEnabled(False)
        act_loop.setChecked(self.config.loop)
        act_search = QtWidgets.QAction(self._get_icon("search"), "Search", self.menu)
        act_search.triggered.connect(self.popup.show)
        act_exit = QtWidgets.QAction(self._get_icon("application-exit"), "Quit", self.menu)
        act_exit.triggered.connect(lambda: self.quit_button())
    
        
        self.menu.addAction(self.act_toggle)
        self.menu.addAction(act_next)
        self.menu.addAction(act_prev)
        self.menu.addAction(act_stop)
        self.menu.addSeparator()
        self.menu.addAction(act_shuffle)
        self.menu.addAction(act_loop)
        self.menu.addAction(act_search)
        self.menu.addSeparator()
        self.menu.addAction(act_exit)
        
        self.tray.setContextMenu(self.menu)
        self.tray.show()
    
    def quit_button(self):
        self.popup.quit()
        self.player.quit()
    
    @QtCore.Slot()
    def select_song(self, filename: str):
        self.player.set_current_by_file(filename)
    
    def _get_icon(self, name: str):
        if hasattr(QtWidgets.QStyle, name):
            icon = QtWidgets.QCommonStyle().standardIcon(getattr(QtWidgets.QStyle, name))
        else:
            icon = QtGui.QIcon.fromTheme(name)
        if not icon:
            raise RuntimeError("icon not found")
        return icon
        
    def handle_clicks(self, button_pressed):
        if button_pressed == QtWidgets.QSystemTrayIcon.Trigger:
            self.player.set_playing(None)
        elif button_pressed == QtWidgets.QSystemTrayIcon.MiddleClick:
            pass
    
    def on_pauseplay(self, is_paused):
        self.act_toggle.setIcon(self._get_icon(f"SP_Media{'Play' if not is_paused else 'Pause'}"))
        self.act_toggle.setText('Play' if not is_paused else 'Pause')


class Mpris(QtCore.QObject):
    class MprisAnnouncer(mpris_server.adapters.MprisAdapter):
        def __init__(self, d_player: basic_player.BasicPlayer, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.d_player = d_player
            
        def can_quit(self) -> bool: return True
        def can_raise(self) -> bool: return False
        def can_fullscreen(self) -> bool: return False
        def has_tracklist(self) -> bool: return False
        def get_uri_schemes(self) -> list[str]: return super().get_uri_schemes()
        def get_mime_types(self) -> list[str]: return super().get_mime_types()
        def quit(self): self.d_player.quit()
        def get_desktop_entry(self) -> mpris_server.base.Paths: return super().get_desktop_entry() #TODO: me
        def get_current_track(self) -> mpris_server.base.Track:
            t = mpris_server.base.Track(
                name = self.d_player.get_current_title(),
                artists=(mpris_server.base.Artist(name = self.d_player.get_current_artist()),),
                album=mpris_server.base.Album(
                    name = self.d_player.get_current_album(),
                    art_url=self.d_player.get_current_art()
                ),
                track_id = "/org/mpris/MediaPlayer2/CurrentTrack",
                uri = self.d_player.get_current_uri(),
                length = self.d_player.get_current_length() * 1000,
            )
            return t
        def get_current_position(self) -> int: return int(self.d_player.get_current_position() * 1000)
        def next(self): return self.d_player.next()
        def previous(self): return self.d_player.previous()
        def pause(self): return self.d_player.set_playing(False)
        def resume(self): return self.d_player.set_playing(True)
        def stop(self): return self.d_player.set_stopped(True)
        def play(self): return self.d_player.set_playing(True)
        def get_playstate(self) -> mpris_server.base.PlayState:
            state = self.d_player.get_current_state()
            if state == "playing":
                return mpris_server.base.PlayState.PLAYING
            elif state == "paused":
                return mpris_server.base.PlayState.PAUSED
            else:
                return mpris_server.base.PlayState.STOPPED
        def seek(self, time: mpris_server.base.Microseconds, track_id: typing.Optional[mpris_server.base.DbusObj] = None): return self.d_player.seek(int(time/1000))
        def open_uri(self, uri: str): return super().open_uri(uri) #TODO: this?
        def is_repeating(self) -> bool: return True
        def is_playlist(self) -> bool: return False
        def set_repeating(self, val: bool): pass
        def set_loop_status(self, val: str): pass
        def get_rate(self) -> mpris_server.base.RateDecimal: return super().get_rate()
        def set_rate(self, val: mpris_server.base.RateDecimal): pass
        def set_minimum_rate(self, val: mpris_server.base.RateDecimal): pass
        def set_maximum_rate(self, val: mpris_server.base.RateDecimal): pass
        def get_minimum_rate(self) -> mpris_server.base.RateDecimal: return super().get_minimum_rate()
        def get_maximum_rate(self) -> mpris_server.base.RateDecimal:return super().get_maximum_rate()
        def get_shuffle(self) -> bool: return self.d_player.get_shuffle()
        def set_shuffle(self, val: bool): return self.d_player.set_shuffle(val)
        def get_art_url(self, track: int) -> str: return "file://"+self.d_player.get_current_art()
        def get_volume(self) -> mpris_server.base.VolumeDecimal: return super().get_volume()
        def set_volume(self, val: mpris_server.base.VolumeDecimal): pass
        def is_mute(self) -> bool: return False
        def set_mute(self, val: bool): pass
        def can_go_next(self) -> bool: return True
        def can_go_previous(self) -> bool: return True
        def can_play(self) -> bool: return True
        def can_pause(self) -> bool: return True
        def can_seek(self) -> bool: return True
        def can_control(self) -> bool: return True
        def get_stream_title(self) -> str: return self.d_player.get_current_title()
        def get_previous_track(self) -> mpris_server.base.Track: return super().get_previous_track() #TODO
        def get_next_track(self) -> mpris_server.base.Track: return super().get_next_track() #TODO
        #TODO: the playlist section
        #TODO: tracks part

    class MprisUpdater(mpris_server.events.EventAdapter):
        def on_title(self):
            return super().on_title()
        def on_playpause(self):
            return super().on_playpause()
        def on_options(self):
            return super().on_options()
        def on_ended(self):
            return super().on_ended()
            
    def __init__(self, player: basic_player.BasicPlayer):
        super().__init__(None)
        self.player = player
    
    def initialize(self):
        adapter = self.MprisAnnouncer(self.player)
        self.mpris = mpris_server.server.Server("dullahan", adapter=adapter)
        updater = self.MprisUpdater(root=self.mpris.root, player=self.mpris.player)
        
        self.player.media_changed.connect(lambda: updater.on_player_all()) #messier but more responsive
        self.player.media_paused.connect(lambda: updater.on_playpause())
        self.player.media_played.connect(lambda: updater.on_playpause())
        self.player.media_shuffled.connect(lambda: updater.on_player_all())
        self.player.media_unshuffled.connect(lambda: updater.on_player_all())
        self.player.media_stopped.connect(lambda: updater.on_player_all())
        self.player.media_meta_ready.connect(lambda: updater.on_player_all())
        
        self.mpris.publish()


def _except_hook(exc_type, exc_value, exc_traceback):
    logging.critical(f"{exc_type} {exc_value} {exc_traceback}", exc_info=True)
    sys.exit(1)

def exec():
    pathlib.Path("~/.config/dullahan/").expanduser().mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(pathlib.Path("~/.config/dullahan/error.log").expanduser()), filemode='a+')
    logging.getLogger().addHandler(logging.StreamHandler(sys.stderr))
    #sys.excepthook = _except_hook
    #threading.excepthook = _except_hook
    parser = argparse.ArgumentParser("Dullahan")
    parser.add_argument("--shuffle", "-s", action="store_true", default=False)
    parser.add_argument("--loop", "-l", action="store_true", default=False)
    parser.add_argument("--crossfade-length", "-c", default=0)
    parser.add_argument("file")
    
    conf = parser.parse_args()
    
    app = QtWidgets.QApplication(sys.argv)
    
    #setup threads
    player_thread = QtCore.QThread()
    #mpris_thread = QtCore.QThread()
    #create class instances
    player = mpd.MPDPlayer(conf)
    mpris = Mpris(player)
    meta = Meta(conf, player)
    tray = Tray(conf, player)
    #move to threads
    player.moveToThread(player_thread)
    #mpris.moveToThread(mpris_thread)
    #connect to thread starts
    player_thread.started.connect(player.event_loop)
    #mpris_thread.started.connect(mpris.run)
    #preprep
    player.start()
    mpris.initialize()
    #start threads
    player_thread.start()
    #mpris_thread.start()
    
    def exit_():
        player_thread.quit()
        #mpris_thread.quit()
        app.quit()
    
    player.finished.connect(exit_)
    #start app
    app.exec_()


if __name__ == "__main__":
    exec()