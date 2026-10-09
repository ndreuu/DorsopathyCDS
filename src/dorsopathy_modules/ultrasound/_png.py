"""Bounded PNG profile: non-interlaced 8-bit grayscale, RGB or RGBA."""
import struct
import zlib
from dorsopathy_platform.contracts import ContractError


def decode_png(data):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ContractError("Ultrasound raster requires the supported PNG profile")
    offset, header, compressed, ended = 8, None, bytearray(), False
    while offset < len(data):
        if offset + 12 > len(data): raise ContractError("PNG chunk is incomplete")
        length = struct.unpack(">I", data[offset:offset+4])[0]
        kind = data[offset+4:offset+8]
        if offset + 12 + length > len(data): raise ContractError("PNG chunk size is invalid")
        payload = data[offset+8:offset+8+length]
        crc = struct.unpack(">I", data[offset+8+length:offset+12+length])[0]
        if zlib.crc32(kind+payload) & 0xffffffff != crc: raise ContractError("PNG CRC check failed")
        if header is None and kind != b"IHDR": raise ContractError("PNG header order is invalid")
        if kind == b"IHDR":
            if header is not None or len(payload) != 13: raise ContractError("PNG header is invalid")
            width,height,depth,color,compression,filtering,interlace=struct.unpack(">IIBBBBB",payload)
            if not 0 < width <= 2048 or not 0 < height <= 2048 or width*height > 1024*1024 or depth != 8 or color not in {0,2,6} or compression or filtering or interlace:
                raise ContractError("PNG dimensions, pixel type or interlace are unsupported")
            header=(width,height,{0:1,2:3,6:4}[color])
        elif kind == b"IDAT": compressed.extend(payload)
        elif kind == b"IEND":
            if payload: raise ContractError("PNG ending is invalid")
            ended=True; offset += 12+length; break
        elif kind[0] & 32 == 0:
            raise ContractError("Unsupported critical PNG chunk")
        offset += 12+length
    if not ended or offset != len(data) or not compressed:
        raise ContractError("PNG payload is incomplete or contains trailing data")
    width,height,bpp=header
    stride=width*bpp; expected=(stride+1)*height
    try:
        inflater=zlib.decompressobj()
        raw=inflater.decompress(bytes(compressed),expected+1)
    except zlib.error:
        raise ContractError("PNG compression is invalid") from None
    if len(raw)!=expected or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
        raise ContractError("PNG decompressed size is invalid")
    rows=[]; previous=bytearray(stride)
    def paeth(a,b,c):
        p=a+b-c;pa,pb,pc=abs(p-a),abs(p-b),abs(p-c)
        return a if pa<=pb and pa<=pc else b if pb<=pc else c
    for y in range(height):
        start=y*(stride+1); mode=raw[start]; row=bytearray(raw[start+1:start+1+stride])
        if mode not in {0,1,2,3,4}: raise ContractError("Unsupported PNG row filter")
        for i in range(stride):
            left=row[i-bpp] if i>=bpp else 0; above=previous[i]; diagonal=previous[i-bpp] if i>=bpp else 0
            prediction=0 if mode==0 else left if mode==1 else above if mode==2 else (left+above)//2 if mode==3 else paeth(left,above,diagonal)
            row[i]=(row[i]+prediction)&255
        rgb=bytearray()
        for x in range(width):
            values=row[x*bpp:(x+1)*bpp]
            if bpp==1: rgb.extend(values*3)
            elif bpp==3: rgb.extend(values)
            else: rgb.extend(round(value*values[3]/255) for value in values[:3])
        rows.append(rgb); previous=row
    return width,height,rows
