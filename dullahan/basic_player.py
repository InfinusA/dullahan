from abc import abstractmethod
import argparse
import collections
import os
import pathlib
import typing

import mutagen
import mutagen._file
import mutagen.mp4
import mutagen.mp3
import mutagen.id3
from PySide2 import QtCore, QtGui

Capabilities = collections.namedtuple('Capabilities', ['loop', 'shuffle', 'crossfade'])

MP4CONV = {
    12: 'gif',
    13: 'jpg',
    14: 'png'
}
MP3CONV = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/gif': 'gif'
}

class FileMetadata(object):
    def __init__(self, file: str | os.PathLike, autoparse=True) -> None:
        self.file = pathlib.Path(file)
        self.base = mutagen._file.File(file)
        if self.base is None:
            raise RuntimeError(f"could not parse file {file}")
        self.title = ''
        self.album = ''
        self.artist = ''
        self.art = ''
        self.raw_art = b''
        self.art_filetype = ''
        self.placeholder_art = QtGui.QImage(256, 256, QtGui.QImage.Format_Indexed8)
        self.placeholder_art.fill(QtGui.qRgb(50,50,50))
        if autoparse:
            self.parse()
    
    def _data_to_qimage(self, data: str | bytes) -> QtGui.QImage:
        return QtGui.QImage.fromData(QtCore.QByteArray.fromRawData(data))
    
    def parse(self):
        if isinstance(self.base, mutagen.mp4.MP4):
            tags: mutagen.mp4.MP4Tags = self.base.tags 
            self.title = tags['\xa9nam'][0] or self.file.name
            self.album = tags['\xa9alb'][0] or self.file.parent
            self.artist = ", ".join(tags['\xa9ART']) or self.file.parent
            self.raw_art: bytes = tags['covr'][0] if 'covr' in tags else b''
            self.art = self._data_to_qimage(tags['covr'][0]) if 'covr' in tags else self.placeholder_art
            self.art_filetype = MP4CONV[tags['covr'][0].imageformat]
            
        elif isinstance(self.base, mutagen.mp3.MP3):
            tags: mutagen.id3.ID3 = self.base.tags
            self.title = tags['TIT2'].text[0]
            self.album = tags['TALB'].text[0]
            self.artist = ", ".join(tags['TPE1'].text)
            self.raw_art: bytes = tags['APIC:'].data
            self.art = self._data_to_qimage(self.raw_art)
            self.art_filetype = MP3CONV[tags['APIC:'].mime]
            
        else:
            raise RuntimeError(f"unknown file type {type(self.base).__name__}")

class BasicPlayer(QtCore.QObject):
    #signals
    media_changed = QtCore.Signal()
    media_paused = QtCore.Signal()
    media_played = QtCore.Signal()
    media_stopped = QtCore.Signal()
    media_shuffled = QtCore.Signal()
    media_unshuffled = QtCore.Signal()
    media_looped = QtCore.Signal()
    media_unlooped = QtCore.Signal()
    media_crossfade = QtCore.Signal()
    media_uncrossfade = QtCore.Signal()
    media_meta_ready = QtCore.Signal()
    media_finished = QtCore.Signal()
    queue_loaded = QtCore.Signal()
    
    media_quitafter_enabled = QtCore.Signal()
    media_quitafter_disabled = QtCore.Signal()
    
    finished = QtCore.Signal()
    request_quit = QtCore.Signal()
    progress = QtCore.Signal(float)
    
    def __init__(self, config: argparse.Namespace) -> None:
        super().__init__(None)
        self.config = config
    # setup
    @QtCore.Slot()
    def start(self) -> None: pass
    @QtCore.Slot()
    def event_loop(self) -> None: pass
    # info
    @abstractmethod
    def get_all_metadata(self) -> list[FileMetadata]: pass
    @abstractmethod
    def get_capabilities(self) -> Capabilities: pass
    @abstractmethod
    def get_current_metadata(self) -> FileMetadata: pass
    @abstractmethod
    def get_file_metadata(self, path: str | os.PathLike) -> FileMetadata: pass
    @abstractmethod
    def get_queue(self) -> list[pathlib.Path]: pass
    @abstractmethod
    @QtCore.Slot(None, result=bool)
    def get_shuffle(self) -> bool: pass
    @abstractmethod
    @QtCore.Slot(None, result=int)
    def get_current_length(self) -> int: pass
    @abstractmethod
    @QtCore.Slot(None, result=int)
    def get_playlist_size(self) -> int: pass
    @abstractmethod
    @QtCore.Slot(None, result=int)
    def get_current_position(self) -> int: pass
    @abstractmethod
    @QtCore.Slot(None, result=str)
    @abstractmethod
    def get_current_uri(self) -> str: pass
    @abstractmethod
    @QtCore.Slot(None, result=str)
    def get_current_art(self) -> str: pass
    @abstractmethod
    @QtCore.Slot(None, result=str)
    def get_current_state(self) -> str: pass #one of: standby, loading, playing, paused, stopped, ended, error. does not have to be perfectly accurate
    @abstractmethod
    @QtCore.Slot(None, result=str)
    def get_current_title(self) -> str: pass
    @abstractmethod
    @QtCore.Slot(None, result=str)
    def get_current_artist(self) -> str: pass
    @abstractmethod
    @QtCore.Slot(None, result=str)
    def get_current_album(self) -> str: pass
    @abstractmethod
    @QtCore.Slot(None, result=bool)
    def get_paused(self) -> bool: pass
    # set/control
    @abstractmethod
    @QtCore.Slot(int)
    def set_current_by_index(self, index: int) -> None: pass
    @abstractmethod
    @QtCore.Slot(str)
    def set_current_by_file(self, file: str) -> None: pass
    @abstractmethod
    @QtCore.Slot(str)
    def queue_by_file(self, file: str) -> None: pass
    @abstractmethod
    @QtCore.Slot(int)
    def queue_by_index(self, index: int) -> None: pass
    @abstractmethod
    @QtCore.Slot()
    def next(self) -> None: pass
    @abstractmethod
    @QtCore.Slot()
    def previous(self) -> None: pass
    @abstractmethod
    @QtCore.Slot(int)
    def seek(self, progress: int) -> None: pass
    @abstractmethod
    @QtCore.Slot(bool)
    def set_shuffle(self, state: bool) -> None: pass
    @abstractmethod
    @QtCore.Slot(bool)
    def set_loop(self, state: bool) -> None: pass
    @abstractmethod
    @QtCore.Slot(bool)
    def set_crossfade(self, state: bool) -> None: pass
    @abstractmethod
    @QtCore.Slot(bool)
    @QtCore.Slot()
    def set_playing(self, state: typing.Optional[bool] = None) -> None: pass #None = toggle, True should also unstop it
    @abstractmethod
    @QtCore.Slot(bool)
    @QtCore.Slot()
    def set_stopped(self, state: typing.Optional[bool] = None) -> None: pass #None = toggle
    @abstractmethod
    @QtCore.Slot()
    def quit(self) -> None: pass
    @QtCore.Slot()
    def quit_after_current(self) -> None: pass
    
        