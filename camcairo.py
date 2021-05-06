import numpy as np
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GstApp, GLib
import cairo
import pyfakewebcam
import time
import cv2
import hrm
import logging

DEV_IN = "/dev/video0"
DEV_OUT = "/dev/video20"
WIDTH, HEIGHT, FPS = 1280, 720, 30
HRMMAC = "db:e8:d7:91:5a:d1"

hrm.log.setLevel(logging.INFO)

hrmt = hrm.HRMThread(addr = HRMMAC)
curhr = 0

def paint(ctx, w, h):
    global curhr
    
    try:
        curhr = hrmt.queue.get(block = False)
    except:
        pass
    
    txt = f"{curhr} bpm"
    
    ctx.select_font_face("Ubuntu", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    fsz = h // 6
    ctx.set_font_size(fsz)

    ctx.set_source_rgba(0, 0, 0, 0.6)
    xp = w // 10
    yp = h - h // 10
    ctx.move_to(xp, yp)
    ctx.show_text(txt)
    
    ctx.set_source_rgba(0.9, 0.2, 0.2, 1.0)
    ctx.move_to(xp - fsz // 10, yp - fsz // 10)
    ctx.show_text(txt)

fake = pyfakewebcam.FakeWebcam(DEV_OUT, WIDTH, HEIGHT)

Gst.init(None)
pipeline = Gst.parse_launch(f"v4l2src device={DEV_IN} ! image/jpeg, width=(int){WIDTH}, height=(int){HEIGHT}, framerate={FPS}/1 ! jpegdec ! videoconvert ! video/x-raw, format=BGRA ! appsink name=sink")
appsink = pipeline.get_by_name("sink")
appsink.set_property("emit-signals", True)
hrmt.start()

t = 0

def new_sample(sink):
    global t
    sample = sink.pull_sample()
    caps = sample.get_caps()
    h = caps.get_structure(0).get_value("height")
    w = caps.get_structure(0).get_value("width")
    
    buffer = sample.get_buffer()
    success, map_info = buffer.map(Gst.MapFlags.READ)
    if not success:
        raise RuntimeError('failed to map buffer')
    
    arr = np.ndarray(shape = (h, w, 4, ), dtype = np.uint8, buffer = map_info.data)
    buffer.unmap(map_info)
    
    arr = np.array(arr)
    surf = cairo.ImageSurface.create_for_data(arr, cairo.FORMAT_ARGB32, w, h)
    ctx = cairo.Context(surf)
    
    paint(ctx, w, h)
    
    rgb = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGB)
    fake.schedule_frame(rgb)

    now = time.time()
    print(f"... {w}x{h}, {1 / (now - t)} fps ...")
    t = now

    return False

appsink.connect("new-sample", new_sample)

pipeline.set_state(Gst.State.PLAYING)
GLib.MainLoop().run()
