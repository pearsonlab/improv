import datetime
import logging
import pickle
import sys
import time
from math import floor
from pathlib import Path

import numpy as np
import pyqtgraph
import tifffile as tiff
from PyQt5 import QtGui, QtCore, QtWidgets
from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QMessageBox, QFileDialog
from pyqtgraph import EllipseROI, PolyLineROI

from nexus.module import Spike
from .widgets import PathSlider
from visual import rasp_ui_huge as rasp_ui

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

#TODO: Behavioral stimuli/timing as input, dynamic calculation of tuning curves
#NOTE: GUI only gives comm signals to Nexus, does not receive any. Visual serves that role
#TODO: Add ability to receive signals like pause updating ...?

class FrontEnd(QtGui.QMainWindow, rasp_ui.Ui_MainWindow):

    def __init__(self, visual, comm, parent=None):
        ''' Setup GUI
            Setup and start Nexus controls
        '''
        self.visual = visual #Visual class that provides plots and images
        self.comm = comm #Link back to Nexus for transmitting signals

        self.total_times = []

        pyqtgraph.setConfigOption('background', QColor(100, 100, 100))
        super(FrontEnd, self).__init__(parent)
        self.setupUi(self)
        self.extraSetup()
        pyqtgraph.setConfigOptions(leftButtonPan=False)

        self.customizePlots()

        self.pushButton_3.clicked.connect(_call(self._runProcess)) #Tell Nexus to start
        self.pushButton_3.clicked.connect(_call(self.update)) #Update front-end graphics
        self.pushButton_2.clicked.connect(_call(self._setup))
        self.pushButton.clicked.connect(_call(self._loadParams)) #File Dialog, then tell Nexus to load tweak
        self.checkBox.stateChanged.connect(self.update) #Show live front-end updates
        

        self.rawplot_2.getImageItem().mouseClickEvent = self.mouseClick #Select a neuron

        self.plots = {'raw': self.rawplot, 'color': self.rawplot_2, 'image': self.rawplot_3}
        self.currentImages = dict.fromkeys(self.plots)
        self.firstImage = {key: True for key in self.plots.keys()}

        self.currentCurves = dict.fromkeys({'Cx', 'C', 'Cpop', 'tune'})
        self.thresh_r = np.array(0)

    def extraSetup(self):
        self.slider2 = PathSlider(self.frame_3, minSize=150, minimum=0, maximum=360)
        #self.slider2 = QRangeSlider(self.frame_3)
        self.slider2.setGeometry(QtCore.QRect(20, 100, 155, 50))
        # self.slider2.setGeometry(QtCore.QRect(150, 0, 200, 200))
        # self.slider2.setObjectName("slider2")
        self.slider2.rangeChanged.connect(self.updatePhaseRange) #Threshold for angular selection
        self.slider2.threshChanged.connect(self.updateThreshold)

    def customizePlots(self):
        self.checkBox.setChecked(True)
        self.draw = True

        #init line plot
        self.flag = True

        self.c1 = self.grplot.plot(clipToView=True)
        self.c2 = self.grplot_2.plot()
        grplot = [self.grplot, self.grplot_2]
        for plt in grplot:
            plt.getAxis('bottom').setTickSpacing(major=50, minor=50)
        #    plt.setLabel('bottom', "Frames")
        #    plt.setLabel('left', "Temporal traces")
        self.updateLines()
        self.activePlot = 'r'

        #polar plotting
        self.num = 21
        theta = np.linspace(0, 2*np.pi, self.num-1)
        theta = np.append(theta,0)
        self.theta = theta
        radius = np.zeros(self.num)
        self.thresh_r = radius + 1
        x = radius * np.cos(theta)
        y = radius * np.sin(theta)

        #polar plots
        polars = [self.grplot_3, self.grplot_4, self.grplot_5]
        for polar in polars:
            polar.setAspectLocked(True)

            # Add polar grid lines
            polar.addLine(x=0, pen=0.2)
            polar.addLine(y=0, pen=0.2)
            for r in range(0, 4, 1):
                circle = pyqtgraph.QtGui.QGraphicsEllipseItem(-r, -r, r*2, r*2)
                circle.setPen(pyqtgraph.mkPen(0.1))
                polar.addItem(circle)
            polar.hideAxis('bottom')
            polar.hideAxis('left')
        
        self.polar1 = polars[0].plot()
        self.polar2 = polars[1].plot()
        self.polar1.setData(x, y)
        self.polar2.setData(x, y)

        for r in range(2, 12, 2):
                circle = pyqtgraph.QtGui.QGraphicsEllipseItem(-r, -r, r*2, r*2)
                circle.setPen(pyqtgraph.mkPen(0.1))
                polars[2].addItem(circle)
        self.polar3 = polars[2].plot()

        #sliders
        self.slider.setMinimum(0)
        self.slider.setMaximum(12)

        #videos
        #self.rawplot.ui.histogram.vb.disableAutoRange()
        self.rawplot.ui.histogram.vb.setLimits(yMin=-0.1, yMax=200) #0-255 needed, saturated here for easy viewing

    def _loadParams(self):
        ''' Button event to load parameters from file
            File location determined from user input
            Throws FileNotFound error
        '''
        fname = QFileDialog.getOpenFileName(self, 'Open file', '/home')
            #TODO: make default home folder system-independent
        try:
            self._loadTweak(fname[0])
        except FileNotFoundError as e:
            logger.error('File not found {}'.format(e))
            #raise FileNotFoundError
    
    def _runProcess(self):
        '''Run ImageProcessor in separate thread
        '''
        #self.flag = True
        self.comm.put(Spike.run())
        logger.info('-------------------------   put run in comm')
        #TODO: grey out button until self.t is done, but allow other buttons to be active

    def _setup(self):
        self.comm.put(Spike.setup())
        self.visual.setup()

    def _loadTweak(self, file):
        self.comm.put(['load', file])

    def update(self):
        ''' Update visualization while running
        '''
        t = time.time()
        #start looking for data to display
        self.visual.getData()
        #logger.info('Did I get something:', self.visual.Cx)

        if self.draw:
            #plot lines
            self.updateLines()

            #plot video
            self.updateVideo()

        #re-update
        if self.checkBox.isChecked():
            self.draw = True
        else:
            self.draw = False    
        self.visual.draw = self.draw
            
        QtCore.QTimer.singleShot(10, self.update)
        
        self.total_times.append(time.time()-t)
    
    def updateVideo(self):
        ''' TODO: Bug on clicking ROI --> trace and report to pyqtgraph
        '''
        try:
            self.currentImages['raw'], self.currentImages['color'] = self.visual.getFrames()
            self.currentImages['image'] = self.visual.plotThreshFrame(self.thresh_r)
        except Exception as e:
            logger.error('Error in FrontEnd update Video:  {}'.format(e))

        for name, plot in self.plots.items():
            if self.currentImages[name] is not None:
                if self.firstImage[name]:
                    plot.setImage(self.currentImages[name], autoHistogramRange=False)
                    plot.tVals = None  # Prevent AttrErr upon invocation of ROI in color.
                    self.firstImage[name] = False
                else:
                    view: pyqtgraph.ViewBox = plot.getView()  # Preserve zoom and translation in ViewBox
                    state = view.getState()
                    plot.setImage(self.currentImages[name], autoHistogramRange=False)
                    view.setState(state)

    def updateLines(self):
        ''' Helper function to plot the line traces
            of the activity of the selected neurons.
            TODO: separate updates for each plot?
        '''
        #t = time.time()
        penW=pyqtgraph.mkPen(width=2, color='w')
        penR=pyqtgraph.mkPen(width=2, color='r')
        C = None
        Cx = None
        tune = None
        try:
            (Cx, C, Cpop, tune) = self.visual.getCurves()
            self.currentCurves = {'Cx': Cx, 'C': C, 'Cpop': C, 'tune': tune}
        except TypeError:
            pass
        except Exception as e:
            logger.error('Output does not likely exist. Error: {}'.format(e))

        if(C is not None and Cx is not None):
            self.c1.setData(Cx, Cpop, pen=penW)
            self.c2.setData(Cx, C, pen=penR)
            
            if(self.flag):
                self.selected = self.visual.getFirstSelect()
                if self.selected is not None:
                    self._updateRedCirc()

        #TODO: rewrite as set of polar[] and set of tune[]
        if tune:
            self.num = tune[0].shape[0]
            theta = np.linspace(0, 2*np.pi, self.num-1)
            theta = np.append(theta,0)
            self.theta = theta  
            if(tune[0] is not None):
                self.radius = np.zeros(self.num)
                self.radius[:len(tune[0])] = tune[0]
                self.x = self.radius * np.cos(self.theta)
                self.y = self.radius * np.sin(self.theta)
                self.polar2.setData(self.x, self.y, pen=penR)

            if(tune[1] is not None):
                self.radius2 = np.zeros(self.num)
                self.radius2[:len(tune[1])] = tune[1]
                self.x2 = self.radius2 * np.cos(self.theta)
                self.y2 = self.radius2 * np.sin(self.theta)
                self.polar1.setData(self.x2, self.y2, pen=penW)
        else:
            logger.error('Visual received None tune')

        #print('Full update Lines time ', time.time()-t)

    def mouseClick(self, event):
        '''Clicked on raw image to select neurons
        '''
        #TODO: make this unclickable until finished updated plot (?)
        event.accept()
        mousePoint = event.pos()
        self.selected = self.visual.selectNeurons(int(mousePoint.x()), int(mousePoint.y()))
        selectedraw = np.zeros(2)
        selectedraw[0] = int(mousePoint.x())
        selectedraw[1] = int(mousePoint.y())
        self._updateRedCirc()

    def keyPressEvent(self, e):
        if e.key() == 83:  # s
            self.saveCurrentImages()

    def saveCurrentImages(self):
        """
        Save current images and plot data
            - raw, color, image in .tif
            - Cx, C, Cpop, tune in pickle
        """
        t = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S.%f")[:-3]

        directory = Path(f'../outputs/')
        directory.mkdir(parents=True, exist_ok=True)

        for name, img in self.currentImages.items():
            tiff.imsave(directory / f'{t}_frame_{self.visual.frame_num}.tif', img)

        with open(directory / f'{t}_frame_{self.visual.frame_num}_plots.pickle', 'wb') as f:
            pickle.dump(self.currentCurves, f)

        logger.info(f'Saved images and plot as {t}_frame_{self.visual.frame_num}')

    def updatePhaseRange(self, start, end):
        """
        Updates the range of angles of interest as input from the circular slider.
        {self.thresh_r} is an ndarray of size {self.num}, which is 360° divided into {self.num} sections.
        All input within range {idx * 360/self.num} are lumped together (inclusive).
        Values in range of interest is {self.slider2.sliderVal} else 0.

        :param start: Start angle from circular slider {self.slider2.rangeChanged}
        :param end: End angle from circular slider

        >>> self.num = 21; self.slider2.sliderVal = 3
        >>> self.updatePhaseRange(12, 45)
        >>> print(self.thresh_r)
        [3 3 3 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0]

        """
        assert 0 <= start < 360
        assert 0 <= end < 360

        r = np.zeros(self.num, dtype=int)
        idxStart, idxEnd = int(start // (360 / self.num)), int(end // (360 / self.num))

        if start < end:
            r[idxStart:idxEnd+1] = self.slider2.sliderVal

        else:  # Over 360
            r[idxStart:] = self.slider2.sliderVal
            r[:idxEnd+1] = self.slider2.sliderVal

        self.thresh_r = r
        self.saveUserCommands('phase', [start, end])
        # r1, r2 = self.slider2.range()
        # r = np.full(self.num, self.slider.value())
        #
        # r1 = 4*np.pi*(r1-4)/360
        # r2 = 4*np.pi*(r2-4)/360
        # t1 = np.argmin(np.abs(np.array(r1)-self.theta))
        # t2 = np.argmin(np.abs(np.array(r2)-self.theta))
        # r[0:t1] = 0
        # r[t2+1:self.num] = 0
        # self.updateThreshGraph(r)

    def updateThreshold(self, newThresh):
        """
        Changes non-zero {self.thresh_r} values into {newThresh}.
        :param newThresh: new threshold from {self.slider2.threshChanged}

        """
        assert newThresh >= 0

        currentValue = np.max(self.thresh_r)
        if currentValue == 0:
            currentValue = 1

        if newThresh != currentValue:
            print('Updating threshold')
            self.thresh_r = (self.thresh_r / currentValue).astype(int)
            self.thresh_r *= newThresh
            self.saveUserCommands('threshold', newThresh)

        # val = self.slider.value()
        # if np.count_nonzero(self.thresh_r) == 0:
        #     r = np.full(self.num,val)
        # else:
        #     r = self.thresh_r
        #     r[np.nonzero(r)] = val
        # self.updateThreshGraph(val)

    def saveUserCommands(self, name, value):
        self.comm.put({
            'type': 'params',
            'target_module': 'GUI',
            'tweak_obj': 'config',
            'change': {name: value},
        })


    def updateThreshGraph(self, r):
        self.thresh_r = r
        x = self.thresh_r * np.cos(self.theta)
        y = self.thresh_r * np.sin(self.theta)
        self.polar3.setData(x, y, pen=pyqtgraph.mkPen(width=2, color='g'))

    def _updateRedCirc(self):
        ''' Circle neuron whose activity is in top (red) graph
            Default is neuron #0 from initialize
            #TODO: raise error if no neurons found (for all plotting..)
            #TODO: add arg instead of self.selected
        '''
        ROIpen1=pyqtgraph.mkPen(width=1, color='r')
        if self.flag:
            self.red_circ = CircleROI(pos = np.array([self.selected[0][0], self.selected[0][1]])-5, size=10, movable=False, pen=ROIpen1)
            self.rawplot_2.getView().addItem(self.red_circ)
            self.flag = False
        if np.count_nonzero(self.selected[0]) > 0:
            self.rawplot_2.getView().removeItem(self.red_circ)
            self.red_circ = CircleROI(pos = np.array([self.selected[0][0], self.selected[0][1]])-5, size=10, movable=False, pen=ROIpen1)
            self.rawplot_2.getView().addItem(self.red_circ)

    def closeEvent(self, event):
        '''Clicked x/close on window
            Add confirmation for closing without saving
        '''
        confirm = QMessageBox.question(self, 'Message', 'Quit without saving?',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if confirm == QMessageBox.Yes:
            self.comm.put(['quit'])
            print('Visual broke, avg time per frame: ', np.mean(self.visual.total_times))
            print('Visual got through ', self.visual.frame_num, ' frames')
            print('GUI avg time ', np.mean(self.total_times))
            np.savetxt('timing/visual_frame_time.txt', np.array(self.visual.total_times))
            np.savetxt('timing/visual_timestamp.txt', np.array(self.visual.timestamp))
            event.accept()
        else: event.ignore()
            

def _call(fnc, *args, **kwargs):
    ''' Call handler for (external) events
    '''
    def _callback():
        return fnc(*args, **kwargs)
    return _callback

class CircleROI(EllipseROI):
    def __init__(self, pos, size, **args):
        pyqtgraph.ROI.__init__(self, pos, size, **args)
        self.aspectLocked = True

class PolyROI(PolyLineROI):
    def __init__(self, positions, pos, **args):
        closed = True
        print('got positions ', positions)
        pyqtgraph.ROI.__init__(self, positions, closed, pos, **args)

class QRangeSlider(QtWidgets.QWidget):

    rangeChanged = pyqtSignal(tuple, name='rangeChanged')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        #self._width_offset = kwargs.pop('widthOffset', 18)

        self._minimum = 0
        self._maximum = 180

        self.min_max = 99
        self.max_max = 99

        self._layout = QtWidgets.QHBoxLayout()
        self._layout.setSpacing(0)
        self.setLayout(self._layout)

        self._min_slider = QtWidgets.QSlider(Qt.Horizontal)
        self._min_slider.setInvertedAppearance(True)

        self._max_slider = QtWidgets.QSlider(Qt.Horizontal)

        # install update handlers
        for slider in [self._min_slider, self._max_slider]:
            slider.blockSignals(True)
            slider.valueChanged.connect(self._value_changed)
            slider.rangeChanged.connect(self._update_layout)

            self._layout.addWidget(slider)
 
        # initialize to reasonable defaults
        self._min_slider.setValue(1 * self._min_slider.maximum())
        self._max_slider.setValue(1 * self._max_slider.maximum())

        self._update_layout()

    def _value_changed(self, *args):
        self._update_layout()
        self.rangeChanged.emit(self.range())

    def _update_layout(self, *args):
        for slider in [self._min_slider, self._max_slider]:
            slider.blockSignals(True)

        mid = floor((self._max_slider.value()-self._min_slider.value())/ 2)

        self.setMax_min(self._min_slider.maximum() + mid)
        self._min_slider.setValue(self._min_slider.value() + mid)
        self.setMax_max(self._max_slider.maximum() - mid)
        self._max_slider.setValue(self._max_slider.value() - mid)

        for slider in [self._min_slider, self._max_slider]:
            slider.blockSignals(False)

        self._layout.setStretch(0, self._min_slider.maximum())
        self._layout.setStretch(1, self._max_slider.maximum())

    def setMax_min(self, value):
        self._min_slider.setMaximum(value)
        self.min_max = value

    def setMax_max(self, value):
        self._max_slider.setMaximum(value)
        self.max_max = value

    def getMax_min(self):
        return self.min_max

    def getMax_max(self):
        return self.max_max

    def lowerSlider(self):
        return self._min_slider

    def upperSlider(self):
        return self._max_slider

    def range(self):
        return (self.getMax_min() - self._min_slider.value(), 180 - (self.getMax_max() - self._max_slider.value()))

    def setRange(self, lower, upper):
        for slider in [self._min_slider, self._max_slider]:
            slider.blockSignals(True)

        # self._min_slider.setValue(lower)
        # self._max_slider.setValue(upper)

        self._update_layout()


if __name__=="__main__":
    app = QtGui.QApplication(sys.argv)
    rasp = FrontEnd(None,None)
    rasp.show()
    app.exec_()
