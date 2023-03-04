import argparse
import os
import pathlib
import random
import time
import typing
from PySide2 import QtCore, QtGui
from . import basic_player
import mpd
import uuid
import queue

class MPDMetadata(basic_player.FileMetadata):
    def __init__(self, mpdata: dict[str, typing.Any], art_data: typing.Optional[bytes] = None, art_filetype: typing.Optional[str] = None) -> None:
        self.is_quick = bool(art_data)
        self.title = mpdata['title']
        self.album = mpdata['album']
        self.artist = mpdata['artist']
        self.raw_art = art_data if art_data else b''
        self.art = self._data_to_qimage(self.raw_art) if self.raw_art else None
        self.art_filetype = self.findtype(self.raw_art[:20]) if self.raw_art and not art_filetype else (art_filetype if art_filetype else None)
    
    def findtype(self, first20: bytes):
        if first20[1:4] == b'PNG':
            return 'png'
        elif first20[6:10] == b'JFIF':
            return 'jpg'
        else:
            raise RuntimeError("unsupported image format")
    
    def parse(self): return

class ThreadSafeMPD(QtCore.QObject):
    '''A thread-safe wrapper around the python-mpd2 library using queues'''
    def __init__(self, host=None, port=None) -> None:
        super().__init__(None)
        self.location = (host, port)
        self.client = mpd.MPDClient()
        self.queue: queue.Queue[dict[typing.Any, typing.Any] | str] = queue.Queue()
        self.thread_has_quit = False
        self.responses = {}
        self.thread = QtCore.QThread()
        self.thread.started.connect(self.request_thread)
    
    def disconnect(self):
        self.wrapper('disconnect')
        self.queue.put('QUIT')
        while not self.thread_has_quit:
            pass
        self.thread.quit()
        return
    
    def connect(self, *ignored, **ignored_) -> None:
        self.wrapper('connect', *self.location)
        self.thread.start()
    
    def request_thread(self):
        while True:
            item = self.queue.get()
            if isinstance(item, str):
                if item == 'QUIT':
                    break
                else:
                    raise RuntimeError(f"unsupported command: {item}")
            
            cmd: str = item['cmd']
            request_id: str = item['id']
            args: list[typing.Any] = item['args']
            kwargs: dict[typing.Any, typing.Any] = item['kwargs']

            try:
                self.client.ping()
            except mpd.ConnectionError:
                try:
                    self.client.disconnect()
                except BrokenPipeError:
                    try:
                        self.client.disconnect()
                    except:
                        pass
                except:
                    pass
                self.client.connect(*self.location)
            
            try:
                res = getattr(self.client, cmd)(*args, **kwargs)
            except mpd.ConnectionError as e:
                if e.args[0] != "Already connected":
                    raise e
                else:
                    res = None
            except Exception as e:
                res = e
            self.responses[request_id] = res
        self.thread_has_quit = True
    
    def wrapper(self, cmd, *args, **kwargs) -> typing.Any:
        req_id = str(uuid.uuid4())
        self.queue.put({
            'cmd': cmd,
            'args': args,
            'kwargs': kwargs,
            'id': req_id
        })
        while req_id not in self.responses:
            time.sleep(0.1)
        res = self.responses[req_id]
        if isinstance(res, Exception):
            raise res
        return res
    
    def update(self) -> None: self.wrapper('update')
    def clear(self) -> None: self.wrapper('clear')
    def consume(self, state: bool) -> None: self.wrapper('clear', int(state))
    def random(self, state: bool) -> None: self.wrapper('random', int(state))
    def repeat(self, state: bool) -> None: self.wrapper('repeat', int(state))
    def crossfade(self, duration: int) -> None: self.wrapper('crossfade', duration)
    def add(self, uri: str) -> None: self.wrapper('add', uri)
    def playid(self, songid: int) -> None: self.wrapper('playid', songid)
    def playlistinfo(self) -> list[dict[str, typing.Any]]: return self.wrapper('playlistinfo')
    def currentsong(self) -> dict[str, typing.Any]: return self.wrapper('currentsong')
    def status(self) -> dict[str, typing.Any]: return self.wrapper('status')
    def play(self, pos: int) -> None: self.wrapper('play', pos)
    def playlistfind(self, tag: str, needle: str) -> dict[str, typing.Any]: return self.wrapper('playlistfind', tag, needle) 
    def next(self) -> None: self.wrapper('next')
    def previous(self) -> None: self.wrapper('previous')
    def seekcur(self, pos: float | str) -> None: self.wrapper('seekcur', str(pos))
    def pause(self, is_paused: bool) -> None: self.wrapper('pause', is_paused)
    def stop(self) -> None: self.wrapper('stop')
    def listmounts(self) -> list[dict[str, str]]: return self.wrapper('listmounts')
    def idle(self, *subsystems: str) -> list[str]: return self.wrapper('idle', *subsystems)
    def readpicture(self, url: str) -> dict[str, typing.Any]: return self.wrapper('readpicture', url)


