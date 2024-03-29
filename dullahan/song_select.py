import pathlib
import sys
import mutagen
import mutagen.mp4
import mutagen._file
from . import basic_player
from PySide2 import QtCore, QtWidgets, QtGui

#def select_song(file_list: list[pathlib.Path]) -> pathlib.Path:
#    ss = SongSelect(file_list)
#    return ss.get_song()
class ETableView(QtWidgets.QTableView):
    def keyPressEvent(self, event):
        if event.type() == QtCore.QEvent.KeyPress and (event.key() == QtCore.Qt.Key_Return or event.key() == QtCore.Qt.Key_Enter):
            self.doubleClicked.emit(self.selectedIndexes()[0])
        super(ETableView, self).keyPressEvent(event)

class MetaParser(QtCore.QObject):
    finished = QtCore.Signal()
    progress = QtCore.Signal(int)
    
    def __init__(self, player: basic_player.BasicPlayer) -> None:
        self.player = player
        self.meta_list: list[dict] = []
        self.dead = False
        self.placeholder_art = QtGui.QImage(50, 50, QtGui.QImage.Format_Indexed8)
        self.placeholder_art.fill(QtGui.qRgb(50,50,50))
        super().__init__()
    
    def quit(self):
        self.dead = True
    
    def _data_to_qimage(self, data: str) -> QtGui.QImage:
        return QtGui.QImage.fromData(QtCore.QByteArray.fromRawData(data))
    
    def run(self):
        for index, meta in enumerate(self.player.get_all_metadata()):
            if self.dead:
                break
            self.progress.emit(index+1)
            self.meta_list.append(vars(meta))
        if not self.dead:
            self.finished.emit()

