import argparse
import os
import pathlib
import queue
import random
import time
import typing
from . import basic_player
import mpd
#import musicpd as mpd
from PySide2 import QtCore, QtGui

class PointlessError(Exception):
    pass

#pretty useless rn
class MPDMetadata(basic_player.FileMetadata):
    def __init__(self, meta: dict | str | os.PathLike, *, client=None, fast=False) -> None:
        self.client = client
        print(meta)
        if isinstance(meta, (str, os.PathLike)):
            meta = self.client.find('file', meta)[0]
        self.title = meta['title']
        self.album = meta['album']
        self.artist = meta['artist']
        self.placeholder_art = QtGui.QImage(256, 256, QtGui.QImage.Format_Indexed8)
        self.placeholder_art.fill(QtGui.qRgb(50,50,50))
        self.art = self.placeholder_art
        self.art_filetype = ''
        self.raw_art = b''
        self.fast = fast
        self.meta = meta

    def findtype(self, first20: bytes):
        if first20[1:4] == b'PNG':
            return 'png'
        elif first20[6:10] == b'JFIF':
            return 'jpg'
        else:
            raise RuntimeError("unsupported image format")

    
    def parse(self):
        if self.fast:
            return
        try:
            self.raw_art = self.client.readpicture(self.meta['file'])['binary']
            self.art = self._data_to_qimage(self.raw_art)
        except:
            self.art = self.placeholder_art
        self.art_filetype = self.findtype(self.raw_art[:20])

class ThreadedMPD(QtCore.QObject):
    '''
    Absurdly scuffed wrapper around the mpd client that makes it thread-safe with queues
    '''
    def __init__(self, socket=None, port=None, *args, **kwargs) -> None:
        super().__init__()
        self.__player = mpd.MPDClient()
        self.__socket = socket
        self.__port = port
        self.__queue = queue.Queue()
        self.__thread = QtCore.QThread()
        self.__resps = {}
        self.moveToThread(self.__thread)
        self.__thread.started.connect(self.threadloop)
        self.__thread.start()
        self.finished = False
    
    def __connect(self):
        return self.__player.connect(self.__socket, self.__port)
    
    def threadloop(self):
        while True:
            v = self.__queue.get(True, None)
            if v == 'QUIT':
                break
            #print("proc", v)
            cmd = v['cmd']
            args = v['args']
            kwargs = v['kwargs']
            retno = v['retno']
            #check if connected and reconnect if not
            try:
                self.__player.ping()
            except mpd.ConnectionError:
                try:
                    self.__player.disconnect()
                except BrokenPipeError:
                    try:
                        self.__player.disconnect()
                    except:
                        pass
                except:
                    pass
                self.__player.connect(self.__socket, self.__port)
            try:
                res = cmd(*args, **kwargs)
            except mpd.ConnectionError as e:
                if e.args[0] != "Already connected":
                    raise e
                else:
                    res = None
            except Exception as e:
                res = e
            self.__resps[retno] = res
            #print("done", retno)
        self.finished = True
    
    def player_override(self, func):
        def d(*args, **kwargs):
            ret = f"{time.time()}_{random.random()}"
            d = {
                'cmd': func,
                'args': args,
                'kwargs': kwargs,
                'retno': ret
            }
            self.__queue.put(d)
            while ret not in self.__resps:
                time.sleep(0.5)
            v = self.__resps[ret]
            if isinstance(v, Exception):
                raise v
            self.__resps.pop(ret)
            return v
        return d
    
    def quit_threadedmpv(self):
        self.__queue.put("QUIT")
        while not self.finished:
            pass
        self.__thread.quit()

    def __getattribute__(self, __name: str) -> typing.Any:
        if __name in ['connect']:
            return self.__getattr__(__name)
        return super().__getattribute__(__name)
    
    def __getattr__(self, __name: str) -> typing.Any:
        #print("fetch", __name)
        if __name in ['connect']:
            return self.__connect
        elif callable(getattr(self.__player, __name)):
            return self.player_override(getattr(self.__player, __name))
        elif getattr(self.__player, __name):
            return getattr(self.__player, __name)
        else:
            return super().__getattribute__(self, __name)

