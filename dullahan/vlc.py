import copy
import pathlib
import random
import typing
from PySide2 import QtCore, QtWidgets
import vlc
class Player(QtCore.QObject):
    media_changed = QtCore.Signal()
    media_paused = QtCore.Signal()
    media_played = QtCore.Signal()
    media_stopped = QtCore.Signal()
    media_shuffled = QtCore.Signal()
    media_unshuffled = QtCore.Signal()
    media_meta_ready = QtCore.Signal()
    media_finished = QtCore.Signal()
    queue_loaded = QtCore.Signal()

    finished = QtCore.Signal()
    progress = QtCore.Signal(float)

    def __init__(self, config, app: QtWidgets.QApplication, inst: vlc.Instance, parent=None) -> None:
        super().__init__(parent)
        self.app = app
        self.config = config
        self.instance = inst
        self.player: vlc.MediaPlayer = self.instance.media_player_new()
        self.queue = []
        self.original_queue = []
        self.queue_position = 0
        self.running = False
        self.m_shuffled = False
        self.thread_finished = False
        self.thread_exited = False
        self.meta_announced = False
        
        self.media_changed.connect(lambda: self._reset_meta_flag())
        self.media_changed.connect(lambda: self.player.get_media().parse_with_options(0|1|2|4|8, 0))
    
    # internal functions

    def _reset_meta_flag(self):
        self.meta_announced = False

    def _get_media(self, filename: str) -> vlc.Media:
        media: vlc.Media = vlc.Media(filename)
        media.parse_async()
        return media
    
    #initialization functions
    
    def load_queue(self, queue: typing.MutableSequence):
        self.queue = copy.deepcopy(queue)
        self.original_queue = copy.deepcopy(queue)
        self.queue_loaded.emit()
    
    
    def start_player(self) -> None:
        self.player.set_media(self._get_media(self.queue[self.queue_position]))
        self.player.play()
        self.media_changed.emit()
        self.running = True
    
    def run(self): #event loop
        while not self.thread_finished:
            if self.running:
                try:
                    pass
                    #self.progress.emit(self.player.get_time())
                except:
                    pass
                if not self.meta_announced and vlc.libvlc_media_get_parsed_status(self.player.get_media()) == 4:
                    self.media_meta_ready.emit()
                    self.meta_announced = True
                if self.player.get_state() == vlc.State.Ended:  # type: ignore
                    self.media_finished.emit()
                    self.media_next()
        self.thread_exited = True
    
    # Media functions

    # get info (including meta)
    
    def get_file_list(self) -> list[pathlib.Path]:
        return [pathlib.Path(e).resolve() for e in self.original_queue]

    @QtCore.Slot(int)
    def get_length(self) -> int:
        return self.player.get_length()
    
    @QtCore.Slot(str)
    def get_uri(self) -> str:
        new_media = self.player.get_media()
        if not new_media:
            return ""
        return self.player.get_media().get_mrl()  # type: ignore
    
    @QtCore.Slot(str)
    def get_art_uri(self) -> str:
        new_media = self.player.get_media()
        if not new_media:
            return ""
        return self.player.get_media().get_meta(vlc.Meta.ArtworkURL)  # type: ignore
    
    @QtCore.Slot(str)
    def get_state(self) -> str:
        val = vlc.State.value
        return ["nothing", "opening", "buffering", "playing", "paused", "stopped", "ended", "error"][int.from_bytes(self.player.get_state(), byteorder='little')]
    
    @QtCore.Slot(None)
    def set_song_by_index(self, index: int) -> None:
        self.queue_position = index
        self.player.set_media(self._get_media(self.queue[self.queue_position]))
        self.player.play(); #self.media_played.emit()
        self.media_changed.emit()
        
    @QtCore.Slot(None)
    def set_song_by_file(self, file: str) -> None:
        index = self.queue.index(file)
        self.queue_position = index
        self.player.set_media(self._get_media(self.queue[self.queue_position]))
        self.player.play(); #self.media_played.emit()
        self.media_changed.emit()
    
    @QtCore.Slot(None)
    def media_next(self) -> None:
        queue_position = 0
        if self.queue_position + 1 < len(self.queue):
            queue_position = self.queue_position + 1
        elif True or config.loop:
            queue_position = 0
        self.set_song_by_index(queue_position)
        
    @QtCore.Slot(None)
    def media_prev(self) -> None:
        queue_position = 0
        if self.queue_position - 1 >= 0:
            queue_position = self.queue_position - 1
        elif True or config.loop:
            queue_position = 0
        self.set_song_by_index(queue_position)
    
    @QtCore.Slot(None)
    def media_seek(self, time: int) -> None:
        self.player.set_time(time)

    @QtCore.Slot(bool)
    def get_shuffle(self) -> bool:
        return self.m_shuffled

    @QtCore.Slot(None)
    def set_shuffle(self, is_shuffled: bool) -> None:
        if is_shuffled:
            self.enable_shuffle()
        else:
            self.disable_shuffle()
            
    @QtCore.Slot(None)
    def enable_shuffle(self) -> None:
        current = copy.deepcopy(self.queue[self.queue_position])
        self.queue = copy.deepcopy(self.original_queue)
        random.shuffle(self.queue)
        self.queue_position = self.queue.index(current)
        self.m_shuffled = True
        self.media_shuffled.emit()
    
    @QtCore.Slot(None)
    def disable_shuffle(self) -> None:
        current = copy.deepcopy(self.queue[self.queue_position])
        self.queue = copy.deepcopy(self.original_queue)
        self.queue_position = self.queue.index(current)
        self.m_shuffled = False
        self.media_unshuffled.emit()
    
    @QtCore.Slot(str)
    def get_title(self) -> str:
        new_media = self.player.get_media()
        if not new_media:
            return ""
        return new_media.get_meta(0)
    
    @QtCore.Slot(str)
    def get_artist(self) -> str:
        new_media = self.player.get_media()
        if not new_media:
            return ""
        return new_media.get_meta(1)
        
    @QtCore.Slot(str)
    def get_album(self) -> str:
        new_media = self.player.get_media()
        if not new_media:
            return ""
        return new_media.get_meta(4)

    @QtCore.Slot(int)
    def get_position(self) -> int:
        return int(self.player.get_time())
    
    @QtCore.Slot(bool)
    def is_paused(self) -> bool:
        return not self.player.is_playing()
        
    @QtCore.Slot(None)
    def media_toggle_playing(self) -> None:
        self.player.pause() #toggle pause, not set
        if not self.player.is_playing():
            self.media_played.emit()
        else:
            self.media_paused.emit()
    
    @QtCore.Slot(None)
    def media_pause(self) -> None:
        self.player.set_pause(True)
        self.media_paused.emit()
    
    @QtCore.Slot(None)
    def media_play(self) -> None:
        self.player.set_pause(False)
        self.media_played.emit()
    
    @QtCore.Slot(None)
    def media_stop(self) -> None:
        #self.player.stop() #stop is funky, pause and reset time
        self.media_pause()
        self.player.set_time(0)
        self.media_stopped.emit()
    
    @QtCore.Slot(None)
    def quit(self):
        self.thread_finished = True
        self.player.stop()
        while not self.thread_exited:
            pass
        self.finished.emit()