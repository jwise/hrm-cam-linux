import numpy as np
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GstApp, GLib
import cairo
import pyfakewebcam
import time
import cv2

def paint(ctx, w, h):
    ctx.move_to(0, 0)
    ctx.line_to(w, h)
    ctx.close_path()
    ctx.set_source_rgb(255, 0, 0)
    ctx.set_line_width(15)
    ctx.stroke()    

DEV_IN = "/dev/video0"
DEV_OUT = "/dev/video20"
WIDTH, HEIGHT, FPS = 1280, 720, 30

fake = pyfakewebcam.FakeWebcam(DEV_OUT, WIDTH, HEIGHT)

Gst.init(None)
pipeline = Gst.parse_launch(f"v4l2src device={DEV_IN} ! image/jpeg, width=(int){WIDTH}, height=(int){HEIGHT}, framerate={FPS}/1 ! jpegdec ! videoconvert ! video/x-raw, format=BGRA ! appsink name=sink")
appsink = pipeline.get_by_name("sink")
appsink.set_property("emit-signals", True)

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
