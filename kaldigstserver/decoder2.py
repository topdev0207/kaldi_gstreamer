"""
Created on May 17, 2013

@author: tanel
"""
import gi

gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst

GObject.threads_init()
Gst.init(None)
import logging
import thread

logger = logging.getLogger(__name__)

import pdb

class DecoderPipeline2(object):
    def __init__(self, conf={}):
        logger.info("Creating decoder using conf: %s" % conf)
        self.create_pipeline(conf)
        self.outdir = conf.get("out-dir", None)

        self.result_handler = None
        self.eos_handler = None
        self.request_id = "<undefined>"


    def create_pipeline(self, conf):

        self.appsrc = Gst.ElementFactory.make("appsrc", "appsrc")
        self.decodebin = Gst.ElementFactory.make("decodebin", "decodebin")
        self.audioconvert = Gst.ElementFactory.make("audioconvert", "audioconvert")
        self.audioresample = Gst.ElementFactory.make("audioresample", "audioresample")
        self.tee = Gst.ElementFactory.make("tee", "tee")
        self.queue1 = Gst.ElementFactory.make("queue", "queue1")
        self.filesink = Gst.ElementFactory.make("filesink", "filesink")
        self.queue2 = Gst.ElementFactory.make("queue", "queue2")
        self.asr = Gst.ElementFactory.make("kaldinnet2onlinedecoder", "asr")
        self.fakesink = Gst.ElementFactory.make("fakesink", "fakesink")

        for (key, val) in conf.get("decoder", {}).iteritems():
            logger.info("Setting decoder property: %s = %s" % (key, val))
            self.asr.set_property(key, val)

        self.appsrc.set_property("is-live", True)
        self.filesink.set_property("location", "/dev/null")
        logger.info('Created GStreamer elements')

        self.pipeline = Gst.Pipeline()
        for element in [self.appsrc, self.decodebin, self.audioconvert, self.audioresample, self.tee,
                        self.queue1, self.filesink,
                        self.queue2, self.asr, self.fakesink]:
            logger.debug("Adding %s to the pipeline" % element)
            self.pipeline.add(element)

        logger.info('Linking GStreamer elements')

        self.appsrc.link(self.decodebin)
        #self.appsrc.link(self.audioconvert)
        self.decodebin.connect('pad-added', self._connect_decoder)

        self.audioconvert.link(self.audioresample)

        self.audioresample.link(self.tee)

        self.tee.link(self.queue1)
        self.queue1.link(self.filesink)

        self.tee.link(self.queue2)
        self.queue2.link(self.asr)

        self.asr.link(self.fakesink)

        # Create bus and connect several handlers
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.enable_sync_message_emission()
        self.bus.connect('message::eos', self._on_eos)
        self.bus.connect('message::error', self._on_error)
        #self.bus.connect('message::cutter', self._on_cutter)

        self.asr.connect('partial-result', self._on_partial_result)
        self.asr.connect('final-result', self._on_final_result)
        logger.info("Setting pipeline to READY")
        self.pipeline.set_state(Gst.State.READY)
        logger.info("Set pipeline to READY")

    def _connect_decoder(self, element, pad):
        logger.info("%s: Connecting audio decoder" % self.request_id)
        pad.link(self.audioconvert.get_static_pad("sink"))
        logger.info("%s: Connected audio decoder" % self.request_id)

    def _on_partial_result(self, asr, hyp):
        logger.info("%s: Got partial result: %s" % (self.request_id, hyp.decode('utf8')))
        if self.result_handler:
            self.result_handler(hyp, False)

    def _on_final_result(self, asr, hyp):
        logger.info("%s: Got final result: %s" % (self.request_id, hyp.decode('utf8')))
        if self.result_handler:
            self.result_handler(hyp, True)

    def _on_error(self, bus, msg):
        self.error = msg.parse_error()
        logger.error(self.error)

    def _on_eos(self, bus, msg):
        logger.info('%s: Pipeline received eos signal' % self.request_id)
        #self.decodebin.unlink(self.audioconvert)

        if self.outdir:
            self.filesink.set_state(Gst.State.NULL)
            self.filesink.set_property('location', "/dev/null")
            self.filesink.set_state(Gst.State.PLAYING)
        self.pipeline.set_state(Gst.State.NULL)

        if self.eos_handler:
            self.eos_handler[0](self.eos_handler[1])
        self.request_id = "<undefined>"


    def init_request(self, id, caps_str):
        self.request_id = id
        logger.info("%s: Initializing request" % (self.request_id))
        if caps_str and len(caps_str) > 0:
            logger.info("%s: Setting caps to %s" % (self.request_id, caps_str))
            caps = Gst.caps_from_string(caps_str)
            self.appsrc.set_property("caps", caps)
        else:
            #caps = Gst.caps_from_string("")
            self.appsrc.set_property("caps", None)
            #self.pipeline.set_state(Gst.State.READY)
            pass
        #self.appsrc.set_state(Gst.State.PAUSED)

        if self.outdir:
            self.pipeline.set_state(Gst.State.PAUSED)
            self.filesink.set_state(Gst.State.NULL)
            self.filesink.set_property('location', "%s/%s.raw" % (self.outdir, id))
            self.filesink.set_state(Gst.State.PLAYING)

        #self.filesink.set_state(Gst.State.PLAYING)        
        #self.decodebin.set_state(Gst.State.PLAYING)
        self.pipeline.set_state(Gst.State.PLAYING)
        self.filesink.set_state(Gst.State.PLAYING)
        # push empty buffer (to avoid hang on client diconnect)
        buf = Gst.Buffer.new_allocate(None, 0, None)
        self.appsrc.emit("push-buffer", buf)


    def process_data(self, data):
        logger.debug('%s: Pushing buffer of size %d to pipeline' % (self.request_id, len(data)))
        buf = Gst.Buffer.new_allocate(None, len(data), None)
        # FIXME: find more efficient way to do this
        for (i, c) in enumerate(data):
            buf.memset(i, c, 1)
        self.appsrc.emit("push-buffer", buf)


    def end_request(self):
        logger.info("%s: Pushing EOS to pipeline" % self.request_id)
        self.pipeline.send_event(Gst.Event.new_eos())


    def set_result_handler(self, handler):
        self.result_handler = handler

    def set_eos_handler(self, handler, user_data=None):
        self.eos_handler = (handler, user_data)


    def cancel(self):
        logger.info("%s: Cancelling pipeline" % self.request_id)
        self.pipeline.send_event(Gst.Event.new_eos())
        #self.asr.set_property("silent", True)
        #self.pipeline.set_state(Gst.State.NULL)

        #if (self.pipeline.get_state() == Gst.State.PLAYING):
        #logger.debug("Sending EOS to pipeline")
        #self.pipeline.send_event(Gst.Event.new_eos())
        #self.pipeline.set_state(Gst.State.READY)
        logger.info("%s: Cancelled pipeline" % self.request_id)