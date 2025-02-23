# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2010-2021 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from time import monotonic
from xpra.net import compression
from xpra.codecs.loader import get_codec
from xpra.util import envbool, first_time
from xpra.codecs.rgb_transform import rgb_reformat
from xpra.os_util import memoryview_to_bytes, bytestostr
from xpra.log import Logger

#"pixels_to_bytes" gets patched up by the OSX shadow server
pixels_to_bytes = memoryview_to_bytes
try:
    from xpra.net.mmap_pipe import mmap_write
except ImportError:
    mmap_write = None               #no mmap

log = Logger("window", "encoding")

WEBP_PILLOW = envbool("XPRA_WEBP_PILLOW", False)


def webp_encode(image, supports_transparency, quality, speed, content_type):
    stride = image.get_rowstride()
    pixel_format = image.get_pixel_format()
    enc_webp = get_codec("enc_webp")
    #log("WEBP_PILLOW=%s, enc_webp=%s, stride=%s, pixel_format=%s", WEBP_PILLOW, enc_webp, stride, pixel_format)
    if not WEBP_PILLOW and enc_webp and stride>0 and stride%4==0 and pixel_format in ("BGRA", "BGRX", "RGBA", "RGBX"):
        #prefer Cython module:
        cdata, client_options = enc_webp.encode(image, quality, speed, supports_transparency, content_type)
        return "webp", compression.Compressed("webp", cdata), client_options, image.get_width(), image.get_height(), 0, 24
    #fallback using Pillow:
    enc_pillow = get_codec("enc_pillow")
    if enc_pillow:
        if not WEBP_PILLOW:
            log.warn("Warning: using PIL fallback for webp")
            log.warn(" enc_webp=%s, stride=%s, pixel format=%s", enc_webp, stride, image.get_pixel_format())
        for x in ("webp", "png", "jpeg"):
            if x in enc_pillow.get_encodings():
                return enc_pillow.encode(x, image, quality, speed, supports_transparency)
    raise Exception("BUG: cannot use 'webp' encoding and none of the PIL fallbacks are available!")


def rgb_encode(coding, image, rgb_formats, supports_transparency, speed, rgb_zlib=True, rgb_lz4=True):
    pixel_format = bytestostr(image.get_pixel_format())
    #log("rgb_encode%s pixel_format=%s, rgb_formats=%s",
    #    (coding, image, rgb_formats, supports_transparency, speed, rgb_zlib, rgb_lz4), pixel_format, rgb_formats)
    if pixel_format not in rgb_formats:
        log("rgb_encode reformatting because %s not in %s, supports_transparency=%s",
            pixel_format, rgb_formats, supports_transparency)
        if not rgb_reformat(image, rgb_formats, supports_transparency):
            raise Exception("cannot find compatible rgb format to use for %s! (supported: %s)" % (
                pixel_format, rgb_formats))
        #get the new format:
        pixel_format = bytestostr(image.get_pixel_format())
        #switch encoding if necessary:
        if len(pixel_format)==4:
            coding = "rgb32"
        elif len(pixel_format)==3:
            coding = "rgb24"
        else:
            raise Exception("invalid pixel format %s" % pixel_format)
    #we may still want to re-stride:
    image.may_restride()
    #always tell client which pixel format we are sending:
    options = {"rgb_format" : pixel_format}

    #compress here and return a wrapper so network code knows it is already zlib compressed:
    pixels = image.get_pixels()
    assert pixels, "failed to get pixels from %s" % image
    width = image.get_width()
    height = image.get_height()
    stride = image.get_rowstride()

    #compression stage:
    level = 0
    algo = "not"
    l = len(pixels)
    if l>=512 and speed<100:
        if l>=4096:
            #speed=99 -> level=1, speed=0 -> level=9
            level = 1+max(0, min(8, int(100-speed)//12))
        else:
            #fewer pixels, make it more likely we won't bother compressing
            #and use a lower level (max=5)
            level = max(0, min(5, int(115-speed)//20))
    if level>0:
        cwrapper = compression.compressed_wrapper(coding, pixels, level=level,
                                                  zlib=rgb_zlib, lz4=rgb_lz4,
                                                  brotli=False, none=True)
        algo = cwrapper.algorithm
        if algo=="none" or len(cwrapper)>=(len(pixels)-32):
            #no compression is enabled, or compressed is actually bigger!
            #(fall through to uncompressed)
            level = 0
        else:
            #add compressed marker:
            options[algo] = level
            #remove network layer compression marker
            #so that this data will be decompressed by the decode thread client side:
            cwrapper.level = 0
    if level==0:
        #can't pass a raw buffer to bencode / rencode,
        #and even if we could, the image containing those pixels may be freed by the time we get to the encoder
        algo = "not"
        cwrapper = compression.Compressed(coding, pixels_to_bytes(pixels), True)
    if pixel_format.find("A")>=0 or pixel_format.find("X")>=0:
        bpp = 32
    else:
        bpp = 24
    log("rgb_encode using level=%s for %5i bytes at %3i speed, %s compressed %4sx%-4s in %s/%s: %5s bytes down to %5s",
        level, l, speed, algo, image.get_width(), image.get_height(), coding, pixel_format, len(pixels), len(cwrapper.data))
    #wrap it using "Compressed" so the network layer receiving it
    #won't decompress it (leave it to the client's draw thread)
    return coding, cwrapper, options, width, height, stride, bpp


def mmap_send(mmap, mmap_size, image, rgb_formats, supports_transparency):
    if mmap_write is None:
        if first_time("mmap_write missing"):
            log.warn("Warning: cannot use mmap, no write method support")
        return None
    if image.get_pixel_format() not in rgb_formats:
        if not rgb_reformat(image, rgb_formats, supports_transparency):
            warning_key = "mmap_send(%s)" % image.get_pixel_format()
            if first_time(warning_key):
                log.warn("Waening: cannot use mmap to send %s" % image.get_pixel_format())
            return None
    start = monotonic()
    data = image.get_pixels()
    assert data, "failed to get pixels from %s" % image
    mmap_data, mmap_free_size = mmap_write(mmap, mmap_size, data)
    elapsed = monotonic()-start+0.000000001 #make sure never zero!
    log("%s MBytes/s - %s bytes written to mmap in %.1f ms", int(len(data)/elapsed/1024/1024), len(data), 1000*elapsed)
    if mmap_data is None:
        return None
    #replace pixels with mmap info:
    return mmap_data, mmap_free_size, len(data)
