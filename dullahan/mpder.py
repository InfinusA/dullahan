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
        self.file = mpdata['file']
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
        self.moveToThread(self.thread)
        self.thread.started.connect(self.request_thread)
    
    def disconnect(self):
        self.wrapper('disconnect')
        self.queue.put('QUIT')
        while not self.thread_has_quit:
            pass
        self.thread.quit()
        return
    
    def connect(self, *ignored, **ignored_) -> None:
        self.thread.start()
        self.wrapper('connect', *self.location)
    
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
                getattr(self.client, 'ping')() #override the dumb type error
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
    def find(self, tag: str, needle: str) -> list[dict[str, typing.Any]]: return self.wrapper('find', tag, needle) 
    def playlistfind(self, tag: str, needle: str) -> list[dict[str, typing.Any]]: return self.wrapper('playlistfind', tag, needle) 
    def next(self) -> None: self.wrapper('next')
    def previous(self) -> None: self.wrapper('previous')
    def seekcur(self, pos: float | str) -> None: self.wrapper('seekcur', str(pos))
    def pause(self, is_paused: bool) -> None: self.wrapper('pause', int(is_paused))
    def stop(self) -> None: self.wrapper('stop')
    def listmounts(self) -> list[dict[str, str]]: return self.wrapper('listmounts')
    def idle(self, *subsystems: str) -> list[str]: return self.wrapper('idle', *subsystems)
    def readpicture(self, url: str) -> dict[str, typing.Any]: return self.wrapper('readpicture', url)
    def prio(self, priority: int, start: int, end: typing.Optional[int] = None) -> None: self.wrapper('prio', priority, start, end)
    def prioid(self, priority: int, id_: int) -> None: self.wrapper('prioid', priority, id_)


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
        self.quitafter_enabled = False
        self.local_status = {
            'state': self.internal_state,
            'random': '1' if self.capabilities.shuffle else '0',
            'xfade': '1' if self.capabilities.crossfade else '0',
        }

    def relative_to_root(self, file: pathlib.Path) -> pathlib.Path | None:
        for root in self.roots:
            try:
                return file.relative_to(root)
            except ValueError:
                continue
        return None

    @QtCore.Slot()
    def start(self) -> None:
        self.client.clear()

        source = pathlib.Path(self.config.file)
        if not source.exists():
            raise FileNotFoundError(f"Cannot load file {source}")
        elif not self.relative_to_root(source):
            raise ValueError(f"File {source} is not inside of a MPD music directory")
        
        if source.is_dir() and source in self.roots:
            self.client.add('') #not sure if this works for mounted roots
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
                resp = self.event_client.idle()
            except mpd.ConnectionError:
                continue
            for event in resp:
                self.local_status = self.event_client.status()
                if event == 'player':
                    cs = self.event_client.currentsong()
                    if self.current_id != int(cs['id']):
                        if self.quitafter_enabled:
                            self.quit()
                        self.current_id = int(cs['id'])
                        self.media_finished.emit()
                        self.media_changed.emit()
                        self.media_meta_ready.emit()
                    state = self.local_status['state']
                    if self.current_state != state:
                        self.current_state = state
                        if state == 'play': self.media_played.emit()
                        elif state == 'pause': self.media_paused.emit()
                        elif state == 'stop': self.media_stopped.emit()
                elif event == 'options':
                    if int(self.local_status['repeat']):
                        self.media_looped.emit()
                    else:
                        self.media_unlooped.emit()
                    if int(self.local_status['random']):
                        self.media_shuffled.emit()
                    else:
                        self.media_unshuffled.emit()
                    if int(self.local_status['xfade']):
                        self.media_crossfade.emit()
                    else:
                        self.media_uncrossfade.emit()
        self.thread_exited = True
    
    def get_capabilities(self) -> basic_player.Capabilities: return self.capabilities
    def get_current_metadata(self) -> MPDMetadata:
        cs = self.client.currentsong()
        return self.get_file_metadata(cs['file'])
    def get_current_metadata_raw(self) -> dict[str, any]:
        return self.client.currentsong()
    def get_file_metadata(self, input: str | os.PathLike | dict, noart=False) -> MPDMetadata:
        if isinstance(input, (str, os.PathLike)):
            cs = self.client.find('file', str(input))[0]
        else:
            cs = input
        if not noart:
            pic_data = self.get_current_art().replace('file://', '', 1)
            if pic_data:
                return MPDMetadata(cs, open(pic_data, 'rb').read(), pic_data.split('.')[-1])
        return MPDMetadata(cs, None, None)
    def get_queue(self) -> typing.Generator[pathlib.Path, None, None]:
        for f in self.get_all_metadata():
            try:
                yield pathlib.Path(self.roots[0], f.file)
            except mpd.ConnectionError:
                pass
    def get_all_metadata(self) -> typing.Generator[MPDMetadata, None, None]:
        for i, f in enumerate(self.client.playlistinfo()):
            yield MPDMetadata(f, None, None)
    @QtCore.Slot(None, result=bool)
    def get_shuffle(self) -> bool: return self.local_status['random'] == '1'
    @QtCore.Slot(None, result=float)
    def get_current_length(self) -> float:
        return float(self.local_status.get('duration', 0))*1000
    @QtCore.Slot(None, result=int)
    def get_playlist_size(self) -> int:
        return self.local_status.get('playlistlength', 0)
    @QtCore.Slot(None, result=float)
    def get_current_position(self) -> float:
        return float(self.client.status()['elapsed'])*1000
    @QtCore.Slot(None, result=str)
    def get_current_uri(self, filename: typing.Optional[str] = None) -> str:
        return "file://"+str(pathlib.Path(self.roots[0], filename if filename else self.client.currentsong()['file']))
    @QtCore.Slot(None, result=str)
    def get_current_art(self) -> str:
        cs = self.client.currentsong()
        find_f = list(pathlib.Path(f"/tmp/dullahan/").glob(f"{cs['id']}.*"))
        if len(find_f) > 0 and find_f[0].exists():
            return str(find_f[0])
        else:
            try:
                from mutagen._file import File
                from mutagen.mp4 import MP4
                from mutagen.mp3 import MP3
                from mutagen.flac import FLAC
                
                dat = File(str(pathlib.Path(self.roots[0], cs['file'])))
                if not dat:
                    raise NotImplementedError
                if isinstance(dat, MP4):
                    pic_bin = bytes(dat.tags['covr'][0])
                    pic_tp = {13: 'jpg', 14: 'png'}[dat.tags['covr'][0].imageformat]
                elif isinstance(dat, MP3):
                    pic_bin = dat.tags['APIC:'].data
                    pic_tp = dat.tags['APIC:'].mime.split('/')[-1]
                elif isinstance(dat, FLAC):
                    pic_bin = dat.pictures[0].data
                    pic_tp = dat.pictures[0].mime.split('/')[-1]
                else:
                    raise NotImplementedError
            except (ImportError, NotImplementedError):
                pic = self.client.readpicture(cs['file'])
                pic_bin = pic['binary']
                pic_tp = pic['type']
            meta = MPDMetadata(cs, pic_bin, pic_tp.split('/')[-1])
            #f = pathlib.Path("/tmp/dullahan-tmp-art")
            f = pathlib.Path(f"/tmp/dullahan/{cs['id']}.{meta.art_filetype}")
            f.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            f.write_bytes(meta.raw_art)
            f.chmod(0o600)
            # for nf in pathlib.Path('/tmp/dullahan').iterdir():
            #     if nf != f:
            #         print("UNLINK", nf)
            #         nf.unlink(True)
            return str(f)
    @QtCore.Slot(None, result=str)
    def get_current_state(self) -> str:
        s = self.local_status['state']
        if s == 'play': return 'playing'
        if s == 'pause': return 'paused'
        if s == 'stop': return 'stopped'
        else: return 'error'
    @QtCore.Slot(None, result=str)
    def get_current_title(self) -> str:
        return self.client.currentsong()['title']
    @QtCore.Slot(None, result=str)
    def get_current_artist(self) -> str:
        return self.client.currentsong()['artist']
    @QtCore.Slot(None, result=str)
    def get_current_album(self) -> str:
        return self.client.currentsong()['album']
    @QtCore.Slot(None, result=bool)
    def get_paused(self) -> bool:
        return self.local_status['state'] == 'pause'
    # set/control
    @QtCore.Slot(int)
    def set_current_by_index(self, index: int) -> None: self.client.play(index)
    @QtCore.Slot(str)
    def set_current_by_file(self, file: str) -> None:
        relfile = pathlib.Path(file)
        if relfile.is_absolute():
            relfile = relfile.relative_to(self.roots[0])
        song_id = self.client.playlistfind('file', str(relfile))[0]['pos']
        self.client.play(song_id)
    @QtCore.Slot(int)
    def queue_by_index(self, index: int) -> None:
        self.client.prio(1, index)
    @QtCore.Slot(str)
    def queue_by_file(self, file: str) -> None:
        relfile = pathlib.Path(file)
        if relfile.is_absolute():
            relfile = relfile.relative_to(self.roots[0])
        song_id = self.client.playlistfind('file', str(relfile))[0]['id']
        self.client.prioid(1, song_id)
    @QtCore.Slot()
    def next(self) -> None: self.client.next()
    @QtCore.Slot()
    def previous(self) -> None: self.client.previous()
    @QtCore.Slot(int)
    def seek(self, progress: int) -> None: self.client.seekcur(progress/1000)
    @QtCore.Slot(bool)
    def set_shuffle(self, state: bool) -> None: self.client.random(state)
    @QtCore.Slot(bool)
    def set_loop(self, state: bool) -> None: self.client.repeat(state)
    @QtCore.Slot(bool)
    def set_crossfade(self, state: bool) -> None:
        self.client.crossfade(state and self.config.crossfade_length) #little bit of boolean logic
    @QtCore.Slot(bool)
    @QtCore.Slot()
    def set_playing(self, state: typing.Optional[bool] = None) -> None:
        if state is None:
            self.client.pause(self.local_status['state']!='pause')
        else:
            self.client.pause(not state)
    @QtCore.Slot(bool)
    @QtCore.Slot()
    def set_stopped(self, state: typing.Optional[bool] = None) -> None:
        state = state if state is not None else self.local_status['state']!='stop'
        if state:
            self.client.stop()
        else:
            self.client.play(self.client.currentsong()['pos'])
    @QtCore.Slot()
    def quit(self) -> None:
        self.thread_stopped = True
        self.client.stop()
        self.event_client.stop()
        self.client.clear()
        self.client.disconnect()
        self.event_client.disconnect()
        while not self.thread_exited:
            pass
        self.finished.emit()
    @QtCore.Slot()
    def quit_after_current(self) -> None:
        self.quitafter_enabled = True
        self.media_quitafter_enabled.emit()