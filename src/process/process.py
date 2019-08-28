import time
import pickle
import cv2
import numpy as np
import scipy.sparse
from collections import UserDict
from nexus.store import Limbo, CannotGetObjectError, ObjectNotFoundError
from caiman.source_extraction import cnmf
from caiman.source_extraction.cnmf.utilities import detrend_df_f
from caiman.source_extraction.cnmf.online_cnmf import OnACID
from caiman.source_extraction.cnmf.params import CNMFParams
from caiman.motion_correction import motion_correct_iteration_fast, tile_and_correct
from caiman.utils.visualization import get_contours
import caiman as cm
from os.path import expanduser
import os
from queue import Empty
from nexus.module import Module, Spike, RunManager
import traceback
import colorama

import logging; logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class Processor(Module):
    '''Abstract class for the processor component
       Needs to take an image from data store (DS)
       Needs to output spikes estimates over time
       Will likely change specifications in the future
    '''
    
    def putEstimates(self):
        # Update the DS with estimates
        raise NotImplementedError


class CaimanProcessor(Processor):
    '''Wraps CaImAn/OnACID functionality to
       interface with our pipeline.
       Uses code from caiman/source_extraction/cnmf/online_cnmf.py
    '''
    def __init__(self, *args, config=None):
        super().__init__(*args)
        self.ests = None #neural activity
        self.coords = None
        self.params: dict = config

    def setup(self):
        ''' Create OnACID object and initialize it
                (runs initialize online)
            limboClient is a client to the data store server
        '''
        logger.info('Running setup for '+self.name)
        self.done = False
        self.dropped_frames = []
        self.coords = None
        self.ests = None
        self.A = None
        self.client.put(self.params, 'params_dict')

        # self.loadParams(param_file=self.param_file)
        # self.params = self.client.get('params_dict')
        
        # MUST include inital set of frames
        # TODO: Institute check here as requirement to Nexus
        
        self.opts = CNMFParams(params_dict=self.params)
        self.params = CNMFDict(self.params, cnmfparams=self.opts, client=self.client, q_comm=self.q_comm)

        self.onAc = OnACID(params = self.opts)
        self.frame_number = 0 #self.params['init_batch']
        #TODO: Need to rewrite init online as well to receive individual frames.
        self.onAc.initialize_online()
        self.max_shifts_online = self.onAc.params.get('online', 'max_shifts_online')

        return self

    def run(self):
        '''Run the processor continually on input frames
        '''
        self.fitframe_time = []
        self.putAnalysis_time = []
        self.procFrame_time = [] #aka t_motion
        self.detect_time = []
        self.shape_time = []
        self.flag = False
        self.total_times = []
        self.timestamp = []
        self.counter = 0

        with RunManager(self.name, self.runProcess, self.setup, self.q_sig, self.q_comm, paramsMethod=self.updateParams) as rm:
            logger.info(rm)
            
        print('Processor broke, avg time per frame: ', np.mean(self.total_times))
        print('Processor got through ', self.frame_number, ' frames')
        np.savetxt('timing/process_frame_time.txt', np.array(self.total_times))
        np.savetxt('timing/process_timestamp.txt', np.array(self.timestamp))

        self.shape_time = np.array(self.onAc.t_shapes)
        self.detect_time = np.array(self.onAc.t_detect)
        np.savetxt('timing/fitframe_time.txt', np.array(self.fitframe_time))
        np.savetxt('timing/shape_time.txt', self.shape_time)
        np.savetxt('timing/detect_time.txt', self.detect_time)
        np.savetxt('timing/putAnalysis_time.txt', np.array(self.putAnalysis_time))
        np.savetxt('timing/procFrame_time.txt', np.array(self.procFrame_time))

        print('Number of times coords updated ', self.counter)

        if self.onAc.estimates.OASISinstances is not None:
            try:
                S = np.stack([osi.s for osi in self.onAc.estimates.OASISinstances])
                np.savetxt('end_spikes.txt', S)
            except Exception as e:
                #logger.error('Exception {}: {} during frame number {}'.format(type(e).__name__, e, self.frame_number))
                print(traceback.format_exc())
        else:
            print('No OASIS')
        

    def runProcess(self):
        ''' Run process. Runs once per frame.
            Output is a location in the DS to continually
            place the Estimates results, with ref number that
            corresponds to the frame number (TODO)
        '''
        #TODO: Error handling for if these parameters don't work
            #should implement in Tweak (?) or getting too complicated for users..
        
        #proc_params = self.client.get('params_dict')
        output = self.params['output']
        init = self.params['init_batch']
        frame = self._checkFrames()
        
        if frame is not None:
            t = time.time()
            self.done = False
            try:
                self.frame = self.client.getID(frame[0][str(self.frame_number)])
                self.frame = self._processFrame(self.frame, self.frame_number+init)
                #self.frame = frame.copy()
                t2 = time.time()
                self._fitFrame(self.frame_number+init, self.frame.reshape(-1, order='F'))
                self.fitframe_time.append([time.time()-t2])
                self.putEstimates(self.onAc.estimates, output)
                self.timestamp.append([time.time(), self.frame_number])
            except ObjectNotFoundError:
                logger.error('Processor: Frame {} unavailable from store, droppping'.format(self.frame_number))
                self.dropped_frames.append(self.frame_number)
            except KeyError as e:
                logger.error('Processor: Key error... {0}'.format(e))
                # Proceed at all costs
                self.dropped_frames.append(self.frame_number)
            except Exception as e:
                logger.error('Processor unknown error: {}: {} during frame number {}'.format(type(e).__name__,
                                                                                            e, self.frame_number))
                print(traceback.format_exc())
                self.dropped_frames.append(self.frame_number)
            self.frame_number += 1
            self.total_times.append([time.time(), time.time()-t])
        else:
            pass
            # logger.error('Done with all available frames: {0}'.format(self.frame_number))
            # self.q_comm.put(None)
            # self.done = True

    def updateParams(self, incoming):
        """
        Apply change received from params signal to local {self.params}, which updates all necessary components.

        :param incoming: Parameter
        :type incoming: dict
        """
        assert incoming['target_module'] == 'Processor'
        if incoming['tweak_obj'] == 'config':
            self.params.update(incoming['change'])

    def finalProcess(self, output):
        if self.onAc.params.get('online', 'normalize'):
            # normalize final estimates for this set of files. Useful?
            #self._normAnalysis()
            self.putEstimates(self.onAc.estimates, output)
            #self._finalAnalysis(frame_number)
            #self.putEstimates(self.finalEstimates, output)
        
        #once runProcess has run once, need to reset init_batch to avoid internal errors
        #currently not logging this change; keeping all internal to caiman
        self.onAc.params.set('online', {'init_batch': 0})
        self.params['init_batch'] = 0
        # also need to extend dimensions of timesteps...TODO: better method init?
            #currently changed T1 inside online_cnmf....

        #TODO: determine a SINGLE location for params. Internal vs logged?
        #self.client.replace(self.params, 'params_dict')
        logger.info('Updated init batch after first run')


    def putEstimates(self, estimates, output):
        ''' Put whatever estimates we currently have
            into the output location specified
            TODO rewrite output input
        '''
        t = time.time()
        nb = self.onAc.params.get('init', 'nb')
        A = self.onAc.estimates.Ab[:, nb:]
        #b = self.onAc.estimates.Ab[:, :nb] #toarray() ?
        before = self.frame_number-500 if self.frame_number > 500 else 0
        #C = self.onAc.estimates.C_on.get_ordered()
        t2 = time.time()
        if self.onAc.estimates.OASISinstances is not None:
            try:
                S = np.stack([osi.s[before:self.frame_number] for osi in self.onAc.estimates.OASISinstances])
            except ValueError:
                max_len = max([len(osi.s[before:self.frame_number]) for osi in self.onAc.estimates.OASISinstances])
                S = np.array([np.lib.pad(osi.s[before:self.frame_number], (0, max_len-len(osi.s[before:self.frame_number])), 'constant', constant_values=0) for osi in self.onAc.estimates.OASISinstances])
            except Exception as e:
                logger.error('Exception {}: {} during frame number {}'.format(type(e).__name__, e, self.frame_number))
                print([osi.s.shape for osi in self.onAc.estimates.OASISinstances])
        else:
            S = None #self.onAc.estimates.S = np.zeros_like(C)
        t3 = time.time()
        # f = self.onAc.estimates.C_on[:nb, :self.frame_number]
        
        #self.ests = C  # detrend_df_f(A, b, C, f) # Too slow!

        image = self.makeImage()
        t4 = time.time()
        dims = image.shape
        self._updateCoords(A,dims)
        t5 = time.time()

        ids = []
        #ids.append(self.client.put(np.array(C), 'C'+str(self.frame_number)))
        ids.append(self.client.put(self.coords, 'coords'+str(self.frame_number)))
        ids.append(self.client.put(image, 'proc_image'+str(self.frame_number)))
        ids.append(self.client.put(S, 'S'+str(self.frame_number)))
        t6 = time.time()
        self.q_out.put(ids)
        #self.q_comm.put([self.frame_number])

        self.putAnalysis_time.append([time.time()-t])
    
    def _updateCoords(self, A, dims):
        '''See if we need to recalculate the coords
           Also see if we need to add components
        '''
        if self.coords is None: #initial calculation
            self.A = A
            self.coords = get_contours(A, dims)

        elif np.shape(A)[1] > np.shape(self.A)[1] and self.frame_number % 10 == 0: 
            #Only recalc if we have new components
            # FIXME: Since this is only for viz, only do this every 100 frames
            # TODO: maybe only recalc coords that are new? 
            print('Recalc coords ', self.counter)
            self.A = A
            self.coords = get_contours(A, dims)
            self.counter += 1

    def makeImage(self):
        '''Create image data for visualiation
            Using caiman code here
            #TODO: move to MeanAnalysis class ?? Check timing if we move it!
                Other idea -- easier way to compute this?
        '''
        mn = self.onAc.M - self.onAc.N
        image = None
        try:
            components = self.onAc.estimates.Ab[:,mn:].dot(self.onAc.estimates.C_on[mn:self.onAc.M,(self.frame_number-1)%self.onAc.window]).reshape(self.onAc.dims, order='F')
            background = self.onAc.estimates.Ab[:,:mn].dot(self.onAc.estimates.C_on[:mn,(self.frame_number-1)%self.onAc.window]).reshape(self.onAc.dims, order='F')
            image = ((components + background) - self.onAc.bnd_Y[0])/np.diff(self.onAc.bnd_Y)
            image = np.minimum((image*255.),255).astype('u1')
        except ValueError as ve:
            logger.info('ValueError: {0}'.format(ve))

        #cor_frame = (self.frame - self.onAc.bnd_Y[0])/np.diff(self.onAc.bnd_Y)

        # print('dtype raw frame:', self.frame.dtype)
        # print('dtype cor_frame:', cor_frame.dtype)

        return image

    def _finalAnalysis(self, t):
        ''' Some kind of final steps for estimates
            t is the final frame count
            TODO: get params from elsewhere in case changed!
            Can't put these into onAc.estimates since it changes shape,
            and thus interferes with running the process again in a loop.
        '''
        #TODO change epochs?
        epochs = 1

        self.finalEstimates = self.onAc.estimates
        
        self.finalEstimates.A, self.finalEstimates.b = self.onAc.estimates.Ab[:, self.onAc.params.get('init', 'nb'):], self.onAc.estimates.Ab[:, :self.onAc.params.get('init', 'nb')].toarray()
        self.finalEstimates.C, self.finalEstimates.f = self.onAc.estimates.C_on[self.onAc.params.get('init', 'nb'):self.onAc.M, t - t //
                         epochs:t], self.onAc.estimates.C_on[:self.onAc.params.get('init', 'nb'), t - t // epochs:t]
        noisyC = self.onAc.estimates.noisyC[self.onAc.params.get('init', 'nb'):self.onAc.M, t - t // epochs:t]
        self.finalEstimates.YrA = noisyC - self.onAc.estimates.C
        self.finalEstimates.bl = [osi.b for osi in self.onAc.estimates.OASISinstances] if hasattr(self, 'OASISinstances') else [0] * self.onAc.estimates.C.shape[0]
        self.finalEstimates.C_on = self.onAc.estimates.C_on[:self.onAc.M]
        self.finalEstimates.noisyC = self.onAc.estimates.noisyC[:self.onAc.M]


    def _checkFrames(self):
        ''' Check to see if we have frames for processing
            TODO: rework logic since not accessing store directly here anymore
        '''
        try:
            res = self.q_in.get(timeout=0.0001)
            return res
            #return self.client.get('frame')
        # except CannotGetObjectError:
        #     logger.error('No frames')
        #TODO: add'l error handling
        except Empty:
            #logger.info('no frames for processing')
            return None


    def _processFrame(self, frame, frame_number):
        ''' Do some basic processing on a single frame
            Raises NaNFrameException if a frame contains NaN
            Returns the normalized/etc modified frame
        '''
        t=time.time()
        if np.isnan(np.sum(frame)):
            raise NaNFrameException
        frame = frame.astype(np.float32) #or require float32 from image acquistion
        if self.onAc.params.get('online', 'ds_factor') > 1:
            frame = cv2.resize(frame, self.onAc.img_norm.shape[::-1])
            # TODO check for params, onAc componenets before calling, or except
        if self.onAc.params.get('online', 'normalize'):
            frame -= self.onAc.img_min
        if self.onAc.params.get('online', 'motion_correct'):
            try:
                templ = self.onAc.estimates.Ab.dot(
                self.onAc.estimates.C_on[:self.onAc.M, (frame_number-1)%self.onAc.window]).reshape(
                self.onAc.params.get('data', 'dims'), order='F')*self.onAc.img_norm
            except Exception as e:
                logger.error('Unknown exception {0}'.format(e))
                raise Exception
            
            if self.onAc.params.get('motion', 'pw_rigid'):
                frame_cor1, shift = motion_correct_iteration_fast(
                            frame, templ, self.max_shifts_online, self.max_shifts_online)
                frame_cor, shift = tile_and_correct(frame, templ, self.onAc.params.motion['strides'], self.onAc.params.motion['overlaps'], self.onAc.params.motion['max_shifts'], newoverlaps=None, newstrides=None, upsample_factor_grid=4, upsample_factor_fft=10, show_movie=False, max_deviation_rigid=self.onAc.params.motion['max_deviation_rigid'],add_to_movie=0, shifts_opencv=True, gSig_filt=None, use_cuda=False, border_nan='copy')[:2]
            else:
                frame_cor, shift = motion_correct_iteration_fast(frame, templ, self.max_shifts_online, self.max_shifts_online)
            self.onAc.estimates.shifts.append(shift)
        else:
            frame_cor = frame
        if self.onAc.params.get('online', 'normalize'):
            frame_cor = frame_cor/self.onAc.img_norm
        self.procFrame_time.append([time.time()-t])
        return frame_cor

    def _fitFrame(self, frame_number, frame):
        ''' Do the heavy lifting here. CNMF, etc
            Updates self.onAc.estimates
        '''
        try:
            self.onAc.fit_next(frame_number, frame)
        except Exception as e:
            print('Fit frame Error!! {}: {}'.format(type(e).__name__, e))
            raise Exception

    def _normAnalysis(self):
        ''' Modifies in place Ab!
        Do not use until the end, or
        TODO: just return nicely normalized Ab. Possibly combine with other
            visualization/estimation output
        '''
        self.onAc.estimates.Ab /= 1./self.onAc.img_norm.reshape(-1, order='F')[:,np.newaxis]
        self.onAc.estimates.Ab = scipy.sparse.csc_matrix(self.onAc.estimates.Ab)


class NaNFrameException(Exception):
    pass


class CNMFDict(UserDict):
    """
    Dictionary that updates CNMFParams and Nexus Tweak when its value is changed.

    Can catch exception from CNMFParams and revert.
    """
    def __init__(self, *args, cnmfparams=None, client=None, q_comm=None, **kwargs):
        self.CNMFParams: CNMFParams = cnmfparams
        self.client: Limbo = client
        self.q_comm = q_comm
        super().__init__(*args, **kwargs)

    def __setitem__(self, key, value):
        old = self.get(key)
        super().__setitem__(key, value)
        try:
            self.CNMFParams.change_params(self)
        except Exception as e:
            logger.error(f'CNMFParams change failed! Reverting to old config. {e}')
            if old is None:
                self.pop(key)
            else:
                self[key] = old
            self.CNMFParams.change_params(self)
            value = old
        else:
            logger.info(colorama.Fore.GREEN + f'Processor: {key} changed from {old} to {value}' + colorama.Fore.RESET)
        finally:
            self.q_comm.put({'type': 'params',
                             'target_module': 'Processor',
                             'tweak_obj': 'config',
                             'change': {key: value}})
