"""File structure and metadata: JPEG, PNG and WebP containers, EXIF, XMP,
C2PA / Content Credentials and AI-generator metadata.

Everything is parsed from the raw bytes, so nothing depends on how an
image library chooses to interpret (or silently drop) a field.
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass, field
from typing import Any

import numpy as np

ZIGZAG = np.array([
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63])
STD_LUM = np.array([
    16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92, 49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99])
STD_CHR = np.full(64, 99)
STD_CHR[[0, 1, 2, 3, 8, 9, 10, 11, 16, 17, 18, 24, 25]] = [17, 18, 24, 47, 18, 21, 26, 66, 24, 26, 56, 47, 66]

TIFF_TAGS = {
    0x010E: "ImageDescription", 0x010F: "Make", 0x0110: "Model", 0x0112: "Orientation", 0x011A: "XResolution",
    0x011B: "YResolution", 0x0128: "ResolutionUnit", 0x0131: "Software", 0x0132: "DateTime", 0x013B: "Artist",
    0x8298: "Copyright", 0x0100: "ImageWidth", 0x0101: "ImageLength", 0x829A: "ExposureTime", 0x829D: "FNumber",
    0x8822: "ExposureProgram", 0x8827: "ISO", 0x9000: "ExifVersion", 0x9003: "DateTimeOriginal",
    0x9004: "DateTimeDigitized", 0x9010: "OffsetTime", 0x9011: "OffsetTimeOriginal", 0x9012: "OffsetTimeDigitized",
    0x9209: "Flash", 0x920A: "FocalLength", 0x927C: "MakerNote", 0x9286: "UserComment", 0x9291: "SubSecTimeOriginal",
    0xA002: "PixelXDimension", 0xA003: "PixelYDimension", 0xA420: "ImageUniqueID", 0xA430: "CameraOwnerName",
    0xA431: "BodySerialNumber", 0xA433: "LensMake", 0xA434: "LensModel", 0xA405: "FocalLengthIn35mmFilm",
    0xA401: "CustomRendered", 0xA402: "ExposureMode", 0xA403: "WhiteBalance", 0xA406: "SceneCaptureType",
    0x0201: "ThumbnailOffset", 0x0202: "ThumbnailLength", 0x0103: "Compression",
}
GPS_TAGS = {1: "GPSLatitudeRef", 2: "GPSLatitude", 3: "GPSLongitudeRef", 4: "GPSLongitude",
            5: "GPSAltitudeRef", 6: "GPSAltitude", 7: "GPSTimeStamp", 0x1D: "GPSDateStamp"}
TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}

EDITORS = re.compile(
    r"photoshop|gimp|lightroom|affinity|pixelmator|paint\.net|snapseed|picsart|canva|facetune|luminar|capture one|"
    r"darktable|rawtherapee|photopea|fotor|meitu|airbrush|photodirector|faceapp|photoscape|acdsee|paintshop|corel|"
    r"krita|photos? editor|picasa|befunky|inpixio|polarr|vsco|lensa|remini", re.I)

# Names of image generators and generator front-ends. Matched against every
# text field in the file (EXIF, XMP, PNG text, JPEG comments, C2PA).
AI_GENERATORS = re.compile(
    r"midjourney|niji ?journey|dall[·\-\s]?e\b|openai|chatgpt|gpt-?4o|gpt-image|\bsora\b|stable[\s_-]?diffusion|"
    r"stability\.?ai|\bsdxl\b|\bflux\.?1\b|\bflux (?:pro|dev|schnell)\b|black forest labs|firefly|"
    r"google imagen|\bimagen ?[234]\b|gemini (?:\d|flash|pro|image)|nano banana|synthid|ideogram|leonardo\.ai|"
    r"runwayml|runway gen|novelai|dreamstudio|craiyon|bing image creator|image creator from microsoft|"
    r"playground ?ai|\bkrea\b|comfyui|automatic1111|invokeai|fooocus|stable ?cascade|kandinsky|deepfloyd|"
    r"wombo|nightcafe|starryai|artbreeder|generative fill|made with ai|ai[- ]generated", re.I)

# IPTC "digital source type" values that declare algorithmic content.
DIGITAL_SOURCE_TYPES = {
    "trainedAlgorithmicMedia": "created by a generative AI model",
    "compositeWithTrainedAlgorithmicMedia": "a composite that includes generative AI content",
    "algorithmicMedia": "created algorithmically (without a trained model)",
    "compositeSynthetic": "a composite including synthetic elements",
    "digitalCreation": "created digitally (not captured by a camera)",
}


def estimate_quality(zz: np.ndarray, chroma: bool) -> dict[str, Any]:
    """Compare a zig-zag-ordered quantisation table with the IJG (libjpeg)
    reference tables scaled to every quality from 1 to 100."""
    base = (STD_CHR if chroma else STD_LUM)[ZIGZAG]
    best_q, best_err = 0, float("inf")
    for q in range(1, 101):
        s = 5000 / q if q < 50 else 200 - 2 * q
        t = np.clip(np.floor((base * s + 50) / 100), 1, 255)
        err = float(np.abs(t - zz).sum())
        if err < best_err:
            best_q, best_err = q, err
    return {"quality": best_q, "exact": best_err == 0, "mean_error": best_err / 64}


def _ascii(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("latin-1", "replace").strip()


def parse_tiff(buf: bytes) -> dict[str, Any] | None:
    """EXIF (a TIFF structure): IFD0, the Exif IFD, GPS and IFD1 (thumbnail)."""
    if len(buf) < 8 or buf[:2] not in (b"II", b"MM"):
        return None
    e = "<" if buf[:2] == b"II" else ">"
    if struct.unpack(e + "H", buf[2:4])[0] != 42:
        return None
    n_len = len(buf)
    out: dict[str, Any] = {"ifd0": {}, "exif": {}, "gps": {}, "ifd1": {}, "thumb": None}
    pointers: dict[str, int] = {}

    def value(typ: int, count: int, off: int):
        size = TYPE_SIZE[typ]
        raw = buf[off:off + size * count]
        if typ == 2:
            return _ascii(raw)
        if typ == 7:
            if count <= 16:
                s = raw.decode("latin-1")
                if s and all(32 <= ord(ch) < 127 for ch in s):
                    return s
            if count > 8 and raw[:8] in (b"ASCII\0\0\0", b"UNICODE\0"):  # UserComment
                text = raw[8:].decode("utf-16" if raw[:1] == b"U" else "latin-1", "replace").strip("\0 ")
                return text[:2000]
            return f"({count} bytes)"
        vals = []
        for i in range(min(count, 8)):
            o = off + i * size
            if typ == 3:
                vals.append(struct.unpack(e + "H", buf[o:o + 2])[0])
            elif typ == 4:
                vals.append(struct.unpack(e + "I", buf[o:o + 4])[0])
            elif typ == 9:
                vals.append(struct.unpack(e + "i", buf[o:o + 4])[0])
            elif typ in (5, 10):
                num, den = struct.unpack(e + ("II" if typ == 5 else "ii"), buf[o:o + 8])
                vals.append(num / den if den else 0)
            elif typ in (1, 6):
                vals.append(buf[o])
        return vals[0] if count == 1 and vals else vals

    def read_ifd(off: int, target: dict, names: dict) -> int:
        if off < 8 or off + 2 > n_len:
            return 0
        n = struct.unpack(e + "H", buf[off:off + 2])[0]
        for k in range(n):
            p = off + 2 + k * 12
            if p + 12 > n_len:
                break
            tag, typ, count = struct.unpack(e + "HHI", buf[p:p + 8])
            if typ not in TYPE_SIZE:
                continue
            size = TYPE_SIZE[typ] * count
            voff = p + 8 if size <= 4 else struct.unpack(e + "I", buf[p + 8:p + 12])[0]
            if voff + min(size, 64) > n_len:
                continue
            if tag in (0x8769, 0x8825):
                pointers["exif" if tag == 0x8769 else "gps"] = struct.unpack(e + "I", buf[p + 8:p + 12])[0]
                continue
            if tag in names:
                try:
                    target[names[tag]] = value(typ, count, voff)
                except struct.error:
                    pass
        nxt = off + 2 + n * 12
        return struct.unpack(e + "I", buf[nxt:nxt + 4])[0] if nxt + 4 <= n_len else 0

    try:
        nxt = read_ifd(struct.unpack(e + "I", buf[4:8])[0], out["ifd0"], TIFF_TAGS)
        if "exif" in pointers:
            read_ifd(pointers["exif"], out["exif"], TIFF_TAGS)
        if "gps" in pointers:
            read_ifd(pointers["gps"], out["gps"], GPS_TAGS)
        if nxt:
            read_ifd(nxt, out["ifd1"], TIFF_TAGS)
        to, tl = out["ifd1"].get("ThumbnailOffset"), out["ifd1"].get("ThumbnailLength")
        if isinstance(to, int) and isinstance(tl, int) and to + tl <= n_len and tl > 0:
            out["thumb"] = buf[to:to + tl]
    except (struct.error, IndexError):
        pass  # truncated EXIF: keep what was read
    return out


def parse_xmp(text: str) -> dict[str, Any]:
    def one(name: str) -> str:
        m = re.search(name + r'(?:="([^"]*)"|>([^<]*)<)', text)
        return (m.group(1) or m.group(2) or "").strip() if m else ""

    def many(name: str) -> list[str]:
        return [(m.group(1) or m.group(2) or "").strip()
                for m in re.finditer(name + r'(?:="([^"]*)"|>([^<]*)<)', text) if (m.group(1) or m.group(2))]

    return {
        "CreatorTool": one("xmp:CreatorTool"), "CreateDate": one("xmp:CreateDate"),
        "ModifyDate": one("xmp:ModifyDate"), "MetadataDate": one("xmp:MetadataDate"),
        "DocumentID": one("xmpMM:DocumentID"), "OriginalDocumentID": one("xmpMM:OriginalDocumentID"),
        "DigitalSourceType": one(r"Iptc4xmpExt:DigitalSourceType"),
        "DerivedFrom": "xmpMM:DerivedFrom" in text,
        "HistorySoftware": many("stEvt:softwareAgent"), "HistoryActions": many("stEvt:action"),
        "Photoshop": "xmlns:photoshop=" in text,
    }


def _cbor_text_after(buf: bytes, key: bytes) -> str:
    """Best-effort: the CBOR text string that follows a CBOR key in a C2PA
    manifest. Enough to show the claim generator without a CBOR library."""
    i = buf.find(key)
    if i < 0:
        return ""
    p = i + len(key)
    if p >= len(buf):
        return ""
    hdr = buf[p]
    if 0x60 <= hdr <= 0x77:
        n, p = hdr - 0x60, p + 1
    elif hdr == 0x78 and p + 1 < len(buf):
        n, p = buf[p + 1], p + 2
    elif hdr == 0x79 and p + 2 < len(buf):
        n, p = struct.unpack(">H", buf[p + 1:p + 3])[0], p + 3
    else:
        return ""
    return buf[p:p + n].decode("utf-8", "replace")


@dataclass
class Metadata:
    format: str = "unknown"
    lossy: bool = False
    structure: dict[str, Any] = field(default_factory=dict)
    exif: dict[str, Any] | None = None
    xmp: dict[str, Any] | None = None
    text: dict[str, str] = field(default_factory=dict)      # PNG text chunks, JPEG comments
    dqt: list[dict[str, Any]] = field(default_factory=list)   # JPEG quantisation tables
    subsampling: str = ""
    trailing: int = 0
    mpf: bool = False
    c2pa: dict[str, Any] | None = None
    thumbnail: bytes | None = None

    @property
    def jpeg_quality(self) -> int | None:
        lum = [t for t in self.dqt if t["id"] == 0]
        return lum[0]["estimate"]["quality"] if lum else None

    def quant_table(self, table_id: int = 0) -> np.ndarray | None:
        """Quantisation table in natural (row-major 8×8) order."""
        for t in self.dqt:
            if t["id"] == table_id:
                q = np.zeros(64)
                q[ZIGZAG] = t["table"]
                return q.reshape(8, 8)
        return None

    def text_fields(self) -> dict[str, str]:
        """Every human-readable text field, labelled by where it came from."""
        out: dict[str, str] = {}
        if self.exif:
            for ifd in ("ifd0", "exif"):
                for k, v in self.exif[ifd].items():
                    if isinstance(v, str) and v and not v.startswith("("):
                        out[f"EXIF {k}"] = v
        if self.xmp:
            for k, v in self.xmp.items():
                if isinstance(v, str) and v:
                    out[f"XMP {k}"] = v
                elif isinstance(v, list) and v:
                    out[f"XMP {k}"] = ", ".join(dict.fromkeys(v))
        for k, v in self.text.items():
            out[k] = v
        if self.c2pa and self.c2pa.get("claim_generator"):
            out["C2PA claim generator"] = self.c2pa["claim_generator"]
        return out

    def to_dict(self) -> dict[str, Any]:
        d = {
            "format": self.format, "lossy": self.lossy, "structure": self.structure,
            "exif": {k: v for k, v in (self.exif or {}).items() if k != "thumb"} or None,
            "xmp": self.xmp, "text": self.text, "subsampling": self.subsampling,
            "trailing_bytes": self.trailing, "c2pa": self.c2pa,
            "quantisation_tables": [{"id": t["id"], "precision": t["precision"], "estimate": t["estimate"],
                                     "table_zigzag": list(map(int, t["table"]))} for t in self.dqt],
        }
        return json.loads(json.dumps(d, default=str))


def _find_c2pa(data: bytes, md: Metadata, segment: bytes | None = None) -> None:
    buf = segment if segment is not None else data
    if b"c2pa" not in buf:
        return
    info = md.c2pa or {"present": True}
    gen = _cbor_text_after(buf, b"claim_generator") or info.get("claim_generator", "")
    if gen:
        info["claim_generator"] = gen
    types = sorted({m.group(1).decode() for m in re.finditer(rb"digitalsourcetype/([A-Za-z]+)", buf)})
    if types:
        info["digital_source_types"] = sorted(set(info.get("digital_source_types", [])) | set(types))
    actions = sorted({m.group(0).decode() for m in re.finditer(rb"c2pa\.[a-z_.]+", buf)
                      if not m.group(0).startswith((b"c2pa.claim", b"c2pa.signature", b"c2pa.assertions",
                                                     b"c2pa.hash", b"c2pa.thumbnail"))})
    if actions:
        info["labels"] = actions[:20]
    md.c2pa = info


def parse_jpeg(data: bytes, md: Metadata) -> None:
    st: dict[str, Any] = {"segments": [], "scans": 0, "huffman_tables": 0, "restart_interval": False, "comments": []}
    i, n = 2, len(data)
    eoi = -1
    app11 = b""
    while i + 4 <= n:
        if data[i] != 0xFF:
            break
        m = data[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        if m == 0xD9:
            eoi = i
            break
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        s, e = i + 4, i + 2 + seg_len
        if seg_len < 2 or e > n:
            break
        seg = data[s:e]
        if 0xE0 <= m <= 0xEF:
            label = f"APP{m - 0xE0}"
            if m == 0xE0 and seg.startswith(b"JFIF"):
                label += f" JFIF {seg[5]}.{seg[6]:02d}"
            elif m == 0xE1 and seg.startswith(b"Exif\0\0"):
                label += " EXIF"
                md.exif = parse_tiff(seg[6:])
            elif m == 0xE1 and seg.startswith(b"http://ns.adobe.com/xap/1.0/\0"):
                label += " XMP"
                md.xmp = parse_xmp(seg[29:].decode("utf-8", "replace"))
            elif m == 0xE2 and seg.startswith(b"ICC_PROFILE"):
                label += " ICC profile"
            elif m == 0xE2 and seg.startswith(b"MPF"):
                label += " MPF (multi-picture)"
                md.mpf = True
            elif m == 0xEB:
                label += " JUMBF" + (" (C2PA Content Credentials)" if b"c2pa" in seg else "")
                app11 += seg
            elif m == 0xED and seg.startswith(b"Photoshop 3.0"):
                label += " Photoshop IRB"
            elif m == 0xEE and seg.startswith(b"Adobe"):
                label += " Adobe"
            elif m == 0xEC and seg.startswith(b"Ducky"):
                label += " Ducky (Save for Web)"
            else:
                ident = re.sub(rb"[^\x20-\x7e]", b"", seg[:12]).decode()
                if ident:
                    label += " " + ident
            st["segments"].append(f"{label} ({seg_len} bytes)")
        elif m == 0xDB:
            p = 0
            while p < len(seg):
                pq, tq = seg[p] >> 4, seg[p] & 15
                p += 1
                if pq:
                    vals = list(struct.unpack(">64H", seg[p:p + 128]))
                    p += 128
                else:
                    vals = list(seg[p:p + 64])
                    p += 64
                if len(vals) == 64:
                    md.dqt.append({"id": tq, "precision": 16 if pq else 8, "table": np.array(vals),
                                   "estimate": estimate_quality(np.array(vals), tq > 0)})
        elif 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
            names = {0xC0: "Baseline DCT", 0xC1: "Extended sequential DCT", 0xC2: "Progressive DCT", 0xC3: "Lossless"}
            st["encoding"] = names.get(m, f"SOF{m - 0xC0}")
            ncomp = seg[5]
            comps = [(seg[6 + c * 3], seg[7 + c * 3] >> 4, seg[7 + c * 3] & 15) for c in range(ncomp)]
            if ncomp == 3:
                (_, yh, yv), (_, ch, cv) = comps[0], comps[1]
                if ch == 1 and cv == 1:
                    md.subsampling = {(2, 2): "4:2:0", (2, 1): "4:2:2", (1, 1): "4:4:4", (4, 1): "4:1:1"}.get((yh, yv), f"{yh}x{yv}")
                else:
                    md.subsampling = "unusual"
            else:
                md.subsampling = "greyscale" if ncomp == 1 else f"{ncomp} components"
        elif m == 0xC4:
            st["huffman_tables"] += 1
        elif m == 0xDD:
            st["restart_interval"] = True
        elif m == 0xFE:
            text = seg.decode("utf-8", "replace").strip("\0 ")
            st["comments"].append(text)
            md.text[f"JPEG comment {len(st['comments'])}"] = text[:2000]
        elif m == 0xDA:
            st["scans"] += 1
            q = e
            while q + 1 < n:
                if data[q] == 0xFF:
                    nm = data[q + 1]
                    if nm == 0x00 or 0xD0 <= nm <= 0xD7:
                        q += 2
                        continue
                    if nm == 0xFF:
                        q += 1
                        continue
                    break
                q += 1
            i = q
            continue
        i = e
    md.trailing = n - (eoi + 2) if eoi >= 0 else 0
    st["end_of_image"] = eoi >= 0
    md.structure = st
    if app11:
        _find_c2pa(data, md, app11)
    if md.exif and md.exif.get("thumb"):
        md.thumbnail = md.exif["thumb"]


def parse_png(data: bytes, md: Metadata) -> None:
    import zlib
    st: dict[str, Any] = {"chunks": []}
    p = 8
    while p + 8 <= len(data):
        length = struct.unpack(">I", data[p:p + 4])[0]
        typ = data[p + 4:p + 8].decode("latin-1")
        d = data[p + 8:p + 8 + length]
        if len(d) < length:
            break
        if typ != "IDAT" and len(st["chunks"]) < 80:
            st["chunks"].append(f"{typ} ({length} bytes)")
        try:
            if typ == "IHDR":
                w, h, bd, ct, _, _, il = struct.unpack(">IIBBBBB", d[:13])
                st["header"] = f"{w}×{h}, {bd}-bit, colour type {ct}{', interlaced' if il else ''}"
            elif typ == "tEXt":
                k, _, v = d.partition(b"\0")
                md.text[k.decode("latin-1")] = v.decode("latin-1")[:20000]
            elif typ == "zTXt":
                k, _, v = d.partition(b"\0")
                md.text[k.decode("latin-1")] = zlib.decompress(v[1:]).decode("latin-1", "replace")[:20000]
            elif typ == "iTXt":
                k, _, rest = d.partition(b"\0")
                comp, rest = rest[0], rest[2:]
                _, _, rest = rest.partition(b"\0")   # language
                _, _, rest = rest.partition(b"\0")   # translated keyword
                val = zlib.decompress(rest) if comp else rest
                key = k.decode("latin-1")
                if key == "XML:com.adobe.xmp":
                    md.xmp = parse_xmp(val.decode("utf-8", "replace"))
                else:
                    md.text[key] = val.decode("utf-8", "replace")[:20000]
            elif typ == "eXIf":
                md.exif = parse_tiff(d)
            elif typ == "tIME":
                y, mo, dd, hh, mi, ss = struct.unpack(">HBBBBB", d[:7])
                st["last_modified_tIME"] = f"{y}-{mo:02d}-{dd:02d} {hh:02d}:{mi:02d}:{ss:02d}"
            elif typ == "caBX":
                _find_c2pa(data, md, d)
        except (struct.error, zlib.error, IndexError):
            pass
        if typ == "IEND":
            md.trailing = len(data) - (p + 12 + length)
            break
        p += 12 + length
    md.structure = st
    if md.exif and md.exif.get("thumb"):
        md.thumbnail = md.exif["thumb"]


def parse_webp(data: bytes, md: Metadata) -> None:
    st: dict[str, Any] = {"chunks": []}
    p, lossless = 12, False
    while p + 8 <= len(data):
        typ = data[p:p + 4].decode("latin-1")
        length = struct.unpack("<I", data[p + 4:p + 8])[0]
        d = data[p + 8:p + 8 + length]
        st["chunks"].append(f"{typ.strip()} ({length} bytes)")
        if typ == "EXIF":
            md.exif = parse_tiff(d[6:] if d.startswith(b"Exif\0\0") else d)
        elif typ == "XMP ":
            md.xmp = parse_xmp(d.decode("utf-8", "replace"))
        elif typ == "VP8L":
            lossless = True
        elif typ == "C2PA":
            _find_c2pa(data, md, d)
        p += 8 + length + (length & 1)
    st["compression"] = "lossless (VP8L)" if lossless else "lossy (VP8)"
    md.structure = st
    md.lossy = not lossless


def parse(data: bytes) -> Metadata:
    md = Metadata()
    if data[:2] == b"\xff\xd8":
        md.format, md.lossy = "JPEG", True
        parse_jpeg(data, md)
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        md.format = "PNG"
        parse_png(data, md)
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        md.format = "WebP"
        parse_webp(data, md)
    elif data[:4] in (b"II*\0", b"MM\0*"):
        md.format = "TIFF"
        md.exif = parse_tiff(data)
    elif data[:2] == b"BM":
        md.format = "BMP"
    elif data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypavif"):
        md.format = "HEIF/AVIF"
        md.lossy = True
    # XMP and C2PA can sit anywhere in some containers; a last sweep over
    # the raw bytes catches what the structured parse didn't reach.
    if md.xmp is None:
        i = data.find(b"<x:xmpmeta")
        if i >= 0:
            j = data.find(b"</x:xmpmeta>", i)
            if j > 0:
                md.xmp = parse_xmp(data[i:j + 12].decode("utf-8", "replace"))
    if md.c2pa is None and b"jumb" in data and b"c2pa" in data:
        _find_c2pa(data, md)
    return md