class MPDPlayer(basic_player.BasicPlayer):
    def __init__(self, config: argparse.Namespace) -> None:
        super().__init__(config)
        self.capabilities = basic_player.Capabilities(loop=True, shuffle=True, crossfade=True)
        self.client = ThreadedMPD('/run/user/1000/mpd/socket')
        self.client.timeout = 5
        self.client.connect()
        self.client.update()
        self.running = False
        self.root = pathlib.Path(next(filter(lambda d: d['mount'] == '', self.client.listmounts()))['storage']).expanduser().resolve() #TODO: multiple music directories
        
        self.thread_finished = False
        self.thread_exited = False
        self.current_id = -1
        
    # setup
    @QtCore.Slot()
    def start(self) -> None:
        real_source = pathlib.Path(self.config.file)
        #TODO: handle erroneous additions
        self.client.clear()
        self.client.consume(0)
        self.client.random(int(self.config.shuffle))
        self.client.repeat(int(self.config.loop))
        self.client.crossfade(int(self.config.crossfade_length))

        source = pathlib.Path(self.config.file).expanduser().resolve()
        if not source.relative_to(self.root):
            raise RuntimeError(f"Source file/folder {source} is not in mpd's music directory")
        if source.is_file():
            self.client.add(str(source))
        elif source.is_dir():
            if source == self.root:
                self.client.add("")
            else:
                self.client.add(str(source.relative_to(self.root)))
        else:
            raise RuntimeError(f"Source must be a file or folder to play")

        self.queue_loaded.emit()
        
        self.client.playid(random.choice(self.client.playlistinfo())['id'])
        self.media_changed.emit()
        self.media_played.emit()
        self.current_id = int(self.client.currentsong()['id'])
        self.current_state = 'play'
        self.running = True
        
    @QtCore.Slot()
    def event_loop(self) -> None:
        self.infoclient = mpd.MPDClient()
        self.infoclient.connect('/run/user/1000/mpd/socket')
        while not self.thread_finished:
            if self.running:
                try:
                    resl = self.infoclient.idle()
                except mpd.ConnectionError:
                    continue
                for res in resl:
                    if res == 'player':
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
                    
                    elif res == 'options':
                        #we cant tell so just emit them all
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

    # info
    def get_capabilities(self) -> basic_player.Capabilities: return self.capabilities
    def get_current_metadata(self) -> basic_player.FileMetadata:
        return basic_player.FileMetadata(pathlib.Path(self.root, self.client.currentsong()['file']))
        #return MPDMetadata(self.client.currentsong()['file'], self.root, self.client)
    def get_file_metadata(self, path: str | os.PathLike) -> basic_player.FileMetadata:
        path = pathlib.Path(path)
        if path.is_absolute():
            path = path.relative_to(self.root)
        return MPDMetadata(path, client=self.client)
    
    def get_queue(self) -> list[pathlib.Path]:
        try:
            return [pathlib.Path(self.root, f['file']) for f in self.client.playlistinfo()]
        except mpd.ConnectionError:
            return []
    
    def get_all_metadata(self) -> list[MPDMetadata]:
        return [MPDMetadata(e, fast=True) for e in self.client.playlistinfo()]

    @QtCore.Slot(None, result=bool)
    def get_shuffle(self) -> bool: return self.client.status()['random'] == '1'
    @QtCore.Slot(None, result=float)
    def get_current_length(self) -> float:
        return float(self.client.status()['duration'])*1000
    @QtCore.Slot(None, result=float)
    def get_current_position(self) -> float:
        return float(self.client.status()['elapsed'])*1000
    @QtCore.Slot(None, result=str)
    def get_current_uri(self) -> str:
        return str(pathlib.Path(self.root, self.client.currentsong()['file']))
    @QtCore.Slot(None, result=str)
    def get_current_art(self) -> str:
        find_f = list(pathlib.Path(f"/tmp/dullahan/").glob(f"{self.client.currentsong()['id']}.*"))
        if len(find_f) > 0 and find_f[0].exists():
            return str(find_f[0])
        else:
            meta = self.get_file_metadata(self.client.currentsong()['file'])
            f = pathlib.Path(f"/tmp/dullahan/{self.client.currentsong()['id']}.{meta.art_filetype}")
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(meta.raw_art)
            for nf in pathlib.Path('/tmp/dullahan').iterdir():
                if nf != f:
                    nf.unlink(True)
            return str(f)
        
    @QtCore.Slot(None, result=str)
    def get_current_state(self) -> str:
        s = self.client.status()['state']
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
        return self.client.status()['state'] == 'pause'
    # set/control
    @QtCore.Slot(int)
    def set_current_by_index(self, index: int) -> None: self.client.play(index)
    @QtCore.Slot(str)
    def set_current_by_file(self, file: str) -> None:
        relfile = pathlib.Path(file)
        if relfile.is_absolute():
            relfile = relfile.relative_to(self.root)
        song_id = self.client.playlistfind('file', str(relfile))[0]['pos']
        self.client.play(song_id)
    @QtCore.Slot()
    def next(self) -> None: self.client.next()
    @QtCore.Slot()
    def previous(self) -> None: self.client.previous()
    @QtCore.Slot(int)
    def seek(self, progress: int) -> None: self.client.seekcur(progress/1000)
    @QtCore.Slot(bool)
    def set_shuffle(self, state: bool) -> None: self.client.random(int(state))
    @QtCore.Slot(bool)
    def set_loop(self, state: bool) -> None: self.client.repeat(int(state))
    @QtCore.Slot(bool)
    def set_crossfade(self, state: bool) -> None:
        self.client.crossfade(state and self.config.crossfade_length) #little bit of boolean logic
    @QtCore.Slot(bool)
    @QtCore.Slot()
    def set_playing(self, state: typing.Optional[bool] = None) -> None:
        if state is None:
            self.client.pause(int(self.client.status()['state']!='pause'))
        else:
            self.client.pause(int(not state))
    @QtCore.Slot(bool)
    @QtCore.Slot()
    def set_stopped(self, state: typing.Optional[bool] = None) -> None:
        state = state if state is not None else self.client.status()['state']!='stop'
        if state:
            self.client.stop()
        else:
            self.client.play()
    @QtCore.Slot()
    def quit(self) -> None:
        self.thread_finished = True
        self.client.stop()
        self.client.close()
        self.client.quit_threadedmpv()
        while not self.thread_exited:
            pass
        self.finished.emit()