class MPDPlayer(basic_player.BasicPlayer):
    def __init__(self, config: argparse.Namespace) -> None:
        super().__init__(config)
        self.capabilities = basic_player.Capabilities(loop=True, shuffle=True, crossfade=True)
        self.client = ThreadSafeMPD(config.host, config.port)
        self.event_client = ThreadSafeMPD(config.host, config.port)
        self.client.client.timeout = 5
        self.client.connect()
        self.event_client.connect()
        self.client.update()
        self.roots = [pathlib.Path(m['storage']) for m in self.client.listmounts()]

        self.current_id = -1

        self.thread_stopped = self.thread_exited = self.running = False
        self.internal_state = 'stop'

    def relative_to_root(self, file: pathlib.Path) -> pathlib.Path | None:
        for root in self.roots:
            try:
                return file.relative_to(root)
            except ValueError:
                continue
        return None

    @QtCore.Slot()
    def start(self) -> None:
        source = pathlib.Path(self.config.file)
        if not source.exists():
            raise FileNotFoundError(f"Cannot load file {source}")
        if not self.relative_to_root(source):
            raise ValueError(f"File {source} is not inside of a MPD music directory")
        if source.is_dir() and source in self.roots:
            self.client.add("") #not sure if this works for mounted roots
        elif source.is_dir():
            self.client.add(str(self.relative_to_root(source)))
        else:
            self.client.add(str(source))
        
        self.queue_loaded.emit()
        self.current_id = random.choice(self.client.playlistinfo())['id']
        self.client.playid(self.current_id)
        self.media_changed.emit()
        self.media_played.emit()
        self.current_state = 'play'
        self.running = True
    
    @QtCore.Slot()
    def event_loop(self) -> None:
        while not self.thread_stopped:
            if not self.running:
                time.sleep(0.1)
            try:
                resp = self.infoclient.idle()
            except mpd.ConnectionError:
                continue
            for event in resp:
                if event == 'player':
                    if self.current_id != int(self.infoclient.currentsong()['id']):
                        self.current_id = int(self.infoclient.currentsong()['id'])
                        self.media_finished.emit()
                        self.media_changed.emit()
                        self.media_meta_ready.emit()
                    state = self.infoclient.status()['state']
                    if self.current_state != state:
                        self.current_state = state
                        if state == 'play': self.media_played.emit()
                        elif state == 'pause': self.media_paused.emit()
                        elif state == 'stop': self.media_stopped.emit()
                elif event == 'options':
                    status = self.infoclient.status()
                    if int(status['repeat']):
                        self.media_looped.emit()
                    else:
                        self.media_unlooped.emit()
                    if int(status['random']):
                        self.media_shuffled.emit()
                    else:
                        self.media_unshuffled.emit()
                    if int(status['xfade']):
                        self.media_crossfade.emit()
                    else:
                        self.media_uncrossfade.emit()
        self.thread_exited = True
    
    def get_capabilities(self) -> basic_player.Capabilities: return self.capabilities
    def get_current_metadata(self) -> MPDMetadata:
        cs = self.client.currentsong()
        return self.get_file_metadata(cs['file'])
    def get_file_metadata(self, input: str | os.PathLike | dict) -> MPDMetadata:
        if isinstance(input, (str, os.PathLike)):
            cs = self.client.find('file', str(input))
        else:
            cs = input
        pic_data = self.client.readpicture(cs['file'])
        if pic_data:
            return MPDMetadata(cs, pic_data['binary'], pic_data['type'].split('/')[-1])
        else:
            return MPDMetadata(cs)
    def get_queue(self) -> list[pathlib.Path]:
        try:
            return [pathlib.Path(self.root, f['file']) for f in self.client.playlistinfo()]
        except mpd.ConnectionError:
            return []
    def get_all_metadata(self) -> list[MPDMetadata]:
        