class SongSelect(QtCore.QObject):
    song_selected = QtCore.Signal(str)
    song_queued = QtCore.Signal(str)
    meta_loaded = QtCore.Signal()
    
    def __init__(self, player: basic_player.BasicPlayer) -> None:
        self.playlist_length = player.get_playlist_size()
        self.meta_list: list[dict] = []
        self.icns = []
        
        self.loader_thread = QtCore.QThread()
        self.loader = MetaParser(player)
        self.loader.moveToThread(self.loader_thread)
        self.loader.finished.connect(lambda: self.when_loaded())
        self.loader.progress.connect(lambda v: self.on_meta_progress(v))
        self.loader_thread.started.connect(self.loader.run)
        
        super().__init__()
        
        self.main = QtWidgets.QDialog()
        self.lmain = QtWidgets.QGridLayout(self.main)
        self.searchbox = QtWidgets.QLineEdit(self.main)
        self.searchbox.setPlaceholderText("Search")
        self.searchtimer = QtCore.QTimer()
        self.searchtimer.setInterval(0)
        self.searchtimer.timeout.connect(lambda: self.layout_songs(self.searchbox.text()))
        self.searchtimer.setSingleShot(True)
        #self.searchbox.textChanged.connect(self.filter_list)
        #self.searchbox.textChanged.connect(self.searchtimer.start)
        #self.searchbox.textChanged.connect(lambda: self.layout_songs(self.searchbox.text()))
        #sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        #self.searchbox.setSizePolicy(sizePolicy)
        self.lmain.addWidget(self.searchbox, 0, 0)
        
        self.songtable = ETableView(self.main)
        self.songtable.verticalHeader().hide()
        self.songtable.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.songtable.setSelectionBehavior(QtWidgets.QTableView.SelectRows)
        self.songtable.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.songtable.customContextMenuRequested.connect(self.right_click)
        self.songtable.doubleClicked.connect(self.on_selected)
        self.tablemodel = QtGui.QStandardItemModel(0, 3)
        self.tablemodel.setHorizontalHeaderLabels(["Title", "Artist", "Album"])
        self.filter = QtCore.QSortFilterProxyModel()
        self.filter.setSourceModel(self.tablemodel)
        self.filter.setFilterKeyColumn(-1)
        self.filter.setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.songtable.setModel(self.filter)
        self.songtable.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.songtable.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Interactive)
        self.songtable.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Interactive)
        self.lmain.addWidget(self.songtable, 1, 0)
        self.searchbox.textChanged.connect(self.filter.setFilterFixedString)
        
        self.loading = QtWidgets.QLabel(self.main)
        self.lmain.addWidget(self.loading, 2, 0)
    
    def right_click(self, pos):
        index = self.songtable.indexAt(pos)
        rightmenu = QtWidgets.QMenu()
        rightplay = QtWidgets.QAction("Play Song")
        queueplay = QtWidgets.QAction("Queue Song")
        rightmenu.addAction(rightplay)
        rightmenu.addAction(queueplay)
        rightplay.triggered.connect(lambda: self.on_selected(index))
        queueplay.triggered.connect(lambda: self.on_selected_queue(index))
        #TODO: This
        # if sys.platform == "linux":
        #     import subprocess
        #         if subprocess.run(["sh", "command", "-v", "xdg-open"]).returncode != 1:
        #         openfolder = QtWidgets.QAction("Open Containing Folder")
        #         rightmenu.addAction(openfolder)
        #         openfolder.triggered.connect(lambda: subprocess.Popen("xdg-open", ))
        rightmenu.exec_(self.songtable.viewport().mapToGlobal(pos))
    
    #def filter_list(self, rowId, row):
    #    e = self.meta_list[row.row()]
    #    return self.searchbox.text().lower() in (e['title']+str(e['file'])+e['artist']+e['album']).lower()
    
    def on_selected_queue(self, index: QtCore.QModelIndex):
        index = self.filter.mapToSource(index)
        title = self.tablemodel.item(index.row(), 0).text()
        artist = self.tablemodel.item(index.row(), 1).text()
        album = self.tablemodel.item(index.row(), 2).text()
        source = next(filter(lambda e: e['title'] == title and e['artist'] == artist and e['album'] == album, self.meta_list))
        self.song_queued.emit(str(source['file']))
        #self.main.hide() #TODO: Make this a config option
        
    def on_selected(self, index: QtCore.QModelIndex):
        index = self.filter.mapToSource(index)
        title = self.tablemodel.item(index.row(), 0).text()
        artist = self.tablemodel.item(index.row(), 1).text()
        album = self.tablemodel.item(index.row(), 2).text()
        source = next(filter(lambda e: e['title'] == title and e['artist'] == artist and e['album'] == album, self.meta_list))
        self.song_selected.emit(str(source['file']))
        self.main.hide()
    
    def quit(self):
        self.loader.quit()
        self.loader_thread.quit()
        
    def on_meta_progress(self, v):
        if self.main.isVisible:
            self.loading.setText(f"Loading... {v}/{self.playlist_length}")
    
    def when_loaded(self):
        self.meta_list = self.loader.meta_list
        self.songtable.setVisible(True)
        self.loading.setVisible(False)
        
        self.tablemodel.setRowCount(len(self.meta_list))
        self.icns = []
        for mindex, meta in enumerate(self.meta_list):
            #self.songtable.setRowHeight(mindex, 32)
            if 'art' in meta:
                i = QtGui.QPixmap(meta['art'])
                self.icns.append(i)
                # self.songtable.setCellWidget(mindex, 0, l)
                self.tablemodel.setItem(mindex, 0, QtGui.QStandardItem(i, meta['title']))
            else:
                self.tablemodel.setItem(mindex, 0, QtGui.QStandardItem(meta['title']))
            self.tablemodel.setItem(mindex, 1, QtGui.QStandardItem(meta['artist']))
            self.tablemodel.setItem(mindex, 2, QtGui.QStandardItem(meta['album']))
    
    def update_metadata(self):
        self.loading.setText(f"Loading... 0/{self.playlist_length}")
        self.loader_thread.start()
        self.songtable.setVisible(False)
        self.loading.setVisible(True)
    
    @QtCore.Slot()
    def show(self):
        self.searchbox.setText("")
        self.layout_songs("")
        self.main.show()
    
    def layout_songs(self, filter_q: str):
        return
        if len(filter_q) == 0:
            return
        for song_index, song_label in enumerate(self.llist):
            e = self.meta_list[song_index]
            is_ok = filter_q.lower() in (e['title']+str(e['file'])+e['artist']+e['album']).lower()
            song_label.setVisible(is_ok)
            
    
    
            
